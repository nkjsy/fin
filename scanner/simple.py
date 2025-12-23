from .base import BaseScanner

class SimpleScanner(BaseScanner):
    def scan(self, min_price: float = 0, min_volume: float = 0) -> list:
        df = self.load_summary()
        if df.empty:
            return []
        
        # Apply filters
        # Ensure columns exist
        if "Close" not in df.columns or "Volume" not in df.columns:
            return []

        mask = (df["Close"] >= min_price) & (df["Volume"] >= min_volume)
        filtered_df = df[mask]
        
        return filtered_df["Ticker"].tolist()
