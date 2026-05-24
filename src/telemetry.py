"""
src/telemetry.py
================
OneGuard Telemetry Module — Telegram Push Notification Client.

Design Rules:
- All functions use a hard 5-second HTTP timeout.
- Failures are logged silently; they NEVER raise exceptions or stall the trading loop.
- Credentials are loaded from settings; missing credentials = silent no-op (safe for dev).
"""

import logging
import requests
from src.config import settings

logger = logging.getLogger("OneGuard.Telemetry")

# ──────────────────────────────────────────────
# Core Low-Level Sender
# ──────────────────────────────────────────────

def send_telegram_message(message: str) -> bool:
    """
    Sends a raw Markdown-formatted message to the configured Telegram chat.
    Safe to call from any thread: failures are logged, never re-raised.
    """
    token = settings.telegram_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        logger.debug(f"Telemetry skipped (credentials missing). Message: {message}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        # Hard 5-second timeout — will NEVER block the trading execution loop
        response = requests.post(url, json=payload, timeout=5.0)
        if response.status_code == 200:
            logger.info("Telegram notification sent successfully.")
            return True
        else:
            logger.warning(
                f"Telegram API error {response.status_code}: {response.text[:200]}"
            )
            return False
    except requests.exceptions.Timeout:
        logger.warning("Telegram notification timed out (5s). Skipping.")
        return False
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return False


# ──────────────────────────────────────────────
# High-Level Alert Formatters
# ──────────────────────────────────────────────

def alert_trade_entry(symbol: str, side: str, quantity: float, price: float, strategy: str) -> bool:
    """
    Sends a trade entry notification.

    Example output:
        🛡️ [OneGuard] TRADE ENTRY
        📈 BUY  BTC/USDT
        Strategy : RSI
        Qty      : 0.00042 BTC
        Price    : ~₹7,935,000.00 INR
    """
    emoji = "📈" if side.upper() == "BUY" else "📉"
    inr_price = price * settings.usdt_inr_rate
    message = (
        f"🛡️ *\\[OneGuard\\] TRADE ENTRY*\n"
        f"{emoji} *{side.upper()}*  `{symbol}`\n"
        f"Strategy : `{strategy}`\n"
        f"Qty      : `{quantity:.8f}`\n"
        f"Price    : `~₹{inr_price:,.2f} INR`"
    )
    logger.info(f"[TELEMETRY] Trade entry alert: {side} {symbol} via {strategy}")
    return send_telegram_message(message)


def alert_trade_exit(
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    pnl: float,
    strategy: str,
    reason: str = "signal",
) -> bool:
    """
    Sends a trade exit notification with realized PnL.

    reason: 'signal' | 'stop_loss' | 'take_profit'

    Example output:
        🛡️ [OneGuard] TRADE EXIT
        📉 SELL  BTC/USDT
        Strategy : RSI  |  Reason: stop_loss
        Qty      : 0.00042
        Price    : ~₹7,830,000.00 INR
        PnL      : -₹38.33 INR  ❌
    """
    emoji = "📈" if side.upper() == "BUY" else "📉"
    inr_price = price * settings.usdt_inr_rate
    inr_pnl = pnl * settings.usdt_inr_rate
    pnl_display = f"+₹{inr_pnl:,.2f} INR ✅" if pnl >= 0 else f"-₹{abs(inr_pnl):,.2f} INR ❌"
    reason_label = {"stop_loss": "🔴 Stop Loss", "take_profit": "🟢 Take Profit"}.get(
        reason, "📊 Signal"
    )

    message = (
        f"🛡️ *\\[OneGuard\\] TRADE EXIT*\n"
        f"{emoji} *{side.upper()}*  `{symbol}`\n"
        f"Strategy : `{strategy}`  |  Reason: {reason_label}\n"
        f"Qty      : `{quantity:.8f}`\n"
        f"Price    : `~₹{inr_price:,.2f} INR`\n"
        f"PnL      : `{pnl_display}`"
    )
    logger.info(f"[TELEMETRY] Trade exit alert: {side} {symbol} | PnL {pnl:+.4f} | reason={reason}")
    return send_telegram_message(message)


def alert_drawdown_halt(weekly_pnl: float, limit: float) -> bool:
    """
    Notifies when the weekly drawdown limit is breached and trading is halted.

    Example output:
        🛡️ [OneGuard] ⛔ DRAWDOWN HALT
        Weekly PnL has hit the safety limit.
        Realized : -₹1,318.00 INR
        Limit    : -₹1,250.00 INR
        Status   : TRADING SUSPENDED
    """
    inr_weekly = weekly_pnl * settings.usdt_inr_rate
    inr_limit = limit * settings.usdt_inr_rate
    message = (
        f"🛡️ *\\[OneGuard\\] ⛔ DRAWDOWN HALT*\n"
        f"Weekly PnL has breached the safety limit.\n"
        f"Realized : `₹{inr_weekly:,.2f} INR`\n"
        f"Limit    : `₹{inr_limit:,.2f} INR`\n"
        f"Status   : *TRADING SUSPENDED* 🔴"
    )
    logger.warning(f"[TELEMETRY] Drawdown halt alert | weekly_pnl={weekly_pnl:.2f} limit={limit:.2f}")
    return send_telegram_message(message)


def alert_cooldown_active(symbol: str, minutes_remaining: float) -> bool:
    """
    Notifies when loss cooldown is active and a new trade was blocked.

    Example output:
        🛡️ [OneGuard] ⏸️ COOLDOWN ACTIVE
        Trade blocked for BTC/USDT
        Cooldown remaining: ~28 min
    """
    message = (
        f"🛡️ *\\[OneGuard\\] ⏸️ COOLDOWN ACTIVE*\n"
        f"Trade blocked for `{symbol}`\n"
        f"Cooldown remaining: `~{minutes_remaining:.0f} min`"
    )
    logger.info(f"[TELEMETRY] Cooldown alert | {symbol} | {minutes_remaining:.0f}m remaining")
    return send_telegram_message(message)


def alert_system_error(context: str, error: Exception) -> bool:
    """
    Sends a system error notification for unexpected exceptions.

    Example output:
        🛡️ [OneGuard] 🚨 SYSTEM ERROR
        Context : pipeline_cycle
        Error   : ConnectionError: ...
    """
    message = (
        f"🛡️ *\\[OneGuard\\] 🚨 SYSTEM ERROR*\n"
        f"Context : `{context}`\n"
        f"Error   : `{type(error).__name__}: {str(error)[:200]}`"
    )
    logger.error(f"[TELEMETRY] System error alert | context={context} | {error}")
    return send_telegram_message(message)


def alert_clock_drift(offset_ms: int) -> bool:
    """
    Sends a clock drift alert warning when the system clock drifts.
    """
    message = (
        f"🛡️ *\\[OneGuard\\] ⚠️ CLOCK SYNC WARNING*\n"
        f"Local host clock drifted by `{offset_ms} ms` from exchange server.\n"
        f"Threshold: `1000 ms`\n"
        f"Status: *PIPELINE CYCLE SUSPENDED* 🛑\n"
        f"Please synchronize your system clock."
    )
    logger.warning(f"[TELEMETRY] Clock drift alert | offset={offset_ms} ms")
    return send_telegram_message(message)


def alert_bot_startup(mode: str, symbols: list) -> bool:
    """
    Sends a startup notification when the bot initializes.

    Example output:
        🛡️ [OneGuard] 🚀 BOT STARTED
        Mode     : sandbox
        Watching : BTC/USDT, ETH/USDT
    """
    symbol_list = ", ".join(symbols)
    message = (
        f"🛡️ *\\[OneGuard\\] 🚀 BOT STARTED*\n"
        f"Mode     : `{mode}`\n"
        f"Watching : `{symbol_list}`"
    )
    logger.info(f"[TELEMETRY] Bot startup alert | mode={mode} | symbols={symbol_list}")
    return send_telegram_message(message)
