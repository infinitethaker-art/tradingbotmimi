"""
MACD + RSI crossover signal engine.

Signal logic (LONG only in Phase 1):
  ENTER_LONG:
    - MACD line crosses above signal line (hist goes from negative/zero to positive)
    - RSI(14) is between RSI_LOW and RSI_HIGH at crossover time

  EXIT_LONG:
    - MACD hist crosses from positive to negative/zero (MACD line drops below signal)

  NO_SIGNAL: none of the above conditions met

Signal detection is purely technical — no time-window gating here.
Time-window and other entry gates are enforced downstream by risk_checks.check_entry().

Output: a typed SignalEvent dict with all fields required by the DB schema.
Missing any required field raises ValueError before the dict is returned.
disposition is always 'PENDING' here — risk layer sets the final disposition.
"""
import datetime
import uuid
from typing import Any
from zoneinfo import ZoneInfo

import config
from tools.data.market_calendar import session_type

ET = ZoneInfo("America/New_York")

# RSI bands loaded from config — tune via RSI_LOW / RSI_HIGH in .env

_REQUIRED_SIGNAL_FIELDS = {
    "event_id", "timestamp", "symbol", "data_feed", "session_type",
    "bar_timestamp", "signal_type", "disposition",
    "rejection_reason",
    "macd_line", "macd_signal_line", "macd_hist",
    "rsi_14", "ema_20", "bar_close", "bar_volume", "relative_volume",
    "relative_volume_ok",
    "iex_bid", "iex_ask", "iex_spread_pct",
    "market_regime",
    "signal_latency_ms",
    "session_start_equity", "daily_loss_limit_usd", "session_pnl_at_signal",
}


def _validate(event: dict[str, Any]) -> None:
    missing = _REQUIRED_SIGNAL_FIELDS - set(event.keys())
    if missing:
        raise ValueError(f"SignalEvent is missing required fields: {sorted(missing)}")
    for field in ("event_id", "timestamp", "symbol", "data_feed"):
        if not event.get(field):
            raise ValueError(f"SignalEvent field '{field}' must be a non-empty string.")


def _crossover(prev_hist: float | None, curr_hist: float) -> bool:
    """True when MACD histogram crosses from negative (or zero) to positive."""
    if prev_hist is None:
        return False
    return prev_hist <= 0 < curr_hist


def _crossunder(prev_hist: float | None, curr_hist: float) -> bool:
    """True when MACD histogram crosses from positive to negative (or zero)."""
    if prev_hist is None:
        return False
    return prev_hist > 0 >= curr_hist


def evaluate(
    features_series: list[dict[str, Any]],
    quote: dict[str, Any],
    session_context: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate the most recent two feature rows and produce a SignalEvent.

    Args:
        features_series: List of feature dicts (ascending by timestamp), at least 2 items.
        quote: Output of data_feed.fetch_latest_quote() for the symbol.
        session_context: Dict with keys:
            symbol, session_start_equity, daily_loss_limit_usd,
            session_pnl_at_signal, market_regime (str)

    Returns:
        A complete SignalEvent dict ready for trade_logger.
        disposition is always 'PENDING' — risk_checks sets the final disposition.
    """
    config.load()

    if len(features_series) < 2:
        raise ValueError("Need at least 2 feature rows to detect a crossover.")

    # Sort defensively by timestamp ascending
    sorted_series = sorted(
        features_series,
        key=lambda r: r.get("timestamp", ""),
    )

    curr = sorted_series[-1]
    prev = sorted_series[-2]

    # Symbol consistency check
    ctx_symbol = session_context["symbol"]
    feat_symbol = curr.get("symbol")
    if feat_symbol and feat_symbol != ctx_symbol:
        raise ValueError(
            f"Symbol mismatch: session_context has '{ctx_symbol}' "
            f"but feature row has '{feat_symbol}'."
        )

    prev_hist: float | None = prev.get("macd_hist")
    curr_hist: float = curr["macd_hist"]
    rsi: float | None = curr.get("rsi_14")

    now = datetime.datetime.now(datetime.timezone.utc)

    # Compute signal latency as age of the bar (how old is the data we're acting on)
    bar_ts_str = curr.get("timestamp")
    signal_latency_ms: int | None = None
    if bar_ts_str:
        try:
            bar_dt = datetime.datetime.fromisoformat(bar_ts_str)
            if bar_dt.tzinfo is None:
                bar_dt = bar_dt.replace(tzinfo=datetime.timezone.utc)
            signal_latency_ms = int((now - bar_dt).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass

    # Volume confirmation for entries
    rel_vol = curr.get("relative_volume")
    volume_ok = rel_vol is not None and rel_vol >= config.MIN_RELATIVE_VOLUME

    # Pure technical signal detection — no time-window gating here
    macd_cross = _crossover(prev_hist, curr_hist)
    rsi_ok = rsi is not None and config.RSI_LOW <= rsi <= config.RSI_HIGH

    if macd_cross and rsi_ok and volume_ok:
        signal_type = "ENTER_LONG"
    elif _crossunder(prev_hist, curr_hist):
        signal_type = "EXIT_LONG"
    else:
        signal_type = "NO_SIGNAL"

    # Per-tick scan log — one compact INFO line showing indicator values and pass/fail per condition
    if config.ENABLE_SCAN_LOGS:
        spread = quote.get("spread_pct")
        spread_str = f"{spread:.3f}%" if spread is not None else "N/A"
        macd_sym = "✓" if macd_cross else f"✗({prev_hist:+.4f}→{curr_hist:+.4f})"
        rsi_sym = "✓" if rsi_ok else (
            f"✗({rsi:.1f} {'HIGH' if rsi is not None and rsi > config.RSI_HIGH else 'LOW'})"
            if rsi is not None else "✗(N/A)"
        )
        vol_sym = f"✓({rel_vol:.2f}x)" if volume_ok else (
            f"✗({rel_vol:.2f}x)" if rel_vol is not None else "✗(N/A)"
        )
        bar_time = (bar_ts_str or "")[-5:] if bar_ts_str else "?"
        logger.info(
            "SCAN %s %s — MACD%s RSI%s VOL%s SPREAD(%s) → %s",
            ctx_symbol, bar_time, macd_sym, rsi_sym, vol_sym, spread_str, signal_type,
        )

    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "timestamp": now.isoformat(),
        "symbol": ctx_symbol,
        "data_feed": curr.get("data_feed", config.ALPACA_DATA_FEED),
        "session_type": session_type(now.astimezone(ET)),
        "bar_timestamp": bar_ts_str,
        "signal_type": signal_type,
        "disposition": "PENDING",
        "rejection_reason": None,
        # Features
        "macd_line": curr.get("macd_line"),
        "macd_signal_line": curr.get("macd_signal_line"),
        "macd_hist": curr_hist,
        "rsi_14": rsi,
        "ema_20": curr.get("ema_20"),
        "bar_close": curr.get("bar_close"),
        "bar_volume": curr.get("bar_volume"),
        "relative_volume": rel_vol,
        "relative_volume_ok": volume_ok,
        # Quote (labelled by configured feed)
        "iex_bid": quote.get("bid_price"),
        "iex_ask": quote.get("ask_price"),
        "iex_spread_pct": quote.get("spread_pct"),
        # Context
        "market_regime": session_context.get("market_regime", "UNKNOWN"),
        "signal_latency_ms": signal_latency_ms,
        "session_start_equity": session_context.get("session_start_equity"),
        "daily_loss_limit_usd": session_context.get("daily_loss_limit_usd"),
        "session_pnl_at_signal": session_context.get("session_pnl_at_signal", 0.0),
    }

    _validate(event)
    return event
