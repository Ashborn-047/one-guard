import os
import sys
import time
import logging
from typing import Dict, Any, List, Optional
import ccxt
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

# Add project root to path to resolve src imports correctly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import settings
from src.db import get_db_connection, get_strategy_data, initialize_db
from src.risk import get_active_positions, get_weekly_pnl

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OneGuard.API")

# Initialize database schema
initialize_db()

app = FastAPI(title="OneGuard Bot Telemetry API", version="1.0.0")

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
    return {
        "is_live": settings.is_live,
        "emergency_halt": settings.emergency_halt,
        "max_position_size": settings.max_position_size,
        "max_open_trades": settings.max_open_trades,
        "weekly_drawdown_limit": settings.weekly_drawdown_limit,
        "loss_cooldown_minutes": settings.loss_cooldown_minutes,
        "stop_loss_target": 2.0,
        "take_profit_target": 4.0
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

@app.get("/api/chart")
def read_chart(symbol: str = "BTC/USDT", limit: int = Query(100, ge=10, le=500)):
    """
    Returns candlestick chart data along with overlays and indicators in a format
    suitable for lightweight-charts.
    """
    df = get_strategy_data(symbol, limit=limit)
    if df.empty:
        return {"candles": [], "indicators": {}}
        
    # Prepare candles array for lightweight-charts: { time: seconds, open, high, low, close }
    # Lightweight charts expects time in unix timestamp (seconds) or YYYY-MM-DD string
    candles = []
    ema_fast = []
    ema_slow = []
    rsi = []
    bb_upper = []
    bb_middle = []
    bb_lower = []
    
    for _, row in df.iterrows():
        # SQLite timestamps are in milliseconds, convert to seconds
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
        }
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
