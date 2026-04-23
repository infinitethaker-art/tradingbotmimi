"""
Startup broker reconciliation.
Compares actual Alpaca account state (positions + open orders) against local DB.
Must run to READY before Loop B accepts any signal intents.

Outcomes:
  CLEAN  — DB and broker match. Safe to proceed.
  FIXED  — Minor discrepancy auto-corrected (e.g. stale DB record closed).
  HALT   — Serious mismatch. New order intents blocked. Alert sent. Manual fix required.
"""
import datetime
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import config
from tools.alerts import telegram_bot as tg
from tools.reporting import trade_logger

logger = logging.getLogger(__name__)

_UTC = datetime.timezone.utc


class ReconcileStatus(str, Enum):
    CLEAN = "CLEAN"
    FIXED = "FIXED"
    HALT = "HALT"


@dataclass
class ReconcileResult:
    status: ReconcileStatus
    messages: list[str]

    def is_safe(self) -> bool:
        return self.status in (ReconcileStatus.CLEAN, ReconcileStatus.FIXED)


def run(trading_client) -> ReconcileResult:
    """
    Run full reconciliation against the live/paper Alpaca account.

    Args:
        trading_client: An alpaca.trading.TradingClient instance.

    Returns:
        ReconcileResult — check .is_safe() before accepting intents.
    """
    config.load()
    messages: list[str] = []
    halted = False

    # ── Fetch broker state ─────────────────────────────────────────────────────
    try:
        broker_positions = {p.symbol: p for p in trading_client.get_all_positions()}
        broker_orders = trading_client.get_orders()  # defaults to open orders
    except Exception as exc:
        msg = f"Failed to fetch broker state: {exc}"
        logger.critical(msg)
        tg.alert_reconciliation_mismatch(msg)
        return ReconcileResult(ReconcileStatus.HALT, [msg])

    # ── Fetch DB state ─────────────────────────────────────────────────────────
    with trade_logger._conn() as conn:
        db_open_positions = conn.execute(
            "SELECT symbol, entry_order_id, qty FROM positions WHERE status='open'"
        ).fetchall()
        db_open_orders = conn.execute(
            "SELECT order_id, client_order_id, symbol, status FROM order_events "
            "WHERE status NOT IN ('filled','canceled','rejected','expired')"
        ).fetchall()

    db_positions = {r["symbol"]: r for r in db_open_positions}
    db_symbols = set(db_positions.keys())
    broker_symbols = set(broker_positions.keys())

    # ── Check 1: broker has position DB doesn't know about ────────────────────
    phantom = broker_symbols - db_symbols
    if phantom:
        msg = (
            f"CRITICAL: Broker shows open positions for {phantom} "
            "but DB has no record. Bot may have restarted mid-trade."
        )
        logger.critical(msg)
        messages.append(msg)
        tg.alert_reconciliation_mismatch(msg)
        halted = True

    # ── Check 2: DB has position broker doesn't show ──────────────────────────
    stale = db_symbols - broker_symbols
    if stale:
        for symbol in stale:
            msg = f"DB shows open position for {symbol} but broker shows none — marking closed in DB."
            logger.warning(msg)
            messages.append(msg)
            closed_at = datetime.datetime.now(_UTC).isoformat()
            with trade_logger._conn() as conn:
                conn.execute(
                    "UPDATE positions SET status='closed', closed_at=? WHERE symbol=? AND status='open'",
                    (closed_at, symbol),
                )

    # ── Check 3: qty mismatch for matched symbols ──────────────────────────────
    for symbol in broker_symbols & db_symbols:
        broker_qty = float(broker_positions[symbol].qty)
        db_qty = float(db_positions[symbol]["qty"] or 0)
        if abs(broker_qty - db_qty) > 0.01:
            msg = (
                f"CRITICAL: Position qty mismatch for {symbol}: "
                f"broker={broker_qty:.4f}, DB={db_qty:.4f}."
            )
            logger.critical(msg)
            messages.append(msg)
            tg.alert_reconciliation_mismatch(msg)
            halted = True

    # ── Check 4: orphan orders in Alpaca not tracked in DB ────────────────────
    broker_order_ids = {o.id for o in broker_orders}
    db_order_ids = {r["order_id"] for r in db_open_orders}
    orphans = broker_order_ids - db_order_ids

    if orphans:
        msg = (
            f"CRITICAL: {len(orphans)} orphan order(s) found in broker not in DB: "
            f"{[str(oid)[:8] for oid in orphans]}. Manual investigation required."
        )
        logger.critical(msg)
        messages.append(msg)
        tg.alert_reconciliation_mismatch(msg)
        halted = True

    # ── Result ────────────────────────────────────────────────────────────────
    if halted:
        return ReconcileResult(ReconcileStatus.HALT, messages)

    if stale:
        logger.info("Reconciliation complete: stale DB positions fixed. Safe to proceed.")
        return ReconcileResult(ReconcileStatus.FIXED, messages or ["Stale DB positions closed."])

    logger.info("Reconciliation complete: CLEAN.")
    return ReconcileResult(ReconcileStatus.CLEAN, ["Broker and DB state match."])
