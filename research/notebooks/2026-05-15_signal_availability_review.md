# Signal Availability Review — Apr 23 – May 15, 2026

> ⚠️ **SUPERSEDED 2026-05-29.** This note describes the live filters as rel-vol ≥ 1.2 / RSI ≤ 65. Production actually runs **rel-vol ≥ 1.1 / RSI ≤ 70** (confirmed via live Railway env, 2026-05-29). The signal-availability analysis still holds; only the stated thresholds were off. Validated config: [`2026-05-29_parameter_validation.md`](2026-05-29_parameter_validation.md).

**Data feed:** IEX (free tier) throughout  
**Symbol:** SPY  
**Mode:** Paper trading

---

## Overview

This note reviews signal availability across the first three weeks of live operation. The period spans two distinct runtime environments: a local machine (Apr 23–May 8) and Railway (May 11–May 15). The goal is to establish whether the absence of submitted trades reflects a broken strategy, normal market conditions, or an operational issue.

---

## Session Log

| Date | Environment | Uptime Notes |
|------|-------------|--------------|
| Apr 23 | Local | Late start; SSL reset on initial broker connection |
| Apr 24–25 | Local | Weekend — no market session |
| Apr 28–29 | Local | Full sessions; SPY in downtrend, MACD deeply negative all morning |
| Apr 30 | Local | Late start (after 11:00 ET); incomplete signal coverage |
| May 1–2 | Local | Loop A/B deployed with self-scheduling outer loop |
| May 5–8 | Local → Railway | Railway deployment in progress; last local heartbeat May 8 20:59 UTC |
| May 9–10 | Weekend | — |
| May 11–15 | Railway | Continuous; self-healing outer loop active |

---

## Terminology

Three terms are used in this note. They are not interchangeable.

**1. Component alignment**  
MACD histogram positive **and** RSI(14) in [35, 65] **and** relative volume ≥ 1.2× the 20-bar rolling mean.  
This is a useful diagnostic measure but is not sufficient to trigger a trade. A bar where all three components pass simultaneously is "aligned," but alignment does not equal a signal.

> **Note on prior scan-log tables:** Earlier session logs used `MACD✓` to mean "MACD histogram positive." That is component alignment, not a fresh crossover. This is why some bars appeared fully aligned in those tables but produced no `ENTER_LONG`.

**2. Technical ENTER_LONG**  
A fresh MACD crossover on that specific bar (`prev_hist <= 0 < curr_hist`, per `tools/signals/signal.py:63`) **and** RSI(14) in [35, 65] **and** relative volume ≥ 1.2×. This is what `signal.py` emits with `signal_type = "ENTER_LONG"`. Crossover is much rarer than component alignment — a histogram that has been positive for several consecutive bars does not re-trigger.

**3. Submitted trade**  
Technical ENTER_LONG **and** risk checks passed (`tools/risk/risk_checks.py`) **and** either `AUTO_EXECUTE=true` or manual approval completed within the 120-second window. Only a submitted trade results in an order reaching the broker.

---

## Signal Filter Activity

### Apr 23–May 8 (Local sessions)

Approximately 96 15-min bars scanned across 6 partial sessions.

| Blocking condition | Estimated frequency |
|--------------------|---------------------|
| Relative volume < 1.2× | ~78% of bars |
| MACD histogram not at fresh crossover | Majority of remaining bars |
| RSI outside [35, 65] (overbought) | ~17 bars, concentrated Apr 27–30 afternoon rallies |

**Regime context:**  
- Apr 28–29: SPY sold off. MACD histogram deeply negative all morning; RSI reached 26. Correct to stay out.  
- Apr 30–May 8: V-bounce recovery. RSI climbed to 68–78 (overbought) while MACD histogram lagged (price rising, momentum not yet confirming). Volume thin on recovery bars.

All three filters needed to align on the same 15-min bar simultaneously. They did not during this period.

**Technical ENTER_LONG count (Apr 23–May 8):** 0

### May 11–May 15 (Railway)

Continuous operation under the self-healing outer loop. Full session coverage.

**Technical ENTER_LONG count:**
- May 11: 0
- May 12: 1 (see incident below)
- May 13: 0
- May 14: 0
- May 15: 0 (as of writing)

---

## May 12 Incident — Operational Missed Trade

On May 12, a technical ENTER_LONG fired: the MACD histogram crossed from ≤ 0 to positive on a bar where RSI was within [35, 65] and relative volume met the 1.2× threshold. The signal was valid by all three criteria.

No order was submitted. The Railway environment had `AUTO_EXECUTE=false` at the time of deployment. The 120-second manual approval window elapsed without a response, and the trade was not placed.

**Root cause:** The `AUTO_EXECUTE` environment variable was not set to `true` in the Railway service configuration when the bot was deployed. This is a runtime config gap, not a strategy or code defect.

**Current status:** Local `.env` shows `AUTO_EXECUTE=true`. The current value of `AUTO_EXECUTE` in the Railway environment variables cannot be verified from this machine — confirm via the Railway dashboard before the next session.

This is the only confirmed case in the observation period where a technically valid signal was ready to trade but was blocked by an operational configuration issue.

---

## Summary Table

| Period | Environment | Sessions | Technical ENTER_LONG | Submitted Trades | Blocking cause |
|--------|-------------|----------|----------------------|------------------|----------------|
| Apr 23–May 8 | Local | 6 (partial) | 0 | 0 | Market regime; filters never simultaneously aligned |
| May 11 | Railway | 1 | 0 | 0 | No alignment |
| May 12 | Railway | 1 | 1 | 0 | `AUTO_EXECUTE=false` in Railway env; approval window expired |
| May 13–15 | Railway | 3 | 0 | 0 | No alignment |

---

## Conclusion

The strategy is functioning as designed. The OOS backtest established a baseline of ~0.93 trades/week (44 trades over 12 months). A three-week period with zero or one technical signal is within 1–2 standard deviations of that baseline, particularly given partial uptime across several local sessions and an unfavorable market regime (sharp sell-off followed by overbought recovery).

The only confirmed operational missed trade in the period is May 12. Its cause was a runtime configuration issue — `AUTO_EXECUTE=false` in the Railway environment — not a strategy defect, signal logic error, or code bug. No parameter changes are recommended. (See `research/notebooks/2026-05-10_signal_validation.md` for the full parameter sensitivity study confirming current settings are optimal by OOS Sharpe.)

**Action item:** Verify `AUTO_EXECUTE` value in Railway dashboard before the next trading session.
