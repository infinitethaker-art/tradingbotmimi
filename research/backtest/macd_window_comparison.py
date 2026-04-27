"""
Backtest: MACD crossover window size comparison
  N=1  strict crossover  (current production)
  N=2  2-bar lookback
  N=3  3-bar lookback

Strategy: MACD(12,26,9) + RSI(14) + relative-volume filter on 15-min SPY bars.
Validation: walk-forward — indicators computed on full history, trade PnL sliced
            into monthly OOS folds starting after the first 3 months of warmup.

ISOLATION RULE: this file does NOT import from tools/ or scheduler/.
Run from project root:
    python research/backtest/macd_window_comparison.py
"""

import datetime
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# ── Parameters (mirrors production values; do NOT import config.py) ───────────
SYMBOL           = "SPY"
BAR_MINUTES      = 15
RSI_LOW          = 35.0
RSI_HIGH         = 65.0
MIN_REL_VOL      = 1.2
STOP_LOSS_PCT    = 0.02
TAKE_PROFIT_PCT  = 0.04
WINDOW_SIZES     = [1, 2, 3]   # bars to look back for a valid crossover event

# Walk-forward
HISTORY_MONTHS   = 13   # total bars fetched; gives ~10 OOS monthly folds
IN_SAMPLE_MONTHS = 3    # first N months treated as warmup (excluded from OOS scoring)


# ── Data ──────────────────────────────────────────────────────────────────────
def fetch_history() -> pd.DataFrame:
    """Fetch HISTORY_MONTHS of 15-min bars. Returns DataFrame in America/New_York."""
    client = StockHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )
    feed = os.environ.get("ALPACA_DATA_FEED", "iex")
    end   = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=HISTORY_MONTHS * 32)

    resp = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=SYMBOL,
        timeframe=TimeFrame(BAR_MINUTES, TimeFrameUnit.Minute),
        start=start,
        end=end,
        feed=feed,
        adjustment="raw",
    ))
    bars = resp.data.get(SYMBOL, [])
    if not bars:
        raise RuntimeError(f"No bars returned for {SYMBOL} (feed={feed}). Check credentials and market hours.")

    df = pd.DataFrame(
        [{"open": float(b.open), "high": float(b.high),
          "low":  float(b.low),  "close": float(b.close),
          "volume": int(b.volume)} for b in bars],
        index=pd.DatetimeIndex([b.timestamp for b in bars], name="timestamp"),
    ).sort_index()

    # Convert to NY for market-hours filtering later
    df.index = df.index.tz_convert("America/New_York")
    print(f"  {len(df):,} bars  |  {df.index[0].date()} -> {df.index[-1].date()}  |  feed={feed}")
    return df


# ── Indicators (self-contained; no tools/ imports) ────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # MACD(12, 26, 9)
    macd_line   = df["close"].ewm(span=12, adjust=False).mean() \
                - df["close"].ewm(span=26, adjust=False).mean()
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    df["macd_hist"] = macd_line - signal_line

    # RSI(14) — Wilder EMA (alpha = 1/period)
    delta = df["close"].diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    avg_loss = delta.clip(upper=0).abs().ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(50.0)

    # Relative volume (20-bar rolling mean, same as production)
    df["rel_vol"] = df["volume"] / df["volume"].rolling(20).mean()

    return df


# ── Signal detection ──────────────────────────────────────────────────────────
def _crossed_up(hist: pd.Series, idx: int, window: int) -> bool:
    """True if histogram crossed from <=0 to >0 within the last <window> bars."""
    for lookback in range(window):
        i = idx - lookback
        if i < 1:
            continue
        if hist.iloc[i - 1] <= 0.0 < hist.iloc[i]:
            return True
    return False


def _crossed_down(hist: pd.Series, idx: int) -> bool:
    return hist.iloc[idx - 1] > 0.0 >= hist.iloc[idx]


# ── Simulation engine ─────────────────────────────────────────────────────────
def simulate(df: pd.DataFrame, window: int) -> list[dict]:
    """
    Run one strategy pass over df with the given crossover window.

    Entry: MACD crossed up within <window> bars AND RSI in band AND vol OK.
    Exit:  MACD crosses down  OR  stop-loss (-2%)  OR  take-profit (+4%)  OR  EOD.
    No overnight positions. Market hours gate: 09:45–15:45 ET.
    """
    trades   = []
    in_pos   = False
    entry_px = entry_i = None
    hist     = df["macd_hist"]

    for i in range(1, len(df)):
        ts  = df.index[i]
        row = df.iloc[i]

        # Market hours gate (09:45–15:45 ET matches production offsets)
        hm = (ts.hour, ts.minute)
        after_open  = hm >= (9, 45)
        before_close = hm <= (15, 45)

        if not after_open or not before_close:
            if in_pos:
                trades.append(_make_trade(entry_px, row["close"], entry_i, i, df, "EOD"))
                in_pos = False
            continue

        # ── Exit check ────────────────────────────────────────────────────────
        if in_pos:
            pnl = (row["close"] - entry_px) / entry_px
            if _crossed_down(hist, i):
                reason = "MACD_DOWN"
            elif pnl <= -STOP_LOSS_PCT:
                reason = "STOP_LOSS"
            elif pnl >= TAKE_PROFIT_PCT:
                reason = "TAKE_PROFIT"
            else:
                reason = None
            if reason:
                trades.append(_make_trade(entry_px, row["close"], entry_i, i, df, reason))
                in_pos = False

        # ── Entry check ───────────────────────────────────────────────────────
        if not in_pos:
            macd_ok = _crossed_up(hist, i, window)
            rsi_ok  = RSI_LOW <= row["rsi"] <= RSI_HIGH
            vol_ok  = pd.notna(row["rel_vol"]) and row["rel_vol"] >= MIN_REL_VOL
            if macd_ok and rsi_ok and vol_ok:
                in_pos   = True
                entry_px = row["close"]
                entry_i  = i

    return trades


def _make_trade(entry_px, exit_px, entry_i, exit_i, df, reason) -> dict:
    return {
        "entry_time":  str(df.index[entry_i]),
        "exit_time":   str(df.index[exit_i]),
        "entry_price": round(entry_px, 4),
        "exit_price":  round(exit_px, 4),
        "pnl_pct":     (exit_px - entry_px) / entry_px,
        "exit_reason": reason,
        "bars_held":   exit_i - entry_i,
    }


# ── Metrics ───────────────────────────────────────────────────────────────────
def metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"n_trades": 0, "win_rate": None, "avg_ret_pct": None,
                "sharpe": None, "max_dd_pct": None, "total_ret_pct": None}
    r      = pd.Series([t["pnl_pct"] for t in trades])
    equity = (1 + r).cumprod()
    dd     = (equity / equity.cummax() - 1).min()
    sharpe = (r.mean() / r.std() * (252 ** 0.5)) if r.std() > 0 and len(r) > 1 else None
    return {
        "n_trades":      len(r),
        "win_rate":      round((r > 0).mean() * 100, 1),
        "avg_ret_pct":   round(r.mean() * 100, 3),
        "sharpe":        round(sharpe, 2) if sharpe is not None else None,
        "max_dd_pct":    round(dd * 100, 2),
        "total_ret_pct": round((equity.iloc[-1] - 1) * 100, 2),
    }


# ── Walk-forward ──────────────────────────────────────────────────────────────
def walk_forward(all_trades: list[dict], df: pd.DataFrame) -> dict[int, list[dict]]:
    """
    Slice the full trade list into monthly OOS folds.
    Folds start after IN_SAMPLE_MONTHS of warmup so indicators are stable.
    Returns {window: [fold_metrics, ...]}.
    """
    periods = df.index.to_period("M").unique().sort_values()
    oos_periods = periods[IN_SAMPLE_MONTHS:]
    return oos_periods


def slice_folds(trades: list[dict], oos_periods) -> list[dict]:
    results = []
    for p in oos_periods:
        fold_trades = [
            t for t in trades
            if pd.Timestamp(t["entry_time"]).to_period("M") == p
        ]
        m = metrics(fold_trades)
        m["fold"]   = str(p)
        m["trades"] = fold_trades
        results.append(m)
    return results


# ── Reporting helpers ─────────────────────────────────────────────────────────
_W = 70

def _row(label, n, win, avg, sharpe, dd, total=""):
    win_s   = f"{win}%"    if win    is not None else "N/A"
    avg_s   = f"{avg}%"   if avg    is not None else "N/A"
    sh_s    = str(sharpe)  if sharpe is not None else "N/A"
    dd_s    = f"{dd}%"     if dd     is not None else "N/A"
    tot_s   = f"{total}%"  if total  is not None else ""
    return f"  {label:<14}{n:>6}  {win_s:>7}  {avg_s:>9}  {sh_s:>7}  {dd_s:>8}  {tot_s}"


def _header():
    return f"  {'Window':<14}{'Trades':>6}  {'Win%':>7}  {'AvgRet%':>9}  {'Sharpe':>7}  {'MaxDD%':>8}  TotalRet%"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\nFetching {HISTORY_MONTHS}m of {BAR_MINUTES}-min {SYMBOL} bars ...")
    df = add_indicators(fetch_history())

    # Run simulation once per window size (full period)
    all_trades_by_window: dict[int, list[dict]] = {}
    for w in WINDOW_SIZES:
        all_trades_by_window[w] = simulate(df, w)

    # ── Full-period summary ───────────────────────────────────────────────────
    print(f"\n{'FULL-PERIOD COMPARISON':^{_W}}")
    print("=" * _W)
    print(_header())
    print("-" * _W)
    full_metrics = {}
    for w in WINDOW_SIZES:
        m = metrics(all_trades_by_window[w])
        full_metrics[w] = m
        label = f"N={w}" + (" (current)" if w == 1 else "")
        print(_row(label, m["n_trades"], m["win_rate"], m["avg_ret_pct"],
                   m["sharpe"], m["max_dd_pct"], m["total_ret_pct"]))

    # ── Walk-forward OOS folds ────────────────────────────────────────────────
    oos_periods = walk_forward(None, df)
    print(f"\n{'WALK-FORWARD  (OOS folds, warmup = first 3 months)':^{_W}}")
    print("=" * _W)

    wf_by_window: dict[int, list[dict]] = {}
    for w in WINDOW_SIZES:
        folds = slice_folds(all_trades_by_window[w], oos_periods)
        wf_by_window[w] = folds
        label = f"N={w}" + (" (current)" if w == 1 else "")
        print(f"\n  {label}")
        print(f"  {'Fold':<12}{'Trades':>6}  {'Win%':>7}  {'AvgRet%':>9}  {'Sharpe':>7}  {'MaxDD%':>8}")
        for f in folds:
            print(f"  {f['fold']:<12}{f['n_trades']:>6}  "
                  f"{str(f['win_rate'])+'%' if f['win_rate'] is not None else 'N/A':>7}  "
                  f"{str(f['avg_ret_pct'])+'%' if f['avg_ret_pct'] is not None else 'N/A':>9}  "
                  f"{str(f['sharpe']) if f['sharpe'] is not None else 'N/A':>7}  "
                  f"{str(f['max_dd_pct'])+'%' if f['max_dd_pct'] is not None else 'N/A':>8}")

        oos_trades = [t for f in folds for t in f["trades"]]
        agg = metrics(oos_trades)
        print(f"  --- OOS aggregate: {agg['n_trades']} trades | "
              f"win={agg['win_rate']}% | avg={agg['avg_ret_pct']}% | sharpe={agg['sharpe']}")

    # ── Overfit check (workflow rule: OOS Sharpe >= 0.8 x IS Sharpe) ─────────
    print(f"\n{'OVERFIT CHECK  (threshold: OOS/IS Sharpe >= 0.8)':^{_W}}")
    print("=" * _W)
    for w in WINDOW_SIZES:
        is_sh = full_metrics[w]["sharpe"]
        oos_trades = [t for f in wf_by_window[w] for t in f["trades"]]
        oos_sh = metrics(oos_trades)["sharpe"]
        label = f"N={w}" + (" (current)" if w == 1 else "")
        if is_sh and oos_sh:
            ratio = oos_sh / is_sh
            flag  = "OK" if ratio >= 0.8 else "OVERFIT WARNING"
            print(f"  {label:<18} IS={is_sh:>6.2f}  OOS={oos_sh:>6.2f}  ratio={ratio:.2f}  [{flag}]")
        else:
            print(f"  {label:<18} insufficient trades for Sharpe calculation")

    # ── Recommendation hint ───────────────────────────────────────────────────
    print(f"\n{'RECOMMENDATION GUIDE':^{_W}}")
    print("=" * _W)
    print("  Compare OOS aggregate metrics across N=1, 2, 3.")
    print("  Prefer the smallest N whose OOS win% and Sharpe are materially better")
    print("  than N=1 and whose overfit check passes.")
    print("  If N=1 OOS metrics are competitive, keep strict crossover (no change).")
    print("  Present findings to user before editing config.py.")

    # ── Save results ──────────────────────────────────────────────────────────
    out = ROOT / "research" / "backtest" / "macd_window_results.json"
    save = {
        "generated_at": datetime.datetime.now().isoformat(),
        "symbol":       SYMBOL,
        "bar_minutes":  BAR_MINUTES,
        "data_feed":    os.environ.get("ALPACA_DATA_FEED", "iex"),
        "params": {
            "RSI_LOW": RSI_LOW, "RSI_HIGH": RSI_HIGH,
            "MIN_REL_VOL": MIN_REL_VOL,
            "STOP_LOSS_PCT": STOP_LOSS_PCT,
            "TAKE_PROFIT_PCT": TAKE_PROFIT_PCT,
        },
        "full_period": {
            str(w): {k: v for k, v in m.items()}
            for w, m in full_metrics.items()
        },
        "walk_forward_agg": {
            str(w): metrics([t for f in folds for t in f["trades"]])
            for w, folds in wf_by_window.items()
        },
        "walk_forward_folds": {
            str(w): [{k: v for k, v in f.items() if k != "trades"} for f in folds]
            for w, folds in wf_by_window.items()
        },
    }
    out.write_text(json.dumps(save, indent=2))
    print(f"\n  Results saved -> research/backtest/macd_window_results.json")
    print()


if __name__ == "__main__":
    main()
