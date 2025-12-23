import pandas as pd
import requests
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

def get_us_stocks():
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        rows = data['data']['rows']
        df = pd.DataFrame(rows)
        
        # Clean symbols
        # NASDAQ uses '/' for classes (e.g. BRK/B) and '^' for other things.
        # yfinance uses '-' for classes (e.g. BRK-B).
        symbols = df['symbol'].tolist()
        cleaned_symbols = []
        for s in symbols:
            s = s.replace('/', '-')
            s = s.replace('^', '-P') # Assumption for preferreds, might need refinement
            cleaned_symbols.append(s)
            
        return cleaned_symbols
    except Exception as e:
        print(f"Error fetching US stocks: {e}")
        return []

if __name__ == "__main__":
    # sp = get_sp500_tickers()
    us = get_us_stocks()
    print(f"US Tickers: {us[:5]} ... Total: {len(us)}")