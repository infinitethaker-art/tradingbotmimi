"""
Schema-enforced SQLite logging for signal events and order events.
Every write validates that all required fields are present.
A missing or empty required field raises ValueError before any DB write occurs.
The data_feed field is required on every record — it is never optional.

trading_date_et is stored on every record (ET date of the event) so that
daily_summary() always aligns to US trading sessions, not host-timezone UTC.
"""
import datetime
import logging
import os
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "../../db/trades.db")

# ── Required fields per schema ─────────────────────────────────────────────────
_SIGNAL_REQUIRED = {
    "event_id", "timestamp", "symbol", "data_feed", "session_type",
    "bar_timestamp", "signal_type", "disposition", "rejection_reason",
    "macd_line", "macd_signal_line", "macd_hist", "rsi_14", "ema_20",
    "bar_close", "bar_volume", "relative_volume", "relative_volume_ok",
    "iex_bid", "iex_ask", "iex_spread_pct",
    "market_regime", "signal_latency_ms",
    "session_start_equity", "daily_loss_limit_usd", "session_pnl_at_signal",
}

_ORDER_REQUIRED = {
    "order_id", "client_order_id", "signal_event_id", "symbol", "data_feed",
    "side", "order_type", "qty", "notional_usd",
    "limit_price", "stop_price",
    "submitted_at", "filled_at",
    "filled_qty", "partial_fill",
    "expected_fill_price", "actual_fill_price",
    "slippage_pct", "fill_latency_ms",
    "status", "broker_reject_reason", "pnl_realized",
}

_NON_EMPTY_REQUIRED = {"event_id", "timestamp", "symbol", "data_feed"}


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _trading_date_et(timestamp_iso: str) -> str:
    """Convert a UTC ISO timestamp string to an ET trading date string (YYYY-MM-DD)."""
    try:
        dt = datetime.datetime.fromisoformat(timestamp_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(ET).date().isoformat()
    except (ValueError, TypeError):
        return datetime.datetime.now(ET).date().isoformat()


def init_db() -> None:
    """Create tables if they do not exist. Safe to call on every startup."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_events (
                event_id            TEXT PRIMARY KEY,
                timestamp           TEXT NOT NULL,
                symbol              TEXT NOT NULL,
                data_feed           TEXT NOT NULL,
                session_type        TEXT,
                bar_timestamp       TEXT,
                signal_type         TEXT,
                disposition         TEXT,
                rejection_reason    TEXT,
                macd_line           REAL,
                macd_signal_line    REAL,
                macd_hist           REAL,
                rsi_14              REAL,
                ema_20              REAL,
                bar_close           REAL,
                bar_volume          INTEGER,
                relative_volume     REAL,
                relative_volume_ok  INTEGER,
                iex_bid             REAL,
                iex_ask             REAL,
                iex_spread_pct      REAL,
                market_regime       TEXT,
                signal_latency_ms   INTEGER,
                session_start_equity    REAL,
                daily_loss_limit_usd    REAL,
                session_pnl_at_signal   REAL,
                trading_date_et     TEXT,
                is_smoke_test       INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_events (
                order_id                TEXT PRIMARY KEY,
                client_order_id         TEXT NOT NULL,
                signal_event_id         TEXT,
                symbol                  TEXT NOT NULL,
                data_feed               TEXT NOT NULL,
                side                    TEXT,
                order_type              TEXT,
                qty                     REAL,
                notional_usd            REAL,
                limit_price             REAL,
                stop_price              REAL,
                submitted_at            TEXT,
                filled_at               TEXT,
                filled_qty              REAL,
                partial_fill            INTEGER,
                expected_fill_price     REAL,
                actual_fill_price       REAL,
                slippage_pct            REAL,
                fill_latency_ms         INTEGER,
                status                  TEXT,
                broker_reject_reason    TEXT,
                pnl_realized            REAL,
                trading_date_et         TEXT,
                is_smoke_test           INTEGER DEFAULT 0,
                FOREIGN KEY (signal_event_id) REFERENCES signal_events(event_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT NOT NULL,
                entry_order_id  TEXT,
                entry_price     REAL,
                qty             REAL,
                notional_usd    REAL,
                stop_price      REAL,
                take_profit     REAL,
                opened_at       TEXT,
                closed_at       TEXT,
                pnl_realized    REAL,
                status          TEXT DEFAULT 'open'
            )
        """)
        # Migration guards for existing databases
        for ddl in (
            "ALTER TABLE signal_events ADD COLUMN trading_date_et TEXT",
            "ALTER TABLE order_events ADD COLUMN trading_date_et TEXT",
            "ALTER TABLE signal_events ADD COLUMN relative_volume_ok INTEGER",
            "ALTER TABLE signal_events ADD COLUMN is_smoke_test INTEGER DEFAULT 0",
            "ALTER TABLE order_events ADD COLUMN is_smoke_test INTEGER DEFAULT 0",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists


def _validate(record: dict[str, Any], required: set[str], label: str) -> None:
    missing = required - set(record.keys())
    if missing:
        raise ValueError(f"{label} record missing required fields: {sorted(missing)}")
    for field in _NON_EMPTY_REQUIRED:
        if field in required and not record.get(field):
            raise ValueError(f"{label} field '{field}' must be a non-empty value.")


def log_signal(event: dict[str, Any]) -> None:
    """Write a signal event to signal_events. Raises on schema violations."""
    _validate(event, _SIGNAL_REQUIRED, "SignalEvent")
    row = dict(event)
    row["trading_date_et"] = _trading_date_et(event["timestamp"])
    row.setdefault("is_smoke_test", 0)
    with _conn() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO signal_events (
                event_id, timestamp, symbol, data_feed, session_type,
                bar_timestamp, signal_type, disposition, rejection_reason,
                macd_line, macd_signal_line, macd_hist, rsi_14, ema_20,
                bar_close, bar_volume, relative_volume, relative_volume_ok,
                iex_bid, iex_ask, iex_spread_pct,
                market_regime, signal_latency_ms,
                session_start_equity, daily_loss_limit_usd, session_pnl_at_signal,
                trading_date_et, is_smoke_test
            ) VALUES (
                :event_id, :timestamp, :symbol, :data_feed, :session_type,
                :bar_timestamp, :signal_type, :disposition, :rejection_reason,
                :macd_line, :macd_signal_line, :macd_hist, :rsi_14, :ema_20,
                :bar_close, :bar_volume, :relative_volume, :relative_volume_ok,
                :iex_bid, :iex_ask, :iex_spread_pct,
                :market_regime, :signal_latency_ms,
                :session_start_equity, :daily_loss_limit_usd, :session_pnl_at_signal,
                :trading_date_et, :is_smoke_test
            )
            """,
            row,
        )
        if cursor.rowcount == 0:
            logger.warning("log_signal: INSERT ignored — event_id already exists: %s", event["event_id"][:8])
    logger.debug("Signal logged: %s %s %s", event["event_id"][:8], event["symbol"], event["signal_type"])


def log_order(event: dict[str, Any]) -> None:
    """Write an order event to order_events. Raises on schema violations."""
    _validate(event, _ORDER_REQUIRED, "OrderEvent")
    row = dict(event)
    row["trading_date_et"] = _trading_date_et(event.get("submitted_at") or event.get("timestamp", ""))
    row.setdefault("is_smoke_test", 0)
    with _conn() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO order_events (
                order_id, client_order_id, signal_event_id, symbol, data_feed,
                side, order_type, qty, notional_usd,
                limit_price, stop_price,
                submitted_at, filled_at,
                filled_qty, partial_fill,
                expected_fill_price, actual_fill_price,
                slippage_pct, fill_latency_ms,
                status, broker_reject_reason, pnl_realized,
                trading_date_et, is_smoke_test
            ) VALUES (
                :order_id, :client_order_id, :signal_event_id, :symbol, :data_feed,
                :side, :order_type, :qty, :notional_usd,
                :limit_price, :stop_price,
                :submitted_at, :filled_at,
                :filled_qty, :partial_fill,
                :expected_fill_price, :actual_fill_price,
                :slippage_pct, :fill_latency_ms,
                :status, :broker_reject_reason, :pnl_realized,
                :trading_date_et, :is_smoke_test
            )
            """,
            row,
        )
        if cursor.rowcount == 0:
            logger.warning("log_order: INSERT ignored — order_id already exists: %s", event["order_id"][:8])
    logger.debug("Order logged: %s %s %s", event["order_id"][:8], event["symbol"], event["status"])


def update_order_fill(order_id: str, fill_data: dict[str, Any]) -> None:
    """Update fill details after a WebSocket fill event arrives."""
    with _conn() as conn:
        cursor = conn.execute(
            """
            UPDATE order_events
            SET filled_at=:filled_at, filled_qty=:filled_qty,
                actual_fill_price=:actual_fill_price, slippage_pct=:slippage_pct,
                fill_latency_ms=:fill_latency_ms, partial_fill=:partial_fill,
                status=:status, pnl_realized=:pnl_realized
            WHERE order_id=:order_id
            """,
            {"order_id": order_id, **fill_data},
        )
        if cursor.rowcount == 0:
            logger.debug("update_order_fill: no row for order_id=%s (bracket child order — expected)", order_id[:8] if order_id else "?")


def get_open_signal_ids() -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT event_id FROM signal_events WHERE disposition='SUBMITTED' ORDER BY timestamp DESC LIMIT 10"
        ).fetchall()
    return [r["event_id"] for r in rows]


def daily_summary(date: str | None = None) -> dict[str, Any]:
    """Return aggregated PnL and trade counts for a trading date (YYYY-MM-DD ET)."""
    if date is None:
        date = datetime.datetime.now(ET).date().isoformat()
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total_signals,
                COALESCE(SUM(CASE WHEN disposition='SUBMITTED' THEN 1 ELSE 0 END), 0) as taken,
                COALESCE(SUM(CASE WHEN disposition='REJECTED' THEN 1 ELSE 0 END), 0) as rejected
            FROM signal_events
            WHERE trading_date_et = ?
              AND (is_smoke_test IS NULL OR is_smoke_test = 0)
            """,
            (date,),
        ).fetchone()
        pnl_row = conn.execute(
            """
            SELECT COALESCE(SUM(pnl_realized), 0) as total_pnl,
                   COUNT(*) as fills
            FROM order_events
            WHERE trading_date_et = ? AND status='filled'
              AND (is_smoke_test IS NULL OR is_smoke_test = 0)
            """,
            (date,),
        ).fetchone()
    return {
        "date": date,
        "total_signals": row["total_signals"],
        "taken": row["taken"],
        "rejected": row["rejected"],
        "fills": pnl_row["fills"],
        "total_pnl": round(pnl_row["total_pnl"], 4),
    }
