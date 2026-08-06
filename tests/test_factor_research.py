from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_bot.config import AppConfig, MarketDataConfig
from crypto_bot.errors import MarketDataError
from crypto_bot.factor_research import (
    format_factor_research_report,
    run_factor_research,
)
from crypto_bot.factors import FactorSpec, compute_factor_frame, compute_forward_returns


def _bars(count: int = 240) -> pd.DataFrame:
    returns = np.array(
        [0.002 if (index // 20) % 2 == 0 else -0.002 for index in range(count)]
    )
    close = 100.0 * np.cumprod(1.0 + returns)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01T00:00:00Z",
                periods=count,
                freq="1min",
                tz="UTC",
            ),
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": 100.0 + np.arange(count) % 17,
        }
    )


def _write_archive(root, frame: pd.DataFrame, stamp: str = "one") -> None:
    batch_root = root / "okx" / "BTC_USDT" / "1m" / "2026-01-01"
    batch_root.mkdir(parents=True, exist_ok=True)
    raw_path = batch_root / f"raw_{stamp}.json"
    normalized_path = batch_root / f"closed_{stamp}.csv"
    manifest_path = batch_root / f"manifest_{stamp}.json"
    csv_frame = frame.copy()
    csv_frame["timestamp"] = pd.to_datetime(csv_frame["timestamp"], utc=True).map(
        lambda value: value.isoformat()
    )
    raw_path.write_text(
        json.dumps({"rows": csv_frame.to_dict(orient="records")}),
        encoding="utf-8",
    )
    csv_frame.to_csv(normalized_path, index=False, lineterminator="\n")
    manifest = {
        "exchange": "okx",
        "symbol": "BTC/USDT",
        "timeframe": "1m",
        "received_at": "2026-01-02T00:00:00+00:00",
        "raw_row_count": len(frame),
        "closed_row_count": len(frame),
        "last_closed_bar": csv_frame.iloc[-1]["timestamp"],
        "raw_file": raw_path.name,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "normalized_file": normalized_path.name,
        "normalized_sha256": hashlib.sha256(normalized_path.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _config() -> AppConfig:
    return AppConfig(
        mode="paper",
        market_data=MarketDataConfig(
            source="ccxt_public",
            exchange="okx",
            symbols=["BTC/USDT"],
            timeframe="1m",
        ),
    )


def test_forward_returns_are_aligned_strictly_after_the_factor_timestamp():
    bars = _bars(5)
    forward = compute_forward_returns(bars, (1, 2))

    assert forward.loc[0, "forward_return_1"] == (
        bars.loc[1, "close"] / bars.loc[0, "close"] - 1
    )
    assert forward.loc[0, "forward_return_2"] == (
        bars.loc[2, "close"] / bars.loc[0, "close"] - 1
    )
    assert pd.isna(forward.loc[4, "forward_return_1"])
    assert pd.isna(forward.loc[3, "forward_return_2"])


def test_factor_values_do_not_change_when_only_future_bars_change():
    original = _bars(120)
    changed = original.copy()
    changed.loc[100:, "close"] *= 2
    changed.loc[100:, "open"] = changed.loc[100:, "close"]
    changed.loc[100:, "high"] = changed.loc[100:, "close"] * 1.001
    changed.loc[100:, "low"] = changed.loc[100:, "close"] * 0.999

    original_factors = compute_factor_frame(original)
    changed_factors = compute_factor_frame(changed)

    pd.testing.assert_frame_equal(
        original_factors.iloc[:100],
        changed_factors.iloc[:100],
    )


def test_factor_research_reports_predictive_diagnostics_cost_pressure_and_exports(tmp_path):
    archive = tmp_path / "archive"
    output = tmp_path / "reports"
    _write_archive(archive, _bars())

    report = run_factor_research(
        _config(),
        archive,
        target_timeframe="1m",
        output_dir=output,
        horizons=(1, 4),
        cost_bps=(0.0, 5.0, 10.0),
        quantiles=5,
        rank_lookback=20,
        rolling_train_bars=80,
        rolling_test_bars=40,
        rolling_step_bars=40,
        min_observations=15,
    )

    assert report["research_status"] == "research_evidence_only"
    assert report["readiness_changed"] is False
    assert report["automatic_factor_approval"] is False
    assert len(report["factor_specs"]) == 6
    assert len(report["factor_metrics"]) == 12
    momentum = next(
        row
        for row in report["factor_metrics"]
        if row["factor"] == "momentum_4" and row["horizon"] == 1
    )
    assert momentum["pearson_ic"] > 0
    assert momentum["rank_ic"] > 0
    assert (
        momentum["mean_net_return_by_cost_bps"]["10"]
        <= momentum["mean_net_return_by_cost_bps"]["0"]
    )
    base_decay = next(
        row
        for row in report["factor_decay"]
        if row["factor"] == "momentum_4" and row["horizon"] == 1
    )
    assert base_decay["pearson_ic_retention_ratio"] == 1.0
    assert base_decay["absolute_rank_ic_retention_ratio"] == 1.0
    assert base_decay["pearson_ic_sign_preserved"] is True
    diagonal = next(
        row
        for row in report["factor_correlations"]
        if row["factor_left"] == "momentum_4"
        and row["factor_right"] == "momentum_4"
    )
    assert diagonal["correlation"] == 1.0
    assert report["rolling_windows"]
    assert report["stability"]
    assert "no_automatic_readiness_upgrade" in report["warnings"]
    assert all(Path(path).is_file() for path in report["export_paths"].values())


def test_factor_research_is_deterministic_and_independent_of_output_directory(tmp_path):
    archive = tmp_path / "archive"
    _write_archive(archive, _bars())
    settings = {
        "target_timeframe": "1m",
        "horizons": (1, 4),
        "rank_lookback": 20,
        "rolling_train_bars": 80,
        "rolling_test_bars": 40,
        "rolling_step_bars": 40,
        "min_observations": 15,
    }

    first = run_factor_research(
        _config(),
        archive,
        output_dir=tmp_path / "first",
        **settings,
    )
    second = run_factor_research(
        _config(),
        archive,
        output_dir=tmp_path / "second",
        **settings,
    )

    assert first["research_sha256"] == second["research_sha256"]
    assert first["factor_metrics"] == second["factor_metrics"]
    assert first["rolling_windows"] == second["rolling_windows"]


def test_factor_input_contract_rejects_invalid_bars_and_specs():
    valid = _bars(30)
    invalid_cases = []
    missing_column = valid.drop(columns=["volume"])
    invalid_cases.append((missing_column, "factor_invalid_columns"))
    missing_value = valid.copy()
    missing_value.loc[0, "open"] = np.nan
    invalid_cases.append((missing_value, "factor_missing_values"))
    descending = valid.iloc[::-1].reset_index(drop=True)
    invalid_cases.append((descending, "factor_timestamps_not_ascending"))
    duplicate = valid.copy()
    duplicate.loc[1, "timestamp"] = duplicate.loc[0, "timestamp"]
    invalid_cases.append((duplicate, "factor_duplicate_timestamp"))
    non_positive_close = valid.copy()
    non_positive_close.loc[0, "close"] = 0
    invalid_cases.append((non_positive_close, "factor_close_must_be_positive"))
    negative_volume = valid.copy()
    negative_volume.loc[0, "volume"] = -1
    invalid_cases.append((negative_volume, "factor_volume_must_be_non_negative"))

    with pytest.raises(MarketDataError, match="factor_bars_empty"):
        compute_factor_frame(pd.DataFrame())
    for bad_bars, expected in invalid_cases:
        with pytest.raises(MarketDataError, match=expected):
            compute_factor_frame(bad_bars)
    with pytest.raises(MarketDataError, match="factor_window_must_be_greater"):
        compute_factor_frame(valid, (FactorSpec("momentum", 1),))
    with pytest.raises(MarketDataError, match="unsupported_factor_family"):
        compute_factor_frame(valid, (FactorSpec("unknown", 4),))
    with pytest.raises(MarketDataError, match="duplicate_factor_name"):
        compute_factor_frame(
            valid,
            (FactorSpec("momentum", 4), FactorSpec("momentum", 4)),
        )
    with pytest.raises(MarketDataError, match="factor_horizons_must_be_positive"):
        compute_forward_returns(valid, (0,))


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"horizons": ()}, "factor_horizons_must_be_positive"),
        ({"cost_bps": (-1.0,)}, "factor_cost_bps_must_be_non_negative"),
        ({"quantiles": 1}, "factor_quantiles_must_be_at_least_two"),
        ({"rank_lookback": 1}, "factor_rank_lookback_must_be_at_least_two"),
        ({"rolling_train_bars": 0}, "factor_rolling_settings_must_be_positive"),
    ],
)
def test_factor_research_rejects_invalid_settings(tmp_path, override, expected):
    archive = tmp_path / "archive"
    _write_archive(archive, _bars(30))

    with pytest.raises(MarketDataError, match=expected):
        run_factor_research(
            _config(),
            archive,
            target_timeframe="1m",
            **override,
        )


def test_short_factor_history_stays_insufficient_and_does_not_export(tmp_path):
    archive = tmp_path / "archive"
    _write_archive(archive, _bars(30))

    report = run_factor_research(
        _config(),
        archive,
        target_timeframe="1m",
        output_dir=None,
        horizons=(1,),
        rank_lookback=10,
        rolling_train_bars=20,
        rolling_test_bars=20,
        rolling_step_bars=10,
        min_observations=5,
        specs=(FactorSpec("momentum", 4),),
    )

    assert report["research_status"] == "insufficient_data"
    assert report["rolling_windows"] == []
    assert report["stability"] == []
    assert report["export_paths"] == {}
    assert "fewer_than_3_rolling_windows" in report["warnings"]
    assert "research_status: insufficient_data" in format_factor_research_report(
        report
    )
