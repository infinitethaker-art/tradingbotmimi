"""
Phase 1 manual approval CLI.
Loop A publishes a pending intent to db/pending_intent.json.
Run this script with --approve or --reject during the approval window.
If the window expires before you act, the intent is discarded automatically.

Usage:
    python tools/execution/approve.py --approve
    python tools/execution/approve.py --reject
    python tools/execution/approve.py --status   # check what's pending
"""
import argparse
import datetime
import json
import os
import sys

_INTENT_PATH = os.path.join(os.path.dirname(__file__), "../../db/pending_intent.json")
_UTC = datetime.timezone.utc


def _read_intent() -> dict | None:
    if not os.path.exists(_INTENT_PATH):
        return None
    try:
        with open(_INTENT_PATH) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        print(f"ERROR: pending_intent.json is malformed: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"ERROR: could not read pending_intent.json: {exc}", file=sys.stderr)
        sys.exit(1)


def _is_expired(intent: dict) -> bool:
    expires_str = intent.get("expires_at")
    if not expires_str:
        return False
    try:
        exp = datetime.datetime.fromisoformat(expires_str)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=_UTC)
        return datetime.datetime.now(_UTC) >= exp
    except (ValueError, TypeError):
        return False


def _atomic_write(path: str, data: dict) -> None:
    """Write JSON atomically via a temp file + os.replace."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def _write_decision(decision: str) -> None:
    intent = _read_intent()
    if intent is None:
        print("No pending intent found. It may have already expired or been consumed.")
        sys.exit(1)
    if _is_expired(intent):
        print(
            f"Intent has already expired (expires_at={intent.get('expires_at')}). "
            "No decision written."
        )
        sys.exit(1)
    intent["decision"] = decision
    _atomic_write(_INTENT_PATH, intent)
    print(f"Decision written: {decision}")


def _status() -> None:
    intent = _read_intent()
    if intent is None:
        print("No pending intent.")
        return
    expired = _is_expired(intent)
    print("Pending intent:")
    print(f"  Intent ID:   {intent.get('intent_id', 'N/A')}")
    print(f"  Symbol:      {intent.get('symbol')}")
    print(f"  Signal:      {intent.get('signal_type')}")
    print(f"  Price:       ~${intent.get('bar_close')}")
    print(f"  MACD hist:   {intent.get('macd_hist')}")
    print(f"  RSI(14):     {intent.get('rsi_14')}")
    print(f"  Expires at:  {intent.get('expires_at')}{'  [EXPIRED]' if expired else ''}")
    print(f"  Decision:    {intent.get('decision', 'PENDING')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual approval for Phase 1 signal intents.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true", help="Approve the pending intent.")
    group.add_argument("--reject", action="store_true", help="Reject the pending intent.")
    group.add_argument("--status", action="store_true", help="Show the current pending intent.")
    args = parser.parse_args()

    if args.status:
        _status()
    elif args.approve:
        _write_decision("APPROVED")
    elif args.reject:
        _write_decision("REJECTED")


if __name__ == "__main__":
    main()
