import sqlite3
import time
import datetime
import logging
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ccxt

from src.config import settings
from src.db import get_db_connection, get_strategy_data
from src.risk import get_active_positions, get_weekly_pnl

# Set up page configurations
st.set_page_config(
    page_title="OneGuard Command Center",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Apply premium styling
st.markdown("""
<style>
    .main {
        background-color: #0f111a;
        color: #e6edf3;
    }
    .stMetric {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 15px;
    }
    .stMetric label {
        color: #8b949e !important;
        font-weight: 600;
    }
    .stMetric div[data-testid="stMetricValue"] {
        color: #ffffff !important;
    }
    .stTable {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
    }
    h1, h2, h3 {
        color: #58a6ff !important;
    }
    .reportview-container .main .block-container{
        padding-top: 2rem;
    }
</style>
""", unsafe_allow_html=True)

logger = logging.getLogger("OneGuard.Dashboard")

# Initialize CCXT exchange client for live ticker price updates
@st.cache_resource
def get_exchange_client():
    try:
        exchange_class = getattr(ccxt, "binance")
        exchange_config = {
            'enableRateLimit': True,
            'timeout': 10000,
        }
        # Read keys if they are set (not required for public ticker queries)
        if settings.api_key and settings.secret_key:
            exchange_config['apiKey'] = settings.api_key
            exchange_config['secret'] = settings.secret_key
            
        exchange = exchange_class(exchange_config)
        if settings.is_sandbox:
            exchange.set_sandbox_mode(True)
        return exchange
    except Exception as e:
        st.error(f"Failed to initialize exchange connection: {e}")
        return None

def fetch_live_price(exchange, symbol: str, fallback_price: float) -> float:
    """
    Fetches the latest closing price of a symbol. Fallbacks to database candle close price if CCXT errors out.
    """
    if exchange is None:
        return fallback_price
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker['close'])
    except Exception as e:
        logger.warning(f"Could not fetch live price for {symbol} from exchange: {e}. Using fallback {fallback_price}")
        return fallback_price

def get_performance_metrics():
    """
    Queries trades table and returns core performance metrics:
    Total Realized PnL, Win Rate, Profit Factor, Total Trades count.
    """
    metrics = {
        "total_trades": 0,
        "closed_trades": 0,
        "realized_pnl": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0
    }
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Fetch total count
            cursor.execute("SELECT COUNT(*) as cnt FROM trades")
            metrics["total_trades"] = cursor.fetchone()["cnt"]
            
            # Fetch closed trades (trades with realized PnL)
            cursor.execute("SELECT pnl FROM trades WHERE pnl IS NOT NULL")
            pnl_rows = cursor.fetchall()
            
            if pnl_rows:
                pnls = [float(row["pnl"]) for row in pnl_rows]
                metrics["closed_trades"] = len(pnls)
                metrics["realized_pnl"] = sum(pnls)
                
                wins = [p for p in pnls if p > 0]
                losses = [p for p in pnls if p < 0]
                
                if pnls:
                    metrics["win_rate"] = (len(wins) / len(pnls)) * 100.0
                
                gross_profit = sum(wins)
                gross_loss = abs(sum(losses))
                
                metrics["gross_profit"] = gross_profit
                metrics["gross_loss"] = gross_loss
                
                if gross_loss > 0:
                    metrics["profit_factor"] = gross_profit / gross_loss
                elif gross_profit > 0:
                    metrics["profit_factor"] = float('inf')  # No losses, positive profit
                else:
                    metrics["profit_factor"] = 0.0
    except Exception as e:
        logger.error(f"Error calculating performance metrics from database: {e}")
        
    return metrics

def main():
    st.title("🛡️ OneGuard Bot | Engineering Command Center")
    st.subheader("Real-time Bot Performance, Risk Guardrails, and Indicator Pipeline")
    
    # ------------------ SIDEBAR CONFIGURATION ------------------
    st.sidebar.header("System Configuration")
    
    # Status badges in sidebar
    mode_color = "green" if settings.is_live else "orange"
    mode_text = "LIVE TRADING" if settings.is_live else "SANDBOX MOCK"
    st.sidebar.markdown(f"**Execution Mode:** <span style='color:{mode_color}; font-weight:bold;'>{mode_text}</span>", unsafe_allow_html=True)
    
    halt_color = "red" if settings.emergency_halt else "green"
    halt_text = "HALTED" if settings.emergency_halt else "RUNNING"
    st.sidebar.markdown(f"**Emergency Guard:** <span style='color:{halt_color}; font-weight:bold;'>{halt_text}</span>", unsafe_allow_html=True)
    
    st.sidebar.markdown("---")
    
    # Refreshes configuration
    st.sidebar.subheader("Dashboard Controls")
    refresh_rate = st.sidebar.selectbox("Auto Refresh Rate", ["Manual", "10 seconds", "30 seconds", "60 seconds"], index=2)
    
    # Handle refresh timing
    refresh_seconds = 0
    if refresh_rate == "10 seconds":
        refresh_seconds = 10
    elif refresh_rate == "30 seconds":
        refresh_seconds = 30
    elif refresh_rate == "60 seconds":
        refresh_seconds = 60
        
    if refresh_seconds > 0:
        time.sleep(refresh_seconds)
        st.rerun()
        
    if st.sidebar.button("Force Refresh Now 🔄"):
        st.rerun()

    # Risk Parameters display
    st.sidebar.markdown("---")
    st.sidebar.subheader("Risk Guardrails Settings")
    st.sidebar.markdown(f"**Max Position Size:** `{settings.max_position_size} USDT`")
    st.sidebar.markdown(f"**Max Open Trades Limit:** `{settings.max_open_trades}`")
    st.sidebar.markdown(f"**Weekly Drawdown Limit:** `{settings.weekly_drawdown_limit} USDT`")
    st.sidebar.markdown(f"**Loss Cooldown:** `{settings.loss_cooldown_minutes} mins`")
    st.sidebar.markdown(f"**Stop Loss Target:** `2.0%`")
    st.sidebar.markdown(f"**Take Profit Target:** `4.0%`")

    # ------------------ KPI STATS HEADER ------------------
    metrics = get_performance_metrics()
    weekly_pnl = get_weekly_pnl()
    active_positions = get_active_positions()
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    # Color coding realized PnL
    pnl_delta = f"{metrics['realized_pnl']:.2f} USDT"
    col1.metric(
        label="Total Realized PnL", 
        value=pnl_delta,
        delta=f"{metrics['realized_pnl']:.2f} USDT" if metrics['realized_pnl'] != 0 else None,
        delta_color="normal" if metrics['realized_pnl'] >= 0 else "inverse"
    )
    
    # Win rate metric
    col2.metric(
        label="Trade Win Rate", 
        value=f"{metrics['win_rate']:.1f}%",
        delta=f"{metrics['closed_trades']} Closed Trades"
    )
    
    # Profit factor metric
    pf_val = "N/A" if metrics['profit_factor'] == 0 else (f"{metrics['profit_factor']:.2f}" if metrics['profit_factor'] != float('inf') else "∞ (Only Wins)")
    col3.metric(
        label="Profit Factor", 
        value=pf_val,
        delta=f"Gross Profit: {metrics['gross_profit']:.1f}"
    )
    
    # Weekly Drawdown Status
    drawdown_limit_neg = -settings.weekly_drawdown_limit
    drawdown_status = "SAFE" if weekly_pnl > drawdown_limit_neg else "HALTED"
    col4.metric(
        label="Weekly Realized PnL",
        value=f"{weekly_pnl:.2f} USDT",
        delta=f"Status: {drawdown_status}",
        delta_color="normal" if drawdown_status == "SAFE" else "inverse"
    )
    
    # Active Positions count
    col5.metric(
        label="Active Positions",
        value=f"{len(active_positions)} / {settings.max_open_trades}",
        delta=f"Total Trades Logged: {metrics['total_trades']}"
    )

    st.markdown("---")

    # ------------------ ACTIVE POSITIONS LIVE TRACKER ------------------
    st.subheader("💼 Active Positions & Live Valuation")
    
    exchange = get_exchange_client()
    
    if not active_positions:
        st.info("No active positions currently held by the bot.")
    else:
        position_rows = []
        for symbol, qty in active_positions.items():
            # Get opening trade details (entry price)
            entry_price = 0.0
            entry_time = 0
            strategy = "Unknown"
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT timestamp, price, strategy 
                        FROM trades 
                        WHERE symbol = ? AND side = 'BUY' 
                        ORDER BY timestamp DESC LIMIT 1
                    """, (symbol,))
                    row = cursor.fetchone()
                    if row:
                        entry_price = float(row["price"])
                        entry_time = int(row["timestamp"])
                        strategy = row["strategy"]
            except Exception as e:
                logger.error(f"Error fetching entry price for live positions display: {e}")
            
            # Fetch live current price
            current_price = fetch_live_price(exchange, symbol, fallback_price=entry_price)
            
            # Calculations
            cost = entry_price * qty
            market_value = current_price * qty
            unrealized_pnl = market_value - cost
            unrealized_pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
            
            # Hard risk targets
            sl_price = entry_price * 0.98
            tp_price = entry_price * 1.04
            
            entry_dt = datetime.datetime.fromtimestamp(entry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
            
            position_rows.append({
                "Symbol": symbol,
                "Quantity": f"{qty:.6f}",
                "Strategy": strategy,
                "Entry Time": entry_dt,
                "Entry Price": f"{entry_price:.4f} USDT",
                "Current Price": f"{current_price:.4f} USDT",
                "Total Cost": f"{cost:.2f} USDT",
                "Market Value": f"{market_value:.2f} USDT",
                "Unrealized PnL": f"{unrealized_pnl:+.2f} USDT",
                "Unrealized PnL (%)": f"{unrealized_pnl_pct:+.2f}%",
                "Stop Loss": f"{sl_price:.4f} USDT",
                "Take Profit": f"{tp_price:.4f} USDT"
            })
            
        df_positions = pd.DataFrame(position_rows)
        
        # Display nicely with styling (custom coloring for PnL column if possible in st.dataframe)
        st.dataframe(df_positions, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ------------------ INTERACTIVE MARKET CHARTS ------------------
    st.subheader("📈 Interactive Market Analysis & Technical Pipeline")
    
    chart_symbol = st.selectbox("Select Trading Pair for Analysis", ["BTC/USDT", "ETH/USDT"])
    candle_limit = st.slider("Candles Count to Display", min_value=30, max_value=200, value=100, step=10)
    
    df_chart = get_strategy_data(chart_symbol, limit=candle_limit)
    
    if df_chart.empty:
        st.warning(f"No candlestick data available in local database for {chart_symbol}.")
    else:
        # Convert timestamp to datetime
        df_chart["datetime"] = pd.to_datetime(df_chart["timestamp"], unit="ms")
        
        # Build subplots: Candlestick overlayed with EMA and BB (top), RSI (bottom)
        fig = make_subplots(
            rows=2, cols=1, 
            shared_xaxes=True, 
            vertical_spacing=0.08, 
            subplot_titles=(f"{chart_symbol} Candlesticks & Overlays", "Relative Strength Index (RSI)"),
            row_heights=[0.7, 0.3]
        )
        
        # Add Candlesticks
        fig.add_trace(
            go.Candlestick(
                x=df_chart["datetime"],
                open=df_chart["open"],
                high=df_chart["high"],
                low=df_chart["low"],
                close=df_chart["close"],
                name="Candlesticks"
            ),
            row=1, col=1
        )
        
        # Overlays: EMA Fast
        if "ema_fast" in df_chart.columns and not df_chart["ema_fast"].isnull().all():
            fig.add_trace(
                go.Scatter(
                    x=df_chart["datetime"], 
                    y=df_chart["ema_fast"], 
                    line=dict(color="#ffc107", width=1.5), 
                    name="EMA Fast (9)"
                ),
                row=1, col=1
            )
            
        # Overlays: EMA Slow
        if "ema_slow" in df_chart.columns and not df_chart["ema_slow"].isnull().all():
            fig.add_trace(
                go.Scatter(
                    x=df_chart["datetime"], 
                    y=df_chart["ema_slow"], 
                    line=dict(color="#e83e8c", width=1.5), 
                    name="EMA Slow (21)"
                ),
                row=1, col=1
            )
            
        # Overlays: Bollinger Bands (Upper/Lower)
        if "bb_upper" in df_chart.columns and not df_chart["bb_upper"].isnull().all():
            fig.add_trace(
                go.Scatter(
                    x=df_chart["datetime"], 
                    y=df_chart["bb_upper"], 
                    line=dict(color="#17a2b8", width=1, dash="dash"), 
                    name="BB Upper"
                ),
                row=1, col=1
            )
            
        if "bb_lower" in df_chart.columns and not df_chart["bb_lower"].isnull().all():
            fig.add_trace(
                go.Scatter(
                    x=df_chart["datetime"], 
                    y=df_chart["bb_lower"], 
                    line=dict(color="#17a2b8", width=1, dash="dash"), 
                    name="BB Lower"
                ),
                row=1, col=1
            )
            
        # Subplot 2: RSI
        if "rsi" in df_chart.columns and not df_chart["rsi"].isnull().all():
            fig.add_trace(
                go.Scatter(
                    x=df_chart["datetime"], 
                    y=df_chart["rsi"], 
                    line=dict(color="#007bff", width=1.8), 
                    name="RSI"
                ),
                row=2, col=1
            )
            
            # Oversold line (30)
            fig.add_shape(
                type="line", 
                x0=df_chart["datetime"].iloc[0], 
                y0=30, 
                x1=df_chart["datetime"].iloc[-1], 
                y1=30,
                line=dict(color="green", width=1, dash="dot"),
                row=2, col=1
            )
            
            # Overbought line (70)
            fig.add_shape(
                type="line", 
                x0=df_chart["datetime"].iloc[0], 
                y0=70, 
                x1=df_chart["datetime"].iloc[-1], 
                y1=70,
                line=dict(color="red", width=1, dash="dot"),
                row=2, col=1
            )
            
            # Update RSI Y axis limits
            fig.update_yaxes(range=[10, 90], row=2, col=1)
            
        fig.update_layout(
            height=600,
            xaxis_rangeslider_visible=False,
            paper_bgcolor="#161b22",
            plot_bgcolor="#0f111a",
            margin=dict(l=40, r=40, t=40, b=40),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # ------------------ HISTORICAL TRADES LOG VIEW ------------------
    st.subheader("📜 Historical Trades Ledger")
    
    try:
        with get_db_connection() as conn:
            query = """
                SELECT timestamp, order_id, symbol, strategy, side, price, amount, cost, fee, pnl
                FROM trades
                ORDER BY timestamp DESC
                LIMIT 100
            """
            df_history = pd.read_sql_query(query, conn)
            
        if df_history.empty:
            st.info("No trade records found in database history.")
        else:
            # Format outputs
            df_history["datetime"] = pd.to_datetime(df_history["timestamp"], unit="ms")
            df_history = df_history.drop(columns=["timestamp"])
            
            # Reorder columns
            cols = ["datetime", "order_id", "symbol", "strategy", "side", "price", "amount", "cost", "fee", "pnl"]
            df_history = df_history[cols]
            
            # Style values for better aesthetics
            st.dataframe(
                df_history.style.format({
                    "price": "{:.4f}",
                    "amount": "{:.6f}",
                    "cost": "{:.2f}",
                    "fee": "{:.4f}",
                    "pnl": lambda x: f"{x:+.2f}" if pd.notnull(x) else "-"
                }),
                use_container_width=True,
                hide_index=True
            )
    except Exception as e:
        st.error(f"Failed to query trade ledger: {e}")

if __name__ == "__main__":
    main()
