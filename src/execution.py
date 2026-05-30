import logging
import time
import traceback
from typing import Dict, Any, Optional
import ccxt
from sqlalchemy import text
from src.config import settings
from src.db import get_db_connection, log_trade, log_to_active_trade_file
from src.risk import (
    verify_trade_execution_safety,
    calculate_position_size,
    calculate_sl_tp,
    get_active_positions
)
from src.telemetry import (
    send_telegram_message,
    alert_trade_entry,
    alert_trade_exit,
    alert_system_error,
)

logger = logging.getLogger("OneGuard.Execution")

def initialize_execution_markets(exchange: ccxt.Exchange):
    """
    Loads exchange markets if they are not already loaded.
    Necessary for precision calculations.
    """
    if not exchange.markets:
        logger.info("Loading exchange markets for precision formatting...")
        exchange.load_markets()


def execute_market_order(
    exchange: ccxt.Exchange,
    symbol: str,
    side: str,
    strategy: str
) -> Optional[Dict[str, Any]]:
    """
    Places a market order on the exchange after performing risk validation.
    Logs successful executions to SQLite and broadcasts telemetry updates.
    """
    side_upper = side.upper()
    try:
        initialize_execution_markets(exchange)
        
        # 1. Fetch current price
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker['close']
        
        # 2. Verify trade safety with Risk Engine
        is_safe, reason = verify_trade_execution_safety(symbol, side_upper, current_price, strategy)
        if not is_safe:
            logger.warning(f"Trade safety check REJECTED for {side_upper} {symbol} ({strategy}): {reason}")
            return None
            
        # 3. Calculate quantity
        if side_upper == "BUY":
            qty = calculate_position_size(symbol, current_price)
        else:  # SELL (sell entire active position)
            active_positions = get_active_positions()
            qty = active_positions.get(symbol, 0.0)
            
        if qty <= 0:
            logger.warning(f"Calculated execution quantity for {symbol} is invalid (0 or negative).")
            return None

        # Format quantity to match exchange rules
        qty_formatted = float(exchange.amount_to_precision(symbol, qty))
        
        # Double check quantity against exchange minimums and precision
        min_amount = 0.0
        if symbol in exchange.markets and 'limits' in exchange.markets[symbol] and 'amount' in exchange.markets[symbol]['limits']:
            min_amount = exchange.markets[symbol]['limits']['amount'].get('min', 0.0)
            
        if qty_formatted <= 0 or (min_amount and qty_formatted < min_amount):
            logger.warning(f"Quantity {qty_formatted} is below exchange minimum {min_amount} for {symbol}. Skipped.")
            if side_upper == "SELL":
                logger.info(f"Clearing dust position of {qty} for {symbol} from database to prevent infinite loops.")
                trade_timestamp = int(time.time() * 1000)
                trade_data = {
                    "timestamp": trade_timestamp,
                    "symbol": symbol,
                    "strategy": f"{strategy}_dust_clear",
                    "side": "SELL",
                    "price": current_price,
                    "amount": qty,
                    "cost": 0.0,
                    "fee": 0.0,
                    "pnl": 0.0,
                    "order_id": f"dust_clear_{trade_timestamp}"
                }
                log_trade(trade_data)
            return None
            
        logger.info(f"PLACING MARKET ORDER: {side_upper} {qty_formatted} {symbol} (Strategy: {strategy})")
        
        # 4. Place order (simulated if keys are missing or placeholders)
        is_mock_execution = (
            not (exchange.apiKey and exchange.secret) or
            "your_binance_api_key" in exchange.apiKey or
            "your_binance_secret" in exchange.secret or
            "your_api_key" in exchange.apiKey or
            "your_secret_key" in exchange.secret
        )
        
        if is_mock_execution:
            trade_timestamp = int(time.time() * 1000)
            logger.info(f"[MOCK EXECUTION] Simulating {side_upper} {qty_formatted} {symbol} locally.")
            order = {
                'id': f"mock_{trade_timestamp}",
                'timestamp': trade_timestamp,
                'datetime': exchange.iso8601(trade_timestamp),
                'symbol': symbol,
                'side': side_upper.lower(),
                'price': current_price,
                'amount': qty_formatted,
                'filled': qty_formatted,
                'remaining': 0.0,
                'status': 'closed',
                'cost': qty_formatted * current_price,
                'fee': {'cost': qty_formatted * current_price * 0.001, 'currency': 'USDT'}
            }
        else:
            if side_upper == "BUY":
                order = exchange.create_market_buy_order(symbol, qty_formatted)
            else:
                order = exchange.create_market_sell_order(symbol, qty_formatted)
            logger.info(f"Order executed successfully on exchange. ID: {order.get('id')}")
        
        # 5. Extract executed details (CCXT normalization fallbacks)
        filled_qty = order.get('filled', qty_formatted)
        filled_price = order.get('average') or order.get('price') or current_price
        cost = order.get('cost') or (filled_qty * filled_price)
        
        # Estimate/fetch trade fee
        fee_cost = 0.0
        if order.get('fee'):
            fee_cost = order['fee'].get('cost', 0.0)
        else:
            # Fallback to standard Binance spot maker/taker fee of 0.1%
            fee_cost = cost * 0.001
            
        # 6. Calculate realized PnL for Sell/Exits
        pnl = None
        if side_upper == "SELL":
            entry_price = None
            try:
                with get_db_connection() as conn:
                    res = conn.execute(text("""
                        SELECT price, fee 
                        FROM trades 
                        WHERE symbol = :symbol AND side = 'BUY' 
                        ORDER BY timestamp DESC LIMIT 1
                    """), {"symbol": symbol})
                    row = res.mappings().fetchone()
                    if row:
                        entry_price = float(row['price'])
                        entry_fee = float(row['fee']) if row['fee'] is not None else 0.0
                        
                        # Realized PnL = (exit_price - entry_price) * exit_qty - entry_fee - exit_fee
                        pnl = (filled_price - entry_price) * filled_qty - entry_fee - fee_cost
            except Exception as db_err:
                logger.error(f"Error fetching entry price for PnL calculation: {db_err}")
                
            if pnl is None:
                # Basic fallback without fee adjustment
                pnl = (filled_price - current_price) * filled_qty
                
        # 7. Log trade execution to SQLite
        trade_timestamp = order.get('timestamp') or int(time.time() * 1000)
        trade_data = {
            "timestamp": trade_timestamp,
            "symbol": symbol,
            "strategy": strategy,
            "side": side_upper,
            "price": filled_price,
            "amount": filled_qty,
            "cost": cost,
            "fee": fee_cost,
            "pnl": pnl,
            "order_id": order.get('id') or f"mock_{trade_timestamp}"
        }
        
        log_trade(trade_data)
        
        # 8. Send structured Telegram alert
        if side_upper == "BUY":
            alert_trade_entry(symbol, side_upper, filled_qty, filled_price, strategy)
        else:
            realized_pnl = pnl if pnl is not None else 0.0
            alert_trade_exit(symbol, side_upper, filled_qty, filled_price, realized_pnl, strategy, reason="signal")

        return order

    except (ccxt.InsufficientFunds, ccxt.InvalidOrder) as e:
        err_msg = f"ORDER REJECTED ({type(e).__name__}): Could not execute {side_upper} {symbol}. Details: {e}"
        logger.error(err_msg)
        alert_system_error(f"{side_upper} {symbol}", e)
        
        # Prevent infinite loops: If a SELL order is rejected, clear it from DB
        if side_upper == "SELL":
            logger.warning(f"Clearing failed/dust position for {symbol} from database to prevent infinite loops.")
            trade_timestamp = int(time.time() * 1000)
            trade_data = {
                "timestamp": trade_timestamp,
                "symbol": symbol,
                "strategy": f"{strategy}_error_clear",
                "side": "SELL",
                "price": current_price if 'current_price' in locals() else 0.0,
                "amount": qty if 'qty' in locals() else 0.0,
                "cost": 0.0,
                "fee": 0.0,
                "pnl": 0.0,
                "order_id": f"error_clear_{trade_timestamp}"
            }
            log_trade(trade_data)
    except ccxt.RateLimitExceeded as e:
        logger.error(f"RATE LIMIT EXCEEDED: CCXT rate-limiter block on {symbol}.")
    except Exception as e:
        logger.error(f"Execution Error during {side_upper} order on {symbol}: {e}")
        logger.error(traceback.format_exc())
        alert_system_error(f"execute_market_order:{symbol}", e)

    return None


def check_and_execute_exits(exchange: ccxt.Exchange):
    """
    Checks active holdings and evaluates them against stop-loss and take-profit levels.
    Exits positions instantly if prices breach SL/TP guardrails.
    """
    active_positions = get_active_positions()
    if not active_positions:
        return

    logger.debug(f"Checking SL/TP levels for {len(active_positions)} active position(s)...")
    initialize_execution_markets(exchange)

    for symbol, qty in active_positions.items():
        try:
            # 1. Fetch current price
            ticker = exchange.fetch_ticker(symbol)
            current_price = ticker['close']
            
            # 2. Fetch last entry trade details
            entry_price = None
            strategy = "Unknown"
            with get_db_connection() as conn:
                res = conn.execute(text("""
                    SELECT price, strategy 
                    FROM trades 
                    WHERE symbol = :symbol AND side = 'BUY' 
                    ORDER BY timestamp DESC LIMIT 1
                """), {"symbol": symbol})
                row = res.mappings().fetchone()
                if row:
                    entry_price = float(row['price'])
                    strategy = row['strategy']
                    
            if entry_price is None:
                logger.warning(f"Could not find matching entry BUY trade details for active position on {symbol}.")
                continue
                
            # 3. Calculate targets (2% Stop Loss, 4% Take Profit)
            stop_loss, take_profit = calculate_sl_tp("BUY", entry_price, sl_percent=0.02, tp_percent=0.04)
            
            # Log scan iteration to individual trade log
            log_to_active_trade_file(
                symbol,
                f"Scan active position exit conditions - Current: {current_price:.2f} | "
                f"Targets: Stop-Loss={stop_loss:.2f}, Take-Profit={take_profit:.2f}"
            )
            
            # 4. Check boundaries
            triggered = False
            exit_reason = ""
            
            if current_price <= stop_loss:
                triggered = True
                exit_reason = f"STOP_LOSS (Breached: {current_price:.2f} <= SL target {stop_loss:.2f})"
            elif current_price >= take_profit:
                triggered = True
                exit_reason = f"TAKE_PROFIT (Breached: {current_price:.2f} >= TP target {take_profit:.2f})"
                
            if triggered:
                logger.warning(f"🚨 Risk target hit on {symbol}! Reason: {exit_reason}. Triggering market SELL order...")
                sl_tp_reason = "stop_loss" if "STOP_LOSS" in exit_reason else "take_profit"
                # Execute exit — strategy tag appended with reason for DB logging
                execute_market_order(exchange, symbol, "SELL", f"{strategy}_{sl_tp_reason}")
                
        except Exception as e:
            logger.error(f"Error checking SL/TP targets for {symbol}: {e}")
