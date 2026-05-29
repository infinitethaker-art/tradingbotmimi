# Cost-Aware Backtest + Skill Test — Does the strategy have edge?

**Date:** 2026-05-29
**Analyst:** Claude (research layer)
**Status:** Complete — awaiting user review
**Feed:** IEX · **Instrument:** SPY (research proxy) · **Config tested:** DEPLOYED `vol=1.1 / RSI≤70`

---

> **Research only. No production change.** This is the test parameter sweeps can't do:
> it asks whether the strategy makes money *after costs* and whether its signal beats *luck*.

---

## Question

Parameter tuning is settled and second-order (see `2026-05-29_parameter_validation.md`).
The real question: with realistic transaction costs, does the deployed strategy have **edge**,
and does its entry signal show **skill** versus random entry?

## Method

- Script: `research/backtest/cost_sensitivity_study.py` · Results: `cost_sensitivity_results.json`
- SPY 15-min IEX, 13 months, **11 walk-forward OOS folds**, deployed config `1.1/70`.
- **Commission $0** (Alpaca equities). **Slippage swept** at 0/1/2/5 bps per side (round-trip = 2×).
- **Buy-and-hold** SPY over the same OOS window (context).
- **Random-entry skill test:** 500 sims, identical structure (one position at a time, same SL/TP/MACD/EOD exits, same 2 bps/side cost) but entries fired at *random* in-session bars at a rate matched to the strategy (~66 trades/sim).

## Results

### 1. Slippage sweep (strategy, OOS)
| Cost/side | net avg R | Sharpe | TotRet% | Win% | MaxDD% |
|-----------|-----------|--------|---------|------|--------|
| 0 bp | 0.030 | 0.223 | +4.47 | 48.6 | −1.37 |
| 1 bp | 0.020 | 0.148 | +2.94 | 47.3 | −1.47 |
| 2 bp | 0.010 | 0.073 | +1.42 | 45.9 | −1.57 |
| 5 bp | −0.020 | −0.152 | −2.98 | 39.2 | −4.32 |

**Breakeven ≈ 2.97 bps/side.** Gross avg trade is only **+0.0595%** — so a few basis points of slippage erases most of it, and ~3 bps/side flips the OOS edge negative.

### 2. Buy-and-hold benchmark
SPY over the OOS window: **+22.62%**, max drawdown −9.26%. The strategy returned **~+1.4%** (at 2 bps) with −1.6% drawdown. B&H wins absolute return ~16×; the strategy only "wins" on drawdown/exposure (it's in the market a few hours a week, never overnight).

### 3. Random-entry skill test (2 bps/side)
| | TotRet% |
|---|---|
| Random p10 / p50 / p90 | −4.42 / −1.52 / +1.34 |
| **Strategy** | **+1.42** |

**The strategy beats 90.2% of random-entry simulations.** Random entries average −1.57% (negative); the signal adds ~+3% over random across 13 months.

## Interpretation — honest

1. **There IS a faint, real signal.** Beating 90% of random isn't luck-level noise — the MACD-crossover + RSI + volume filter genuinely picks better-than-random entries. But 90th percentile ≈ p0.10, **not** a slam-dunk (a strict bar would want ≥95%). Call it *weak-to-moderate* skill.
2. **The edge is thin and cost-fragile.** Gross +0.06%/trade, breakeven ~3 bps/side. On SPY (penny spread, tiny size) realistic slippage is sub-1 bp, so it survives — but the **live bot trades AAPL/TSLA/NVDA too**, where spreads are wider and the **market-order exits** (time-exit, stop-loss) pay half-spread each. Blended live cost is plausibly 2–5 bps/side → edge ranges from *thin-positive* to *gone*.
3. **Structural tell — the brackets barely fire.** Across the OOS trades, exits are dominated by MACD-crossunder and end-of-day; SL (−2%) and TP (+4%) essentially never trigger intraday (15-min SPY rarely moves 2–4% within a session). So the strategy is really *"enter on MACD cross-up, exit on cross-down or EOD"* — a small intraday-momentum scalp. The thin returns follow directly: it nets tiny moves and bleeds the rest to EOD.
4. **Opportunity cost is large.** As an absolute-return vehicle it is *far* worse than simply holding SPY (+1.4% vs +22.6%). Its only theoretical merit is low drawdown / near-zero exposure — but the returns are too small for that to matter yet.

## Verdict

**Not yet a strategy worth real capital.** There's a genuine but faint signal that barely survives realistic costs and is dwarfed by buy-and-hold. This is the quantified version of CLAUDE.md's rule: paper validated the *plumbing*; this backtest shows the *edge* is marginal. **Do not let a clean paper week be read as edge.**

## Recommended next research directions (in priority order)

1. **Redesign the exit, not the entry.** The +4%/−2% brackets never trigger — they're dead weight. Test exits matched to the intraday horizon: a tighter profit target, a trailing stop, or a fixed N-bar time stop. This is the highest-leverage change because EOD exits are where the edge leaks.
2. **Cut the cost drag.** Prefer **limit exits** over market exits (the time-exit/stop market fills are the cost sinks), and consider restricting the universe to **tight-spread names (SPY, QQQ)** — the wide-spread volatile symbols (TSLA) likely have negative net edge once their real slippage is paid.
3. **Re-run this test on the actual 5-symbol universe with per-symbol spreads**, not just the SPY proxy — that's the true cost picture.
4. Keep the reliability gate running in parallel; none of this justifies live capital yet.

## Caveats
- SPY is a proxy; live trades 5 symbols with different (mostly worse) cost profiles.
- IEX volume/quotes differ from SIP. No overnight, no fees beyond modeled slippage, no partial-fill modeling.
- Walk-forward avoids look-ahead but does not guarantee the future. 74 OOS trades is a modest sample.
- Random-entry shares the strategy's exit logic and session window, isolating *entry* skill specifically.

---

**No `config.py`/Railway change proposed.** This is a strategy-research finding, not a parameter change. Decision for the user: which next direction (1–4) to pursue, or pause strategy research and keep hardening reliability.
