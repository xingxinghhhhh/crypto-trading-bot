from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from crypto_bot.config import AppConfig, MarketDataConfig, RiskConfig, StrategyConfig
from crypto_bot.errors import MarketDataError
from crypto_bot.market.archive_replay import build_replay_dataset
from crypto_bot.replay import run_market_replay


def _rows(start: str, count: int, *, first_value: int = 100) -> list[dict]:
    timestamps = pd.date_range(start, periods=count, freq="1min", tz="UTC")
    return [
        {
            "timestamp": timestamp.isoformat(),
            "open": float(first_value + index),
            "high": float(first_value + index + 1),
            "low": float(first_value + index - 1),
            "close": float(first_value + index + 0.5),
            "volume": float(index + 1),
        }
        for index, timestamp in enumerate(timestamps)
    ]


def _write_batch(root, stamp: str, rows: list[dict]) -> tuple:
    batch_root = root / "okx" / "BTC_USDT" / "1m" / "2026-07-23"
    batch_root.mkdir(parents=True, exist_ok=True)
    raw_path = batch_root / f"raw_{stamp}.json"
    normalized_path = batch_root / f"closed_{stamp}.csv"
    manifest_path = batch_root / f"manifest_{stamp}.json"
    raw_path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    pd.DataFrame(rows).to_csv(normalized_path, index=False, lineterminator="\n")
    manifest = {
        "exchange": "okx",
        "symbol": "BTC/USDT",
        "timeframe": "1m",
        "received_at": "2026-07-23T10:30:00+00:00",
        "raw_row_count": len(rows),
        "closed_row_count": len(rows),
        "last_closed_bar": rows[-1]["timestamp"],
        "raw_file": raw_path.name,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "normalized_file": normalized_path.name,
        "normalized_sha256": hashlib.sha256(normalized_path.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return raw_path, normalized_path, manifest_path


def test_replay_deduplicates_overlapping_batches_and_resamples_complete_bars(tmp_path):
    all_rows = _rows("2026-07-23T10:00:00Z", 20)
    first = all_rows[:15]
    second = all_rows[10:]
    _write_batch(tmp_path, "one", first)
    _write_batch(tmp_path, "two", second)
    output = tmp_path / "replay_15m.csv"

    result = build_replay_dataset(
        tmp_path,
        exchange="okx",
        symbol="BTC/USDT",
        source_timeframe="1m",
        target_timeframe="15m",
        output_csv=output,
    )

    assert result.report.manifest_count == 2
    assert result.report.input_bar_count == 25
    assert result.report.unique_source_bar_count == 20
    assert result.report.duplicate_bar_count == 5
    assert result.report.dropped_incomplete_source_bar_count == 5
    assert result.report.replay_bar_count == 1
    assert result.bars.iloc[0].to_dict() == {
        "timestamp": pd.Timestamp("2026-07-23T10:00:00Z"),
        "open": 100.0,
        "high": 115.0,
        "low": 99.0,
        "close": 114.5,
        "volume": 120.0,
    }
    assert hashlib.sha256(output.read_bytes()).hexdigest() == result.report.dataset_sha256


def test_replay_dataset_hash_is_deterministic(tmp_path):
    _write_batch(tmp_path, "one", _rows("2026-07-23T10:00:00Z", 15))

    first = build_replay_dataset(
        tmp_path,
        exchange="okx",
        symbol="BTC/USDT",
        source_timeframe="1m",
        target_timeframe="15m",
    )
    second = build_replay_dataset(
        tmp_path,
        exchange="okx",
        symbol="BTC/USDT",
        source_timeframe="1m",
        target_timeframe="15m",
    )

    assert first.report.dataset_sha256 == second.report.dataset_sha256
    pd.testing.assert_frame_equal(first.bars, second.bars)


def test_replay_fails_closed_on_checksum_mismatch(tmp_path):
    _, normalized_path, _ = _write_batch(
        tmp_path,
        "one",
        _rows("2026-07-23T10:00:00Z", 15),
    )
    normalized_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(MarketDataError, match="replay_checksum_mismatch"):
        build_replay_dataset(
            tmp_path,
            exchange="okx",
            symbol="BTC/USDT",
            source_timeframe="1m",
            target_timeframe="15m",
        )


def test_replay_fails_closed_on_conflicting_duplicate(tmp_path):
    first = _rows("2026-07-23T10:00:00Z", 15)
    conflicting = _rows("2026-07-23T10:10:00Z", 5, first_value=999)
    _write_batch(tmp_path, "one", first)
    _write_batch(tmp_path, "two", conflicting)

    with pytest.raises(MarketDataError, match="replay_conflicting_duplicate"):
        build_replay_dataset(
            tmp_path,
            exchange="okx",
            symbol="BTC/USDT",
            source_timeframe="1m",
            target_timeframe="15m",
        )


def test_replay_fails_closed_on_source_gap(tmp_path):
    rows = _rows("2026-07-23T10:00:00Z", 15)
    del rows[5]
    _write_batch(tmp_path, "one", rows)

    with pytest.raises(MarketDataError, match="replay_source_gap_detected:1"):
        build_replay_dataset(
            tmp_path,
            exchange="okx",
            symbol="BTC/USDT",
            source_timeframe="1m",
            target_timeframe="15m",
        )


def test_strategy_replay_digest_is_deterministic_even_when_signal_ids_are_random(tmp_path):
    rows = []
    prices = [100.0, 90.0, 90.0, 120.0]
    for bucket, price in enumerate(prices):
        for minute in range(15):
            timestamp = pd.Timestamp("2026-07-23T10:00:00Z") + pd.Timedelta(
                minutes=bucket * 15 + minute
            )
            rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "open": price,
                    "high": price + 1,
                    "low": price - 1,
                    "close": price,
                    "volume": 1.0,
                }
            )
    _write_batch(tmp_path, "one", rows)
    config = AppConfig(
        mode="paper",
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
        ),
        strategy=StrategyConfig(fast_window=2, slow_window=3),
        risk=RiskConfig(min_bars_required=3, abnormal_move_pct=1.0),
    )

    first = run_market_replay(
        config,
        tmp_path,
        target_timeframe="15m",
        export_path=tmp_path / "first.json",
    )
    second = run_market_replay(
        config,
        tmp_path,
        target_timeframe="15m",
        export_path=tmp_path / "second.json",
    )

    assert len(first.backtest.trades) == 1
    assert len(second.backtest.trades) == 1
    assert first.replay_sha256 == second.replay_sha256
    first_report = json.loads((tmp_path / "first.json").read_text(encoding="utf-8"))
    second_report = json.loads((tmp_path / "second.json").read_text(encoding="utf-8"))
    assert first_report == second_report


def test_strategy_replay_digest_ignores_equivalent_archive_batching(tmp_path):
    rows = _rows("2026-07-23T10:00:00Z", 60)
    single_root = tmp_path / "single"
    overlapping_root = tmp_path / "overlapping"
    _write_batch(single_root, "one", rows)
    _write_batch(overlapping_root, "one", rows[:45])
    _write_batch(overlapping_root, "two", rows[30:])
    config = AppConfig(
        mode="paper",
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
        ),
        strategy=StrategyConfig(fast_window=2, slow_window=3),
        risk=RiskConfig(min_bars_required=3, abnormal_move_pct=1.0),
    )

    single = run_market_replay(config, single_root, target_timeframe="15m")
    overlapping = run_market_replay(
        config,
        overlapping_root,
        target_timeframe="15m",
    )

    assert single.dataset.dataset_sha256 == overlapping.dataset.dataset_sha256
    assert single.replay_sha256 == overlapping.replay_sha256
    assert single.dataset.manifest_count == 1
    assert overlapping.dataset.manifest_count == 2
    assert overlapping.dataset.duplicate_bar_count == 15
