"""
Fetches historical bars from Alpaca Data API v2.
Always attaches the data_feed label (iex / sip) to every record.
This label is mandatory in every downstream schema — never strip it.

Lookback buffer: bar_minutes * n_bars * 3 minutes back from the last completed
bar boundary. This covers weekends and single holidays. Extended holiday clusters
(e.g. week between Christmas and New Year) may still return fewer bars than
requested; callers must handle a short list.
"""
import datetime
import logging
import time
from typing import Any

import config
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

logger = logging.getLogger(__name__)

_UTC = datetime.timezone.utc


def _client() -> StockHistoricalDataClient:
    return StockHistoricalDataClient(
        api_key=config.ALPACA_API_KEY,
        secret_key=config.ALPACA_SECRET_KEY,
    )


def _feed_param() -> str:
    return config.ALPACA_DATA_FEED  # "iex" | "sip"


def _last_completed_bar_end(bar_minutes: int) -> datetime.datetime:
    """Return the UTC datetime of the last completed bar boundary."""
    epoch_min = int(time.time() // 60)
    completed_bar_min = (epoch_min // bar_minutes) * bar_minutes
    return datetime.datetime(1970, 1, 1, tzinfo=_UTC) + datetime.timedelta(minutes=completed_bar_min)


def fetch_bars(
    symbol: str,
    n_bars: int = 60,
    bar_minutes: int = 15,
    end: datetime.datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch the most recent *n_bars* completed bars for *symbol*.

    Returns a list of dicts — one per bar — each containing:
        timestamp, open, high, low, close, volume, vwap,
        trade_count, data_feed, symbol

    data_feed is always present and labelled from ALPACA_DATA_FEED.
    Bars are sorted ascending by timestamp.

    Lookback: bar_minutes * n_bars * 3 minutes from the last completed bar
    boundary. Best-effort — warns if fewer than n_bars are returned.
    """
    config.load()
    client = _client()

    if end is None:
        end = _last_completed_bar_end(bar_minutes)

    # bar_minutes * n_bars * 3 minutes of lookback
    lookback_minutes = bar_minutes * n_bars * 3
    start = end - datetime.timedelta(minutes=lookback_minutes)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(bar_minutes, TimeFrameUnit.Minute),
        start=start,
        end=end,
        feed=_feed_param(),
        adjustment="raw",
    )

    try:
        bar_set = client.get_stock_bars(request)
    except Exception as exc:
        raise RuntimeError(f"fetch_bars failed for {symbol}: {exc}") from exc

    raw_bars = bar_set.data.get(symbol, [])

    if not raw_bars:
        logger.warning("fetch_bars: no bars returned for %s (feed=%s)", symbol, config.ALPACA_DATA_FEED)
        return []

    result = []
    for bar in raw_bars[-n_bars:]:
        result.append(
            {
                "symbol": symbol,
                "timestamp": bar.timestamp.isoformat(),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume),
                "vwap": float(bar.vwap) if bar.vwap is not None else None,
                "trade_count": int(bar.trade_count) if bar.trade_count is not None else None,
                "data_feed": config.ALPACA_DATA_FEED,
            }
        )

    if len(result) < n_bars:
        logger.warning(
            "fetch_bars: requested %d bars for %s but only %d returned. "
            "Possible holiday gap or sparse session.",
            n_bars, symbol, len(result),
        )

    return result


def fetch_latest_quote(symbol: str) -> dict[str, Any]:
    """
    Fetch the latest bid/ask quote for *symbol* from the configured feed.
    Returns bid_price, ask_price, spread_pct, data_feed.
    Used for spread logging — feed label matches ALPACA_DATA_FEED, not necessarily NBBO.
    """
    config.load()
    client = _client()

    request = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=_feed_param())

    try:
        quotes = client.get_stock_latest_quote(request)
    except Exception as exc:
        raise RuntimeError(f"fetch_latest_quote failed for {symbol}: {exc}") from exc

    q = quotes.get(symbol)

    if q is None:
        return {"bid_price": None, "ask_price": None, "spread_pct": None, "data_feed": config.ALPACA_DATA_FEED}

    bid = float(q.bid_price) if q.bid_price is not None else None
    ask = float(q.ask_price) if q.ask_price is not None else None
    spread_pct = None
    if bid is not None and ask is not None and bid > 0:
        mid = (bid + ask) / 2
        spread_pct = round((ask - bid) / mid * 100, 4)

    return {
        "bid_price": bid,
        "ask_price": ask,
        "spread_pct": spread_pct,
        "data_feed": config.ALPACA_DATA_FEED,
    }


if __name__ == "__main__":
    config.load()
    symbol = config.WATCHLIST[0]
    print(f"Fetching last 5 bars for {symbol} (feed={config.ALPACA_DATA_FEED}) …")
    bars = fetch_bars(symbol, n_bars=5)
    if bars:
        for b in bars:
            print(f"  {b['timestamp']}  close={b['close']}  vol={b['volume']}  feed={b['data_feed']}")
    else:
        print("  No bars returned — check market hours and API credentials.")

    print("\nFetching latest quote …")
    q = fetch_latest_quote(symbol)
    print(f"  bid={q['bid_price']}  ask={q['ask_price']}  spread={q['spread_pct']}%  feed={q['data_feed']}")
