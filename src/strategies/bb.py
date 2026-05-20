import logging
import pandas as pd
from typing import Literal

logger = logging.getLogger("OneGuard.Strategy.BB")

def generate_bb_signal(df: pd.DataFrame) -> Literal["BUY", "SELL", "HOLD"]:
    """
    Generates trading signals based on Bollinger Band breakouts/bounces.
    Args:
        df: DataFrame containing 'close', 'bb_lower', and 'bb_upper' columns.
            Expects rows ordered oldest to newest.
    Returns:
        "BUY", "SELL", or "HOLD"
    """
    required_cols = ['close', 'bb_lower', 'bb_upper']
    if df.empty or not all(col in df.columns for col in required_cols):
        logger.warning("Empty DataFrame or missing required BB columns ('close', 'bb_lower', 'bb_upper').")
        return "HOLD"

    latest_row = df.iloc[-1]
    prev_row = df.iloc[-2] if len(df) > 1 else None
    
    close = latest_row['close']
    bb_lower = latest_row['bb_lower']
    bb_upper = latest_row['bb_upper']
    
    if pd.isna(close) or pd.isna(bb_lower) or pd.isna(bb_upper):
        return "HOLD"
        
    # Check for crossover below the lower Bollinger Band (BUY)
    if prev_row is not None and not any(pd.isna(prev_row[c]) for c in required_cols):
        prev_close = prev_row['close']
        prev_lower = prev_row['bb_lower']
        prev_upper = prev_row['bb_upper']
        
        # Close price crossing below the lower band (underpriced)
        if prev_close >= prev_lower and close < bb_lower:
            logger.info(f"BB Lower Band Crossover: Close {close:.2f} < BB Lower {bb_lower:.2f} | BUY Signal")
            return "BUY"
            
        # Close price crossing above the upper band (overpriced)
        elif prev_close <= prev_upper and close > bb_upper:
            logger.info(f"BB Upper Band Crossover: Close {close:.2f} > BB Upper {bb_upper:.2f} | SELL Signal")
            return "SELL"
    else:
        # Fallback to absolute thresholds if we don't have enough history
        if close < bb_lower:
            return "BUY"
        elif close > bb_upper:
            return "SELL"
            
    return "HOLD"
