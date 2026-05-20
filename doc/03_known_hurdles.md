# Known Hurdles & Mitigation Strategies

This document serves as our defensive runbook, outlining specific technical and behavioral challenges, their impact, and the engineering mitigations required to bypass them.

---

## ⚠️ Hazard Registry

### 1. Overfitting to Historical Data
* **Severity:** **HIGH**
* **The Problem:** The strategy looks excellent during backtesting because parameters were tuned too specifically to past price charts. It performs poorly in live environments due to statistical noise.
* **The Fix:**
  * Implement strict **Out-of-Sample (OOS) validation**. Train strategy parameters on 70% of historical data, validate on the remaining 30% that the bot has never seen.
  * Limit the number of indicator parameters (avoid stacking 10 indicators together). Simple models generalize better.

### 2. API Rate Limits
* **Severity:** **MEDIUM**
* **The Problem:** Exchange APIs limit requests per minute. Repeatedly polling the exchange for price updates causes rate-limit blocks (HTTP 429), leading to crashed bots or missed exits.
* **The Fix:**
  * Enable CCXT's built-in rate-limiting feature (`enableRateLimit: true`).
  * Cache candlestick and indicator data locally in SQLite; do not fetch historical candles multiple times per hour. Use WebSocket feeds instead of REST polling for ticker updates.

### 3. Network Outages & Host Failures
* **Severity:** **HIGH**
* **The Problem:** The bot runs locally on a personal machine. If the computer sleeps, loses internet, or crashes, the bot fails to monitor active trades. Open positions could remain unmanaged without a stop-loss.
* **The Fix:**
  * Deploy the production bot on a Virtual Private Server (VPS) (e.g., AWS EC2 micro, DigitalOcean droplet) ensuring 99.9% uptime.
  * Implement robust error-catching blocks (`try...except`) on all network operations.
  * **Critical Startup Routine:** Upon booting, the bot must query the exchange API to check for any orphan open positions and reconcile them with local state.

### 4. Exchange Fees & Drag
* **Severity:** **MEDIUM**
* **The Problem:** Standard trading fees (e.g., 0.1% spot fee on Binance) amount to a 0.2% round-trip drag. On a target profit of 0.5%, fees consume 40% of the gains.
* **The Fix:**
  * Factor exchange fee calculations into the target profit exit triggers.
  * Hold a small amount of BNB on the exchange to pay fees (reduces spot fees by 25%).
  * Only enter trades where the expected gross profit is at least >0.4% after accounting for fee drag.

### 5. Slippage on Small Capital
* **Severity:** **LOW**
* **The Problem:** The price displayed in the feed is slightly different from the actual filled order price, especially in highly volatile moments, eroding target margins.
* **The Fix:**
  * Utilize **Limit Orders** for entry and exit instead of Market Orders.
  * Accept that some limit orders will not get filled. Let the signal expire and wait for the next setup rather than chasing the market.

### 6. Market Regime Shifts
* **Severity:** **HIGH**
* **The Problem:** The market transitions from a trending regime (where EMA crossover excels) to a range-bound regime (where Bollinger Bands/RSI excel). The active strategy's win rate drops.
* **The Fix:**
  * Implement a weekly performance check.
  * If the win rate drops below 50% for 2 consecutive weeks, pause the active strategy immediately and return the bot to cash while analyzing the market regime.

### 7. Emotional Interference
* **Severity:** **MEDIUM**
* **The Problem:** The developer sees a trade going in the wrong direction and manually overrides the bot on the exchange interface, or adjusts rules mid-week out of panic.
* **The Fix:**
  * Establish a strict operational protocol: no manual intervention on active trades and no code or configuration changes during the trading week (Monday to Friday).
  * Carry out all parameter adjustments, review logs, and deploy code updates *only* over the weekend when markets are reviewed.

### 8. API Key Leaks (Security Risk)
* **Severity:** **CRITICAL**
* **The Problem:** Pushing hard-coded API credentials to Git repositories (especially public ones) allows scrapers to compromise the account and drain funds.
* **The Fix:**
  * Store all keys in a `.env` file at the root.
  * Add `.env` to the `.gitignore` file before the very first commit.
  * Disable withdrawal permissions on the exchange API panel. Ensure keys are authorized *only* for read and trade capabilities.
