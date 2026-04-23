# Workflow: Pre-Session Routine

## Objective
Prepare for the trading day before market open.

## When to run
30 minutes before market open (approximately 9:00 AM ET on trading days).
`main.py` runs this automatically via session-start alert. You can also run it manually.

## Steps

1. Confirm today is a trading day
   - Run: `python tools/data/market_calendar.py`
   - Check output: is today a trading day? What time does the market close?
   - If early close: note the close time. The bot uses this dynamically.

2. Verify Alpaca connection and account state
   - Run: `python -c "import config; config.validate(); print('Config OK')"`
   - Open Alpaca paper dashboard and confirm: balance, open positions, open orders

3. Check day trade count
   - API: `GET /v2/account` → read `daytrade_count`
   - If count >= 2: note that new entries will be blocked today. Review manually.

4. Review overnight news for watchlist symbols
   - Open Alpaca dashboard → News tab → filter last 12 hours for each symbol
   - Flag any high-risk events: earnings, FDA decisions, legal actions, analyst downgrades
   - Note flagged symbols — Phase 3 will automate this gating

5. Check VIX level (optional but recommended)
   - Check current VIX. If VIX > 30, note elevated volatility. Consider reducing position size manually.

6. Confirm bot is running (if starting manually)
   - Check `db/heartbeat.txt` — timestamp should be recent
   - Or start: `python scheduler/main.py`
   - And in a separate terminal: `python scheduler/watchdog.py`

## Output
- Telegram session-start message confirms equity, loss limit, market close time
- Bot is running, heartbeat is live, watchdog is active

## Edge cases
- Market closed today: bot exits cleanly, sends "Market closed" Telegram
- Early close detected: bot uses dynamic close time — no manual action needed
- Alpaca API error on startup: bot exits with error log — fix credentials and restart
