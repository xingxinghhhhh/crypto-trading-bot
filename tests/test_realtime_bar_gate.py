from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from crypto_bot.market.realtime_gate import gate_realtime_bars, timeframe_duration


def _bars(timestamps: list[str]) -> pd.DataFrame:
    count = len(timestamps)
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps, utc=True),
            "open": [100.0] * count,
            "high": [101.0] * count,
            "low": [99.0] * count,
            "close": [100.0] * count,
            "volume": [1.0] * count,
        }
    )


def test_gate_drops_forming_candle_and_keeps_closed_bars() -> None:
    bars = _bars(
        [
            "2026-07-23T10:00:00Z",
            "2026-07-23T10:01:00Z",
            "2026-07-23T10:02:00Z",
        ]
    )

    result = gate_realtime_bars(
        bars,
        "1m",
        datetime(2026, 7, 23, 10, 2, 30, tzinfo=timezone.utc),
    )

    assert result.reason is None
    assert result.bars["timestamp"].tolist() == bars["timestamp"].tolist()[:2]


def test_gate_rejects_stale_closed_market_data() -> None:
    result = gate_realtime_bars(
        _bars(["2026-07-23T10:00:00Z", "2026-07-23T10:01:00Z"]),
        "1m",
        datetime(2026, 7, 23, 10, 10, tzinfo=timezone.utc),
        max_staleness_seconds=120,
    )

    assert result.reason == "stale_market_data"


def test_gate_rejects_market_data_gap() -> None:
    result = gate_realtime_bars(
        _bars(["2026-07-23T10:00:00Z", "2026-07-23T10:02:00Z"]),
        "1m",
        datetime(2026, 7, 23, 10, 4, tzinfo=timezone.utc),
    )

    assert result.reason == "market_data_gap"


def test_timeframe_duration_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="unsupported timeframe"):
        timeframe_duration("monthly")
