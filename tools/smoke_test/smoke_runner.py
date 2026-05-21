"""
Remote paper-only execution smoke test.
Triggered by /smoketest Telegram command via the command listener in telegram_bot.py.

Validates the full entry path:
  safety guard → risk checks → order submission → broker fill confirmation

Loop B's existing WebSocket handler processes fills, DB updates, position tracking,
and Telegram fill alerts. This runner does NOT open a second WebSocket.

Hard safety rule: smoke_test_guard() hard-fails on any non-paper condition.
Max 1 smoke test trade per trading day.
"""
import datetime
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import config
from tools.alerts import telegram_bot as tg
from tools.data import data_feed, market_calendar
from tools.features import features
from tools.reporting import trade_logger
from tools.risk.risk_checks import RiskState, apply_result, check_entry

if TYPE_CHECKING:
    from alpaca.trading.client import TradingClient
    from scheduler.loop_b import LoopB

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_SYMBOL = "SPY"
_POLL_INTERVAL_SEC = 5
_POLL_TIMEOUT_SEC = 120
_SMOKE_PREFIX = "🧪 <b>EXECUTION TEST — NOT STRATEGY SIGNAL</b>\n"


def _daily_smoke_count() -> int:
    """Return number of smoke test signals logged today that were not rejected."""
    today = datetime.datetime.now(_ET).date().isoformat()
    try:
        import sqlite3
        import os
        db_path = os.path.join(os.path.dirname(__file__), "../../db/trades.db")
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM signal_events
            WHERE trading_date_et = ?
              AND is_smoke_test = 1
              AND disposition != 'REJECTED'
            """,
            (today,),
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0
    except Exception as exc:
        logger.error("_daily_smoke_count query failed: %s", exc)
        return 0


def run(loop_b: "LoopB", trading_client: "TradingClient") -> None:
    """
    Execute the smoke test entry path. Called in a daemon thread by the Telegram
    command listener. Runs entirely within the existing bot process on Railway.
    """
    config.load()

    # ── 1. Hard safety guard ──────────────────────────────────────────────────
    try:
        config.smoke_test_guard()
    except RuntimeError as exc:
        msg = f"{_SMOKE_PREFIX}⛔ Guard failed: {exc}"
        logger.error("Smoke test guard failed: %s", exc)
        tg.send_raw(msg)
        return

    # ── 2. Trading day check ──────────────────────────────────────────────────
    if not market_calendar.is_trading_day():
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Not a trading day — smoke test aborted.")
        return

    # ── 3. Market hours check (delegated to risk check, but fail early here) ──
    if not market_calendar.is_within_trading_window(
        start_offset_min=config.TRADE_START_OFFSET_MIN,
        end_offset_min=config.TRADE_END_OFFSET_MIN,
    ):
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Outside trading window — smoke test aborted.")
        return

    # ── 4. Daily limit: max 1 smoke trade per day ─────────────────────────────
    if _daily_smoke_count() >= 1:
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Smoke test already run today (max 1 per day).")
        return

    tg.send_raw(f"{_SMOKE_PREFIX}Running safety checks…")
    logger.info("Smoke test initiated.")

    # ── 5. Fetch market data ──────────────────────────────────────────────────
    try:
        bars = data_feed.fetch_bars(_SYMBOL, n_bars=50)
    except Exception as exc:
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Failed to fetch bars: {exc}")
        logger.error("Smoke test: fetch_bars failed: %s", exc)
        return

    if len(bars) < 27:
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Not enough bars ({len(bars)}/27) — market may be pre-open.")
        return

    try:
        quote = data_feed.fetch_latest_quote(_SYMBOL)
    except Exception as exc:
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Failed to fetch quote: {exc}")
        logger.error("Smoke test: fetch_latest_quote failed: %s", exc)
        return

    # ── 6. Compute features ───────────────────────────────────────────────────
    computed = features.compute(bars)
    if len(computed) < 2:
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Insufficient computed features — cannot build signal.")
        return

    curr = computed[-1]
    prev = computed[-2]

    # ── 7. Build synthetic SignalEvent ────────────────────────────────────────
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    bar_ts = curr.get("timestamp", now_utc.isoformat())
    session_ctx_type = market_calendar.session_type(now_utc.astimezone(_ET))

    try:
        account = trading_client.get_account()
        session_equity = float(account.equity)
    except Exception as exc:
        logger.warning("Smoke test: could not fetch equity: %s — using 0.0", exc)
        session_equity = 0.0

    daily_loss_limit = session_equity * config.MAX_DAILY_LOSS_PCT

    # Compute signal latency from bar timestamp
    signal_latency_ms: int | None = None
    try:
        bar_dt = datetime.datetime.fromisoformat(bar_ts)
        if bar_dt.tzinfo is None:
            bar_dt = bar_dt.replace(tzinfo=datetime.timezone.utc)
        signal_latency_ms = int((now_utc - bar_dt).total_seconds() * 1000)
    except Exception:
        pass

    rel_vol = curr.get("relative_volume")
    volume_ok = rel_vol is not None and rel_vol >= config.MIN_RELATIVE_VOLUME
    rsi = curr.get("rsi_14")

    signal_event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "timestamp": now_utc.isoformat(),
        "symbol": _SYMBOL,
        "data_feed": curr.get("data_feed", config.ALPACA_DATA_FEED),
        "session_type": session_ctx_type,
        "bar_timestamp": bar_ts,
        "signal_type": "ENTER_LONG",
        "disposition": "PENDING",
        "rejection_reason": None,
        "macd_line": curr.get("macd_line"),
        "macd_signal_line": curr.get("macd_signal_line"),
        "macd_hist": curr.get("macd_hist"),
        "rsi_14": rsi,
        "ema_20": curr.get("ema_20"),
        "bar_close": curr.get("bar_close"),
        "bar_volume": curr.get("bar_volume"),
        "relative_volume": rel_vol,
        "relative_volume_ok": int(volume_ok),
        "iex_bid": quote.get("bid_price"),
        "iex_ask": quote.get("ask_price"),
        "iex_spread_pct": quote.get("spread_pct"),
        "market_regime": "SMOKE_TEST",
        "signal_latency_ms": signal_latency_ms,
        "session_start_equity": session_equity,
        "daily_loss_limit_usd": daily_loss_limit,
        "session_pnl_at_signal": 0.0,
        "is_smoke_test": 1,
    }

    # ── 8. Risk checks ────────────────────────────────────────────────────────
    risk_state = RiskState(session_equity)
    try:
        daytrade_count = int(getattr(account, "daytrade_count", 0) or 0)
        risk_state.set_daytrade_count(daytrade_count)
        risk_state.set_open_positions(loop_b._position_tracker.open_count())
    except Exception as exc:
        logger.warning("Smoke test: risk state init partial failure: %s", exc)

    result = check_entry(signal_event, risk_state, session_equity)
    apply_result(signal_event, result)

    trade_logger.log_signal(signal_event)

    if not result.passed:
        reason = result.rejection_reason or "unknown"
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Risk gate rejected: <b>{reason}</b>\n{result.details}")
        logger.info("Smoke test risk rejected: %s", reason)
        return

    # ── 9. Submit bracket order ───────────────────────────────────────────────
    from tools.execution import order_manager

    tg.send_raw(
        f"{_SMOKE_PREFIX}"
        f"Submitting ${config.TEST_NOTIONAL_USD:.0f} bracket order {_SYMBOL}…"
    )

    try:
        result_tuple = order_manager.submit_bracket_entry(
            trading_client,
            signal_event,
            notional_usd=config.TEST_NOTIONAL_USD,
            is_smoke_test=True,
            alert_prefix=_SMOKE_PREFIX,
        )
    except Exception as exc:
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Order submission raised: {exc}")
        logger.error("Smoke test: submit_bracket_entry raised: %s", exc)
        return

    if result_tuple is None:
        tg.send_raw(f"{_SMOKE_PREFIX}⛔ Order submission failed or duplicate — check logs.")
        logger.error("Smoke test: submit_bracket_entry returned None.")
        return

    order_event, tp_price, sl_stop = result_tuple
    order_id = order_event["order_id"]

    # ── 10. Register with Loop B so fill handler has bracket prices + notional ─
    loop_b.register_pending_order(
        order_id=order_id,
        tp_price=tp_price,
        sl_stop=sl_stop,
        expected_price=order_event["expected_fill_price"],
        notional_usd=config.TEST_NOTIONAL_USD,
    )
    logger.info("Smoke test order submitted: %s", order_id[:8])

    # ── 11. Poll for fill confirmation (REST, no WebSocket) ───────────────────
    deadline = time.monotonic() + _POLL_TIMEOUT_SEC
    confirmed = False
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_SEC)
        try:
            order = trading_client.get_order_by_id(order_id)
            status = str(order.status).lower()
            if status in ("filled", "partially_filled"):
                confirmed = True
                break
            if status in ("canceled", "expired", "rejected"):
                tg.send_raw(
                    f"{_SMOKE_PREFIX}⚠️ Order terminal status: <b>{status}</b>\n"
                    "Bracket order did not fill — check broker."
                )
                logger.warning("Smoke test order terminal status: %s", status)
                return
        except Exception as exc:
            logger.warning("Smoke test: poll error (will retry): %s", exc)

    if confirmed:
        tg.send_raw(
            f"{_SMOKE_PREFIX}"
            f"✅ Entry fill confirmed by broker.\n"
            f"Loop B is processing fill, DB update, and position tracking."
        )
        logger.info("Smoke test fill confirmed for order %s.", order_id[:8])
    else:
        tg.send_raw(
            f"{_SMOKE_PREFIX}"
            f"⚠️ Order submitted but fill not confirmed in {_POLL_TIMEOUT_SEC}s.\n"
            f"Order ID: <code>{order_id[:8]}</code>\n"
            "Monitor broker manually."
        )
        logger.warning("Smoke test: fill not confirmed within %ds for %s.", _POLL_TIMEOUT_SEC, order_id[:8])
