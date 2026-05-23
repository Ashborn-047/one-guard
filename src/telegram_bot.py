import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from src.config import settings

logger = logging.getLogger("OneGuard.Telegram")

def verify_user(update: Update) -> bool:
    """Check if the user invoking the command is the authorized user."""
    chat_id = str(update.effective_chat.id)
    if chat_id != settings.telegram_chat_id:
        logger.warning(f"Unauthorized access attempt from Chat ID: {chat_id}")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    await update.message.reply_text(
        "🛡️ Welcome to OneGuard Trading Bot.\n\n"
        "Available Commands:\n"
        "/status - Check bot status, balance, and active trades\n"
        "/pause - Emergency halt all trades\n"
        "/resume - Resume normal trading operations\n"
        "/trade - Force execute a trade evaluation cycle\n"
        "/mocktrade <buy|sell> <symbol> - Execute a manual mock/simulated trade"
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    mode = "🔴 LIVE" if settings.is_live else "🟢 SANDBOX"
    status = "⏸️ PAUSED" if settings.emergency_halt else "▶️ ACTIVE"
    
    await update.message.reply_text(
        f"📊 **OneGuard Status**\n"
        f"Mode: {mode}\n"
        f"Status: {status}\n"
        f"Max Trade Size: {settings.max_position_size} USDT"
    )

async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    settings.emergency_halt = True
    await update.message.reply_text("🛑 **EMERGENCY HALT ACTIVATED**\nAll automated trading has been paused.")

async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    settings.emergency_halt = False
    await update.message.reply_text("▶️ **TRADING RESUMED**\nAutomated execution is now active.")

async def trade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    
    if settings.emergency_halt:
        await update.message.reply_text("❌ Cannot execute trade: System is currently PAUSED.")
        return
        
    await update.message.reply_text("⚙️ Triggering manual trade evaluation cycle. Check logs for results.")
    
    # We trigger the pipeline asynchronously or directly if we import it.
    # Since telegram polling runs alongside our scheduler, we can run it.
    try:
        from src.pipeline import run_pipeline_cycle, initialize_exchange
        exchange = initialize_exchange()
        run_pipeline_cycle(exchange)
        await update.message.reply_text("✅ Manual cycle completed.")
    except Exception as e:
        logger.error(f"Error during manual trade cycle: {e}")
        await update.message.reply_text(f"❌ Error executing cycle: {e}")

async def mocktrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    
    if settings.emergency_halt:
        await update.message.reply_text("❌ Cannot execute mock trade: System is currently PAUSED.")
        return
        
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "⚠️ Usage: `/mocktrade <buy|sell> <symbol>`\n"
            "Example: `/mocktrade buy BTC/USDT` or `/mocktrade sell BTC/USDT`"
        )
        return
        
    side = args[0].upper()
    symbol = args[1].upper()
    
    if side not in ("BUY", "SELL"):
        await update.message.reply_text("❌ Invalid side. Must be either `buy` or `sell`.")
        return
        
    await update.message.reply_text(f"⚙️ Executing mock {side} order for {symbol}...")
    
    try:
        from src.pipeline import initialize_exchange
        from src.execution import execute_market_order
        
        exchange = initialize_exchange()
        order = execute_market_order(exchange, symbol, side, "Mock_Manual")
        
        if order:
            order_id = order.get('id', 'unknown')
            price = order.get('price', 0.0)
            amount = order.get('amount', 0.0)
            await update.message.reply_text(
                f"✅ Mock Trade Executed Successfully!\n\n"
                f"Order ID: `{order_id}`\n"
                f"Symbol: `{symbol}`\n"
                f"Side: `{side}`\n"
                f"Amount: `{amount}`\n"
                f"Price: `{price}` USDT"
            )
        else:
            await update.message.reply_text("❌ Mock trade execution rejected by risk engine or failed. Check logs.")
    except Exception as e:
        logger.error(f"Error during mock trade execution: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {e}")

def create_bot_application() -> Application:
    if not settings.telegram_token:
        logger.warning("No Telegram token provided. Bot will not start.")
        return None
        
    app = Application.builder().token(settings.telegram_token).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("trade", trade_cmd))
    app.add_handler(CommandHandler("mocktrade", mocktrade_cmd))
    
    return app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = create_bot_application()
    if app:
        logger.info("Starting Telegram Bot (Polling Mode)...")
        app.run_polling()
