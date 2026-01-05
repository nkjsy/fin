import pandas as pd
import pandas_ta as ta
from .base import BaseStrategy

class BullFlagStrategy(BaseStrategy):
    """
    A strategy of day trading, based on 1m or 5m charts.
    The entry conditions are:
    1. The price increases for at least 2 green bars by a certain percentage. At the same time, the volumes are also N times higher than the same time yesterday.
    2. Once a red bar follows the green bars, the price should not drop below the 50% of the previous high, and the price should not drop below the 9 period EMA, and the volume should be less than those in the green bars.
    Look for the first bar which has a higher high price than its previous bar's high price. This price is the entry price.
    
    The exit conditions:
    1. The stop loss exit price is the lowest low price of the pullback red bars.
    2. Take profit with the close price of the first red bar after entry.
    
    https://www.warriortrading.com/bull-flag-trading/
    """
    def __init__(self, 
                 min_green_bars: int = 2, 
                 price_increase_pct: float = 1.0, 
                 volume_factor: float = 2.0, 
                 ema_period: int = 9,
                 pullback_retracement: float = 0.5):
        """
        Args:
            min_green_bars: Minimum number of consecutive green bars.
            price_increase_pct: Minimum percentage increase during the green run (e.g. 1.0 for 1%).
            volume_factor: Volume must be N times higher than the same time yesterday.
            ema_period: Period for the EMA support.
            pullback_retracement: Max retracement allowed (0.5 for 50%).
        """
        super().__init__("Bull Flag Strategy")
        self.min_green_bars = min_green_bars
        self.price_increase_pct = price_increase_pct / 100.0
        self.volume_factor = volume_factor
        self.ema_period = ema_period
        self.pullback_retracement = pullback_retracement

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # Ensure we have data
        if df.empty:
            return df

        # Calculate EMA
        # pandas-ta might return None if not enough data
        df['EMA'] = df.ta.ema(length=self.ema_period)
        
        # Get Previous Day Volume
        # We attempt to shift the data by 1 day to align timestamps.
        # This requires a DatetimeIndex.
        if isinstance(df.index, pd.DatetimeIndex):
            # Shift the Volume column forward by 1 day (24 hours)
            # Note: This assumes the index is timezone-aware or consistent.
            # If weekends are skipped in the index, '1D' shift might land on a non-existent time.
            # However, we can't easily map "trading day - 1" without a calendar.
            # We will try to use the exact timestamp - 1 day.
            
            # Create a series with the index shifted by 1 Day
            # We want: Value at T = Volume at (T - 1 Day)
            # So we take the Volume series, shift its index by +1 Day, and reindex to original.
            
            # Actually, df.shift(freq='1D') moves the data at T to T+1D.
            # So the value at T+1D becomes the value from T.
            # This is exactly what we want: At T+1D, we want to see T's volume.
            
            try:
                shifted_vol = df['Volume'].shift(freq='1D')
                # Rename and join
                shifted_vol.name = 'Volume_Prev'
                # We need to join on index. 
                # Since shift(freq) preserves the index type, we can join.
                # But we must handle duplicates if any.
                df = df.join(shifted_vol)
            except Exception as e:
                # Fallback if shift fails (e.g. non-unique index)
                df['Volume_Prev'] = 0
        else:
            df['Volume_Prev'] = 0
            
        # Fill NaNs in Volume_Prev with 0 or handle in logic
        df['Volume_Prev'] = df['Volume_Prev'].fillna(0)

        # Initialize columns
        df['Signal'] = 0
        df['Entry_Price'] = 0.0
        df['Stop_Loss'] = 0.0
        df['Exit_Price'] = 0.0
        
        # Pre-calculate Green/Red
        df['is_green'] = df['Close'] > df['Open']
        df['is_red'] = df['Close'] < df['Open']
        
        # State variables
        state = 'SCANNING' # SCANNING, PULLBACK, IN_POSITION
        
        green_seq_count = 0
        green_seq_vol_sum = 0.0
        green_seq_start_price = 0.0
        green_seq_high = 0.0
        green_seq_low = 0.0
        
        # Variables to persist during PULLBACK
        pb_limit_price = 0.0
        pb_avg_green_vol = 0.0
        pb_min_low = float('inf') # Track lowest low of red bars in pullback
        
        # Position variables
        pos_entry_price = 0.0
        pos_stop_loss = 0.0
        
        # Iterate
        # Using itertuples for better performance than iterrows
        # We need index access for setting values
        
        # We need to access previous row, so we start from 1
        # Convert to list of dicts to avoid index lookup overhead inside loop?
        # Or just use integer indexing on columns.
        
        # Let's use direct array access for speed if possible, but we need to write back to DF.
        # We will write to lists and assign at the end.
        signals = [0] * len(df)
        entry_prices = [0.0] * len(df)
        stop_losses = [0.0] * len(df)
        exit_prices = [0.0] * len(df)
        
        # Get numpy arrays for speed
        opens = df['Open'].values
        closes = df['Close'].values
        highs = df['High'].values
        lows = df['Low'].values
        volumes = df['Volume'].values
        vol_prevs = df['Volume_Prev'].values
        emas = df['EMA'].values
        is_greens = df['is_green'].values
        is_reds = df['is_red'].values
        
        for i in range(1, len(df)):
            # Current bar data
            curr_open = opens[i]
            curr_close = closes[i]
            curr_high = highs[i]
            curr_low = lows[i]
            curr_vol = volumes[i]
            curr_vol_prev = vol_prevs[i]
            curr_ema = emas[i]
            is_green = is_greens[i]
            is_red = is_reds[i]
            
            # Previous bar data
            prev_high = highs[i-1]
            prev_close = closes[i-1]
            
            # Volume Condition: Current Vol > N * Prev Vol
            # If Prev Vol is 0 (missing data), condition fails
            vol_cond = (curr_vol > self.volume_factor * curr_vol_prev) if curr_vol_prev > 0 else False
            
            if state == 'SCANNING':
                if is_green:
                    if green_seq_count == 0:
                        green_seq_start_price = curr_open
                        green_seq_low = curr_low
                        green_seq_high = curr_high
                        green_seq_vol_sum = curr_vol
                    else:
                        green_seq_high = max(green_seq_high, curr_high)
                        green_seq_low = min(green_seq_low, curr_low)
                        green_seq_vol_sum += curr_vol
                    
                    if vol_cond:
                        green_seq_count += 1
                    else:
                        # Volume condition failed
                        green_seq_count = 0
                        
                elif is_red:
                    # Potential transition to PULLBACK
                    if green_seq_count >= self.min_green_bars:
                        # Check price increase
                        # Increase from Start Open to Last Close (Prev Close)
                        increase = (prev_close - green_seq_start_price) / green_seq_start_price
                        
                        if increase >= self.price_increase_pct:
                            # Setup confirmed. Check Pullback conditions for THIS bar.
                            
                            # Calculate Retracement Limit
                            # Low > SwingHigh - 0.5 * (SwingHigh - SwingLow)
                            pb_limit_price = green_seq_high - self.pullback_retracement * (green_seq_high - green_seq_low)
                            pb_avg_green_vol = green_seq_vol_sum / green_seq_count
                            
                            # Check conditions
                            cond_retracement = curr_low >= pb_limit_price
                            cond_ema = curr_low >= curr_ema if not pd.isna(curr_ema) else False
                            cond_vol = curr_vol < pb_avg_green_vol
                            
                            if cond_retracement and cond_ema and cond_vol:
                                state = 'PULLBACK'
                                pb_min_low = curr_low # Initialize with this red bar's low
                                # We do not check for entry on the first red bar of the pullback.
                            else:
                                # Failed pullback conditions
                                green_seq_count = 0
                        else:
                            green_seq_count = 0
                    else:
                        green_seq_count = 0
                else:
                    # Doji/Flat
                    green_seq_count = 0
            
            elif state == 'PULLBACK':                
                # Check Trigger
                if curr_high > prev_high:
                    signals[i] = 1
                    entry_prices[i] = prev_high
                    stop_losses[i] = pb_min_low
                    
                    pos_entry_price = prev_high
                    pos_stop_loss = pb_min_low
                    state = 'IN_POSITION'
                    green_seq_count = 0
                else:
                    # Check validity
                    cond_retracement = curr_low >= pb_limit_price
                    cond_ema = curr_low >= curr_ema if not pd.isna(curr_ema) else False
                    cond_vol = curr_vol < pb_avg_green_vol
                    
                    if not (cond_retracement and cond_ema and cond_vol):
                        # Failed
                        state = 'SCANNING'
                        green_seq_count = 0
                        # If this bar is Green and meets volume, start new sequence.
                        if is_green and vol_cond:
                             green_seq_count = 1
                             green_seq_start_price = curr_open
                             green_seq_low = curr_low
                             green_seq_high = curr_high
                             green_seq_vol_sum = curr_vol
                    else:
                        # Validity check passed, continue PULLBACK
                        pb_min_low = min(pb_min_low, curr_low)
            
            elif state == 'IN_POSITION':
                # Check Stop Loss
                if curr_low < pos_stop_loss:
                    signals[i] = -1
                    exit_prices[i] = pos_stop_loss
                    state = 'SCANNING'
                    green_seq_count = 0
                # Check Take Profit (First Red Bar Close)
                elif is_red:
                    signals[i] = -1
                    exit_prices[i] = curr_close
                    state = 'SCANNING'
                    green_seq_count = 0
                else:
                # continue in position
                    pass

        df['Signal'] = signals
        df['Entry_Price'] = entry_prices
        df['Stop_Loss'] = stop_losses
        df['Exit_Price'] = exit_prices
        
        return df

