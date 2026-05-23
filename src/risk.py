import logging
import time
import datetime
from typing import Dict, Any, Tuple, Optional
from sqlalchemy import text
from src.config import settings
from src.db import get_db_connection

logger = logging.getLogger("OneGuard.Risk")

# Epsilon value to account for small float precision issues when calculating position balances
EPSILON = 1e-6

def get_start_of_week_ms() -> int:
    """
    Returns the timestamp (in milliseconds since epoch) for Monday 00:00:00 UTC of the current week.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    start_of_week = now - datetime.timedelta(days=now.weekday())
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start_of_week.timestamp() * 1000)


def get_weekly_pnl() -> float:
    """
    Calculates the sum of realized PnL for all closed trades since the start of the current week.
    """
    start_ms = get_start_of_week_ms()
    try:
        with get_db_connection() as conn:
            res = conn.execute(text("""
                SELECT SUM(pnl) as total_pnl 
                FROM trades 
                WHERE timestamp >= :start_ms AND pnl IS NOT NULL
            """), {"start_ms": start_ms})
            row = res.fetchone()
            total_pnl = row[0] if row and row[0] is not None else 0.0
            return float(total_pnl)
    except Exception as e:
        logger.error(f"Error calculating weekly PnL: {e}")
        return 0.0


def get_active_positions() -> Dict[str, float]:
    """
    Calculates the currently held assets by summing buy quantities and subtracting sell quantities.
    Returns a dict mapping symbol -> current quantity.
    """
    positions = {}
    try:
        with get_db_connection() as conn:
            res = conn.execute(text("""
                SELECT symbol,
                       SUM(CASE WHEN side = 'BUY' THEN amount ELSE -amount END) as net_amount
                FROM trades
                GROUP BY symbol
            """))
            rows = res.fetchall()
            for row in rows:
                net_amount = float(row[1])
                if net_amount > EPSILON:
                    positions[row[0]] = net_amount
    except Exception as e:
        logger.error(f"Error checking active positions in database: {e}")
    return positions


def get_last_loss_timestamp() -> Optional[int]:
    """
    Returns the timestamp (in milliseconds) of the most recent trade that closed with a loss.
    """
    try:
        with get_db_connection() as conn:
            res = conn.execute(text("""
                SELECT timestamp 
                FROM trades 
                WHERE pnl < 0 
                ORDER BY timestamp DESC 
                LIMIT 1
            """))
            row = res.mappings().fetchone()
            return int(row['timestamp']) if row else None
    except Exception as e:
        logger.error(f"Error fetching last loss timestamp: {e}")
        return None


def calculate_position_size(symbol: str, current_price: float) -> float:
    """
    Calculates the quantity of token to purchase based on settings.max_position_size.
    Example: If max_position_size is 10.0 USDT and BTC price is 50,000, returns 10.0 / 50,000 = 0.0002 BTC.
    """
    if current_price <= 0:
        return 0.0
    
    # Calculate amount in base currency (e.g. amount of BTC to buy)
    qty = settings.max_position_size / current_price
    logger.debug(f"Position size calculation for {symbol} at price {current_price}: {qty:.6f} units (~{settings.max_position_size} USDT)")
    return qty


def calculate_sl_tp(side: str, entry_price: float, sl_percent: float = 0.02, tp_percent: float = 0.04) -> Tuple[float, float]:
    """
    Calculates hard stop-loss and take-profit price levels.
    Default parameters from process guidelines: 2% Stop Loss, 4% Take Profit.
    """
    if side.upper() == "BUY":
        stop_loss = entry_price * (1.0 - sl_percent)
        take_profit = entry_price * (1.0 + tp_percent)
    else:  # SELL (for short setups, or exit rules)
        stop_loss = entry_price * (1.0 + sl_percent)
        take_profit = entry_price * (1.0 - tp_percent)
        
    return round(stop_loss, 4), round(take_profit, 4)


def verify_trade_execution_safety(symbol: str, side: str, current_price: float, strategy: str = "") -> Tuple[bool, str]:
    """
    Validates signal execution against all risk limits.
    Returns:
        (is_safe: bool, reason: str)
    """
    side_upper = side.upper()
    if side_upper not in ("BUY", "SELL"):
        return False, f"Invalid order side '{side}'"

    # 1. Check Emergency Halt Switch
    if settings.emergency_halt:
        return False, "Safety Halt active (EMERGENCY_HALT=TRUE in settings)."

    # Get current DB states
    weekly_pnl = get_weekly_pnl()
    active_positions = get_active_positions()
    open_positions_count = len(active_positions)
    has_active_position = symbol in active_positions

    # 2. Check Weekly Drawdown Limit
    # Note: Weekly drawdown limit is checked as a negative amount, e.g. -weekly_drawdown_limit
    if weekly_pnl <= -settings.weekly_drawdown_limit:
        return False, f"Weekly drawdown limit reached ({weekly_pnl:.2f} <= -{settings.weekly_drawdown_limit:.2f}). Trading halted."

    # 3. Check Loss Cooldown (30-minute block after a realized loss)
    last_loss_ms = get_last_loss_timestamp()
    if last_loss_ms is not None:
        current_time_ms = int(time.time() * 1000)
        cooldown_duration_ms = settings.loss_cooldown_minutes * 60 * 1000
        elapsed_ms = current_time_ms - last_loss_ms
        if elapsed_ms < cooldown_duration_ms:
            remaining_mins = (cooldown_duration_ms - elapsed_ms) / (60 * 1000)
            return False, f"Loss cooldown active. Must wait {remaining_mins:.1f} more minutes before placing new trades."

    # 4. Check Position Direction Conflicts
    if side_upper == "BUY":
        # Cannot buy if we already hold an active position on this symbol
        if has_active_position:
            return False, f"Already holding active BUY position on {symbol} (Qty: {active_positions[symbol]:.6f})."
        
        # 5. Check Maximum Open Trades Limit
        if open_positions_count >= settings.max_open_trades:
            return False, f"Max simultaneous open trades reached ({open_positions_count} >= {settings.max_open_trades})."
            
    elif side_upper == "SELL":
        # Cannot sell to exit if we do not hold an active position on this symbol
        if not has_active_position:
            return False, f"Cannot execute SELL exit order: No active position found for {symbol}."
            
        # Verify strategy matches the opening strategy of this position
        if strategy:
            opening_strategy = None
            try:
                with get_db_connection() as conn:
                    res = conn.execute(text("""
                        SELECT strategy FROM trades 
                        WHERE symbol = :symbol AND side = 'BUY' 
                        ORDER BY timestamp DESC LIMIT 1
                    """), {"symbol": symbol})
                    row = res.mappings().fetchone()
                    if row:
                        opening_strategy = row['strategy']
            except Exception as e:
                logger.error(f"Error checking opening strategy for exit: {e}")
                
            # Allow exits if strategy matches or if it's an SL/TP exit opened by this strategy
            if opening_strategy and strategy != opening_strategy and not strategy.startswith(opening_strategy):
                return False, f"Strategy mismatch: position opened by '{opening_strategy}', cannot exit via '{strategy}'."

    logger.info(f"Risk checks passed for {side_upper} {symbol} at {current_price}. Open positions: {open_positions_count}/{settings.max_open_trades}. Weekly PnL: {weekly_pnl:.2f} USDT.")
    return True, "Risk validation succeeded."


if __name__ == "__main__":
    # Test queries directly
    logging.basicConfig(level=logging.INFO)
    print("--- Risk Engine Test Run ---")
    print(f"Start of week (MS): {get_start_of_week_ms()}")
    print(f"Weekly Realized PnL: {get_weekly_pnl()} USDT")
    print(f"Active Positions: {get_active_positions()}")
    print(f"Last Loss Timestamp: {get_last_loss_timestamp()}")
    
    # Run test validation
    is_safe, reason = verify_trade_execution_safety("BTC/USDT", "BUY", 65000.0)
    print(f"Validation Result (BUY BTC): {is_safe} | Reason: {reason}")
