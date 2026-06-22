# Algorithmic Trading Bot

A Python trading bot that paper-trades US equities on live market data using a multi-signal engine with built-in risk controls. Built to test whether disciplined, rules-based execution beats discretionary trading — without risking real capital.

> **Status:** Paper-trading only, on live market data. Educational project. Not financial advice.

## What it does

- **Signal engine** — combines MACD, RSI and volume signals to generate entry/exit decisions, rather than relying on a single indicator.
- **Risk management built in** — position-size limits, a daily loss cap that halts trading once hit, and a manual kill switch to flatten everything and stop.
- **Live data, simulated fills** — connects to a broker API for real-time price data and routes orders to a paper account, so strategy logic runs against real market conditions.

## How it works

1. Pulls live price and volume data for a configured watchlist via the broker API.
2. Computes MACD, RSI and volume signals on each update.
3. Applies the combined signal logic to decide whether to open, hold or close a position.
4. Checks every prospective order against the risk limits (max position size, daily loss cap) before it is sent.
5. Logs each decision and fill for later review.

## Risk controls

| Control | Purpose |
|---|---|
| Max position size | Caps exposure on any single name |
| Daily loss cap | Stops all trading for the day once a loss threshold is reached |
| Kill switch | Immediately closes open positions and halts the bot |

## Tech

- **Python**
- **[Broker] API** — *replace with your actual broker (e.g. Alpaca)*
- Standard data/maths libraries for the indicators

## Setup

```bash
# 1. Clone and install
git clone https://github.com/infinitethaker-art/tradingbotmimi.git
cd tradingbotmimi
pip install -r requirements.txt

# 2. Add your broker credentials to a .env file (never commit this)
#    API_KEY=your_key
#    API_SECRET=your_secret

# 3. Run
python main.py   # adjust to your actual entry point
```

**Never commit API keys.** Keep them in `.env` and make sure `.env` is listed in `.gitignore`.

## Disclaimer

This project is for educational and research purposes only. It runs against a paper-trading account and does not place real-money trades. Nothing here is financial advice.

---
*Built by Meet Thaker.*
