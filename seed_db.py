import sys
import os
import time
import math
import sqlite3
import random
import pandas as pd

# The workspace is the root path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.config import settings
from src.db import initialize_db, save_candles, save_indicators, log_trade, get_db_connection
from src.indicators import calculate_technical_indicators

def seed():
    print("Database path:", settings.db_path)
    
    # Initialize schema
    success = initialize_db()
    if not success:
        print("Failed to initialize database")
        return
        
    # Clear existing data so we start fresh
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM candles;")
        cursor.execute("DELETE FROM indicators;")
        cursor.execute("DELETE FROM trades;")
        print("Cleared candles, indicators, and trades.")

    # Time configuration
    # 15 minutes in milliseconds
    interval_ms = 15 * 60 * 1000
    now_ms = int(time.time() * 1000)
    
    # Let's generate candles for BTC/USDT and ETH/USDT
    for symbol, start_price in [("BTC/USDT", 62000.0), ("ETH/USDT", 3100.0)]:
        print(f"Generating candles for {symbol}...")
        candles = []
        curr_price = start_price
        
        # 100 candles, starting from 100 intervals ago
        for i in range(100):
            t = now_ms - (100 - i) * interval_ms
            
            # Simple random walk with upward trend
            change = curr_price * random.uniform(-0.012, 0.015)
            open_p = curr_price
            close_p = curr_price + change
            high_p = max(open_p, close_p) + (curr_price * random.uniform(0.001, 0.005))
            low_p = min(open_p, close_p) - (curr_price * random.uniform(0.001, 0.005))
            volume = random.uniform(10.0, 150.0) if "BTC" in symbol else random.uniform(100.0, 1500.0)
            
            candles.append((t, open_p, high_p, low_p, close_p, volume))
            curr_price = close_p
            
        # Save candles
        save_candles(symbol, candles)
        print(f"Saved 100 candles for {symbol}.")
        
        # Calculate and save indicators in a rolling fashion
        # Convert to pandas DataFrame for pandas_ta
        db_candles = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp, open, high, low, close, volume FROM candles WHERE symbol = ? ORDER BY timestamp ASC", (symbol,))
            db_candles = [dict(row) for row in cursor.fetchall()]
            
        df = pd.DataFrame(db_candles)
        
        print("Calculating indicators...")
        # Slice and calculate indicators for each timestamp from index 30 onwards
        for i in range(30, len(df)):
            sub_df = df.iloc[:i+1]
            indicators = calculate_technical_indicators(sub_df)
            if indicators:
                t = int(df["timestamp"].iloc[i])
                save_indicators(symbol, t, indicators)
        print(f"Calculated and saved indicators for {symbol}.")

    print("Database seeding completed successfully!")

if __name__ == "__main__":
    seed()
