# Parameter Validation — Deployed Config vs Research Baseline

**Date:** 2026-05-29
**Analyst:** Claude (research layer)
**Status:** Complete — awaiting user review
**Feed:** IEX (free tier) · **Instrument:** SPY · **Mode:** research backtest (isolated)

---

> **Research only. No production change applied.** Per `workflows/research_backtest.md`,
> `config.py`/Railway env are never modified from the research layer. This note recommends;
> the user decides.

---

## Why this study

A drift exists between **what research has validated** and **what production runs**:

| Parameter | Research baseline (all prior notebooks) | **Live Railway (confirmed 2026-05-29)** |
|-----------|------------------------------------------|------------------------------------------|
| `MIN_RELATIVE_VOLUME` | 1.2 | **1.1** |
| `RSI_HIGH` | 65 | **70** |

Every prior notebook ("keep 1.2/65") was written against a baseline production **never actually ran**.
Live confirmation (`railway variables`, and today's signals): a rejected signal carried
`rel_vol=1.1508` rejected for `SYMBOL_ALREADY_OPEN` (not volume → gate ≤1.15 → 1.1), and AAPL
entered at `RSI 68.9` (only passes if `RSI_HIGH≥69` → 70). So **production = vol 1.1 / RSI≤70**,
and that config had **never been blessed by a walk-forward study.** This closes that gap.

## Method

- Script: `research/backtest/parameter_sensitivity_study.py` (re-run 2026-05-29)
- Results: `research/backtest/parameter_sensitivity_results.json`
- SPY, 15-min bars, IEX. 13 months history; **11 walk-forward OOS folds, 2025-07 → 2026-05** (230 OOS trading days), 3-month warmup.
- Indicators reimplemented in isolation (no `tools/` import). Strict 1-bar MACD crossover + RSI∈[35,RSI_HIGH] + rel_vol≥MIN_VOL. Exit: MACD crossunder | SL −2% | TP +4% | EOD. SL-before-TP on intrabar conflict (conservative).

## Results — OOS aggregate (base case)

| Config | N | Win% | AvgR | Sharpe | MaxDD% | TotRet% | Folds |
|--------|---|------|------|--------|--------|---------|-------|
| vol=1.2 rsi≤65 — *research baseline* | 48 | 45.8 | 0.034 | 0.220 | −1.16 | 3.25 | 9/11 |
| **vol=1.1 rsi≤70 — DEPLOYED** | **74** | **48.6** | **0.030** | **0.221** | **−1.37** | **4.47** | **9/11** |
| vol=1.1 rsi≤65 — *best Sharpe* | 64 | 48.4 | 0.032 | **0.223** | −1.37 | 4.12 | 9/11 |
| vol=1.2 rsi≤70 | 55 | 43.6 | 0.029 | 0.202 | −1.16 | 3.25 | 9/11 |

**Volume sweep (RSI≤65):** Sharpe by threshold — 0.8→0.094, 0.9→0.085, 1.0→0.076, **1.1→0.223**, 1.2→0.220.
There is a sharp jump at 1.1: thresholds ≤1.0 are clearly worse (Sharpe ~0.08, MaxDD −2 to −2.5%). **1.1 is a genuine local optimum** — production's volume setting is well-chosen, not a mistake.

**RSI sweep (vol=1.2):** Sharpe monotonically declines as the band widens — 65→0.220, 70→0.202, 75→0.163.
Raising `RSI_HIGH` only adds overbought entries of decreasing quality. **65 ≥ 70 > 75.** Production's `RSI_HIGH=70` is mildly *sub*-optimal vs 65.

## The finding that matters most — edge is thin everywhere

Restricting to OOS flatters the numbers. Over the **full 13 months** (warmup included), every config is near break-even:

| Config | Full-period Sharpe | Full-period TotRet% | OOS Sharpe |
|--------|--------------------|---------------------|-----------|
| vol=1.2 rsi≤65 | 0.031 | +0.68% | 0.220 |
| vol=1.1 rsi≤70 (deployed) | 0.076 | +2.40% | 0.221 |
| vol=1.1 rsi≤65 | 0.030 | +0.79% | 0.223 |

The "good" OOS Sharpe (~0.22) exists because the 3-month warmup window (2025-04→07) was a poor regime that got excluded. Across the whole period the strategy returns **<2.5% over 13 months, Sharpe ≈ 0.03–0.08 — and that is BEFORE fees, slippage, or partial fills**, none of which the backtest models. After realistic costs this is plausibly flat-to-negative. **The differences between configs (Sharpe 0.202–0.223) are within noise of a strategy whose edge is marginal at best.**

## False-negative / rejected-trade check

Live rejected trades are not strategy rejections (today's two were `SYMBOL_ALREADY_OPEN` — the re-entry guard). The volume sweep is the stand-in: taking the *rejected* lower-volume signals (thresholds 0.8–1.0) **underperforms** (Sharpe ~0.08, deeper drawdown), so the volume filter is correctly screening out bad trades. The 1.2→1.1 relaxation recovers ~16 OOS trades that are net-fine; going below 1.1 recovers trades that are net-bad. No valuable false negatives below 1.1.

## Overfitting checklist (`workflows/research_backtest.md`)

- [x] Walk-forward used (11 monthly OOS folds; params are a fixed grid, not fitted)
- [x] OOS Sharpe ≥ 0.8 × in-sample — passes trivially (OOS > full-period; no degradation)
- [x] Fold consistency 9/11 for all candidate configs (robust, not a single-fold artifact)
- [x] MaxDD well-controlled (≤ −1.37%) across candidates
- [x] Results labelled `data_feed=iex`
- [ ] **User review — pending**
- ⚠️ Caveat: absolute edge is low; backtest excludes fees/slippage; IEX≠SIP volume.

## Recommendation

1. **Keep production as-is (vol=1.1 / RSI≤70). No urgent change.** It is essentially Sharpe-equivalent to every other reasonable config (0.221 vs baseline 0.220) and produces more trades and a higher win rate. It is **not** misconfigured.
2. **Fix the governance drift (documentation, not parameters):** prior notebooks claim "keep 1.2/65" while production runs 1.1/70. This note supersedes that — **1.1/70 is now a validated-acceptable production config.** Future notebooks should baseline against 1.1/70.
3. **Optional, marginal:** `RSI_HIGH 70 → 65` (keep vol=1.1) moves to the highest-Sharpe config (0.223 vs 0.221) with a slightly higher win rate, at the cost of ~10 fewer trades/yr. The gain is **within noise** — take it or leave it; not worth a special deploy on its own.
4. **Do NOT keep tuning these parameters.** The edge is too thin for parameter choice to matter. The real question is strategy-level: **does this MACD-crossover + RSI + volume long-only strategy survive realistic costs?** Next research step should add fees/slippage to the backtest and/or test the signal against a buy-and-hold and random-entry baseline — not another threshold sweep.

## Caveats

- Paper/backtest PnL does not predict live edge. This validates parameter *choice*, not that the strategy *has* edge.
- Backtest omits fees, slippage, partial fills. Real results will be worse.
- IEX volume may differ from SIP; thresholds may not transfer if the feed is upgraded.
- Walk-forward avoids look-ahead but does not guarantee future performance.

---

**Decision required:** (a) keep 1.1/70 and update docs [recommended], or (b) also set RSI_HIGH→65 [marginal]. No `config.py`/Railway change will be made without explicit approval.
