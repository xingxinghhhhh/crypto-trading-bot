from datetime import timezone

import pytest

from crypto_bot.errors import MarketDataError
from crypto_bot.market.public_ccxt import PublicMarketDataProvider


class FakeExchange:
    def __init__(self, config=None):
        self.config = config or {}
        self.fetch_calls = []
        self.create_order_called = False

    def fetch_ohlcv(self, symbol, timeframe="1m", limit=100):
        self.fetch_calls.append((symbol, timeframe, limit))
        return [
            [1_767_225_600_000, 100, 101, 99, 100.5, 12],
            [1_767_225_660_000, 100.5, 102, 100, 101.5, 10],
        ]

    def create_order(self, *args, **kwargs):
        self.create_order_called = True
        raise AssertionError("create_order must never be called by public market data provider")


def test_public_market_data_provider_fetches_ohlcv_with_expected_shape():
    provider = PublicMarketDataProvider(exchange_id="binance", exchange_factory=lambda _: FakeExchange())

    frame = provider.fetch_ohlcv("BTC/USDT", timeframe="1m", limit=2)

    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert frame["timestamp"].iloc[0].tzinfo == timezone.utc
    assert frame["close"].iloc[-1] == 101.5


def test_public_market_data_provider_wraps_network_errors():
    class BrokenExchange(FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe="1m", limit=100):
            raise OSError("network down")

    provider = PublicMarketDataProvider(exchange_id="binance", exchange_factory=lambda _: BrokenExchange())

    with pytest.raises(MarketDataError, match="fetch_ohlcv_failed"):
        provider.fetch_ohlcv("BTC/USDT", timeframe="1m", limit=2)


def test_public_market_data_provider_rejects_empty_ohlcv():
    class EmptyExchange(FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe="1m", limit=100):
            return []

    provider = PublicMarketDataProvider(exchange_id="binance", exchange_factory=lambda _: EmptyExchange())

    with pytest.raises(MarketDataError, match="empty_ohlcv"):
        provider.fetch_ohlcv("BTC/USDT", timeframe="1m", limit=2)


def test_public_market_data_provider_rejects_missing_values():
    class MissingExchange(FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe="1m", limit=100):
            return [[1_767_225_600_000, 100, 101, 99, None, 12]]

    provider = PublicMarketDataProvider(exchange_id="binance", exchange_factory=lambda _: MissingExchange())

    with pytest.raises(MarketDataError, match="missing_ohlcv_values"):
        provider.fetch_ohlcv("BTC/USDT", timeframe="1m", limit=1)


def test_public_market_data_provider_rejects_abnormal_timestamps():
    class BadTimestampExchange(FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe="1m", limit=100):
            return [
                [1_767_225_660_000, 100, 101, 99, 100.5, 12],
                [1_767_225_600_000, 100.5, 102, 100, 101.5, 10],
            ]

    provider = PublicMarketDataProvider(exchange_id="binance", exchange_factory=lambda _: BadTimestampExchange())

    with pytest.raises(MarketDataError, match="abnormal_ohlcv_timestamp"):
        provider.fetch_ohlcv("BTC/USDT", timeframe="1m", limit=2)


def test_public_market_data_provider_does_not_call_create_order():
    fake = FakeExchange()
    provider = PublicMarketDataProvider(exchange_id="binance", exchange_factory=lambda _: fake)

    provider.fetch_ohlcv("BTC/USDT", timeframe="1m", limit=2)

    assert fake.create_order_called is False


def test_public_market_data_provider_supports_okx_fetch_ohlcv_path():
    fake = FakeExchange()
    provider = PublicMarketDataProvider(exchange_id="okx", exchange_factory=lambda _: fake)

    frame = provider.fetch_ohlcv("BTC/USDT", timeframe="1m", limit=2)

    assert len(frame) == 2
    assert fake.fetch_calls == [("BTC/USDT", "1m", 2)]
    assert fake.create_order_called is False


def test_public_market_data_provider_explicitly_enables_environment_proxy():
    captured_config = {}
    fake = FakeExchange()

    def factory(config):
        captured_config.update(config)
        return fake

    provider = PublicMarketDataProvider(
        exchange_id="okx",
        use_environment_proxy=True,
        exchange_factory=factory,
    )

    provider.fetch_ohlcv("BTC/USDT", timeframe="1m", limit=2)

    assert captured_config == {
        "enableRateLimit": True,
        "requests_trust_env": True,
    }


def test_public_market_data_provider_does_not_trust_environment_proxy_by_default():
    captured_config = {}

    PublicMarketDataProvider(
        exchange_id="okx",
        exchange_factory=lambda config: captured_config.update(config) or FakeExchange(),
    )

    assert captured_config["requests_trust_env"] is False
