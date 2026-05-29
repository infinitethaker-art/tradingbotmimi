"""
Main entry point. Starts Loop A and Loop B in separate threads.
Implements PID lock file to prevent duplicate instances.
Sends session-start Telegram alert after Loop B is reconciled and ready.
Loop A does not start until Loop B is ready.
"""
import datetime
import logging
import os
import queue
import signal
import sys
import threading
import time
from zoneinfo import ZoneInfo

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
_ET = ZoneInfo("America/New_York")


def _midday_monitor(symbol: str, position_tracker, stop_event: threading.Event) -> None:
    """Fire one Telegram status ping at noon ET, then exit."""
    now = datetime.datetime.now(_ET)
    noon = datetime.datetime.combine(now.date(), datetime.time(12, 0), tzinfo=_ET)
    if now >= noon:
        logger.info("Mid-session monitor: already past noon, skipping.")
        return
    wait_secs = (noon - now).total_seconds()
    logger.info("Mid-session monitor: status ping in %.0fs (noon ET).", wait_secs)
    if stop_event.wait(timeout=wait_secs):
        return  # Shutdown before noon
    try:
        from tools.reporting.trade_logger import daily_summary
        summary = daily_summary()
        pos = position_tracker.get(symbol)
        tg.send_midday_status(symbol, summary, pos)
    except Exception as exc:
        logger.error("Mid-session status failed: %s", exc)


_LOCK_PATH = os.path.join(_DB_DIR, "scheduler.lock")

# Create db/ directory BEFORE configuring FileHandler so the handler never fails
os.makedirs(_DB_DIR, exist_ok=True)

from logging.handlers import RotatingFileHandler as _RotatingFileHandler
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        _RotatingFileHandler(
            os.path.join(_DB_DIR, "bot.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        ),
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


def _readiness_action(ready: bool, thread_alive: bool, stopped: bool) -> str:
    """
    Decide what main() should do while waiting for Loop B to become ready.
      "proceed"  — Loop B is ready; continue startup.
      "shutdown" — a stop was requested; shut down cleanly.
      "exit"     — Loop B's thread died before becoming ready (WS failure / crash);
                   exit non-zero so the supervisor restarts the process.
      "wait"     — Loop B is alive and still working (reconciling/connecting); wait.
    """
    if ready:
        return "proceed"
    if stopped:
        return "shutdown"
    if not thread_alive:
        return "exit"
    return "wait"


def main() -> None:
    config.validate()

    if not config.PAPER_TRADING and not config.DRY_RUN:
        if config.LIVE_CONFIRMED != "yes_i_understand_real_money":
            logger.critical("Live mode requires LIVE_CONFIRMED=yes_i_understand_real_money. Exiting.")
            sys.exit(1)

    _acquire_lock()

    # ── Runtime mode banner — unambiguous record in every log session ──────────
    logger.info("=" * 60)
    logger.info("RUNTIME MODE")
    logger.info("  PAPER_TRADING : %s", config.PAPER_TRADING)
    logger.info("  DRY_RUN       : %s", config.DRY_RUN)
    logger.info("  AUTO_EXECUTE  : %s", config.AUTO_EXECUTE)
    logger.info("  KILL_SWITCH   : %s", config.KILL_SWITCH)
    logger.info("=" * 60)

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

    # Shared queue for Loop A -> Loop B intent handoff (bounded to MAX_OPEN_POSITIONS)
    intent_queue: queue.Queue = queue.Queue(maxsize=config.MAX_OPEN_POSITIONS)

    # Boot Loop B in a thread (handles reconciliation + WebSocket)
    loop_b = LoopB(risk_state, position_tracker, intent_queue)
    loop_b_thread = threading.Thread(target=loop_b.start, name="LoopB", daemon=True)
    loop_b_thread.start()

    # Wait for Loop B to be reconciled and WebSocket-connected before doing anything
    # else. A genuine reconciliation HALT is handled INSIDE Loop B (it holds the
    # process alive and retries on a backoff), so we must NOT kill the process on a
    # fixed timeout — that is exactly what turned a halt into a restart storm. We keep
    # waiting while Loop B's thread is alive and working; we only exit if that thread
    # dies before becoming ready (a real WebSocket failure or crash).
    logger.info("Waiting for Loop B to reconcile and connect…")
    while True:
        ready = loop_b._ready.wait(timeout=15)
        action = _readiness_action(
            ready=ready,
            thread_alive=loop_b_thread.is_alive(),
            stopped=stop_event.is_set(),
        )
        if action == "proceed":
            break
        if action == "shutdown":
            logger.info("Shutdown requested before Loop B was ready. Stopping.")
            loop_b.stop()
            _release_lock()
            return
        if action == "exit":
            logger.critical(
                "Loop B thread exited before becoming ready "
                "(WebSocket failure or crash). Exiting for supervisor restart."
            )
            _release_lock()
            sys.exit(1)
        logger.info("Loop B not ready yet (reconciling/connecting) — continuing to wait.")

    # Session start alert — sent only after Loop B is confirmed ready
    close_time = market_calendar.market_close_time()
    tg.alert_session_start(
        equity=session_start_equity,
        loss_limit=risk_state.daily_loss_limit_usd,
        symbols=config.WATCHLIST,
        market_close=close_time.strftime("%H:%M"),
        feed=config.ALPACA_DATA_FEED,
    )

    # Start Telegram command listener (remote /smoketest trigger)
    from tools.smoke_test import smoke_runner

    def _run_smoketest() -> None:
        threading.Thread(
            target=smoke_runner.run,
            args=(loop_b, trading_client),
            name="SmokeTestRunner",
            daemon=True,
        ).start()

    tg.start_command_listener(_run_smoketest, stop_event)

    # Boot one Loop A thread per symbol — only starts after Loop B is ready
    for sym in config.WATCHLIST:
        la = LoopA(risk_state, position_tracker, intent_queue, ws_client=loop_b._ws)
        threading.Thread(
            target=la.run, args=(sym, stop_event), name=f"LoopA-{sym}", daemon=True
        ).start()

    # Midday monitor sends summary keyed to first symbol (representative)
    symbol = config.WATCHLIST[0]
    midday_thread = threading.Thread(
        target=_midday_monitor,
        args=(symbol, position_tracker, stop_event),
        name="MidSessionMonitor",
        daemon=True,
    )
    midday_thread.start()

    logger.info("Bot running. Symbols=%s Feed=%s Mode=%s",
                ",".join(config.WATCHLIST), config.ALPACA_DATA_FEED,
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
    def _next_market_open() -> datetime.datetime:
        check = datetime.datetime.now(_ET).date() + datetime.timedelta(days=1)
        for _ in range(14):
            if market_calendar.is_trading_day(check):
                return market_calendar.market_open_time(check)
            check += datetime.timedelta(days=1)
        raise RuntimeError("No trading day found in next 14 days.")

    while True:
        now = datetime.datetime.now(_ET)
        if market_calendar.is_trading_day(now.date()):
            open_t = market_calendar.market_open_time(now.date())
            close = market_calendar.market_close_time(now.date())
            if open_t <= now < close:
                main()

        try:
            next_open = _next_market_open()
            sleep_secs = max(60, (next_open - datetime.datetime.now(_ET)).total_seconds())
            logger.info("Next session %s ET — sleeping %.1fh.",
                        next_open.strftime("%Y-%m-%d %H:%M"), sleep_secs / 3600)
            deadline = time.monotonic() + sleep_secs
            while time.monotonic() < deadline:
                time.sleep(min(3600, deadline - time.monotonic()))
        except Exception as exc:
            logger.error("Sleep loop error: %s. Retrying in 60s.", exc)
            time.sleep(60)
