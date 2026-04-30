# Weekly Review — Week of Apr 23–30, 2026
**Date:** 2026-04-30
**Analyst:** Claude (research layer)
**Status:** Complete — no production changes recommended
**Approval required before any config.py edit:** Yes

---

## Overview

| | |
|---|---|
| Sessions | 6 (2 full, 4 late starts) |
| Total scans | 96 |
| ENTER_LONG signals (current filters) | 0 |
| Trades taken | 0 |
| PnL | $0.00 |
| Week classification | Stability / setup week |

4 of 6 sessions were late starts (bot started after 11:00 ET). Signal coverage was incomplete for those sessions. This week should not be used as a baseline for signal frequency evaluation.

---

## The only ENTER_LONG (Apr 23) — not relevant to current system

Signal fired with MACD crossover, RSI=55.4 — but volume was 0.46x. That session predated the volume filter. User manually rejected it (`MANUAL_REJECT`). Excluded from current-system analysis.

---

## What blocked entries this week

**By session (volume assessed using `relative_volume < 1.2`):**

| Date | Scans | Vol fails | Avg vol | Max vol | MACD fails | RSI fails |
|---|---|---|---|---|---|---|
| Apr 23 | 3 | 2 | 0.77x | 1.56x | 2 | 0 |
| Apr 24 | 23 | 20 | 0.80x | 1.85x | 3 | 1 |
| Apr 27 | 13 | 12 | 0.79x | 1.25x | 4 | 7 |
| Apr 28 | 19 | 15 | 0.98x | 2.89x | 10 | 3 |
| Apr 29 | 15 | 13 | 0.73x | 1.97x | 14 | 1 |
| Apr 30 | 23 | 13 | 1.32x | 3.67x | 1 | 6 |

**Key patterns:**

- **Volume** was below 1.2x for 75 of 96 bars (78%). The persistent structural blocker. Intraday SPY volume concentrates at open and close — the crossover window and the volume window rarely overlap.
- **MACD** dominated Apr 28–29 when SPY was in a clean downtrend. Histogram trended negative all day; no crossover came. Bot correctly sat out.
- **RSI overbought** blocked 7 bars Apr 27 and 6 bars Apr 30 afternoon (RSI 70–74). Correct behaviour — those were extended moves.
- **One strict crossover** with all conditions nearly met: Apr 30 10:45 ET (MACD:OK, RSI:OK, vol=1.01x). Volume was the sole miss by 0.19x.

---

## Near-miss on Apr 28 — notable

Three consecutive bars at 13:30–14:15 had volume of 2.89x, 2.23x, 1.60x — highest of the week. But MACD was deeply negative (−0.29 to −0.17). This is exactly the scenario the volume filter is designed to avoid: high-volume bars in a downtrend that look tempting but aren't valid entries.

---

## Red flags (from workflow checklist)

| Check | Status |
|---|---|
| Win rate < 40% two weeks running | N/A — no trades yet |
| Slippage increasing | N/A |
| Daily loss limit hit | No |
| Signals near-zero | ⚠️ 0 ENTER_LONG in 6 sessions |
| Reconciliation mismatches | None |

The zero-signal count is within normal variance. Expected rate is ~3–4/month (~1 per 5 sessions). Six sessions with zero is possible, particularly in a directional week (Apr 28–29 selloff, Apr 30 strong rally) where trending price action produces few crossovers. The late-start sessions also reduced coverage of the highest-probability entry window (9:30–10:30 ET open).

---

## System issues resolved this week

1. False BOT SILENT alerts on restart — fixed
2. Three startup bugs found in first Monday session — fixed
3. DB logging bug: `relative_volume_ok` column shift corrupted `trading_date_et` for 93 rows — fixed and backfilled
4. Mid-session Telegram status ping at noon ET — added
5. 9:15 AM daily session-start reminder via Telegram — added

---

## Recommendations

**Keep all current parameters unchanged:**

- **Strict MACD crossover (N=1):** Validated in Apr 27 backtest research. Every additional bar of lookback degrades OOS win rate and Sharpe. No change.
- **MIN_RELATIVE_VOLUME=1.2:** Validated in Apr 28 backtest research. The ≥1.2x bucket was profitable in 8/11 OOS folds. No change.
- **SPY only:** Phase 1 instrument. No expansion until 90-day paper gate is passed. No change.
- **Continue paper sessions next week:** System stability confirmed. Execution path not yet validated (no fills). Paper trading continues.

**No parameter hypotheses to test this week.** Both key parameters have been researched and confirmed within the past 5 days. Market regime (strongly directional) explains the silence better than parameter miscalibration.

---

## Priority for next week

1. Run full sessions from open (9:15 AM reminder is now active)
2. Get the first ENTER_LONG signal through the execution path in paper mode
3. Verify fill, slippage logging, and position tracking end-to-end
4. If still zero trades after two more full weeks, revisit signal frequency hypothesis

---

## Caveats

- IEX data may differ from SIP in volume readings; relative volume thresholds may not transfer to live
- Paper trading validates plumbing, not edge
- This week's data is not a reliable baseline — 4 of 6 sessions were partial due to late starts
- All data flagged `data_feed=iex`
