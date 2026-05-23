import multiprocessing
import uvicorn
import logging
from src.config import setup_logging, settings

# Setup root logger
logger = setup_logging("OneGuard.Main")

def start_backend():
    """Starts the algorithmic trading pipeline scheduler."""
    from src.pipeline import main as run_pipeline
    logger.info("Starting Backend Trading Pipeline...")
    run_pipeline()

def start_dashboard():
    """Starts the FastAPI web server serving the React dashboard and API."""
    logger.info("Starting FastAPI Dashboard...")
    uvicorn.run("dashboard.api:app", host="0.0.0.0", port=8000, log_level="info")

def start_telegram():
    """Starts the Telegram bot polling process."""
    if not settings.telegram_token:
        logger.warning("No Telegram token found. Telegram bot will not be started.")
        return
        
    from src.telegram_bot import create_bot_application
    app = create_bot_application()
    if app:
        logger.info("Starting Telegram Bot Polling...")
        app.run_polling()

if __name__ == "__main__":
    logger.info("Initializing OneGuard Unified Application Runner...")
    
    # Ensure database is initialized before starting workers
    from src.db import initialize_db
    if not initialize_db():
        logger.error("Failed to initialize database. Exiting.")
        exit(1)
        
    processes = []
    
    # 1. Trading Pipeline Process
    p_backend = multiprocessing.Process(target=start_backend, name="TradingPipeline")
    p_backend.start()
    processes.append(p_backend)
    
    # 2. Web Dashboard Process
    p_dashboard = multiprocessing.Process(target=start_dashboard, name="WebDashboard")
    p_dashboard.start()
    processes.append(p_dashboard)
    
    # 3. Telegram Bot Process
    if settings.telegram_token:
        p_telegram = multiprocessing.Process(target=start_telegram, name="TelegramBot")
        p_telegram.start()
        processes.append(p_telegram)
        
    try:
        # Wait for all processes to complete
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        logger.info("Shutdown signal received. Terminating all processes...")
        for p in processes:
            p.terminate()
            p.join()
        logger.info("Shutdown complete.")
