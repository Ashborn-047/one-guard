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
        "/mocktrade <buy|sell> <symbol> - Execute a manual mock/simulated trade\n"
        "/leaderboard - View strategy comparison performance\n"
        "/papertrades [strat] - View recent paper trades"
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    mode = "🔴 LIVE" if settings.is_live else "🟢 SANDBOX"
    status = "⏸️ PAUSED" if settings.emergency_halt else "▶️ ACTIVE"
    
    # Calculate paper trading stats
    paper_info = ""
    try:
        from src.db import get_strategy_performance_data
        perf_data = get_strategy_performance_data()
        if perf_data:
            total_pnl = sum(d["total_pnl"] for d in perf_data)
            inr_pnl = total_pnl * settings.usdt_inr_rate
            pnl_str = f"+₹{inr_pnl:,.2f} INR" if total_pnl >= 0 else f"-₹{abs(inr_pnl):,.2f} INR"
            total_trades = sum(d["total_trades"] for d in perf_data)
            total_wins = sum(d.get("winning_trades", 0) for d in perf_data)
            total_losses = sum(d.get("losing_trades", 0) for d in perf_data)
            paper_info = f"\n📈 **Paper Trading Stats**\nTotal Trades: {total_trades} (W:{total_wins} / L:{total_losses})\nNet PnL: {pnl_str}"
    except Exception as e:
        logger.error(f"Error reading paper stats for status: {e}")
        
    await update.message.reply_text(
        f"📊 **OneGuard Status**\n"
        f"Mode: {mode}\n"
        f"Status: {status}\n"
        f"Max Trade Size: ₹{settings.max_position_size * settings.usdt_inr_rate:.2f} INR"
        f"{paper_info}"
    )

async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    try:
        from src.paper_engine import get_leaderboard
        leaderboard = get_leaderboard()
        await update.message.reply_text(leaderboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error fetching leaderboard: {e}")

async def papertrades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    try:
        from src.db import get_recent_paper_trades
        import datetime
        
        args = context.args
        strategy = args[0].upper() if args else None
        
        trades = get_recent_paper_trades(limit=10, strategy=strategy)
        if not trades:
            await update.message.reply_text("📝 No paper trades found.")
            return
            
        lines = ["📝 *Recent Paper Trades*\n"]
        for t in trades:
            ts = t["timestamp"]
            ts_sec = ts / 1000.0 if ts > 1e11 else ts
            dt_str = datetime.datetime.fromtimestamp(ts_sec, datetime.timezone.utc).strftime("%m-%d %H:%M")
            pnl_display = ""
            if t["pnl"] is not None:
                pnl_display = f" | PnL: `+${t['pnl']:,.4f}`" if t["pnl"] >= 0 else f" | PnL: `-${abs(t['pnl']):,.4f}`"
            
            lines.append(
                f"• `[{dt_str}]` *{t['side']}* `{t['symbol']}`\n"
                f"  Strat: `{t['strategy']}` | Price: `${t['price']:,.2f}`{pnl_display}"
            )
            
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching paper trades: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {e}")

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
            inr_price = price * settings.usdt_inr_rate
            await update.message.reply_text(
                f"✅ Mock Trade Executed Successfully!\n\n"
                f"Order ID: `{order_id}`\n"
                f"Symbol: `{symbol}`\n"
                f"Side: `{side}`\n"
                f"Amount: `{amount}`\n"
                f"Price: `₹{inr_price:,.2f}` INR"
            )
        else:
            await update.message.reply_text("❌ Mock trade execution rejected by risk engine or failed. Check logs.")
    except Exception as e:
        logger.error(f"Error during mock trade execution: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {e}")

async def resetstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    try:
        from src.db import reset_strategy_performance
        success = reset_strategy_performance()
        if success:
            await update.message.reply_text("✅ **Leaderboard Reset Successful**\nAll old paper trading strategy statistics have been wiped. The leaderboard will now start fresh from the next completed trade!")
        else:
            await update.message.reply_text("❌ Failed to reset strategy performance. Check logs.")
    except Exception as e:
        logger.error(f"Error resetting stats: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not verify_user(update): return
    help_text = (
        "🤖 **OneGuard Bot Commands**\n\n"
        "**System & Status**\n"
        "🔹 `/start` - Start the bot\n"
        "🔹 `/help` - List all commands\n"
        "🔹 `/status` - Check bot status and overall PnL\n"
        "🔹 `/pause` - Pause the trading engine\n"
        "🔹 `/resume` - Resume the trading engine\n\n"
        "**Trading & Performance**\n"
        "🔹 `/leaderboard` - View performance of each strategy\n"
        "🔹 `/papertrades` - View the active/open paper trades\n"
        "🔹 `/resetstats` - Wipe historical performance metrics\n\n"
        "**Manual Controls**\n"
        "🔹 `/trade <symbol> <side> <amount>` - Execute real trade\n"
        "🔹 `/mocktrade <symbol> <side> <amount>` - Execute paper trade\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

def create_bot_application() -> Application:
    if not settings.telegram_token:
        logger.warning("No Telegram token provided. Bot will not start.")
        return None
        
    app = Application.builder().token(settings.telegram_token).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("trade", trade_cmd))
    app.add_handler(CommandHandler("mocktrade", mocktrade_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    app.add_handler(CommandHandler("papertrades", papertrades_cmd))
    app.add_handler(CommandHandler("resetstats", resetstats_cmd))
    
    return app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = create_bot_application()
    if app:
        logger.info("Starting Telegram Bot (Polling Mode)...")
        app.run_polling()
