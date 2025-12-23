import pandas as pd
from yfinance import EquityQuery

def get_sp500_tickers():
    try:
        # Requires lxml or html5lib
        table = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
        df = table[0]
        return df['Symbol'].tolist()
    except Exception as e:
        print(f"Error fetching S&P 500: {e}")
        return ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA"] # Fallback

# https://ranaroussi.github.io/yfinance/reference/api/yfinance.EquityQuery.html#yfinance.EquityQuery
# https://ranaroussi.github.io/yfinance/reference/api/yfinance.screen.html
def get_stocks(region: str):
    try:
        return EquityQuery('EQ', ['region', region])
    except Exception as e:
        raise Exception(f"Error fetching US stocks: {e}")
        
if __name__ == "__main__":
    # sp = get_sp500_tickers()
    us = get_stocks('us')
    print(f"us Tickers: {us[:5]} ... Total: {len(us)}")