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
        "/trade - Force execute a trade evaluation cycle"
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
        from src.pipeline import run_pipeline_cycle
        # We need to run the blocking pipeline cycle in a thread or just call it directly 
        # since python-telegram-bot handles async, but pipeline might be sync.
        # Run it synchronously for now, or just notify.
        run_pipeline_cycle()
        await update.message.reply_text("✅ Manual cycle completed.")
    except Exception as e:
        logger.error(f"Error during manual trade cycle: {e}")
        await update.message.reply_text(f"❌ Error executing cycle: {e}")

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
    
    return app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = create_bot_application()
    if app:
        logger.info("Starting Telegram Bot (Polling Mode)...")
        app.run_polling()
