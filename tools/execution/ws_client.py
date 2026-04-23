"""
Alpaca WebSocket client for trade update events (Loop B transport layer).
Handles:
  - Connection to Alpaca trade_updates stream
  - Exponential backoff reconnect (cap 60s, max 10 attempts before alerting)
  - Heartbeat timestamp written every HEARTBEAT_INTERVAL_SEC seconds,
    but ONLY while the WebSocket is connected and authenticated.
    A stale heartbeat therefore means the process is dead or the WS is disconnected.
  - Event dispatching to registered handlers

Usage:
    client = AlpacaWSClient(on_fill=my_fill_handler, on_reject=my_reject_handler)
    client.run()  # blocking; run in a thread
"""
import datetime
import json
import logging
import os
import threading
import time
from typing import Any, Callable

import websocket  # websocket-client package

import config
from tools.alerts import telegram_bot as tg

logger = logging.getLogger(__name__)

_HEARTBEAT_PATH = os.path.join(os.path.dirname(__file__), "../../db/heartbeat.txt")

_WS_URL_PAPER = "wss://paper-api.alpaca.markets/stream"
_WS_URL_LIVE = "wss://api.alpaca.markets/stream"

_UTC = datetime.timezone.utc


def _write_heartbeat() -> None:
    os.makedirs(os.path.dirname(_HEARTBEAT_PATH), exist_ok=True)
    with open(_HEARTBEAT_PATH, "w") as f:
        f.write(datetime.datetime.now(_UTC).isoformat())


class AlpacaWSClient:
    """
    Persistent WebSocket client for Alpaca trade update events.
    Call run() in a daemon thread; stop with stop().
    """

    def __init__(
        self,
        on_fill: Callable[[dict[str, Any]], None] | None = None,
        on_partial_fill: Callable[[dict[str, Any]], None] | None = None,
        on_reject: Callable[[dict[str, Any]], None] | None = None,
        on_cancel: Callable[[dict[str, Any]], None] | None = None,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        self._on_fill = on_fill
        self._on_partial_fill = on_partial_fill
        self._on_reject = on_reject
        self._on_cancel = on_cancel
        self._on_ready = on_ready

        self._ws: websocket.WebSocketApp | None = None
        self._stop_event = threading.Event()
        self._connected = threading.Event()
        self._attempt = 0
        self._max_attempts = 10

        self._heartbeat_thread: threading.Thread | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Blocking run loop with reconnect. Call in a dedicated thread."""
        config.load()
        self._start_heartbeat_thread()
        while not self._stop_event.is_set():
            self._attempt += 1
            if self._attempt > 1:
                wait = min(2 ** (self._attempt - 1), 60)
                logger.warning("Reconnect attempt %d — waiting %ds", self._attempt, wait)
                tg.alert_ws_reconnecting(self._attempt)
                time.sleep(wait)

            if self._attempt > self._max_attempts:
                logger.critical("WebSocket reconnect failed after %d attempts.", self._max_attempts)
                tg.alert_ws_failed(self._max_attempts)
                self._stop_event.set()
                return

            url = _WS_URL_PAPER if config.PAPER_TRADING else _WS_URL_LIVE
            self._ws = websocket.WebSocketApp(
                url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._ws.run_forever(ping_interval=30, ping_timeout=10)

            if self._stop_event.is_set():
                break

            # Connection dropped — loop will reconnect
            self._connected.clear()

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws:
            self._ws.close()

    def is_connected(self) -> bool:
        return self._connected.is_set()

    # ── Internal WebSocket handlers ────────────────────────────────────────────

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("WebSocket connected.")
        auth_msg = json.dumps({
            "action": "auth",
            "key": config.ALPACA_API_KEY,
            "secret": config.ALPACA_SECRET_KEY,
        })
        ws.send(auth_msg)

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("Non-JSON WS message: %s", raw[:200])
            return

        stream = msg.get("stream", "")
        data = msg.get("data", {})

        if stream == "authorization":
            status = data.get("status")
            if status == "authorized":
                logger.info("WS authorized. Subscribing to trade_updates.")
                ws.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
            else:
                logger.error("WS authorization failed: %s", data)
                ws.close()
            return

        if stream == "listening":
            logger.info("WS listening: %s", data.get("streams"))
            self._connected.set()
            self._attempt = 0  # reset counter on successful connection
            if self._on_ready:
                self._on_ready()
            return

        if stream == "trade_updates":
            self._dispatch(data)

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.error("WebSocket error: %s", error)

    def _on_close(self, ws: websocket.WebSocketApp, code: int, reason: str) -> None:
        self._connected.clear()
        if not self._stop_event.is_set():
            logger.warning("WebSocket closed (code=%s, reason=%s). Will reconnect.", code, reason)

    # ── Event dispatch ─────────────────────────────────────────────────────────

    def _dispatch(self, data: dict[str, Any]) -> None:
        event = data.get("event")
        order = data.get("order", {})
        logger.debug("Trade update: event=%s order_id=%s", event, order.get("id", "?")[:8])

        if event == "fill" and self._on_fill:
            self._on_fill(data)
        elif event == "partial_fill" and self._on_partial_fill:
            self._on_partial_fill(data)
        elif event == "rejected" and self._on_reject:
            self._on_reject(data)
        elif event in ("canceled", "cancelled") and self._on_cancel:
            self._on_cancel(data)
        else:
            logger.warning("Unknown or unhandled trade_updates event type: %s", event)

    # ── Heartbeat thread ───────────────────────────────────────────────────────

    def _start_heartbeat_thread(self) -> None:
        def _beat() -> None:
            while not self._stop_event.is_set():
                # Only write heartbeat while the WS is connected and authenticated.
                # A stale heartbeat means the process is dead or the connection dropped.
                if self._connected.is_set():
                    _write_heartbeat()
                time.sleep(config.HEARTBEAT_INTERVAL_SEC)

        self._heartbeat_thread = threading.Thread(target=_beat, daemon=True, name="heartbeat")
        self._heartbeat_thread.start()
