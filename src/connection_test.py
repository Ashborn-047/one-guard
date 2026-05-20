import time
import sys
import logging
import ccxt
from src.config import settings

logger = logging.getLogger("OneGuard.ConnectionTest")

def run_test():
    logger.info("Initializing CCXT connection test...")
    
    # 1. Initialize exchange connection
    # We use getattr to instantiate the exchange dynamically (for portability)
    try:
        exchange_class = getattr(ccxt, "binance")
    except AttributeError:
        logger.error("Exchange class 'binance' is not supported in the installed version of CCXT.")
        sys.exit(1)

    exchange_config = {
        'enableRateLimit': True,
        'timeout': 15000,
    }
    
    # Inject keys if configured
    if settings.api_key and settings.secret_key:
        exchange_config['apiKey'] = settings.api_key
        exchange_config['secret'] = settings.secret_key
        logger.info("API Credentials injected into exchange client configuration.")
    else:
        logger.warning("No API credentials configured. Operating in public-only mode.")

    exchange = exchange_class(exchange_config)

    # 2. Handle Sandbox/Testnet configuration
    if settings.is_sandbox:
        logger.info("Enabling exchange sandbox/testnet mode...")
        exchange.set_sandbox_mode(True)
    else:
        logger.warning("WARNING: Connecting to the LIVE Binance production exchange!")

    # 3. Test Public Endpoints (Fetch BTC/USDT and ETH/USDT)
    test_symbols = ["BTC/USDT", "ETH/USDT"]
    logger.info(f"Fetching tickers for: {test_symbols}...")
    
    for symbol in test_symbols:
        try:
            start_time = time.time()
            ticker = exchange.fetch_ticker(symbol)
            latency = (time.time() - start_time) * 1000
            
            last_price = ticker.get("last")
            bid = ticker.get("bid")
            ask = ticker.get("ask")
            
            logger.info(
                f"Successfully fetched ticker for {symbol} | "
                f"Last: {last_price} | Bid: {bid} | Ask: {ask} | Latency: {latency:.1f}ms"
            )
        except Exception as e:
            logger.error(f"Failed to fetch ticker for {symbol}: {e}")

    # 4. Check Latency and System Time Sync
    try:
        start_time = time.time()
        exchange_time = exchange.fetch_time()
        roundtrip = (time.time() - start_time) * 1000
        
        system_time_ms = int(time.time() * 1000)
        time_diff = system_time_ms - exchange_time
        
        logger.info(f"Exchange Server Time: {exchange_time} ms")
        logger.info(f"Local Host System Time: {system_time_ms} ms")
        logger.info(f"System Clock Offset: {time_diff} ms (Roundtrip: {roundtrip:.1f}ms)")
        
        # Windows / VPS systems must be within 1000ms offset to avoid signature validation failures.
        if abs(time_diff) > 1000:
            logger.warning(
                f"CLOCK SYNC WARNING: Local system clock offset is {time_diff}ms. "
                "Offsets greater than 1000ms will cause authenticated trade execution to fail due to 'Timestamp for this request is outside of the recvWindow' errors. "
                "Please synchronize your system clock."
            )
        else:
            logger.info("System clock is synchronized with the exchange server.")
    except Exception as e:
        logger.error(f"Failed to fetch server time or check clock synchronization: {e}")

    # 5. Test Authenticated Endpoints (Fetch Balance)
    if settings.api_key and settings.secret_key:
        logger.info("Testing private balance query...")
        try:
            balance = exchange.fetch_balance()
            # Log non-zero asset balances
            non_zero_assets = {
                asset: details 
                for asset, details in balance.get("total", {}).items() 
                if details > 0
            }
            logger.info(f"Private balance check succeeded. Non-zero balances: {non_zero_assets}")
        except Exception as e:
            logger.error(
                f"Failed to fetch balance. This is likely due to invalid API keys, "
                f"IP binding restrictions, or expired credentials. Error: {e}"
            )
    else:
        logger.info("Skipping authenticated balance check (credentials not configured).")

    logger.info("CCXT connection test sequence complete.")

if __name__ == "__main__":
    # Validate configurations first
    if settings.validate():
        run_test()
    else:
        logger.error("Configuration validation failed. Aborting connection test.")
        sys.exit(1)
