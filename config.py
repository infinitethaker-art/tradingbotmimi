"""
All configuration is read from environment variables.
No defaults are hardcoded except for safe non-secret values.
Call config.validate() on startup to catch missing required vars early.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Required environment variable '{key}' is not set. See .env.example.")
    return value


def _bool(key: str, default: bool) -> bool:
    val = os.getenv(key, str(default)).strip().lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    raise EnvironmentError(
        f"Environment variable '{key}' has invalid boolean value '{val}'. "
        "Expected: true/false, 1/0, or yes/no."
    )


def _float(key: str, default: float) -> float:
    raw = os.getenv(key, str(default))
    try:
        return float(raw)
    except ValueError:
        raise EnvironmentError(f"Environment variable '{key}' must be a float. Got: '{raw}'")


def _int(key: str, default: int) -> int:
    raw = os.getenv(key, str(default))
    try:
        return int(raw)
    except ValueError:
        raise EnvironmentError(f"Environment variable '{key}' must be an integer. Got: '{raw}'")


# ── Broker ────────────────────────────────────────────────────────────────────
ALPACA_API_KEY: str = ""
ALPACA_SECRET_KEY: str = ""
ALPACA_BASE_URL: str = ""
ALPACA_DATA_URL: str = ""
ALPACA_DATA_FEED: str = ""

# ── Mode ──────────────────────────────────────────────────────────────────────
PAPER_TRADING: bool = True
DRY_RUN: bool = False
LIVE_CONFIRMED: str = ""

# ── Alerts ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = ""
TELEGRAM_CHAT_ID: str = ""

# ── Universe ──────────────────────────────────────────────────────────────────
WATCHLIST: list[str] = []

# ── Risk ──────────────────────────────────────────────────────────────────────
MAX_DAILY_LOSS_PCT: float = 0.03
MAX_OPEN_POSITIONS: int = 1
STOP_LOSS_PCT: float = 0.02
TAKE_PROFIT_PCT: float = 0.04

# ── Position sizing ───────────────────────────────────────────────────────────
MVP_POSITION_NOTIONAL_USD: float = 500.0

# ── Timing ────────────────────────────────────────────────────────────────────
TRADE_START_OFFSET_MIN: int = 15
TRADE_END_OFFSET_MIN: int = 30

# ── Manual approval ───────────────────────────────────────────────────────────
MANUAL_APPROVAL_WINDOW_SEC: int = 120
AUTO_EXECUTE: bool = False

# ── Signal filters ────────────────────────────────────────────────────────────
MIN_RELATIVE_VOLUME: float = 1.2

# ── Day trade safety ──────────────────────────────────────────────────────────
MAX_DAYTRADE_COUNT: int = 2

# ── System ────────────────────────────────────────────────────────────────────
KILL_SWITCH: bool = False
HEARTBEAT_INTERVAL_SEC: int = 300
WATCHDOG_STALE_THRESHOLD_MIN: int = 15


def load() -> None:
    """Load all values from environment. Call once at startup."""
    global ALPACA_API_KEY
    global ALPACA_SECRET_KEY
    global ALPACA_BASE_URL
    global ALPACA_DATA_URL
    global ALPACA_DATA_FEED
    global PAPER_TRADING
    global DRY_RUN
    global LIVE_CONFIRMED
    global TELEGRAM_BOT_TOKEN
    global TELEGRAM_CHAT_ID
    global WATCHLIST
    global MAX_DAILY_LOSS_PCT
    global MAX_OPEN_POSITIONS
    global STOP_LOSS_PCT
    global TAKE_PROFIT_PCT
    global MVP_POSITION_NOTIONAL_USD
    global TRADE_START_OFFSET_MIN
    global TRADE_END_OFFSET_MIN
    global MANUAL_APPROVAL_WINDOW_SEC
    global AUTO_EXECUTE
    global MIN_RELATIVE_VOLUME
    global MAX_DAYTRADE_COUNT
    global KILL_SWITCH
    global HEARTBEAT_INTERVAL_SEC
    global WATCHDOG_STALE_THRESHOLD_MIN

    ALPACA_API_KEY = _require("ALPACA_API_KEY")
    ALPACA_SECRET_KEY = _require("ALPACA_SECRET_KEY")
    ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    ALPACA_DATA_URL = os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
    ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex").lower()

    PAPER_TRADING = _bool("PAPER_TRADING", True)
    DRY_RUN = _bool("DRY_RUN", False)
    LIVE_CONFIRMED = os.getenv("LIVE_CONFIRMED", "")

    TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = _require("TELEGRAM_CHAT_ID")

    raw_watchlist = os.getenv("WATCHLIST", "SPY")
    WATCHLIST = [s.strip().upper() for s in raw_watchlist.split(",") if s.strip()]

    MAX_DAILY_LOSS_PCT = _float("MAX_DAILY_LOSS_PCT", 0.03)
    MAX_OPEN_POSITIONS = _int("MAX_OPEN_POSITIONS", 1)
    STOP_LOSS_PCT = _float("STOP_LOSS_PCT", 0.02)
    TAKE_PROFIT_PCT = _float("TAKE_PROFIT_PCT", 0.04)

    MVP_POSITION_NOTIONAL_USD = _float("MVP_POSITION_NOTIONAL_USD", 500.0)

    TRADE_START_OFFSET_MIN = _int("TRADE_START_OFFSET_MIN", 15)
    TRADE_END_OFFSET_MIN = _int("TRADE_END_OFFSET_MIN", 30)

    MANUAL_APPROVAL_WINDOW_SEC = _int("MANUAL_APPROVAL_WINDOW_SEC", 120)
    AUTO_EXECUTE = _bool("AUTO_EXECUTE", False)
    MIN_RELATIVE_VOLUME = _float("MIN_RELATIVE_VOLUME", 1.2)

    MAX_DAYTRADE_COUNT = _int("MAX_DAYTRADE_COUNT", 2)

    KILL_SWITCH = _bool("KILL_SWITCH", False)
    HEARTBEAT_INTERVAL_SEC = _int("HEARTBEAT_INTERVAL_SEC", 300)
    WATCHDOG_STALE_THRESHOLD_MIN = _int("WATCHDOG_STALE_THRESHOLD_MIN", 15)


def validate() -> None:
    """Raise on any configuration that would cause a runtime failure."""
    load()

    if not PAPER_TRADING and not DRY_RUN:
        if LIVE_CONFIRMED != "yes_i_understand_real_money":
            raise EnvironmentError(
                "Live mode requires LIVE_CONFIRMED=yes_i_understand_real_money. "
                "Set PAPER_TRADING=true to use paper trading instead."
            )

    if ALPACA_DATA_FEED not in ("iex", "sip", "delayed_sip"):
        raise EnvironmentError(
            f"ALPACA_DATA_FEED must be 'iex', 'sip', or 'delayed_sip'. Got: '{ALPACA_DATA_FEED}'"
        )

    # URL / mode consistency
    _paper_url = "paper-api.alpaca.markets"
    _live_url = "api.alpaca.markets"
    if PAPER_TRADING and _live_url in ALPACA_BASE_URL and _paper_url not in ALPACA_BASE_URL:
        raise EnvironmentError(
            f"PAPER_TRADING=true but ALPACA_BASE_URL points to the live endpoint ('{ALPACA_BASE_URL}'). "
            "Use https://paper-api.alpaca.markets for paper trading."
        )
    if not PAPER_TRADING and not DRY_RUN and _paper_url in ALPACA_BASE_URL:
        raise EnvironmentError(
            f"Live trading mode but ALPACA_BASE_URL points to the paper endpoint ('{ALPACA_BASE_URL}'). "
            "Use https://api.alpaca.markets for live trading."
        )

    if not WATCHLIST:
        raise EnvironmentError("WATCHLIST must contain at least one symbol.")

    if MAX_DAILY_LOSS_PCT <= 0 or MAX_DAILY_LOSS_PCT > 0.20:
        raise EnvironmentError("MAX_DAILY_LOSS_PCT must be between 0 and 0.20 (0% and 20%).")

    if MVP_POSITION_NOTIONAL_USD <= 0:
        raise EnvironmentError("MVP_POSITION_NOTIONAL_USD must be positive.")

    if STOP_LOSS_PCT <= 0:
        raise EnvironmentError("STOP_LOSS_PCT must be > 0.")
    if TAKE_PROFIT_PCT <= 0:
        raise EnvironmentError("TAKE_PROFIT_PCT must be > 0.")
    if MAX_OPEN_POSITIONS < 1:
        raise EnvironmentError("MAX_OPEN_POSITIONS must be >= 1.")
    if TRADE_START_OFFSET_MIN < 0:
        raise EnvironmentError("TRADE_START_OFFSET_MIN must be >= 0.")
    if TRADE_END_OFFSET_MIN < 0:
        raise EnvironmentError("TRADE_END_OFFSET_MIN must be >= 0.")
    if MANUAL_APPROVAL_WINDOW_SEC <= 0:
        raise EnvironmentError("MANUAL_APPROVAL_WINDOW_SEC must be > 0.")
    if MIN_RELATIVE_VOLUME <= 0:
        raise EnvironmentError("MIN_RELATIVE_VOLUME must be > 0.")
    if MAX_DAYTRADE_COUNT < 0:
        raise EnvironmentError("MAX_DAYTRADE_COUNT must be >= 0.")
    if HEARTBEAT_INTERVAL_SEC <= 0:
        raise EnvironmentError("HEARTBEAT_INTERVAL_SEC must be > 0.")
    if WATCHDOG_STALE_THRESHOLD_MIN <= 0:
        raise EnvironmentError("WATCHDOG_STALE_THRESHOLD_MIN must be > 0.")
