"""
Main entry point. Starts Loop A and Loop B in separate threads.
Implements PID lock file to prevent duplicate instances.
Sends session-start Telegram alert after Loop B is reconciled and ready.
Loop A does not start until Loop B is ready.
"""
import logging
import os
import queue
import signal
import sys
import threading

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from tools.alerts import telegram_bot as tg
from tools.data import market_calendar
from tools.reporting import trade_logger
from tools.risk.risk_checks import RiskState
from tools.execution.position_tracker import PositionTracker
from scheduler.loop_a import LoopA
from scheduler.loop_b import LoopB

_DB_DIR = os.path.join(os.path.dirname(__file__), "../db")
_LOCK_PATH = os.path.join(_DB_DIR, "scheduler.lock")

# Create db/ directory BEFORE configuring FileHandler so the handler never fails
os.makedirs(_DB_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(_DB_DIR, "bot.log")),
    ],
)
logger = logging.getLogger("main")


def _acquire_lock() -> None:
    """Write PID to lock file. Exit if another instance is already running."""
    if os.path.exists(_LOCK_PATH):
        try:
            with open(_LOCK_PATH) as f:
                existing_pid = int(f.read().strip())
            os.kill(existing_pid, 0)
            tg.alert_duplicate_scheduler(existing_pid)
            logger.critical("Duplicate scheduler detected (PID %d). Exiting.", existing_pid)
            sys.exit(1)
        except (ProcessLookupError, ValueError, OSError):
            logger.warning("Stale lock file found. Overwriting.")

    with open(_LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    logger.info("Lock acquired (PID %d).", os.getpid())


def _release_lock() -> None:
    try:
        os.remove(_LOCK_PATH)
    except FileNotFoundError:
        pass


def main() -> None:
    config.validate()

    if not config.PAPER_TRADING and not config.DRY_RUN:
        if config.LIVE_CONFIRMED != "yes_i_understand_real_money":
            logger.critical("Live mode requires LIVE_CONFIRMED=yes_i_understand_real_money. Exiting.")
            sys.exit(1)

    _acquire_lock()

    stop_event = threading.Event()

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received (%s). Stopping…", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    trade_logger.init_db()

    if not market_calendar.is_trading_day():
        logger.info("Today is not a trading day. Exiting cleanly.")
        tg.send_raw("📅 Market closed today — bot exiting.")
        _release_lock()
        return

    # Fetch session-start equity from Alpaca
    from alpaca.trading.client import TradingClient
    trading_client = TradingClient(
        config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING
    )
    account = trading_client.get_account()
    session_start_equity = float(account.equity)
    daytrade_count = int(getattr(account, "daytrade_count", 0) or 0)

    risk_state = RiskState(session_start_equity)
    risk_state.set_daytrade_count(daytrade_count)
    position_tracker = PositionTracker()

    # Shared queue for Loop A -> Loop B intent handoff (bounded to 1)
    intent_queue: queue.Queue = queue.Queue(maxsize=1)

    # Boot Loop B in a thread (handles reconciliation + WebSocket)
    loop_b = LoopB(risk_state, position_tracker, intent_queue)
    loop_b_thread = threading.Thread(target=loop_b.start, name="LoopB", daemon=True)
    loop_b_thread.start()

    # Wait for Loop B to be reconciled and WebSocket-connected before doing anything else
    logger.info("Waiting for Loop B to reconcile and connect (timeout=30s)…")
    ready = loop_b._ready.wait(timeout=30)
    if not ready:
        logger.critical(
            "Loop B did not become ready within 30 seconds. "
            "Reconciliation may have failed or WebSocket did not connect. Exiting."
        )
        loop_b.stop()
        _release_lock()
        sys.exit(1)

    # Session start alert — sent only after Loop B is confirmed ready
    close_time = market_calendar.market_close_time()
    tg.alert_session_start(
        equity=session_start_equity,
        loss_limit=risk_state.daily_loss_limit_usd,
        symbols=config.WATCHLIST,
        market_close=close_time.strftime("%H:%M"),
        feed=config.ALPACA_DATA_FEED,
    )

    # Boot Loop A — only starts after Loop B is ready
    symbol = config.WATCHLIST[0]
    loop_a = LoopA(risk_state, position_tracker, intent_queue, ws_client=loop_b._ws)
    loop_a_thread = threading.Thread(
        target=loop_a.run, args=(symbol, stop_event), name="LoopA", daemon=True
    )
    loop_a_thread.start()

    logger.info("Bot running. Symbol=%s Feed=%s Mode=%s",
                symbol, config.ALPACA_DATA_FEED,
                "PAPER" if config.PAPER_TRADING else "LIVE")

    # Block main thread until shutdown
    stop_event.wait()

    logger.info("Shutdown initiated.")
    loop_b.stop()
    loop_b_thread.join(timeout=10)

    # Send daily report
    try:
        from tools.reporting.daily_report import send_daily_report
        send_daily_report()
    except Exception as exc:
        logger.error("Daily report failed: %s", exc)

    _release_lock()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
