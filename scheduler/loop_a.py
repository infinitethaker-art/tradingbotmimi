"""
Loop A — Signal Scanning Loop.
Runs on a 15-minute interval aligned to bar boundaries during market hours.
Responsibilities: fetch bars, compute features, generate signal, run pre-signal
risk gate, publish SignalIntent to the shared execution queue (and intent file
for human inspection via approve.py).

Does NOT place orders. Orders are placed by Loop B when it consumes from the queue.
If Loop A crashes, Loop B continues protecting existing positions.
"""
import datetime
import json
import logging
import os
import queue
import threading
import time
import uuid
from zoneinfo import ZoneInfo

import config
from tools.alerts import telegram_bot as tg
from tools.data import data_feed, market_calendar
from tools.features import features
from tools.risk import risk_checks
from tools.signals import signal as sig
from tools.reporting import trade_logger

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
_UTC = datetime.timezone.utc

_INTENT_PATH = os.path.join(os.path.dirname(__file__), "../db/pending_intent.json")


def _write_intent(signal_event: dict, intent_id: str) -> None:
    """Publish a pending intent file for human inspection via approve.py."""
    os.makedirs(os.path.dirname(_INTENT_PATH), exist_ok=True)
    expires_at = (
        datetime.datetime.now(_UTC) + datetime.timedelta(seconds=config.MANUAL_APPROVAL_WINDOW_SEC)
    ).isoformat()
    intent = {
        **signal_event,
        "intent_id": intent_id,
        "expires_at": expires_at,
        "decision": "PENDING",
    }
    tmp_path = _INTENT_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(intent, f, indent=2)
    os.replace(tmp_path, _INTENT_PATH)


def _clear_intent() -> None:
    if os.path.exists(_INTENT_PATH):
        os.remove(_INTENT_PATH)


def _wait_for_approval(signal_event: dict) -> str:
    """
    Block for up to MANUAL_APPROVAL_WINDOW_SEC seconds, polling the intent file.
    Returns: 'APPROVED', 'REJECTED', or 'EXPIRED'
    """
    if config.AUTO_EXECUTE:
        return "APPROVED"

    deadline = time.time() + config.MANUAL_APPROVAL_WINDOW_SEC
    while time.time() < deadline:
        try:
            with open(_INTENT_PATH) as f:
                intent = json.load(f)
            decision = intent.get("decision", "PENDING")
            if decision in ("APPROVED", "REJECTED"):
                return decision
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(2)
    return "EXPIRED"


def _seconds_to_next_bar(bar_minutes: int = 15) -> float:
    """Compute seconds until the next 15-min bar boundary, plus a 5s buffer."""
    now = datetime.datetime.now(ET)
    elapsed_seconds = (now.minute % bar_minutes) * 60 + now.second
    remaining = bar_minutes * 60 - elapsed_seconds + 5
    return float(remaining)


class LoopA:
    def __init__(
        self,
        risk_state: risk_checks.RiskState,
        position_tracker,
        intent_queue: queue.Queue,
        ws_client=None,
    ) -> None:
        self._risk_state = risk_state
        self._position_tracker = position_tracker
        self._intent_queue = intent_queue  # shared queue to Loop B
        self._ws_client = ws_client
        self._session_pnl = 0.0

    def tick(self, symbol: str) -> None:
        """Run one signal evaluation cycle for *symbol*."""
        config.load()

        # Check for time-based exit FIRST — must run even outside the entry window
        self._check_time_exit(symbol)

        # Skip new entries if outside trading window
        if not market_calendar.is_within_trading_window(
            start_offset_min=config.TRADE_START_OFFSET_MIN,
            end_offset_min=config.TRADE_END_OFFSET_MIN,
        ):
            logger.debug("Outside trading window — skipping entry scan.")
            return

        # Fetch bars
        bars = data_feed.fetch_bars(symbol, n_bars=50)
        if not bars or len(bars) < 27:
            logger.warning("Insufficient bars for %s (%d). Skipping.", symbol, len(bars) if bars else 0)
            return

        # Compute features
        feat_series = features.compute(bars)
        if len(feat_series) < 2:
            logger.warning("Insufficient feature rows for %s. Skipping.", symbol)
            return

        # Fetch quote for spread logging
        quote = data_feed.fetch_latest_quote(symbol)

        # Build session context
        context = {
            "symbol": symbol,
            "session_start_equity": self._risk_state.session_start_equity,
            "daily_loss_limit_usd": self._risk_state.daily_loss_limit_usd,
            "session_pnl_at_signal": self._session_pnl,
            "market_regime": "UNKNOWN",  # Phase 2 adds regime tagger
        }

        # Generate signal (purely technical — no time-window gating in signal engine)
        signal_event = sig.evaluate(feat_series, quote, context)

        # Handle DRY_RUN before touching risk checks
        if config.DRY_RUN:
            signal_event["disposition"] = "DRY_RUN"
            signal_event["rejection_reason"] = None
            trade_logger.log_signal(signal_event)
            logger.info("DRY_RUN: signal logged as DRY_RUN, no intent published.")
            return

        if signal_event["signal_type"] == "ENTER_LONG":
            from alpaca.trading.client import TradingClient
            trading_client = TradingClient(
                config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING
            )
            account = trading_client.get_account()
            current_equity = float(account.equity)

            result = risk_checks.check_entry(signal_event, self._risk_state, current_equity)
            risk_checks.apply_result(signal_event, result)

            if result.passed:
                # Check Loop B is ready before publishing intent
                if self._ws_client and not self._ws_client.is_connected():
                    logger.warning("Loop B WebSocket not connected — skipping intent publish for %s.", symbol)
                    signal_event["disposition"] = "REJECTED"
                    signal_event["rejection_reason"] = "LOOP_B_NOT_READY"
                    trade_logger.log_signal(signal_event)
                    return

                intent_id = str(uuid.uuid4())
                logger.info("ENTER_LONG signal for %s — publishing intent %s.", symbol, intent_id[:8])
                _write_intent(signal_event, intent_id)
                tg.alert_signal(signal_event)

                decision = _wait_for_approval(signal_event)
                _clear_intent()

                if decision == "APPROVED":
                    tg.alert_intent_approved(symbol, "ENTER_LONG")
                    # Put onto the queue for Loop B to execute
                    try:
                        self._intent_queue.put_nowait(signal_event)
                        logger.info("Intent queued for Loop B execution: %s", intent_id[:8])
                    except queue.Full:
                        logger.warning(
                            "Intent queue full — Loop B has not consumed the previous intent. "
                            "Discarding intent %s.", intent_id[:8],
                        )
                        signal_event["disposition"] = "REJECTED"
                        signal_event["rejection_reason"] = "QUEUE_FULL"

                elif decision == "REJECTED":
                    tg.alert_intent_rejected_by_user(symbol, "ENTER_LONG")
                    signal_event["disposition"] = "REJECTED"
                    signal_event["rejection_reason"] = "MANUAL_REJECT"

                else:  # EXPIRED
                    tg.alert_intent_expired(symbol, "ENTER_LONG")
                    signal_event["disposition"] = "EXPIRED"
                    signal_event["rejection_reason"] = "MANUAL_EXPIRE"

            else:
                logger.info("Signal REJECTED: %s — %s", signal_event["rejection_reason"], result.details)
                if result.rejection_reason == "DAILY_LIMIT":
                    tg.alert_daily_limit_hit(
                        self._risk_state.session_loss(current_equity),
                        self._risk_state.daily_loss_limit_usd,
                    )

        # Always log the final signal event
        trade_logger.log_signal(signal_event)

    def _check_time_exit(self, symbol: str) -> None:
        """If a position is open and we're past the exit deadline, queue a time-based exit."""
        pos = self._position_tracker.get(symbol)
        if pos is None:
            return

        now = datetime.datetime.now(ET)
        today = now.date()
        if not market_calendar.is_trading_day(today):
            return

        close_t = market_calendar.market_close_time(today)
        exit_deadline = close_t - datetime.timedelta(minutes=config.TRADE_END_OFFSET_MIN)
        if now >= exit_deadline:
            logger.info("Time-based exit triggered for %s — queuing EXIT_TIME intent.", symbol)
            exit_intent = {
                "signal_type": "EXIT_TIME",
                "symbol": symbol,
                "decision": "APPROVED",
                "intent_id": str(uuid.uuid4()),
                "expires_at": (
                    datetime.datetime.now(_UTC) + datetime.timedelta(seconds=60)
                ).isoformat(),
            }
            try:
                self._intent_queue.put_nowait(exit_intent)
            except queue.Full:
                logger.warning("Intent queue full — could not queue EXIT_TIME for %s.", symbol)

    def run(self, symbol: str, stop_event: threading.Event | None = None) -> None:
        """Blocking run loop. Sleeps to next bar boundary between ticks."""
        if stop_event is None:
            stop_event = threading.Event()

        logger.info("Loop A started for %s.", symbol)
        while not stop_event.is_set():
            if market_calendar.is_trading_day():
                try:
                    self.tick(symbol)
                except Exception as exc:
                    logger.exception("Loop A tick error: %s", exc)

            sleep_secs = _seconds_to_next_bar()
            logger.debug("Next tick in %.0fs.", sleep_secs)
            stop_event.wait(timeout=sleep_secs)

        logger.info("Loop A stopped.")
