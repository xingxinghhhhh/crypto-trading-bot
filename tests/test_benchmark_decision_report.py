import json
import subprocess
import sys
from pathlib import Path

import pytest

from crypto_bot.benchmark_decision import create_benchmark_decision_report


def test_benchmark_decision_report_layers_strategies_and_datasets(tmp_path):
    benchmark_path, matrix_path = _write_reports(tmp_path)

    report = create_benchmark_decision_report(benchmark_path, matrix_path)

    decisions = {item["strategy_name"]: item["decision"] for item in report["strategy_decisions"]}
    assert decisions["ema_pullback"] == "eliminate"
    assert decisions["donchian_breakout"] == "eliminate"
    assert decisions["moving_average_cross_filtered"] == "eliminate"
    assert decisions["bollinger_mean_reversion"] == "watchlist"
    assert decisions["rsi_mean_reversion"] == "watchlist"
    assert decisions["moving_average_cross"] == "watchlist"

    dataset_decisions = {item["dataset_name"]: item for item in report["dataset_decisions"]}
    assert dataset_decisions["BTC_USDT_4h"]["whether_more_history_needed"] is True
    assert dataset_decisions["BTC_USDT_4h"]["reason"] == "insufficient_evidence"
    assert dataset_decisions["BTC_USDT_1h"]["whether_more_history_needed"] is False
    assert report["global_conclusion"]["whether_any_strategy_can_enter_long_paper"] is False
    assert report["global_conclusion"]["whether_any_strategy_can_enter_live_discussion"] is False


def test_benchmark_decision_report_exports_json(tmp_path):
    benchmark_path, matrix_path = _write_reports(tmp_path)
    export_path = tmp_path / "decision.json"

    report = create_benchmark_decision_report(benchmark_path, matrix_path, export_path=export_path)

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["global_conclusion"] == report["global_conclusion"]
    assert payload["strategy_decisions"][0]["strategy_name"]


def test_benchmark_decision_report_clear_errors_for_empty_or_missing_fields(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    matrix = tmp_path / "matrix.json"
    matrix.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        create_benchmark_decision_report(empty, matrix)

    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="benchmark json missing required field: rows"):
        create_benchmark_decision_report(bad, matrix)


def test_benchmark_decision_report_cli_outputs_and_exports(tmp_path):
    benchmark_path, matrix_path = _write_reports(tmp_path)
    export_path = tmp_path / "decision.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "benchmark-decision-report",
            "--benchmark-json",
            str(benchmark_path),
            "--matrix-json",
            str(matrix_path),
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
    assert "whether_any_strategy_can_enter_long_paper: false" in completed.stdout
    assert export_path.exists()


def _write_reports(tmp_path: Path) -> tuple[Path, Path]:
    rows = [
        _row("bollinger_mean_reversion", "BTC_USDT_1h", passing=10, avg_return=-0.45, pf=2.9, issues="window_performance_concentrated", pos=9, neg=15),
        _row("bollinger_mean_reversion", "BTC_USDT_4h", passing=1, avg_return=-0.18, pf=7.5, issues="insufficient_bar_count,insufficient_walk_forward_windows,insufficient_test_trades", pos=1, neg=1),
        _row("rsi_mean_reversion", "ETH_USDT_1h", passing=11, avg_return=-0.48, pf=1.3, issues="window_performance_concentrated", pos=10, neg=14),
        _row("rsi_mean_reversion", "SOL_USDT_1h", passing=11, avg_return=-0.39, pf=3.5, issues="window_performance_concentrated", pos=10, neg=14),
        _row("moving_average_cross", "BTC_USDT_1h", passing=10, avg_return=0.07, pf=2.0, issues="window_performance_concentrated", pos=12, neg=12),
        _row("moving_average_cross", "SOL_USDT_1h", passing=9, avg_return=-0.21, pf=1.47, issues="window_performance_concentrated", pos=10, neg=14),
        _row("moving_average_cross_filtered", "BTC_USDT_1h", passing=5, avg_return=-0.1, pf=1.06, issues="insufficient_test_trades,weak_average_test_profit_factor,window_performance_concentrated", pos=10, neg=10),
        _row("donchian_breakout", "BTC_USDT_1h", passing=4, avg_return=-0.79, pf=0.60, issues="weak_average_test_profit_factor,window_performance_concentrated", pos=4, neg=20),
        _row("ema_pullback", "BTC_USDT_1h", passing=0, avg_return=-0.87, pf=0.58, issues="weak_average_test_profit_factor,window_performance_concentrated", pos=0, neg=24),
    ]
    matrix = {
        "strategy_summaries": [
            _summary("bollinger_mean_reversion", pf=10.39, avg_return=0.30, passing=5.0, datasets={"BTC_USDT_1h": "not_ready", "BTC_USDT_4h": "not_ready"}),
            _summary("rsi_mean_reversion", pf=1.63, avg_return=0.12, passing=4.66, datasets={"ETH_USDT_1h": "not_ready", "SOL_USDT_1h": "not_ready"}),
            _summary("moving_average_cross", pf=1.43, avg_return=-0.40, passing=4.66, datasets={"BTC_USDT_1h": "not_ready", "SOL_USDT_1h": "not_ready"}),
            _summary("moving_average_cross_filtered", pf=1.99, avg_return=0.11, passing=3.0, datasets={"BTC_USDT_1h": "not_ready"}),
            _summary("donchian_breakout", pf=1.06, avg_return=0.16, passing=3.0, datasets={"BTC_USDT_1h": "not_ready"}),
            _summary("ema_pullback", pf=0.62, avg_return=-0.79, passing=2.33, datasets={"BTC_USDT_1h": "not_ready"}),
        ],
        "dataset_summaries": [
            {
                "dataset_name": "BTC_USDT_1h",
                "best_strategy": "bollinger_mean_reversion",
                "best_strategy_readiness": "not_ready",
                "bar_count": 20784,
                "error_count": 0,
            },
            {
                "dataset_name": "BTC_USDT_4h",
                "best_strategy": "bollinger_mean_reversion",
                "best_strategy_readiness": "not_ready",
                "bar_count": 5106,
                "error_count": 0,
            },
        ],
    }
    benchmark_path = tmp_path / "benchmark.json"
    matrix_path = tmp_path / "matrix.json"
    benchmark_path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    return benchmark_path, matrix_path


def _row(strategy: str, dataset: str, *, passing: int, avg_return: float, pf: float, issues: str, pos: int, neg: int) -> dict:
    return {
        "strategy_name": strategy,
        "dataset_name": dataset,
        "readiness_conclusion": "not_ready",
        "passing_window_count": passing,
        "average_test_return_pct": avg_return,
        "average_test_profit_factor": pf,
        "positive_test_window_count": pos,
        "negative_test_window_count": neg,
        "readiness_issues": issues,
    }


def _summary(strategy: str, *, pf: float, avg_return: float, passing: float, datasets: dict) -> dict:
    return {
        "strategy_name": strategy,
        "paper_ready_count": 0,
        "average_passing_window_count": passing,
        "average_test_return_pct": avg_return,
        "average_test_profit_factor": pf,
        "dataset_count": len(datasets),
        "error_count": 0,
        "single_dataset_effective": False,
        "readiness_by_dataset": datasets,
    }
