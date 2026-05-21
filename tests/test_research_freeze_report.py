import json
import subprocess
import sys
from pathlib import Path

from crypto_bot.research_freeze import create_research_freeze_report


def test_research_freeze_report_exports_markdown_and_json(tmp_path):
    benchmark_path, matrix_path, decision_path, watchlist_path = _write_freeze_fixture(tmp_path)
    export_path = tmp_path / "research_freeze_report_20260520T085132Z.md"

    report = create_research_freeze_report(
        benchmark_path,
        matrix_path,
        decision_path,
        watchlist_path,
        export_path=export_path,
    )

    json_path = export_path.with_suffix(".json")
    assert export_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = export_path.read_text(encoding="utf-8")
    assert payload["executive_summary"]["long_paper_trading_allowed"] is False
    assert payload["executive_summary"]["live_discussion_allowed"] is False
    assert payload["executive_summary"]["strategy_pool_passed"] is False
    assert payload["key_findings"]["all_strategies_not_ready"] is True
    assert "Current long-term paper trading allowed: false" in markdown
    assert "Current live discussion allowed: false" in markdown
    assert "All strategies are not_ready" in markdown
    assert report["export_paths"]["markdown"] == str(export_path)
    assert report["export_paths"]["json"] == str(json_path)


def test_research_freeze_report_contains_freeze_rules(tmp_path):
    benchmark_path, matrix_path, decision_path, watchlist_path = _write_freeze_fixture(tmp_path)

    report = create_research_freeze_report(benchmark_path, matrix_path, decision_path, watchlist_path)

    rules = report["freeze_rules"]
    assert "no_live_trading" in rules
    assert "no_api_keys" in rules
    assert "no_create_order_fetch_balance_fetch_positions" in rules
    assert "no_readiness_gate_bypass" in rules
    assert "no_not_ready_strategy_long_paper" in rules
    assert report["risk_statement"]["current_strategies_must_not_connect_to_paper_live"] is True


def test_research_freeze_report_cli_outputs_both_paths(tmp_path):
    benchmark_path, matrix_path, decision_path, watchlist_path = _write_freeze_fixture(tmp_path)
    export_path = tmp_path / "freeze.md"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "research-freeze-report",
            "--benchmark-json",
            str(benchmark_path),
            "--matrix-json",
            str(matrix_path),
            "--decision-json",
            str(decision_path),
            "--watchlist-json",
            str(watchlist_path),
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
    assert "long_paper_trading_allowed: false" in completed.stdout
    assert "live_discussion_allowed: false" in completed.stdout
    assert "strategy_pool_passed: false" in completed.stdout
    assert "exported_research_freeze_markdown:" in completed.stdout
    assert "exported_research_freeze_json:" in completed.stdout
    assert export_path.exists()
    assert export_path.with_suffix(".json").exists()


def _write_freeze_fixture(tmp_path: Path):
    strategies = [
        ("moving_average_cross", "watchlist", "window_performance_concentrated"),
        ("bollinger_mean_reversion", "watchlist", "window_performance_concentrated"),
        ("rsi_mean_reversion", "watchlist", "window_performance_concentrated"),
        ("donchian_breakout", "eliminate", "insufficient_test_trades,window_performance_concentrated"),
        ("ema_pullback", "eliminate", "weak_average_test_profit_factor,window_performance_concentrated"),
        ("moving_average_cross_filtered", "eliminate", "insufficient_test_trades,weak_average_test_profit_factor"),
    ]
    datasets = [
        ("BTC_USDT_1h", "BTC/USDT", "1h", 20784, "2024-01-01T00:00:00+00:00", "2026-05-15T23:00:00+00:00"),
        ("BTC_USDT_4h", "BTC/USDT", "4h", 13574, "2020-02-19T16:00:00+00:00", "2026-04-30T20:00:00+00:00"),
    ]
    rows = []
    for dataset_name, symbol, timeframe, bar_count, start, end in datasets:
        for strategy, _decision, issue in strategies:
            rows.append(
                {
                    "strategy_name": strategy,
                    "dataset_name": dataset_name,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bar_count": bar_count,
                    "data_start": start,
                    "data_end": end,
                    "readiness_conclusion": "not_ready",
                    "readiness_issues": issue,
                    "passing_window_count": 6,
                    "average_test_return_pct": -0.1,
                    "average_test_profit_factor": 1.1,
                    "error": "",
                }
            )
    matrix = {
        "strategy_summaries": [
            {
                "strategy_name": strategy,
                "paper_ready_count": 0,
                "average_passing_window_count": 6,
                "average_test_return_pct": -0.1,
                "average_test_profit_factor": 1.1,
                "readiness_by_dataset": {dataset[0]: "not_ready" for dataset in datasets},
            }
            for strategy, _decision, _issue in strategies
        ],
        "dataset_summaries": [
            {
                "dataset_name": dataset_name,
                "best_strategy": "moving_average_cross",
                "best_strategy_readiness": "not_ready",
                "bar_count": bar_count,
                "data_start": start,
                "data_end": end,
                "error_count": 0,
            }
            for dataset_name, _symbol, _timeframe, bar_count, start, end in datasets
        ],
    }
    decision = {
        "strategy_decisions": [
            {
                "strategy_name": strategy,
                "decision": decision_name,
                "reasons": [issue],
                "next_action": "keep_for_targeted_research_only_and_investigate_concentration",
            }
            for strategy, decision_name, issue in strategies
        ],
        "dataset_decisions": [],
        "global_conclusion": {
            "whether_any_strategy_can_enter_long_paper": False,
            "whether_any_strategy_can_enter_live_discussion": False,
            "recommended_next_phase": "continue_offline_research_only",
        },
    }
    watchlist = {
        "watchlist_strategies": ["bollinger_mean_reversion", "moving_average_cross", "rsi_mean_reversion"],
        "strategy_diagnostics": [
            {
                "strategy_name": "bollinger_mean_reversion",
                "next_action": "require_regime_filter_research",
                "stability_rating": "concentrated",
            }
        ],
        "global_recommendation": {
            "worth_researching_combo_strategy": False,
            "worth_extending_4h_history_first": False,
            "still_forbid_paper_live": True,
        },
    }
    benchmark_path = tmp_path / "benchmark.json"
    matrix_path = tmp_path / "matrix.json"
    decision_path = tmp_path / "decision.json"
    watchlist_path = tmp_path / "watchlist.json"
    benchmark_path.write_text(json.dumps({"generated_at": "20260520T085132Z", "rows": rows}), encoding="utf-8")
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    watchlist_path.write_text(json.dumps(watchlist), encoding="utf-8")
    return benchmark_path, matrix_path, decision_path, watchlist_path
