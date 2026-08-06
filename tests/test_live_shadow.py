from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from crypto_bot.config import AppConfig, MarketDataConfig, RiskConfig, StorageConfig, StrategyConfig
from crypto_bot.paper.shadow import run_shadow_once, run_shadow_session
from crypto_bot.storage.repositories import SQLiteStorage


class FrameProvider:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return self.frame


def test_live_shadow_records_decision_without_order_fill_or_balance_change(tmp_path) -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-07-23T10:00:00Z",
                    "2026-07-23T10:01:00Z",
                    "2026-07-23T10:02:00Z",
                    "2026-07-23T10:03:00Z",
                    "2026-07-23T10:04:00Z",
                    "2026-07-23T10:05:00Z",
                ],
                utc=True,
            ),
            "open": [10, 10, 10, 10, 10, 10.5],
            "high": [10, 10, 10, 10, 10, 10.5],
            "low": [10, 10, 10, 10, 10, 10.5],
            "close": [10, 10, 10, 10, 10, 10.5],
            "volume": [1, 1, 1, 1, 1, 1],
        }
    )
    database = tmp_path / "shadow.db"
    config = AppConfig(
        mode="shadow",
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
            limit=6,
        ),
        strategy=StrategyConfig(
            name="moving_average_cross",
            readiness="not_ready",
            fast_window=2,
            slow_window=3,
        ),
        risk=RiskConfig(min_bars_required=4),
        storage=StorageConfig(url=f"sqlite:///{database}"),
    )

    result = run_shadow_once(
        config,
        market_data_provider=FrameProvider(frame),
        now=datetime(2026, 7, 23, 10, 6, 30, tzinfo=timezone.utc),
    )

    assert result.signal_side == "buy"
    assert result.proposed_order is True

    storage = SQLiteStorage(config.storage.url)
    try:
        counts = {
            table: storage.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in [
                "shadow_observations",
                "orders",
                "fills",
                "balance_snapshots",
            ]
        }
        heartbeat = storage.latest_runtime_heartbeat("shadow")
    finally:
        storage.close()

    assert counts == {
        "shadow_observations": 1,
        "orders": 0,
        "fills": 0,
        "balance_snapshots": 0,
    }
    assert heartbeat is not None
    assert heartbeat["status"] == "completed"


def test_live_shadow_deduplicates_same_strategy_and_closed_bar(tmp_path) -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-07-23T10:00:00Z", periods=6, freq="1min"),
            "open": [10.0] * 6,
            "high": [10.0] * 6,
            "low": [10.0] * 6,
            "close": [10.0] * 6,
            "volume": [1.0] * 6,
        }
    )
    config = AppConfig(
        mode="shadow",
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
            limit=6,
        ),
        strategy=StrategyConfig(fast_window=2, slow_window=3),
        risk=RiskConfig(min_bars_required=4),
        storage=StorageConfig(url=f"sqlite:///{tmp_path / 'shadow.db'}"),
    )
    provider = FrameProvider(frame)
    now = datetime(2026, 7, 23, 10, 6, 30, tzinfo=timezone.utc)

    first = run_shadow_once(config, market_data_provider=provider, now=now)
    second = run_shadow_once(config, market_data_provider=provider, now=now)

    assert first.duplicate is False
    assert second.duplicate is True


def test_live_shadow_session_keeps_running_after_market_data_error(tmp_path) -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-07-23T10:00:00Z", periods=6, freq="1min"),
            "open": [10.0] * 6,
            "high": [10.0] * 6,
            "low": [10.0] * 6,
            "close": [10.0] * 6,
            "volume": [1.0] * 6,
        }
    )
    config = AppConfig(
        mode="shadow",
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
            limit=6,
            max_staleness_seconds=60,
        ),
        strategy=StrategyConfig(fast_window=2, slow_window=3),
        risk=RiskConfig(min_bars_required=4),
        storage=StorageConfig(url=f"sqlite:///{tmp_path / 'shadow_loop.db'}"),
    )
    times = iter(
        [
            datetime(2026, 7, 23, 11, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 23, 10, 6, 30, tzinfo=timezone.utc),
        ]
    )

    result = run_shadow_session(
        config,
        market_data_provider=FrameProvider(frame),
        max_iterations=2,
        now_provider=lambda: next(times),
    )

    assert result.errors == ["stale_market_data"]
    assert len(result.observations) == 1
