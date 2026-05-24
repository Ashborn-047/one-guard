import logging
from typing import Dict
from src.db import get_strategy_data
from src.strategies.rsi import generate_rsi_signal
from src.strategies.bb import generate_bb_signal
from src.strategies.ema import generate_ema_signal

logger = logging.getLogger("OneGuard.Strategies")

def evaluate_all_strategies(symbol: str) -> Dict[str, str]:
    """
    Retrieves the latest indicators for a symbol and evaluates all three active strategies.
    Returns:
        Dict[str, str]: Map of strategy name -> signal ('BUY', 'SELL', 'HOLD')
    """
    logger.debug(f"Evaluating all strategy modules for symbol: {symbol}")
    
    # We retrieve the last 100 periods to have enough lookback for indicators
    df = get_strategy_data(symbol, limit=100)
    
    if df.empty or len(df) < 2:
        logger.warning(f"Insufficient historical data to evaluate strategies for {symbol}.")
        return {
            "RSI": "HOLD",
            "BB": "HOLD",
            "EMA": "HOLD"
        }
        
    # Use looser thresholds in sandbox for quick signals, otherwise standard 30/70
    from src.config import settings
    if settings.is_sandbox:
        rsi_signal = generate_rsi_signal(df, rsi_lower=45.0, rsi_upper=55.0)
    else:
        rsi_signal = generate_rsi_signal(df, rsi_lower=30.0, rsi_upper=70.0)
    bb_signal = generate_bb_signal(df)
    ema_signal = generate_ema_signal(df)
    
    signals = {
        "RSI": rsi_signal,
        "BB": bb_signal,
        "EMA": ema_signal
    }
    
    logger.info(f"Strategy evaluation results for {symbol}: {signals}")
    return signals


if __name__ == "__main__":
    # Test strategy evaluation
    logging.basicConfig(level=logging.INFO)
    print("--- Strategy Evaluator Test ---")
    results = evaluate_all_strategies("BTC/USDT")
    print(f"Final Signal Map: {results}")
