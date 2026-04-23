"""
Determines whether today is a trading day and what time the market closes.
Uses pandas_market_calendars for the official NYSE schedule.
Close time is always dynamic — never hardcoded — to handle early-close days correctly.
"""
import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal

ET = ZoneInfo("America/New_York")
_NYSE = mcal.get_calendar("NYSE")

# Simple in-process cache: date string -> schedule DataFrame
_schedule_cache: dict[str, pd.DataFrame] = {}


def _schedule_for(date: datetime.date) -> pd.DataFrame:
    """Return the NYSE schedule DataFrame for *date*. Cached per date string."""
    ds = date.strftime("%Y-%m-%d")
    if ds not in _schedule_cache:
        _schedule_cache[ds] = _NYSE.schedule(start_date=ds, end_date=ds)
    return _schedule_cache[ds]


def is_trading_day(date: datetime.date | None = None) -> bool:
    """Return True if the NYSE is open on *date* (default: today ET)."""
    if date is None:
        date = datetime.datetime.now(ET).date()
    sched = _schedule_for(date)
    return not sched.empty


def market_open_time(date: datetime.date | None = None) -> datetime.datetime:
    """Return the market open datetime (ET-aware) for *date*."""
    if date is None:
        date = datetime.datetime.now(ET).date()
    sched = _schedule_for(date)
    if sched.empty:
        raise ValueError(f"{date} is not a trading day.")
    open_utc = sched.iloc[0]["market_open"]
    return open_utc.to_pydatetime().astimezone(ET)


def market_close_time(date: datetime.date | None = None) -> datetime.datetime:
    """Return the market close datetime (ET-aware) for *date*.
    Handles early-close days (e.g. day before Thanksgiving = 1:00 PM ET).
    """
    if date is None:
        date = datetime.datetime.now(ET).date()
    sched = _schedule_for(date)
    if sched.empty:
        raise ValueError(f"{date} is not a trading day.")
    close_utc = sched.iloc[0]["market_close"]
    return close_utc.to_pydatetime().astimezone(ET)


def trading_window(
    date: datetime.date | None = None,
    start_offset_min: int = 15,
    end_offset_min: int = 30,
) -> tuple[datetime.datetime, datetime.datetime]:
    """
    Return (earliest_entry_time, latest_entry_time) respecting offsets.
    earliest_entry = open + start_offset_min
    latest_entry   = close - end_offset_min
    """
    open_t = market_open_time(date)
    close_t = market_close_time(date)
    earliest = open_t + datetime.timedelta(minutes=start_offset_min)
    latest = close_t - datetime.timedelta(minutes=end_offset_min)
    return earliest, latest


def is_within_trading_window(
    now: datetime.datetime | None = None,
    start_offset_min: int = 15,
    end_offset_min: int = 30,
) -> bool:
    """Return True if *now* falls inside the tradeable window for today."""
    if now is None:
        now = datetime.datetime.now(ET)
    today = now.date()
    if not is_trading_day(today):
        return False
    earliest, latest = trading_window(today, start_offset_min, end_offset_min)
    return earliest <= now < latest


def session_type(now: datetime.datetime | None = None) -> str:
    """
    Classify the current moment as one of:
    'pre_market', 'regular', 'post_market', 'closed', 'half_day'

    'half_day' is a heuristic: sessions shorter than 6.4h are flagged as half_day.
    The exact cutoff may need tuning for unusual early-close schedules.
    """
    if now is None:
        now = datetime.datetime.now(ET)
    today = now.date()
    if not is_trading_day(today):
        return "closed"

    open_t = market_open_time(today)
    close_t = market_close_time(today)
    session_length_h = (close_t - open_t).total_seconds() / 3600

    if now < open_t:
        return "pre_market"
    if now >= close_t:
        return "post_market"

    if session_length_h < 6.4:
        return "half_day"
    return "regular"


if __name__ == "__main__":
    today = datetime.datetime.now(ET).date()
    print(f"Date:          {today}")
    print(f"Trading day:   {is_trading_day(today)}")
    if is_trading_day(today):
        print(f"Market open:   {market_open_time(today).strftime('%H:%M %Z')}")
        print(f"Market close:  {market_close_time(today).strftime('%H:%M %Z')}")
        e, l = trading_window(today)
        print(f"Trade window:  {e.strftime('%H:%M')} – {l.strftime('%H:%M')} ET")
        print(f"Session type:  {session_type()}")
        print(f"In window now: {is_within_trading_window()}")

    print("\nNext 5 calendar dates:")
    check = today
    for _ in range(5):
        check += datetime.timedelta(days=1)
        flag = "TRADING" if is_trading_day(check) else "closed"
        print(f"  {check}  {flag}")
