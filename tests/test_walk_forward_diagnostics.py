import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crypto_bot.walk_forward_diagnostics import diagnose_walk_forward


def test_walk_forward_diagnose_reads_results_and_summarizes_distribution(tmp_path):
    path = _write_walk_forward_results(
        tmp_path,
        returns=[1, -2, 0, 3, -1],
        profit_factors=[1.5, 0.5, 1.0, 2.0, 0.8],
        params=[(5, 30), (5, 30), (10, 50), (5, 30), (10, 50)],
    )

    report = diagnose_walk_forward(path)

    assert report.window_count == 5
    assert report.positive_test_window_count == 2
    assert report.negative_test_window_count == 2
    assert report.zero_or_flat_window_count == 1
    assert report.average_test_return_pct == 0.2
    assert report.median_test_return_pct == 0
    assert report.worst_test_return_pct == -2
    assert report.best_test_return_pct == 3
    assert report.most_common_parameter == "5/30"
    assert report.most_common_parameter_share_pct == 60
    assert report.number_of_unique_parameter_sets == 2
    assert len(report.worst_windows) == 5
    assert len(report.best_windows) == 5


def test_walk_forward_diagnose_identifies_profit_concentration(tmp_path):
    path = _write_walk_forward_results(
        tmp_path,
        returns=[10, 1, -1, -1, -1],
        profit_factors=[3.0, 1.2, 0.8, 0.7, 0.6],
        params=[(5, 30)] * 5,
    )

    report = diagnose_walk_forward(path)

    assert report.percentage_of_profit_from_top_20pct_windows == 90.9090909091
    assert report.stability_rating == "concentrated"
    assert "returns_are_concentrated_in_top_windows" in report.flags


def test_walk_forward_diagnose_identifies_frequent_parameter_switching(tmp_path):
    path = _write_walk_forward_results(
        tmp_path,
        returns=[1, 1, 1, 1, 1],
        profit_factors=[1.4] * 5,
        params=[(5, 30), (10, 50), (15, 100), (20, 150), (5, 150)],
    )

    report = diagnose_walk_forward(path)

    assert report.parameter_switching_detected is True
    assert report.stability_rating == "unstable"
    assert "parameters_switch_frequently" in report.flags


def test_walk_forward_diagnose_exports_json(tmp_path):
    path = _write_walk_forward_results(
        tmp_path,
        returns=[1, 2, 3, 4, 5],
        profit_factors=[1.4] * 5,
        params=[(5, 30)] * 5,
    )
    export_path = tmp_path / "diagnosis.json"

    report = diagnose_walk_forward(path, export_path=export_path)

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["window_count"] == report.window_count
    assert payload["stability_rating"] == report.stability_rating


def test_walk_forward_diagnose_raises_clear_error_for_empty_or_missing_columns(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        diagnose_walk_forward(empty)

    missing = tmp_path / "missing.csv"
    missing.write_text("window_id,test_total_return_pct\n1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        diagnose_walk_forward(missing)


def test_walk_forward_diagnose_cli_outputs_and_exports(tmp_path):
    path = _write_walk_forward_results(
        tmp_path,
        returns=[1, -1, 2, -2, 3],
        profit_factors=[1.5, 0.5, 2.0, 0.4, 2.5],
        params=[(5, 30), (5, 30), (10, 50), (10, 50), (10, 50)],
    )
    export_path = tmp_path / "diagnosis.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "walk-forward-diagnose",
            "--walk-forward-results",
            str(path),
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
    assert "stability_rating:" in completed.stdout
    assert export_path.exists()


def _write_walk_forward_results(
    tmp_path: Path,
    *,
    returns: list[float],
    profit_factors: list[float],
    params: list[tuple[int, int]],
) -> Path:
    path = tmp_path / "walk_forward_results.csv"
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
        for index, value in enumerate(returns):
            fast, slow = params[index]
            writer.writerow(
                {
                    "window_id": index + 1,
                    "test_start": f"2026-01-{index + 1:02d}T00:00:00+00:00",
                    "test_end": f"2026-01-{index + 1:02d}T23:00:00+00:00",
                    "selected_fast_window": fast,
                    "selected_slow_window": slow,
                    "test_total_return_pct": value,
                    "test_max_drawdown_pct": index + 1,
                    "test_trade_count": index + 2,
                    "test_profit_factor": profit_factors[index],
                }
            )
    return path
