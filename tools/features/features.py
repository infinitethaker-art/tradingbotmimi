"""
Computes technical indicators from a bar series.
All output dicts include data_feed so the source is always traceable.

Indicators computed:
  - MACD(12, 26, 9): macd_line, macd_signal_line, macd_hist
  - RSI(14): rsi_14
  - EMA(20): ema_20
  - Relative volume: bar_volume / 20-bar rolling mean volume

Early rows will have NaN indicators while EWM windows fill (first ~26 bars for MACD,
first ~14 bars for RSI). Callers should use latest() or check for None values.
"""
from typing import Any

import numpy as np
import pandas as pd


def _ema_series(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi_series(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()

    # Explicit edge cases: avoid division by zero and NaN propagation
    rsi = pd.Series(index=series.index, dtype=float)
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    gain_only = (avg_gain > 0) & (avg_loss == 0)
    loss_only = (avg_gain == 0) & (avg_loss > 0)
    normal = ~both_zero & ~gain_only & ~loss_only

    rsi[both_zero] = 50.0
    rsi[gain_only] = 100.0
    rsi[loss_only] = 0.0
    rs = avg_gain[normal] / avg_loss[normal]
    rsi[normal] = 100 - (100 / (1 + rs))

    return rsi


def compute(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Given a list of bar dicts (ascending, each with 'close', 'volume', 'data_feed'),
    return a list of feature dicts — one per bar — with all indicators attached.

    Requires at least 27 bars (26 for MACD slow EMA + 1 for MACD signal).
    Returns an empty list if bars are insufficient.

    Raises ValueError if:
      - any bar is missing 'close', 'volume', or 'data_feed'
      - bars have mixed data_feed values (feed contamination)
    """
    if len(bars) < 27:
        return []

    # Validate required fields
    for i, bar in enumerate(bars):
        for field in ("close", "volume", "data_feed"):
            if field not in bar:
                raise ValueError(f"Bar at index {i} is missing required field '{field}'")

    # Validate data_feed consistency
    feeds = {bar["data_feed"] for bar in bars}
    if len(feeds) > 1:
        raise ValueError(
            f"Bars have mixed data_feed values: {feeds}. "
            "All bars in a series must come from the same feed."
        )
    data_feed = bars[0]["data_feed"]

    df = pd.DataFrame(bars)
    # Sort defensively by timestamp ascending
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)

    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)

    # MACD
    ema_fast = _ema_series(df["close"], 12)
    ema_slow = _ema_series(df["close"], 26)
    macd_line = ema_fast - ema_slow
    macd_signal = _ema_series(macd_line, 9)
    macd_hist = macd_line - macd_signal

    # RSI
    rsi = _rsi_series(df["close"], 14)

    # EMA 20
    ema_20 = _ema_series(df["close"], 20)

    # Relative volume (bar vol / 20-bar rolling mean)
    vol_mean_20 = df["volume"].rolling(20).mean()
    rel_vol = (df["volume"] / vol_mean_20).round(4)

    result = []
    for i, row in df.iterrows():
        result.append(
            {
                "symbol": row.get("symbol"),
                "timestamp": row.get("timestamp"),
                "bar_close": float(row["close"]),
                "bar_volume": int(row["volume"]),
                "data_feed": data_feed,
                "macd_line": round(float(macd_line.iloc[i]), 6),
                "macd_signal_line": round(float(macd_signal.iloc[i]), 6),
                "macd_hist": round(float(macd_hist.iloc[i]), 6),
                "rsi_14": round(float(rsi.iloc[i]), 4) if not pd.isna(rsi.iloc[i]) else None,
                "ema_20": round(float(ema_20.iloc[i]), 4),
                "relative_volume": float(rel_vol.iloc[i]) if not pd.isna(rel_vol.iloc[i]) else None,
            }
        )

    return result


def latest(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Convenience: return only the most recent feature row."""
    rows = compute(bars)
    return rows[-1] if rows else None


if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    import config
    from tools.data.data_feed import fetch_bars

    config.load()
    symbol = config.WATCHLIST[0]
    print(f"Fetching bars for {symbol} and computing features …")
    bars = fetch_bars(symbol, n_bars=50)
    if not bars:
        print("No bars returned. Is the market open?")
        sys.exit(0)

    feat = latest(bars)
    if feat:
        print(f"  Timestamp:    {feat['timestamp']}")
        print(f"  Close:        {feat['bar_close']}")
        print(f"  MACD line:    {feat['macd_line']}")
        print(f"  MACD signal:  {feat['macd_signal_line']}")
        print(f"  MACD hist:    {feat['macd_hist']}")
        print(f"  RSI(14):      {feat['rsi_14']}")
        print(f"  EMA(20):      {feat['ema_20']}")
        print(f"  Rel. volume:  {feat['relative_volume']}")
        print(f"  Data feed:    {feat['data_feed']}")
    else:
        print("Not enough bars to compute features (need at least 27).")
