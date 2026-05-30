import csv
from collections import defaultdict

def analyze_trades(file_path):
    trades = []
    try:
        with open(file_path, mode='r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(row)
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return

    total_trades = len(trades)
    if total_trades == 0:
        print("No trades to analyze.")
        return

    buys = [t for t in trades if t['Side'] == 'BUY']
    sells = [t for t in trades if t['Side'] == 'SELL']
    
    total_pnl = 0.0
    wins = 0
    losses = 0
    pnl_by_symbol = defaultdict(float)
    pnl_by_strategy = defaultdict(float)

    for sell in sells:
        # Some platforms log PnL as empty if it's not a closing trade, handle gracefully
        pnl_str = sell.get('PnL', '')
        if pnl_str and pnl_str.strip():
            try:
                pnl = float(pnl_str)
                total_pnl += pnl
                symbol = sell.get('Symbol', 'Unknown')
                strategy = sell.get('Strategy', 'Unknown')
                pnl_by_symbol[symbol] += pnl
                pnl_by_strategy[strategy] += pnl
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1
            except ValueError:
                pass

    total_closed = wins + losses
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

    print("=== TRADE ANALYSIS ===")
    print(f"Total Trades Logged: {total_trades}")
    print(f"  BUYs:  {len(buys)}")
    print(f"  SELLs: {len(sells)}")
    print()
    print("=== PNL ANALYSIS (from closing SELLs) ===")
    print(f"Total PnL: ${total_pnl:.4f}")
    print(f"Total Closed Trades: {total_closed}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Win Rate: {win_rate:.2f}%")
    print()
    print("=== PNL BY SYMBOL ===")
    for sym, pnl in sorted(pnl_by_symbol.items(), key=lambda x: x[1], reverse=True):
        print(f"  {sym}: ${pnl:.4f}")
    print()
    print("=== PNL BY STRATEGY ===")
    for strat, pnl in sorted(pnl_by_strategy.items(), key=lambda x: x[1], reverse=True):
        print(f"  {strat}: ${pnl:.4f}")

if __name__ == '__main__':
    analyze_trades('fly_trades.csv')
