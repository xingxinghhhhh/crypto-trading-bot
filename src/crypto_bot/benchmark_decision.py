from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WATCHLIST_PASSING_WINDOW_THRESHOLD = 9
LOW_PASSING_WINDOW_THRESHOLD = 5
READINESS_PASSING_WINDOW_TARGET = 15
INSUFFICIENT_ISSUES = {
    "insufficient_bar_count",
    "insufficient_walk_forward_windows",
    "insufficient_test_trades",
}


def create_benchmark_decision_report(
    benchmark_json_path: str | Path,
    matrix_json_path: str | Path,
    export_path: str | Path | None = None,
) -> dict[str, Any]:
    benchmark = _load_json_object(benchmark_json_path, label="benchmark json")
    matrix = _load_json_object(matrix_json_path, label="matrix json")
    rows = _required(benchmark, "rows", "benchmark json")
    strategy_summaries = _required(matrix, "strategy_summaries", "matrix json")
    dataset_summaries = _required(matrix, "dataset_summaries", "matrix json")
    if not isinstance(rows, list):
        raise ValueError("benchmark json field must be a list: rows")
    if not isinstance(strategy_summaries, list):
        raise ValueError("matrix json field must be a list: strategy_summaries")
    if not isinstance(dataset_summaries, list):
        raise ValueError("matrix json field must be a list: dataset_summaries")

    strategy_decisions = [
        _strategy_decision(summary, [row for row in rows if row.get("strategy_name") == summary.get("strategy_name")])
        for summary in strategy_summaries
    ]
    dataset_decisions = [_dataset_decision(summary, rows) for summary in dataset_summaries]
    report = {
        "strategy_decisions": strategy_decisions,
        "dataset_decisions": dataset_decisions,
        "global_conclusion": _global_conclusion(strategy_decisions),
        "source_files": {
            "benchmark_json": str(benchmark_json_path),
            "matrix_json": str(matrix_json_path),
        },
    }
    if export_path is not None:
        path = Path(export_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def format_benchmark_decision_report(report: dict[str, Any]) -> str:
    conclusion = report["global_conclusion"]
    counts: dict[str, int] = {}
    for item in report["strategy_decisions"]:
        counts[item["decision"]] = counts.get(item["decision"], 0) + 1
    return "\n".join(
        [
            f"whether_any_strategy_can_enter_long_paper: {str(conclusion['whether_any_strategy_can_enter_long_paper']).lower()}",
            f"whether_any_strategy_can_enter_live_discussion: {str(conclusion['whether_any_strategy_can_enter_live_discussion']).lower()}",
            f"recommended_next_phase: {conclusion['recommended_next_phase']}",
            "strategy_decision_counts: "
            + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())),
        ]
    )


def _strategy_decision(summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    name = str(summary.get("strategy_name") or "")
    paper_ready_count = int(_number(summary.get("paper_ready_count")))
    average_pf = _number(summary.get("average_test_profit_factor"), default=0)
    average_return = _number(summary.get("average_test_return_pct"), default=0)
    average_passing = _number(summary.get("average_passing_window_count"), default=0)
    max_passing = max((_number(row.get("passing_window_count"), default=0) for row in rows), default=0)
    concentrated_rows = [row for row in rows if "window_performance_concentrated" in str(row.get("readiness_issues") or "")]
    insufficient_rows = [row for row in rows if _has_insufficient_issue(row)]
    weak_positive_windows = _positive_windows_clearly_weaker(rows)
    reasons: list[str] = []

    if paper_ready_count > 0:
        decision = "continue_research"
        reasons.append("has_paper_ready_dataset")
    elif _continue_research_candidate(average_return, average_pf, max_passing, rows):
        decision = "continue_research"
        reasons.extend(["positive_average_return", "average_profit_factor_above_1_2", "passing_windows_near_gate"])
    elif _watchlist_candidate(average_pf, max_passing, concentrated_rows, rows):
        decision = "watchlist"
        reasons.extend(["average_profit_factor_above_1_2", "some_datasets_near_readiness", "window_performance_concentrated"])
    elif average_pf < 1 or (average_return < 0 and paper_ready_count == 0) or average_passing < LOW_PASSING_WINDOW_THRESHOLD or weak_positive_windows:
        decision = "eliminate"
        if average_pf < 1:
            reasons.append("average_test_profit_factor_below_1")
        if average_return < 0 and paper_ready_count == 0:
            reasons.append("negative_average_test_return_without_paper_ready")
        if average_passing < LOW_PASSING_WINDOW_THRESHOLD:
            reasons.append("low_average_passing_window_count")
        if weak_positive_windows:
            reasons.append("positive_windows_less_than_negative_windows")
        if not reasons:
            reasons.append("does_not_meet_watchlist_or_continue_research_criteria")
    elif _mostly_insufficient_evidence(rows, insufficient_rows):
        decision = "insufficient_evidence"
        reasons.append("mostly_insufficient_evidence")
    else:
        decision = "eliminate"
        reasons.append("does_not_meet_watchlist_or_continue_research_criteria")

    return {
        "strategy_name": name,
        "decision": decision,
        "reasons": reasons,
        "strongest_datasets": _dataset_names(_strongest_rows(rows)),
        "weakest_datasets": _dataset_names(_weakest_rows(rows)),
        "next_action": _next_action(decision),
    }


def _dataset_decision(summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    name = str(summary.get("dataset_name") or "")
    dataset_rows = [row for row in rows if row.get("dataset_name") == name]
    insufficient_count = sum(1 for row in dataset_rows if _has_insufficient_issue(row))
    more_history_needed = insufficient_count > len(dataset_rows) / 2 if dataset_rows else False
    reason = "insufficient_evidence" if more_history_needed else _dataset_reason(summary, dataset_rows)
    return {
        "dataset_name": name,
        "best_strategy": summary.get("best_strategy"),
        "readiness": summary.get("best_strategy_readiness"),
        "whether_more_history_needed": more_history_needed,
        "reason": reason,
    }


def _global_conclusion(strategy_decisions: list[dict[str, Any]]) -> dict[str, Any]:
    any_paper_ready = any("has_paper_ready_dataset" in item["reasons"] for item in strategy_decisions)
    continue_count = sum(1 for item in strategy_decisions if item["decision"] == "continue_research")
    watchlist_count = sum(1 for item in strategy_decisions if item["decision"] == "watchlist")
    if any_paper_ready:
        next_phase = "review_paper_ready_candidates_without_live_discussion"
    elif continue_count or watchlist_count:
        next_phase = "focus_research_on_watchlist_and_collect_more_history"
    else:
        next_phase = "pause_strategy_expansion_and_reassess_research_hypotheses"
    return {
        "whether_any_strategy_can_enter_long_paper": any_paper_ready,
        "whether_any_strategy_can_enter_live_discussion": False,
        "recommended_next_phase": next_phase,
    }


def _continue_research_candidate(average_return: float, average_pf: float, max_passing: float, rows: list[dict[str, Any]]) -> bool:
    return (
        average_return > 0
        and average_pf > 1.2
        and max_passing >= READINESS_PASSING_WINDOW_TARGET - 2
        and len([row for row in rows if _number(row.get("passing_window_count"), default=0) >= WATCHLIST_PASSING_WINDOW_THRESHOLD]) >= 2
    )


def _watchlist_candidate(
    average_pf: float,
    max_passing: float,
    concentrated_rows: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> bool:
    near_rows = [row for row in rows if _number(row.get("passing_window_count"), default=0) >= WATCHLIST_PASSING_WINDOW_THRESHOLD]
    return average_pf > 1.2 and max_passing >= WATCHLIST_PASSING_WINDOW_THRESHOLD and bool(near_rows) and bool(concentrated_rows)


def _mostly_insufficient_evidence(rows: list[dict[str, Any]], insufficient_rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and len(insufficient_rows) > len(rows) / 2


def _has_insufficient_issue(row: dict[str, Any]) -> bool:
    issues = {item.strip() for item in str(row.get("readiness_issues") or "").split(",") if item.strip()}
    return bool(issues & INSUFFICIENT_ISSUES)


def _positive_windows_clearly_weaker(rows: list[dict[str, Any]]) -> bool:
    comparable = [
        row
        for row in rows
        if str(row.get("positive_test_window_count") or "") != "" and str(row.get("negative_test_window_count") or "") != ""
    ]
    if not comparable:
        return False
    return sum(_number(row.get("positive_test_window_count")) for row in comparable) < sum(
        _number(row.get("negative_test_window_count")) for row in comparable
    ) * 0.7


def _strongest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _number(row.get("passing_window_count")),
            _number(row.get("average_test_profit_factor")),
            _number(row.get("average_test_return_pct")),
        ),
        reverse=True,
    )[:3]


def _weakest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _number(row.get("passing_window_count")),
            _number(row.get("average_test_profit_factor")),
            _number(row.get("average_test_return_pct")),
        ),
    )[:3]


def _dataset_names(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("dataset_name") or "") for row in rows if row.get("dataset_name")]


def _next_action(decision: str) -> str:
    return {
        "eliminate": "stop_prioritizing_this_strategy_in_the_next_research_round",
        "watchlist": "keep_for_targeted_research_only_and_investigate_concentration",
        "insufficient_evidence": "collect_more_history_before_strategy_judgment",
        "continue_research": "run_deeper_offline_validation_without_paper_or_live_changes",
    }[decision]


def _dataset_reason(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no_benchmark_rows"
    if summary.get("best_strategy_readiness") == "paper_ready":
        return "has_paper_ready_strategy"
    if any("window_performance_concentrated" in str(row.get("readiness_issues") or "") for row in rows):
        return "window_performance_concentrated"
    return "no_strategy_passed_readiness"


def _load_json_object(path: str | Path, *, label: str) -> dict[str, Any]:
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


def _required(payload: dict[str, Any], field: str, label: str):
    if field not in payload:
        raise ValueError(f"{label} missing required field: {field}")
    return payload[field]


def _number(value: Any, default: float = 0.0) -> float:
    if value in {None, "", "null"}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
