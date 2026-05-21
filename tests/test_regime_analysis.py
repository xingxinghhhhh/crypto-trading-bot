import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crypto_bot.regime_analysis import analyze_regimes


def test_regime_analysis_calculates_window_features(tmp_path):
    bars = _write_bars(
        tmp_path,
        [
            ("2024-01-01T00:00:00Z", 100, 101, 99, 100, 100),
            ("2024-01-01T01:00:00Z", 100, 111, 99, 110, 110),
            ("2024-01-01T02:00:00Z", 110, 121, 109, 120, 150),
        ],
    )
    wf = _write_walk_forward(tmp_path, [(1, "2024-01-01T00:00:00Z", "2024-01-01T02:00:00Z", 2.5, 5, 30)])

    report = analyze_regimes(bars, wf)
    features = report.windows[0].features

    assert report.window_count == 1
    assert features["window_return_pct"] == 20.0
    assert features["max_drawdown_pct"] == 0.0
    assert features["moving_average_slope"] == 10.0
    assert features["price_above_slow_ma_pct"] == 66.6666666667
    assert features["volume_change_pct"] == 50.0


def test_regime_analysis_matches_walk_forward_windows_and_splits_profitability(tmp_path):
    bars = _write_bars(
        tmp_path,
        [
            ("2024-01-01T00:00:00Z", 100, 101, 99, 100, 100),
            ("2024-01-01T01:00:00Z", 100, 111, 99, 110, 110),
            ("2024-01-01T02:00:00Z", 110, 121, 109, 120, 120),
            ("2024-01-01T03:00:00Z", 120, 122, 95, 100, 150),
            ("2024-01-01T04:00:00Z", 100, 101, 89, 90, 140),
            ("2024-01-01T05:00:00Z", 90, 91, 79, 80, 130),
        ],
    )
    wf = _write_walk_forward(
        tmp_path,
        [
            (1, "2024-01-01T00:00:00Z", "2024-01-01T02:00:00Z", 3.0, 5, 30),
            (2, "2024-01-01T03:00:00Z", "2024-01-01T05:00:00Z", -2.0, 10, 50),
        ],
    )

    report = analyze_regimes(bars, wf)

    assert report.profitable_window_count == 1
    assert report.losing_window_count == 1
    assert report.profitable_window_regime_summary["window_return_pct"]["average"] == 20.0
    assert report.losing_window_regime_summary["window_return_pct"]["average"] == -20.0
    assert report.feature_difference_summary["window_return_pct"]["profitable_minus_losing_average"] == 40.0
    assert report.likely_discriminating_features[0] == "window_return_pct"


def test_regime_analysis_exports_json(tmp_path):
    bars = _write_bars(
        tmp_path,
        [
            ("2024-01-01T00:00:00Z", 100, 101, 99, 100, 100),
            ("2024-01-01T01:00:00Z", 100, 111, 99, 110, 110),
        ],
    )
    wf = _write_walk_forward(tmp_path, [(1, "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", 1.0, 5, 30)])
    export_path = tmp_path / "regime.json"

    report = analyze_regimes(bars, wf, export_path=export_path)

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["window_count"] == report.window_count
    assert payload["candidate_filter_rules"]


def test_regime_analysis_empty_or_missing_columns_raise_clear_errors(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    wf = _write_walk_forward(tmp_path, [(1, "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", 1.0, 5, 30)])

    with pytest.raises(ValueError, match="ohlcv csv is empty"):
        analyze_regimes(empty, wf)

    bad = tmp_path / "bad.csv"
    bad.write_text("timestamp,open,high,low,close\n2024-01-01T00:00:00Z,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        analyze_regimes(bad, wf)


def test_regime_analysis_cli_outputs_and_exports(tmp_path):
    bars = _write_bars(
        tmp_path,
        [
            ("2024-01-01T00:00:00Z", 100, 101, 99, 100, 100),
            ("2024-01-01T01:00:00Z", 100, 111, 99, 110, 110),
        ],
    )
    wf = _write_walk_forward(tmp_path, [(1, "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", 1.0, 5, 30)])
    export_path = tmp_path / "regime.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "regime-analysis",
            "--csv",
            str(bars),
            "--walk-forward-results",
            str(wf),
            "--export",
            str(export_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "regime_analysis_window_count: 1" in completed.stdout
    assert export_path.exists()


def _write_bars(tmp_path: Path, rows: list[tuple[str, float, float, float, float, float]]) -> Path:
    path = tmp_path / "bars.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        writer.writerows(rows)
    return path


def _write_walk_forward(
    tmp_path: Path,
    rows: list[tuple[int, str, str, float, int, int]],
) -> Path:
    path = tmp_path / "walk_forward.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "window_id",
                "test_start",
                "test_end",
                "selected_fast_window",
                "selected_slow_window",
                "test_total_return_pct",
                "test_max_drawdown_pct",
                "test_trade_count",
                "test_profit_factor",
            ],
        )
        writer.writeheader()
        for window_id, start, end, return_pct, fast, slow in rows:
            writer.writerow(
                {
                    "window_id": window_id,
                    "test_start": start,
                    "test_end": end,
                    "selected_fast_window": fast,
                    "selected_slow_window": slow,
                    "test_total_return_pct": return_pct,
                    "test_max_drawdown_pct": 1.0,
                    "test_trade_count": 3,
                    "test_profit_factor": 1.5,
                }
            )
    return path
