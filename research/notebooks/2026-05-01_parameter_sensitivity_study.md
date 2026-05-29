# Parameter Sensitivity Study — Volume & RSI Upper Band
**Date:** 2026-05-29
**Analyst:** Claude (research layer)
**Status:** Complete — awaiting user review

---

> **Research only. No production change approved.**
> Do not lower thresholds just to increase trade count.
> A change is only recommended if it improves OOS avg R, drawdown, and fold consistency
> relative to the current baseline (MIN_RELATIVE_VOLUME=1.2, RSI_HIGH=65).

---

## Background

The bot generated 0 ENTER_LONG signals across 93 scans in the week of 2026-04-28.
Volume was the primary blocker (~78% of bars below 1.2x threshold).
RSI was secondary (overbought mornings). MACD crossover logic is not under review
(Apr 27 research confirmed strict N=1 is optimal).

Prior result (Apr 28 backtest): the 1.0–1.2x bucket was already isolated:
32 OOS trades, 46.9% win rate, avg R −0.035, total return −2.25%, max DD −2.82%.
This does not support lowering the volume threshold.

This study tests whether any combination of relaxed volume or RSI upper band
improves the OOS edge profile — not merely the trade count.

---

## Method

- Script: `research/backtest/parameter_sensitivity_study.py`
- Results: `research/backtest/parameter_sensitivity_results.json`
- Instrument: SPY, 15-min bars, IEX feed
- History: 13 months — walk-forward OOS after 3-month warmup
  (11 OOS folds)
- Indicators: MACD(12,26,9), RSI(14),
  rel vol (20-bar rolling mean) — reimplemented in research isolation
- Entry: strict 1-bar MACD histogram crossover + RSI in [35, RSI_HIGH] + rel_vol >= MIN_VOL
- Exit: MACD crossunder | SL -2% | TP +4% | EOD 15:45 ET
- Intrabar SL+TP conflict: STOP_LOSS assumed hit first (conservative)
- Two execution cases: base_case (signal-bar close) and sensitivity_case (next-bar open)
- \* = fewer than 30 OOS trades — low confidence; do not drive a threshold change

All tables below show **OOS aggregate (base case)** unless labelled otherwise.
Tr/Wk = average trades per week over the OOS period.
Folds = profitable OOS months / OOS months with at least one trade.

---

## Results

### Volume Sweep (RSI_HIGH fixed at 65)

| Config | N | Win% | AvgRet% | AvgR | TotRet% | MaxDD% | Sharpe | Tr/Wk | Folds (base) | Exits |
|--------|---|------|---------|------|---------|--------|--------|-------|-------------|-------|
| vol=0.8 rsi<=65 | 109 | 43.1% | 0.033% | 0.016 | 3.55% | -2.49% | 0.094 | 2.37 | 9/11 | MD=44 SL=0 TP=0 EOD=65 |
| vol=0.9 rsi<=65 | 93 | 45.2% | 0.026% | 0.013 | 2.37% | -2.49% | 0.085 | 2.02 | 7/11 | MD=36 SL=0 TP=0 EOD=57 |
| vol=1.0 rsi<=65 | 82 | 46.3% | 0.023% | 0.011 | 1.84% | -2.09% | 0.076 | 1.78 | 8/11 | MD=28 SL=0 TP=0 EOD=54 |
| vol=1.1 rsi<=65 | 64 | 48.4% | 0.063% | 0.032 | 4.12% | -1.37% | 0.223 | 1.39 | 9/11 | MD=19 SL=0 TP=0 EOD=45 |
| vol=1.2 rsi<=65 **← baseline** | 48 | 45.8% | 0.067% | 0.034 | 3.25% | -1.16% | 0.22 | 1.04 | 9/11 | MD=12 SL=0 TP=0 EOD=36 |

### RSI Upper Band Sweep (MIN_RELATIVE_VOLUME fixed at 1.2)

| Config | N | Win% | AvgRet% | AvgR | TotRet% | MaxDD% | Sharpe | Tr/Wk | Folds (base) | Exits |
|--------|---|------|---------|------|---------|--------|--------|-------|-------------|-------|
| vol=1.2 rsi<=65 **← baseline** | 48 | 45.8% | 0.067% | 0.034 | 3.25% | -1.16% | 0.22 | 1.04 | 9/11 | MD=12 SL=0 TP=0 EOD=36 |
| vol=1.2 rsi<=70 | 55 | 43.6% | 0.059% | 0.029 | 3.25% | -1.16% | 0.202 | 1.2 | 9/11 | MD=17 SL=0 TP=0 EOD=38 |
| vol=1.2 rsi<=75 | 58 | 41.4% | 0.047% | 0.023 | 2.73% | -1.29% | 0.163 | 1.26 | 8/11 | MD=19 SL=0 TP=0 EOD=39 |

### Combination Matrix

| Config | N | Win% | AvgRet% | AvgR | TotRet% | MaxDD% | Sharpe | Tr/Wk | Folds (base) | Exits |
|--------|---|------|---------|------|---------|--------|--------|-------|-------------|-------|
| vol=1.2 rsi<=65 **← baseline** | 48 | 45.8% | 0.067% | 0.034 | 3.25% | -1.16% | 0.22 | 1.04 | 9/11 | MD=12 SL=0 TP=0 EOD=36 |
| vol=1.2 rsi<=70 | 55 | 43.6% | 0.059% | 0.029 | 3.25% | -1.16% | 0.202 | 1.2 | 9/11 | MD=17 SL=0 TP=0 EOD=38 |
| vol=1.2 rsi<=75 | 58 | 41.4% | 0.047% | 0.023 | 2.73% | -1.29% | 0.163 | 1.26 | 8/11 | MD=19 SL=0 TP=0 EOD=39 |
| vol=1.1 rsi<=70 | 74 | 48.6% | 0.059% | 0.03 | 4.47% | -1.37% | 0.221 | 1.61 | 9/11 | MD=27 SL=0 TP=0 EOD=47 |
| vol=1.0 rsi<=70 | 92 | 46.7% | 0.024% | 0.012 | 2.18% | -2.09% | 0.084 | 2.0 | 8/11 | MD=36 SL=0 TP=0 EOD=56 |

---

## Sensitivity Case (next-bar open entry) — OOS Aggregate

All configs below; if conclusion changes versus base case, evidence is weak.

| Config | N | Win% | AvgR | TotRet% | MaxDD% | Sharpe |
|--------|---|------|------|---------|--------|--------|
| vol=0.8 rsi<=65  | 100 | 47.0% | 0.017 | 3.41% | -2.45% | 0.095 |
| vol=0.9 rsi<=65  | 84 | 50.0% | 0.014 | 2.29% | -2.45% | 0.086 |
| vol=1.0 rsi<=65  | 73 | 52.1% | 0.012 | 1.74% | -2.05% | 0.076 |
| vol=1.1 rsi<=65  | 55 | 56.4% | 0.036 | 3.99% | -1.34% | 0.232 |
| vol=1.2 rsi<=65 [baseline] | 39 | 56.4% | 0.041 | 3.24% | -1.13% | 0.244 |
| vol=1.2 rsi<=70  | 44 | 54.5% | 0.036 | 3.23% | -1.13% | 0.224 |
| vol=1.2 rsi<=75  | 47 | 51.1% | 0.029 | 2.71% | -1.25% | 0.179 |
| vol=1.1 rsi<=70  | 63 | 57.1% | 0.034 | 4.33% | -1.34% | 0.23 |
| vol=1.0 rsi<=70  | 81 | 53.1% | 0.013 | 2.07% | -2.05% | 0.084 |

---

## Recommendation

No configuration tested here outperforms the baseline on **all three criteria** (OOS avg R, max drawdown, fold consistency).

**Recommendation: keep MIN_RELATIVE_VOLUME=1.2 and RSI_HIGH=65.**

Do not lower thresholds to increase trade count. Higher trade count alone is not a reason to change parameters.

---

## Caveats

- IEX data may differ from SIP in volume readings; relative volume thresholds calibrated on IEX
  may not transfer to SIP when going live
- Paper trading PnL does not predict live PnL
- Backtest does not account for slippage, fees, or partial fills
- Walk-forward avoids look-ahead bias but does not guarantee future performance
- Any configuration with OOS n<30 should not drive a threshold change

---

**User reviewed — no production change approved.**
