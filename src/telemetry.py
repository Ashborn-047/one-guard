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
        Price    : ~$95,200.00
    """
    emoji = "📈" if side.upper() == "BUY" else "📉"
    message = (
        f"🛡️ *\\[OneGuard\\] TRADE ENTRY*\n"
        f"{emoji} *{side.upper()}*  `{symbol}`\n"
        f"Strategy : `{strategy}`\n"
        f"Qty      : `{quantity}`\n"
        f"Price    : `~${price:,.2f}`"
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
        Price    : ~$94,100.00
        PnL      : -$0.46  ❌
    """
    emoji = "📈" if side.upper() == "BUY" else "📉"
    pnl_display = f"+${pnl:,.2f} ✅" if pnl >= 0 else f"-${abs(pnl):,.2f} ❌"
    reason_label = {"stop_loss": "🔴 Stop Loss", "take_profit": "🟢 Take Profit"}.get(
        reason, "📊 Signal"
    )

    message = (
        f"🛡️ *\\[OneGuard\\] TRADE EXIT*\n"
        f"{emoji} *{side.upper()}*  `{symbol}`\n"
        f"Strategy : `{strategy}`  |  Reason: {reason_label}\n"
        f"Qty      : `{quantity}`\n"
        f"Price    : `~${price:,.2f}`\n"
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
        Realized : -$15.82
        Limit    : -$15.00
        Status   : TRADING SUSPENDED
    """
    message = (
        f"🛡️ *\\[OneGuard\\] ⛔ DRAWDOWN HALT*\n"
        f"Weekly PnL has breached the safety limit.\n"
        f"Realized : `${weekly_pnl:,.2f}`\n"
        f"Limit    : `${limit:,.2f}`\n"
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
