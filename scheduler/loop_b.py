"""
Loop B — Event-Driven Execution Loop.
Always-on WebSocket connection to Alpaca trade update stream.
Handles: fills, partial fills, rejections, cancellations.
Consumes approved signal intents from the shared queue and submits bracket orders.
Runs startup reconciliation before accepting any intents.
"""
import logging
import queue
import threading
import time
from typing import Any

import config
from alpaca.trading.client import TradingClient
from tools.alerts import telegram_bot as tg
from tools.execution import reconciler, order_manager
from tools.execution.position_tracker import PositionTracker
from tools.execution.ws_client import AlpacaWSClient
from tools.reporting import trade_logger

logger = logging.getLogger(__name__)


class LoopB:
    def __init__(
        self,
        risk_state,
        position_tracker: PositionTracker,
        intent_queue: queue.Queue,
    ) -> None:
        self._risk_state = risk_state
        self._position_tracker = position_tracker
        self._intent_queue = intent_queue
        self._trading_client: TradingClient | None = None
        self._ws: AlpacaWSClient | None = None

        # _ready is set only when BOTH reconciliation passed AND WS is connected
        self._ready = threading.Event()
        self._reconciled = False

        # Maps order_id -> (tp_price, sl_stop) stored at submission time
        # so we don't rely on order["legs"] in trade update payloads
        self._bracket_prices: dict[str, tuple[float, float]] = {}

        # Maps order_id -> expected_fill_price for slippage calculation
        self._expected_prices: dict[str, float] = {}

        # Maps order_id -> notional_usd (supports smoke test $50 override)
        self._order_notionals: dict[str, float] = {}

        self._stop_event = threading.Event()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def start(self) -> None:
        """Initialize broker client, reconcile, start WebSocket and queue consumer."""
        config.load()

        self._trading_client = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        )

        # Startup reconciliation — must pass before accepting intents
        recon_result = reconciler.run(self._trading_client)
        for msg in recon_result.messages:
            logger.info("Reconcile: %s", msg)

        if not recon_result.is_safe():
            logger.critical("Reconciliation HALTED. Loop B will not accept intents.")
            return

        self._reconciled = True

        # Restore position state from DB
        self._position_tracker.load_from_db()
        self._risk_state.set_open_positions(self._position_tracker.open_count())

        # Start queue consumer thread
        consumer = threading.Thread(
            target=self._consume_intents,
            name="LoopB-QueueConsumer",
            daemon=True,
        )
        consumer.start()

        # Start WebSocket (blocking — runs until stop() is called)
        self._ws = AlpacaWSClient(
            on_fill=self._handle_fill,
            on_partial_fill=self._handle_partial_fill,
            on_reject=self._handle_reject,
            on_cancel=self._handle_cancel,
            on_ready=self._on_ws_ready,
        )
        self._ws.run()

    def _on_ws_ready(self) -> None:
        """Called by ws_client when authenticated and listening. Set ready if reconciled."""
        if self._reconciled:
            logger.info("Loop B ready — reconciled and WebSocket connected.")
            self._ready.set()
        else:
            logger.warning("WS connected but reconciliation not yet complete — not marking ready.")

    # ── Intent queue consumer ──────────────────────────────────────────────────

    def _consume_intents(self) -> None:
        """Polls the intent queue and executes approved intents."""
        while not self._stop_event.is_set():
            try:
                intent = self._intent_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if not self._ready.is_set():
                logger.warning("Loop B not ready — discarding intent for %s.", intent.get("symbol"))
                self._intent_queue.task_done()
                continue

            signal_type = intent.get("signal_type")
            try:
                if signal_type == "ENTER_LONG":
                    self._execute_entry(intent)
                elif signal_type == "EXIT_TIME":
                    self._execute_time_exit(intent)
                else:
                    logger.warning("Unknown intent signal_type: %s", signal_type)
            except Exception as exc:
                logger.exception("Error processing intent %s: %s", signal_type, exc)
            finally:
                self._intent_queue.task_done()

    def register_pending_order(
        self,
        order_id: str,
        tp_price: float,
        sl_stop: float,
        expected_price: float,
        notional_usd: float,
    ) -> None:
        """Register an order submitted outside the intent queue (e.g. smoke test)."""
        self._bracket_prices[order_id] = (tp_price, sl_stop)
        self._expected_prices[order_id] = expected_price
        self._order_notionals[order_id] = notional_usd

    def _execute_entry(self, signal_event: dict[str, Any]) -> None:
        result = order_manager.submit_bracket_entry(self._trading_client, signal_event)
        if result is None:
            return
        order_event, tp_price, sl_stop = result
        order_id = order_event["order_id"]
        self._expected_prices[order_id] = order_event["expected_fill_price"]
        self._bracket_prices[order_id] = (tp_price, sl_stop)
        self._order_notionals[order_id] = order_event["notional_usd"]
        logger.info("Entry order submitted by Loop B: %s", order_id[:8])

    def _execute_time_exit(self, intent: dict[str, Any]) -> None:
        symbol = intent.get("symbol", "?")
        pos = self._position_tracker.get(symbol)
        if pos is None:
            logger.info("Time exit: no open position for %s.", symbol)
            return
        logger.info("Time exit: cancelling bracket for %s (entry_order=%s).", symbol, pos.entry_order_id[:8])
        order_manager.cancel_open_bracket(self._trading_client, pos.entry_order_id)
        # Alpaca processes cancels asynchronously — retry with backoff until shares are available
        for attempt in range(1, 4):
            time.sleep(attempt)
            if order_manager.submit_market_exit(self._trading_client, symbol, pos.qty):
                return
            logger.warning("Time exit attempt %d/3 failed for %s — retrying.", attempt, symbol)

    # ── WebSocket fill handlers ────────────────────────────────────────────────

    def _handle_fill(self, data: dict[str, Any]) -> None:
        order = data.get("order", {})
        order_id = order.get("id", "")
        symbol = order.get("symbol", "")
        side = order.get("side", "")
        filled_qty = float(order.get("filled_qty", 0))
        fill_price = float(order.get("filled_avg_price") or 0)
        filled_at = data.get("timestamp", "")

        logger.info("Fill: %s %s %.4f @ $%.4f", side, symbol, filled_qty, fill_price)

        order_manager.on_fill_event(data, self._expected_prices)
        # Clean up expected price after terminal fill
        self._expected_prices.pop(order_id, None)

        if side == "buy":
            tp_price, sl_stop = self._bracket_prices.get(order_id, (0.0, 0.0))
            notional = self._order_notionals.pop(order_id, config.MVP_POSITION_NOTIONAL_USD)
            self._position_tracker.on_entry_fill(
                symbol=symbol,
                entry_order_id=order_id,
                fill_price=fill_price,
                qty=filled_qty,
                notional_usd=notional,
                stop_price=sl_stop,
                take_profit=tp_price,
                filled_at=filled_at,
            )
            self._risk_state.set_open_positions(self._position_tracker.open_count())

        elif side == "sell":
            pnl = self._position_tracker.on_exit_fill(
                symbol=symbol,
                exit_order_id=order_id,
                exit_price=fill_price,
                qty=filled_qty,
                filled_at=filled_at,
                reason="fill",
            )
            self._risk_state.set_open_positions(self._position_tracker.open_count())
            if pnl is not None:
                logger.info("Realized PnL for %s: $%.4f", symbol, pnl)

    def _handle_partial_fill(self, data: dict[str, Any]) -> None:
        order = data.get("order", {})
        order_id = order.get("id", "")
        symbol = order.get("symbol", "")
        side = order.get("side", "")
        filled_qty = float(order.get("filled_qty", 0))
        fill_price = float(order.get("filled_avg_price") or 0)
        filled_at = data.get("timestamp", "")

        logger.info(
            "Partial fill: %s %s %.4f of %.4f @ $%.4f",
            side, symbol, filled_qty, float(order.get("qty", 0)), fill_price,
        )

        order_manager.on_fill_event(data, self._expected_prices)

        if side == "sell":
            # Partial exit — reduces position qty, does not close
            self._position_tracker.on_exit_fill(
                symbol=symbol,
                exit_order_id=order_id,
                exit_price=fill_price,
                qty=filled_qty,
                filled_at=filled_at,
                reason="partial_fill",
            )
            self._risk_state.set_open_positions(self._position_tracker.open_count())

    def _handle_reject(self, data: dict[str, Any]) -> None:
        order = data.get("order", {})
        reason = (
            order.get("reason")
            or order.get("reject_reason")
            or "broker_rejected"
        )
        symbol = order.get("symbol", "?")
        oid = order.get("id", "")
        logger.error("Order REJECTED: %s — %s", symbol, reason)
        tg.send_raw(f"❌ Broker rejected order for {symbol}: {reason}")
        if oid:
            trade_logger.update_order_fill(oid, {
                "filled_at": None, "filled_qty": 0, "actual_fill_price": None,
                "slippage_pct": None, "fill_latency_ms": None, "partial_fill": 0,
                "status": "rejected", "pnl_realized": None,
            })
            self._expected_prices.pop(oid, None)
            self._bracket_prices.pop(oid, None)
            self._order_notionals.pop(oid, None)

    def _handle_cancel(self, data: dict[str, Any]) -> None:
        order = data.get("order", {})
        symbol = order.get("symbol", "?")
        oid = order.get("id", "")
        logger.info("Order canceled: %s (%s)", symbol, oid[:8] if oid else "?")
        if oid:
            trade_logger.update_order_fill(oid, {
                "filled_at": None, "filled_qty": 0, "actual_fill_price": None,
                "slippage_pct": None, "fill_latency_ms": None, "partial_fill": 0,
                "status": "canceled", "pnl_realized": None,
            })
            self._expected_prices.pop(oid, None)
            self._bracket_prices.pop(oid, None)
            self._order_notionals.pop(oid, None)

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws:
            self._ws.stop()
