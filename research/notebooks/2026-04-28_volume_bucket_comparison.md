# Volume Bucket Comparison — MACD Crossover Entry Filter
**Date:** 2026-04-28
**Analyst:** Claude (research layer)
**Status:** Complete — awaiting user review
**Approval required before any config.py edit:** Yes

---

## Question
On 2026-04-28, SPY had a MACD bullish crossover at 13:00 ET with RSI OK but relative volume
at only 0.50x. The production `MIN_RELATIVE_VOLUME=1.2` blocked the entry.
SPY then rallied. Is the volume floor adding alpha, or filtering out too many winners?

## Method
- Script: `research/backtest/volume_bucket_comparison.py`
- Results: `research/backtest/volume_bucket_results.json`
- Instrument: SPY, 15-min bars, IEX feed
- History: 13 months — walk-forward OOS after 3-month warmup
- Indicators: MACD(12,26,9), RSI(14),
  relative volume (20-bar rolling mean) — reimplemented in research isolation
- Entry: strict 1-bar MACD histogram crossover, RSI in [35.0, 65.0]
  **No volume filter at entry — each trade is tagged by crossover-bar bucket only**
- Intrabar SL+TP conflict: STOP_LOSS assumed hit first (conservative)
- Exit: MACD crossunder | stop-loss -2% | take-profit +4% | EOD (15:45 ET)
- Two execution cases reported; if conclusion changes between them, evidence is weak
- \* = fewer than 30 trades — low confidence

---

## Results — Overall (full period)

### Base Case (entry at signal bar close)

| Bucket | N | Win% | AvgRet% | MedRet% | AvgR | MedR | TotRet% | MaxDD% | Exits |
|--------|---|------|---------|---------|------|------|---------|--------|-------|
| <0.5x \* | 28 | 57.1% | 0.017% | 0.027% | 0.008 | 0.013 | 0.43% | -1.79% | MD=17 SL=0 TP=0 EOD=11 |
| 0.5-1.0x | 85 | 36.5% | -0.017% | -0.056% | -0.008 | -0.028 | -1.49% | -3.27% | MD=50 SL=0 TP=0 EOD=35 |
| 1.0-1.2x | 35 | 42.9% | -0.078% | -0.064% | -0.039 | -0.032 | -2.69% | -2.82% | MD=16 SL=0 TP=0 EOD=19 |
| >=1.2x | 58 | 39.7% | -0.049% | 0.0% | -0.024 | 0.0 | -2.86% | -5.1% | MD=17 SL=1 TP=0 EOD=40 |

### Sensitivity Case (entry at next-bar open)

| Bucket | N | Win% | AvgRet% | MedRet% | AvgR | MedR | TotRet% | MaxDD% | Exits |
|--------|---|------|---------|---------|------|------|---------|--------|-------|
| <0.5x \* | 28 | 57.1% | 0.019% | 0.064% | 0.01 | 0.032 | 0.5% | -1.85% | MD=17 SL=0 TP=0 EOD=11 |
| 0.5-1.0x | 85 | 35.3% | -0.019% | -0.061% | -0.01 | -0.03 | -1.69% | -3.43% | MD=50 SL=0 TP=0 EOD=35 |
| 1.0-1.2x | 35 | 42.9% | -0.079% | -0.061% | -0.04 | -0.031 | -2.75% | -2.76% | MD=16 SL=0 TP=0 EOD=19 |
| >=1.2x | 47 | 48.9% | -0.06% | -0.006% | -0.03 | -0.003 | -2.86% | -5.1% | MD=17 SL=1 TP=0 EOD=29 |

---

## OOS Aggregate (after 3-month warmup)

### Base Case

| Bucket | N | Win% | AvgRet% | MedRet% | AvgR | MedR | TotRet% | MaxDD% | Exits |
|--------|---|------|---------|---------|------|------|---------|--------|-------|
| <0.5x \* | 19 | 52.6% | -0.009% | 0.026% | -0.004 | 0.013 | -0.17% | -1.11% | MD=12 SL=0 TP=0 EOD=7 |
| 0.5-1.0x | 68 | 36.8% | -0.002% | -0.071% | -0.001 | -0.036 | -0.17% | -2.03% | MD=41 SL=0 TP=0 EOD=27 |
| 1.0-1.2x | 32 | 46.9% | -0.071% | -0.05% | -0.035 | -0.025 | -2.25% | -2.82% | MD=15 SL=0 TP=0 EOD=17 |
| >=1.2x | 44 | 40.9% | 0.04% | 0.0% | 0.02 | 0.0 | 1.76% | -1.16% | MD=11 SL=0 TP=0 EOD=33 |

### Sensitivity Case

| Bucket | N | Win% | AvgRet% | MedRet% | AvgR | MedR | TotRet% | MaxDD% | Exits |
|--------|---|------|---------|---------|------|------|---------|--------|-------|
| <0.5x \* | 19 | 52.6% | -0.007% | 0.029% | -0.004 | 0.015 | -0.14% | -1.07% | MD=12 SL=0 TP=0 EOD=7 |
| 0.5-1.0x | 68 | 36.8% | -0.003% | -0.073% | -0.002 | -0.036 | -0.26% | -2.04% | MD=41 SL=0 TP=0 EOD=27 |
| 1.0-1.2x | 32 | 46.9% | -0.073% | -0.035% | -0.036 | -0.017 | -2.31% | -2.76% | MD=15 SL=0 TP=0 EOD=17 |
| >=1.2x | 35 | 51.4% | 0.05% | 0.047% | 0.025 | 0.023 | 1.76% | -1.13% | MD=11 SL=0 TP=0 EOD=24 |

---

## Fold Consistency

### Base Case

- **<0.5x**: profitable in 5/9 OOS folds with trades | OOS n=19 — **LOW CONFIDENCE (OOS n<30)**
- **0.5-1.0x**: profitable in 4/11 OOS folds with trades | OOS n=68
- **1.0-1.2x**: profitable in 2/10 OOS folds with trades | OOS n=32
- **>=1.2x**: profitable in 8/11 OOS folds with trades | OOS n=44

### Sensitivity Case

- **<0.5x**: profitable in 5/9 OOS folds with trades | OOS n=19 — **LOW CONFIDENCE (OOS n<30)**
- **0.5-1.0x**: profitable in 4/11 OOS folds with trades | OOS n=68
- **1.0-1.2x**: profitable in 2/10 OOS folds with trades | OOS n=32
- **>=1.2x**: profitable in 8/11 OOS folds with trades | OOS n=35

---

## Key Findings

**1. >=1.2x bucket (production threshold):** OOS avg R = 0.02, win rate = 40.9%, n = 44.

**2. 0.5-1.0x bucket (below threshold):** OOS avg R = -0.001, win rate = 36.8%, n = 68.

**3. Volume filter appears to be adding alpha in the base case.** The >=1.2x bucket outperforms 0.5-1.0x on both avg R and win rate.

**4. Conclusion is consistent across both execution assumptions.**

---

## Recommendation

The >=1.2x bucket outperforms the 0.5-1.0x bucket on avg R (0.02 vs -0.001) and win rate (40.9% vs 36.8%) in OOS data. The >=1.2x bucket was profitable in 8/11 OOS folds. **Recommend keeping `MIN_RELATIVE_VOLUME=1.2`.** The volume filter appears to be earning its keep.

---

## Caveats
- IEX data may differ from SIP in volume readings; relative volume thresholds may not transfer
- Paper trading PnL does not predict live PnL
- Backtest does not account for slippage, fees, or partial fills
- Walk-forward avoids look-ahead bias but does not guarantee future performance
- Any bucket with OOS n<30 should not drive a threshold change

---

**User reviewed — no production change approved.**
