import logging
import datetime
import time
import pandas as pd
from pathlib import Path
from contextlib import contextmanager
from typing import List, Tuple, Dict, Any, Optional
from src.config import settings

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("OneGuard.Database")

is_postgres = settings.database_url.startswith("postgres")
db_url = settings.database_url.replace("postgres://", "postgresql://") if is_postgres else f"sqlite:///{settings.db_path}"

# For SQLite, we want AUTOCOMMIT to match our previous isolation_level=None behavior.
# For Postgres, standard SQLAlchemy transaction management is fine.
if is_postgres:
    engine = create_engine(db_url, pool_size=5, max_overflow=10, pool_timeout=30)
else:
    # Ensure SQLite directory exists
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")


@contextmanager
def get_db_connection():
    """
    Context manager for database connections using SQLAlchemy.
    Yields a connection that is automatically committed (via begin) unless an exception occurs.
    """
    with engine.begin() as conn:
        yield conn


def initialize_db() -> bool:
    """
    Creates tables and indices if they do not exist.
    """
    logger.info(f"Initializing database at: {settings.db_path if not is_postgres else 'Neon Postgres'}")
    
    try:
        with get_db_connection() as conn:
            if not is_postgres:
                # Enable WAL mode for SQLite
                conn.execute(text("PRAGMA journal_mode=WAL;"))
                
                # Table Migration logic for SQLite
                res = conn.execute(text("PRAGMA table_info(candles);"))
                columns = [row[1] for row in res.fetchall()]  # index 1 is name
                if columns and "timeframe" not in columns:
                    logger.warning("Upgrading DB schema: dropping old candles and indicators tables.")
                    conn.execute(text("DROP TABLE IF EXISTS candles;"))
                    conn.execute(text("DROP TABLE IF EXISTS indicators;"))
            else:
                # Postgres Schema Migration Logic (check column existence)
                res = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='candles';
                """))
                columns = [row[0] for row in res.fetchall()]
                if columns and "timeframe" not in columns:
                    logger.warning("Upgrading DB schema: dropping old candles and indicators tables.")
                    conn.execute(text("DROP TABLE IF EXISTS candles CASCADE;"))
                    conn.execute(text("DROP TABLE IF EXISTS indicators CASCADE;"))

            # 1. Candles Table
            if is_postgres:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS candles (
                        id SERIAL PRIMARY KEY,
                        symbol VARCHAR NOT NULL,
                        timeframe VARCHAR NOT NULL DEFAULT '15m',
                        timestamp BIGINT NOT NULL,
                        open DOUBLE PRECISION NOT NULL,
                        high DOUBLE PRECISION NOT NULL,
                        low DOUBLE PRECISION NOT NULL,
                        close DOUBLE PRECISION NOT NULL,
                        volume DOUBLE PRECISION NOT NULL,
                        UNIQUE(symbol, timeframe, timestamp)
                    );
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS candles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL DEFAULT '15m',
                        timestamp INTEGER NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume REAL NOT NULL,
                        UNIQUE(symbol, timeframe, timestamp) ON CONFLICT REPLACE
                    );
                """))
            
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_candles_symbol_timeframe_time ON candles(symbol, timeframe, timestamp DESC);"))

            # 2. Indicators Table
            if is_postgres:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS indicators (
                        id SERIAL PRIMARY KEY,
                        symbol VARCHAR NOT NULL,
                        timeframe VARCHAR NOT NULL DEFAULT '15m',
                        timestamp BIGINT NOT NULL,
                        rsi DOUBLE PRECISION,
                        ema_fast DOUBLE PRECISION,
                        ema_slow DOUBLE PRECISION,
                        bb_upper DOUBLE PRECISION,
                        bb_middle DOUBLE PRECISION,
                        bb_lower DOUBLE PRECISION,
                        UNIQUE(symbol, timeframe, timestamp)
                    );
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS indicators (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL DEFAULT '15m',
                        timestamp INTEGER NOT NULL,
                        rsi REAL,
                        ema_fast REAL,
                        ema_slow REAL,
                        bb_upper REAL,
                        bb_middle REAL,
                        bb_lower REAL,
                        UNIQUE(symbol, timeframe, timestamp) ON CONFLICT REPLACE
                    );
                """))
            
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_indicators_symbol_timeframe_time ON indicators(symbol, timeframe, timestamp DESC);"))

            # 3. Trades Table
            if is_postgres:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id SERIAL PRIMARY KEY,
                        timestamp BIGINT NOT NULL,
                        symbol VARCHAR NOT NULL,
                        strategy VARCHAR NOT NULL,
                        side VARCHAR NOT NULL,
                        price DOUBLE PRECISION NOT NULL,
                        amount DOUBLE PRECISION NOT NULL,
                        cost DOUBLE PRECISION NOT NULL,
                        fee DOUBLE PRECISION,
                        pnl DOUBLE PRECISION,
                        order_id VARCHAR UNIQUE NOT NULL
                    );
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        strategy TEXT NOT NULL,
                        side TEXT NOT NULL,
                        price REAL NOT NULL,
                        amount REAL NOT NULL,
                        cost REAL NOT NULL,
                        fee REAL,
                        pnl REAL,
                        order_id TEXT UNIQUE NOT NULL
                    );
                """))
            
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, timestamp DESC);"))

            # 4. System Config Table
            if is_postgres:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS system_config (
                        key VARCHAR PRIMARY KEY,
                        value VARCHAR NOT NULL
                    );
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS system_config (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                """))
            
            logger.info("Database schemas and indexes initialized successfully.")
            return True
            
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False


def save_candles(symbol: str, candles: List[Tuple[int, float, float, float, float, float]], timeframe: str = "15m") -> bool:
    """
    Saves a list of candles to the database.
    Each candle tuple: (timestamp_ms, open, high, low, close, volume)
    """
    if not candles:
        return True
        
    try:
        with get_db_connection() as conn:
            # Prepare rows as list of dicts for SQLAlchemy executemany
            rows = [
                {"symbol": symbol, "timeframe": timeframe, "timestamp": c[0], 
                 "open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": c[5]}
                for c in candles
            ]
            
            if is_postgres:
                # Postgres ON CONFLICT DO NOTHING
                conn.execute(text("""
                    INSERT INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume)
                    VALUES (:symbol, :timeframe, :timestamp, :open, :high, :low, :close, :volume)
                    ON CONFLICT (symbol, timeframe, timestamp) DO NOTHING;
                """), rows)
            else:
                # SQLite INSERT OR REPLACE
                conn.execute(text("""
                    INSERT OR REPLACE INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume)
                    VALUES (:symbol, :timeframe, :timestamp, :open, :high, :low, :close, :volume);
                """), rows)
            
            logger.debug(f"Saved {len(candles)} candles for {symbol} ({timeframe}) to database.")
            return True
    except Exception as e:
        logger.error(f"Failed to save candles for {symbol}: {e}")
        return False


def get_latest_candles(symbol: str, limit: int = 100, timeframe: str = "15m") -> List[Dict[str, Any]]:
    """
    Retrieves the most recent candles for a symbol, ordered from oldest to newest.
    """
    try:
        with get_db_connection() as conn:
            res = conn.execute(text("""
                SELECT timestamp, open, high, low, close, volume
                FROM candles
                WHERE symbol = :symbol AND timeframe = :timeframe
                ORDER BY timestamp DESC
                LIMIT :limit
            """), {"symbol": symbol, "timeframe": timeframe, "limit": limit})
            
            rows = [dict(row) for row in res.mappings()]
            return list(reversed(rows))
    except Exception as e:
        logger.error(f"Failed to retrieve latest candles for {symbol} ({timeframe}): {e}")
        return []


def save_indicators(symbol: str, timestamp: int, values: Dict[str, Optional[float]], timeframe: str = "15m") -> bool:
    """
    Saves technical indicator calculations for a specific candle timestamp.
    """
    try:
        with get_db_connection() as conn:
            params = {
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": timestamp,
                "rsi": values.get("rsi"),
                "ema_fast": values.get("ema_fast"),
                "ema_slow": values.get("ema_slow"),
                "bb_upper": values.get("bb_upper"),
                "bb_middle": values.get("bb_middle"),
                "bb_lower": values.get("bb_lower")
            }
            
            if is_postgres:
                conn.execute(text("""
                    INSERT INTO indicators (symbol, timeframe, timestamp, rsi, ema_fast, ema_slow, bb_upper, bb_middle, bb_lower)
                    VALUES (:symbol, :timeframe, :timestamp, :rsi, :ema_fast, :ema_slow, :bb_upper, :bb_middle, :bb_lower)
                    ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                        rsi = EXCLUDED.rsi,
                        ema_fast = EXCLUDED.ema_fast,
                        ema_slow = EXCLUDED.ema_slow,
                        bb_upper = EXCLUDED.bb_upper,
                        bb_middle = EXCLUDED.bb_middle,
                        bb_lower = EXCLUDED.bb_lower;
                """), params)
            else:
                conn.execute(text("""
                    INSERT OR REPLACE INTO indicators (symbol, timeframe, timestamp, rsi, ema_fast, ema_slow, bb_upper, bb_middle, bb_lower)
                    VALUES (:symbol, :timeframe, :timestamp, :rsi, :ema_fast, :ema_slow, :bb_upper, :bb_middle, :bb_lower)
                """), params)
                
            return True
    except Exception as e:
        logger.error(f"Failed to save indicators for {symbol} at {timestamp} ({timeframe}): {e}")
        return False


def log_trade_to_csv(trade_data: Dict[str, Any]) -> bool:
    try:
        import csv
        
        logs_dir = settings.db_path.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        
        if "test" in settings.db_file:
            csv_path = logs_dir / "trades_test.csv"
        else:
            csv_path = logs_dir / "trades.csv"
            
        file_exists = csv_path.exists()
        
        headers = [
            "Timestamp", "Date Time", "Symbol", "Strategy", "Side",
            "Price", "Amount", "Cost", "Fee", "PnL", "Order ID"
        ]
        
        ts = trade_data["timestamp"]
        ts_sec = ts / 1000.0 if ts > 1e11 else ts
        dt_str = datetime.datetime.fromtimestamp(ts_sec, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        row = [
            trade_data["timestamp"],
            dt_str,
            trade_data["symbol"],
            trade_data["strategy"],
            trade_data["side"],
            trade_data["price"],
            trade_data["amount"],
            trade_data["cost"],
            trade_data.get("fee", ""),
            trade_data.get("pnl", ""),
            trade_data["order_id"]
        ]
        
        with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists or csv_path.stat().st_size == 0:
                writer.writerow(headers)
            writer.writerow(row)
            
        logger.info(f"TRADE EXCEL LOGGED: Saved trade {trade_data['order_id']} to {csv_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to log trade to CSV: {e}")
        return False


def get_active_position_buy_order_id(symbol: str) -> Optional[str]:
    try:
        with get_db_connection() as conn:
            res = conn.execute(text("""
                SELECT SUM(CASE WHEN side = 'BUY' THEN amount ELSE -amount END) as net_amount
                FROM trades
                WHERE symbol = :symbol
            """), {"symbol": symbol})
            row = res.fetchone()
            net_amount = float(row[0]) if row and row[0] is not None else 0.0
            
            if net_amount > 1e-6:
                res2 = conn.execute(text("""
                    SELECT order_id 
                    FROM trades 
                    WHERE symbol = :symbol AND side = 'BUY' 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                """), {"symbol": symbol})
                buy_row = res2.fetchone()
                if buy_row:
                    return buy_row[0]
    except Exception as e:
        logger.error(f"Error getting active position buy order ID for {symbol}: {e}")
    return None


def log_trade_to_individual_files(trade_data: Dict[str, Any]) -> bool:
    try:
        import csv
        
        symbol = trade_data["symbol"]
        symbol_clean = symbol.replace("/", "_")
        side = trade_data["side"]
        
        logs_dir = settings.db_path.parent / "logs" / "trades"
        logs_dir.mkdir(parents=True, exist_ok=True)
        
        if side == "BUY":
            buy_order_id = trade_data["order_id"]
        else:
            buy_order_id = get_active_position_buy_order_id(symbol)
            if not buy_order_id:
                buy_order_id = f"unknown_{int(time.time())}"
                
        test_suffix = "_test" if "test" in settings.db_file else ""
        base_filename = f"trade_{symbol_clean}_{buy_order_id}{test_suffix}"
        csv_path = logs_dir / f"{base_filename}.csv"
        log_path = logs_dir / f"{base_filename}.log"
        
        file_exists = csv_path.exists()
        headers = [
            "Timestamp", "Date Time", "Symbol", "Strategy", "Side",
            "Price", "Amount", "Cost", "Fee", "PnL", "Order ID"
        ]
        
        ts = trade_data["timestamp"]
        ts_sec = ts / 1000.0 if ts > 1e11 else ts
        dt_str = datetime.datetime.fromtimestamp(ts_sec, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        row = [
            trade_data["timestamp"],
            dt_str,
            trade_data["symbol"],
            trade_data["strategy"],
            trade_data["side"],
            trade_data["price"],
            trade_data["amount"],
            trade_data["cost"],
            trade_data.get("fee", ""),
            trade_data.get("pnl", ""),
            trade_data["order_id"]
        ]
        
        with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists or csv_path.stat().st_size == 0:
                writer.writerow(headers)
            writer.writerow(row)
            
        log_message = f"{dt_str} [{side}] {symbol} - Executed {side} order of {trade_data['amount']} at price {trade_data['price']}. Strategy: {trade_data['strategy']}, Cost: {trade_data['cost']}, Fee: {trade_data.get('fee')}, PnL: {trade_data.get('pnl')}, Order ID: {trade_data['order_id']}\n"
        with open(log_path, mode="a", encoding="utf-8") as f:
            f.write(log_message)
            
        logger.info(f"TRADE INDIVIDUAL LOGGED: Saved trade details to {csv_path} and {log_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to log trade to individual files: {e}")
        return False


def log_to_active_trade_file(symbol: str, message: str) -> None:
    try:
        buy_order_id = get_active_position_buy_order_id(symbol)
        if buy_order_id:
            symbol_clean = symbol.replace("/", "_")
            test_suffix = "_test" if "test" in settings.db_file else ""
            base_filename = f"trade_{symbol_clean}_{buy_order_id}{test_suffix}"
            
            logs_dir = settings.db_path.parent / "logs" / "trades"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / f"{base_filename}.log"
            
            dt_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            with open(log_path, mode="a", encoding="utf-8") as f:
                f.write(f"{dt_str} [INFO] {message}\n")
    except Exception as e:
        pass


def log_trade(trade_data: Dict[str, Any]) -> bool:
    try:
        with get_db_connection() as conn:
            conn.execute(text("""
                INSERT INTO trades (timestamp, symbol, strategy, side, price, amount, cost, fee, pnl, order_id)
                VALUES (:timestamp, :symbol, :strategy, :side, :price, :amount, :cost, :fee, :pnl, :order_id);
            """), {
                "timestamp": trade_data["timestamp"],
                "symbol": trade_data["symbol"],
                "strategy": trade_data["strategy"],
                "side": trade_data["side"],
                "price": trade_data["price"],
                "amount": trade_data["amount"],
                "cost": trade_data["cost"],
                "fee": trade_data.get("fee"),
                "pnl": trade_data.get("pnl"),
                "order_id": trade_data["order_id"]
            })
            logger.info(
                f"TRADE LOGGED: {trade_data['side']} {trade_data['amount']} {trade_data['symbol']} "
                f"at {trade_data['price']} | PnL: {trade_data.get('pnl')}"
            )
            
            log_trade_to_csv(trade_data)
            log_trade_to_individual_files(trade_data)
            
            return True
    except Exception as e:
        logger.error(f"Failed to log trade {trade_data.get('order_id')}: {e}")
        return False


def get_strategy_data(symbol: str, limit: int = 100, timeframe: str = "15m") -> Any:
    try:
        query = """
            SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume,
                   i.rsi, i.ema_fast, i.ema_slow, i.bb_upper, i.bb_middle, i.bb_lower
            FROM candles c
            LEFT JOIN indicators i ON c.symbol = i.symbol AND c.timeframe = i.timeframe AND c.timestamp = i.timestamp
            WHERE c.symbol = :symbol AND c.timeframe = :timeframe
            ORDER BY c.timestamp DESC
            LIMIT :limit
        """
        df = pd.read_sql_query(
            sql=text(query), 
            con=engine, 
            params={"symbol": symbol, "timeframe": timeframe, "limit": limit}
        )
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Failed to fetch merged strategy data for {symbol} ({timeframe}): {e}")
        return pd.DataFrame()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = initialize_db()
    print(f"\nDatabase Initialization Status: {success}")
    
    df = get_strategy_data("BTC/USDT", 5)
    print(f"Sample strategy DataFrame (len={len(df)}):")
    print(df.head() if not df.empty else "Empty DataFrame")
