"""
src/paper_engine.py
===================
Autonomous Paper Trading Engine.

Runs all 3 strategies (RSI, BB, EMA) simultaneously with strategy-isolated
virtual portfolios. Each strategy independently:
  - Evaluates signals from shared market data
  - Opens/closes positions tracked only under its own name
  - Has its own SL/TP exit monitoring
  - Accumulates performance metrics on the leaderboard

This module is called by the pipeline scheduler every 15 minutes.
No human interaction is needed — fully autonomous.
"""

import logging
import time
from typing import Dict, List, Any, Optional, Tuple

from src.config import settings
from src.db import (
    get_paper_positions,
    log_paper_trade,
    update_strategy_performance,
    get_strategy_performance_data,
)
from src.strategies import evaluate_all_strategies
from src.telemetry import send_telegram_message

logger = logging.getLogger("OneGuard.PaperEngine")

# Strategy names matching the keys returned by evaluate_all_strategies()
STRATEGY_NAMES = ["RSI", "BB", "EMA"]

# Risk parameters for paper trading (from doc/06_strategies_and_algorithms.md)
SL_PERCENT = 0.02   # 2% stop-loss
TP_PERCENT = 0.04   # 4% take-profit
FEE_RATE = 0.001    # 0.1% simulated trading fee (Binance standard)


def _calculate_paper_position_size(current_price: float) -> float:
    """
    Calculate the token quantity to buy given the max position size budget.
    Uses the same budget as the real execution engine.
    """
    if current_price <= 0:
        return 0.0
    qty = settings.max_position_size / current_price
    return qty


def execute_paper_buy(
    strategy: str,
    symbol: str,
    current_price: float,
) -> Optional[Dict[str, Any]]:
    """
    Executes a paper BUY trade for a specific strategy.
    Returns the trade data dict if successful, None otherwise.
    """
    # Check if this strategy already holds a position on this symbol
    positions = get_paper_positions(strategy)
    if symbol in positions:
        logger.debug(f"[{strategy}] Already holding paper position on {symbol}. Skipping BUY.")
        return None

    qty = _calculate_paper_position_size(current_price)
    if qty <= 0:
        return None

    cost = qty * current_price
    fee = cost * FEE_RATE
    trade_timestamp = int(time.time() * 1000)

    trade_data = {
        "timestamp": trade_timestamp,
        "symbol": symbol,
        "strategy": strategy,
        "side": "BUY",
        "price": current_price,
        "amount": qty,
        "cost": cost,
        "fee": fee,
        "pnl": None,
        "order_id": f"paper_{strategy}_{trade_timestamp}",
    }

    success = log_paper_trade(trade_data)
    if success:
        logger.info(
            f"📝 [{strategy}] PAPER BUY: {qty:.8f} {symbol} at ${current_price:,.2f} "
            f"(Cost: ${cost:.4f}, Fee: ${fee:.6f})"
        )
        # Send Telegram alert
        alert_paper_trade_entry(strategy, symbol, qty, current_price)
        return trade_data
    return None


def execute_paper_sell(
    strategy: str,
    symbol: str,
    current_price: float,
    reason: str = "signal",
) -> Optional[Dict[str, Any]]:
    """
    Executes a paper SELL trade for a specific strategy.
    Calculates realized PnL against the entry price.
    Returns the trade data dict if successful, None otherwise.
    """
    positions = get_paper_positions(strategy)
    if symbol not in positions:
        logger.debug(f"[{strategy}] No active paper position on {symbol}. Skipping SELL.")
        return None

    position = positions[symbol]
    qty = position["qty"]
    entry_price = position["entry_price"]

    cost = qty * current_price
    fee = cost * FEE_RATE
    entry_fee = qty * entry_price * FEE_RATE

    # Realized PnL = (exit_price - entry_price) * qty - entry_fee - exit_fee
    pnl = (current_price - entry_price) * qty - entry_fee - fee

    trade_timestamp = int(time.time() * 1000)
    trade_data = {
        "timestamp": trade_timestamp,
        "symbol": symbol,
        "strategy": strategy,
        "side": "SELL",
        "price": current_price,
        "amount": qty,
        "cost": cost,
        "fee": fee,
        "pnl": pnl,
        "order_id": f"paper_{strategy}_{trade_timestamp}",
    }

    success = log_paper_trade(trade_data)
    if success:
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        logger.info(
            f"📝 [{strategy}] PAPER SELL ({reason}): {qty:.8f} {symbol} at ${current_price:,.2f} "
            f"| PnL: ${pnl:+.6f} {pnl_emoji}"
        )
        # Update leaderboard
        update_strategy_performance(strategy, pnl, is_win=(pnl >= 0))
        # Send Telegram alert
        alert_paper_trade_exit(strategy, symbol, qty, current_price, pnl, reason)
        return trade_data
    return None


def check_paper_exits(symbol: str, current_price: float) -> None:
    """
    Checks SL/TP exit conditions for ALL strategies on a given symbol.
    Each strategy's positions are checked independently.
    """
    for strategy in STRATEGY_NAMES:
        positions = get_paper_positions(strategy)
        if symbol not in positions:
            continue

        position = positions[symbol]
        entry_price = position["entry_price"]

        # Calculate SL/TP targets
        stop_loss = entry_price * (1.0 - SL_PERCENT)
        take_profit = entry_price * (1.0 + TP_PERCENT)

        # Check boundaries
        if current_price <= stop_loss:
            logger.warning(
                f"🚨 [{strategy}] STOP LOSS triggered on {symbol}! "
                f"Current: ${current_price:,.2f} <= SL: ${stop_loss:,.2f}"
            )
            execute_paper_sell(strategy, symbol, current_price, reason="stop_loss")

        elif current_price >= take_profit:
            logger.info(
                f"🎯 [{strategy}] TAKE PROFIT triggered on {symbol}! "
                f"Current: ${current_price:,.2f} >= TP: ${take_profit:,.2f}"
            )
            execute_paper_sell(strategy, symbol, current_price, reason="take_profit")

        else:
            logger.debug(
                f"[{strategy}] {symbol} position scan: "
                f"Entry=${entry_price:,.2f} Current=${current_price:,.2f} "
                f"SL=${stop_loss:,.2f} TP=${take_profit:,.2f}"
            )


def run_paper_trading_cycle(symbol: str, current_price: float) -> None:
    """
    Main paper trading cycle for a single symbol. Called by the pipeline after indicators.
    
    1. Checks SL/TP exits for all strategies
    2. Evaluates all strategy signals
    3. Executes paper trades for any BUY/SELL signals
    """
    logger.info(f"Running paper trading cycle for {symbol} at ${current_price:,.2f}...")

    # 1. Check exits first (SL/TP)
    check_paper_exits(symbol, current_price)

    # 2. Evaluate all strategy signals
    signals = evaluate_all_strategies(symbol)
    logger.info(f"Paper trading signals for {symbol}: {signals}")

    # 3. Execute paper trades for each strategy independently
    for strategy_name, signal in signals.items():
        if signal == "BUY":
            logger.info(f"📊 [{strategy_name}] BUY signal for {symbol}. Opening paper position...")
            execute_paper_buy(strategy_name, symbol, current_price)

        elif signal == "SELL":
            logger.info(f"📊 [{strategy_name}] SELL signal for {symbol}. Closing paper position...")
            execute_paper_sell(strategy_name, symbol, current_price, reason="signal")

        # HOLD = no action


def get_leaderboard() -> str:
    """
    Returns a formatted leaderboard string showing strategy comparison.
    """
    perf_data = get_strategy_performance_data()

    if not perf_data:
        return "📊 No paper trades executed yet. Waiting for strategy signals..."

    medals = ["🥇", "🥈", "🥉"]
    strategy_labels = {
        "RSI": "RSI Mean Reversion",
        "BB": "Bollinger Band Bounce",
        "EMA": "EMA Crossover",
    }

    lines = ["📊 *Strategy Leaderboard*\n"]
    for i, data in enumerate(perf_data):
        medal = medals[i] if i < len(medals) else "  "
        name = strategy_labels.get(data["strategy"], data["strategy"])
        total = data["total_trades"]
        wins = data["winning_trades"]
        losses = data["losing_trades"]
        pnl = data["total_pnl"]
        win_rate = (wins / total * 100) if total > 0 else 0.0

        inr_pnl = pnl * settings.usdt_inr_rate
        pnl_str = f"+₹{inr_pnl:,.2f} INR" if pnl >= 0 else f"-₹{abs(inr_pnl):,.2f} INR"
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        inr_best = data.get('best_trade_pnl', 0.0) * settings.usdt_inr_rate
        inr_worst = data.get('worst_trade_pnl', 0.0) * settings.usdt_inr_rate

        lines.append(
            f"{medal} *{name}*\n"
            f"   PnL: `{pnl_str}` {pnl_emoji} | Win Rate: `{win_rate:.0f}%`\n"
            f"   Trades: `{total}` (W:{wins} / L:{losses})\n"
            f"   Best: `₹{inr_best:+.2f} INR` | "
            f"Worst: `₹{inr_worst:+.2f} INR`"
        )

    return "\n\n".join(lines)


# ──────────────────────────────────────────────
# Telegram Alerts for Paper Trades
# ──────────────────────────────────────────────

def alert_paper_trade_entry(strategy: str, symbol: str, qty: float, price: float) -> bool:
    """Sends a Telegram notification for a paper trade entry."""
    strategy_labels = {"RSI": "RSI Mean Reversion", "BB": "Bollinger Band Bounce", "EMA": "EMA Crossover"}
    label = strategy_labels.get(strategy, strategy)
    inr_price = price * settings.usdt_inr_rate
    inr_budget = qty * price * settings.usdt_inr_rate
    message = (
        f"📝 *\\[OneGuard\\] PAPER TRADE*\n"
        f"📈 *BUY*  `{symbol}`\n"
        f"Strategy : `{label}`\n"
        f"Qty      : `{qty:.8f}`\n"
        f"Price    : `~₹{inr_price:,.2f} INR`\n"
        f"Budget   : `~₹{inr_budget:,.2f} INR`"
    )
    return send_telegram_message(message)


def alert_paper_trade_exit(
    strategy: str, symbol: str, qty: float, price: float, pnl: float, reason: str
) -> bool:
    """Sends a Telegram notification for a paper trade exit with PnL."""
    strategy_labels = {"RSI": "RSI Mean Reversion", "BB": "Bollinger Band Bounce", "EMA": "EMA Crossover"}
    label = strategy_labels.get(strategy, strategy)
    inr_price = price * settings.usdt_inr_rate
    inr_pnl = pnl * settings.usdt_inr_rate
    pnl_display = f"+₹{inr_pnl:,.2f} INR ✅" if pnl >= 0 else f"-₹{abs(inr_pnl):,.2f} INR ❌"
    reason_label = {
        "stop_loss": "🔴 Stop Loss",
        "take_profit": "🟢 Take Profit",
    }.get(reason, "📊 Signal")

    message = (
        f"📝 *\\[OneGuard\\] PAPER EXIT*\n"
        f"📉 *SELL*  `{symbol}`\n"
        f"Strategy : `{label}`  |  {reason_label}\n"
        f"Qty      : `{qty:.8f}`\n"
        f"Price    : `~₹{inr_price:,.2f} INR`\n"
        f"PnL      : `{pnl_display}`"
    )
    return send_telegram_message(message)
