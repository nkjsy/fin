from .base import BaseScanner

class MomentumScanner(BaseScanner):
    def scan(self, min_price: float = 0, max_price: float = 0, max_float: int = 0) -> list:
        df = self.load_summary()
        if df.empty:
            return []
        print(f"Loaded universe summary with {len(df)} tickers.")
        
        # Apply filters
        # Ensure columns exist
        if "Close" not in df.columns or "FloatShares" not in df.columns:
            return []

        mask = (df["Close"] >= min_price) & (df["Close"] <= max_price) & (df["FloatShares"] <= max_float)
        filtered_df = df[mask]
        
        return filtered_df["Ticker"].tolist()
