# Per-Symbol Viability — Which of the 5 live symbols actually have edge?

**Date:** 2026-05-30
**Analyst:** Claude (research layer)
**Status:** Complete — awaiting user review
**Feed:** IEX · **Config tested:** DEPLOYED `vol=1.1 / RSI≤70` · 13mo, 15-min, walk-forward OOS

---

> **Research only. No production change made or pushed.** This recommends a universe change;
> the user decides, and any change to `WATCHLIST` would be a deliberate config edit + redeploy.

---

## Question

The SPY-only cost test looked OK. But the bot trades **5 symbols** (`SPY, QQQ, AAPL, NVDA, TSLA`) with the *same* parameters and very different spreads. Which symbols actually carry the edge, and which are dragging it down?

## Method

- Script: `research/backtest/cost_universe_study.py` · Results: `cost_universe_results.json`
- Deployed config `1.1/70` run **per symbol**, 13mo 15-min IEX, 11 walk-forward OOS folds.
- Cost shown two ways: **penny floor** (1-cent round-trip spread — optimistic best case) and **live quoted spread**.
- ⚠️ Run on a **weekend** — live quotes for several names came back as stale/auction garbage (NVDA "750 bps", TSLA "1005 bps" are impossible intraday). Only QQQ's live quote (1.22 bps) is usable. **The gross and penny-floor numbers do NOT depend on quotes and are reliable; the live-spread overlay should be re-measured on a weekday.**

## Results (OOS aggregate)

| Symbol | OOS N | Gross Sharpe | Gross Ret% | Net Ret% @ penny floor | Verdict |
|--------|-------|--------------|-----------|------------------------|---------|
| **SPY** | 74 | **0.223** | **+4.47** | **+4.37** | **VIABLE** |
| **QQQ** | 65 | **0.131** | **+3.59** | +3.49 (live 1.22bp → **+2.77**, Sharpe 0.10) | **VIABLE** |
| AAPL | 49 | 0.04 | +0.86 | +0.70 | marginal — flat gross, dies after real cost |
| NVDA | 60 | 0.01 | +0.30 | +0.01 | dead — no edge even at penny floor |
| TSLA | 64 | **−0.095** | **−6.47** | −6.61 | **DEAD — loses money GROSS, before any cost** |

## The finding

**The strategy's edge lives entirely in SPY and QQQ. The three single-stock names range from flat to outright losing — and TSLA loses before you pay a cent of cost.**

- **TSLA: −6.47% gross, negative Sharpe.** This isn't a cost problem — the strategy is structurally *wrong* for TSLA. Likely mechanism: TSLA is volatile enough that the −2% stop actually *fires* (unlike SPY, where it never triggers), so the long-only MACD-cross strategy buys local momentum tops, eats −2% stops, and rarely reaches +4%. Asymmetric losses.
- **NVDA flat / AAPL marginal:** essentially no edge gross; once a realistic spread is paid they go negative.
- **SPY + QQQ:** the real engines — and QQQ at its *actual* 1.22 bp spread still nets +2.77%.

**Implication:** trading all 5 symbols with one parameter set **dilutes** the bot — the index ETFs earn a thin edge while the single stocks give it back (TSLA actively destroys it). The bracket that's "dead weight" on SPY is "actively harmful" on TSLA. Same config, opposite effect — because the strategy was tuned/validated on SPY.

## Recommendation (in priority order)

1. **Narrow the universe to `WATCHLIST=SPY,QQQ`.** This is the highest-value, near-free change — evidence-backed (TSLA negative gross; NVDA/AAPL flat gross), no strategy redesign required. It's a `WATCHLIST` env-var change on Railway → **needs your approval + a redeploy.** Do NOT add the single stocks back without per-symbol evidence.
2. **Re-measure real intraday spreads on a weekday** (re-run this script when market is open) to firm up the cost overlay for SPY/QQQ. (Doesn't change the gross verdicts above.)
3. **Then** the exit redesign (`2026-05-29_cost_and_skill_test.md` #1) — now scoped to SPY+QQQ only, where edge actually exists.

## Caveats

- Per-symbol OOS samples are modest (49–74 trades). TSLA's negative result is large and consistent enough to act on; AAPL/NVDA "flat" is lower-confidence but points the same way (don't trade them).
- Weekend live quotes unreliable (only QQQ valid); **gross/penny conclusions are quote-independent and robust.**
- Single 13-month window, SPY/QQQ are the most data-rich. No fees beyond modeled slippage; no overnight; walk-forward ≠ guarantee.
- Past per-symbol behavior need not persist, but "don't trade a symbol the strategy loses on gross" is a safe rule regardless.

---

**Decision for the user:** approve narrowing `WATCHLIST` to `SPY,QQQ`? If yes, it's a Railway env change + redeploy (I'll prepare it for your approval — I won't change it unilaterally). No change made or pushed by this note.
