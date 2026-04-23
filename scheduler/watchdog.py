"""
Watchdog — separate process that monitors the heartbeat file.
Run this independently from main.py (not inside it — otherwise a full crash kills both).

Usage:
    python scheduler/watchdog.py

Checks heartbeat.txt every WATCHDOG_CHECK_INTERVAL_SEC seconds.
Heartbeat is written by ws_client only while the WebSocket is connected and authenticated.
A stale heartbeat during market hours means either the process is dead or the WS disconnected.

Alerts are throttled to one per 10 minutes to avoid spam.
"""
import datetime
import logging
import os
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from tools.alerts import telegram_bot as tg
from tools.data.market_calendar import is_trading_day, market_open_time, market_close_time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("watchdog")

ET = ZoneInfo("America/New_York")
_UTC = datetime.timezone.utc
_HEARTBEAT_PATH = os.path.join(os.path.dirname(__file__), "../db/heartbeat.txt")
_CHECK_INTERVAL_SEC = 600  # check every 10 minutes

_last_alert_time: float = 0.0
_ALERT_COOLDOWN_SEC = 600  # send at most one silence alert per 10 minutes


def _read_heartbeat() -> datetime.datetime | None:
    if not os.path.exists(_HEARTBEAT_PATH):
        return None
    try:
        with open(_HEARTBEAT_PATH) as f:
            ts = f.read().strip()
        dt = datetime.datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
        return dt
    except (ValueError, OSError):
        return None


def _is_market_hours() -> bool:
    """Return True if we're currently within market hours (open to close)."""
    now = datetime.datetime.now(ET)
    today = now.date()
    if not is_trading_day(today):
        return False
    try:
        open_t = market_open_time(today)
        close_t = market_close_time(today)
        return open_t <= now < close_t
    except Exception:
        return False


def _check_once() -> None:
    global _last_alert_time

    if not _is_market_hours():
        logger.debug("Outside market hours — skipping check.")
        return

    heartbeat = _read_heartbeat()
    now_utc = datetime.datetime.now(_UTC)
    threshold_minutes = config.WATCHDOG_STALE_THRESHOLD_MIN

    def _maybe_alert(msg: str) -> None:
        global _last_alert_time
        logger.warning(msg)
        if time.time() - _last_alert_time >= _ALERT_COOLDOWN_SEC:
            tg.alert_bot_silent()
            _last_alert_time = time.time()
        else:
            logger.debug("Silence alert suppressed (cooldown active).")

    if heartbeat is None:
        _maybe_alert("Heartbeat file missing during market hours — bot may not be running.")
        return

    age_minutes = (now_utc - heartbeat).total_seconds() / 60
    if age_minutes > threshold_minutes:
        _maybe_alert(
            f"Heartbeat stale by {age_minutes:.1f} min (threshold={threshold_minutes} min). "
            "Process dead or WebSocket disconnected."
        )
    else:
        logger.info("Heartbeat OK — age=%.1f min.", age_minutes)


def main() -> None:
    config.load()
    logger.info(
        "Watchdog started. Check interval=%ds, stale threshold=%dmin.",
        _CHECK_INTERVAL_SEC,
        config.WATCHDOG_STALE_THRESHOLD_MIN,
    )
    while True:
        try:
            _check_once()
        except Exception as exc:
            logger.error("Watchdog check failed: %s", exc)
        time.sleep(_CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
