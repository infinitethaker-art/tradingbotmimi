"""
Hard risk guardrails. Every check here is a gate, not a suggestion.
All checks run before any order is submitted. A single failure blocks the order.

Rejection reasons map to the DB schema's rejection_reason field:
  KILL_SWITCH        — manual override active
  DAILY_LIMIT        — session loss exceeds MAX_DAILY_LOSS_PCT × start equity
  MAX_POSITIONS      — already at MAX_OPEN_POSITIONS
  DAYTRADE_LIMIT     — daytrade_count from broker >= MAX_DAYTRADE_COUNT
  TIME_WINDOW        — signal is outside the allowed trading window

DRY_RUN is handled by the caller (loop_a) before calling risk checks.
DRY_RUN signals are stamped disposition='DRY_RUN' by the caller and never reach here.
"""
import datetime
import logging
from dataclasses import dataclass
from typing import Any

import config

logger = logging.getLogger(__name__)


@dataclass
class RiskResult:
    passed: bool
    rejection_reason: str | None  # None if passed
    details: str = ""


class RiskState:
    """
    Mutable session state for risk tracking.
    One instance lives for the duration of a trading session.
    Reset on each startup after reconciliation.
    """

    def __init__(self, session_start_equity: float) -> None:
        self.session_start_equity = session_start_equity
        self.daily_loss_limit_usd = session_start_equity * config.MAX_DAILY_LOSS_PCT
        self.halted = False  # set True when daily limit is hit — stays True for the session
        self._open_positions: int = 0
        self._daytrade_count: int = 0

    def set_open_positions(self, count: int) -> None:
        self._open_positions = count

    def set_daytrade_count(self, count: int) -> None:
        self._daytrade_count = count
        if count >= config.MAX_DAYTRADE_COUNT:
            logger.warning(
                "Day trade count %d >= threshold %d — new entries blocked.",
                count, config.MAX_DAYTRADE_COUNT,
            )

    def session_loss(self, current_equity: float) -> float:
        return self.session_start_equity - current_equity

    def summary(self) -> dict[str, Any]:
        return {
            "session_start_equity": self.session_start_equity,
            "daily_loss_limit_usd": round(self.daily_loss_limit_usd, 2),
            "daily_loss_limit_pct": config.MAX_DAILY_LOSS_PCT,
            "halted": self.halted,
            "open_positions": self._open_positions,
            "daytrade_count": self._daytrade_count,
        }


def check_entry(
    signal_event: dict[str, Any],
    risk_state: RiskState,
    current_equity: float,
) -> RiskResult:
    """
    Run all entry checks. Returns the first failure found, or a pass.
    Order of checks matters — cheapest/most critical first.

    DRY_RUN mode is handled before this function is called; it never reaches here.
    """
    config.load()

    # 1. Kill switch — always first; blocks new entries only
    if config.KILL_SWITCH:
        return RiskResult(False, "KILL_SWITCH", "KILL_SWITCH env var is true.")

    # 2. Session halted from prior daily loss limit breach
    if risk_state.halted:
        return RiskResult(False, "DAILY_LIMIT", "Session already halted by daily loss limit.")

    # 3. Current session loss check
    loss = risk_state.session_loss(current_equity)
    if loss >= risk_state.daily_loss_limit_usd:
        risk_state.halted = True
        logger.critical(
            "Daily loss limit hit: loss=$%.2f >= limit=$%.2f. Halting session.",
            loss, risk_state.daily_loss_limit_usd,
        )
        return RiskResult(
            False, "DAILY_LIMIT",
            f"Session loss ${loss:.2f} >= limit ${risk_state.daily_loss_limit_usd:.2f}."
        )

    # 4. Max open positions
    if risk_state._open_positions >= config.MAX_OPEN_POSITIONS:
        return RiskResult(
            False, "MAX_POSITIONS",
            f"Open positions={risk_state._open_positions} >= max={config.MAX_OPEN_POSITIONS}."
        )

    # 5. Day trade count
    if risk_state._daytrade_count >= config.MAX_DAYTRADE_COUNT:
        return RiskResult(
            False, "DAYTRADE_LIMIT",
            f"daytrade_count={risk_state._daytrade_count} >= limit={config.MAX_DAYTRADE_COUNT}."
        )

    # 6. Time window check (belt-and-suspenders — signal.py no longer checks this)
    from tools.data.market_calendar import is_within_trading_window
    if not is_within_trading_window(
        start_offset_min=config.TRADE_START_OFFSET_MIN,
        end_offset_min=config.TRADE_END_OFFSET_MIN,
    ):
        return RiskResult(False, "TIME_WINDOW", "Outside tradeable time window.")

    return RiskResult(True, None, "All checks passed.")


def check_exit(
    signal_event: dict[str, Any],
    risk_state: RiskState,
    current_equity: float,
) -> RiskResult:
    """
    Exit orders have a minimal check.
    Kill switch logs a warning but does NOT block exits — positions must be closeable.
    DRY_RUN does NOT block exits.
    """
    config.load()

    if config.KILL_SWITCH:
        logger.warning(
            "KILL_SWITCH is active but allowing exit order to proceed for %s.",
            signal_event.get("symbol", "?"),
        )

    return RiskResult(True, None, "Exit checks passed.")


def apply_result(signal_event: dict[str, Any], result: RiskResult) -> dict[str, Any]:
    """
    Stamp the signal event with the risk check outcome.
    Returns the modified event dict (mutates in place and returns for chaining).

    Passed check -> disposition='SUBMITTED' (cleared for order submission).
    Failed check -> disposition='REJECTED' with rejection_reason set.

    Mode (paper vs live) is a separate account-level fact, not a disposition value.
    """
    if result.passed:
        signal_event["disposition"] = "SUBMITTED"
        signal_event["rejection_reason"] = None
    else:
        signal_event["disposition"] = "REJECTED"
        signal_event["rejection_reason"] = result.rejection_reason
    return signal_event
