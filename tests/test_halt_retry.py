"""
Tests for the "alert once + quiet retry with backoff" HALT policy.

A genuine reconciliation HALT must NOT crash-restart the process every ~36s
(which Railway's restartPolicy=ALWAYS turns into an alert storm). Instead Loop B
holds the process alive, retries reconciliation on a backoff, alerts ONCE, and
resumes when the broker/DB realign.

Run: .venv/Scripts/python.exe tests/test_halt_retry.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tools.execution import reconciler
from tools.execution.reconciler import ReconcileResult, ReconcileStatus
from tools.alerts import telegram_bot as tg


class _FakePos:
    def __init__(self, symbol, qty):
        self.symbol = symbol
        self.qty = str(qty)


class _FakeClient:
    def __init__(self, positions=None, orders=None):
        self._positions = positions or []
        self._orders = orders or []

    def get_all_positions(self):
        return self._positions

    def get_orders(self):
        return self._orders


# ── reconciler send_alert flag ───────────────────────────────────────────────
def test_reconciler_suppresses_alert_when_send_alert_false():
    """With send_alert=False the reconciler must NOT call the Telegram alert."""
    calls = []
    tg.alert_reconciliation_mismatch = lambda *a, **k: calls.append(a)
    # phantom position -> would normally alert
    result = reconciler.run(_FakeClient(positions=[_FakePos("NVDA", 1)]), send_alert=False)
    assert result.status == ReconcileStatus.HALT, result.status
    assert calls == [], f"expected no Telegram alerts, got {len(calls)}"


def test_reconciler_sends_alert_by_default():
    """Default behaviour (send_alert=True) still alerts — unchanged for callers."""
    calls = []
    tg.alert_reconciliation_mismatch = lambda *a, **k: calls.append(a)
    result = reconciler.run(_FakeClient(positions=[_FakePos("NVDA", 1)]))
    assert result.status == ReconcileStatus.HALT
    assert len(calls) >= 1, "expected at least one Telegram alert by default"


# ── LoopB._reconcile_until_safe: retry + alert once ──────────────────────────
def test_reconcile_until_safe_retries_then_proceeds_alerting_once():
    """HALT, HALT, CLEAN -> returns CLEAN, reconciler called 3x, alert only on first."""
    from scheduler import loop_b as loop_b_mod
    from scheduler.loop_b import LoopB

    # Make backoff instant for the test
    loop_b_mod.RECONCILE_RETRY_BASE_SEC = 0.0
    loop_b_mod.RECONCILE_RETRY_MAX_SEC = 0.0

    seq = [
        ReconcileResult(ReconcileStatus.HALT, ["mismatch"]),
        ReconcileResult(ReconcileStatus.HALT, ["mismatch"]),
        ReconcileResult(ReconcileStatus.CLEAN, ["ok"]),
    ]
    send_alert_flags = []

    def fake_run(client, send_alert=True):
        send_alert_flags.append(send_alert)
        return seq[len(send_alert_flags) - 1]

    loop_b_mod.reconciler.run = fake_run
    tg.send_raw = lambda *a, **k: None

    lb = LoopB(risk_state=None, position_tracker=None, intent_queue=None)
    lb._trading_client = _FakeClient()

    result = lb._reconcile_until_safe()

    assert result is not None and result.status == ReconcileStatus.CLEAN, result
    assert len(send_alert_flags) == 3, f"expected 3 reconcile attempts, got {len(send_alert_flags)}"
    assert send_alert_flags == [True, False, False], (
        f"alert must fire only on first attempt, got {send_alert_flags}"
    )


def test_reconcile_until_safe_returns_none_when_stopped():
    """If stop is signalled while halted, return None (no infinite hold)."""
    from scheduler import loop_b as loop_b_mod
    from scheduler.loop_b import LoopB

    loop_b_mod.RECONCILE_RETRY_BASE_SEC = 0.0
    loop_b_mod.RECONCILE_RETRY_MAX_SEC = 0.0
    loop_b_mod.reconciler.run = lambda client, send_alert=True: ReconcileResult(
        ReconcileStatus.HALT, ["mismatch"]
    )
    tg.send_raw = lambda *a, **k: None

    lb = LoopB(risk_state=None, position_tracker=None, intent_queue=None)
    lb._trading_client = _FakeClient()
    lb._stop_event.set()  # already stopped

    result = lb._reconcile_until_safe()
    assert result is None, f"expected None when stopped, got {result}"


# ── main readiness decision ──────────────────────────────────────────────────
def test_readiness_action_decisions():
    from scheduler.main import _readiness_action

    assert _readiness_action(ready=True, thread_alive=True, stopped=False) == "proceed"
    assert _readiness_action(ready=False, thread_alive=True, stopped=True) == "shutdown"
    assert _readiness_action(ready=False, thread_alive=False, stopped=False) == "exit"
    assert _readiness_action(ready=False, thread_alive=True, stopped=False) == "wait"


if __name__ == "__main__":
    tests = [
        test_reconciler_suppresses_alert_when_send_alert_false,
        test_reconciler_sends_alert_by_default,
        test_reconcile_until_safe_retries_then_proceeds_alerting_once,
        test_reconcile_until_safe_returns_none_when_stopped,
        test_readiness_action_decisions,
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
