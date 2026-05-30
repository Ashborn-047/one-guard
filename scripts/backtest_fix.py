import os
import sys
import pandas as pd
import pandas_ta as ta
import ccxt
import time

# Ensure we can import from src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.strategies.rsi import generate_rsi_signal
from src.risk import calculate_sl_tp

def fetch_data(symbol='BTC/USDT', timeframe='1h', limit=1000):
    print(f"Fetching {limit} candles of {timeframe} data for {symbol}...")
    exchange = ccxt.binance()
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def simulate_strategy(df, inverted=False, use_atr_sl=False):
    # Calculate indicators
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    
    in_position = False
    entry_price = 0.0
    entry_atr = 0.0
    
    trades = []
    
    for i in range(15, len(df)):
        # We simulate what the bot saw at index i
        # The bot looks at the completed candle, so we slice up to i
        window = df.iloc[:i+1].copy()
        
        # Manually invert the logic for testing the "old" behavior
        signal = "HOLD"
        latest_row = window.iloc[-1]
        prev_row = window.iloc[-2]
        
        rsi_val = latest_row['rsi']
        prev_rsi = prev_row['rsi']
        
        if inverted:
            # Old inverted logic
            if prev_rsi >= 30.0 and rsi_val < 30.0:
                signal = "BUY"
            elif prev_rsi <= 70.0 and rsi_val > 70.0:
                signal = "SELL"
        else:
            # New correct logic
            signal = generate_rsi_signal(window)
            
        current_price = latest_row['close']
        
        # Check exits if in position
        if in_position:
            sl, tp = calculate_sl_tp(
                "BUY", entry_price, sl_percent=0.02, tp_percent=0.04, 
                use_atr_sl=use_atr_sl, atr_value=entry_atr, atr_multiplier=1.5
            )
            
            # Did we hit SL or TP in this candle? (Simplified: check low and high)
            low = latest_row['low']
            high = latest_row['high']
            
            exit_price = None
            reason = None
            
            if low <= sl:
                exit_price = sl
                reason = "STOP_LOSS"
            elif high >= tp:
                exit_price = tp
                reason = "TAKE_PROFIT"
            elif signal == "SELL":
                exit_price = current_price
                reason = "SIGNAL"
                
            if exit_price is not None:
                pnl_pct = (exit_price - entry_price) / entry_price
                trades.append({
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl_pct': pnl_pct,
                    'reason': reason
                })
                in_position = False
                continue
                
        # Check entries
        if signal == "BUY" and not in_position:
            in_position = True
            entry_price = current_price
            entry_atr = latest_row['atr']

    # Compile stats
    if len(trades) == 0:
        return {"total_trades": 0, "win_rate": 0.0, "net_pnl_pct": 0.0, "sl_hits": 0, "tp_hits": 0, "signal_exits": 0}
        
    wins = sum(1 for t in trades if t['pnl_pct'] > 0)
    sl_hits = sum(1 for t in trades if t['reason'] == "STOP_LOSS")
    tp_hits = sum(1 for t in trades if t['reason'] == "TAKE_PROFIT")
    signal_exits = sum(1 for t in trades if t['reason'] == "SIGNAL")
    net_pnl = sum(t['pnl_pct'] for t in trades)
    
    return {
        "total_trades": len(trades),
        "win_rate": (wins / len(trades)) * 100,
        "net_pnl_pct": net_pnl * 100,
        "sl_hits": sl_hits,
        "tp_hits": tp_hits,
        "signal_exits": signal_exits
    }

if __name__ == "__main__":
    df = fetch_data(symbol='BTC/USDT', timeframe='1h', limit=1000)
    
    print("\n--- BACKTEST RESULTS ---")
    
    old_stats = simulate_strategy(df, inverted=True, use_atr_sl=False)
    print(f"\n[OLD INVERTED LOGIC] 2% Stop Loss")
    print(f"Total Trades: {old_stats['total_trades']}")
    print(f"Win Rate:     {old_stats['win_rate']:.2f}%")
    print(f"Net PnL:      {old_stats['net_pnl_pct']:.2f}%")
    print(f"SL Hits:      {old_stats['sl_hits']}")
    
    new_stats_fixed = simulate_strategy(df, inverted=False, use_atr_sl=False)
    print(f"\n[NEW CORRECT LOGIC] 2% Stop Loss (Current Default)")
    print(f"Total Trades: {new_stats_fixed['total_trades']}")
    print(f"Win Rate:     {new_stats_fixed['win_rate']:.2f}%")
    print(f"Net PnL:      {new_stats_fixed['net_pnl_pct']:.2f}%")
    print(f"SL Hits:      {new_stats_fixed['sl_hits']}")
    
    new_stats_atr = simulate_strategy(df, inverted=False, use_atr_sl=True)
    print(f"\n[NEW CORRECT LOGIC] ATR Stop Loss (Optional Toggle)")
    print(f"Total Trades: {new_stats_atr['total_trades']}")
    print(f"Win Rate:     {new_stats_atr['win_rate']:.2f}%")
    print(f"Net PnL:      {new_stats_atr['net_pnl_pct']:.2f}%")
    print(f"SL Hits:      {new_stats_atr['sl_hits']}")
    print()
