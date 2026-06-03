"""
Per-Symbol Cost Viability — the REAL universe (SPY, QQQ, AAPL, NVDA, TSLA)
==========================================================================
Research only. No production change. ISOLATION RULE: no imports from tools/ or scheduler/.

The SPY-only cost test (cost_sensitivity_study.py) showed the edge survives on the
friendliest symbol. But the live bot trades 5 symbols with very different spreads.
This runs the deployed strategy (vol=1.1, RSI_HIGH=70) on EACH symbol and charges
EACH its OWN realistic round-trip cost (estimated from a live quote), to find which
symbols are net-viable and which are bleeding the edge away.

Cost model: round-trip slippage ≈ full quoted spread (entry pays ~half-spread, exit
pays ~half-spread). Shown two ways per symbol:
  - "penny floor" = a 1-cent spread (optimistic best case for a penny-spread name)
  - "live quote"  = the current quoted spread (realistic; weekend = last close quote)

Run from project root:
    python research/backtest/cost_universe_study.py
"""
import datetime
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

SYMBOLS         = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]
BAR_MINUTES     = 15
MACD_FAST, MACD_SLOW, MACD_SIGNAL_WIN = 12, 26, 9
RSI_PERIOD      = 14
RSI_LOW, RSI_HIGH = 35.0, 70.0     # DEPLOYED
MIN_REL_VOL     = 1.1              # DEPLOYED
STOP_LOSS_PCT, TAKE_PROFIT_PCT = 0.02, 0.04
REL_VOL_WINDOW  = 20
HISTORY_MONTHS, WARMUP_MONTHS = 13, 3


def _client():
    return StockHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"], secret_key=os.environ["ALPACA_SECRET_KEY"])


def fetch_bars(client, symbol, feed):
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=HISTORY_MONTHS * 32)
    resp = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbol, timeframe=TimeFrame(BAR_MINUTES, TimeFrameUnit.Minute),
        start=start, end=end, feed=feed, adjustment="raw"))
    bars = resp.data.get(symbol, [])
    if not bars:
        return None
    df = pd.DataFrame(
        [{"open": float(b.open), "high": float(b.high), "low": float(b.low),
          "close": float(b.close), "volume": int(b.volume)} for b in bars],
        index=pd.DatetimeIndex([b.timestamp for b in bars])).sort_index()
    df.index = df.index.tz_convert("America/New_York")
    return df


def add_indicators(df):
    df = df.copy()
    macd = (df["close"].ewm(span=MACD_FAST, adjust=False).mean()
            - df["close"].ewm(span=MACD_SLOW, adjust=False).mean())
    df["macd_hist"] = macd - macd.ewm(span=MACD_SIGNAL_WIN, adjust=False).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    loss = delta.clip(upper=0).abs().ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    df["rsi"] = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(50.0)
    df["rel_vol"] = df["volume"] / df["volume"].rolling(REL_VOL_WINDOW).mean()
    return df


def _in_session(idx):
    hm = np.asarray(idx.hour) * 60 + np.asarray(idx.minute)
    return (hm >= 9 * 60 + 45) & (hm <= 15 * 60 + 45)


def simulate(entry_ok, close, high, low, hist, in_sess):
    n = len(close)
    trades, in_pos, epx, ei = [], False, 0.0, 0
    for i in range(1, n):
        if in_pos and not in_sess[i]:
            trades.append((ei, (close[i - 1] - epx) / epx)); in_pos = False; continue
        if not in_sess[i]:
            continue
        if in_pos:
            sl, tp = epx * (1 - STOP_LOSS_PCT), epx * (1 + TAKE_PROFIT_PCT)
            if low[i] <= sl:    trades.append((ei, -STOP_LOSS_PCT)); in_pos = False
            elif high[i] >= tp: trades.append((ei, TAKE_PROFIT_PCT)); in_pos = False
            elif hist[i - 1] > 0.0 >= hist[i]:
                trades.append((ei, (close[i] - epx) / epx)); in_pos = False
        if in_pos:
            continue
        if entry_ok[i]:
            epx, ei, in_pos = close[i], i, True
    return trades


def metrics(net):
    if len(net) == 0:
        return {"n": 0, "win_pct": None, "avg_R": None, "sharpe": None,
                "total_ret_pct": None, "max_dd_pct": None}
    R = net / STOP_LOSS_PCT
    eq = np.cumprod(1 + net)
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    sd = float(R.std())
    return {"n": int(len(net)), "win_pct": round(float((net > 0).mean() * 100), 1),
            "avg_R": round(float(R.mean()), 4),
            "sharpe": round(float(R.mean()) / sd, 3) if sd > 0 else None,
            "total_ret_pct": round(float((eq[-1] - 1) * 100), 2),
            "max_dd_pct": round(dd * 100, 2)}


def strat_oos(df):
    close = df["close"].to_numpy(); high = df["high"].to_numpy(); low = df["low"].to_numpy()
    hist = df["macd_hist"].to_numpy(); rsi = df["rsi"].to_numpy(); rv = df["rel_vol"].to_numpy()
    in_sess = _in_session(df.index)
    periods = df.index.to_period("M")
    oos_set = set(str(p) for p in periods.unique().sort_values()[WARMUP_MONTHS:])
    oos_bar = np.array([str(p) in oos_set for p in periods])
    prev = np.concatenate([[np.nan], hist[:-1]])
    entry = ((prev <= 0) & (hist > 0) & (rsi >= RSI_LOW) & (rsi <= RSI_HIGH)
             & (~np.isnan(rv)) & (rv >= MIN_REL_VOL) & in_sess)
    trades = simulate(entry, close, high, low, hist, in_sess)
    return np.array([gr for (ei, gr) in trades if oos_bar[ei]]), len(oos_set)


def get_spreads(client, feed):
    out = {}
    try:
        q = client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=SYMBOLS, feed=feed))
    except Exception as e:
        print(f"  quote fetch failed: {e}")
        q = {}
    for s in SYMBOLS:
        bid = float(getattr(q.get(s), "bid_price", 0) or 0) if q else 0.0
        ask = float(getattr(q.get(s), "ask_price", 0) or 0) if q else 0.0
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0.0
        spread_bps = round((ask - bid) / mid * 1e4, 2) if (mid > 0 and ask >= bid) else None
        out[s] = {"bid": bid, "ask": ask, "spread_bps_live": spread_bps}
    return out


def main():
    feed = os.environ.get("ALPACA_DATA_FEED", "iex")
    client = _client()
    print(f"\nPer-Symbol Cost Viability | deployed vol=1.1 RSI<=70 | 15m {feed} | {HISTORY_MONTHS}mo, walk-fwd OOS")
    spreads = get_spreads(client, feed)

    rows, results = [], {}
    for s in SYMBOLS:
        df = fetch_bars(client, s, feed)
        if df is None or len(df) < 300:
            print(f"  {s}: no/insufficient data"); continue
        df = add_indicators(df)
        oos, n_folds = strat_oos(df)
        price = float(df["close"].iloc[-1])
        penny_bps = round(0.01 / price * 1e4, 2)                 # 1-cent round-trip floor
        live_bps = spreads[s]["spread_bps_live"]
        gross = metrics(oos)
        net_penny = metrics(oos - penny_bps / 1e4)
        net_live = metrics(oos - live_bps / 1e4) if live_bps else None
        results[s] = {"price": round(price, 2), "n_folds": n_folds, "oos_trades": int(len(oos)),
                      "penny_bps": penny_bps, "live_spread_bps": live_bps,
                      "gross": gross, "net_penny": net_penny, "net_live": net_live}
        rows.append((s, len(oos), gross, penny_bps, net_penny, live_bps, net_live))

    print(f"\n  {'sym':<5}{'N':>4}{'grossSh':>8}{'grossRet%':>10}{'pennyBp':>8}{'netRet@penny':>13}"
          f"{'liveBp':>8}{'netRet@live':>12}{'netSh@live':>11}  verdict")
    print("  " + "-" * 104)
    for s, n, g, pb, npn, lb, nl in rows:
        lc = "*" if n < 30 else " "
        live_ret = nl["total_ret_pct"] if nl else None
        live_sh = nl["sharpe"] if nl else None
        if nl is None:                           verdict = "no live quote"
        elif (nl["total_ret_pct"] or 0) > 0 and (nl["sharpe"] or 0) > 0:  verdict = "VIABLE"
        elif (npn["total_ret_pct"] or 0) > 0:    verdict = "marginal (penny ok, live neg)"
        else:                                    verdict = "DEAD (neg even at penny)"
        print(f"  {s:<5}{n:>3}{lc}{str(g['sharpe']):>8}{str(g['total_ret_pct']):>10}{pb:>8.2f}"
              f"{str(npn['total_ret_pct']):>13}{str(lb):>8}{str(live_ret):>12}{str(live_sh):>11}  {verdict}")
    print("\n  * = <30 OOS trades (low confidence).  netRet = OOS total return after round-trip cost.")
    print("  penny floor = 1-cent spread (optimistic). live = current quoted spread (weekend=last close; wide).")

    out = {"run_date": datetime.datetime.now().isoformat(), "feed": feed,
           "config": {"min_rel_vol": MIN_REL_VOL, "rsi_high": RSI_HIGH},
           "commission": "0 (Alpaca equities)", "symbols": results}
    (ROOT / "research" / "backtest" / "cost_universe_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print("\n  Results saved -> research/backtest/cost_universe_results.json\n")


if __name__ == "__main__":
    main()
