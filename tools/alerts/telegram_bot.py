"""
Telegram alert client. All bot communication goes through this module.
Uses the Telegram Bot HTTP API directly — no SDK dependency.
Fails gracefully: a Telegram error never crashes the trading loop.
"""
import logging
import threading
import time
from typing import Any, Callable

import requests

import config

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# Alert throttle state (module-level, in-process only)
_last_reconnect_alert: float = 0.0
_last_silence_alert: float = 0.0
_RECONNECT_THROTTLE_SEC = 300   # 5 minutes
_SILENCE_THROTTLE_SEC = 600     # 10 minutes


def _esc(s: Any) -> str:
    """Escape a value for Telegram HTML parse mode. Returns 'N/A' for None."""
    if s is None:
        return "N/A"
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_price(v: Any) -> str:
    """Format a price safely; returns 'N/A' if None or non-numeric."""
    if v is None:
        return "N/A"
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_pct(v: Any) -> str:
    """Format a percentage safely; returns 'N/A' if None or non-numeric."""
    if v is None:
        return "N/A"
    try:
        return f"{float(v):+.4f}%"
    except (TypeError, ValueError):
        return "N/A"


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send *text* to the configured chat. Returns True on success.
    Never raises — logs the error and returns False instead.
    Truncates to Telegram's 4096-char limit automatically.
    """
    config.load()
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping alert.")
        return False

    text = text[:4096]
    url = _BASE.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            logger.error("Telegram API error %d: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        logger.error("Telegram request failed: %s", exc)
        return False


# ── Formatted message builders ─────────────────────────────────────────────────

def send_raw(text: str) -> bool:
    return _send(text)


def alert_signal(event: dict) -> bool:
    sig = _esc(event.get("signal_type"))
    symbol = _esc(event.get("symbol"))
    price = _fmt_price(event.get("bar_close"))
    macd_h = event.get("macd_hist")
    rsi = event.get("rsi_14")
    feed = _esc(event.get("data_feed"))
    rel_vol = event.get("relative_volume")
    vol_ok = event.get("relative_volume_ok")
    macd_str = f"{float(macd_h):.5f}" if macd_h is not None else "N/A"
    rsi_str = f"{float(rsi):.1f}" if rsi is not None else "N/A"
    rel_vol_str = f"{float(rel_vol):.2f}x" if rel_vol is not None else "N/A"
    vol_flag = "✓" if vol_ok else "✗"

    text = (
        f"📡 <b>SIGNAL: {sig}</b> {symbol}\n"
        f"Price:       ~{price}\n"
        f"MACD hist:   {macd_str}\n"
        f"RSI(14):     {rsi_str}\n"
        f"Rel volume:  {rel_vol_str} {vol_flag}\n"
        f"Feed:        {feed.upper() if feed != 'N/A' else feed}\n"
    )
    return _send(text)


def alert_order_submitted(client_order_id: str, symbol: str, side: str,
                          qty: float, limit_price: float, notional: float,
                          prefix: str = "") -> bool:
    text = (
        f"{prefix}"
        f"✅ <b>ORDER SUBMITTED</b>\n"
        f"Symbol:  {_esc(symbol)}\n"
        f"Side:    {_esc(side).upper()}\n"
        f"Qty:     {_fmt_price(qty).replace('$', '') if qty is not None else 'N/A'} shares\n"
        f"Limit:   {_fmt_price(limit_price)}\n"
        f"Notional: {_fmt_price(notional)}\n"
        f"ID:      <code>{_esc(client_order_id)}</code>"
    )
    return _send(text)


def alert_fill(symbol: str, side: str, qty: float, fill_price: float,
               expected_price: float, slippage_pct: float) -> bool:
    direction = "🟢" if side == "buy" else "🔴"
    qty_str = f"{float(qty):.4f}" if qty is not None else "N/A"
    text = (
        f"{direction} <b>FILL</b> {_esc(symbol)} {_esc(side).upper()}\n"
        f"Qty:       {qty_str}\n"
        f"Fill:      {_fmt_price(fill_price)}\n"
        f"Expected:  {_fmt_price(expected_price)}\n"
        f"Slippage:  {_fmt_pct(slippage_pct)}"
    )
    return _send(text)


def alert_risk_halt(reason: str, session_loss: float, limit: float) -> bool:
    text = (
        f"🛑 <b>RISK HALT — {_esc(reason)}</b>\n"
        f"Session loss: {_fmt_price(session_loss)}\n"
        f"Limit:        {_fmt_price(limit)}\n"
        f"All new entries blocked for remainder of session."
    )
    return _send(text)


def alert_daily_limit_hit(session_loss: float, limit: float) -> bool:
    return alert_risk_halt("DAILY LOSS LIMIT HIT", session_loss, limit)


def alert_kill_switch() -> bool:
    return _send("🔴 <b>KILL SWITCH ACTIVE</b> — KILL_SWITCH=true in .env. No new entries will be submitted.")


def alert_intent_expired(symbol: str, signal_type: str) -> bool:
    return _send(f"⏱ Intent expired — no action taken.\n{_esc(signal_type)} {_esc(symbol)}")


def alert_intent_approved(symbol: str, signal_type: str) -> bool:
    return _send(f"✅ Intent approved — submitting {_esc(signal_type)} {_esc(symbol)}.")


def alert_intent_rejected_by_user(symbol: str, signal_type: str) -> bool:
    return _send(f"❌ Intent rejected by user — {_esc(signal_type)} {_esc(symbol)} discarded.")


def alert_ws_reconnecting(attempt: int) -> bool:
    global _last_reconnect_alert
    now = time.time()
    if now - _last_reconnect_alert < _RECONNECT_THROTTLE_SEC:
        return False
    _last_reconnect_alert = now
    return _send(f"⚠️ WebSocket disconnected. Reconnect attempt {attempt}…")


def alert_ws_failed(max_attempts: int) -> bool:
    return _send(
        f"🚨 <b>WS RECONNECT FAILED after {max_attempts} attempts.</b>\n"
        "Bot is NOT receiving broker events. Manual check required."
    )


def alert_bot_silent() -> bool:
    global _last_silence_alert
    now = time.time()
    if now - _last_silence_alert < _SILENCE_THROTTLE_SEC:
        return False
    _last_silence_alert = now
    return _send("🚨 <b>BOT SILENT</b> — heartbeat is stale. Check the process immediately.")


def alert_reconciliation_mismatch(details: str) -> bool:
    return _send(
        f"🚨 <b>RECONCILIATION MISMATCH</b>\n{_esc(details)}\n"
        "New order intents are HALTED. Resolve manually before restarting."
    )


def alert_duplicate_scheduler(pid: int) -> bool:
    return _send(
        f"⚠️ <b>DUPLICATE SCHEDULER DETECTED</b>\n"
        f"Another instance is already running (PID {pid}). Exiting."
    )


def alert_session_start(equity: float, loss_limit: float, symbols: list[str],
                        market_close: str, feed: str) -> bool:
    mode = "PAPER" if config.PAPER_TRADING else "LIVE"
    if config.DRY_RUN:
        mode += " [DRY RUN]"
    auto = "ON — trades execute automatically" if config.AUTO_EXECUTE else "OFF — manual approval required"
    kill = "🔴 ACTIVE" if config.KILL_SWITCH else "off"
    text = (
        f"🟢 <b>SESSION START</b>\n"
        f"Equity:       {_fmt_price(equity)}\n"
        f"Loss limit:   {_fmt_price(loss_limit)} ({config.MAX_DAILY_LOSS_PCT*100:.1f}%)\n"
        f"Symbols:      {_esc(', '.join(symbols))}\n"
        f"Close:        {_esc(market_close)} ET\n"
        f"Feed:         {_esc(feed).upper()}\n"
        f"PAPER_TRADING: {config.PAPER_TRADING}\n"
        f"DRY_RUN:      {config.DRY_RUN}\n"
        f"AUTO_EXECUTE: {auto}\n"
        f"KILL_SWITCH:  {kill}\n"
        f"Mode:         {mode}"
    )
    return _send(text)


def daily_report(summary: dict) -> bool:
    pnl = summary.get("total_pnl", 0)
    sign = "+" if pnl >= 0 else ""
    text = (
        f"📊 <b>DAILY REPORT — {_esc(summary.get('date'))}</b>\n"
        f"Signals:   {summary.get('total_signals', 0)}\n"
        f"Taken:     {summary.get('taken', 0)}\n"
        f"Rejected:  {summary.get('rejected', 0)}\n"
        f"Fills:     {summary.get('fills', 0)}\n"
        f"PnL:       {sign}${pnl:.2f}"
    )
    return _send(text)


def send_midday_status(symbol: str, summary: dict, position) -> bool:
    scans = summary.get("total_signals", 0)
    taken = summary.get("taken", 0)
    rejected = summary.get("rejected", 0)
    pnl = summary.get("total_pnl", 0.0)
    sign = "+" if pnl >= 0 else ""

    if position is not None:
        pos_str = (
            f"{_esc(symbol)} @ {_fmt_price(position.entry_price)} "
            f"(stop={_fmt_price(position.stop_price)}, tp={_fmt_price(position.take_profit)})"
        )
    else:
        pos_str = "None"

    text = (
        f"🕛 <b>MID-SESSION — 12:00 ET</b>\n"
        f"Scans:      {scans}\n"
        f"Signals:    {taken} taken | {rejected} rejected\n"
        f"Position:   {pos_str}\n"
        f"PnL:        {sign}${pnl:.2f}\n"
        f"Bot running."
    )
    return _send(text)


def start_command_listener(
    on_smoketest: "Callable[[], None]",
    stop_event: threading.Event,
) -> None:
    """
    Start a background thread that long-polls the Telegram Bot API for incoming commands.
    Only /smoketest from the configured TELEGRAM_CHAT_ID is acted on.
    All other senders are silently ignored.
    Thread exits when stop_event is set.
    """
    def _poll() -> None:
        config.load()
        token = config.TELEGRAM_BOT_TOKEN
        chat_id = str(config.TELEGRAM_CHAT_ID)
        base_url = f"https://api.telegram.org/bot{token}"
        offset = 0

        logger.info("Telegram command listener started (polling).")
        while not stop_event.is_set():
            try:
                resp = requests.get(
                    f"{base_url}/getUpdates",
                    params={"timeout": 30, "offset": offset},
                    timeout=35,
                )
                if not resp.ok:
                    logger.warning("getUpdates error %d: %s", resp.status_code, resp.text[:100])
                    stop_event.wait(timeout=5)
                    continue

                updates = resp.json().get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    sender_id = str(message.get("chat", {}).get("id", ""))
                    text = (message.get("text") or "").strip()

                    if sender_id != chat_id:
                        continue  # silently ignore unknown senders

                    if text.startswith("/smoketest"):
                        logger.info("Received /smoketest command from chat_id=%s", sender_id)
                        threading.Thread(target=on_smoketest, daemon=True).start()

            except requests.RequestException as exc:
                logger.warning("Command listener request error: %s", exc)
                stop_event.wait(timeout=5)
            except Exception as exc:
                logger.exception("Command listener unexpected error: %s", exc)
                stop_event.wait(timeout=5)

        logger.info("Telegram command listener stopped.")

    thread = threading.Thread(target=_poll, name="TelegramCommandListener", daemon=True)
    thread.start()


if __name__ == "__main__":
    config.load()
    ok = send_raw("✅ Telegram connection test — trading bot is online.")
    print("Sent:", ok)
