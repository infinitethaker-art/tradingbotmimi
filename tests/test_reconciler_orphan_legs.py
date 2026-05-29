"""
Tests for reconciler orphan-order classification.

Bug under test: a filled bracket entry leaves TP + SL child orders resting at the
broker. Those child order IDs are never written to order_events (by design — see
trade_logger.update_order_fill "bracket child order — expected"). The reconciler
must NOT flag a held position's own protective legs as orphan orders, or it HALTs
on every restart while any bracketed position is open.

Run: .venv/Scripts/python.exe tests/test_reconciler_orphan_legs.py
"""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tools.reporting import trade_logger
from tools.alerts import telegram_bot as tg
from tools.execution import reconciler
from tools.execution.reconciler import ReconcileStatus


# ── Fakes (Alpaca-shaped) ───────────────────────────────────────────────────
class FakePos:
    def __init__(self, symbol, qty):
        self.symbol = symbol
        self.qty = str(qty)


class FakeOrder:
    def __init__(self, oid, symbol):
        self.id = oid
        self.symbol = symbol


class FakeClient:
    def __init__(self, positions, orders):
        self._positions = positions
        self._orders = orders

    def get_all_positions(self):
        return self._positions

    def get_orders(self):
        return self._orders


# ── Test harness ────────────────────────────────────────────────────────────
def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    trade_logger._DB_PATH = path
    trade_logger.init_db()
    return path


def _insert_open_position(symbol, entry_order_id, qty):
    with trade_logger._conn() as conn:
        conn.execute(
            "INSERT INTO positions (symbol, entry_order_id, entry_price, qty, "
            "notional_usd, stop_price, take_profit, opened_at, status) "
            "VALUES (?,?,?,?,?,?,?,?,'open')",
            (symbol, entry_order_id, 100.0, qty, 500.0, 98.0, 104.0, "2026-05-29T13:31:00+00:00"),
        )


def _insert_filled_entry_order(order_id, symbol):
    with trade_logger._conn() as conn:
        conn.execute(
            "INSERT INTO order_events (order_id, client_order_id, symbol, data_feed, "
            "side, order_type, status) VALUES (?,?,?,?,?,?, 'filled')",
            (order_id, f"{symbol}_X", symbol, "iex", "buy", "bracket"),
        )


def _run(positions, orders):
    # Never hit Telegram during tests
    tg.alert_reconciliation_mismatch = lambda *a, **k: None
    return reconciler.run(FakeClient(positions, orders))


# ── Tests ───────────────────────────────────────────────────────────────────
def test_bracket_legs_of_held_position_are_not_orphans():
    """A tracked open position + its resting TP/SL legs at the broker = CLEAN."""
    _fresh_db()
    _insert_open_position("AAPL", "ENTRY1", qty=2)
    _insert_filled_entry_order("ENTRY1", "AAPL")

    result = _run(
        positions=[FakePos("AAPL", 2)],
        orders=[FakeOrder("leg-tp-aaaa", "AAPL"), FakeOrder("leg-sl-bbbb", "AAPL")],
    )
    assert result.status == ReconcileStatus.CLEAN, (
        f"expected CLEAN, got {result.status}: {result.messages}"
    )


def test_open_order_for_unheld_symbol_is_still_orphan():
    """An open broker order for a symbol we hold no position in is a genuine orphan -> HALT."""
    _fresh_db()
    result = _run(positions=[], orders=[FakeOrder("rogue-1234", "TSLA")])
    assert result.status == ReconcileStatus.HALT, (
        f"expected HALT for genuine orphan, got {result.status}: {result.messages}"
    )


def test_broker_position_not_in_db_still_halts():
    """Phantom position (broker has it, DB doesn't) must still HALT — unchanged safety."""
    _fresh_db()
    result = _run(positions=[FakePos("NVDA", 1)], orders=[])
    assert result.status == ReconcileStatus.HALT, (
        f"expected HALT for phantom position, got {result.status}: {result.messages}"
    )


def test_flat_broker_and_db_is_clean():
    """Nothing open anywhere -> CLEAN."""
    _fresh_db()
    result = _run(positions=[], orders=[])
    assert result.status == ReconcileStatus.CLEAN, (
        f"expected CLEAN, got {result.status}: {result.messages}"
    )


if __name__ == "__main__":
    tests = [
        test_bracket_legs_of_held_position_are_not_orphans,
        test_open_order_for_unheld_symbol_is_still_orphan,
        test_broker_position_not_in_db_still_halts,
        test_flat_broker_and_db_is_clean,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
