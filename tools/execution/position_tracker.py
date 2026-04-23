"""
Position state machine for a single open position per symbol (Phase 1 = SPY only).
Tracks entry, partial exits, full exits, and PnL.
Synchronized from broker fill events via Loop B.
Thread-safe via an explicit threading.Lock.
"""
import datetime
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from tools.reporting import trade_logger

logger = logging.getLogger(__name__)

_UTC = datetime.timezone.utc


@dataclass
class Position:
    symbol: str
    entry_order_id: str
    entry_price: float
    qty: float
    notional_usd: float
    stop_price: float
    take_profit: float
    opened_at: str
    status: str = "open"          # "open" | "closed"
    closed_at: str | None = None
    exit_price: float | None = None
    pnl_realized: float | None = None

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.qty

    def is_open(self) -> bool:
        return self.status == "open"


class PositionTracker:
    """
    Manages one position per symbol. Thread-safe via threading.Lock.
    In Phase 1, there is at most one open position at any time (MAX_OPEN_POSITIONS=1).
    """

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._lock = threading.Lock()

    def open_count(self) -> int:
        with self._lock:
            return sum(1 for p in self._positions.values() if p.is_open())

    def has_open(self, symbol: str) -> bool:
        with self._lock:
            p = self._positions.get(symbol)
            return p is not None and p.is_open()

    def get(self, symbol: str) -> Position | None:
        with self._lock:
            p = self._positions.get(symbol)
            return p if p and p.is_open() else None

    def on_entry_fill(
        self,
        symbol: str,
        entry_order_id: str,
        fill_price: float,
        qty: float,
        notional_usd: float,
        stop_price: float,
        take_profit: float,
        filled_at: str,
    ) -> Position:
        with self._lock:
            if symbol in self._positions and self._positions[symbol].is_open():
                logger.error(
                    "on_entry_fill: %s already has an open position (entry_order=%s). "
                    "This may indicate a duplicate fill or a missed close event. "
                    "Returning existing position without overwriting.",
                    symbol, self._positions[symbol].entry_order_id,
                )
                return self._positions[symbol]

            pos = Position(
                symbol=symbol,
                entry_order_id=entry_order_id,
                entry_price=fill_price,
                qty=qty,
                notional_usd=notional_usd,
                stop_price=stop_price,
                take_profit=take_profit,
                opened_at=filled_at,
            )
            self._positions[symbol] = pos

        # Persist to DB (outside lock to avoid holding lock during I/O)
        with trade_logger._conn() as conn:
            conn.execute(
                """
                INSERT INTO positions
                    (symbol, entry_order_id, entry_price, qty, notional_usd,
                     stop_price, take_profit, opened_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
                """,
                (symbol, entry_order_id, fill_price, qty, notional_usd,
                 stop_price, take_profit, filled_at),
            )

        logger.info("Position opened: %s %.4f @ $%.4f (stop=$%.2f, tp=$%.2f)",
                    symbol, qty, fill_price, stop_price, take_profit)
        return pos

    def on_exit_fill(
        self,
        symbol: str,
        exit_order_id: str,
        exit_price: float,
        qty: float,
        filled_at: str,
        reason: str = "signal",
    ) -> float | None:
        """
        Record an exit fill. Handles partial exits correctly:
        - Partial exit: reduces remaining qty, keeps position open.
        - Full exit: marks position closed and computes realized PnL.
        Returns realized PnL on full close, None otherwise.
        """
        with self._lock:
            pos = self._positions.get(symbol)
            if pos is None or not pos.is_open():
                logger.warning("on_exit_fill: no open position for %s. Ignoring.", symbol)
                return None

            pnl_this_fill = (exit_price - pos.entry_price) * qty
            remaining_qty = pos.qty - qty

            if remaining_qty > 0.001:
                # Partial exit — reduce qty, keep open
                pos.qty = remaining_qty
                logger.info(
                    "Partial exit: %s sold %.4f @ $%.4f | remaining=%.4f | reason: %s",
                    symbol, qty, exit_price, remaining_qty, reason,
                )
                # Update order PnL by exit_order_id, not "latest sell"
                with trade_logger._conn() as conn:
                    conn.execute(
                        "UPDATE order_events SET pnl_realized=? WHERE order_id=?",
                        (pnl_this_fill, exit_order_id),
                    )
                return None

            # Full close
            total_pnl = (exit_price - pos.entry_price) * pos.qty
            pos.status = "closed"
            pos.closed_at = filled_at
            pos.exit_price = exit_price
            pos.pnl_realized = total_pnl

        # Persist outside lock
        with trade_logger._conn() as conn:
            conn.execute(
                """
                UPDATE positions
                SET status='closed', closed_at=?, pnl_realized=?
                WHERE symbol=? AND status='open'
                """,
                (filled_at, total_pnl, symbol),
            )
            conn.execute(
                "UPDATE order_events SET pnl_realized=? WHERE order_id=?",
                (total_pnl, exit_order_id),
            )

        logger.info("Position closed: %s @ $%.4f | PnL: $%.4f | reason: %s",
                    symbol, exit_price, total_pnl, reason)
        return total_pnl

    def load_from_db(self) -> None:
        """
        Restore in-memory state from the DB on startup (after reconciliation).
        Only loads positions that reconciler confirmed are still genuinely open.
        Clears existing in-memory state first to prevent stale data.
        """
        with self._lock:
            self._positions = {}

        with trade_logger._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status='open'"
            ).fetchall()

        with self._lock:
            for row in rows:
                pos = Position(
                    symbol=row["symbol"],
                    entry_order_id=row["entry_order_id"] or "",
                    entry_price=float(row["entry_price"] or 0),
                    qty=float(row["qty"] or 0),
                    notional_usd=float(row["notional_usd"] or 0),
                    stop_price=float(row["stop_price"] or 0),
                    take_profit=float(row["take_profit"] or 0),
                    opened_at=row["opened_at"] or "",
                    status="open",
                )
                self._positions[row["symbol"]] = pos
                logger.info("Restored position from DB: %s %.4f @ $%.4f",
                            pos.symbol, pos.qty, pos.entry_price)

    def summary(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "symbol": p.symbol,
                    "status": p.status,
                    "entry_price": p.entry_price,
                    "qty": p.qty,
                    "notional_usd": p.notional_usd,
                    "stop_price": p.stop_price,
                    "take_profit": p.take_profit,
                    "opened_at": p.opened_at,
                }
                for p in self._positions.values()
                if p.is_open()
            ]
