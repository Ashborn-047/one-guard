# Goals & Vision

This document serves as the high-level guiding framework and source of truth for the OneGuard project.

---

## ◈ Vision
Build a self-running Python trading bot that trades cryptocurrency with a weekly budget of **₹1,000**, targeting small, realistic compounding profits (**0.5% – 1% per trade**), managing its own risk autonomously, and scaling safely over time.

## ◈ Mission
Replace emotional human trading with a disciplined, data-driven algorithm that trades *only* when conditions are exactly right — and does absolutely nothing when they are not.

---

## ◈ Project Goals

| ID | Category | Goal | Target Metric / Description |
| :--- | :--- | :--- | :--- |
| **G1** | **Financial** | Compounding Gains | Generate consistent small profits (0.5–1% per trade) compounding into meaningful returns over 3–6 months. |
| **G2** | **Technical** | Complete Automation | Build a fully automated bot in Python requiring zero manual intervention during trading hours. |
| **G3** | **Risk** | Capital Protection | Never lose more than **10%** of the weekly ₹1,000 fund in any single week, enforced by hard-coded rules. |
| **G4** | **Growth** | Skill Development | Deeply understand algorithmic trading, indicator-based strategies, and risk management systems to continuously improve the bot. |
| **G5** | **Scale** | Gradual Graduation | Prove the system works on paper/testnet first, then graduate to live trading and slowly increase the weekly fund. |

---

## ◈ Guiding Principles

1. 🎯 **A trade skipped is never a loss. A bad trade taken is always one.**
   * *Rationale:* Preservation of capital is the absolute priority. Market opportunities are infinite; account balances are finite.
2. 📐 **The risk engine is the boss. The strategy is just a suggestion.**
   * *Rationale:* A mediocre strategy with a world-class risk engine survives. A world-class strategy with a poor risk engine goes bust.
3. 🧪 **Paper trading is not optional. It is step zero of real trading.**
   * *Rationale:* Simulation reveals structural issues, latency bugs, and indicator calculations that cannot be fully anticipated in offline backtests.
4. 📊 **Win rate matters more than profit size.**
   * *Rationale:* 60% win rate at 0.5% profit beats 40% win rate at 1.0% profit because it creates smoother equity curves and reduces psychological drawdown.
5. 🔒 **API keys never go in code. Ever. Full stop.**
   * *Rationale:* Security is paramount. Use environment variables and standard secret managers. Never commit keys to version control.
6. 🩺 **Review weekly. Markets change. Your bot must too.**
   * *Rationale:* Market regimes shift (trending to range-bound). Periodic reviews ensure parameter tuning stays aligned with current volatility.
