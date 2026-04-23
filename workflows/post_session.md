# Workflow: Post-Session Routine

## Objective
Review the trading session and confirm the system is in a clean state.

## When to run
After market close (4:15–4:30 PM ET). Triggered automatically by main.py on shutdown.
Can also be run manually.

## Steps

1. Verify all positions closed
   - Check Alpaca paper dashboard: open positions = 0
   - Check DB: `SELECT * FROM positions WHERE status='open';`
   - If any position is open: investigate. Was the time-based exit triggered?

2. Review today's daily report (sent via Telegram automatically)
   - Check: total signals, taken, rejected, fills, PnL
   - If no report arrived: check `db/bot.log` for errors

3. Inspect the signal log
   - Query: `SELECT signal_type, disposition, rejection_reason, COUNT(*) FROM signal_events WHERE date(timestamp)=date('now') GROUP BY 1,2,3;`
   - Review rejected trades: were they correctly blocked or false negatives?

4. Inspect fill quality
   - Query: `SELECT symbol, side, expected_fill_price, actual_fill_price, slippage_pct FROM order_events WHERE date(submitted_at)=date('now');`
   - Log to a running slippage tracker (spreadsheet or notebook)

5. Export today's signal log to CSV (for research)
   - Run: `python -c "import sqlite3, csv; ... "` (add to research scripts in Phase 4)

6. Note any incidents
   - Did the bot crash? Reconnect? Alert fire unexpectedly?
   - Write a short note to `incidents/YYYY-MM-DD.md` if anything unusual happened

7. Verify heartbeat was active all session
   - Check `db/bot.log` for any gaps in heartbeat writes

## Output
- Clean position state (0 open positions)
- Daily report received via Telegram
- Signal log reviewed
- Any incidents documented

## Checklist before closing VS Code
- [ ] 0 open positions confirmed
- [ ] Daily report received
- [ ] Any anomalies noted in incidents/
- [ ] heartbeat.txt is recent (or bot was stopped cleanly)
