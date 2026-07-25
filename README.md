# Algorithmic Trading Bot

A Python trading bot that paper-trades US equities on live market data using a multi-signal engine with built-in risk controls. Built to test whether disciplined, rules-based execution beats discretionary trading, without risking real capital.

> **Status:** Paper-trading only, on live market data via Alpaca. Educational / research project. Not financial advice.

## What it does

- **Multi-signal engine:** combines MACD, RSI and relative-volume signals to generate entry/exit decisions, rather than relying on a single indicator.
- **Risk management built in:** per-trade position sizing, a daily loss cap that halts trading once hit, stop-loss / take-profit exits, and a manual kill switch to flatten everything and stop.
- **Live data, simulated fills:** connects to the Alpaca API for real-time price/volume data and routes orders to a paper account, so the strategy runs against real market conditions with zero capital at risk.
- **Runs unattended:** a scheduler drives the session loop and a separate watchdog process monitors a heartbeat, so a silent failure during market hours is detected rather than missed.

## How it works

1. Pulls live price and volume data for the configured watchlist from Alpaca.
2. Computes MACD, RSI and relative-volume signals on each update.
3. Applies the combined signal logic to decide whether to open, hold or close a position.
4. Checks every prospective order against the risk limits (position size, daily loss cap, open-position and day-trade limits) before it is sent.
5. Reconciles its internal state against the broker account, and logs every decision and fill for later review.

## Risk & safety controls

| Control | Purpose |
|---|---|
| Per-trade position sizing | Caps notional exposure on any single order |
| Daily loss cap | Halts all trading for the day once a loss threshold is reached |
| Stop-loss / take-profit | Defines exit levels on every open position |
| Kill switch | Immediately flattens open positions and halts the bot |
| Broker reconciliation | Detects and refuses to trade through state/position mismatches |
| Live-mode guard | Real-money trading stays disabled unless an explicit confirmation flag is set |

## Engineering notes

A research project run with production-style discipline:

- **Paper validates plumbing, not edge.** Paper-trading PnL is treated as a reliability signal, never as proof of a real-money edge.
- **Backtests use walk-forward validation** and cost modelling (commission + slippage) before any parameter is trusted.
- A **go-live readiness gate** (a ~90-day paper reliability period plus demonstrated cost-adjusted positive edge) must pass before real capital is ever considered.
- Deployable to Railway as a long-running worker; operational alerts go out over Telegram.

## Tech

`Python` · `alpaca-py` (Alpaca API, paper) · `pandas` / `numpy` / `pandas-ta` for indicators · `APScheduler` scheduling · `SQLite` trade log · `Telegram` alerts · `Railway` deployment

## Setup

```bash
# 1. Clone and install
git clone https://github.com/infinitethaker-art/tradingbotmimi.git
cd tradingbotmimi
pip install -r requirements.txt

# 2. Add your Alpaca paper credentials to a .env file
#    (see .env.example for the full list — never commit .env)
#    ALPACA_API_KEY=...
#    ALPACA_SECRET_KEY=...
#    ALPACA_BASE_URL=https://paper-api.alpaca.markets

# 3. Run the scheduler (drives the session loop)
python scheduler/main.py
Never commit API keys. Keep them in .env, which is listed in .gitignore.

Disclaimer
For educational and research purposes only. It runs against an Alpaca paper-trading account and does not place real-money trades. Nothing here is financial advice.

Built by Meet Thaker.


*
