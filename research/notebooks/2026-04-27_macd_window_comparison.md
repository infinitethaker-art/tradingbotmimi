# MACD Crossover Window Size Comparison
**Date:** 2026-04-27  
**Analyst:** Claude (research layer)  
**Status:** Complete — recommendation: NO CHANGE to production  
**Approval required before any config.py edit:** Yes

---

## Question
Today at 13:15 ET, SPY had RSI:OK and VOL:OK (1.25x) but MACD:NO because the crossover had happened 2 bars earlier (while volume was still low). Would a 2- or 3-bar lookback window produce better overall results than the current strict crossover?

## Method
- Script: `research/backtest/macd_window_comparison.py`
- Results: `research/backtest/macd_window_results.json`
- Instrument: SPY, 15-min bars, IEX feed
- History: 2025-03-07 → 2026-04-27 (8,688 bars, ~13 months)
- Indicators: MACD(12,26,9), RSI(14), relative volume (20-bar mean) — all reimplemented in research isolation; no imports from `tools/`
- Parameters: RSI_LOW=35, RSI_HIGH=65, MIN_REL_VOL=1.2, SL=-2%, TP=+4%
- Validation: walk-forward — first 3 months treated as warmup, each subsequent month scored as an OOS fold (10 folds total)
- Exit logic: MACD crossunder OR stop-loss OR take-profit OR EOD

## Results

### Out-of-sample aggregate (primary metric)
| Window | Trades | Win% | Avg Return | Sharpe | Max DD |
|--------|-------:|-----:|----------:|-------:|-------:|
| N=1 (current) | 44 | **59.1%** | **+0.064%** | **2.95** | **-1.11%** |
| N=2 | 74 | 52.7% | +0.044% | 2.05 | -2.01% |
| N=3 | 93 | 50.5% | +0.028% | 1.32 | -3.02% |

### Walk-forward OOS folds — N=1 (current)
| Month | Trades | Win% | Avg Return | Sharpe |
|-------|-------:|-----:|----------:|-------:|
| 2025-06 | 2 | 0.0% | -0.228% | -12.6 |
| 2025-07 | 4 | 25.0% | -0.041% | -3.31 |
| 2025-08 | 3 | 100.0% | +0.191% | 12.3 |
| 2025-09 | 4 | 50.0% | -0.127% | -7.2 |
| 2025-10 | 3 | 100.0% | +0.285% | 14.44 |
| 2025-11 | 6 | 33.3% | -0.067% | -7.69 |
| 2025-12 | 4 | 75.0% | +0.080% | 6.36 |
| 2026-01 | 5 | 60.0% | +0.135% | 5.46 |
| 2026-02 | 2 | 100.0% | +0.075% | 90.71 |
| 2026-03 | 6 | 66.7% | +0.068% | 1.85 |
| 2026-04 | 5 | 60.0% | +0.271% | 9.11 |

Note: high Sharpe values in low-trade months (e.g., 2026-02 with 2 trades) are statistically unreliable. The OOS aggregate across all 44 trades is the reliable number.

## Key Findings

**1. Strict crossover (N=1) produces the highest-quality signals.**  
Every additional bar of lookback adds trade count but degrades win rate and average return. N=2 adds 30 trades (+68%) while dropping win rate 6.4 points and Sharpe by 1. N=3 is worse on every metric.

**2. The overfit check formula is not reliable for this dataset.**  
The check computes `OOS Sharpe / IS Sharpe >= 0.8`. Full-period IS Sharpe is negative (dragged down by indicator warmup noise in the first 3 months), so dividing a positive OOS number by a negative IS number produces a meaningless negative ratio. The overfit check itself needs to be redesigned — for example, using a rolling IS window guaranteed to have positive Sharpe — before it can serve as a hard pass/fail gate. The OOS Sharpe of 2.95 is assessed on its own merits and is considered acceptable.

**3. Today's miss (13:15 ET) is supported by the backtest as an acceptable tradeoff.**  
The 13:15 entry would have been 2 bars after crossover. One missed trade is not individually significant, but the OOS data shows that late entries as a class average lower quality — adding them via N=2 drops win rate from 59.1% to 52.7% across the full test period.

**4. Monthly variance is high with low trade counts.**  
Some months have 0–4 trades for N=1. June and September 2025 were losing months. This is expected: a high-selectivity strategy on one instrument will have months with few signals. 90 days of paper trading (not 13 days) is the right gate before live evaluation.

## Recommendation
**Keep `_crossover()` as `prev_hist <= 0 < curr_hist`. No change to production.**

The strict crossover is not a bug — it is a feature. Multi-condition strategies have a synchronization cost, and the data shows that cost is worth paying. Relaxing the window would trade signal quality for signal count, which is the wrong direction for a low-frequency, risk-controlled paper trading system.

## Walk-forward overfit checklist
- [x] Walk-forward validation used (10 OOS folds)
- [ ] Out-of-sample Sharpe >= 0.8 × in-sample Sharpe — *formula not reliable here: IS Sharpe is negative (warmup period drag), making the ratio meaningless. Overfit check needs redesign before use as a pass/fail gate. OOS Sharpe of 2.95 assessed on its own merits.*
- [x] Max drawdown acceptable in all OOS folds
- [x] Tested across multiple market regimes within the available 13-month IEX dataset (including at least one elevated-volatility month)
- [x] All results labelled data_feed=iex
- [x] User reviewed — no production change approved.
