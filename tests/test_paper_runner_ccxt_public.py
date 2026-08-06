import pandas as pd

from crypto_bot.errors import MarketDataError
from crypto_bot.config import AppConfig, MarketDataConfig, RiskConfig, StrategyConfig
from crypto_bot.paper.runner import run_paper_session


class FakeProvider:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, limit):
        self.calls.append((symbol, timeframe, limit))
        return self.frame


class BrokenProvider:
    def fetch_ohlcv(self, symbol, timeframe, limit):
        raise MarketDataError("fetch_ohlcv_failed:network down")


def _config(source="ccxt_public"):
    return AppConfig(
        mode="paper",
        symbols=["BTC/USDT"],
        live_trading=False,
        market_data=MarketDataConfig(
            source=source,
            exchange="binance",
            symbols=["BTC/USDT"],
            timeframe="1m",
            limit=6,
        ),
        strategy=StrategyConfig(fast_window=2, slow_window=3),
        risk=RiskConfig(min_bars_required=4),
    )


def test_paper_runner_uses_ccxt_public_provider_and_paper_engine_only(tmp_path):
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=6, freq="1min", tz="UTC"),
            "open": [10, 10, 10, 10, 10, 10.5],
            "high": [10, 10, 10, 10, 10, 10.5],
            "low": [10, 10, 10, 10, 10, 10.5],
            "close": [10, 10, 10, 10, 10, 10.5],
            "volume": [1, 1, 1, 1, 1, 1],
        }
    )
    config = _config()
    config = AppConfig(
        **{**config.__dict__, "storage": type(config.storage)(url=f"sqlite:///{tmp_path / 'paper.db'}")}
    )
    provider = FakeProvider(bars)

    result = run_paper_session(config, market_data_provider=provider)

    assert provider.calls == [("BTC/USDT", "1m", 6)]
    assert len(result.fills) == 1


def test_paper_runner_skips_trading_when_ccxt_public_returns_empty_data(tmp_path):
    config = _config()
    config = AppConfig(
        **{**config.__dict__, "storage": type(config.storage)(url=f"sqlite:///{tmp_path / 'paper.db'}")}
    )
    provider = FakeProvider(pd.DataFrame())

    result = run_paper_session(config, market_data_provider=provider)

    assert result.fills == []
    assert result.metrics.trade_count == 0


def test_paper_runner_skips_trading_when_ccxt_public_provider_fails(tmp_path):
    config = _config()
    config = AppConfig(
        **{**config.__dict__, "storage": type(config.storage)(url=f"sqlite:///{tmp_path / 'paper.db'}")}
    )

    result = run_paper_session(config, market_data_provider=BrokenProvider())

    assert result.fills == []
    assert result.metrics.trade_count == 0


def test_paper_runner_rejects_trade_when_ccxt_public_has_too_few_bars(tmp_path):
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=2, freq="1min", tz="UTC"),
            "open": [10, 10],
            "high": [10, 10],
            "low": [10, 10],
            "close": [10, 10],
            "volume": [1, 1],
        }
    )
    config = _config()
    config = AppConfig(
        **{**config.__dict__, "storage": type(config.storage)(url=f"sqlite:///{tmp_path / 'paper.db'}")}
    )

    result = run_paper_session(config, market_data_provider=FakeProvider(bars))

    assert result.fills == []
    assert result.metrics.trade_count == 0
