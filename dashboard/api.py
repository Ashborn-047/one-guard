import os
import sys
import time
import logging
import threading
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional
import ccxt
import pandas as pd
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

# Add project root to path to resolve src imports correctly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import settings, setup_logging
from src.db import get_db_connection, get_strategy_data, get_latest_candles, initialize_db, save_candles, save_indicators
from src.indicators import calculate_technical_indicators
from src.risk import get_active_positions, get_weekly_pnl

# Set up logging
logger = setup_logging("OneGuard.API")

# Initialize database schema
initialize_db()
settings.validate()

# ---------------------------------------------------------------------------
# Live Market Data Feed — Background Ingestion Engine
# ---------------------------------------------------------------------------
# Fetches real OHLCV candles from Binance (public endpoint, no API keys needed)
# and persists them to the local SQLite database with recalculated indicators.
# Runs as a daemon thread alongside the FastAPI server.
# ---------------------------------------------------------------------------

TRADING_SYMBOLS = ["BTC/USDT", "ETH/USDT"]
INGESTION_TIMEFRAME = "15m"
INGESTION_INTERVAL_SECONDS = 30
INGESTION_CANDLE_LIMIT = 1000  # Initial backfill depth (covers ~10 days of 15m candles)


def recalculate_and_save_all_indicators(symbol: str, limit: int = 1000, timeframe: str = "15m"):
    """
    Fetch the latest `limit` candles from DB, calculate technical indicators
    for each candle (which requires historical context), and save them to DB.
    """
    import pandas_ta as ta
    db_candles = get_latest_candles(symbol, limit=limit, timeframe=timeframe)
    if not db_candles or len(db_candles) < 30:
        return
        
    df = pd.DataFrame(db_candles)
    try:
        # 1. Compute RSI (14)
        rsi_series = ta.rsi(close=df["close"], length=14)
        
        # 2. Compute EMAs (9 and 21)
        ema_fast_series = ta.ema(close=df["close"], length=9)
        ema_slow_series = ta.ema(close=df["close"], length=21)
        
        # 3. Compute Bollinger Bands (20, 2)
        bb_df = ta.bbands(close=df["close"], length=20, std=2)
        if bb_df is None or bb_df.empty:
            return
            
        bbu_col = [col for col in bb_df.columns if col.startswith("BBU")][0]
        bbm_col = [col for col in bb_df.columns if col.startswith("BBM")][0]
        bbl_col = [col for col in bb_df.columns if col.startswith("BBL")][0]
        
        # Save indicators in a single transaction
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION;")
            for idx, row in df.iterrows():
                timestamp = int(row["timestamp"])
                
                # Check if we have valid values for the indicators
                rsi_val = float(rsi_series.loc[idx]) if not pd.isna(rsi_series.loc[idx]) else None
                ema_fast_val = float(ema_fast_series.loc[idx]) if not pd.isna(ema_fast_series.loc[idx]) else None
                ema_slow_val = float(ema_slow_series.loc[idx]) if not pd.isna(ema_slow_series.loc[idx]) else None
                bb_upper_val = float(bb_df.loc[idx, bbu_col]) if not pd.isna(bb_df.loc[idx, bbu_col]) else None
                bb_middle_val = float(bb_df.loc[idx, bbm_col]) if not pd.isna(bb_df.loc[idx, bbm_col]) else None
                bb_lower_val = float(bb_df.loc[idx, bbl_col]) if not pd.isna(bb_df.loc[idx, bbl_col]) else None
                
                cursor.execute("""
                    INSERT OR REPLACE INTO indicators (symbol, timeframe, timestamp, rsi, ema_fast, ema_slow, bb_upper, bb_middle, bb_lower)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    timeframe,
                    timestamp,
                    rsi_val,
                    ema_fast_val,
                    ema_slow_val,
                    bb_upper_val,
                    bb_middle_val,
                    bb_lower_val
                ))
            cursor.execute("COMMIT;")
        logger.info(f"MarketDataFeed: Successfully calculated and saved indicators for {len(df)} candles for {symbol} ({timeframe}).")
    except Exception as e:
        logger.error(f"MarketDataFeed: Error calculating historical indicators for {symbol}: {e}", exc_info=True)


class MarketDataFeed:
    """
    Background market data ingestion engine.
    Creates an unauthenticated ccxt Binance client and fetches live OHLCV
    candles on a recurring interval. Clears stale seed data on the first
    successful live fetch.
    """

    def __init__(self):
        self.status: str = "starting"  # starting | active | error
        self.data_source: str = "unknown"  # live | seed | unknown
        self.last_fetch_time: Optional[float] = None
        self.last_error: Optional[str] = None
        self.candles_fetched: int = 0
        self.symbols_tracked: List[str] = TRADING_SYMBOLS
        self._seed_cleared: bool = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._exchange: Optional[ccxt.Exchange] = None

    def _init_exchange(self) -> ccxt.Exchange:
        """Initialize an unauthenticated Binance client for public data."""
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'timeout': 15000,
        })
        logger.info("MarketDataFeed: Initialized unauthenticated Binance client for public OHLCV.")
        return exchange

    def _clear_seed_data(self):
        """Purge all existing candle and indicator data (seed/stale) on first live fetch."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM candles;")
                cursor.execute("DELETE FROM indicators;")
                conn.commit()
            self._seed_cleared = True
            logger.info("MarketDataFeed: Cleared stale seed data from candles and indicators tables.")
        except Exception as e:
            logger.error(f"MarketDataFeed: Failed to clear seed data: {e}")

    def _ingest_cycle(self):
        """Single ingestion cycle: fetch OHLCV + recalculate indicators for all symbols."""
        for symbol in self.symbols_tracked:
            try:
                # Fetch live OHLCV candles
                limit = INGESTION_CANDLE_LIMIT if self.data_source == "unknown" else 100
                ohlcv = self._exchange.fetch_ohlcv(
                    symbol,
                    timeframe=INGESTION_TIMEFRAME,
                    limit=limit
                )

                if not ohlcv:
                    logger.warning(f"MarketDataFeed: No candles returned for {symbol}.")
                    continue

                # On first successful fetch, clear stale seed data
                if not self._seed_cleared:
                    self._clear_seed_data()

                # Save candles to database
                save_candles(symbol, ohlcv, timeframe=INGESTION_TIMEFRAME)
                self.candles_fetched += len(ohlcv)

                # Recalculate indicators for all candles in the DB (up to 1000) to populate historical indicators
                recalculate_and_save_all_indicators(symbol, limit=1000, timeframe=INGESTION_TIMEFRAME)

                logger.debug(
                    f"MarketDataFeed: Ingested {len(ohlcv)} candles for {symbol} | "
                    f"Latest close: {ohlcv[-1][4]}"
                )

            except Exception as e:
                logger.error(f"MarketDataFeed: Error fetching {symbol}: {e}")
                self.last_error = f"{symbol}: {str(e)}"

        self.last_fetch_time = time.time()
        self.data_source = "live"
        self.status = "active"

    def _run_loop(self):
        """Main background loop — runs until stop event is set."""
        try:
            self._exchange = self._init_exchange()

            # Initial fetch immediately on startup
            logger.info("MarketDataFeed: Running initial data ingestion...")
            self._ingest_cycle()
            logger.info(
                f"MarketDataFeed: Initial ingestion complete. "
                f"Fetched {self.candles_fetched} candles across {len(self.symbols_tracked)} symbols."
            )

            # Recurring loop
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=INGESTION_INTERVAL_SECONDS)
                if self._stop_event.is_set():
                    break
                self._ingest_cycle()

        except Exception as e:
            self.status = "error"
            self.last_error = str(e)
            logger.error(f"MarketDataFeed: Fatal error in background loop: {e}", exc_info=True)

        logger.info("MarketDataFeed: Background ingestion loop stopped.")

    def start(self):
        """Start the background ingestion thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MarketDataFeed")
        self._thread.start()
        logger.info("MarketDataFeed: Background ingestion thread started.")

    def stop(self):
        """Signal the background thread to stop gracefully."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("MarketDataFeed: Background ingestion thread stopped.")

    def get_status(self) -> Dict[str, Any]:
        """Return current feed health status."""
        return {
            "feed_active": self.status == "active",
            "status": self.status,
            "data_source": self.data_source,
            "last_fetch": int(self.last_fetch_time) if self.last_fetch_time else None,
            "symbols_tracked": self.symbols_tracked,
            "candles_fetched": self.candles_fetched,
            "error": self.last_error
        }


# Global feed instance
market_feed = MarketDataFeed()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: start live data feed on startup, stop on shutdown."""
    market_feed.start()
    yield
    market_feed.stop()


app = FastAPI(
    title="OneGuard Bot Telemetry API",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for local dev environment
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify the exact origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize CCXT exchange client
_exchange = None

def get_exchange_client():
    global _exchange
    if _exchange is not None:
        return _exchange
    try:
        exchange_class = getattr(ccxt, "binance")
        exchange_config = {
            'enableRateLimit': True,
            'timeout': 10000,
        }
        if settings.api_key and settings.secret_key:
            exchange_config['apiKey'] = settings.api_key
            exchange_config['secret'] = settings.secret_key
            
        exchange = exchange_class(exchange_config)
        if settings.is_sandbox:
            exchange.set_sandbox_mode(True)
        _exchange = exchange
        logger.info(f"Initialized exchange client (sandbox={settings.is_sandbox})")
        return _exchange
    except Exception as e:
        logger.error(f"Failed to initialize exchange connection: {e}")
        return None

def fetch_live_price(exchange, symbol: str, fallback_price: float) -> float:
    if exchange is None:
        return fallback_price
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['close'])
    except Exception as e:
        logger.warning(f"Could not fetch live price for {symbol} from exchange: {e}. Using fallback {fallback_price}")
        return fallback_price

def get_performance_metrics():
    metrics = {
        "total_trades": 0,
        "closed_trades": 0,
        "realized_pnl": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0
    }
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Fetch total count
            cursor.execute("SELECT COUNT(*) as cnt FROM trades")
            metrics["total_trades"] = cursor.fetchone()["cnt"]
            
            # Fetch closed trades (trades with realized PnL)
            cursor.execute("SELECT pnl FROM trades WHERE pnl IS NOT NULL")
            pnl_rows = cursor.fetchall()
            
            if pnl_rows:
                pnls = [float(row["pnl"]) for row in pnl_rows]
                metrics["closed_trades"] = len(pnls)
                metrics["realized_pnl"] = sum(pnls)
                
                wins = [p for p in pnls if p > 0]
                losses = [p for p in pnls if p < 0]
                
                if pnls:
                    metrics["win_rate"] = (len(wins) / len(pnls)) * 100.0
                
                gross_profit = sum(wins)
                gross_loss = abs(sum(losses))
                
                metrics["gross_profit"] = gross_profit
                metrics["gross_loss"] = gross_loss
                
                if gross_loss > 0:
                    metrics["profit_factor"] = gross_profit / gross_loss
                elif gross_profit > 0:
                    metrics["profit_factor"] = float('inf')
                else:
                    metrics["profit_factor"] = 0.0
    except Exception as e:
        logger.error(f"Error calculating performance metrics from database: {e}")
        
    return metrics

@app.get("/api/status")
def read_status():
    """
    Returns bot status, execution mode, emergency halt switch, and risk settings.
    """
    is_mock = not (settings.api_key and settings.secret_key)
    return {
        "is_live": settings.is_live,
        "emergency_halt": settings.emergency_halt,
        "max_position_size": settings.max_position_size,
        "max_open_trades": settings.max_open_trades,
        "weekly_drawdown_limit": settings.weekly_drawdown_limit,
        "loss_cooldown_minutes": settings.loss_cooldown_minutes,
        "stop_loss_target": 2.0,
        "take_profit_target": 4.0,
        "is_mock": is_mock
    }

@app.get("/api/metrics")
def read_metrics():
    """
    Returns total trades, closed trades, win rate %, profit factor, gross stats, realized PnL,
    weekly realized PnL, and weekly drawdown status.
    """
    perf = get_performance_metrics()
    weekly_pnl = get_weekly_pnl()
    
    drawdown_limit_neg = -settings.weekly_drawdown_limit
    drawdown_status = "SAFE" if weekly_pnl > drawdown_limit_neg else "HALTED"
    
    return {
        "total_trades": perf["total_trades"],
        "closed_trades": perf["closed_trades"],
        "realized_pnl": round(perf["realized_pnl"], 4),
        "win_rate": round(perf["win_rate"], 2),
        "profit_factor": round(perf["profit_factor"], 2) if perf["profit_factor"] != float('inf') else "inf",
        "gross_profit": round(perf["gross_profit"], 4),
        "gross_loss": round(perf["gross_loss"], 4),
        "weekly_pnl": round(weekly_pnl, 4),
        "drawdown_status": drawdown_status
    }

@app.get("/api/positions")
def read_positions():
    """
    Returns active positions with entry details, current live price from exchange, cost, and unrealized PnL.
    """
    active_positions = get_active_positions()
    exchange = get_exchange_client()
    
    position_list = []
    for symbol, qty in active_positions.items():
        entry_price = 0.0
        entry_time = 0
        strategy = "Unknown"
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT timestamp, price, strategy 
                    FROM trades 
                    WHERE symbol = ? AND side = 'BUY' 
                    ORDER BY timestamp DESC LIMIT 1
                """, (symbol,))
                row = cursor.fetchone()
                if row:
                    entry_price = float(row["price"])
                    entry_time = int(row["timestamp"])
                    strategy = row["strategy"]
        except Exception as e:
            logger.error(f"Error fetching entry price for {symbol}: {e}")
            
        current_price = fetch_live_price(exchange, symbol, fallback_price=entry_price)
        
        cost = entry_price * qty
        market_value = current_price * qty
        unrealized_pnl = market_value - cost
        unrealized_pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
        
        sl_price = entry_price * 0.98
        tp_price = entry_price * 1.04
        
        position_list.append({
            "symbol": symbol,
            "quantity": qty,
            "strategy": strategy,
            "entry_time": entry_time,
            "entry_price": entry_price,
            "current_price": current_price,
            "cost": cost,
            "market_value": market_value,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "stop_loss": sl_price,
            "take_profit": tp_price
        })
        
    return position_list

@app.get("/api/market-status")
def read_market_status():
    """
    Returns the health status of the live market data feed.
    """
    return market_feed.get_status()


@app.get("/api/chart")
def read_chart(
    symbol: str = "BTC/USDT", 
    timeframe: str = "15m",
    limit: int = Query(100, ge=10, le=2000)
):
    """
    Returns candlestick chart data along with overlays and indicators in a format
    suitable for lightweight-charts. Includes data source metadata and handles on-demand loading.
    """
    supported_timeframes = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    if timeframe not in supported_timeframes:
        timeframe = "15m"
        
    timeframe_seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400
    }
    
    # Check data freshness in DB
    db_candles = get_latest_candles(symbol, limit=1, timeframe=timeframe)
    need_fetch = False
    
    if not db_candles:
        need_fetch = True
    else:
        latest_timestamp_ms = db_candles[-1]["timestamp"]
        now_ms = int(time.time() * 1000)
        period_ms = timeframe_seconds[timeframe] * 1000
        # If the latest candle is older than 2 periods, fetch fresh data
        if now_ms - latest_timestamp_ms > 2 * period_ms:
            need_fetch = True
            
    if need_fetch:
        try:
            logger.info(f"API /api/chart: Cache miss or stale data for {symbol} ({timeframe}). Fetching live candles from Binance...")
            public_exchange = ccxt.binance({
                'enableRateLimit': True,
                'timeout': 15000,
            })
            # Fetch 1000 candles to give plenty of history
            ohlcv = public_exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=1000)
            if ohlcv:
                save_candles(symbol, ohlcv, timeframe=timeframe)
                recalculate_and_save_all_indicators(symbol, limit=1000, timeframe=timeframe)
                logger.info(f"API /api/chart: Successfully backfilled and saved 1000 candles for {symbol} ({timeframe}).")
        except Exception as e:
            logger.error(f"API /api/chart: Failed to fetch live backfill for {symbol} ({timeframe}): {e}")
            
    df = get_strategy_data(symbol, limit=limit, timeframe=timeframe)
    if df.empty:
        return {
            "candles": [],
            "indicators": {},
            "data_source": market_feed.data_source,
            "last_updated": int(market_feed.last_fetch_time) if market_feed.last_fetch_time else None
        }
        
    # Prepare candles array for lightweight-charts: { time: seconds, open, high, low, close }
    candles = []
    ema_fast = []
    ema_slow = []
    rsi = []
    bb_upper = []
    bb_middle = []
    bb_lower = []
    
    for _, row in df.iterrows():
        t_sec = int(row["timestamp"]) // 1000
        
        candles.append({
            "time": t_sec,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"])
        })
        
        if "ema_fast" in row and row["ema_fast"] is not None and not str(row["ema_fast"]).lower() == 'nan':
            ema_fast.append({"time": t_sec, "value": float(row["ema_fast"])})
            
        if "ema_slow" in row and row["ema_slow"] is not None and not str(row["ema_slow"]).lower() == 'nan':
            ema_slow.append({"time": t_sec, "value": float(row["ema_slow"])})
            
        if "rsi" in row and row["rsi"] is not None and not str(row["rsi"]).lower() == 'nan':
            rsi.append({"time": t_sec, "value": float(row["rsi"])})
            
        if "bb_upper" in row and row["bb_upper"] is not None and not str(row["bb_upper"]).lower() == 'nan':
            bb_upper.append({"time": t_sec, "value": float(row["bb_upper"])})
            
        if "bb_middle" in row and row["bb_middle"] is not None and not str(row["bb_middle"]).lower() == 'nan':
            bb_middle.append({"time": t_sec, "value": float(row["bb_middle"])})
            
        if "bb_lower" in row and row["bb_lower"] is not None and not str(row["bb_lower"]).lower() == 'nan':
            bb_lower.append({"time": t_sec, "value": float(row["bb_lower"])})
            
    return {
        "candles": candles,
        "indicators": {
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "rsi": rsi,
            "bb_upper": bb_upper,
            "bb_middle": bb_middle,
            "bb_lower": bb_lower
        },
        "data_source": "live",
        "last_updated": int(time.time())
    }

# Keep track of the last time we synced trades for each symbol to prevent rate limits
_last_trade_sync_time: Dict[str, float] = {}

def sync_exchange_trades():
    """
    Synchronizes trades from the exchange into our local database.
    Only runs if API credentials are set and limits sync per symbol to once every 30 seconds.
    """
    global _last_trade_sync_time
    
    # Check if exchange API keys are present
    if not settings.api_key or not settings.secret_key:
        logger.info("Exchange sync skipped: API credentials are not configured.")
        return

    exchange = get_exchange_client()
    if not exchange:
        logger.error("Exchange sync failed: could not initialize exchange client.")
        return

    # Get symbols to sync
    symbols = read_symbols()
    now = time.time()
    
    for symbol in symbols:
        # Limit sync rate to once every 30 seconds per symbol
        last_sync = _last_trade_sync_time.get(symbol, 0.0)
        if now - last_sync < 30.0:
            logger.debug(f"Sync for {symbol} skipped: throttled (last sync {now - last_sync:.1f}s ago).")
            continue
            
        logger.info(f"Syncing trades from exchange for symbol: {symbol}...")
        try:
            # Update last sync time before call to prevent concurrent requests
            _last_trade_sync_time[symbol] = now
            
            # Fetch trades from exchange
            raw_trades = exchange.fetch_my_trades(symbol)
            if not raw_trades:
                logger.info(f"No trades returned from exchange for {symbol}.")
                continue
                
            # Group trades by Order ID
            order_trades = {}
            for t in raw_trades:
                order_id = t.get('order') or t.get('id')
                if not order_id:
                    continue
                if order_id not in order_trades:
                    order_trades[order_id] = []
                order_trades[order_id].append(t)
                
            # Aggregate and save trades
            with get_db_connection() as conn:
                cursor = conn.cursor()
                for order_id, t_list in order_trades.items():
                    # Check if trade already exists in our database
                    cursor.execute("SELECT id FROM trades WHERE order_id = ?", (order_id,))
                    if cursor.fetchone():
                        continue
                        
                    # Sort trades by timestamp ascending
                    t_list = sorted(t_list, key=lambda x: x.get('timestamp') or 0)
                    
                    first_t = t_list[0]
                    
                    timestamp = first_t.get('timestamp') or int(time.time() * 1000)
                    side = first_t.get('side').upper()
                    
                    # Calculate totals
                    total_amount = sum(float(x.get('amount') or 0.0) for x in t_list)
                    total_cost = sum(float(x.get('cost') or 0.0) for x in t_list)
                    
                    # Calculate weighted average price
                    if total_amount > 0:
                        avg_price = sum(float(x.get('price') or 0.0) * float(x.get('amount') or 0.0) for x in t_list) / total_amount
                    else:
                        avg_price = float(first_t.get('price') or 0.0)
                        
                    # Calculate fee cost
                    total_fee = 0.0
                    for x in t_list:
                        fee = x.get('fee')
                        if fee:
                            total_fee += float(fee.get('cost') or 0.0)
                            
                    # Calculate realized PnL for exits (SELL)
                    pnl = None
                    if side == 'SELL':
                        # Find the preceding BUY trade for this symbol to calculate PnL.
                        cursor.execute("""
                            SELECT price, fee FROM trades 
                            WHERE symbol = ? AND side = 'BUY' 
                            ORDER BY timestamp DESC LIMIT 1
                        """, (symbol,))
                        buy_row = cursor.fetchone()
                        if buy_row:
                            entry_price = float(buy_row['price'])
                            entry_fee = float(buy_row['fee']) if buy_row['fee'] is not None else 0.0
                            pnl = (avg_price - entry_price) * total_amount - entry_fee - total_fee
                        else:
                            pnl = 0.0
                            
                    # Insert the aggregated trade log
                    cursor.execute("""
                        INSERT OR IGNORE INTO trades (timestamp, symbol, strategy, side, price, amount, cost, fee, pnl, order_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        timestamp,
                        symbol,
                        "Exchange Sync",
                        side,
                        avg_price,
                        total_amount,
                        total_cost,
                        total_fee,
                        pnl,
                        order_id
                    ))
                conn.commit()
            logger.info(f"Successfully synced exchange trades for {symbol}.")
        except Exception as e:
            logger.error(f"Error syncing trades from exchange for {symbol}: {e}")

@app.get("/api/trades")
def read_trades(limit: int = Query(100, ge=1, le=200)):
    """
    Returns historical trades log, synchronizing with the exchange first if API keys are configured.
    """
    sync_exchange_trades()
    
    trades_list = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, order_id, symbol, strategy, side, price, amount, cost, fee, pnl
                FROM trades
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            for row in rows:
                trades_list.append({
                    "timestamp": row["timestamp"],
                    "order_id": row["order_id"],
                    "symbol": row["symbol"],
                    "strategy": row["strategy"],
                    "side": row["side"],
                    "price": float(row["price"]),
                    "amount": float(row["amount"]),
                    "cost": float(row["cost"]),
                    "fee": float(row["fee"]) if row["fee"] is not None else None,
                    "pnl": float(row["pnl"]) if row["pnl"] is not None else None
                })
    except Exception as e:
        logger.error(f"Failed to query trades ledger: {e}")
        
    return trades_list


from pydantic import BaseModel

class SettingsUpdate(BaseModel):
    is_live: Optional[bool] = None
    emergency_halt: Optional[bool] = None
    max_position_size: Optional[float] = None
    max_open_trades: Optional[int] = None
    weekly_drawdown_limit: Optional[float] = None
    loss_cooldown_minutes: Optional[int] = None


@app.get("/api/symbols")
def read_symbols():
    """
    Returns a list of all distinct symbols available in the candles database.
    """
    symbols_list = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT symbol FROM candles")
            rows = cursor.fetchall()
            symbols_list = [row["symbol"] for row in rows]
    except Exception as e:
        logger.error(f"Failed to query available symbols: {e}")
        
    if not symbols_list:
        symbols_list = ["BTC/USDT", "ETH/USDT"]
    return symbols_list


@app.post("/api/settings")
def update_settings(payload: SettingsUpdate):
    """
    Updates the system configuration parameters dynamically.
    """
    try:
        if payload.is_live is not None:
            settings.mode = "live" if payload.is_live else "sandbox"
        if payload.emergency_halt is not None:
            settings.emergency_halt = payload.emergency_halt
        if payload.max_position_size is not None:
            settings.max_position_size = payload.max_position_size
        if payload.max_open_trades is not None:
            settings.max_open_trades = payload.max_open_trades
        if payload.weekly_drawdown_limit is not None:
            settings.weekly_drawdown_limit = payload.weekly_drawdown_limit
        if payload.loss_cooldown_minutes is not None:
            settings.loss_cooldown_minutes = payload.loss_cooldown_minutes
        
        return {"status": "success", "settings": read_status()}
    except Exception as e:
        logger.error(f"Failed to update dynamic settings: {e}")
        return {"status": "error", "message": str(e)}
