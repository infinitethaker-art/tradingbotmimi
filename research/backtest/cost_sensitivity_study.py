"""
Cost-Aware Backtest + Skill Test — DEPLOYED config (vol=1.1, RSI_HIGH=70)
=========================================================================
Research only. No production change. ISOLATION RULE: no imports from tools/ or scheduler/.

Answers the question parameter sweeps cannot: does this strategy have EDGE once you
pay realistic costs, and does its signal beat RANDOM entry with the same structure?

Three tests, all on the deployed config (vol=1.1, RSI_HIGH=70), SPY 15-min IEX,
walk-forward OOS (3-month warmup, monthly folds):

  1. Slippage sweep   — net OOS metrics at 0/1/2/5 bps per side (Alpaca equity commission = $0).
  2. Buy-and-hold     — SPY over the same OOS window, for context (exposure differs hugely).
  3. Random-entry     — N simulations: same one-position-at-a-time structure, same SL/TP/EOD
                        exits, same cost, but entries fired at RANDOM in-session bars at a rate
                        matched to the strategy. If the strategy isn't clearly above the random
                        distribution, the signal has no demonstrable skill.

Run from project root:
    python research/backtest/cost_sensitivity_study.py
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

# ── Fixed params — mirror DEPLOYED production (do NOT import config.py) ─────────
SYMBOL          = "SPY"
BAR_MINUTES     = 15
MACD_FAST, MACD_SLOW, MACD_SIGNAL_WIN = 12, 26, 9
RSI_PERIOD      = 14
RSI_LOW         = 35.0
RSI_HIGH        = 70.0      # DEPLOYED
MIN_REL_VOL     = 1.1       # DEPLOYED
STOP_LOSS_PCT   = 0.02
TAKE_PROFIT_PCT = 0.04
REL_VOL_WINDOW  = 20
HISTORY_MONTHS  = 13
WARMUP_MONTHS   = 3

COST_LEVELS_BPS = [0.0, 1.0, 2.0, 5.0]   # per side; round-trip = 2x
N_RANDOM_SIMS   = 500
RANDOM_SEED     = 42


def fetch_history() -> pd.DataFrame:
    client = StockHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"], secret_key=os.environ["ALPACA_SECRET_KEY"],
    )
    feed  = os.environ.get("ALPACA_DATA_FEED", "iex")
    end   = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=HISTORY_MONTHS * 32)
    resp = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=SYMBOL,
        timeframe=TimeFrame(BAR_MINUTES, TimeFrameUnit.Minute),
        start=start, end=end, feed=feed, adjustment="raw",
    ))
    bars = resp.data.get(SYMBOL, [])
    if not bars:
        raise RuntimeError(f"No bars for {SYMBOL} (feed={feed}).")
    df = pd.DataFrame(
        [{"open": float(b.open), "high": float(b.high), "low": float(b.low),
          "close": float(b.close), "volume": int(b.volume)} for b in bars],
        index=pd.DatetimeIndex([b.timestamp for b in bars], name="timestamp"),
    ).sort_index()
    df.index = df.index.tz_convert("America/New_York")
    print(f"  {len(df):,} bars | {df.index[0].date()} -> {df.index[-1].date()} | feed={feed}")
    return df, feed


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    macd = (df["close"].ewm(span=MACD_FAST, adjust=False).mean()
            - df["close"].ewm(span=MACD_SLOW, adjust=False).mean())
    df["macd_hist"] = macd - macd.ewm(span=MACD_SIGNAL_WIN, adjust=False).mean()
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    loss  = delta.clip(upper=0).abs().ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(50.0)
    df["rel_vol"] = df["volume"] / df["volume"].rolling(REL_VOL_WINDOW).mean()
    return df


def _in_session_mask(idx: pd.DatetimeIndex) -> np.ndarray:
    hm = np.asarray(idx.hour) * 60 + np.asarray(idx.minute)
    return (hm >= 9 * 60 + 45) & (hm <= 15 * 60 + 45)


def simulate(entry_ok: np.ndarray, close, high, low, hist, in_sess) -> list[tuple[int, float]]:
    """One-position-at-a-time. Enter at close[i] when flat, in-session, entry_ok[i].
    Exit: SL -2% | TP +4% | MACD crossunder | EOD (exit at prior close). Returns (entry_idx, gross_ret)."""
    n = len(close)
    trades, in_pos, entry_px, entry_i = [], False, 0.0, 0
    for i in range(1, n):
        if in_pos and not in_sess[i]:
            trades.append((entry_i, (close[i - 1] - entry_px) / entry_px)); in_pos = False; continue
        if not in_sess[i]:
            continue
        if in_pos:
            sl, tp = entry_px * (1 - STOP_LOSS_PCT), entry_px * (1 + TAKE_PROFIT_PCT)
            if low[i] <= sl:        trades.append((entry_i, -STOP_LOSS_PCT));       in_pos = False
            elif high[i] >= tp:     trades.append((entry_i, TAKE_PROFIT_PCT));      in_pos = False
            elif hist[i - 1] > 0.0 >= hist[i]:
                trades.append((entry_i, (close[i] - entry_px) / entry_px));        in_pos = False
        if in_pos:
            continue
        if entry_ok[i]:
            entry_px, entry_i, in_pos = close[i], i, True
    return trades


def metrics(net_rets: np.ndarray) -> dict:
    if len(net_rets) == 0:
        return {"n": 0, "win_pct": None, "avg_R": None, "sharpe": None,
                "total_ret_pct": None, "max_dd_pct": None}
    R = net_rets / STOP_LOSS_PCT
    eq = np.cumprod(1 + net_rets)
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    sd = float(R.std())
    return {
        "n": int(len(net_rets)),
        "win_pct": round(float((net_rets > 0).mean() * 100), 1),
        "avg_R": round(float(R.mean()), 4),
        "sharpe": round(float(R.mean()) / sd, 3) if sd > 0 else None,
        "total_ret_pct": round(float((eq[-1] - 1) * 100), 2),
        "max_dd_pct": round(dd * 100, 2),
    }


def main() -> None:
    print("\nCost-Aware Backtest -- DEPLOYED config vol=1.1 RSI<=70 | SPY 15m")
    df, feed = fetch_history()
    df = add_indicators(df)

    close = df["close"].to_numpy(); high = df["high"].to_numpy()
    low = df["low"].to_numpy(); hist = df["macd_hist"].to_numpy()
    rsi = df["rsi"].to_numpy(); rel_vol = df["rel_vol"].to_numpy()
    in_sess = _in_session_mask(df.index)
    periods = df.index.to_period("M")
    oos_set = set(str(p) for p in periods.unique().sort_values()[WARMUP_MONTHS:])
    oos_bar = np.array([str(p) in oos_set for p in periods])
    print(f"  OOS folds: {len(oos_set)} | OOS in-session bars: {int((oos_bar & in_sess).sum()):,}")

    # ── Strategy entries ────────────────────────────────────────────────────────
    prev_hist = np.concatenate([[np.nan], hist[:-1]])
    strat_entry = (
        (prev_hist <= 0) & (hist > 0)
        & (rsi >= RSI_LOW) & (rsi <= RSI_HIGH)
        & (~np.isnan(rel_vol)) & (rel_vol >= MIN_REL_VOL)
        & in_sess
    )
    strat_trades = simulate(strat_entry, close, high, low, hist, in_sess)
    strat_oos = np.array([gr for (ei, gr) in strat_trades if oos_bar[ei]])
    print(f"  Strategy OOS trades: {len(strat_oos)}")

    # ── 1. Slippage sweep (strategy) ─────────────────────────────────────────────
    sweep = {}
    for c in COST_LEVELS_BPS:
        rt = 2 * c / 1e4
        sweep[c] = metrics(strat_oos - rt)
    # breakeven cost per side where avg net return crosses 0
    gross_mean = float(strat_oos.mean()) if len(strat_oos) else 0.0
    breakeven_bps_per_side = round(gross_mean / 2 * 1e4, 2)

    # ── 2. Buy & hold over OOS window ────────────────────────────────────────────
    oos_close = close[oos_bar]
    bh_total = round((oos_close[-1] / oos_close[0] - 1) * 100, 2)
    daily = df["close"][oos_bar].resample("1D").last().dropna()
    dret = daily.pct_change().dropna()
    bh_eq = (1 + dret).cumprod()
    bh_dd = round(float((bh_eq / bh_eq.cummax() - 1).min()) * 100, 2)

    # ── 3. Random-entry skill test (matched rate, same exits, cost = 2 bps/side) ──
    rng = np.random.default_rng(RANDOM_SEED)
    eligible = int((oos_bar & in_sess).sum())
    # probability per eligible bar tuned to match strategy OOS trade count
    p_entry = min(1.0, len(strat_oos) / max(eligible, 1)) if eligible else 0.0
    HEAD_COST = 2.0
    rt2 = 2 * HEAD_COST / 1e4
    rand_totals, rand_avgR, rand_sharpe, rand_n = [], [], [], []
    sess_idx = in_sess  # entries only allowed in session (and we OOS-filter after)
    for _ in range(N_RANDOM_SIMS):
        draw = rng.random(len(close)) < p_entry
        rand_entry = draw & sess_idx
        rtr = simulate(rand_entry, close, high, low, hist, in_sess)
        oos_r = np.array([gr for (ei, gr) in rtr if oos_bar[ei]])
        if len(oos_r) == 0:
            continue
        m = metrics(oos_r - rt2)
        rand_totals.append(m["total_ret_pct"]); rand_avgR.append(m["avg_R"])
        if m["sharpe"] is not None:
            rand_sharpe.append(m["sharpe"])
        rand_n.append(m["n"])
    rand_totals = np.array(rand_totals)
    strat_net2 = metrics(strat_oos - rt2)
    # percentile of the strategy within the random total-return distribution
    pctile = round(float((rand_totals < strat_net2["total_ret_pct"]).mean() * 100), 1)

    # ── Print summary ────────────────────────────────────────────────────────────
    print("\n  SLIPPAGE SWEEP (strategy, OOS) -- Alpaca equity commission = $0")
    print(f"  {'cost/side':>10} {'net avgR':>9} {'sharpe':>7} {'totRet%':>8} {'win%':>6} {'maxDD%':>7}")
    for c in COST_LEVELS_BPS:
        m = sweep[c]
        print(f"  {c:>8.0f}bp {str(m['avg_R']):>9} {str(m['sharpe']):>7} "
              f"{str(m['total_ret_pct']):>8} {str(m['win_pct']):>6} {str(m['max_dd_pct']):>7}")
    print(f"\n  Breakeven slippage = {breakeven_bps_per_side} bps/side "
          f"(gross avg trade = {gross_mean*100:.4f}%); above this the OOS edge is negative.")
    print(f"\n  BUY & HOLD SPY (OOS window): total {bh_total}%  maxDD {bh_dd}%  "
          f"(full exposure vs strategy's brief intraday exposure)")
    print(f"\n  RANDOM-ENTRY SKILL TEST  (cost {HEAD_COST}bp/side, {len(rand_totals)} sims, "
          f"~{int(np.mean(rand_n))} trades/sim)")
    print(f"    random totRet%  : mean {rand_totals.mean():.2f}  "
          f"p10 {np.percentile(rand_totals,10):.2f}  p50 {np.percentile(rand_totals,50):.2f}  "
          f"p90 {np.percentile(rand_totals,90):.2f}")
    print(f"    strategy totRet%: {strat_net2['total_ret_pct']}  (avgR {strat_net2['avg_R']}, "
          f"sharpe {strat_net2['sharpe']})")
    print(f"    => strategy beats {pctile}% of random-entry sims")

    out = {
        "run_date": datetime.datetime.now().isoformat(), "symbol": SYMBOL, "feed": feed,
        "config": {"min_rel_vol": MIN_REL_VOL, "rsi_high": RSI_HIGH,
                   "stop_loss_pct": STOP_LOSS_PCT, "take_profit_pct": TAKE_PROFIT_PCT},
        "oos_folds": sorted(oos_set), "commission": "0 (Alpaca equities)",
        "slippage_sweep_bps_per_side": {str(c): sweep[c] for c in COST_LEVELS_BPS},
        "breakeven_bps_per_side": breakeven_bps_per_side,
        "buy_and_hold_oos": {"total_ret_pct": bh_total, "max_dd_pct": bh_dd},
        "random_entry": {
            "cost_bps_per_side": HEAD_COST, "n_sims": len(rand_totals),
            "mean_trades": int(np.mean(rand_n)) if rand_n else 0,
            "totRet_mean": round(float(rand_totals.mean()), 2),
            "totRet_p10": round(float(np.percentile(rand_totals, 10)), 2),
            "totRet_p50": round(float(np.percentile(rand_totals, 50)), 2),
            "totRet_p90": round(float(np.percentile(rand_totals, 90)), 2),
            "strategy_totRet": strat_net2["total_ret_pct"],
            "strategy_avgR": strat_net2["avg_R"], "strategy_sharpe": strat_net2["sharpe"],
            "strategy_pctile_vs_random": pctile,
        },
    }
    outp = ROOT / "research" / "backtest" / "cost_sensitivity_results.json"
    outp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n  Results saved -> research/backtest/cost_sensitivity_results.json\n")


if __name__ == "__main__":
    main()
