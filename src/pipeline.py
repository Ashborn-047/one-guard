import time
import sys
import argparse
import logging
import pandas as pd
import ccxt
from apscheduler.schedulers.blocking import BlockingScheduler
from src.config import settings
from src.db import initialize_db, save_candles, get_latest_candles, save_indicators
from src.indicators import calculate_technical_indicators
from src.strategies import evaluate_all_strategies
from src.execution import check_and_execute_exits, execute_market_order
from src.telemetry import alert_bot_startup, alert_system_error, alert_clock_drift

logger = logging.getLogger("OneGuard.Pipeline")

# Pinned symbols and timeframe from masterplan
TRADING_SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAME = "15m"
CANDLE_LIMIT = 100

def initialize_exchange() -> ccxt.Exchange:
    """
    Initializes the CCXT exchange instance.
    """
    exchange_class = getattr(ccxt, "binance")
    exchange_config = {
        'enableRateLimit': True,
        'timeout': 15000,
    }
    
    if settings.api_key and settings.secret_key:
        exchange_config['apiKey'] = settings.api_key
        exchange_config['secret'] = settings.secret_key
        
    exchange = exchange_class(exchange_config)
    
    if settings.is_sandbox:
        exchange.set_sandbox_mode(True)
        
    return exchange


def run_pipeline_cycle(exchange: ccxt.Exchange):
    """
    Executes a single data ingestion and calculation iteration.
    For each target symbol, it fetches raw candles, saves them to SQLite,
    calculates technical indicators, and saves indicator results.
    """
    logger.info("Starting pipeline ingestion cycle...")
    
    # Safety Check: check clock synchronization
    try:
        start_time = time.time()
        exchange_time = exchange.fetch_time()
        roundtrip = (time.time() - start_time) * 1000
        system_time_ms = int(time.time() * 1000)
        time_diff = system_time_ms - exchange_time
        
        logger.info(f"System Clock Offset: {time_diff} ms (Roundtrip: {roundtrip:.1f}ms)")
        
        if abs(time_diff) > 1000:
            logger.error(
                f"CLOCK DRIFT DETECTED: Local system clock offset is {time_diff}ms. "
                "Exceeds 1000ms threshold. Trading execution suspended to prevent signature errors."
            )
            alert_clock_drift(time_diff)
            return
    except Exception as e:
        logger.error(f"Failed to verify clock synchronization: {e}")
    
    # Safety Check: check if emergency halt is active
    if settings.emergency_halt:
        logger.warning("Pipeline cycle skipped: EMERGENCY_HALT is active.")
        return

    for symbol in TRADING_SYMBOLS:
        try:
            logger.info(f"Ingesting data for {symbol} ({TIMEFRAME} timeframe)...")
            
            # 1. Fetch OHLCV candles from Exchange
            # CCXT fetch_ohlcv returns list of: [timestamp, open, high, low, close, volume]
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
            if not ohlcv:
                logger.warning(f"No candles returned from exchange for {symbol}.")
                continue
                
            logger.info(f"Fetched {len(ohlcv)} candles from exchange.")
            
            # 2. Save raw candles to database
            db_success = save_candles(symbol, ohlcv)
            if not db_success:
                logger.error(f"Failed to persist candles for {symbol} to database.")
                continue

            # 3. Retrieve historical candles from DB to compute indicators
            # This ensures we work with a clean, continuous dataset
            db_candles = get_latest_candles(symbol, limit=CANDLE_LIMIT)
            if not db_candles:
                logger.warning(f"No candles found in database for {symbol}.")
                continue
                
            # Convert to DataFrame
            df = pd.DataFrame(db_candles)
            
            # 4. Calculate technical indicators (RSI, EMAs, Bollinger Bands)
            latest_indicators = calculate_technical_indicators(df)
            if latest_indicators is None:
                logger.warning(f"Failed to calculate indicators for {symbol}.")
                continue
                
            # 5. Save latest calculated indicators to DB
            latest_timestamp = int(df["timestamp"].iloc[-1])
            save_success = save_indicators(symbol, latest_timestamp, latest_indicators)
            if save_success:
                logger.info(
                    f"Successfully updated market data and indicators for {symbol} | "
                    f"Time: {latest_timestamp} | Close: {df['close'].iloc[-1]} | "
                    f"RSI: {latest_indicators['rsi']:.2f} | "
                    f"BB Upper: {latest_indicators['bb_upper']:.2f}"
                )
                
                # 6. Check and execute exits (SL/TP targets)
                check_and_execute_exits(exchange)
                
                # 7. Evaluate and execute strategy signals
                signals = evaluate_all_strategies(symbol)
                for strategy_name, signal in signals.items():
                    if signal == "BUY":
                        logger.info(f"Signal triggered: {strategy_name} -> BUY {symbol}. Attempting entry...")
                        execute_market_order(exchange, symbol, "BUY", strategy_name)
                    elif signal == "SELL":
                        logger.info(f"Signal triggered: {strategy_name} -> SELL {symbol}. Attempting exit...")
                        execute_market_order(exchange, symbol, "SELL", strategy_name)
            else:
                logger.error(f"Failed to save indicators for {symbol} to database.")
                
        except Exception as e:
            logger.error(f"Error in pipeline cycle for {symbol}: {e}", exc_info=True)
            alert_system_error(f"pipeline_cycle:{symbol}", e)

    logger.info("Pipeline cycle execution complete.")


def start_scheduler():
    """
    Sets up the recurring schedule to run the data pipeline.
    Default interval is every 15 minutes.
    """
    exchange = initialize_exchange()
    
    # Run once immediately on start
    run_pipeline_cycle(exchange)
    
    scheduler = BlockingScheduler()
    # Schedule to execute at the start of every 15-minute interval (00, 15, 30, 45)
    scheduler.add_job(
        run_pipeline_cycle,
        trigger="cron",
        minute="0,15,30,45",
        args=[exchange],
        id="data_pipeline_job",
        name="Fetch Candlestick and Compute Indicators"
    )
    
    logger.info("Pipeline Scheduler started. Running every 15 minutes...")
    alert_bot_startup(settings.mode, TRADING_SYMBOLS)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Pipeline Scheduler stopped by user.")


if __name__ == "__main__":
    # Setup argument parser for standalone testing runs
    parser = argparse.ArgumentParser(description="OneGuard Data Pipeline Ingestion Engine")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run the pipeline cycle exactly once and exit (no scheduler)"
    )
    args = parser.parse_args()
    
    # Configure main module logging level
    from src.config import setup_logging
    setup_logging("OneGuard.Pipeline")
    
    # Ensure database is initialized
    if not initialize_db():
        logger.critical("Database initialization failed. Exiting.")
        sys.exit(1)
        
    # Validate configurations
    if not settings.validate():
        logger.critical("Configuration validation failed. Exiting.")
        sys.exit(1)
        
    if args.once:
        logger.info("Running standalone single pipeline cycle...")
        ex = initialize_exchange()
        run_pipeline_cycle(ex)
    else:
        start_scheduler()
