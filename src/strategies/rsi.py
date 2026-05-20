import logging
import pandas as pd
from typing import Literal

logger = logging.getLogger("OneGuard.Strategy.RSI")

def generate_rsi_signal(df: pd.DataFrame, rsi_lower: float = 30.0, rsi_upper: float = 70.0) -> Literal["BUY", "SELL", "HOLD"]:
    """
    Generates trading signals based on the Relative Strength Index (RSI).
    Args:
        df: DataFrame containing at least a 'rsi' column. Expects rows ordered oldest to newest.
        rsi_lower: Oversold threshold.
        rsi_upper: Overbought threshold.
    Returns:
        "BUY", "SELL", or "HOLD"
    """
    if df.empty or 'rsi' not in df.columns:
        logger.warning("Empty DataFrame or missing 'rsi' column in RSI strategy.")
        return "HOLD"

    # We look at the latest completed candle (the last row in chronological order)
    latest_row = df.iloc[-1]
    prev_row = df.iloc[-2] if len(df) > 1 else None
    
    rsi_val = latest_row['rsi']
    
    if rsi_val is None or pd.isna(rsi_val):
        return "HOLD"
        
    # Check for crossover of oversold threshold (RSI crossing below 30)
    # If previous candle RSI was above 30, and current is below 30, trigger BUY
    if prev_row is not None and not pd.isna(prev_row['rsi']):
        prev_rsi = prev_row['rsi']
        if prev_rsi >= rsi_lower and rsi_val < rsi_lower:
            logger.info(f"RSI Oversold Crossover: {prev_rsi:.2f} -> {rsi_val:.2f} (Threshold: {rsi_lower}) | BUY Signal")
            return "BUY"
        # Check for crossover of overbought threshold (RSI crossing above 70)
        elif prev_rsi <= rsi_upper and rsi_val > rsi_upper:
            logger.info(f"RSI Overbought Crossover: {prev_rsi:.2f} -> {rsi_val:.2f} (Threshold: {rsi_upper}) | SELL Signal")
            return "SELL"
    else:
        # Fallback to absolute thresholds if we don't have enough history
        if rsi_val < rsi_lower:
            return "BUY"
        elif rsi_val > rsi_upper:
            return "SELL"
            
    return "HOLD"
