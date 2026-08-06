from pathlib import Path

import pandas as pd
import pytest

from crypto_bot.config import AppConfig, MarketDataConfig
from crypto_bot.errors import MarketDataError
from crypto_bot.market.history import fetch_history_to_csv


class FakePagedExchange:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=100):
        self.calls.append((symbol, timeframe, since, limit))
        if not self.pages:
            return []
        return self.pages.pop(0)


def _config(limit=2, use_environment_proxy=False):
    return AppConfig(
        mode="paper",
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1h",
            since="2024-01-01",
            until="2024-01-01T04:00:00Z",
            limit=limit,
            use_environment_proxy=use_environment_proxy,
        ),
    )


def test_fetch_history_paginates_deduplicates_sorts_and_writes_expected_columns(tmp_path):
    exchange = FakePagedExchange(
        [
            [
                [1_704_067_200_000, 10, 11, 9, 10.5, 100],
                [1_704_070_800_000, 10.5, 12, 10, 11.5, 120],
            ],
            [
                [1_704_070_800_000, 10.5, 12, 10, 11.5, 120],
                [1_704_074_400_000, 11.5, 13, 11, 12.5, 140],
            ],
            [
                [1_704_078_000_000, 12.5, 14, 12, 13.5, 160],
                [1_704_081_600_000, 13.5, 15, 13, 14.5, 180],
            ],
        ]
    )
    output = tmp_path / "BTC_USDT_1h.csv"

    result = fetch_history_to_csv(
        _config(),
        output,
        exchange_factory=lambda _: exchange,
    )

    frame = pd.read_csv(output)
    assert result.written_rows == 5
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    timestamps = pd.to_datetime(frame["timestamp"], utc=True)
    assert timestamps.is_monotonic_increasing
    assert not timestamps.duplicated().any()
    assert frame["close"].tolist() == [10.5, 11.5, 12.5, 13.5, 14.5]
    assert exchange.calls[0] == ("BTC/USDT", "1h", 1_704_067_200_000, 2)
    assert exchange.calls[1] == ("BTC/USDT", "1h", 1_704_074_400_000, 2)


def test_fetch_history_resumes_after_existing_csv_last_timestamp(tmp_path):
    output = tmp_path / "BTC_USDT_1h.csv"
    output.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume",
                "2024-01-01T00:00:00+00:00,10,11,9,10.5,100",
                "2024-01-01T01:00:00+00:00,10.5,12,10,11.5,120",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    exchange = FakePagedExchange(
        [
            [
                [1_704_074_400_000, 11.5, 13, 11, 12.5, 140],
                [1_704_078_000_000, 12.5, 14, 12, 13.5, 160],
            ],
        ]
    )

    fetch_history_to_csv(_config(), output, exchange_factory=lambda _: exchange)

    frame = pd.read_csv(output)
    assert frame["close"].tolist() == [10.5, 11.5, 12.5, 13.5]
    assert exchange.calls[0][2] == 1_704_074_400_000


def test_fetch_history_network_failure_does_not_change_existing_file(tmp_path):
    output = tmp_path / "BTC_USDT_1h.csv"
    original = "\n".join(
        [
            "timestamp,open,high,low,close,volume",
            "2024-01-01T00:00:00+00:00,10,11,9,10.5,100",
        ]
    ) + "\n"
    output.write_text(original, encoding="utf-8")

    class BrokenExchange:
        def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=100):
            raise OSError("network down")

    with pytest.raises(MarketDataError, match="fetch_ohlcv_failed"):
        fetch_history_to_csv(_config(), output, exchange_factory=lambda _: BrokenExchange())

    assert output.read_text(encoding="utf-8") == original


def test_fetch_history_network_failure_does_not_create_new_file(tmp_path):
    output = tmp_path / "BTC_USDT_1h.csv"

    class BrokenExchange:
        def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=100):
            raise OSError("network down")

    with pytest.raises(MarketDataError, match="fetch_ohlcv_failed"):
        fetch_history_to_csv(_config(), output, exchange_factory=lambda _: BrokenExchange())

    assert not Path(output).exists()


def test_fetch_history_passes_environment_proxy_setting_to_ccxt_factory(tmp_path):
    output = tmp_path / "BTC_USDT_1h.csv"
    captured_config = {}
    exchange = FakePagedExchange(
        [
            [[1_704_067_200_000, 10, 11, 9, 10.5, 100]],
            [],
        ]
    )

    fetch_history_to_csv(
        _config(use_environment_proxy=True),
        output,
        exchange_factory=lambda config: captured_config.update(config) or exchange,
    )

    assert captured_config["requests_trust_env"] is True
