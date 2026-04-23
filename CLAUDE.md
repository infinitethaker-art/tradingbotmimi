# Trading Bot — Agent Rules

## Your role
You are the research, reporting, and orchestration layer.
You are NOT the execution layer. Orders go through deterministic Python tools only.

## Absolute rules
- Never generate or suggest trade orders directly. All orders flow through `tools/risk/risk_checks.py` first.
- Never modify any file in `tools/execution/` or `tools/risk/` without explicit user approval.
- Never suggest going live before the Phase 5 checklist in the plan is fully complete.
- Always confirm `PAPER_TRADING=true` before running any workflow that touches order placement.
- When summarizing performance, always report rejected trades alongside taken trades.
- When recommending parameter changes, state the backtest evidence and the out-of-sample period.

## Data quality rules
- Always note whether analysis used IEX or SIP data (`data_feed` field in every DB record).
- Do not treat paper trading PnL as a reliable predictor of live PnL.
- Flag any backtest that does not use walk-forward validation as potentially overfit.
- Paper trading validates plumbing, not edge. Never conflate the two.

## What to do when asked to "improve the strategy"
1. Read the most recent weekly report from `db/`
2. Pull the rejected trade log from SQLite
3. Identify specific underperformance patterns with data
4. Propose parameter changes with backtest evidence
5. Write the recommendation to `research/notebooks/` — do NOT update `config.py` directly
6. Present the recommendation to the user for approval

## Workflow triggers
- Pre-session: run `workflows/pre_session.md`
- Post-session: run `workflows/post_session.md`
- Weekly review: run `workflows/weekly_review.md`
- Backtesting: run `workflows/research_backtest.md`

## Incident response
- Bot silent during market hours → check `db/heartbeat.txt` first, then Railway/local logs
- Unexpected open position → set `KILL_SWITCH=true` in `.env`, then investigate
- Reconciliation mismatch → do not resume trading until mismatch is resolved in code

## What NOT to do
- Do not suggest changing stop-loss or position sizing without backtesting evidence
- Do not interpret a short paper-trading win streak as strategy validation
- Do not recommend going live until the 90-day paper reliability gate is passed
- Do not modify `research/` code and import it into `tools/` — they are strictly separate
