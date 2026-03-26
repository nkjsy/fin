import pandas as pd


def get_sp500_tickers():
    try:
        table = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
        df = table[0]
        return df['Symbol'].tolist()
    except Exception as e:
        print(f"Error fetching S&P 500: {e}")
        return ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA"]


def get_nasdaq100_tickers():
    try:
        tables = pd.read_html('https://en.wikipedia.org/wiki/Nasdaq-100')
        for df in tables:
            columns = {str(col).strip() for col in df.columns}
            if 'Ticker' in columns:
                series = df['Ticker']
                return [str(symbol).strip() for symbol in series.dropna().tolist()]
            if 'Symbol' in columns:
                series = df['Symbol']
                return [str(symbol).strip() for symbol in series.dropna().tolist()]
        raise ValueError('Ticker column not found in Nasdaq-100 tables')
    except Exception as e:
        print(f"Error fetching Nasdaq-100: {e}")
        return ["AAPL", "MSFT", "NVDA", "AMZN", "META"]
