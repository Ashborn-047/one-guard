import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager
from typing import List, Tuple, Dict, Any, Optional
from src.config import settings

logger = logging.getLogger("OneGuard.Database")

@contextmanager
def get_db_connection():
    """
    Context manager for database connections.
    Ensures that connections are closed properly even if exceptions occur.
    """
    conn = sqlite3.connect(
        settings.db_path,
        timeout=10.0,  # 10s timeout to prevent locking under high frequency writes
        isolation_level=None  # Enable autocommit mode (managed manually via with/commit)
    )
    conn.row_factory = sqlite3.Row  # Access columns by name
    try:
        yield conn
    finally:
        conn.close()


def initialize_db() -> bool:
    """
    Creates tables and indices if they do not exist.
    """
    logger.info(f"Initializing database at: {settings.db_path}")
    
    # Ensure parent directories exist
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Enable WAL mode for high performance concurrent read/writes
            cursor.execute("PRAGMA journal_mode=WAL;")
            
            # 1. Candles Table (Historical OHLCV data)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    UNIQUE(symbol, timestamp) ON CONFLICT REPLACE
                );
            """)
            
            # Index for faster retrieval by symbol and time
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_candles_symbol_time ON candles(symbol, timestamp DESC);")

            # 2. Indicators Table (Stores calculated technical indicators)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS indicators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    rsi REAL,
                    ema_fast REAL,
                    ema_slow REAL,
                    bb_upper REAL,
                    bb_middle REAL,
                    bb_lower REAL,
                    UNIQUE(symbol, timestamp) ON CONFLICT REPLACE
                );
            """)
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_indicators_symbol_time ON indicators(symbol, timestamp DESC);")

            # 3. Trades Table (Records bot trading execution details)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,          -- 'BUY' or 'SELL'
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    cost REAL NOT NULL,
                    fee REAL,
                    pnl REAL,                    -- Realized PnL (for close orders)
                    order_id TEXT UNIQUE NOT NULL
                );
            """)
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, timestamp DESC);")
            
            logger.info("Database schemas and indexes initialized successfully.")
            return True
            
    except Exception as e:
        logger.error(f"Failed to initialize local SQLite database: {e}")
        return False


def save_candles(symbol: str, candles: List[Tuple[int, float, float, float, float, float]]) -> bool:
    """
    Saves a list of candles to the database.
    Each candle tuple: (timestamp_ms, open, high, low, close, volume)
    """
    if not candles:
        return True
        
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION;")
            
            # Prepare rows with symbol prefixed
            rows = [(symbol, c[0], c[1], c[2], c[3], c[4], c[5]) for c in candles]
            
            cursor.executemany("""
                INSERT INTO candles (symbol, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, rows)
            
            cursor.execute("COMMIT;")
            logger.debug(f"Saved {len(candles)} candles for {symbol} to database.")
            return True
    except Exception as e:
        logger.error(f"Failed to save candles for {symbol}: {e}")
        return False


def get_latest_candles(symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Retrieves the most recent candles for a symbol, ordered from oldest to newest
    (suitable for technical indicator calculation).
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, open, high, low, close, volume
                FROM candles
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol, limit))
            
            rows = cursor.fetchall()
            # Reverse to return chronologically (oldest to newest)
            return [dict(row) for row in reversed(rows)]
    except Exception as e:
        logger.error(f"Failed to retrieve latest candles for {symbol}: {e}")
        return []


def save_indicators(symbol: str, timestamp: int, values: Dict[str, Optional[float]]) -> bool:
    """
    Saves technical indicator calculations for a specific candle timestamp.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO indicators (symbol, timestamp, rsi, ema_fast, ema_slow, bb_upper, bb_middle, bb_lower)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timestamp) DO UPDATE SET
                    rsi = excluded.rsi,
                    ema_fast = excluded.ema_fast,
                    ema_slow = excluded.ema_slow,
                    bb_upper = excluded.bb_upper,
                    bb_middle = excluded.bb_middle,
                    bb_lower = excluded.bb_lower;
            """, (
                symbol,
                timestamp,
                values.get("rsi"),
                values.get("ema_fast"),
                values.get("ema_slow"),
                values.get("bb_upper"),
                values.get("bb_middle"),
                values.get("bb_lower")
            ))
            return True
    except Exception as e:
        logger.error(f"Failed to save indicators for {symbol} at {timestamp}: {e}")
        return False


def log_trade(trade_data: Dict[str, Any]) -> bool:
    """
    Logs an executed order into the database.
    Expects keys: timestamp, symbol, strategy, side, price, amount, cost, fee, pnl, order_id
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (timestamp, symbol, strategy, side, price, amount, cost, fee, pnl, order_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                trade_data["timestamp"],
                trade_data["symbol"],
                trade_data["strategy"],
                trade_data["side"],
                trade_data["price"],
                trade_data["amount"],
                trade_data["cost"],
                trade_data.get("fee"),
                trade_data.get("pnl"),
                trade_data["order_id"]
            ))
            logger.info(
                f"TRADE LOGGED: {trade_data['side']} {trade_data['amount']} {trade_data['symbol']} "
                f"at {trade_data['price']} | PnL: {trade_data.get('pnl')}"
            )
            return True
    except Exception as e:
        logger.error(f"Failed to log trade {trade_data.get('order_id')}: {e}")
        return False


def get_strategy_data(symbol: str, limit: int = 100) -> Any:
    """
    Retrieves merged candle and indicator data for a symbol as a pandas DataFrame,
    ordered chronologically (oldest to newest).
    """
    import pandas as pd
    try:
        with get_db_connection() as conn:
            query = """
                SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume,
                       i.rsi, i.ema_fast, i.ema_slow, i.bb_upper, i.bb_middle, i.bb_lower
                FROM candles c
                LEFT JOIN indicators i ON c.symbol = i.symbol AND c.timestamp = i.timestamp
                WHERE c.symbol = ?
                ORDER BY c.timestamp DESC
                LIMIT ?
            """
            df = pd.read_sql_query(query, conn, params=(symbol, limit))
            # Reverse to order chronologically (oldest to newest)
            df = df.iloc[::-1].reset_index(drop=True)
            return df
    except Exception as e:
        logger.error(f"Failed to fetch merged strategy data for {symbol}: {e}")
        return pd.DataFrame()


if __name__ == "__main__":
    # Test initialization when run directly
    logging.basicConfig(level=logging.INFO)
    success = initialize_db()
    print(f"\nDatabase Initialization Status: {success}")
    
    # Test data retrieval
    df = get_strategy_data("BTC/USDT", 5)
    print(f"Sample strategy DataFrame (len={len(df)}):")
    print(df.head() if not df.empty else "Empty DataFrame")
