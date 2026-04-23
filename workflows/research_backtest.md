# Workflow: Research & Backtesting

## Objective
Safely evaluate parameter changes or new strategies in isolation before promoting to production.

## Critical rule
Code in `research/` is NEVER imported by code in `tools/` or `scheduler/`.
The research environment is fully isolated from the production execution path.

## When to use
- You have a parameter hypothesis from the weekly review
- You want to evaluate a new strategy signal
- You want to understand why a particular period performed poorly or well

## Steps

1. Export signal + outcome data from DB
   ```python
   import sqlite3, pandas as pd
   conn = sqlite3.connect('db/trades.db')
   df = pd.read_sql("""
     SELECT se.*, oe.actual_fill_price, oe.pnl_realized, oe.slippage_pct
     FROM signal_events se
     LEFT JOIN order_events oe ON se.event_id = oe.signal_event_id
     WHERE se.data_feed = 'iex'  -- always filter by feed
   """, conn)
   df.to_csv('.tmp/signal_outcomes.csv', index=False)
   ```

2. Open `research/notebooks/walk_forward.ipynb`
   - Load `.tmp/signal_outcomes.csv`
   - **Do NOT import from tools/ — reimplement indicators in the notebook**
   - Set aside the most recent 4 weeks as the out-of-sample test period

3. Run walk-forward validation
   - In-sample period: compute optimal parameter range
   - Out-of-sample period: apply those parameters without re-fitting
   - Compare Sharpe ratio, win rate, max drawdown in both periods
   - If out-of-sample Sharpe degrades by more than 30%, the strategy is overfit

4. Evaluate rejected trades
   - Simulate: what would have happened if the rejected signal was taken?
   - Compare against actual market movement in the following bars
   - This surfaces false negatives in the risk engine

5. Document findings
   - Write a short summary: hypothesis, backtest result, recommendation
   - Include: feed used (IEX), period tested, parameter range tested
   - If recommending a parameter change: state the expected improvement and the risk

6. If recommending promotion to production:
   - Present the finding to the user
   - User manually updates `config.py`
   - Bot is restarted with new parameters
   - Track the parameter change in `incidents/` or a dedicated `CHANGELOG.md`

## What NOT to do
- Do not run backtests on the same data you used to fit parameters (in-sample overfitting)
- Do not backtest on less than 90 days of data (too short for regime coverage)
- Do not promote a change that only improves performance on the last 2 weeks
- Do not modify production `config.py` directly from a research notebook

## Overfitting checklist before any promotion
- [ ] Walk-forward validation used (not single in-sample fit)
- [ ] Out-of-sample Sharpe ratio ≥ 0.8 × in-sample Sharpe
- [ ] Max drawdown in out-of-sample ≤ 1.5 × in-sample max drawdown
- [ ] Strategy tested across at least one high-volatility period
- [ ] All results labelled with `data_feed=iex` (or sip if upgraded)
- [ ] User has reviewed and approved the change
