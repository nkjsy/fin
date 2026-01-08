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
        
        # Detect datetime column name (yfinance uses 'Datetime' for intraday, 'Date' for daily)
        date_col = "Datetime" if "Datetime" in df.columns else "Date"
        
        # Ensure datetime column is available for logging
        if date_col not in df.columns:
            df[date_col] = df.index
        
        for i in range(len(df)):
            row = df.iloc[i]
            close_price = row["Close"]
            signal = row["Signal"]
            datetime_val = row[date_col]
            entry_price = row.get("Entry_Price", 0.0)
            exit_price = row.get("Exit_Price", 0.0)
            
            # Execute Trade
            if signal == 1 and cash > 0: # Buy
                # Use Entry_Price if available, otherwise fall back to Close
                buy_price = entry_price if entry_price > 0 else close_price
                # Buy max shares
                shares_to_buy = int(cash // buy_price)
                if shares_to_buy > 0:
                    cost = shares_to_buy * buy_price
                    cash -= cost
                    position += shares_to_buy
                    trade_log.append({
                        "Datetime": datetime_val,
                        "Action": "BUY",
                        "Price": buy_price,
                        "Shares": shares_to_buy,
                        "Value": cost
                    })
            
            elif signal == -1 and position > 0: # Sell
                # Use Exit_Price if available, otherwise fall back to Close
                sell_price = exit_price if exit_price > 0 else close_price
                revenue = position * sell_price
                cash += revenue
                trade_log.append({
                    "Datetime": datetime_val,
                    "Action": "SELL",
                    "Price": sell_price,
                    "Shares": position,
                    "Value": revenue
                })
                position = 0
            
            # Record Equity
            equity = cash + (position * close_price)
            equity_curve.append(equity)
            
        df["Equity"] = equity_curve
        
        results = {
            "Final-Equity": equity_curve[-1],
            "Return %": ((equity_curve[-1] - self.initial_capital) / self.initial_capital) * 100,
            "Trades": len(trade_log)
        }
        
        return df, pd.DataFrame(trade_log), results
