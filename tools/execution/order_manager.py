"""
Idempotent bracket order placement.
Every order is identified by a deterministic client_order_id.
Before submission, the broker is queried for that ID — duplicate submissions are blocked.

MVP order policy (fixed, per plan):
  Entry:     Limit order at bar close price, day TIF, extended hours OFF
  Exit:      Bracket — TP limit + SL stop-limit attached to entry
  Stop-loss: Stop-limit with limit 2 ticks below stop price
  Duration:  day for all legs
"""
import datetime
import logging
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, OrderType, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
)

import config
from tools.alerts import telegram_bot as tg
from tools.reporting import trade_logger

logger = logging.getLogger(__name__)

_TICK_SIZE = 0.01  # SPY minimum price increment
_UTC = datetime.timezone.utc


def make_client_order_id(symbol: str, signal_type: str, bar_timestamp: str) -> str:
    """
    Build a deterministic client_order_id that is safe to re-check on reconnect.
    Format: {SYMBOL}_{YYYYMMDD}_{SIGNALTYPE}_{HHMM}
    bar_timestamp is a required ISO8601 string — raises if malformed or empty.
    """
    if not bar_timestamp:
        raise ValueError("bar_timestamp is required for deterministic client_order_id")
    try:
        dt = datetime.datetime.fromisoformat(bar_timestamp)
        date_str = dt.strftime("%Y%m%d")
        time_str = dt.strftime("%H%M")
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"bar_timestamp '{bar_timestamp}' is not a valid ISO8601 string: {exc}"
        ) from exc

    return f"{symbol}_{date_str}_{signal_type}_{time_str}"


def _already_submitted(client: TradingClient, client_order_id: str) -> bool:
    """
    Return True if Alpaca already has an order with this client_order_id.
    Raises on API/network/auth errors — only swallows the "not found" case.
    """
    try:
        order = client.get_order_by_client_id(client_order_id)
        if order:
            logger.info(
                "Idempotency: order %s already exists (status=%s). Skipping.",
                client_order_id, order.status,
            )
            return True
    except Exception as exc:
        exc_str = str(exc).lower()
        # Alpaca returns 404 / "not found" when the order doesn't exist
        if "not found" in exc_str or "404" in exc_str:
            return False
        # Any other error (auth, network, rate limit) should propagate
        raise
    return False


def _has_broker_exposure(client: TradingClient, symbol: str) -> bool:
    """
    True if the broker already holds a position in *symbol* or has any open order
    for it. Used to block duplicate entries: the in-memory has_open() check in
    Loop A can miss the window between submitting an entry and its fill (or a missed
    WS fill can leave it stale). The broker is the source of truth. Errors propagate
    — if we cannot verify broker state, we must not risk submitting a duplicate.
    """
    if any(p.symbol == symbol for p in client.get_all_positions()):
        return True
    if any(o.symbol == symbol for o in client.get_orders()):
        return True
    return False


def compute_qty(bar_price: float) -> int:
    """
    Fixed notional position sizing per plan.
    Floor to whole shares — bracket orders require integer qty.
    """
    config.load()
    if bar_price <= 0:
        raise ValueError(f"bar_price must be positive, got {bar_price}")
    return int(config.MVP_POSITION_NOTIONAL_USD / bar_price)


def submit_bracket_entry(
    client: TradingClient,
    signal_event: dict[str, Any],
    notional_usd: float | None = None,
    is_smoke_test: bool = False,
    alert_prefix: str = "",
) -> tuple[dict[str, Any], float, float] | None:
    """
    Submit a bracket buy order (entry + TP + SL) to Alpaca.

    Returns (order_event, tp_price, sl_stop) tuple so Loop B can store bracket
    prices locally without relying on order["legs"] in trade update payloads.
    Returns None on failure or duplicate.

    The bracket order attaches:
      - Take-profit limit leg at entry_price * (1 + TAKE_PROFIT_PCT)
      - Stop-loss stop-limit leg at entry_price * (1 - STOP_LOSS_PCT),
        limit 2 ticks below stop price
    """
    config.load()

    symbol = signal_event["symbol"]
    bar_close = signal_event["bar_close"]
    bar_timestamp = signal_event.get("bar_timestamp", "")
    signal_event_id = signal_event["event_id"]

    if bar_close <= 0:
        logger.error("submit_bracket_entry: bar_close=%s is invalid for %s", bar_close, symbol)
        return None

    effective_notional = notional_usd if notional_usd is not None else config.MVP_POSITION_NOTIONAL_USD
    order_type_tag = "SMOKE" if is_smoke_test else "ENTER"
    client_order_id = make_client_order_id(symbol, order_type_tag, bar_timestamp)

    # Idempotency check (same client_order_id / same bar)
    if _already_submitted(client, client_order_id):
        return None

    # Duplicate-entry guard against the broker (source of truth). Covers the
    # submit->fill window and missed WS fills that the in-memory has_open() check
    # in Loop A cannot. Never open a second position in a symbol already held or
    # with a working order.
    if _has_broker_exposure(client, symbol):
        logger.warning(
            "submit_bracket_entry: broker already has a position or open order for %s "
            "— skipping duplicate entry.", symbol,
        )
        return None

    qty = int(effective_notional / bar_close)
    if qty < 1:
        logger.warning(
            "submit_bracket_entry: computed qty=%d is zero for %s @ %.2f (notional=%.2f). "
            "Increase MVP_POSITION_NOTIONAL_USD above the share price.",
            qty, symbol, bar_close, effective_notional,
        )
        return None

    limit_price = round(bar_close, 2)
    tp_price = round(limit_price * (1 + config.TAKE_PROFIT_PCT), 2)
    sl_stop = round(limit_price * (1 - config.STOP_LOSS_PCT), 2)
    sl_limit = round(sl_stop - 2 * _TICK_SIZE, 2)

    submitted_at = datetime.datetime.now(_UTC).isoformat()

    try:
        order = client.submit_order(
            LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=tp_price),
                stop_loss=StopLossRequest(stop_price=sl_stop, limit_price=sl_limit),
                client_order_id=client_order_id,
                extended_hours=False,
            )
        )
    except Exception as exc:
        logger.error("Order submission failed for %s: %s", symbol, exc)
        tg.send_raw(f"❌ Order submission FAILED for {symbol}: {exc}")
        return None

    tg.alert_order_submitted(
        client_order_id=client_order_id,
        symbol=symbol,
        side="buy",
        qty=qty,
        limit_price=limit_price,
        notional=effective_notional,
        prefix=alert_prefix,
    )

    order_event: dict[str, Any] = {
        "order_id": str(order.id),
        "client_order_id": client_order_id,
        "signal_event_id": signal_event_id,
        "symbol": symbol,
        "data_feed": signal_event.get("data_feed", config.ALPACA_DATA_FEED),
        "side": "buy",
        "order_type": "bracket",
        "qty": qty,
        "notional_usd": effective_notional,
        "is_smoke_test": int(is_smoke_test),
        "limit_price": limit_price,
        "stop_price": sl_stop,
        "submitted_at": submitted_at,
        "filled_at": None,
        "filled_qty": None,
        "partial_fill": 0,
        "expected_fill_price": limit_price,
        "actual_fill_price": None,
        "slippage_pct": None,
        "fill_latency_ms": None,
        "status": str(order.status),
        "broker_reject_reason": None,
        "pnl_realized": None,
    }

    trade_logger.log_order(order_event)
    logger.info("Bracket order submitted: %s qty=%.4f @ $%.2f", client_order_id, qty, limit_price)
    return order_event, tp_price, sl_stop


def cancel_open_bracket(client: TradingClient, order_id: str) -> bool:
    """Cancel an open bracket order (time-based exit). Returns True on success."""
    try:
        client.cancel_order_by_id(order_id)
        logger.info("Cancelled bracket order %s for time-based exit.", order_id[:8])
        return True
    except Exception as exc:
        logger.error("Failed to cancel order %s: %s", order_id[:8], exc)
        return False


def submit_market_exit(client: TradingClient, symbol: str, qty: float) -> bool:
    """Submit a market sell to close an open position (time-based exit)."""
    try:
        order = client.submit_order(
            MarketOrderRequest(
                symbol=symbol,
                qty=int(qty),
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
        )
        logger.info("Time exit: market sell submitted for %s qty=%d", symbol, int(qty))
        tg.send_raw(f"⏱ <b>TIME EXIT</b> {symbol} — market sell {int(qty)} share(s) submitted.")

        # Log the order so fill events have a row to update PnL against
        submitted_at = (
            order.submitted_at.isoformat()
            if order.submitted_at
            else datetime.datetime.now(_UTC).isoformat()
        )
        config.load()
        trade_logger.log_order({
            "order_id": str(order.id),
            "client_order_id": str(order.client_order_id or order.id),
            "signal_event_id": None,
            "symbol": symbol,
            "data_feed": config.ALPACA_DATA_FEED,
            "side": "sell",
            "order_type": "market",
            "qty": int(qty),
            "notional_usd": 0.0,
            "limit_price": None,
            "stop_price": None,
            "submitted_at": submitted_at,
            "filled_at": None,
            "filled_qty": 0,
            "partial_fill": 0,
            "expected_fill_price": None,
            "actual_fill_price": None,
            "slippage_pct": None,
            "fill_latency_ms": None,
            "status": "submitted",
            "broker_reject_reason": None,
            "pnl_realized": None,
        })
        return True
    except Exception as exc:
        logger.error("Time exit: market sell failed for %s: %s", symbol, exc)
        tg.send_raw(f"⚠️ <b>TIME EXIT FAILED</b> for {symbol}: {exc}")
        return False


def on_fill_event(fill_data: dict[str, Any], expected_price_map: dict[str, float]) -> None:
    """
    Called by Loop B when a fill event arrives from the WebSocket.
    Updates the order record in the DB with actual fill data.
    expected_price_map: {order_id -> expected_fill_price}
    """
    order = fill_data.get("order", {})
    order_id = order.get("id", "")
    filled_at = fill_data.get("timestamp", datetime.datetime.now(_UTC).isoformat())
    filled_qty = float(order.get("filled_qty", 0))
    actual_fill = float(order.get("filled_avg_price") or 0)
    requested_qty = float(order.get("qty", 0))
    partial = int(filled_qty < requested_qty)

    expected = expected_price_map.get(order_id)
    slippage_pct = None
    if expected and expected > 0 and actual_fill > 0:
        slippage_pct = round((actual_fill - expected) / expected * 100, 4)

    submitted_str = order.get("submitted_at", "")
    fill_latency_ms = None
    if submitted_str and filled_at:
        try:
            sub_dt = datetime.datetime.fromisoformat(submitted_str.replace("Z", "+00:00"))
            fill_dt = datetime.datetime.fromisoformat(str(filled_at).replace("Z", "+00:00"))
            fill_latency_ms = int((fill_dt - sub_dt).total_seconds() * 1000)
        except Exception:
            pass

    trade_logger.update_order_fill(order_id, {
        "filled_at": filled_at,
        "filled_qty": filled_qty,
        "actual_fill_price": actual_fill,
        "slippage_pct": slippage_pct,
        "fill_latency_ms": fill_latency_ms,
        "partial_fill": partial,
        "status": "partially_filled" if partial else "filled",
        "pnl_realized": None,  # computed at position close
    })

    tg.alert_fill(
        symbol=order.get("symbol", "?"),
        side=order.get("side", "?"),
        qty=filled_qty,
        fill_price=actual_fill,
        expected_price=expected or actual_fill,
        slippage_pct=slippage_pct or 0.0,
    )
