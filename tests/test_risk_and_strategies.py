import unittest
import time
import pandas as pd
import sqlite3
from typing import Generator
from unittest.mock import patch, MagicMock

# Import modules to test
from src.config import settings
from src.db import initialize_db, log_trade, get_db_connection
from src.risk import (
    verify_trade_execution_safety,
    calculate_position_size,
    calculate_sl_tp,
    get_weekly_pnl,
    get_active_positions,
    get_last_loss_timestamp
)
from src.strategies.rsi import generate_rsi_signal
from src.strategies.bb import generate_bb_signal
from src.strategies.ema import generate_ema_signal

class TestOneGuardRiskAndStrategies(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        # Override the database file configuration to use a separate test database
        cls.original_db_file = settings.db_file
        object.__setattr__(settings, 'db_file', 'one_guard_test.db')
        
        # Clear or initialize the test database
        if settings.db_path.exists():
            try:
                settings.db_path.unlink()
            except PermissionError:
                pass
        initialize_db()
        
    @classmethod
    def tearDownClass(cls):
        # Restore original database configuration
        object.__setattr__(settings, 'db_file', cls.original_db_file)
        if settings.db_path.exists():
            try:
                settings.db_path.unlink()
            except PermissionError:
                pass # SQLite file might be temporarily locked, ignore
                
    def setUp(self):
        # Clear trades table before every test
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trades;")
            cursor.execute("DELETE FROM candles;")
            cursor.execute("DELETE FROM indicators;")
            
        # Reset halt setting
        self.set_emergency_halt(False)
        
    def set_emergency_halt(self, state: bool):
        # Patch/override settings values since dataclass is frozen
        object.__setattr__(settings, 'emergency_halt', state)

    def set_weekly_drawdown_limit(self, value: float):
        object.__setattr__(settings, 'weekly_drawdown_limit', value)

    def set_loss_cooldown_minutes(self, value: int):
        object.__setattr__(settings, 'loss_cooldown_minutes', value)

    # ---------------------------------------------------------
    # RISK ENGINE TESTS
    # ---------------------------------------------------------
    
    def test_emergency_halt(self):
        self.set_emergency_halt(True)
        is_safe, reason = verify_trade_execution_safety("BTC/USDT", "BUY", 50000.0)
        self.assertFalse(is_safe)
        self.assertIn("Safety Halt active", reason)
        
    def test_duplicate_position(self):
        # 1. Log a BUY trade for BTC/USDT (opening a position)
        log_trade({
            "timestamp": int(time.time() * 1000) - 10000,
            "symbol": "BTC/USDT",
            "strategy": "RSI",
            "side": "BUY",
            "price": 50000.0,
            "amount": 0.002,
            "cost": 100.0,
            "fee": 0.1,
            "pnl": None,
            "order_id": "test_buy_01"
        })
        
        # 2. Check if another BUY on BTC/USDT is blocked
        is_safe, reason = verify_trade_execution_safety("BTC/USDT", "BUY", 51000.0, "RSI")
        self.assertFalse(is_safe)
        self.assertIn("Already holding active BUY position", reason)
        
        # 3. Check if a SELL on BTC/USDT is permitted
        is_safe, reason = verify_trade_execution_safety("BTC/USDT", "SELL", 51000.0, "RSI")
        self.assertTrue(is_safe)
        
        # 4. Check if SELL exit from a different strategy is blocked
        is_safe, reason = verify_trade_execution_safety("BTC/USDT", "SELL", 51000.0, "EMA")
        self.assertFalse(is_safe)
        self.assertIn("Strategy mismatch", reason)

    def test_max_open_trades(self):
        # Open 3 positions on different symbols (BTC, ETH, LTC)
        symbols = ["BTC/USDT", "ETH/USDT", "LTC/USDT"]
        for idx, sym in enumerate(symbols):
            log_trade({
                "timestamp": int(time.time() * 1000) - (10000 * (idx + 1)),
                "symbol": sym,
                "strategy": "BB",
                "side": "BUY",
                "price": 100.0 * (idx + 1),
                "amount": 1.0,
                "cost": 100.0 * (idx + 1),
                "fee": 0.1,
                "pnl": None,
                "order_id": f"test_buy_{idx}"
            })
            
        # Check active positions count
        positions = get_active_positions()
        self.assertEqual(len(positions), 3)
        
        # 4th position (e.g. SOL/USDT) should be blocked by Max Open Trades rule
        is_safe, reason = verify_trade_execution_safety("SOL/USDT", "BUY", 20.0, "BB")
        self.assertFalse(is_safe)
        self.assertIn("Max simultaneous open trades reached", reason)

    def test_loss_cooldown(self):
        self.set_loss_cooldown_minutes(30)
        
        # Log a losing trade that ended right now
        now_ms = int(time.time() * 1000)
        log_trade({
            "timestamp": now_ms,
            "symbol": "BTC/USDT",
            "strategy": "EMA",
            "side": "SELL",
            "price": 49000.0,
            "amount": 0.002,
            "cost": 98.0,
            "fee": 0.1,
            "pnl": -2.1,  # A realized loss
            "order_id": "test_sell_loss"
        })
        
        # Check that we are blocked from buying BTC/USDT or any other coin
        is_safe, reason = verify_trade_execution_safety("ETH/USDT", "BUY", 3000.0, "EMA")
        self.assertFalse(is_safe)
        self.assertIn("Loss cooldown active", reason)
        
        # If we patch the last loss timestamp to be 31 minutes ago
        thirty_one_mins_ago = now_ms - (31 * 60 * 1000)
        with patch('src.risk.get_last_loss_timestamp', return_value=thirty_one_mins_ago):
            is_safe, reason = verify_trade_execution_safety("ETH/USDT", "BUY", 3000.0, "EMA")
            self.assertTrue(is_safe)

    def test_weekly_drawdown_limit(self):
        self.set_weekly_drawdown_limit(15.0)
        
        # Log trades causing a total loss of $20.00 in the current week
        now_ms = int(time.time() * 1000)
        log_trade({
            "timestamp": now_ms - 2000,
            "symbol": "BTC/USDT",
            "strategy": "RSI",
            "side": "SELL",
            "price": 49000.0,
            "amount": 0.002,
            "cost": 98.0,
            "fee": 0.1,
            "pnl": -10.0,
            "order_id": "test_loss_01"
        })
        log_trade({
            "timestamp": now_ms - 1000,
            "symbol": "ETH/USDT",
            "strategy": "RSI",
            "side": "SELL",
            "price": 28000.0,
            "amount": 0.005,
            "cost": 140.0,
            "fee": 0.1,
            "pnl": -10.0,
            "order_id": "test_loss_02"
        })
        
        # Current weekly PnL should be -20.0 (exceeding limit of -15.0)
        weekly_pnl = get_weekly_pnl()
        self.assertEqual(weekly_pnl, -20.0)
        
        # New buy signal must be blocked
        is_safe, reason = verify_trade_execution_safety("SOL/USDT", "BUY", 20.0, "RSI")
        self.assertFalse(is_safe)
        self.assertIn("Weekly drawdown limit reached", reason)

    # ---------------------------------------------------------
    # STRATEGY SIGNAL GENERATION TESTS
    # ---------------------------------------------------------

    def test_rsi_signals(self):
        # Case 1: RSI crosses below oversold (30) -> BUY
        df_buy = pd.DataFrame([
            {"rsi": 32.0},
            {"rsi": 28.0}  # Latest rsi is < 30 (crossover)
        ])
        self.assertEqual(generate_rsi_signal(df_buy), "BUY")
        
        # Case 2: RSI crosses above overbought (70) -> SELL
        df_sell = pd.DataFrame([
            {"rsi": 68.0},
            {"rsi": 72.0}  # Latest rsi is > 70 (crossover)
        ])
        self.assertEqual(generate_rsi_signal(df_sell), "SELL")
        
        # Case 3: RSI remains in neutral territory -> HOLD
        df_hold = pd.DataFrame([
            {"rsi": 50.0},
            {"rsi": 52.0}
        ])
        self.assertEqual(generate_rsi_signal(df_hold), "HOLD")

    def test_bb_signals(self):
        # Case 1: Close crosses below BB lower -> BUY
        df_buy = pd.DataFrame([
            {"close": 100.0, "bb_lower": 95.0, "bb_upper": 105.0},
            {"close": 94.0,  "bb_lower": 95.0, "bb_upper": 105.0}  # Close below BB lower
        ])
        self.assertEqual(generate_bb_signal(df_buy), "BUY")
        
        # Case 2: Close crosses above BB upper -> SELL
        df_sell = pd.DataFrame([
            {"close": 100.0, "bb_lower": 95.0, "bb_upper": 105.0},
            {"close": 106.0, "bb_lower": 95.0, "bb_upper": 105.0}  # Close above BB upper
        ])
        self.assertEqual(generate_bb_signal(df_sell), "SELL")

    def test_ema_signals(self):
        # Case 1: Fast crosses above slow -> BUY (Golden Cross)
        df_buy = pd.DataFrame([
            {"ema_fast": 99.0,  "ema_slow": 100.0},
            {"ema_fast": 101.0, "ema_slow": 100.5}  # Fast crossed above slow
        ])
        self.assertEqual(generate_ema_signal(df_buy), "BUY")
        
        # Case 2: Fast crosses below slow -> SELL (Death Cross)
        df_sell = pd.DataFrame([
            {"ema_fast": 101.0, "ema_slow": 100.0},
            {"ema_fast": 99.5,  "ema_slow": 100.5}  # Fast crossed below slow
        ])
        self.assertEqual(generate_ema_signal(df_sell), "SELL")


if __name__ == "__main__":
    unittest.main()
