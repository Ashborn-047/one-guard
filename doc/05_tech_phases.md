# Technical Development Phases

This document provides a deep dive into the 7 technical phases of building the AegisAlgo bot.

---

## 🛠️ Stack Component Summary

* **Language:** Python 3.10+
* **Exchange Client:** CCXT (CryptoCurrency eXchange Trading library)
* **Data Processing:** Pandas + NumPy
* **Technical Analysis:** `pandas-ta`
* **Scheduling:** `APScheduler` (Advanced Python Scheduler)
* **Local Storage:** SQLite (built-in relational database)
* **Configuration:** `.env` using `python-dotenv`
* **Dashboard:** Streamlit
* **Telemetry & Alerts:** Telegram Bot API

---

## Phase 1: Foundation (Week 1–2)
* **Goal:** Environment setup, secure credential storage, and connection verification.
* **Key Tasks:**
  * Initialize repository and virtual environment.
  * Install dependencies: `ccxt`, `pandas-ta`, `apscheduler`, `python-dotenv`, `streamlit`.
  * Set up exchange API access (Binance / WazirX Testnet/Sandbox).
* **Security Checkpoint:** Verify that API keys are stored strictly in `.env` and that `.gitignore` correctly ignores `.env`. Disable withdrawal privileges on the API key.
* **Target Pair Selection:** Highly liquid pairs only (`BTC/USDT`, `ETH/USDT`).

---

## Phase 2: Data Pipeline (Week 2–3)
* **Goal:** Create a scheduling client that polls historical and live candlestick data.
* **Key Tasks:**
  * Fetch OHLCV (Open, High, Low, Close, Volume) data via CCXT on 15m, 1h, and 4h intervals.
  * Implement automated indicator calculations using `pandas-ta` (RSI, Bollinger Bands, EMA).
  * Design SQLite database schema to cache historical logs.
  * Set up standard Python logging module (rotating log files) rather than basic stdout `print()` calls.

---

## Phase 3: Strategy Engines (Week 3–4)
* **Goal:** Implement the three core algorithm modules running in parallel.

### Strategy A: RSI Mean Reversion
* **Signal Buy:** RSI (14) < 32 (oversold region)
* **Signal Sell:** RSI (14) > 55 (momentum exit)
* **Target Profit:** 0.5% – 1.0%
* **Stop Loss:** 0.5%

### Strategy B: Bollinger Band Bounce
* **Signal Buy:** Price touches lower BB (20, 2) (mean reversion)
* **Signal Sell:** Price touches middle BB (moving average)
* **Target Profit:** 0.4% – 0.8%
* **Stop Loss:** 0.6%

### Strategy C: EMA Crossover
* **Signal Buy:** Fast EMA (9) crosses above Slow EMA (21)
* **Signal Sell:** Fast EMA (9) crosses below Slow EMA (21)
* **Target Profit:** 0.8% – 1.5% (trend follower)
* **Stop Loss:** 0.8%

---

## Phase 4: Risk Engine (Week 4)
* **Goal:** The ultimate defensive layer intercepting and filtering all trading signals.
* **Key Capital Rules:**
  * **Weekly Budget:** ₹1,000 fixed allocation.
  * **Per-Trade Allocation:** Max 20% of budget (₹200).
  * **Concurrency Limit:** Max 3 open positions at any time.
  * **Max Drawdown Halt:** If weekly losses hit 10% (₹100), halt trading until manual reset.
* **Key Execution Rules:**
  * Auto-inject Stop Loss and Take Profit levels into every order.
  * Add a 0.2% price buffer to target profit exit levels to cover exchange fees.
  * Enforce a 30-minute cooldown period after any realized loss to mitigate revenge trading.

---

## Phase 5: Paper Trading (Week 5–6)
* **Goal:** Run simulated executions on Binance Testnet with real-time market feeds.
* **Key Tasks:**
  * Configure CCXT to sandbox mode.
  * Run the bot continuously for 3–4 weeks.
  * Log all trades, fees, exit indicators, and performance parameters in SQLite.
* **Success Criteria:**
  * Zero execution crashes.
  * Win rate > 55% for the best-performing strategy.
  * Average net profit per trade > 0.4% post-slippage.
  * Max drawdown strictly under 8%.

---

## Phase 6: Dashboard & Telemetry (Week 6–7)
* **Goal:** Interactive reporting and instant alert notifications.
* **Key Tasks:**
  * Build a local Streamlit dashboard reading SQLite trade records.
  * Display live realized/unrealized P&L, equity curves, strategy comparisons, and drawdown levels.
  * Configure Telegram Bot API webhook to push alerts immediately when orders are placed, modified, filled, or when stops are hit.

---

## Phase 7: Go Live (Week 8+)
* **Goal:** Shift to live exchange with small initial capital.
* **Pre-Launch Checklist:**
  * [ ] At least 3 weeks of profitable paper trading.
  * [ ] Verified strategy win rate > 55% on sandbox.
  * [ ] Risk engine unit tested (stop loss and weekly limit halts verify).
  * [ ] API keys verified absent from version control.
  * [ ] Telemetry notifications active.
* **Scaling Rules:**
  * **Month 1:** Trade strictly with ₹1,000 weekly budget and only the single winning strategy.
  * **Month 2:** Increase budget to ₹2,000 if Month 1 is net profitable.
  * **Month 3+:** Gradually add the second-best strategy.
  * **Profit Distribution:** Withdraw 50% of monthly net earnings, reinvest 50% into trading capital.
