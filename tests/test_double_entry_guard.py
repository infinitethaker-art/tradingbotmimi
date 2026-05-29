"""
Belt-and-suspenders guard against duplicate entries.

The in-memory `has_open(symbol)` check in Loop A can miss the window between
submitting an entry and its fill (and a missed WS fill can leave it stale). The
broker is the source of truth: submit_bracket_entry must NOT open a second position
in a symbol the broker already holds, or already has a working order for.

Run: .venv/Scripts/python.exe tests/test_double_entry_guard.py
"""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tools.execution import order_manager
from tools.reporting import trade_logger
from tools.alerts import telegram_bot as tg


class _FakePos:
    def __init__(self, symbol):
        self.symbol = symbol
        self.qty = "1"


class _FakeOrder:
    def __init__(self, symbol):
        self.symbol = symbol
        self.id = "open-order-1"


class _FakeSubmitted:
    def __init__(self):
        self.id = "new-order-123"
        self.status = "accepted"
        self.client_order_id = "AAPL_20260529_ENTER_1415"
        self.submitted_at = None


class _FakeClient:
    def __init__(self, positions=None, orders=None):
        self._positions = positions or []
        self._orders = orders or []
        self.submit_called = False

    def get_order_by_client_id(self, cid):
        raise Exception("404 not found")  # never previously submitted

    def get_all_positions(self):
        return self._positions

    def get_orders(self):
        return self._orders

    def submit_order(self, req):
        self.submit_called = True
        return _FakeSubmitted()


_SIGNAL = {
    "symbol": "AAPL",
    "bar_close": 314.0,
    "bar_timestamp": "2026-05-29T14:15:00+00:00",
    "event_id": "ev-1",
    "data_feed": "iex",
}


def _setup():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    trade_logger._DB_PATH = path
    trade_logger.init_db()
    # order_events.signal_event_id has a FK to signal_events — insert the parent row
    # so a successful submit can log its order without tripping the constraint.
    with trade_logger._conn() as conn:
        conn.execute(
            "INSERT INTO signal_events (event_id, timestamp, symbol, data_feed) VALUES (?,?,?,?)",
            ("ev-1", "2026-05-29T14:15:00+00:00", "AAPL", "iex"),
        )
    tg.alert_order_submitted = lambda *a, **k: None
    tg.send_raw = lambda *a, **k: None


def test_skips_entry_when_broker_holds_position():
    _setup()
    c = _FakeClient(positions=[_FakePos("AAPL")])
    res = order_manager.submit_bracket_entry(c, dict(_SIGNAL))
    assert res is None, "must skip when broker already holds the position"
    assert c.submit_called is False, "must NOT submit an order"


def test_skips_entry_when_broker_has_open_order():
    _setup()
    c = _FakeClient(orders=[_FakeOrder("AAPL")])
    res = order_manager.submit_bracket_entry(c, dict(_SIGNAL))
    assert res is None, "must skip when broker has a working order for the symbol"
    assert c.submit_called is False, "must NOT submit an order"


def test_submits_when_broker_flat():
    _setup()
    c = _FakeClient()  # no positions, no orders
    res = order_manager.submit_bracket_entry(c, dict(_SIGNAL))
    assert res is not None, "must submit when broker is flat for the symbol"
    assert c.submit_called is True, "should reach submit_order"


def test_open_order_for_other_symbol_does_not_block():
    _setup()
    c = _FakeClient(positions=[_FakePos("TSLA")], orders=[_FakeOrder("NVDA")])
    res = order_manager.submit_bracket_entry(c, dict(_SIGNAL))
    assert res is not None, "exposure in OTHER symbols must not block this entry"
    assert c.submit_called is True


if __name__ == "__main__":
    tests = [
        test_skips_entry_when_broker_holds_position,
        test_skips_entry_when_broker_has_open_order,
        test_submits_when_broker_flat,
        test_open_order_for_other_symbol_does_not_block,
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
