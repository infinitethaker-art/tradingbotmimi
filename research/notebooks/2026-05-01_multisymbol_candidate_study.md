# Multi-Symbol Candidate Study — SPY + QQQ
**Date:** 2026-05-02
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
- Instruments: SPY, QQQ — 15-min bars, IEX feed
- History: 13 months — walk-forward OOS after 3-month warmup (12 OOS folds)
- Indicators: MACD(12,26,9), RSI(14), rel_vol 20-bar
  rolling mean — computed independently per symbol (no shared state)
- Entry: strict 1-bar MACD histogram crossover + RSI in [35, 65] + rel_vol ≥ 1.2
- Exit: MACD crossunder | SL -2% | TP +4% | EOD 15:45 ET
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

\* = fewer than 30 OOS trades — low confidence
Tr/Wk = avg trades per week over OOS period
Prof. Months = profitable OOS months / OOS months with ≥1 trade

---

## Results

### OOS Aggregate

| Scenario | N (OOS) | Tr/Wk | Win% | AvgR | TotRet% | MaxDD% | Sharpe | Prof. Months | Exits |
|----------|---------|-------|------|------|---------|--------|--------|--------------|-------|
| SPY only | 44 | 0.95 | 40.9% | 0.020 | 1.76% | -1.16% | 0.132 | 8/11 | MD=11 SL=0 TP=0 EOD=33 |
| QQQ only | 50 | 1.08 | 48.0% | 0.023 | 2.25% | -1.24% | 0.105 | 5/11 | MD=30 SL=0 TP=0 EOD=20 |
| Combined — 1 pos total | 82 | 1.77 | 45.1% | 0.015 | 2.43% | -2.21% | 0.079 | 6/11 | MD=39 SL=0 TP=0 EOD=43 |
| Combined — 1 per symbol ⚠ HIGHER RISK | 94 | 2.03 | 44.7% | 0.021 | 4.05% | -2.21% | 0.114 | 6/11 | MD=41 SL=0 TP=0 EOD=53 |

---

## Per-Fold OOS Breakdown

#### SPY only

| Fold | N | Win% | AvgR | TotRet% | MaxDD% |
|------|---|------|------|---------|--------|
| 2025-06 | 2 | 0.0% | -0.108 | -0.43% | 0.00% |
| 2025-07 | 4 | 25.0% | 0.003 | 0.03% | -0.20% |
| 2025-08 | 3 | 66.7% | 0.037 | 0.22% | 0.00% |
| 2025-09 | 4 | 0.0% | -0.084 | -0.67% | -0.67% |
| 2025-10 | 3 | 100.0% | 0.139 | 0.83% | 0.00% |
| 2025-11 | 6 | 0.0% | -0.049 | -0.58% | -0.47% |
| 2025-12 | 4 | 50.0% | 0.051 | 0.41% | -0.11% |
| 2026-01 | 5 | 40.0% | 0.024 | 0.24% | -0.31% |
| 2026-02 | 2 | 100.0% | 0.059 | 0.24% | 0.00% |
| 2026-03 | 6 | 66.7% | 0.010 | 0.12% | -1.11% |
| 2026-04 | 5 | 40.0% | 0.136 | 1.36% | 0.00% |
| 2026-05 | 0 | — | — | — | — |

#### QQQ only

| Fold | N | Win% | AvgR | TotRet% | MaxDD% |
|------|---|------|------|---------|--------|
| 2025-06 | 5 | 60.0% | -0.045 | -0.46% | -0.09% |
| 2025-07 | 3 | 0.0% | -0.008 | -0.05% | -0.05% |
| 2025-08 | 7 | 14.3% | -0.076 | -1.07% | -1.20% |
| 2025-09 | 7 | 42.9% | -0.003 | -0.04% | -0.47% |
| 2025-10 | 4 | 50.0% | 0.115 | 0.92% | -0.26% |
| 2025-11 | 3 | 100.0% | 0.164 | 0.99% | 0.00% |
| 2025-12 | 5 | 20.0% | -0.090 | -0.90% | -1.09% |
| 2026-01 | 5 | 60.0% | 0.006 | 0.06% | -0.48% |
| 2026-02 | 3 | 66.7% | -0.006 | -0.04% | -0.16% |
| 2026-03 | 4 | 50.0% | 0.110 | 0.86% | -1.06% |
| 2026-04 | 4 | 100.0% | 0.245 | 1.97% | 0.00% |
| 2026-05 | 0 | — | — | — | — |

#### Combined — 1 pos total

| Fold | N | Win% | AvgR | TotRet% | MaxDD% |
|------|---|------|------|---------|--------|
| 2025-06 | 6 | 50.0% | -0.038 | -0.46% | -0.09% |
| 2025-07 | 5 | 20.0% | -0.002 | -0.02% | -0.25% |
| 2025-08 | 9 | 33.3% | -0.047 | -0.84% | -1.14% |
| 2025-09 | 10 | 30.0% | -0.029 | -0.59% | -0.65% |
| 2025-10 | 6 | 66.7% | 0.100 | 1.21% | -0.19% |
| 2025-11 | 8 | 37.5% | 0.025 | 0.40% | -0.43% |
| 2025-12 | 9 | 33.3% | -0.027 | -0.49% | -1.10% |
| 2026-01 | 9 | 55.6% | 0.017 | 0.30% | -0.48% |
| 2026-02 | 4 | 75.0% | 0.011 | 0.09% | -0.16% |
| 2026-03 | 9 | 55.6% | 0.052 | 0.92% | -2.16% |
| 2026-04 | 7 | 57.1% | 0.136 | 1.92% | 0.00% |
| 2026-05 | 0 | — | — | — | — |

#### Combined — 1 per symbol (HIGHER RISK / INFORMATIONAL ONLY)

| Fold | N | Win% | AvgR | TotRet% | MaxDD% |
|------|---|------|------|---------|--------|
| 2025-06 | 7 | 42.9% | -0.063 | -0.88% | -0.52% |
| 2025-07 | 7 | 14.3% | -0.001 | -0.02% | -0.25% |
| 2025-08 | 10 | 30.0% | -0.042 | -0.84% | -1.14% |
| 2025-09 | 11 | 27.3% | -0.032 | -0.71% | -0.77% |
| 2025-10 | 7 | 71.4% | 0.125 | 1.76% | -0.19% |
| 2025-11 | 9 | 33.3% | 0.022 | 0.40% | -0.43% |
| 2025-12 | 9 | 33.3% | -0.027 | -0.49% | -1.10% |
| 2026-01 | 10 | 50.0% | 0.015 | 0.30% | -0.48% |
| 2026-02 | 5 | 80.0% | 0.020 | 0.20% | -0.16% |
| 2026-03 | 10 | 60.0% | 0.050 | 0.98% | -2.16% |
| 2026-04 | 9 | 66.7% | 0.184 | 3.36% | 0.00% |
| 2026-05 | 0 | — | — | — | — |

---

## Signal Overlap (OOS Period — Unconstrained)

| Metric | Value |
|--------|-------|
| SPY qualifying signal bars | 44 |
| QQQ qualifying signal bars | 50 |
| Simultaneous overlap bars | 6 |
| Overlap as % of SPY signals | 13.6% |
| Overlap as % of QQQ signals | 12.0% |

High overlap (>50%) means SPY and QQQ often signal together — incremental frequency
benefit in `combined_1pos` is limited by position conflicts. Low overlap means signals
are more independent and frequency gains are real.

---

## Key Findings

- **SPY standalone:** 44 OOS trades, 0.95 tr/wk, avg R 0.020, max DD -1.16%, 8/11 profitable months
- **QQQ standalone:** 50 OOS trades, 1.08 tr/wk, avg R 0.023, max DD -1.24%, 5/11 profitable months
- **Combined (1 pos total):** 82 OOS trades, 1.77 tr/wk, avg R 0.015, max DD -2.21%, 6/11 profitable months
- **Signal overlap:** 6 simultaneous bars (13.6% of SPY signals, 12.0% of QQQ) — indicates how often position conflicts arise
- **Higher-risk reference:** combined_1pos_per_symbol shows 94 OOS trades (2.03 tr/wk) at the cost of potentially double capital at risk

---

## Recommendation

### combined_1pos vs spy_only — four criteria

- ✓ **Frequency (trades/week):** 0.95 → 1.77
- ✓ **Avg R:** 0.020 → 0.015
- ✓ **Max DD:** -1.16% → -2.21%
- ✗ **Fold consistency:** 73% → 55%

**QQQ addition does NOT pass all four criteria.** Failed: Fold consistency. SPY-only remains the recommended configuration. Do not add QQQ to production.

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
