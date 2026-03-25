from datetime import datetime

import pandas as pd

from broker.paper_broker import PaperBroker
from data_manager import DataManager
from live_momentum_portfolio import MomentumLiveTrader, RebalanceOrder
from strategy.momentum_11_1 import Momentum11_1Strategy


class DummyClientWrapper:
    class DummyClient:
        def get_quotes(self, symbols):
            class Resp:
                status_code = 200

                @staticmethod
                def json():
                    return {
                        symbol: {"quote": {"lastPrice": 100.0 + idx * 10.0}}
                        for idx, symbol in enumerate(symbols)
                    }

            return Resp()

    @property
    def client(self):
        return self.DummyClient()


class DummyDataManager(DataManager):
    def __init__(self, price_data):
        self._price_data = price_data
        self.data_dir = "data"
        self.provider = None

    def load_data(self, ticker: str, interval: str) -> pd.DataFrame:
        return self._price_data.get(ticker, pd.DataFrame()).copy()

    def download_data(self, ticker: str, interval: str, period: str = "1y", end_date: str = None) -> bool:
        return ticker in self._price_data and not self._price_data[ticker].empty


def make_symbol_df(dates: pd.DatetimeIndex, closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1_000_000] * len(dates),
        }
    )


def test_build_rebalance_plan_smoke():
    dates = pd.date_range("2024-01-01", periods=320, freq="B")
    price_data = {
        "AAA": make_symbol_df(dates, [100.0 + i * 0.50 for i in range(len(dates))]),
        "BBB": make_symbol_df(dates, [100.0 + i * 0.30 for i in range(len(dates))]),
        "CCC": make_symbol_df(dates, [100.0 + i * 0.10 for i in range(len(dates))]),
    }

    trader = MomentumLiveTrader(
        client_wrapper=DummyClientWrapper(),
        broker=PaperBroker(initial_cash=100000.0),
        data_manager=DummyDataManager(price_data),
        strategy=Momentum11_1Strategy(lookback_days=60, skip_days=21, top_n=2),
        universe_by_month={pd.Timestamp("2024-12-31"): ["AAA", "BBB", "CCC"]},
        history_period="2y",
    )

    plan = trader.build_rebalance_plan(datetime(2024, 12, 31, 15, 55))
    assert len(plan.selected_symbols) == 2
    assert len(plan.orders) == 2
    assert all(isinstance(order, RebalanceOrder) for order in plan.orders)


def test_is_rebalance_day_approximation():
    trader = MomentumLiveTrader(
        client_wrapper=DummyClientWrapper(),
        broker=PaperBroker(initial_cash=100000.0),
        data_manager=DummyDataManager({}),
        strategy=Momentum11_1Strategy(top_n=2),
        universe_by_month={pd.Timestamp("2024-01-31"): ["AAA"]},
    )

    assert trader.is_rebalance_day(datetime(2024, 1, 31, 15, 55))
    assert not trader.is_rebalance_day(datetime(2024, 1, 30, 15, 55))


if __name__ == "__main__":
    test_build_rebalance_plan_smoke()
    test_is_rebalance_day_approximation()
    print("Momentum live tests passed.")