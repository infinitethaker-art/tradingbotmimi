# Signal Validation Study — 2026-05-10

> ⚠️ **SUPERSEDED 2026-05-29.** This note labels `vol=1.2 / RSI≤65` as the live config ("[current]") and recommends "deploy as-is." Production actually runs **`vol=1.1 / RSI≤70`** (confirmed via live Railway env, 2026-05-29). A fresh 13-month walk-forward validated 1.1/70 as acceptable (Sharpe ≈ tied with 1.2/65). See [`2026-05-29_parameter_validation.md`](2026-05-29_parameter_validation.md). Numbers below are retained for history.

## Question
Has the strategy been broken for 2 weeks, or did market conditions explain zero trades?

## Method
Ran `research/backtest/parameter_sensitivity_study.py` against 13 months of SPY 15-min IEX bars (2025-03-20 → 2026-05-08), 12 OOS folds (walk-forward). Simulates exact live signal logic: MACD(12,26,9) crossover + RSI(14) band + relative volume ≥ threshold.

## Results — Volume Sweep (RSI_HIGH = 65)

| Config | N (OOS) | Win% | AvgRet% | TotRet% | MaxDD% | Sharpe | Tr/Wk |
|--------|---------|------|---------|---------|--------|--------|-------|
| vol=0.8 | 110 | 40.0 | +0.011 | +1.10 | -2.49 | 0.031 | 2.33 |
| vol=0.9 | 91 | 41.8 | +0.004 | +0.35 | -2.49 | 0.014 | 1.93 |
| vol=1.0 | 78 | 43.6 | -0.001 | -0.08 | -2.09 | -0.002 | 1.65 |
| vol=1.1 | 61 | 44.3 | +0.034 | +2.07 | -1.37 | 0.118 | 1.29 |
| **vol=1.2 [current]** | **44** | **40.9** | **+0.040** | **+1.76** | **-1.16** | **0.132** | **0.93** |

## Results — RSI Sweep (vol = 1.2)

| Config | N (OOS) | Win% | AvgRet% | TotRet% | MaxDD% | Sharpe | Tr/Wk |
|--------|---------|------|---------|---------|--------|--------|-------|
| **rsi≤65 [current]** | **44** | **40.9** | **+0.040** | **+1.76** | **-1.16** | **0.132** | **0.93** |
| rsi≤70 | 51 | 39.2 | +0.029 | +1.47 | -1.16 | 0.102 | 1.08 |
| rsi≤75 | 54 | 37.0 | +0.018 | +0.96 | -1.29 | 0.064 | 1.14 |

## Findings

1. **Signal is not broken.** Current params (vol=1.2, RSI 35–65) fired 44 trades over 12 OOS months — ~0.93 trades/week historically. The strategy does generate entries.

2. **Current params are the best in the sweep.** Highest Sharpe (0.132), highest AvgRet (+0.040%), lowest drawdown (-1.16%). Loosening either parameter hurts quality even as it increases frequency.

3. **Two-week silence explained by market conditions, not a bug:**
   - Week 1 (05/04): SPY sold off — MACD deeply negative all morning, RSI hit 26. Correct to stay out.
   - Week 1 recovery + Week 2 (05/05–08): V-bounce sent RSI to 68–78 (overbought) while MACD histogram remained negative (price up, momentum lagging). Volume thin throughout. All three filters needed to align simultaneously on the same 15-min bar — they didn't.
   - At 0.93 trades/week, a 2-week dry spell is within 1–2 standard deviations of normal variance, especially with partial uptime on 3 of 5 days.

## Recommendation

**No parameter changes.** Current params are optimal by OOS Sharpe. Deploy as-is.

Continue monitoring. If 4+ consecutive weeks produce 0 ENTER_LONG signals, re-run this study with updated data to see if market regime has structurally changed.
