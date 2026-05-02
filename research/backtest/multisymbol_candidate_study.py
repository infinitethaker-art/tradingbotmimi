"""
Multi-Symbol Candidate Study — SPY + QQQ
=========================================
Research only. No production change approved.

Research question:
    Can adding QQQ as a second symbol increase trade frequency (trades/week)
    without reducing average R or worsening max drawdown compared to SPY-only,
    using current production parameters?

Scenarios:
    1. spy_only                 — SPY alone with current production parameters
    2. qqq_only                 — QQQ alone with current production parameters
    3. combined_1pos            — SPY+QQQ, global max 1 open position at a time.
                                  Tiebreaker when both signal simultaneously:
                                    (a) higher rel_vol wins
                                    (b) if tied: RSI closer to 50 wins (abs(rsi-50) smaller)
                                    (c) if still tied: SPY wins (incumbent symbol)
    4. combined_1pos_per_symbol — 1 independent position per symbol (up to 2 simultaneous).
                                  HIGHER RISK / INFORMATIONAL ONLY. Not a production candidate.

Recommendation rule:
    QQQ addition via combined_1pos is worth considering ONLY IF vs spy_only it shows:
      - higher trades/week
      - no material reduction in avg R (threshold: -0.10 R)
      - no material worsening of max DD (threshold: -1.5 pp)
      - similar or better profitable OOS month consistency

Methodology (mirrors prior research):
    - 15-min bars, IEX feed, 13-month history, 3-month warmup, monthly OOS folds
    - MACD(12,26,9) strict 1-bar crossover, RSI(14), rel_vol 20-bar rolling mean
    - Long only, no overnight, window 09:45-15:45 ET
    - Exit: MACD crossunder | SL -2% | TP +4% | EOD
    - Intrabar SL+TP conflict: STOP_LOSS assumed hit first (conservative)
    - Indicators computed independently per symbol (no shared state)
    - Base case only (signal-bar close entry)

ISOLATION RULE: no imports from tools/ or scheduler/.
Run from project root:
    python research/backtest/multisymbol_candidate_study.py
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
BAR_MINUTES     = 15
MACD_FAST       = 12
MACD_SLOW       = 26
MACD_SIGNAL_WIN = 9
RSI_PERIOD      = 14
RSI_LOW         = 35.0
RSI_HIGH        = 65.0
MIN_REL_VOL     = 1.2
STOP_LOSS_PCT   = 0.02
TAKE_PROFIT_PCT = 0.04
REL_VOL_WINDOW  = 20
HISTORY_MONTHS  = 13
WARMUP_MONTHS   = 3


# ── Data ───────────────────────────────────────────────────────────────────────
def fetch_bars(symbol: str) -> pd.DataFrame:
    client = StockHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )
    feed  = os.environ.get("ALPACA_DATA_FEED", "iex")
    end   = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=HISTORY_MONTHS * 32)

    resp = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame(BAR_MINUTES, TimeFrameUnit.Minute),
        start=start,
        end=end,
        feed=feed,
        adjustment="raw",
    ))
    bars = resp.data.get(symbol, [])
    if not bars:
        raise RuntimeError(f"No bars returned for {symbol} (feed={feed}). Check credentials.")

    df = pd.DataFrame(
        [{"open": float(b.open), "high": float(b.high),
          "low":  float(b.low),  "close": float(b.close),
          "volume": int(b.volume)} for b in bars],
        index=pd.DatetimeIndex([b.timestamp for b in bars], name="timestamp"),
    ).sort_index()
    df.index = df.index.tz_convert("America/New_York")
    print(f"  {symbol}: {len(df):,} bars  |  {df.index[0].date()} -> {df.index[-1].date()}  |  feed={feed}")
    return df


# ── Indicators (self-contained; no tools/ imports) ─────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    macd_line   = (df["close"].ewm(span=MACD_FAST, adjust=False).mean()
                   - df["close"].ewm(span=MACD_SLOW, adjust=False).mean())
    signal_line = macd_line.ewm(span=MACD_SIGNAL_WIN, adjust=False).mean()
    df["macd_hist"] = macd_line - signal_line
    delta    = df["close"].diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    avg_loss = delta.clip(upper=0).abs().ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"]     = (100 - 100 / (1 + rs)).fillna(50.0)
    df["rel_vol"] = df["volume"] / df["volume"].rolling(REL_VOL_WINDOW).mean()
    return df


# ── Session gate ───────────────────────────────────────────────────────────────
def _in_session(ts: pd.Timestamp) -> bool:
    hm = (ts.hour, ts.minute)
    return (9, 45) <= hm <= (15, 45)


# ── Trade construction ─────────────────────────────────────────────────────────
def _make_trade(
    symbol: str,
    entry_px: float, exit_px: float,
    signal_ts: pd.Timestamp, entry_ts: pd.Timestamp, exit_ts: pd.Timestamp,
    bars_held: int, reason: str,
    rel_vol: float, rsi: float, macd_hist: float,
) -> dict:
    pnl_decimal = (exit_px - entry_px) / entry_px
    return {
        "symbol":              symbol,
        "signal_time":         str(signal_ts),
        "entry_time":          str(entry_ts),
        "exit_time":           str(exit_ts),
        "entry_price":         round(entry_px, 4),
        "exit_price":          round(exit_px, 4),
        "pnl_pct":             round(pnl_decimal * 100, 4),
        "R":                   round(pnl_decimal / STOP_LOSS_PCT, 4),
        "bars_held":           bars_held,
        "exit_reason":         reason,
        "rel_vol":             round(rel_vol, 4),
        "rsi_at_signal":       round(rsi, 2),
        "macd_hist_at_signal": round(macd_hist, 6),
    }


# ── Single-symbol simulation ───────────────────────────────────────────────────
def simulate_single(df: pd.DataFrame, symbol: str) -> list[dict]:
    """Base-case simulation (signal-bar close entry) for one symbol."""
    trades   = []
    hist     = df["macd_hist"]
    in_pos   = False
    entry_px = 0.0
    entry_i  = 0
    signal_ts = entry_ts = df.index[0]
    rv_sig = rsi_sig = mh_sig = 0.0

    for i in range(1, len(df)):
        ts      = df.index[i]
        row     = df.iloc[i]
        in_sess = _in_session(ts)

        if in_pos and not in_sess:
            trades.append(_make_trade(
                symbol, entry_px, float(df.iloc[i - 1]["close"]),
                signal_ts, entry_ts, df.index[i - 1],
                i - 1 - entry_i, "EOD",
                rv_sig, rsi_sig, mh_sig,
            ))
            in_pos = False
            continue

        if not in_sess:
            continue

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
                    symbol, entry_px, exit_px,
                    signal_ts, entry_ts, ts,
                    i - entry_i, reason,
                    rv_sig, rsi_sig, mh_sig,
                ))
                in_pos = False

        if in_pos:
            continue

        prev_h = hist.iloc[i - 1]
        curr_h = hist.iloc[i]
        if not (prev_h <= 0.0 < curr_h):
            continue

        rsi = float(row["rsi"])
        rv  = row["rel_vol"]
        if pd.isna(rv) or not (RSI_LOW <= rsi <= RSI_HIGH) or float(rv) < MIN_REL_VOL:
            continue

        rv_sig    = float(rv)
        rsi_sig   = rsi
        mh_sig    = float(curr_h)
        signal_ts = ts
        entry_ts  = ts
        entry_px  = float(row["close"])
        entry_i   = i
        in_pos    = True

    return trades


# ── Combined: global max 1 position ───────────────────────────────────────────
def simulate_combined_1pos(spy_df: pd.DataFrame, qqq_df: pd.DataFrame) -> list[dict]:
    """
    SPY+QQQ, global max 1 open position at a time (base_case).

    Tiebreaker when both symbols fire on the same bar:
      (a) Higher rel_vol wins.
      (b) If tied: RSI closer to 50 wins (smaller abs(rsi - 50)).
      (c) If still tied: SPY wins (incumbent symbol).
    """
    dfs = {"SPY": spy_df, "QQQ": qqq_df}
    common_idx = spy_df.index.intersection(qqq_df.index).sort_values()

    # Integer positions for each timestamp in each DataFrame — faster than per-call get_loc
    spy_locs = spy_df.index.get_indexer(common_idx)
    qqq_locs = qqq_df.index.get_indexer(common_idx)

    trades      = []
    in_pos      = False
    pos         = {}
    bars_in_pos = 0

    for ci in range(len(common_idx)):
        ts      = common_idx[ci]
        spy_i   = int(spy_locs[ci])
        qqq_i   = int(qqq_locs[ci])
        in_sess = _in_session(ts)

        # EOD exit at previous bar's close
        if in_pos and not in_sess:
            if ci > 0:
                sym     = pos["symbol"]
                df      = dfs[sym]
                prev_ts = common_idx[ci - 1]
                prev_i  = int(spy_locs[ci - 1]) if sym == "SPY" else int(qqq_locs[ci - 1])
                exit_px = float(df.iloc[prev_i]["close"])
            else:
                exit_px = pos["entry_px"]
                prev_ts = ts
            trades.append(_make_trade(
                pos["symbol"], pos["entry_px"], exit_px,
                pos["signal_ts"], pos["entry_ts"], prev_ts,
                bars_in_pos, "EOD",
                pos["rel_vol"], pos["rsi"], pos["macd_hist"],
            ))
            in_pos = False
            pos    = {}
            bars_in_pos = 0
            continue

        if not in_sess:
            continue

        # Exit check for open position
        if in_pos:
            sym  = pos["symbol"]
            df   = dfs[sym]
            loc  = spy_i if sym == "SPY" else qqq_i
            row  = df.iloc[loc]
            hist = df["macd_hist"]

            sl_level = pos["entry_px"] * (1 - STOP_LOSS_PCT)
            tp_level = pos["entry_px"] * (1 + TAKE_PROFIT_PCT)
            sl_hit   = float(row["low"])  <= sl_level
            tp_hit   = float(row["high"]) >= tp_level
            md_hit   = loc >= 1 and (hist.iloc[loc - 1] > 0.0 >= hist.iloc[loc])

            if sl_hit:
                reason, exit_px = "STOP_LOSS", sl_level
            elif tp_hit:
                reason, exit_px = "TAKE_PROFIT", tp_level
            elif md_hit:
                reason, exit_px = "MACD_DOWN", float(row["close"])
            else:
                reason = None

            bars_in_pos += 1

            if reason:
                trades.append(_make_trade(
                    pos["symbol"], pos["entry_px"], exit_px,
                    pos["signal_ts"], pos["entry_ts"], ts,
                    bars_in_pos, reason,
                    pos["rel_vol"], pos["rsi"], pos["macd_hist"],
                ))
                in_pos = False
                pos    = {}
                bars_in_pos = 0

        if in_pos:
            continue

        # Entry check: scan both symbols for qualifying signals
        candidates = []
        for sym, loc in (("SPY", spy_i), ("QQQ", qqq_i)):
            if loc < 1:
                continue
            df   = dfs[sym]
            hist = df["macd_hist"]
            prev_h = hist.iloc[loc - 1]
            curr_h = hist.iloc[loc]
            if not (prev_h <= 0.0 < curr_h):
                continue
            row = df.iloc[loc]
            rsi = float(row["rsi"])
            rv  = row["rel_vol"]
            if pd.isna(rv) or not (RSI_LOW <= rsi <= RSI_HIGH) or float(rv) < MIN_REL_VOL:
                continue
            candidates.append({
                "symbol":    sym,
                "rel_vol":   float(rv),
                "rsi":       rsi,
                "macd_hist": float(curr_h),
                "entry_px":  float(row["close"]),
            })

        if not candidates:
            continue

        # Tiebreaker: (a) higher rel_vol; (b) RSI closer to 50; (c) SPY preferred
        def _rank(c: dict) -> tuple:
            spy_bias = 0 if c["symbol"] == "SPY" else 1
            return (-c["rel_vol"], abs(c["rsi"] - 50.0), spy_bias)

        winner  = min(candidates, key=_rank)
        pos     = {
            "symbol":    winner["symbol"],
            "entry_px":  winner["entry_px"],
            "signal_ts": ts,
            "entry_ts":  ts,
            "rel_vol":   winner["rel_vol"],
            "rsi":       winner["rsi"],
            "macd_hist": winner["macd_hist"],
        }
        in_pos      = True
        bars_in_pos = 0

    return trades


# ── Combined: 1 position per symbol (higher risk) ─────────────────────────────
def simulate_combined_1pos_per_symbol(
    spy_df: pd.DataFrame, qqq_df: pd.DataFrame
) -> list[dict]:
    """
    1 independent position per symbol (up to 2 simultaneous open positions).
    HIGHER RISK / INFORMATIONAL ONLY — capital at risk can double.
    Implemented by running two independent single-symbol simulations and merging.
    """
    spy_trades = simulate_single(spy_df, "SPY")
    qqq_trades = simulate_single(qqq_df, "QQQ")
    return sorted(spy_trades + qqq_trades, key=lambda t: t["signal_time"])


# ── Signal overlap (OOS period, unconstrained) ─────────────────────────────────
def compute_signal_overlap(
    spy_df: pd.DataFrame, qqq_df: pd.DataFrame, oos_ps_set: set
) -> dict:
    """Count bars where SPY and/or QQQ fire an entry signal, over the OOS period only."""
    def _signal_timestamps(df: pd.DataFrame) -> set:
        result = set()
        hist   = df["macd_hist"]
        for i in range(1, len(df)):
            ts = df.index[i]
            if str(ts.to_period("M")) not in oos_ps_set:
                continue
            if not _in_session(ts):
                continue
            if not (hist.iloc[i - 1] <= 0.0 < hist.iloc[i]):
                continue
            row = df.iloc[i]
            rsi = float(row["rsi"])
            rv  = row["rel_vol"]
            if pd.isna(rv) or not (RSI_LOW <= rsi <= RSI_HIGH) or float(rv) < MIN_REL_VOL:
                continue
            result.add(ts)
        return result

    spy_sigs = _signal_timestamps(spy_df)
    qqq_sigs = _signal_timestamps(qqq_df)
    overlap  = spy_sigs & qqq_sigs

    return {
        "spy_signal_bars":     len(spy_sigs),
        "qqq_signal_bars":     len(qqq_sigs),
        "overlap_bars":        len(overlap),
        "overlap_pct_of_spy":  round(100.0 * len(overlap) / max(len(spy_sigs), 1), 1),
        "overlap_pct_of_qqq":  round(100.0 * len(overlap) / max(len(qqq_sigs), 1), 1),
    }


# ── Metrics ────────────────────────────────────────────────────────────────────
_EMPTY_REASONS = {"MACD_DOWN": 0, "STOP_LOSS": 0, "TAKE_PROFIT": 0, "EOD": 0}


def compute_metrics(trades: list[dict], oos_trading_days: float = 0.0) -> dict:
    if not trades:
        return {
            "n_trades": 0, "win_rate_pct": None,
            "avg_ret_pct": None, "median_ret_pct": None,
            "avg_R": None, "median_R": None,
            "total_ret_pct": None, "max_dd_pct": None,
            "sharpe": None, "trades_per_week": None,
            "exit_reasons": dict(_EMPTY_REASONS),
        }

    r_dec  = pd.Series([t["pnl_pct"] / 100 for t in trades])
    R_vals = pd.Series([t["R"] for t in trades])
    equity = (1 + r_dec).cumprod()
    dd     = float((equity / equity.cummax() - 1).min())
    std_R  = float(R_vals.std())
    sharpe = round(float(R_vals.mean()) / std_R, 3) if std_R > 0 else None

    tpw = None
    if oos_trading_days > 0:
        weeks = oos_trading_days / 5.0
        tpw   = round(len(trades) / weeks, 2) if weeks > 0 else None

    reasons = dict(_EMPTY_REASONS)
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1

    return {
        "n_trades":        len(trades),
        "win_rate_pct":    round(float((r_dec > 0).mean() * 100), 1),
        "avg_ret_pct":     round(float(r_dec.mean() * 100), 3),
        "median_ret_pct":  round(float(r_dec.median() * 100), 3),
        "avg_R":           round(float(R_vals.mean()), 3),
        "median_R":        round(float(R_vals.median()), 3),
        "total_ret_pct":   round(float((equity.iloc[-1] - 1) * 100), 2),
        "max_dd_pct":      round(dd * 100, 2),
        "sharpe":          sharpe,
        "trades_per_week": tpw,
        "exit_reasons":    reasons,
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


def compute_fold_metrics(trades: list[dict], oos_ps) -> list[dict]:
    folds = []
    for p in oos_ps:
        p_str       = str(p)
        fold_trades = slice_by_period(trades, p_str)
        m           = compute_metrics(fold_trades)
        m["fold"]   = p_str
        folds.append(m)
    return folds


def fold_consistency(oos_folds: list[dict]) -> str:
    with_trades = [f for f in oos_folds if (f["n_trades"] or 0) > 0]
    profitable  = [f for f in with_trades if (f["total_ret_pct"] or 0) > 0]
    return f"{len(profitable)}/{len(with_trades)}"


# ── Markdown ───────────────────────────────────────────────────────────────────
def generate_markdown(
    scenario_results: dict,
    overlap: dict,
    oos_ps,
    feed: str,
    run_date: str,
) -> str:
    def _v(v, fmt="{}", na="—"):
        return na if v is None else fmt.format(v)

    sc  = scenario_results
    n_folds = len(oos_ps)

    def _row(label: str, key: str, note: str = "") -> str:
        m  = sc[key]["oos_agg"]
        fc = sc[key]["fold_consistency"]
        er = m["exit_reasons"]
        exits = f"MD={er['MACD_DOWN']} SL={er['STOP_LOSS']} TP={er['TAKE_PROFIT']} EOD={er['EOD']}"
        lc  = " \\*" if (m["n_trades"] or 0) > 0 and (m["n_trades"] or 0) < 30 else ""
        sfx = f" {note}" if note else ""
        return (
            f"| {label}{lc}{sfx} | {m['n_trades'] or 0} "
            f"| {_v(m['trades_per_week'], '{:.2f}')} "
            f"| {_v(m['win_rate_pct'], '{:.1f}%')} "
            f"| {_v(m['avg_R'], '{:.3f}')} "
            f"| {_v(m['total_ret_pct'], '{:.2f}%')} "
            f"| {_v(m['max_dd_pct'], '{:.2f}%')} "
            f"| {_v(m['sharpe'], '{:.3f}')} "
            f"| {fc} "
            f"| {exits} |"
        )

    main_hdr = (
        "| Scenario | N (OOS) | Tr/Wk | Win% | AvgR | TotRet% | MaxDD% | Sharpe | "
        "Prof. Months | Exits |\n"
        "|----------|---------|-------|------|------|---------|--------|--------|"
        "--------------|-------|"
    )
    main_table = "\n".join([
        _row("SPY only",                  "spy_only"),
        _row("QQQ only",                  "qqq_only"),
        _row("Combined — 1 pos total",    "combined_1pos"),
        _row("Combined — 1 per symbol",   "combined_1pos_per_symbol", "⚠ HIGHER RISK"),
    ])

    def _fold_section(title: str, key: str) -> str:
        folds = sc[key]["oos_folds"]
        hdr   = ("| Fold | N | Win% | AvgR | TotRet% | MaxDD% |\n"
                 "|------|---|------|------|---------|--------|")
        rows  = []
        for f in folds:
            n = f["n_trades"] or 0
            if n == 0:
                rows.append(f"| {f['fold']} | 0 | — | — | — | — |")
            else:
                rows.append(
                    f"| {f['fold']} | {n} "
                    f"| {_v(f['win_rate_pct'], '{:.1f}%')} "
                    f"| {_v(f['avg_R'], '{:.3f}')} "
                    f"| {_v(f['total_ret_pct'], '{:.2f}%')} "
                    f"| {_v(f['max_dd_pct'], '{:.2f}%')} |"
                )
        return f"#### {title}\n\n{hdr}\n" + "\n".join(rows)

    fold_sections = "\n\n".join([
        _fold_section("SPY only",                                          "spy_only"),
        _fold_section("QQQ only",                                          "qqq_only"),
        _fold_section("Combined — 1 pos total",                            "combined_1pos"),
        _fold_section("Combined — 1 per symbol (HIGHER RISK / INFORMATIONAL ONLY)",
                      "combined_1pos_per_symbol"),
    ])

    # Auto-recommendation: judge combined_1pos vs spy_only only
    spy_m = sc["spy_only"]["oos_agg"]
    c1_m  = sc["combined_1pos"]["oos_agg"]

    def _parse_fc(fc_str: str) -> float:
        try:
            num, den = fc_str.split("/")
            return int(num) / max(int(den), 1)
        except Exception:
            return 0.0

    spy_tpw = spy_m["trades_per_week"] or 0.0
    c1_tpw  = c1_m["trades_per_week"]  or 0.0
    spy_avgR = spy_m["avg_R"]    or 0.0
    c1_avgR  = c1_m["avg_R"]     or 0.0
    spy_dd   = spy_m["max_dd_pct"] or 0.0
    c1_dd    = c1_m["max_dd_pct"]  or 0.0
    spy_fc   = _parse_fc(sc["spy_only"]["fold_consistency"])
    c1_fc    = _parse_fc(sc["combined_1pos"]["fold_consistency"])

    checks = {
        "Frequency (trades/week)":
            (c1_tpw > spy_tpw,   f"{spy_tpw:.2f} → {c1_tpw:.2f}"),
        "Avg R":
            (c1_avgR - spy_avgR > -0.10, f"{spy_avgR:.3f} → {c1_avgR:.3f}"),
        "Max DD":
            (c1_dd - spy_dd > -1.5,      f"{spy_dd:.2f}% → {c1_dd:.2f}%"),
        "Fold consistency":
            (c1_fc >= spy_fc - 0.10,     f"{spy_fc:.0%} → {c1_fc:.0%}"),
    }
    check_lines  = "\n".join(
        f"- {'✓' if ok else '✗'} **{k}:** {vals}"
        for k, (ok, vals) in checks.items()
    )
    all_pass = all(ok for ok, _ in checks.values())
    if all_pass:
        verdict = (
            "**QQQ addition via `combined_1pos` passes all four criteria.** "
            "This is a candidate for user review. Verify per-fold consistency before proceeding. "
            "Do not update `.env` or `config.py` without explicit user approval."
        )
    else:
        failed = [k for k, (ok, _) in checks.items() if not ok]
        verdict = (
            f"**QQQ addition does NOT pass all four criteria.** "
            f"Failed: {', '.join(failed)}. "
            "SPY-only remains the recommended configuration. "
            "Do not add QQQ to production."
        )

    spy_qqq_m   = sc["qqq_only"]["oos_agg"]
    c1ps_m      = sc["combined_1pos_per_symbol"]["oos_agg"]
    spy_n       = spy_m["n_trades"] or 0
    qqq_n       = spy_qqq_m["n_trades"] or 0
    c1_n        = c1_m["n_trades"] or 0
    c1ps_n      = c1ps_m["n_trades"] or 0

    return f"""\
# Multi-Symbol Candidate Study — SPY + QQQ
**Date:** {run_date[:10]}
**Analyst:** Claude (research layer)
**Status:** Complete — awaiting user review
**Approval required before any production change.**

---

> **Research only. No production change approved.**
> Judgment is based solely on `combined_1pos` vs `spy_only`.
> `combined_1pos_per_symbol` is informational only and is NOT a production candidate.

---

## Research Question

Can adding QQQ as a second symbol increase trade frequency (trades/week) without reducing
average R or worsening max drawdown quality compared to SPY-only, using the current
production parameters (MACD(12,26,9), RSI 35–65, MIN_REL_VOL 1.2, SL 2%, TP 4%)?

---

## Method

- Script: `research/backtest/multisymbol_candidate_study.py`
- Results: `research/backtest/multisymbol_candidate_results.json`
- Instruments: SPY, QQQ — {BAR_MINUTES}-min bars, {feed.upper()} feed
- History: {HISTORY_MONTHS} months — walk-forward OOS after {WARMUP_MONTHS}-month warmup ({n_folds} OOS folds)
- Indicators: MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL_WIN}), RSI({RSI_PERIOD}), rel_vol {REL_VOL_WINDOW}-bar
  rolling mean — computed independently per symbol (no shared state)
- Entry: strict 1-bar MACD histogram crossover + RSI in [{RSI_LOW:.0f}, {RSI_HIGH:.0f}] + rel_vol ≥ {MIN_REL_VOL}
- Exit: MACD crossunder | SL -{STOP_LOSS_PCT*100:.0f}% | TP +{TAKE_PROFIT_PCT*100:.0f}% | EOD 15:45 ET
- Intrabar SL+TP conflict: STOP_LOSS assumed hit first (conservative)
- Execution: base_case only (signal-bar close entry)

**Scenario definitions:**
1. **spy_only** — SPY with production parameters, independent simulation
2. **qqq_only** — QQQ with production parameters, independent simulation
3. **combined_1pos** — global max 1 open position. When both signal simultaneously:
   (a) higher rel_vol wins; (b) if tied, RSI closer to 50 wins; (c) if still tied, SPY wins.
   Same capital at risk as SPY-only.
4. **combined_1pos_per_symbol** — 1 independent position per symbol, up to 2 simultaneous.
   **HIGHER RISK / INFORMATIONAL ONLY.** Capital at risk can double.

\\* = fewer than 30 OOS trades — low confidence
Tr/Wk = avg trades per week over OOS period
Prof. Months = profitable OOS months / OOS months with ≥1 trade

---

## Results

### OOS Aggregate

{main_hdr}
{main_table}

---

## Per-Fold OOS Breakdown

{fold_sections}

---

## Signal Overlap (OOS Period — Unconstrained)

| Metric | Value |
|--------|-------|
| SPY qualifying signal bars | {overlap['spy_signal_bars']} |
| QQQ qualifying signal bars | {overlap['qqq_signal_bars']} |
| Simultaneous overlap bars | {overlap['overlap_bars']} |
| Overlap as % of SPY signals | {overlap['overlap_pct_of_spy']}% |
| Overlap as % of QQQ signals | {overlap['overlap_pct_of_qqq']}% |

High overlap (>50%) means SPY and QQQ often signal together — incremental frequency
benefit in `combined_1pos` is limited by position conflicts. Low overlap means signals
are more independent and frequency gains are real.

---

## Key Findings

- **SPY standalone:** {spy_n} OOS trades, {_v(spy_m['trades_per_week'], '{:.2f}')} tr/wk, avg R {_v(spy_m['avg_R'], '{:.3f}')}, max DD {_v(spy_m['max_dd_pct'], '{:.2f}%')}, {sc['spy_only']['fold_consistency']} profitable months
- **QQQ standalone:** {qqq_n} OOS trades, {_v(spy_qqq_m['trades_per_week'], '{:.2f}')} tr/wk, avg R {_v(spy_qqq_m['avg_R'], '{:.3f}')}, max DD {_v(spy_qqq_m['max_dd_pct'], '{:.2f}%')}, {sc['qqq_only']['fold_consistency']} profitable months
- **Combined (1 pos total):** {c1_n} OOS trades, {_v(c1_m['trades_per_week'], '{:.2f}')} tr/wk, avg R {_v(c1_m['avg_R'], '{:.3f}')}, max DD {_v(c1_m['max_dd_pct'], '{:.2f}%')}, {sc['combined_1pos']['fold_consistency']} profitable months
- **Signal overlap:** {overlap['overlap_bars']} simultaneous bars ({overlap['overlap_pct_of_spy']}% of SPY signals, {overlap['overlap_pct_of_qqq']}% of QQQ) — indicates how often position conflicts arise
- **Higher-risk reference:** combined_1pos_per_symbol shows {c1ps_n} OOS trades ({_v(c1ps_m['trades_per_week'], '{:.2f}')} tr/wk) at the cost of potentially double capital at risk

---

## Recommendation

### combined_1pos vs spy_only — four criteria

{check_lines}

{verdict}

> `combined_1pos_per_symbol` is **HIGHER RISK / INFORMATIONAL ONLY.**
> It doubles maximum capital at risk and must not be recommended as the next production step.

---

## Caveats

- IEX volume data may differ from SIP; rel_vol thresholds calibrated on IEX may not transfer identically to SIP
- SPY and QQQ are highly correlated (~0.95+); combined drawdowns may be worse than the per-scenario max DD figures suggest, particularly in `combined_1pos_per_symbol`
- Backtest does not model slippage, fees, or partial fills
- Paper trading PnL does not predict live PnL
- Walk-forward avoids look-ahead bias but does not guarantee future performance
- Any scenario with OOS n<30 should not drive a production change
- The tiebreaker rule (rel_vol → RSI proximity to 50 → SPY) is a deterministic heuristic; its optimality has not been separately validated

---

**User reviewed — no production change approved.**
"""


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"\nMulti-Symbol Candidate Study — SPY+QQQ {BAR_MINUTES}m bars")
    print("Fetching history ...")
    spy_raw = fetch_bars("SPY")
    qqq_raw = fetch_bars("QQQ")
    feed    = os.environ.get("ALPACA_DATA_FEED", "iex")

    spy_df = add_indicators(spy_raw)
    qqq_df = add_indicators(qqq_raw)

    oos_ps     = oos_periods(spy_df)
    oos_ps_set = set(str(p) for p in oos_ps)
    oos_td     = oos_trading_days_count(spy_df, oos_ps_set)
    print(f"  OOS folds: {len(oos_ps)}  ({oos_ps[0]} -> {oos_ps[-1]})  |  {oos_td:.0f} OOS trading days\n")

    print("Running simulations ...")
    all_trades: dict[str, list[dict]] = {
        "spy_only":                 simulate_single(spy_df, "SPY"),
        "qqq_only":                 simulate_single(qqq_df, "QQQ"),
        "combined_1pos":            simulate_combined_1pos(spy_df, qqq_df),
        "combined_1pos_per_symbol": simulate_combined_1pos_per_symbol(spy_df, qqq_df),
    }

    print("\nComputing signal overlap ...")
    overlap = compute_signal_overlap(spy_df, qqq_df, oos_ps_set)
    print(f"  SPY={overlap['spy_signal_bars']}  QQQ={overlap['qqq_signal_bars']}  "
          f"overlap={overlap['overlap_bars']}  "
          f"({overlap['overlap_pct_of_spy']}% of SPY, {overlap['overlap_pct_of_qqq']}% of QQQ)")

    print("\nOOS metrics:")
    scenario_results: dict[str, dict] = {}
    for sc, trades in all_trades.items():
        oos_trades = [
            t for t in trades
            if str(pd.Timestamp(t["signal_time"]).to_period("M")) in oos_ps_set
        ]
        oos_agg   = compute_metrics(oos_trades, oos_trading_days=oos_td)
        oos_folds = compute_fold_metrics(oos_trades, oos_ps)
        fc        = fold_consistency(oos_folds)
        scenario_results[sc] = {
            "oos_agg":          oos_agg,
            "oos_folds":        oos_folds,
            "fold_consistency": fc,
            "trades":           trades,
        }
        n    = oos_agg["n_trades"] or 0
        tpw  = oos_agg["trades_per_week"]
        avgR = oos_agg["avg_R"]
        dd   = oos_agg["max_dd_pct"]
        print(f"  {sc:<28} n={n:>3}  tr/wk={tpw or '—':>5}  avgR={avgR or '—':>6}  "
              f"maxDD={dd or '—':>7}%  fc={fc}")

    run_date = datetime.datetime.now().isoformat()

    # Save JSON
    output = {
        "generated_at":    run_date,
        "bar_minutes":     BAR_MINUTES,
        "data_feed":       feed,
        "history_months":  HISTORY_MONTHS,
        "warmup_months":   WARMUP_MONTHS,
        "oos_folds":       [str(p) for p in oos_ps],
        "oos_trading_days": oos_td,
        "fixed_params": {
            "MACD":                [MACD_FAST, MACD_SLOW, MACD_SIGNAL_WIN],
            "RSI_LOW":             RSI_LOW,
            "RSI_HIGH":            RSI_HIGH,
            "MIN_RELATIVE_VOLUME": MIN_REL_VOL,
            "STOP_LOSS_PCT":       STOP_LOSS_PCT,
            "TAKE_PROFIT_PCT":     TAKE_PROFIT_PCT,
        },
        "scenarios": {
            sc: {
                "oos_agg":          r["oos_agg"],
                "oos_folds":        r["oos_folds"],
                "fold_consistency": r["fold_consistency"],
                "trades":           r["trades"],
            }
            for sc, r in scenario_results.items()
        },
        "signal_overlap": overlap,
    }

    out_json = ROOT / "research" / "backtest" / "multisymbol_candidate_results.json"
    out_json.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n  JSON  -> research/backtest/multisymbol_candidate_results.json")

    # Save markdown
    md_text = generate_markdown(scenario_results, overlap, oos_ps, feed, run_date)
    out_md  = ROOT / "research" / "notebooks" / "2026-05-01_multisymbol_candidate_study.md"
    out_md.write_text(md_text, encoding="utf-8")
    print(f"  Note  -> research/notebooks/2026-05-01_multisymbol_candidate_study.md\n")


if __name__ == "__main__":
    main()
