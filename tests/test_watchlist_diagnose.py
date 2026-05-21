import csv
import json
import subprocess
import sys
from pathlib import Path

from crypto_bot.watchlist_diagnosis import create_watchlist_diagnosis


def test_watchlist_diagnose_reads_reports_and_calculates_overlap(tmp_path):
    benchmark_path, matrix_path, decision_path, reports_dir = _write_watchlist_fixture(tmp_path)

    report = create_watchlist_diagnosis(benchmark_path, matrix_path, decision_path, reports_dir)

    assert report["watchlist_strategies"] == ["bollinger_mean_reversion", "rsi_mean_reversion", "moving_average_cross"]
    assert {item["strategy_name"] for item in report["strategy_diagnostics"]} == set(report["watchlist_strategies"])
    btc_overlap = next(item for item in report["window_overlap_analysis"] if item["dataset_name"] == "BTC_USDT_1h")
    pair = next(item for item in btc_overlap["pairwise_overlaps"] if set(item["strategies"]) == {"bollinger_mean_reversion", "rsi_mean_reversion"})
    assert pair["profit_window_overlap_rate"] == 50.0
    assert pair["loss_window_overlap_rate"] == 0.0
    assert btc_overlap["simultaneous_profit_windows"]
    assert btc_overlap["complementarity_detected"] is True


def test_watchlist_diagnose_warns_when_diagnosis_file_is_missing(tmp_path):
    benchmark_path, matrix_path, decision_path, reports_dir = _write_watchlist_fixture(tmp_path, omit_diagnosis=True)

    report = create_watchlist_diagnosis(benchmark_path, matrix_path, decision_path, reports_dir)

    assert any("missing_walk_forward_diagnosis" in warning for warning in report["warnings"])
    assert report["strategy_diagnostics"]


def test_watchlist_diagnose_exports_json(tmp_path):
    benchmark_path, matrix_path, decision_path, reports_dir = _write_watchlist_fixture(tmp_path)
    export_path = tmp_path / "watchlist.json"

    report = create_watchlist_diagnosis(benchmark_path, matrix_path, decision_path, reports_dir, export_path=export_path)

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["global_recommendation"] == report["global_recommendation"]


def test_watchlist_diagnose_cli_outputs_and_exports(tmp_path):
    benchmark_path, matrix_path, decision_path, reports_dir = _write_watchlist_fixture(tmp_path)
    export_path = tmp_path / "watchlist.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "watchlist-diagnose",
            "--benchmark-json",
            str(benchmark_path),
            "--matrix-json",
            str(matrix_path),
            "--decision-json",
            str(decision_path),
            "--reports-dir",
            str(reports_dir),
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
    assert "watchlist_strategy_count: 3" in completed.stdout
    assert "still_forbid_paper_live: true" in completed.stdout
    assert export_path.exists()


def _write_watchlist_fixture(tmp_path: Path, omit_diagnosis: bool = False):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    stamp = "20260519T170039Z"
    benchmark = {
        "generated_at": stamp,
        "rows": [
            _bench("bollinger_mean_reversion", "BTC_USDT_1h", 10, -0.4, 2.9, "concentrated", True, 70),
            _bench("rsi_mean_reversion", "BTC_USDT_1h", 9, -0.2, 2.0, "concentrated", True, 65),
            _bench("moving_average_cross", "BTC_USDT_1h", 8, 0.1, 1.4, "unstable", True, 55),
            _bench("ema_pullback", "BTC_USDT_1h", 0, -1.0, 0.5, "unstable", True, None),
        ],
    }
    matrix = {
        "strategy_summaries": [
            _summary("bollinger_mean_reversion", 5, 0.3, 2.9),
            _summary("rsi_mean_reversion", 5, 0.1, 1.8),
            _summary("moving_average_cross", 4, -0.4, 1.4),
        ],
        "dataset_summaries": [{"dataset_name": "BTC_USDT_1h"}],
    }
    decision = {
        "strategy_decisions": [
            {"strategy_name": "bollinger_mean_reversion", "decision": "watchlist"},
            {"strategy_name": "rsi_mean_reversion", "decision": "watchlist"},
            {"strategy_name": "moving_average_cross", "decision": "watchlist"},
            {"strategy_name": "ema_pullback", "decision": "eliminate"},
        ],
        "dataset_decisions": [],
        "global_conclusion": {"whether_any_strategy_can_enter_long_paper": False},
    }
    benchmark_path = tmp_path / "benchmark.json"
    matrix_path = tmp_path / "matrix.json"
    decision_path = tmp_path / "decision.json"
    benchmark_path.write_text(json.dumps(benchmark), encoding="utf-8")
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    _write_wf(reports_dir / f"walk_forward_results_benchmark_btc_usdt_1h_bollinger_mean_reversion_{stamp}.csv", [1, 2, -1, 0])
    _write_wf(reports_dir / f"walk_forward_results_benchmark_btc_usdt_1h_rsi_mean_reversion_{stamp}.csv", [2, -1, 1, 0])
    _write_wf(reports_dir / f"walk_forward_results_benchmark_btc_usdt_1h_moving_average_cross_{stamp}.csv", [-1, 2, -2, 1])
    if not omit_diagnosis:
        _write_diag(reports_dir / f"walk_forward_diagnosis_benchmark_btc_usdt_1h_bollinger_mean_reversion_{stamp}.json", "20/2.0", 2, 55, True, 70)
        _write_diag(reports_dir / f"walk_forward_diagnosis_benchmark_btc_usdt_1h_rsi_mean_reversion_{stamp}.json", "rsi=14", 3, 40, True, 65)
        _write_diag(reports_dir / f"walk_forward_diagnosis_benchmark_btc_usdt_1h_moving_average_cross_{stamp}.json", "10/30", 2, 60, False, 55)
    return benchmark_path, matrix_path, decision_path, reports_dir


def _bench(strategy, dataset, passing, avg_return, pf, stability, switching, top):
    return {
        "strategy_name": strategy,
        "dataset_name": dataset,
        "average_test_return_pct": avg_return,
        "average_test_profit_factor": pf,
        "passing_window_count": passing,
        "readiness_issues": "window_performance_concentrated",
        "parameter_switching_detected": switching,
        "stability_rating": stability,
        "top_20pct_profit_contribution_pct": top,
    }


def _summary(strategy, passing, avg_return, pf):
    return {
        "strategy_name": strategy,
        "paper_ready_count": 0,
        "average_passing_window_count": passing,
        "average_test_return_pct": avg_return,
        "average_test_profit_factor": pf,
        "readiness_by_dataset": {"BTC_USDT_1h": "not_ready"},
    }


def _write_wf(path: Path, returns: list[float]):
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
        for index, value in enumerate(returns, start=1):
            writer.writerow(
                {
                    "window_id": index,
                    "test_start": f"2026-01-{index:02d}T00:00:00+00:00",
                    "test_end": f"2026-01-{index:02d}T23:00:00+00:00",
                    "selected_fast_window": 10,
                    "selected_slow_window": 30,
                    "test_total_return_pct": value,
                    "test_max_drawdown_pct": 1,
                    "test_trade_count": 2,
                    "test_profit_factor": 1.2,
                }
            )


def _write_diag(path: Path, most_common, unique, share, switching, top):
    path.write_text(
        json.dumps(
            {
                "number_of_unique_parameter_sets": unique,
                "most_common_parameter": most_common,
                "most_common_parameter_share_pct": share,
                "parameter_switching_detected": switching,
                "top_20pct_profit_contribution_pct": top,
                "percentage_of_profit_from_top_20pct_windows": top,
                "stability_rating": "concentrated",
            }
        ),
        encoding="utf-8",
    )
