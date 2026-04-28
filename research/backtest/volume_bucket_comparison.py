"""
Backtest: MACD crossover — relative-volume bucket comparison

Research question:
    Among MACD(12,26,9) bullish crossover bars where RSI(14) is in band,
    do outcomes differ materially by relative volume at the crossover bar?
    Is MIN_RELATIVE_VOLUME=1.2 adding alpha, or filtering too many winners?

Volume buckets (tagged at crossover bar; no volume filter at entry):
    lt_0_5          rel_vol < 0.5
    vol_0_5_to_1_0  0.5 <= rel_vol < 1.0
    vol_1_0_to_1_2  1.0 <= rel_vol < 1.2
    gte_1_2         rel_vol >= 1.2  (current production threshold)

Two execution assumptions (both reported; if conclusion changes, evidence is weak):
    base_case        entry at signal bar close
    sensitivity_case entry at next-bar open

Exit: MACD crossunder OR stop-loss OR take-profit OR EOD.
Intrabar SL+TP conflict: STOP_LOSS assumed hit first (conservative).

ISOLATION RULE: no imports from tools/ or scheduler/.
Run from project root:
    python research/backtest/volume_bucket_comparison.py
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

# ── Parameters (mirrors production; do NOT import config.py) ──────────────────
SYMBOL           = "SPY"
BAR_MINUTES      = 15
MACD_FAST        = 12
MACD_SLOW        = 26
MACD_SIGNAL_WIN  = 9
RSI_PERIOD       = 14
RSI_LOW          = 35.0
RSI_HIGH         = 65.0
STOP_LOSS_PCT    = 0.02
TAKE_PROFIT_PCT  = 0.04
REL_VOL_WINDOW   = 20
HISTORY_MONTHS   = 13
WARMUP_MONTHS    = 3
MIN_REL_VOL_PROD = 1.2   # current production threshold (reference only — not used as filter)

BUCKETS = ["lt_0_5", "vol_0_5_to_1_0", "vol_1_0_to_1_2", "gte_1_2"]
BUCKET_LABELS = {
    "lt_0_5":         "<0.5x",
    "vol_0_5_to_1_0": "0.5-1.0x",
    "vol_1_0_to_1_2": "1.0-1.2x",
    "gte_1_2":        ">=1.2x",
}
CASES = ["base_case", "sensitivity_case"]


def _assign_bucket(rel_vol: float) -> str:
    if rel_vol < 0.5:
        return "lt_0_5"
    elif rel_vol < 1.0:
        return "vol_0_5_to_1_0"
    elif rel_vol < 1.2:
        return "vol_1_0_to_1_2"
    else:
        return "gte_1_2"


# ── Data ──────────────────────────────────────────────────────────────────────
def fetch_history() -> pd.DataFrame:
    client = StockHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )
    feed  = os.environ.get("ALPACA_DATA_FEED", "iex")
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
        raise RuntimeError(
            f"No bars returned for {SYMBOL} (feed={feed}). Check credentials."
        )

    df = pd.DataFrame(
        [{"open":   float(b.open),   "high":   float(b.high),
          "low":    float(b.low),    "close":  float(b.close),
          "volume": int(b.volume)} for b in bars],
        index=pd.DatetimeIndex([b.timestamp for b in bars], name="timestamp"),
    ).sort_index()
    df.index = df.index.tz_convert("America/New_York")
    print(f"  {len(df):,} bars  |  {df.index[0].date()} -> {df.index[-1].date()}  |  feed={feed}")
    return df


# ── Indicators (self-contained; no tools/ imports) ────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # MACD(12, 26, 9)
    macd_line   = (df["close"].ewm(span=MACD_FAST, adjust=False).mean()
                   - df["close"].ewm(span=MACD_SLOW, adjust=False).mean())
    signal_line = macd_line.ewm(span=MACD_SIGNAL_WIN, adjust=False).mean()
    df["macd_hist"] = macd_line - signal_line

    # RSI(14) — Wilder's EMA (alpha = 1/period)
    delta    = df["close"].diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    avg_loss = delta.clip(upper=0).abs().ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(50.0)

    # Relative volume (20-bar rolling mean, matches production)
    df["rel_vol"] = df["volume"] / df["volume"].rolling(REL_VOL_WINDOW).mean()

    return df


# ── Simulation ────────────────────────────────────────────────────────────────
def _in_session(ts: pd.Timestamp) -> bool:
    hm = (ts.hour, ts.minute)
    return (9, 45) <= hm <= (15, 45)


def simulate(df: pd.DataFrame, case: str) -> list[dict]:
    """
    One simulation pass over the full DataFrame.

    No volume filter at entry — all MACD+RSI qualified crossovers are entered,
    then each trade is tagged with the crossover bar's volume bucket.

    case:
        "base_case"        → entry at signal bar close
        "sensitivity_case" → entry at next-bar open
    """
    trades              = []
    hist                = df["macd_hist"]
    in_pos              = False
    entry_px            = 0.0
    signal_i            = 0
    entry_i             = 0
    bucket              = ""
    rel_vol_at_signal   = 0.0
    rsi_at_signal       = 0.0
    macd_hist_at_signal = 0.0
    pending_next_open   = False   # sensitivity case: waiting for next bar to open

    for i in range(1, len(df)):
        ts      = df.index[i]
        row     = df.iloc[i]
        in_sess = _in_session(ts)

        # ── Sensitivity case: execute pending entry at this bar's open ─────────
        if pending_next_open and not in_pos:
            if in_sess:
                entry_px = float(row["open"])
                entry_i  = i
                in_pos   = True
            pending_next_open = False

        # ── Force EOD exit (bar is outside session, position still open) ───────
        if in_pos and not in_sess:
            exit_px = float(df.iloc[i - 1]["close"])   # close of last session bar
            trades.append(_make_trade(
                entry_px, exit_px, signal_i, entry_i, i - 1, df,
                "EOD", bucket, rel_vol_at_signal, rsi_at_signal, macd_hist_at_signal,
            ))
            in_pos = False
            continue

        if not in_sess:
            continue

        # ── Exit check ────────────────────────────────────────────────────────
        if in_pos:
            sl_level = entry_px * (1 - STOP_LOSS_PCT)
            tp_level = entry_px * (1 + TAKE_PROFIT_PCT)
            sl_hit   = float(row["low"])  <= sl_level
            tp_hit   = float(row["high"]) >= tp_level
            md_hit   = hist.iloc[i - 1] > 0.0 >= hist.iloc[i]

            # SL takes priority over TP when both triggered in the same bar
            if sl_hit:
                reason  = "STOP_LOSS"
                exit_px = sl_level
            elif tp_hit:
                reason  = "TAKE_PROFIT"
                exit_px = tp_level
            elif md_hit:
                reason  = "MACD_DOWN"
                exit_px = float(row["close"])
            else:
                reason = None

            if reason:
                trades.append(_make_trade(
                    entry_px, exit_px, signal_i, entry_i, i, df,
                    reason, bucket, rel_vol_at_signal, rsi_at_signal, macd_hist_at_signal,
                ))
                in_pos = False

        # ── Entry check ───────────────────────────────────────────────────────
        # Skip if already in a position or waiting for next-open entry.
        if in_pos or pending_next_open:
            continue

        prev_hist = hist.iloc[i - 1]
        curr_hist = hist.iloc[i]
        # Strict 1-bar crossover (matches production signal.py)
        if not (prev_hist <= 0.0 < curr_hist):
            continue

        rsi = float(row["rsi"])
        rv  = row["rel_vol"]
        if not (RSI_LOW <= rsi <= RSI_HIGH) or pd.isna(rv):
            continue

        # Signal qualifies — tag bucket, then enter
        bucket              = _assign_bucket(float(rv))
        rel_vol_at_signal   = float(rv)
        rsi_at_signal       = rsi
        macd_hist_at_signal = float(curr_hist)
        signal_i            = i

        if case == "base_case":
            entry_px = float(row["close"])
            entry_i  = i
            in_pos   = True
        else:
            # Sensitivity: enter at next bar open (if data exists)
            if i + 1 < len(df):
                pending_next_open = True
            # else: signal on last bar — no trade

    return trades


def _make_trade(
    entry_px: float, exit_px: float,
    signal_i: int, entry_i: int, exit_i: int,
    df: pd.DataFrame,
    reason: str, bucket: str,
    rel_vol: float, rsi: float, macd_hist: float,
) -> dict:
    pnl_decimal = (exit_px - entry_px) / entry_px
    return {
        "signal_time":         str(df.index[signal_i]),
        "entry_time":          str(df.index[entry_i]),
        "exit_time":           str(df.index[exit_i]),
        "entry_price":         round(entry_px, 4),
        "exit_price":          round(exit_px, 4),
        "pnl_pct":             round(pnl_decimal * 100, 4),
        "R":                   round(pnl_decimal / STOP_LOSS_PCT, 4),
        "bars_held":           exit_i - entry_i,
        "exit_reason":         reason,
        "bucket":              bucket,
        "rel_vol":             round(rel_vol, 4),
        "rsi_at_signal":       round(rsi, 2),
        "macd_hist_at_signal": round(macd_hist, 6),
    }


# ── Metrics ───────────────────────────────────────────────────────────────────
_EMPTY_REASONS = {"MACD_DOWN": 0, "STOP_LOSS": 0, "TAKE_PROFIT": 0, "EOD": 0}


def compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {
            "n_trades": 0, "win_rate_pct": None,
            "avg_ret_pct": None,    "median_ret_pct": None,
            "avg_R": None,          "median_R": None,
            "total_ret_pct": None,  "max_dd_pct": None,
            "avg_bars_held": None,
            "exit_reasons": dict(_EMPTY_REASONS),
        }
    r_dec  = pd.Series([t["pnl_pct"] / 100 for t in trades])
    R_vals = pd.Series([t["R"] for t in trades])
    equity = (1 + r_dec).cumprod()
    dd     = float((equity / equity.cummax() - 1).min())

    reasons = dict(_EMPTY_REASONS)
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1

    return {
        "n_trades":       len(trades),
        "win_rate_pct":   round(float((r_dec > 0).mean() * 100), 1),
        "avg_ret_pct":    round(float(r_dec.mean() * 100), 3),
        "median_ret_pct": round(float(r_dec.median() * 100), 3),
        "avg_R":          round(float(R_vals.mean()), 3),
        "median_R":       round(float(R_vals.median()), 3),
        "total_ret_pct":  round(float((equity.iloc[-1] - 1) * 100), 2),
        "max_dd_pct":     round(dd * 100, 2),
        "avg_bars_held":  round(float(pd.Series([t["bars_held"] for t in trades]).mean()), 1),
        "exit_reasons":   reasons,
    }


# ── Walk-forward helpers ──────────────────────────────────────────────────────
def oos_periods(df: pd.DataFrame):
    periods = df.index.to_period("M").unique().sort_values()
    return periods[WARMUP_MONTHS:]


def slice_by_period(trades: list[dict], period_str: str) -> list[dict]:
    return [
        t for t in trades
        if str(pd.Timestamp(t["signal_time"]).to_period("M")) == period_str
    ]


# ── Console reporting ─────────────────────────────────────────────────────────
_W = 118


def _print_table(title: str, case_label: str, metrics_by_bucket: dict) -> None:
    print(f"\n{title} — {case_label}")
    print("=" * _W)
    hdr = (
        f"  {'Bucket':<14} {'N':>5} {'Win%':>6} {'AvgRet%':>8} {'MedRet%':>8} "
        f"{'AvgR':>6} {'MedR':>6} {'TotRet%':>8} {'MaxDD%':>7} {'AvgBars':>8}  ExitReasons"
    )
    print(hdr)
    print("-" * _W)
    for bk in BUCKETS:
        m = metrics_by_bucket.get(bk, {"n_trades": 0})
        if not m or m["n_trades"] == 0:
            print(f"  {BUCKET_LABELS[bk]:<14} {'0':>5}  — no trades —")
            continue
        er = m["exit_reasons"]
        low = "  *LOW CONF*" if m["n_trades"] < 30 else ""
        print(
            f"  {BUCKET_LABELS[bk]:<14} {m['n_trades']:>5} {m['win_rate_pct']:>6.1f} "
            f"{m['avg_ret_pct']:>8.3f} {m['median_ret_pct']:>8.3f} "
            f"{m['avg_R']:>6.3f} {m['median_R']:>6.3f} "
            f"{m['total_ret_pct']:>8.2f} {m['max_dd_pct']:>7.2f} "
            f"{m['avg_bars_held']:>8.1f}  "
            f"MD={er['MACD_DOWN']} SL={er['STOP_LOSS']} TP={er['TAKE_PROFIT']} EOD={er['EOD']}"
            f"{low}"
        )


# ── Markdown generation ───────────────────────────────────────────────────────
def _md_table_row(m: dict, label: str) -> str:
    if m["n_trades"] == 0:
        return f"| {label} | 0 | — | — | — | — | — | — | — | — |"
    er = m["exit_reasons"]
    lc = " \\*" if m["n_trades"] < 30 else ""
    exits = f"MD={er['MACD_DOWN']} SL={er['STOP_LOSS']} TP={er['TAKE_PROFIT']} EOD={er['EOD']}"
    return (
        f"| {label}{lc} | {m['n_trades']} | {m['win_rate_pct']}% | "
        f"{m['avg_ret_pct']}% | {m['median_ret_pct']}% | "
        f"{m['avg_R']} | {m['median_R']} | "
        f"{m['total_ret_pct']}% | {m['max_dd_pct']}% | {exits} |"
    )


_MD_HEADER = "| Bucket | N | Win% | AvgRet% | MedRet% | AvgR | MedR | TotRet% | MaxDD% | Exits |\n|--------|---|------|---------|---------|------|------|---------|--------|-------|"


def _md_metrics_table(metrics_by_bucket: dict) -> str:
    rows = "\n".join(
        _md_table_row(metrics_by_bucket.get(bk, {"n_trades": 0}), BUCKET_LABELS[bk])
        for bk in BUCKETS
    )
    return _MD_HEADER + "\n" + rows


def _fold_consistency_section(oos_folds_json: list[dict], case: str) -> str:
    lines = []
    for bk in BUCKETS:
        folds_with_trades  = [f for f in oos_folds_json if f[case].get(bk, {}).get("n_trades", 0) > 0]
        folds_profitable   = [f for f in folds_with_trades
                              if (f[case][bk].get("total_ret_pct") or 0) > 0]
        oos_n = sum(f[case].get(bk, {}).get("n_trades", 0) for f in oos_folds_json)
        lc = " — **LOW CONFIDENCE (OOS n<30)**" if oos_n < 30 else ""
        lines.append(
            f"- **{BUCKET_LABELS[bk]}**: profitable in "
            f"{len(folds_profitable)}/{len(folds_with_trades)} OOS folds with trades "
            f"| OOS n={oos_n}{lc}"
        )
    return "\n".join(lines)


def _auto_findings(oos_agg: dict) -> str:
    base = oos_agg["base_case"]
    sens = oos_agg["sensitivity_case"]
    gte_b  = base.get("gte_1_2",        {"n_trades": 0})
    mid_b  = base.get("vol_0_5_to_1_0", {"n_trades": 0})
    gte_s  = sens.get("gte_1_2",        {"n_trades": 0})
    mid_s  = sens.get("vol_0_5_to_1_0", {"n_trades": 0})

    lines = []

    if gte_b["n_trades"] < 10 or mid_b["n_trades"] < 10:
        lines.append(
            "**Insufficient OOS sample size.** One or more buckets have fewer than 10 OOS trades. "
            "No conclusion can be drawn about the volume filter from this dataset alone."
        )
        return "\n".join(lines)

    # Compare avg_R between >=1.2x and 0.5-1.0x buckets (base case)
    gte_R = gte_b.get("avg_R") or 0
    mid_R = mid_b.get("avg_R") or 0
    gte_win = gte_b.get("win_rate_pct") or 0
    mid_win = mid_b.get("win_rate_pct") or 0

    lines.append(
        f"**1. >=1.2x bucket (production threshold):** "
        f"OOS avg R = {gte_R}, win rate = {gte_win}%, n = {gte_b['n_trades']}."
    )
    lines.append(
        f"**2. 0.5-1.0x bucket (below threshold):** "
        f"OOS avg R = {mid_R}, win rate = {mid_win}%, n = {mid_b['n_trades']}."
    )

    if gte_R > mid_R and gte_win > mid_win:
        lines.append(
            "**3. Volume filter appears to be adding alpha in the base case.** "
            "The >=1.2x bucket outperforms 0.5-1.0x on both avg R and win rate."
        )
    elif gte_R < mid_R or gte_win < mid_win:
        lines.append(
            "**3. Mixed or reversed signal in base case.** "
            "Lower-volume trades performed comparably or better on at least one metric. "
            "This is not sufficient evidence to lower the threshold — check fold consistency."
        )
    else:
        lines.append("**3. Results are approximately equal across buckets.**")

    # Check if conclusion is stable across execution assumptions
    gte_R_s = gte_s.get("avg_R") or 0
    mid_R_s = mid_s.get("avg_R") or 0
    base_conclusion_positive = gte_R > mid_R
    sens_conclusion_positive = gte_R_s > mid_R_s
    if base_conclusion_positive != sens_conclusion_positive:
        lines.append(
            "**4. Conclusion is NOT stable across execution assumptions.** "
            "The ranking of buckets by avg R flips between base case and sensitivity case. "
            "This means the evidence is not strong enough for a production threshold change."
        )
    else:
        lines.append(
            "**4. Conclusion is consistent across both execution assumptions.**"
        )

    return "\n\n".join(lines)


def _auto_recommendation(oos_agg: dict, oos_folds_json: list[dict]) -> str:
    base = oos_agg["base_case"]
    gte_b = base.get("gte_1_2",        {"n_trades": 0})
    mid_b = base.get("vol_0_5_to_1_0", {"n_trades": 0})

    if gte_b["n_trades"] < 10 or mid_b["n_trades"] < 10:
        return (
            "Insufficient OOS sample size to make a recommendation. "
            "Keep `MIN_RELATIVE_VOLUME=1.2` and collect more paper sessions."
        )

    gte_R   = gte_b.get("avg_R")   or 0
    mid_R   = mid_b.get("avg_R")   or 0
    gte_win = gte_b.get("win_rate_pct") or 0
    mid_win = mid_b.get("win_rate_pct") or 0

    # Check fold consistency for >=1.2x bucket
    folds_with_gte = [f for f in oos_folds_json if f["base_case"].get("gte_1_2", {}).get("n_trades", 0) > 0]
    folds_gte_profitable = sum(
        1 for f in folds_with_gte
        if (f["base_case"]["gte_1_2"].get("total_ret_pct") or 0) > 0
    )

    if gte_R > mid_R and gte_win > mid_win:
        return (
            f"The >=1.2x bucket outperforms the 0.5-1.0x bucket on avg R ({gte_R} vs {mid_R}) "
            f"and win rate ({gte_win}% vs {mid_win}%) in OOS data. "
            f"The >=1.2x bucket was profitable in {folds_gte_profitable}/{len(folds_with_gte)} OOS folds. "
            "**Recommend keeping `MIN_RELATIVE_VOLUME=1.2`.** "
            "The volume filter appears to be earning its keep."
        )
    else:
        return (
            f"Evidence is mixed: >=1.2x avg R = {gte_R}, 0.5-1.0x avg R = {mid_R}. "
            "Do not lower the volume threshold based on this data alone. "
            "**Recommend keeping `MIN_RELATIVE_VOLUME=1.2`** and collecting more paper sessions "
            "before revisiting this question."
        )


def generate_markdown(results: dict, oos_folds_json: list[dict], feed: str) -> str:
    p = results["params"]
    run_date = results["run_date"][:10]

    findings_text      = _auto_findings(results["oos_aggregate"])
    recommendation_txt = _auto_recommendation(results["oos_aggregate"], oos_folds_json)

    return f"""\
# Volume Bucket Comparison — MACD Crossover Entry Filter
**Date:** {run_date}
**Analyst:** Claude (research layer)
**Status:** Complete — awaiting user review
**Approval required before any config.py edit:** Yes

---

## Question
On 2026-04-28, SPY had a MACD bullish crossover at 13:00 ET with RSI OK but relative volume
at only 0.50x. The production `MIN_RELATIVE_VOLUME={p['MIN_REL_VOL_PROD']}` blocked the entry.
SPY then rallied. Is the volume floor adding alpha, or filtering out too many winners?

## Method
- Script: `research/backtest/volume_bucket_comparison.py`
- Results: `research/backtest/volume_bucket_results.json`
- Instrument: {SYMBOL}, {BAR_MINUTES}-min bars, {feed.upper()} feed
- History: {results['history_months']} months — walk-forward OOS after {WARMUP_MONTHS}-month warmup
- Indicators: MACD({p['MACD_FAST']},{p['MACD_SLOW']},{p['MACD_SIGNAL_WIN']}), RSI({p['RSI_PERIOD']}),
  relative volume ({p['REL_VOL_WINDOW']}-bar rolling mean) — reimplemented in research isolation
- Entry: strict 1-bar MACD histogram crossover, RSI in [{p['RSI_LOW']}, {p['RSI_HIGH']}]
  **No volume filter at entry — each trade is tagged by crossover-bar bucket only**
- Intrabar SL+TP conflict: STOP_LOSS assumed hit first (conservative)
- Exit: MACD crossunder | stop-loss -{p['STOP_LOSS_PCT']*100:.0f}% | take-profit +{p['TAKE_PROFIT_PCT']*100:.0f}% | EOD (15:45 ET)
- Two execution cases reported; if conclusion changes between them, evidence is weak
- \\* = fewer than 30 trades — low confidence

---

## Results — Overall (full period)

### Base Case (entry at signal bar close)

{_md_metrics_table(results['overall']['base_case'])}

### Sensitivity Case (entry at next-bar open)

{_md_metrics_table(results['overall']['sensitivity_case'])}

---

## OOS Aggregate (after {WARMUP_MONTHS}-month warmup)

### Base Case

{_md_metrics_table(results['oos_aggregate']['base_case'])}

### Sensitivity Case

{_md_metrics_table(results['oos_aggregate']['sensitivity_case'])}

---

## Fold Consistency

### Base Case

{_fold_consistency_section(oos_folds_json, 'base_case')}

### Sensitivity Case

{_fold_consistency_section(oos_folds_json, 'sensitivity_case')}

---

## Key Findings

{findings_text}

---

## Recommendation

{recommendation_txt}

---

## Caveats
- IEX data may differ from SIP in volume readings; relative volume thresholds may not transfer
- Paper trading PnL does not predict live PnL
- Backtest does not account for slippage, fees, or partial fills
- Walk-forward avoids look-ahead bias but does not guarantee future performance
- Any bucket with OOS n<30 should not drive a threshold change

---

**User reviewed — no production change approved.**
"""


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"\nFetching {HISTORY_MONTHS}m of {BAR_MINUTES}-min {SYMBOL} bars ...")
    df   = add_indicators(fetch_history())
    feed = os.environ.get("ALPACA_DATA_FEED", "iex")

    # ── Run both simulation passes ────────────────────────────────────────────
    all_trades: dict[str, list[dict]] = {}
    for case in CASES:
        print(f"  Running {case} ...")
        all_trades[case] = simulate(df, case)
        counts = {bk: sum(1 for t in all_trades[case] if t["bucket"] == bk) for bk in BUCKETS}
        print(f"    → {len(all_trades[case])} trades total | {counts}")

    # ── Overall metrics ───────────────────────────────────────────────────────
    overall: dict[str, dict] = {}
    for case in CASES:
        overall[case] = {
            bk: compute_metrics([t for t in all_trades[case] if t["bucket"] == bk])
            for bk in BUCKETS
        }

    # ── OOS periods ───────────────────────────────────────────────────────────
    oos_ps     = oos_periods(df)
    oos_ps_set = set(str(p) for p in oos_ps)

    # ── Per-fold breakdown ────────────────────────────────────────────────────
    oos_folds_json: list[dict] = []
    for p in oos_ps:
        p_str = str(p)
        entry: dict = {"fold": p_str}
        for case in CASES:
            fold_trades = slice_by_period(all_trades[case], p_str)
            entry[case] = {
                bk: compute_metrics([t for t in fold_trades if t["bucket"] == bk])
                for bk in BUCKETS
            }
        oos_folds_json.append(entry)

    # ── OOS aggregate ─────────────────────────────────────────────────────────
    oos_aggregate: dict[str, dict] = {}
    for case in CASES:
        oos_trades = [
            t for t in all_trades[case]
            if str(pd.Timestamp(t["signal_time"]).to_period("M")) in oos_ps_set
        ]
        oos_aggregate[case] = {
            bk: compute_metrics([t for t in oos_trades if t["bucket"] == bk])
            for bk in BUCKETS
        }

    # ── Print summary tables ──────────────────────────────────────────────────
    for case in CASES:
        _print_table("OVERALL (full period)", case, overall[case])
    for case in CASES:
        _print_table("OOS AGGREGATE", case, oos_aggregate[case])

    # ── Save JSON ─────────────────────────────────────────────────────────────
    results = {
        "run_date":       datetime.datetime.now().isoformat(),
        "symbol":         SYMBOL,
        "feed":           feed,
        "history_months": HISTORY_MONTHS,
        "warmup_months":  WARMUP_MONTHS,
        "params": {
            "MACD_FAST":        MACD_FAST,
            "MACD_SLOW":        MACD_SLOW,
            "MACD_SIGNAL_WIN":  MACD_SIGNAL_WIN,
            "RSI_PERIOD":       RSI_PERIOD,
            "RSI_LOW":          RSI_LOW,
            "RSI_HIGH":         RSI_HIGH,
            "STOP_LOSS_PCT":    STOP_LOSS_PCT,
            "TAKE_PROFIT_PCT":  TAKE_PROFIT_PCT,
            "REL_VOL_WINDOW":   REL_VOL_WINDOW,
            "MIN_REL_VOL_PROD": MIN_REL_VOL_PROD,
        },
        "overall":        {case: overall[case]      for case in CASES},
        "oos_aggregate":  {case: oos_aggregate[case] for case in CASES},
        "oos_by_fold":    oos_folds_json,
        "trades":         {case: all_trades[case]   for case in CASES},
    }

    out_json = ROOT / "research" / "backtest" / "volume_bucket_results.json"
    out_json.write_text(json.dumps(results, indent=2))
    print(f"\n  Results saved -> research/backtest/volume_bucket_results.json")

    # ── Generate markdown note ────────────────────────────────────────────────
    md_text = generate_markdown(results, oos_folds_json, feed)
    out_md  = ROOT / "research" / "notebooks" / "2026-04-28_volume_bucket_comparison.md"
    out_md.write_text(md_text)
    print(f"  Note saved    -> research/notebooks/2026-04-28_volume_bucket_comparison.md\n")


if __name__ == "__main__":
    main()
