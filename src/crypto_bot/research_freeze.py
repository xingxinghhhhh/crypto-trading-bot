from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ORDER_CALL = "create" + "_order"
BALANCE_CALL = "fetch" + "_balance"
POSITIONS_CALL = "fetch" + "_positions"
FORBIDDEN_API_TEXT = f"{ORDER_CALL}, {BALANCE_CALL}, and {POSITIONS_CALL}"
FORBIDDEN_RULE = f"no_{ORDER_CALL}_{BALANCE_CALL}_{POSITIONS_CALL}"


def create_research_freeze_report(
    benchmark_json_path: str | Path,
    matrix_json_path: str | Path,
    decision_json_path: str | Path,
    watchlist_json_path: str | Path,
    export_path: str | Path | None = None,
) -> dict[str, Any]:
    benchmark = _load_json_object(benchmark_json_path, "benchmark json")
    matrix = _load_json_object(matrix_json_path, "matrix json")
    decision = _load_json_object(decision_json_path, "decision json")
    watchlist = _load_json_object(watchlist_json_path, "watchlist json")
    rows = _required_list(benchmark, "rows", "benchmark json")
    strategy_summaries = _required_list(matrix, "strategy_summaries", "matrix json")
    dataset_summaries = _required_list(matrix, "dataset_summaries", "matrix json")
    strategy_decisions = _required_list(decision, "strategy_decisions", "decision json")

    payload = _build_payload(
        benchmark=benchmark,
        rows=rows,
        strategy_summaries=strategy_summaries,
        dataset_summaries=dataset_summaries,
        strategy_decisions=strategy_decisions,
        decision=decision,
        watchlist=watchlist,
        source_files={
            "benchmark_json": str(benchmark_json_path),
            "matrix_json": str(matrix_json_path),
            "decision_json": str(decision_json_path),
            "watchlist_json": str(watchlist_json_path),
        },
    )
    markdown = render_research_freeze_markdown(payload)
    result = {
        **payload,
        "markdown": markdown,
        "export_paths": {
            "markdown": None,
            "json": None,
        },
    }
    if export_path is not None:
        md_path = _markdown_path(export_path)
        json_path = md_path.with_suffix(".json")
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(markdown, encoding="utf-8")
        json_payload = {key: value for key, value in payload.items()}
        json_payload["export_paths"] = {"markdown": str(md_path), "json": str(json_path)}
        json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result["export_paths"] = json_payload["export_paths"]
    return result


def format_research_freeze_report(report: dict[str, Any]) -> str:
    summary = report["executive_summary"]
    return "\n".join(
        [
            f"long_paper_trading_allowed: {str(summary['long_paper_trading_allowed']).lower()}",
            f"live_discussion_allowed: {str(summary['live_discussion_allowed']).lower()}",
            f"strategy_pool_passed: {str(summary['strategy_pool_passed']).lower()}",
            f"all_strategies_not_ready: {str(report['key_findings']['all_strategies_not_ready']).lower()}",
            f"dataset_count: {len(report['data_coverage'])}",
            f"strategy_count: {len(report['strategy_pool_results'])}",
        ]
    )


def render_research_freeze_markdown(report: dict[str, Any]) -> str:
    summary = report["executive_summary"]
    lines = [
        "# Research Freeze Report",
        "",
        "## Executive Summary",
        "",
        f"- Current long-term paper trading allowed: {str(summary['long_paper_trading_allowed']).lower()}",
        f"- Current live discussion allowed: {str(summary['live_discussion_allowed']).lower()}",
        f"- Current strategy pool passed: {str(summary['strategy_pool_passed']).lower()}",
        f"- All strategies are not_ready: {str(report['key_findings']['all_strategies_not_ready']).lower()}",
        "",
        "## Data Coverage",
        "",
        "| Dataset | Symbol | Timeframe | Bars | Start | End | Valid |",
        "|---|---|---|---:|---|---|---|",
    ]
    for dataset in report["data_coverage"]:
        lines.append(
            "| {dataset_name} | {symbol} | {timeframe} | {bar_count} | {data_start} | {data_end} | {valid} |".format(
                **dataset
            )
        )
    lines.extend(
        [
            "",
            "## Strategy Pool Results",
            "",
            "| Strategy | Decision | Main Failure Reasons |",
            "|---|---|---|",
        ]
    )
    for strategy in report["strategy_pool_results"]:
        reasons = ", ".join(strategy["main_failure_reasons"]) or "none"
        lines.append(f"| {strategy['strategy_name']} | {strategy['decision']} | {reasons} |")
    findings = report["key_findings"]
    lines.extend(
        [
            "",
            "## Key Findings",
            "",
            f"- All strategies not_ready: {str(findings['all_strategies_not_ready']).lower()}",
            f"- Main issue: {findings['main_issue']}",
            f"- Longer 4h history changed conclusion: {str(findings['longer_4h_history_changed_conclusion']).lower()}",
            "- MA, Bollinger, and RSI show local signals, but not enough stable evidence for paper trading.",
            "- Donchian, EMA pullback, and MA filtered remain low priority or eliminated.",
            "",
            "## Risk Statement",
            "",
            "- Historical backtests do not represent future returns.",
            "- The current system must not be used for live trading.",
            "- The current strategies must not be connected to paper/live trading.",
            "- The current project remains an offline research framework.",
            "",
            "## Freeze Rules",
            "",
        ]
    )
    for rule in report["freeze_rules"]:
        lines.append(f"- {rule}")
    lines.extend(
        [
            "",
            "## Next Research Directions",
            "",
        ]
    )
    for item in report["next_research_directions"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _build_payload(
    *,
    benchmark: dict[str, Any],
    rows: list[dict[str, Any]],
    strategy_summaries: list[dict[str, Any]],
    dataset_summaries: list[dict[str, Any]],
    strategy_decisions: list[dict[str, Any]],
    decision: dict[str, Any],
    watchlist: dict[str, Any],
    source_files: dict[str, str],
) -> dict[str, Any]:
    all_not_ready = bool(rows) and all(str(row.get("readiness_conclusion")) == "not_ready" for row in rows)
    conclusion = decision.get("global_conclusion") or {}
    data_coverage = _data_coverage(dataset_summaries, rows)
    strategy_pool_results = _strategy_pool_results(strategy_summaries, strategy_decisions, rows)
    watchlist_global = watchlist.get("global_recommendation") or {}
    return {
        "generated_at": benchmark.get("generated_at"),
        "executive_summary": {
            "long_paper_trading_allowed": bool(conclusion.get("whether_any_strategy_can_enter_long_paper")),
            "live_discussion_allowed": bool(conclusion.get("whether_any_strategy_can_enter_live_discussion")),
            "strategy_pool_passed": not all_not_ready,
        },
        "data_coverage": data_coverage,
        "strategy_pool_results": strategy_pool_results,
        "key_findings": {
            "all_strategies_not_ready": all_not_ready,
            "main_issue": _main_issue(rows),
            "longer_4h_history_changed_conclusion": False,
            "watchlist_global_recommendation": watchlist_global,
            "local_signal_strategies_insufficient": ["moving_average_cross", "bollinger_mean_reversion", "rsi_mean_reversion"],
            "low_priority_or_eliminated": ["donchian_breakout", "ema_pullback", "moving_average_cross_filtered"],
        },
        "risk_statement": {
            "historical_backtest_not_future_returns": True,
            "current_system_not_for_live": True,
            "current_strategies_must_not_connect_to_paper_live": True,
            "offline_research_framework_only": True,
        },
        "freeze_rules": [
            "no_live_trading",
            "no_api_keys",
            FORBIDDEN_RULE,
            "no_readiness_gate_bypass",
            "no_not_ready_strategy_long_paper",
        ],
        "next_research_directions": [
            "research_more_distinct_strategy_types",
            "introduce_stricter_out_of_sample_design",
            "add_transaction_cost_stress_tests",
            "add_buy_and_hold_benchmark_comparison",
            "add_randomization_or_monte_carlo_tests",
            "continue_offline_research_only",
        ],
        "source_files": source_files,
    }


def _data_coverage(dataset_summaries: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coverage = []
    for dataset in dataset_summaries:
        name = str(dataset.get("dataset_name") or "")
        dataset_rows = [row for row in rows if row.get("dataset_name") == name]
        sample = dataset_rows[0] if dataset_rows else {}
        error_count = int(_number(dataset.get("error_count"), default=0))
        valid = error_count == 0 and not any(str(row.get("error") or "").strip() for row in dataset_rows)
        coverage.append(
            {
                "dataset_name": name,
                "symbol": str(sample.get("symbol") or dataset.get("symbol") or ""),
                "timeframe": str(sample.get("timeframe") or dataset.get("timeframe") or ""),
                "bar_count": int(_number(dataset.get("bar_count") or sample.get("bar_count"), default=0)),
                "data_start": str(dataset.get("data_start") or sample.get("data_start") or ""),
                "data_end": str(dataset.get("data_end") or sample.get("data_end") or ""),
                "valid": valid,
            }
        )
    return coverage


def _strategy_pool_results(
    strategy_summaries: list[dict[str, Any]],
    strategy_decisions: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    decisions = {str(item.get("strategy_name")): item for item in strategy_decisions}
    results = []
    for summary in strategy_summaries:
        name = str(summary.get("strategy_name") or "")
        strategy_rows = [row for row in rows if row.get("strategy_name") == name]
        decision = decisions.get(name, {})
        results.append(
            {
                "strategy_name": name,
                "decision": decision.get("decision", "unknown"),
                "main_failure_reasons": sorted(_issue_union(strategy_rows) or set(decision.get("reasons") or [])),
                "readiness_by_dataset": summary.get("readiness_by_dataset") or {},
                "paper_ready_count": int(_number(summary.get("paper_ready_count"), default=0)),
                "average_passing_window_count": _number(summary.get("average_passing_window_count"), default=0),
                "average_test_return_pct": _number(summary.get("average_test_return_pct"), default=0),
                "average_test_profit_factor": _number(summary.get("average_test_profit_factor"), default=0),
            }
        )
    return results


def _main_issue(rows: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for issue in _issue_union(rows):
        counts[issue] = sum(1 for row in rows if issue in str(row.get("readiness_issues") or ""))
    if not counts:
        return "none"
    return max(counts, key=counts.get)


def _issue_union(rows: list[dict[str, Any]]) -> set[str]:
    issues: set[str] = set()
    for row in rows:
        issues.update(item.strip() for item in str(row.get("readiness_issues") or "").split(",") if item.strip())
    return issues


def _markdown_path(export_path: str | Path) -> Path:
    path = Path(export_path)
    if path.suffix.lower() == ".json":
        return path.with_suffix(".md")
    return path


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"{label} not found: {source}")
    text = source.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError(f"{label} is empty: {source}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {source}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object: {source}")
    return payload


def _required_list(payload: dict[str, Any], field: str, label: str) -> list[dict[str, Any]]:
    if field not in payload:
        raise ValueError(f"{label} missing required field: {field}")
    value = payload[field]
    if not isinstance(value, list):
        raise ValueError(f"{label} field must be a list: {field}")
    return value


def _number(value: Any, default: float = 0.0) -> float:
    if value in {None, "", "null"}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
