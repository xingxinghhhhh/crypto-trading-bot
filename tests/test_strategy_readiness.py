import csv
import json
import subprocess
import sys
from pathlib import Path

from crypto_bot.strategy_readiness import evaluate_strategy_readiness


def test_strategy_readiness_marks_paper_ready_when_all_gates_pass(tmp_path):
    paths = _write_reports(tmp_path, window_count=10, bar_count=9000)

    report = evaluate_strategy_readiness(**paths, max_drawdown_pct=10)

    assert report.conclusion == "paper_ready"
    assert report.data_quality_valid is True
    assert report.bar_count == 9000
    assert report.walk_forward_window_count == 10
    assert report.test_trade_count_total == 150
    assert report.average_test_profit_factor == 1.5
    assert report.average_test_max_drawdown_pct == 4.0
    assert report.no_losing_trades_window_count == 0
    assert report.issues == []


def test_strategy_readiness_requires_review_for_no_losing_windows(tmp_path):
    paths = _write_reports(
        tmp_path,
        window_count=10,
        bar_count=9000,
        profit_factors=[1.6] * 9 + [None],
        profit_factor_notes=["calculated"] * 9 + ["no_losing_trades"],
    )

    report = evaluate_strategy_readiness(**paths, max_drawdown_pct=10)

    assert report.conclusion == "review_required"
    assert report.no_losing_trades_window_count == 1
    assert "no_losing_trades_present" in report.warnings


def test_strategy_readiness_marks_not_ready_for_hard_gate_failures(tmp_path):
    paths = _write_reports(tmp_path, window_count=1, bar_count=1500, trades_per_window=[4])

    report = evaluate_strategy_readiness(**paths, max_drawdown_pct=10)

    assert report.conclusion == "not_ready"
    assert "insufficient_bar_count" in report.issues
    assert "insufficient_walk_forward_windows" in report.issues
    assert "insufficient_test_trades" in report.issues


def test_strategy_readiness_rejects_single_good_window_concentration(tmp_path):
    paths = _write_reports(
        tmp_path,
        window_count=10,
        bar_count=9000,
        profit_factors=[2.0] + [0.8] * 9,
        drawdowns=[3.0] * 10,
        trades_per_window=[15] * 10,
    )

    report = evaluate_strategy_readiness(**paths, max_drawdown_pct=10)

    assert report.conclusion == "not_ready"
    assert report.passing_window_count == 1
    assert "window_performance_concentrated" in report.issues


def test_strategy_readiness_cli_outputs_report(tmp_path):
    paths = _write_reports(tmp_path, window_count=10, bar_count=9000)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "strategy-readiness",
            "--backtest-summary",
            str(paths["backtest_summary_path"]),
            "--optimization-summary",
            str(paths["optimization_summary_path"]),
            "--walk-forward-summary",
            str(paths["walk_forward_summary_path"]),
            "--walk-forward-results",
            str(paths["walk_forward_results_path"]),
            "--data-quality",
            str(paths["data_quality_path"]),
            "--max-drawdown-pct",
            "10",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "conclusion: paper_ready" in completed.stdout
    assert "test_trade_count_total: 150" in completed.stdout


def _write_reports(
    tmp_path: Path,
    *,
    window_count: int,
    bar_count: int,
    profit_factors: list[float | None] | None = None,
    profit_factor_notes: list[str] | None = None,
    drawdowns: list[float] | None = None,
    trades_per_window: list[int] | None = None,
) -> dict:
    backtest_summary_path = tmp_path / "backtest_summary.json"
    optimization_summary_path = tmp_path / "optimization_summary.json"
    walk_forward_summary_path = tmp_path / "walk_forward_summary.json"
    walk_forward_results_path = tmp_path / "walk_forward_results.csv"
    data_quality_path = tmp_path / "data_quality.json"

    profit_factors = profit_factors or [1.5] * window_count
    profit_factor_notes = profit_factor_notes or ["calculated"] * window_count
    drawdowns = drawdowns or [4.0] * window_count
    trades_per_window = trades_per_window or [15] * window_count

    backtest_summary_path.write_text(
        json.dumps({"profit_factor": 1.4, "profit_factor_note": "calculated"}),
        encoding="utf-8",
    )
    optimization_summary_path.write_text(
        json.dumps({"result_count": 16, "best_parameters": {"score": 1.0}}),
        encoding="utf-8",
    )
    walk_forward_summary_path.write_text(
        json.dumps({"window_count": window_count}),
        encoding="utf-8",
    )
    data_quality_path.write_text(
        json.dumps({"valid": True, "bar_count": bar_count}),
        encoding="utf-8",
    )

    with walk_forward_results_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "window_id",
                "test_trade_count",
                "test_profit_factor",
                "test_profit_factor_note",
                "test_max_drawdown_pct",
            ],
        )
        writer.writeheader()
        for index in range(window_count):
            profit_factor = profit_factors[index]
            writer.writerow(
                {
                    "window_id": index + 1,
                    "test_trade_count": trades_per_window[index],
                    "test_profit_factor": "null" if profit_factor is None else profit_factor,
                    "test_profit_factor_note": profit_factor_notes[index],
                    "test_max_drawdown_pct": drawdowns[index],
                }
            )

    return {
        "backtest_summary_path": backtest_summary_path,
        "optimization_summary_path": optimization_summary_path,
        "walk_forward_summary_path": walk_forward_summary_path,
        "walk_forward_results_path": walk_forward_results_path,
        "data_quality_path": data_quality_path,
    }
