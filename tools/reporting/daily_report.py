"""
Post-session daily report sent via Telegram.
Reads today's trade log from SQLite and formats a summary.
"""
import datetime
import logging
from zoneinfo import ZoneInfo

import config
from tools.alerts import telegram_bot as tg
from tools.reporting.trade_logger import daily_summary

_ET = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def send_daily_report(date: str | None = None) -> None:
    config.load()
    if date is None:
        date = datetime.datetime.now(_ET).date().isoformat()
    summary = daily_summary(date)
    ok = tg.daily_report(summary)
    if ok:
        logger.info("Daily report sent for %s.", date)
    else:
        logger.warning("Daily report failed to send for %s.", date)
