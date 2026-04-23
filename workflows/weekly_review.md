# Workflow: Weekly Review

## Objective
Systematic review of the past week's trading performance.
Produce a written assessment and identify any parameter improvements to research.

## When to run
Sunday morning, or Friday after close. Budget 30–45 minutes.

## Steps

1. Pull weekly trade summary from DB
   ```sql
   SELECT
     date(timestamp) as day,
     COUNT(*) as signals,
     SUM(CASE WHEN disposition='PAPER' THEN 1 ELSE 0 END) as taken,
     SUM(CASE WHEN disposition='REJECTED' THEN 1 ELSE 0 END) as rejected
   FROM signal_events
   WHERE timestamp >= date('now', '-7 days')
   GROUP BY 1;
   ```

2. Pull weekly PnL from DB
   ```sql
   SELECT
     SUM(pnl_realized) as total_pnl,
     COUNT(*) as fills,
     AVG(pnl_realized) as avg_pnl_per_trade,
     MIN(pnl_realized) as worst_trade,
     MAX(pnl_realized) as best_trade
   FROM order_events
   WHERE submitted_at >= date('now', '-7 days') AND status='filled';
   ```

3. Pull slippage summary
   ```sql
   SELECT AVG(slippage_pct), MAX(slippage_pct), MIN(slippage_pct)
   FROM order_events
   WHERE submitted_at >= date('now', '-7 days') AND status='filled';
   ```

4. Pull rejected trade log
   ```sql
   SELECT rejection_reason, COUNT(*) FROM signal_events
   WHERE disposition='REJECTED' AND timestamp >= date('now', '-7 days')
   GROUP BY 1;
   ```

5. Simulate rejected trades (manual step)
   - For each rejected ENTER signal: check what the price did afterward
   - Were rejections correct (protected from losses) or false negatives (missed gains)?

6. Ask Claude to generate a narrative weekly summary
   - Provide: signal counts, PnL, slippage data, rejected trade outcomes
   - Claude reads this and writes a 1-page summary with observations
   - Do NOT ask Claude to recommend live parameter changes directly

7. Review the summary and decide:
   - Is the strategy performing as expected for the market regime this week?
   - Any signals worth backtesting with different parameters?
   - Any system issues (missed sessions, reconciliation mismatches, alert failures)?

8. If a parameter change is warranted:
   - Write a hypothesis: "Narrowing RSI range to 40–60 may reduce false entries"
   - Run backtests in `research/notebooks/walk_forward.ipynb`
   - Only update `config.py` after 4+ weeks of evidence

## Output
- Written weekly summary (save to `research/weekly/YYYY-WW.md`)
- List of any parameter hypotheses to test
- Any system issues to fix before next week

## Red flags to watch for
- Win rate dropping below 40% for two consecutive weeks
- Average slippage increasing week-over-week
- Daily loss limit hit more than once in a week
- Signals dropping to near-zero (strategy going silent)
- Reconciliation mismatches appearing in the log
