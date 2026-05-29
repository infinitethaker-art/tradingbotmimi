"""
Parameter Sensitivity Study — Volume & RSI Upper Band
======================================================
Research only. No production change approved.
Do not lower thresholds just to increase trade count.
A change is only recommended if it improves OOS avg R, drawdown, and fold consistency
relative to the current baseline (MIN_RELATIVE_VOLUME=1.2, RSI_HIGH=65).

Research question:
    The bot generated 0 ENTER_LONG signals across 93 scans (week of 2026-04-28).
    Volume was the primary blocker (~78% of bars). RSI was secondary.
    This study tests whether any relaxation of MIN_RELATIVE_VOLUME or RSI_HIGH
    improves OOS avg R, max drawdown, and fold consistency versus the baseline.
    Higher trade count alone is NOT sufficient justification for a change.

Test matrix:
    Volume sweep   (RSI_HIGH fixed at 65):  vol in [0.8, 0.9, 1.0, 1.1, 1.2]
    RSI sweep      (vol fixed at 1.2):      rsi_high in [65, 70, 75]
    Combinations:  (1.2,65) (1.2,70) (1.2,75) (1.1,70) (1.0,70)

Same methodology as prior research (volume_bucket_comparison.py, macd_window_comparison.py):
    - SPY, 15-min bars, IEX feed
    - MACD(12,26,9) strict 1-bar crossover
    - RSI(14), relative volume vs 20-bar rolling mean
    - Long only, no overnight positions, window 09:45–15:45 ET
    - Exit: MACD crossunder | SL -2% | TP +4% | EOD
    - Intrabar SL+TP conflict: STOP_LOSS assumed hit first (conservative)
    - Walk-forward: 3-month warmup, monthly OOS folds
    - Two execution cases: signal-bar close and next-bar open

ISOLATION RULE: no imports from tools/ or scheduler/.
Run from project root:
    python research/backtest/parameter_sensitivity_study.py
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

# ── Fixed parameters (mirrors production; do NOT import config.py) ─────────────
SYMBOL          = "SPY"
BAR_MINUTES     = 15
MACD_FAST       = 12
MACD_SLOW       = 26
MACD_SIGNAL_WIN = 9
RSI_PERIOD      = 14
RSI_LOW         = 35.0          # not swept — only upper band varies
STOP_LOSS_PCT   = 0.02
TAKE_PROFIT_PCT = 0.04
REL_VOL_WINDOW  = 20
HISTORY_MONTHS  = 13
WARMUP_MONTHS   = 3

CASES = ["base_case", "sensitivity_case"]

# ── Test matrix ────────────────────────────────────────────────────────────────
# Each entry: (min_rel_vol, rsi_high, group_tags)
# group_tags controls which summary sections a config appears in.
_RAW_MATRIX = [
    # Volume sweep (RSI_HIGH=65)
    (0.8,  65.0, ["vol_sweep"]),
    (0.9,  65.0, ["vol_sweep"]),
    (1.0,  65.0, ["vol_sweep", "combo"]),
    (1.1,  65.0, ["vol_sweep", "combo"]),
    (1.2,  65.0, ["vol_sweep", "rsi_sweep", "combo"]),  # baseline
    # RSI sweep (vol=1.2)
    (1.2,  70.0, ["rsi_sweep", "combo"]),
    (1.2,  75.0, ["rsi_sweep", "combo"]),
    # Additional combinations
    (1.1,  70.0, ["combo"]),
    (1.0,  70.0, ["combo"]),
]

# Deduplicate by (vol, rsi) — preserve order
_seen: set = set()
TEST_CONFIGS: list[tuple[float, float, list[str]]] = []
for _vol, _rsi, _tags in _RAW_MATRIX:
    _key = (_vol, _rsi)
    if _key not in _seen:
        _seen.add(_key)
        TEST_CONFIGS.append((_vol, _rsi, _tags))

BASELINE_KEY = (1.2, 65.0)


def config_label(vol: float, rsi_high: float) -> str:
    return f"vol={vol:.1f} rsi<={rsi_high:.0f}"


# ── Data ───────────────────────────────────────────────────────────────────────
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
        raise RuntimeError(f"No bars returned for {SYMBOL} (feed={feed}). Check credentials.")

    df = pd.DataFrame(
        [{"open": float(b.open), "high": float(b.high),
          "low":  float(b.low),  "close": float(b.close),
          "volume": int(b.volume)} for b in bars],
        index=pd.DatetimeIndex([b.timestamp for b in bars], name="timestamp"),
    ).sort_index()
    df.index = df.index.tz_convert("America/New_York")
    print(f"  {len(df):,} bars  |  {df.index[0].date()} -> {df.index[-1].date()}  |  feed={feed}")
    return df


# ── Indicators (self-contained; no tools/ imports) ─────────────────────────────
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

    # Relative volume (20-bar rolling mean)
    df["rel_vol"] = df["volume"] / df["volume"].rolling(REL_VOL_WINDOW).mean()

    return df


# ── Simulation ─────────────────────────────────────────────────────────────────
def _in_session(ts: pd.Timestamp) -> bool:
    hm = (ts.hour, ts.minute)
    return (9, 45) <= hm <= (15, 45)


def simulate(
    df: pd.DataFrame,
    case: str,
    min_rel_vol: float,
    rsi_high: float,
) -> list[dict]:
    """
    Run one simulation pass applying entry filters: MACD crossover + RSI in band + rel_vol >= min_rel_vol.

    case:
        "base_case"        — entry at signal bar close
        "sensitivity_case" — entry at next-bar open
    """
    trades            = []
    hist              = df["macd_hist"]
    in_pos            = False
    entry_px          = 0.0
    signal_i          = 0
    entry_i           = 0
    rel_vol_at_signal = 0.0
    rsi_at_signal     = 0.0
    pending_next_open = False

    for i in range(1, len(df)):
        ts      = df.index[i]
        row     = df.iloc[i]
        in_sess = _in_session(ts)

        # Sensitivity case: execute pending entry at this bar's open
        if pending_next_open and not in_pos:
            if in_sess:
                entry_px = float(row["open"])
                entry_i  = i
                in_pos   = True
            pending_next_open = False

        # Force EOD exit
        if in_pos and not in_sess:
            exit_px = float(df.iloc[i - 1]["close"])
            trades.append(_make_trade(
                entry_px, exit_px, signal_i, entry_i, i - 1, df,
                "EOD", rel_vol_at_signal, rsi_at_signal,
            ))
            in_pos = False
            continue

        if not in_sess:
            continue

        # Exit check
        if in_pos:
            sl_level = entry_px * (1 - STOP_LOSS_PCT)
            tp_level = entry_px * (1 + TAKE_PROFIT_PCT)
            sl_hit   = float(row["low"])  <= sl_level
            tp_hit   = float(row["high"]) >= tp_level
            md_hit   = hist.iloc[i - 1] > 0.0 >= hist.iloc[i]

            if sl_hit:
                reason, exit_px = "STOP_LOSS", sl_level
            elif tp_hit:
                reason, exit_px = "TAKE_PROFIT", tp_level
            elif md_hit:
                reason, exit_px = "MACD_DOWN", float(row["close"])
            else:
                reason = None

            if reason:
                trades.append(_make_trade(
                    entry_px, exit_px, signal_i, entry_i, i, df,
                    reason, rel_vol_at_signal, rsi_at_signal,
                ))
                in_pos = False

        # Entry check
        if in_pos or pending_next_open:
            continue

        prev_hist = hist.iloc[i - 1]
        curr_hist = hist.iloc[i]
        if not (prev_hist <= 0.0 < curr_hist):
            continue

        rsi = float(row["rsi"])
        rv  = row["rel_vol"]

        if pd.isna(rv) or not (RSI_LOW <= rsi <= rsi_high) or float(rv) < min_rel_vol:
            continue

        rel_vol_at_signal = float(rv)
        rsi_at_signal     = rsi
        signal_i          = i

        if case == "base_case":
            entry_px = float(row["close"])
            entry_i  = i
            in_pos   = True
        else:
            if i + 1 < len(df):
                pending_next_open = True

    return trades


def _make_trade(
    entry_px: float, exit_px: float,
    signal_i: int, entry_i: int, exit_i: int,
    df: pd.DataFrame,
    reason: str,
    rel_vol: float, rsi: float,
) -> dict:
    pnl_decimal = (exit_px - entry_px) / entry_px
    return {
        "signal_time":   str(df.index[signal_i]),
        "entry_time":    str(df.index[entry_i]),
        "exit_time":     str(df.index[exit_i]),
        "entry_price":   round(entry_px, 4),
        "exit_price":    round(exit_px, 4),
        "pnl_pct":       round(pnl_decimal * 100, 4),
        "R":             round(pnl_decimal / STOP_LOSS_PCT, 4),
        "bars_held":     exit_i - entry_i,
        "exit_reason":   reason,
        "rel_vol":       round(rel_vol, 4),
        "rsi_at_signal": round(rsi, 2),
    }


# ── Metrics ────────────────────────────────────────────────────────────────────
_EMPTY_REASONS = {"MACD_DOWN": 0, "STOP_LOSS": 0, "TAKE_PROFIT": 0, "EOD": 0}


def compute_metrics(trades: list[dict], oos_trading_days: float = 0.0) -> dict:
    if not trades:
        return {
            "n_trades": 0, "win_rate_pct": None,
            "avg_ret_pct": None, "median_ret_pct": None,
            "avg_R": None,       "median_R": None,
            "total_ret_pct": None, "max_dd_pct": None,
            "sharpe": None,       "avg_trades_per_week": None,
            "exit_reasons": dict(_EMPTY_REASONS),
        }

    r_dec  = pd.Series([t["pnl_pct"] / 100 for t in trades])
    R_vals = pd.Series([t["R"] for t in trades])
    equity = (1 + r_dec).cumprod()
    dd     = float((equity / equity.cummax() - 1).min())

    # Sharpe: mean_R / std_R (trade-level, no annualization)
    std_R  = float(R_vals.std())
    sharpe = round(float(R_vals.mean()) / std_R, 3) if std_R > 0 else None

    # Avg trades per week
    atpw = None
    if oos_trading_days > 0:
        weeks = oos_trading_days / 5.0
        atpw  = round(len(trades) / weeks, 2) if weeks > 0 else None

    reasons = dict(_EMPTY_REASONS)
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1

    return {
        "n_trades":           len(trades),
        "win_rate_pct":       round(float((r_dec > 0).mean() * 100), 1),
        "avg_ret_pct":        round(float(r_dec.mean() * 100), 3),
        "median_ret_pct":     round(float(r_dec.median() * 100), 3),
        "avg_R":              round(float(R_vals.mean()), 3),
        "median_R":           round(float(R_vals.median()), 3),
        "total_ret_pct":      round(float((equity.iloc[-1] - 1) * 100), 2),
        "max_dd_pct":         round(dd * 100, 2),
        "sharpe":             sharpe,
        "avg_trades_per_week": atpw,
        "exit_reasons":       reasons,
    }


# ── Walk-forward helpers ───────────────────────────────────────────────────────
def oos_periods(df: pd.DataFrame):
    periods = df.index.to_period("M").unique().sort_values()
    return periods[WARMUP_MONTHS:]


def oos_trading_days_count(df: pd.DataFrame, oos_ps_set: set) -> float:
    oos_mask = df.index.to_period("M").astype(str).isin(oos_ps_set)
    return float(df[oos_mask].index.normalize().nunique())


def slice_by_period(trades: list[dict], period_str: str) -> list[dict]:
    return [
        t for t in trades
        if str(pd.Timestamp(t["signal_time"]).to_period("M")) == period_str
    ]


# ── Per-config run ─────────────────────────────────────────────────────────────
def run_config(
    df: pd.DataFrame,
    min_rel_vol: float,
    rsi_high: float,
    oos_ps,
    oos_ps_set: set,
    oos_td: float,
) -> dict:
    """Run both execution cases for a single (min_rel_vol, rsi_high) pair."""
    all_trades: dict[str, list[dict]] = {}
    for case in CASES:
        all_trades[case] = simulate(df, case, min_rel_vol, rsi_high)

    # Overall (full period)
    overall: dict[str, dict] = {
        case: compute_metrics(all_trades[case]) for case in CASES
    }

    # OOS aggregate
    oos_agg: dict[str, dict] = {}
    for case in CASES:
        oos_trades = [
            t for t in all_trades[case]
            if str(pd.Timestamp(t["signal_time"]).to_period("M")) in oos_ps_set
        ]
        oos_agg[case] = compute_metrics(oos_trades, oos_trading_days=oos_td)

    # Per-fold breakdown
    oos_folds: list[dict] = []
    for p in oos_ps:
        p_str = str(p)
        entry: dict = {"fold": p_str}
        for case in CASES:
            fold_trades = slice_by_period(all_trades[case], p_str)
            entry[case] = compute_metrics(fold_trades)
        oos_folds.append(entry)

    # Fold consistency (base case): profitable folds / folds with trades
    folds_with_trades  = [f for f in oos_folds if (f["base_case"]["n_trades"] or 0) > 0]
    folds_profitable   = [f for f in folds_with_trades
                          if (f["base_case"]["total_ret_pct"] or 0) > 0]
    fold_consistency   = f"{len(folds_profitable)}/{len(folds_with_trades)}"

    return {
        "min_rel_vol": min_rel_vol,
        "rsi_high":    rsi_high,
        "overall":     overall,
        "oos_agg":     oos_agg,
        "oos_folds":   oos_folds,
        "fold_consistency_base": fold_consistency,
        "trades":      all_trades,
    }


# ── Printing ───────────────────────────────────────────────────────────────────
_W = 130


def _print_summary_row(label: str, m: dict, is_baseline: bool = False) -> None:
    flag = " [baseline]" if is_baseline else ""
    if m["n_trades"] == 0:
        print(f"  {label:<22} {'0':>5}  -- no trades --{flag}")
        return
    lc = "  *LOW CONF*" if m["n_trades"] < 30 else ""
    er = m["exit_reasons"]
    print(
        f"  {label:<22} {m['n_trades']:>5} {m['win_rate_pct']:>6.1f} "
        f"{m['avg_ret_pct']:>8.3f} {m['avg_R']:>7.3f} "
        f"{m['total_ret_pct']:>9.2f} {m['max_dd_pct']:>8.2f} "
        f"{str(m['sharpe'] or 'n/a'):>7} {str(m['avg_trades_per_week'] or 'n/a'):>7} "
        f"{m['fold_consistency_base'] if 'fold_consistency_base' in m else 'n/a':>6}  "
        f"MD={er['MACD_DOWN']} SL={er['STOP_LOSS']} TP={er['TAKE_PROFIT']} EOD={er['EOD']}"
        f"{lc}{flag}"
    )


def _print_table(title: str, rows: list[tuple[str, dict, bool]]) -> None:
    print(f"\n{'=' * _W}")
    print(f"  {title}")
    print(f"{'=' * _W}")
    hdr = (
        f"  {'Config':<22} {'N':>5} {'Win%':>6} {'AvgRet%':>8} {'AvgR':>7} "
        f"{'TotRet%':>9} {'MaxDD%':>8} {'Sharpe':>7} {'Tr/Wk':>7} {'Folds':>6}  ExitReasons"
    )
    print(hdr)
    print("-" * _W)
    for label, m, is_bl in rows:
        _print_summary_row(label, m, is_bl)


# ── Markdown ───────────────────────────────────────────────────────────────────
_MD_HDR = (
    "| Config | N | Win% | AvgRet% | AvgR | TotRet% | MaxDD% | Sharpe | Tr/Wk | Folds (base) | "
    "Exits |\n"
    "|--------|---|------|---------|------|---------|--------|--------|-------|-------------|-------|"
)


def _md_row(label: str, m: dict, fold_cons: str, is_baseline: bool = False) -> str:
    flag = " **← baseline**" if is_baseline else ""
    if m["n_trades"] == 0:
        return f"| {label}{flag} | 0 | — | — | — | — | — | — | — | — | — |"
    er   = m["exit_reasons"]
    lc   = " \\*" if m["n_trades"] < 30 else ""
    exits = f"MD={er['MACD_DOWN']} SL={er['STOP_LOSS']} TP={er['TAKE_PROFIT']} EOD={er['EOD']}"
    return (
        f"| {label}{lc}{flag} | {m['n_trades']} | {m['win_rate_pct']}% | "
        f"{m['avg_ret_pct']}% | {m['avg_R']} | {m['total_ret_pct']}% | "
        f"{m['max_dd_pct']}% | {m['sharpe'] or '—'} | {m['avg_trades_per_week'] or '—'} | "
        f"{fold_cons} | {exits} |"
    )


def _md_section(title: str, entries: list[tuple[str, dict, str, bool]]) -> str:
    rows = "\n".join(_md_row(lbl, m, fc, bl) for lbl, m, fc, bl in entries)
    return f"### {title}\n\n{_MD_HDR}\n{rows}"


def generate_markdown(
    results_map: dict,
    oos_ps,
    feed: str,
    run_date: str,
) -> str:
    baseline = results_map[BASELINE_KEY]

    def oos_entry(key):
        r = results_map[key]
        m = dict(r["oos_agg"]["base_case"])
        m["fold_consistency_base"] = r["fold_consistency_base"]
        return m

    bl_m = oos_entry(BASELINE_KEY)

    # Volume sweep (RSI=65)
    vol_sweep_keys = [(v, 65.0) for v in [0.8, 0.9, 1.0, 1.1, 1.2]]
    vol_sweep_entries = [
        (config_label(v, r), oos_entry((v, r)),
         results_map[(v, r)]["fold_consistency_base"], (v, r) == BASELINE_KEY)
        for v, r in vol_sweep_keys if (v, r) in results_map
    ]

    # RSI sweep (vol=1.2)
    rsi_sweep_keys = [(1.2, rh) for rh in [65.0, 70.0, 75.0]]
    rsi_sweep_entries = [
        (config_label(v, r), oos_entry((v, r)),
         results_map[(v, r)]["fold_consistency_base"], (v, r) == BASELINE_KEY)
        for v, r in rsi_sweep_keys if (v, r) in results_map
    ]

    # Combination matrix
    combo_keys = [(1.2, 65.0), (1.2, 70.0), (1.2, 75.0), (1.1, 70.0), (1.0, 70.0)]
    combo_entries = [
        (config_label(v, r), oos_entry((v, r)),
         results_map[(v, r)]["fold_consistency_base"], (v, r) == BASELINE_KEY)
        for v, r in combo_keys if (v, r) in results_map
    ]

    # Auto-recommendation logic
    def _recommend() -> str:
        candidates = []
        for key, r in results_map.items():
            if key == BASELINE_KEY:
                continue
            m = r["oos_agg"]["base_case"]
            bm = bl_m
            if m["n_trades"] is None or m["n_trades"] < 10:
                continue
            avg_R_better = (m["avg_R"] or -99) > (bm["avg_R"] or -99)
            dd_better    = (m["max_dd_pct"] or -99) > (bm["max_dd_pct"] or -99)  # less negative = better
            # Parse fold consistency
            def parse_fc(fc_str: str):
                try:
                    num, den = fc_str.split("/")
                    return int(num), int(den)
                except Exception:
                    return 0, 0
            bl_num, bl_den = parse_fc(bm.get("fold_consistency_base", "0/0") if isinstance(bm, dict) else "0/0")
            c_num,  c_den  = parse_fc(r["fold_consistency_base"])
            fold_better = (c_num / c_den if c_den else 0) >= (bl_num / bl_den if bl_den else 0)
            if avg_R_better and dd_better and fold_better:
                candidates.append((key, m))
        if not candidates:
            return (
                "No configuration tested here outperforms the baseline on **all three criteria** "
                "(OOS avg R, max drawdown, fold consistency).\n\n"
                "**Recommendation: keep MIN_RELATIVE_VOLUME=1.2 and RSI_HIGH=65.**\n\n"
                "Do not lower thresholds to increase trade count. Higher trade count alone is not "
                "a reason to change parameters."
            )
        best_key, best_m = max(candidates, key=lambda x: (x[1]["avg_R"] or -99))
        v, rh = best_key
        return (
            f"Configuration `vol={v:.1f} rsi≤{rh:.0f}` improves on the baseline on avg R, "
            f"max drawdown, and fold consistency in OOS data.\n\n"
            f"**This is a candidate for user review.** "
            f"Do not apply to `.env` without explicit user approval. "
            f"Verify that the sensitivity case (next-bar open) shows the same conclusion before proceeding."
        )

    recommendation = _recommend()
    n_folds = len(oos_ps)

    return f"""\
# Parameter Sensitivity Study — Volume & RSI Upper Band
**Date:** {run_date[:10]}
**Analyst:** Claude (research layer)
**Status:** Complete — awaiting user review

---

> **Research only. No production change approved.**
> Do not lower thresholds just to increase trade count.
> A change is only recommended if it improves OOS avg R, drawdown, and fold consistency
> relative to the current baseline (MIN_RELATIVE_VOLUME=1.2, RSI_HIGH=65).

---

## Background

The bot generated 0 ENTER_LONG signals across 93 scans in the week of 2026-04-28.
Volume was the primary blocker (~78% of bars below 1.2x threshold).
RSI was secondary (overbought mornings). MACD crossover logic is not under review
(Apr 27 research confirmed strict N=1 is optimal).

Prior result (Apr 28 backtest): the 1.0–1.2x bucket was already isolated:
32 OOS trades, 46.9% win rate, avg R −0.035, total return −2.25%, max DD −2.82%.
This does not support lowering the volume threshold.

This study tests whether any combination of relaxed volume or RSI upper band
improves the OOS edge profile — not merely the trade count.

---

## Method

- Script: `research/backtest/parameter_sensitivity_study.py`
- Results: `research/backtest/parameter_sensitivity_results.json`
- Instrument: {SYMBOL}, {BAR_MINUTES}-min bars, {feed.upper()} feed
- History: {HISTORY_MONTHS} months — walk-forward OOS after {WARMUP_MONTHS}-month warmup
  ({n_folds} OOS folds)
- Indicators: MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL_WIN}), RSI({RSI_PERIOD}),
  rel vol ({REL_VOL_WINDOW}-bar rolling mean) — reimplemented in research isolation
- Entry: strict 1-bar MACD histogram crossover + RSI in [35, RSI_HIGH] + rel_vol >= MIN_VOL
- Exit: MACD crossunder | SL -{STOP_LOSS_PCT*100:.0f}% | TP +{TAKE_PROFIT_PCT*100:.0f}% | EOD 15:45 ET
- Intrabar SL+TP conflict: STOP_LOSS assumed hit first (conservative)
- Two execution cases: base_case (signal-bar close) and sensitivity_case (next-bar open)
- \\* = fewer than 30 OOS trades — low confidence; do not drive a threshold change

All tables below show **OOS aggregate (base case)** unless labelled otherwise.
Tr/Wk = average trades per week over the OOS period.
Folds = profitable OOS months / OOS months with at least one trade.

---

## Results

{_md_section("Volume Sweep (RSI_HIGH fixed at 65)", vol_sweep_entries)}

{_md_section("RSI Upper Band Sweep (MIN_RELATIVE_VOLUME fixed at 1.2)", rsi_sweep_entries)}

{_md_section("Combination Matrix", combo_entries)}

---

## Sensitivity Case (next-bar open entry) — OOS Aggregate

All configs below; if conclusion changes versus base case, evidence is weak.

| Config | N | Win% | AvgR | TotRet% | MaxDD% | Sharpe |
|--------|---|------|------|---------|--------|--------|
{"".join(
    f"| {config_label(v, r)} {'[baseline]' if (v,r)==BASELINE_KEY else ''} "
    f"| {results_map[(v,r)]['oos_agg']['sensitivity_case']['n_trades'] or 0} "
    f"| {results_map[(v,r)]['oos_agg']['sensitivity_case']['win_rate_pct'] or '—'}% "
    f"| {results_map[(v,r)]['oos_agg']['sensitivity_case']['avg_R'] or '—'} "
    f"| {results_map[(v,r)]['oos_agg']['sensitivity_case']['total_ret_pct'] or '—'}% "
    f"| {results_map[(v,r)]['oos_agg']['sensitivity_case']['max_dd_pct'] or '—'}% "
    f"| {results_map[(v,r)]['oos_agg']['sensitivity_case']['sharpe'] or '—'} |\n"
    for v, r, _ in TEST_CONFIGS if (v, r) in results_map
)}
---

## Recommendation

{recommendation}

---

## Caveats

- IEX data may differ from SIP in volume readings; relative volume thresholds calibrated on IEX
  may not transfer to SIP when going live
- Paper trading PnL does not predict live PnL
- Backtest does not account for slippage, fees, or partial fills
- Walk-forward avoids look-ahead bias but does not guarantee future performance
- Any configuration with OOS n<30 should not drive a threshold change

---

**User reviewed — no production change approved.**
"""


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"\nParameter Sensitivity Study -- {SYMBOL} {BAR_MINUTES}m bars")
    print(f"Fetching {HISTORY_MONTHS}m of history ...")
    df   = add_indicators(fetch_history())
    feed = os.environ.get("ALPACA_DATA_FEED", "iex")

    oos_ps     = oos_periods(df)
    oos_ps_set = set(str(p) for p in oos_ps)
    oos_td     = oos_trading_days_count(df, oos_ps_set)
    print(f"  OOS folds: {len(oos_ps)}  ({oos_ps[0]} -> {oos_ps[-1]})  |  {oos_td:.0f} OOS trading days\n")

    # ── Run all configs ────────────────────────────────────────────────────────
    results_map: dict[tuple[float, float], dict] = {}
    for vol, rsi_h, _ in TEST_CONFIGS:
        label = config_label(vol, rsi_h)
        print(f"  Running {label} ...")
        r = run_config(df, vol, rsi_h, oos_ps, oos_ps_set, oos_td)
        for case in CASES:
            n = r["oos_agg"][case]["n_trades"] or 0
            print(f"    {case}: OOS n={n}")
        results_map[(vol, rsi_h)] = r

    # ── Print OOS summary tables ───────────────────────────────────────────────
    def _summary_row(key):
        r   = results_map[key]
        m   = dict(r["oos_agg"]["base_case"])
        m["fold_consistency_base"] = r["fold_consistency_base"]
        return config_label(*key), m, key == BASELINE_KEY

    vol_rows = [_summary_row((v, 65.0)) for v in [0.8, 0.9, 1.0, 1.1, 1.2] if (v, 65.0) in results_map]
    rsi_rows = [_summary_row((1.2, rh)) for rh in [65.0, 70.0, 75.0] if (1.2, rh) in results_map]
    combo_rows = [
        _summary_row(k) for k in [(1.2,65.0),(1.2,70.0),(1.2,75.0),(1.1,70.0),(1.0,70.0)]
        if k in results_map
    ]

    _print_table("VOLUME SWEEP -- OOS AGGREGATE (base case, RSI_HIGH=65)", vol_rows)
    _print_table("RSI SWEEP -- OOS AGGREGATE (base case, vol=1.2)", rsi_rows)
    _print_table("COMBINATION MATRIX -- OOS AGGREGATE (base case)", combo_rows)

    # ── Save JSON ──────────────────────────────────────────────────────────────
    run_date = datetime.datetime.now().isoformat()
    output = {
        "run_date":      run_date,
        "symbol":        SYMBOL,
        "feed":          feed,
        "history_months": HISTORY_MONTHS,
        "warmup_months": WARMUP_MONTHS,
        "oos_folds":     [str(p) for p in oos_ps],
        "oos_trading_days": oos_td,
        "fixed_params": {
            "MACD_FAST": MACD_FAST, "MACD_SLOW": MACD_SLOW,
            "MACD_SIGNAL_WIN": MACD_SIGNAL_WIN, "RSI_PERIOD": RSI_PERIOD,
            "RSI_LOW": RSI_LOW, "STOP_LOSS_PCT": STOP_LOSS_PCT,
            "TAKE_PROFIT_PCT": TAKE_PROFIT_PCT, "REL_VOL_WINDOW": REL_VOL_WINDOW,
        },
        "configs": [
            {
                "min_rel_vol":   r["min_rel_vol"],
                "rsi_high":      r["rsi_high"],
                "tags":          tags,
                "overall":       r["overall"],
                "oos_agg":       r["oos_agg"],
                "oos_folds":     r["oos_folds"],
                "fold_consistency_base": r["fold_consistency_base"],
            }
            for (vol, rsi_h, tags), r in zip(TEST_CONFIGS, [results_map[(v, rh)] for v, rh, _ in TEST_CONFIGS])
        ],
    }

    out_json = ROOT / "research" / "backtest" / "parameter_sensitivity_results.json"
    out_json.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n  Results saved -> research/backtest/parameter_sensitivity_results.json")

    # ── Generate markdown ──────────────────────────────────────────────────────
    md_text = generate_markdown(results_map, oos_ps, feed, run_date)
    out_md  = ROOT / "research" / "notebooks" / "2026-05-01_parameter_sensitivity_study.md"
    out_md.write_text(md_text, encoding="utf-8")
    print(f"  Note saved    -> research/notebooks/2026-05-01_parameter_sensitivity_study.md\n")


if __name__ == "__main__":
    main()
