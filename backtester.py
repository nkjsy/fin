import pandas as pd
from strategy import BaseStrategy

class BacktestEngine:
    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = initial_capital

    def run(self, df: pd.DataFrame, strategy: BaseStrategy):
        # Prepare data
        df = strategy.generate_signals(df)
        
        cash = self.initial_capital
        position = 0
        trade_log = []
        equity_curve = []
        
        # Ensure Date is available for logging
        if "Date" not in df.columns:
            df["Date"] = df.index
        
        for i in range(len(df)):
            row = df.iloc[i]
            price = row["Close"]
            signal = row["Signal"]
            date = row["Date"]
            
            # Execute Trade
            if signal == 1 and cash > 0: # Buy
                # Buy max shares
                shares_to_buy = int(cash // price)
                if shares_to_buy > 0:
                    cost = shares_to_buy * price
                    cash -= cost
                    position += shares_to_buy
                    trade_log.append({
                        "Date": date,
                        "Action": "BUY",
                        "Price": price,
                        "Shares": shares_to_buy,
                        "Value": cost
                    })
            
            elif signal == -1 and position > 0: # Sell
                revenue = position * price
                cash += revenue
                trade_log.append({
                    "Date": date,
                    "Action": "SELL",
                    "Price": price,
                    "Shares": position,
                    "Value": revenue
                })
                position = 0
            
            # Record Equity
            equity = cash + (position * price)
            equity_curve.append(equity)
            
        df["Equity"] = equity_curve
        
        results = {
            "Final Equity": equity_curve[-1],
            "Return %": ((equity_curve[-1] - self.initial_capital) / self.initial_capital) * 100,
            "Trades": len(trade_log)
        }
        
        return df, pd.DataFrame(trade_log), results
