import logging
import pandas as pd
from typing import Literal

logger = logging.getLogger("OneGuard.Strategy.EMA")

def generate_ema_signal(df: pd.DataFrame) -> Literal["BUY", "SELL", "HOLD"]:
    """
    Generates trading signals based on Exponential Moving Average (EMA) Crossover.
    Args:
        df: DataFrame containing 'ema_fast' and 'ema_slow' columns.
            Expects rows ordered oldest to newest.
    Returns:
        "BUY", "SELL", or "HOLD"
    """
    required_cols = ['ema_fast', 'ema_slow']
    if df.empty or not all(col in df.columns for col in required_cols):
        logger.warning("Empty DataFrame or missing required EMA columns ('ema_fast', 'ema_slow').")
        return "HOLD"

    if len(df) < 2:
        # We need at least 2 data points to detect a crossover
        return "HOLD"

    latest_row = df.iloc[-1]
    prev_row = df.iloc[-2]
    
    fast_curr = latest_row['ema_fast']
    slow_curr = latest_row['ema_slow']
    fast_prev = prev_row['ema_fast']
    slow_prev = prev_row['ema_slow']
    
    if pd.isna(fast_curr) or pd.isna(slow_curr) or pd.isna(fast_prev) or pd.isna(slow_prev):
        return "HOLD"
        
    # Golden Cross (Fast EMA crosses above Slow EMA) -> BUY
    if fast_prev <= slow_prev and fast_curr > slow_curr:
        logger.info(f"EMA Golden Cross: Fast EMA crossed ABOVE Slow EMA ({fast_prev:.2f}/{slow_prev:.2f} -> {fast_curr:.2f}/{slow_curr:.2f}) | BUY Signal")
        return "BUY"
        
    # Death Cross (Fast EMA crosses below Slow EMA) -> SELL
    elif fast_prev >= slow_prev and fast_curr < slow_curr:
        logger.info(f"EMA Death Cross: Fast EMA crossed BELOW Slow EMA ({fast_prev:.2f}/{slow_prev:.2f} -> {fast_curr:.2f}/{slow_curr:.2f}) | SELL Signal")
        return "SELL"
        
    return "HOLD"
