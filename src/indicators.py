import logging
import pandas as pd
import pandas_ta as ta
from typing import Dict, Any, Optional

logger = logging.getLogger("OneGuard.Indicators")

def calculate_technical_indicators(candles_df: pd.DataFrame) -> Optional[Dict[str, float]]:
    """
    Computes technical indicators for a given symbol DataFrame of OHLCV.
    Expects candles_df to have columns: timestamp, open, high, low, close, volume,
    ordered chronologically.
    Returns:
        Dict of the LATEST calculated indicator values, or None if calculation fails.
    """
    if candles_df.empty or len(candles_df) < 30:
        logger.warning(
            f"Insufficient data points to calculate indicators. "
            f"Need at least 30 candles, got {len(candles_df)}."
        )
        return None

    try:
        # 1. Compute RSI (14)
        rsi_series = ta.rsi(close=candles_df["close"], length=14)
        if rsi_series is None or rsi_series.empty:
            logger.error("Failed to compute RSI series.")
            return None
            
        # 2. Compute EMAs (9 and 21)
        ema_fast_series = ta.ema(close=candles_df["close"], length=9)
        ema_slow_series = ta.ema(close=candles_df["close"], length=21)
        
        # 3. Compute Bollinger Bands (20, 2)
        bb_df = ta.bbands(close=candles_df["close"], length=20, std=1)
        if bb_df is None or bb_df.empty:
            logger.error("Failed to compute Bollinger Bands series.")
            return None
            
        # Extract the latest values (last row in DataFrame)
        latest_idx = candles_df.index[-1]
        
        # Bollinger Bands output column names vary by pandas-ta version
        # Usually it is BBU_20_2.0, BBM_20_2.0, BBL_20_2.0
        bbu_col = [col for col in bb_df.columns if col.startswith("BBU")][0]
        bbm_col = [col for col in bb_df.columns if col.startswith("BBM")][0]
        bbl_col = [col for col in bb_df.columns if col.startswith("BBL")][0]

        latest_values = {
            "rsi": float(rsi_series.loc[latest_idx]) if not pd.isna(rsi_series.loc[latest_idx]) else None,
            "ema_fast": float(ema_fast_series.loc[latest_idx]) if not pd.isna(ema_fast_series.loc[latest_idx]) else None,
            "ema_slow": float(ema_slow_series.loc[latest_idx]) if not pd.isna(ema_slow_series.loc[latest_idx]) else None,
            "bb_upper": float(bb_df.loc[latest_idx, bbu_col]) if not pd.isna(bb_df.loc[latest_idx, bbu_col]) else None,
            "bb_middle": float(bb_df.loc[latest_idx, bbm_col]) if not pd.isna(bb_df.loc[latest_idx, bbm_col]) else None,
            "bb_lower": float(bb_df.loc[latest_idx, bbl_col]) if not pd.isna(bb_df.loc[latest_idx, bbl_col]) else None,
        }
        
        logger.debug(f"Calculated Indicators: {latest_values}")
        return latest_values

    except Exception as e:
        logger.error(f"Error occurred during indicator calculations: {e}")
        return None
