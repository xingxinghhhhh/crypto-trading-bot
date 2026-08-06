from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pandas as pd

from crypto_bot.config import AppConfig, MarketDataConfig
from crypto_bot.market.public_ccxt import PublicOhlcvBatch
from crypto_bot.market.recorder import record_public_market_data, run_market_recorder
from crypto_bot.market.recorder_summary import summarize_market_recordings


class BatchProvider:
    def __init__(self, batch: PublicOhlcvBatch) -> None:
        self.batch = batch

    def fetch_ohlcv_batch(self, symbol, timeframe, limit):
        return self.batch


class FlakyBatchProvider(BatchProvider):
    def __init__(self, batch: PublicOhlcvBatch, failures: int) -> None:
        super().__init__(batch)
        self.failures = failures
        self.call_count = 0

    def fetch_ohlcv_batch(self, symbol, timeframe, limit):
        self.call_count += 1
        if self.call_count <= self.failures:
            raise OSError(f"temporary network failure {self.call_count}")
        return super().fetch_ohlcv_batch(symbol, timeframe, limit)


def test_recorder_archives_raw_and_closed_bars_with_checksums(tmp_path) -> None:
    timestamps = pd.to_datetime(
        [
            "2026-07-23T10:00:00Z",
            "2026-07-23T10:01:00Z",
            "2026-07-23T10:02:00Z",
        ],
        utc=True,
    )
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [1.0, 2.0, 3.0],
        }
    )
    raw_rows = [
        [1784800800000, 100.0, 101.0, 99.0, 100.5, 1.0],
        [1784800860000, 101.0, 102.0, 100.0, 101.5, 2.0],
        [1784800920000, 102.0, 103.0, 101.0, 102.5, 3.0],
    ]
    config = AppConfig(
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
            limit=3,
            require_closed_bars=True,
        )
    )

    result = record_public_market_data(
        config,
        tmp_path,
        provider=BatchProvider(PublicOhlcvBatch(raw_rows=raw_rows, frame=frame)),
        now=datetime(2026, 7, 23, 10, 2, 30, tzinfo=timezone.utc),
    )

    assert result.raw_row_count == 3
    assert result.closed_row_count == 2
    assert len(pd.read_csv(result.normalized_path)) == 2
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["raw_sha256"] == hashlib.sha256(result.raw_path.read_bytes()).hexdigest()
    assert manifest["normalized_sha256"] == hashlib.sha256(
        result.normalized_path.read_bytes()
    ).hexdigest()

    summary = summarize_market_recordings(
        tmp_path,
        datetime(2026, 7, 23, tzinfo=timezone.utc).date(),
    )
    assert summary["healthy"] is True
    assert summary["batch_count"] == 1
    assert summary["closed_row_count"] == 2
    assert summary["duplicate_count"] == 0
    assert summary["gap_count"] == 0
    assert summary["checksum_failure_count"] == 0


def test_recorder_summary_reports_checksum_tampering(tmp_path) -> None:
    timestamps = pd.to_datetime(
        ["2026-07-23T10:00:00Z", "2026-07-23T10:01:00Z"],
        utc=True,
    )
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0, 100.0],
            "high": [100.0, 100.0],
            "low": [100.0, 100.0],
            "close": [100.0, 100.0],
            "volume": [1.0, 1.0],
        }
    )
    config = AppConfig(
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
            limit=2,
        )
    )
    result = record_public_market_data(
        config,
        tmp_path,
        provider=BatchProvider(
            PublicOhlcvBatch(
                raw_rows=[[1, 100, 100, 100, 100, 1], [2, 100, 100, 100, 100, 1]],
                frame=frame,
            )
        ),
        now=datetime(2026, 7, 23, 10, 2, 30, tzinfo=timezone.utc),
    )
    result.normalized_path.write_text("tampered", encoding="utf-8")

    summary = summarize_market_recordings(
        tmp_path,
        datetime(2026, 7, 23, tzinfo=timezone.utc).date(),
    )

    assert summary["healthy"] is False
    assert summary["checksum_failure_count"] == 1


def test_recorder_loop_continues_after_failed_iteration(tmp_path) -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-07-23T10:00:00Z", "2026-07-23T10:01:00Z"],
                utc=True,
            ),
            "open": [100.0, 100.0],
            "high": [100.0, 100.0],
            "low": [100.0, 100.0],
            "close": [100.0, 100.0],
            "volume": [1.0, 1.0],
        }
    )
    config = AppConfig(
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
            limit=2,
            max_staleness_seconds=30,
        )
    )
    times = iter(
        [
            datetime(2026, 7, 23, 10, 10, tzinfo=timezone.utc),
            datetime(2026, 7, 23, 10, 2, 20, tzinfo=timezone.utc),
        ]
    )

    result = run_market_recorder(
        config,
        tmp_path,
        provider=BatchProvider(
            PublicOhlcvBatch(
                raw_rows=[[1, 100, 100, 100, 100, 1], [2, 100, 100, 100, 100, 1]],
                frame=frame,
            )
        ),
        max_iterations=2,
        now_provider=lambda: next(times),
    )

    assert len(result.errors) == 1
    assert result.errors[0] == "stale_market_data"
    assert len(result.recordings) == 1
    assert result.attempt_count == 2
    assert result.retry_count == 0


def test_recorder_retries_with_bounded_exponential_backoff(tmp_path) -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2026-07-23T10:00:00Z", "2026-07-23T10:01:00Z"],
                utc=True,
            ),
            "open": [100.0, 100.0],
            "high": [100.0, 100.0],
            "low": [100.0, 100.0],
            "close": [100.0, 100.0],
            "volume": [1.0, 1.0],
        }
    )
    config = AppConfig(
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
            limit=2,
        )
    )
    provider = FlakyBatchProvider(
        PublicOhlcvBatch(
            raw_rows=[[1, 100, 100, 100, 100, 1], [2, 100, 100, 100, 100, 1]],
            frame=frame,
        ),
        failures=2,
    )
    sleep_calls = []

    result = run_market_recorder(
        config,
        tmp_path,
        provider=provider,
        max_retries=3,
        initial_backoff_seconds=0.5,
        max_backoff_seconds=0.75,
        now_provider=lambda: datetime(2026, 7, 23, 10, 2, 30, tzinfo=timezone.utc),
        sleep_fn=sleep_calls.append,
    )

    assert len(result.recordings) == 1
    assert result.errors == []
    assert result.attempt_count == 3
    assert result.retry_count == 2
    assert result.retry_delays_seconds == [0.5, 0.75]
    assert sleep_calls == [0.5, 0.75]
    assert result.attempt_errors == [
        "temporary network failure 1",
        "temporary network failure 2",
    ]


def test_recorder_reports_final_error_after_retry_budget_is_exhausted(tmp_path) -> None:
    config = AppConfig(
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
        )
    )
    provider = FlakyBatchProvider(
        PublicOhlcvBatch(raw_rows=[], frame=pd.DataFrame()),
        failures=10,
    )

    result = run_market_recorder(
        config,
        tmp_path,
        provider=provider,
        max_retries=2,
        initial_backoff_seconds=0,
        max_backoff_seconds=0,
        sleep_fn=lambda _: None,
    )

    assert result.recordings == []
    assert result.errors == ["temporary network failure 3"]
    assert result.attempt_count == 3
    assert result.retry_count == 2
    assert len(result.attempt_errors) == 3
