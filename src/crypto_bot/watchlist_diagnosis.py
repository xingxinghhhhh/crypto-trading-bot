from __future__ import annotations

import csv
import json
from itertools import combinations
from pathlib import Path
from typing import Any


def create_watchlist_diagnosis(
    benchmark_json_path: str | Path,
    matrix_json_path: str | Path,
    decision_json_path: str | Path,
    reports_dir: str | Path,
    export_path: str | Path | None = None,
) -> dict[str, Any]:
    benchmark = _load_json_object(benchmark_json_path, "benchmark json")
    matrix = _load_json_object(matrix_json_path, "matrix json")
    decision = _load_json_object(decision_json_path, "decision json")
    rows = _required_list(benchmark, "rows", "benchmark json")
    strategy_summaries = _required_list(matrix, "strategy_summaries", "matrix json")
    decision_rows = _required_list(decision, "strategy_decisions", "decision json")
    watchlist = [
        str(item.get("strategy_name"))
        for item in decision_rows
        if item.get("decision") == "watchlist" and item.get("strategy_name")
    ]
    report_dir = Path(reports_dir)
    stamp = str(benchmark.get("generated_at") or _stamp_from_path(benchmark_json_path))
    warnings: list[str] = []
    strategy_diagnostics = [
        _strategy_diagnostic(strategy, rows, strategy_summaries, report_dir, stamp, warnings)
        for strategy in watchlist
    ]
    overlap = _window_overlap_analysis(watchlist, rows, report_dir, stamp, warnings)
    report = {
        "watchlist_strategies": watchlist,
        "strategy_diagnostics": strategy_diagnostics,
        "window_overlap_analysis": overlap,
        "global_recommendation": _global_recommendation(strategy_diagnostics, overlap, decision),
        "warnings": warnings,
        "source_files": {
            "benchmark_json": str(benchmark_json_path),
            "matrix_json": str(matrix_json_path),
            "decision_json": str(decision_json_path),
            "reports_dir": str(reports_dir),
        },
    }
    if export_path is not None:
        path = Path(export_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def format_watchlist_diagnosis(report: dict[str, Any]) -> str:
    recommendation = report["global_recommendation"]
    return "\n".join(
        [
            f"watchlist_strategy_count: {len(report['watchlist_strategies'])}",
            f"watchlist_strategies: {', '.join(report['watchlist_strategies'])}",
            f"worth_researching_combo_strategy: {str(recommendation['worth_researching_combo_strategy']).lower()}",
            f"worth_extending_4h_history_first: {str(recommendation['worth_extending_4h_history_first']).lower()}",
            f"should_abandon_current_strategy_pool: {str(recommendation['should_abandon_current_strategy_pool']).lower()}",
            f"still_forbid_paper_live: {str(recommendation['still_forbid_paper_live']).lower()}",
            f"warning_count: {len(report['warnings'])}",
        ]
    )


def _strategy_diagnostic(
    strategy: str,
    rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    reports_dir: Path,
    stamp: str,
    warnings: list[str],
) -> dict[str, Any]:
    strategy_rows = [row for row in rows if row.get("strategy_name") == strategy]
    summary = next((item for item in summaries if item.get("strategy_name") == strategy), {})
    diagnostics = [_load_diagnosis_for_row(row, reports_dir, stamp, warnings) for row in strategy_rows]
    present_diagnostics = [item for item in diagnostics if item]
    strongest = _strongest_rows(strategy_rows)
    weakest = _weakest_rows(strategy_rows)
    concentration_values = [
        _number(row.get("top_20pct_profit_contribution_pct"), default=float("nan"))
        for row in strategy_rows
    ]
    concentration_values.extend(
        _number(item.get("percentage_of_profit_from_top_20pct_windows"), default=float("nan"))
        for item in present_diagnostics
    )
    concentration_values = [value for value in concentration_values if value == value]
    return {
        "strategy_name": strategy,
        "best_dataset": strongest[0].get("dataset_name") if strongest else None,
        "worst_dataset": weakest[0].get("dataset_name") if weakest else None,
        "average_test_return_pct": _number(summary.get("average_test_return_pct"), default=None),
        "average_test_profit_factor": _number(summary.get("average_test_profit_factor"), default=None),
        "average_passing_window_count": _number(summary.get("average_passing_window_count"), default=None),
        "readiness_issues": sorted(_issue_union(strategy_rows)),
        "parameter_switching_detected": any(_truthy(row.get("parameter_switching_detected")) for row in strategy_rows)
        or any(_truthy(item.get("parameter_switching_detected")) for item in present_diagnostics),
        "stability_rating": _most_common([str(row.get("stability_rating") or "") for row in strategy_rows if row.get("stability_rating")]),
        "parameter_stability_by_dataset": _parameter_stability(strategy_rows, present_diagnostics),
        "top_20pct_profit_contribution_pct": _average(concentration_values),
        "profit_concentration_detected": any(value >= 60 for value in concentration_values),
        "strongest_datasets": [row.get("dataset_name") for row in strongest],
        "weakest_datasets": [row.get("dataset_name") for row in weakest],
        "next_action": _strategy_next_action(strategy_rows, concentration_values, present_diagnostics),
    }


def _window_overlap_analysis(
    watchlist: list[str],
    rows: list[dict[str, Any]],
    reports_dir: Path,
    stamp: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    datasets = sorted({str(row.get("dataset_name")) for row in rows if row.get("strategy_name") in watchlist})
    output = []
    for dataset in datasets:
        loaded: dict[str, list[dict[str, Any]]] = {}
        for strategy in watchlist:
            row = next((item for item in rows if item.get("dataset_name") == dataset and item.get("strategy_name") == strategy), None)
            if row is None:
                continue
            path = _find_report_file(reports_dir, "walk_forward_results", dataset, strategy, stamp, ".csv")
            if path is None:
                warnings.append(f"missing_walk_forward_results:{dataset}:{strategy}")
                continue
            try:
                loaded[strategy] = _load_windows(path)
            except ValueError as exc:
                warnings.append(f"invalid_walk_forward_results:{dataset}:{strategy}:{exc}")
        if len(loaded) < 2:
            continue
        pairwise = [_pair_overlap(left, right, loaded[left], loaded[right]) for left, right in combinations(sorted(loaded), 2)]
        output.append(
            {
                "dataset_name": dataset,
                "simultaneous_profit_windows": _simultaneous_windows(loaded, positive=True),
                "simultaneous_loss_windows": _simultaneous_windows(loaded, positive=False),
                "pairwise_overlaps": pairwise,
                "complementarity_detected": any(item["complementarity_detected"] for item in pairwise),
            }
        )
    return output


def _pair_overlap(left: str, right: str, left_windows: list[dict[str, Any]], right_windows: list[dict[str, Any]]) -> dict[str, Any]:
    left_profit = _window_ids(left_windows, positive=True)
    right_profit = _window_ids(right_windows, positive=True)
    left_loss = _window_ids(left_windows, positive=False)
    right_loss = _window_ids(right_windows, positive=False)
    profit_rate = _overlap_rate(left_profit, right_profit)
    loss_rate = _overlap_rate(left_loss, right_loss)
    return {
        "strategies": [left, right],
        "profit_window_overlap_rate": profit_rate,
        "loss_window_overlap_rate": loss_rate,
        "complementarity_detected": profit_rate < 40 and loss_rate < 50,
    }


def _simultaneous_windows(loaded: dict[str, list[dict[str, Any]]], *, positive: bool) -> list[dict[str, Any]]:
    by_id: dict[int, list[str]] = {}
    for strategy, windows in loaded.items():
        for window in windows:
            value = _number(window.get("test_total_return_pct"))
            if (positive and value > 0) or (not positive and value < 0):
                by_id.setdefault(int(_number(window.get("window_id"))), []).append(strategy)
    return [
        {"window_id": window_id, "strategies": strategies}
        for window_id, strategies in sorted(by_id.items())
        if len(strategies) >= 2
    ]


def _load_windows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("empty walk_forward_results")
        rows = list(reader)
    if not rows:
        raise ValueError("empty walk_forward_results")
    fieldnames = set(rows[0])
    window_id_field = _first_existing(fieldnames, ["window_id", "窗口ID"])
    return_field = _first_existing(fieldnames, ["test_total_return_pct", "测试总收益率百分比"])
    missing = []
    if window_id_field is None:
        missing.append("window_id")
    if return_field is None:
        missing.append("test_total_return_pct")
    if missing:
        raise ValueError("missing columns: " + ", ".join(missing))
    normalized = []
    for row in rows:
        normalized.append(
            {
                **row,
                "window_id": row.get(window_id_field, ""),
                "test_total_return_pct": row.get(return_field, ""),
            }
        )
    return normalized


def _load_diagnosis_for_row(row: dict[str, Any], reports_dir: Path, stamp: str, warnings: list[str]) -> dict[str, Any] | None:
    path = _find_report_file(
        reports_dir,
        "walk_forward_diagnosis",
        str(row.get("dataset_name")),
        str(row.get("strategy_name")),
        stamp,
        ".json",
    )
    if path is None:
        warnings.append(f"missing_walk_forward_diagnosis:{row.get('dataset_name')}:{row.get('strategy_name')}")
        return None
    try:
        return _load_json_object(path, "walk_forward diagnosis")
    except ValueError as exc:
        warnings.append(f"invalid_walk_forward_diagnosis:{row.get('dataset_name')}:{row.get('strategy_name')}:{exc}")
        return None


def _find_report_file(reports_dir: Path, prefix: str, dataset: str, strategy: str, stamp: str, suffix: str) -> Path | None:
    expected = reports_dir / f"{prefix}_benchmark_{_slug(dataset)}_{_slug(strategy)}_{stamp}{suffix}"
    if expected.exists():
        return expected
    matches = sorted(reports_dir.glob(f"{prefix}_benchmark_{_slug(dataset)}_{_slug(strategy)}_*{suffix}"))
    return matches[-1] if matches else None


def _parameter_stability(rows: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_dataset = {str(row.get("dataset_name")): row for row in rows}
    result = []
    for diagnosis in diagnostics:
        source = diagnosis.get("source_files", {}).get("walk_forward_results", "")
        dataset = _dataset_from_source(source, by_dataset)
        result.append(
            {
                "dataset_name": dataset,
                "unique_parameter_sets": diagnosis.get("number_of_unique_parameter_sets"),
                "most_common_parameter": diagnosis.get("most_common_parameter"),
                "most_common_parameter_share_pct": diagnosis.get("most_common_parameter_share_pct"),
                "parameter_switching_detected": diagnosis.get("parameter_switching_detected"),
            }
        )
    return result


def _dataset_from_source(source: str, by_dataset: dict[str, dict[str, Any]]) -> str | None:
    text = str(source).lower()
    for dataset in by_dataset:
        if _slug(dataset) in text:
            return dataset
    return None


def _global_recommendation(
    strategy_diagnostics: list[dict[str, Any]],
    overlap: list[dict[str, Any]],
    decision: dict[str, Any],
) -> dict[str, Any]:
    any_complementarity = any(item.get("complementarity_detected") for item in overlap)
    concentrated_count = sum(1 for item in strategy_diagnostics if item.get("profit_concentration_detected"))
    dataset_decisions = decision.get("dataset_decisions") or []
    return {
        "worth_researching_combo_strategy": bool(any_complementarity and concentrated_count < len(strategy_diagnostics)),
        "worth_extending_4h_history_first": any(item.get("whether_more_history_needed") for item in dataset_decisions),
        "should_abandon_current_strategy_pool": not strategy_diagnostics,
        "still_forbid_paper_live": True,
        "summary": "Watchlist remains research-only; paper/live stays forbidden until readiness gates pass.",
    }


def _strategy_next_action(rows: list[dict[str, Any]], concentration_values: list[float], diagnostics: list[dict[str, Any]]) -> str:
    if _mostly_4h_insufficient(rows):
        return "require_more_history"
    if any(value >= 60 for value in concentration_values):
        return "require_regime_filter_research"
    if any(_truthy(item.get("parameter_switching_detected")) for item in diagnostics):
        return "deprioritize"
    return "keep_watchlist"


def _mostly_4h_insufficient(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    insufficient = [
        row
        for row in rows
        if str(row.get("timeframe") or "").lower() == "4h"
        and "insufficient" in str(row.get("readiness_issues") or "")
    ]
    return len(insufficient) > len(rows) / 2


def _window_ids(windows: list[dict[str, Any]], *, positive: bool) -> set[int]:
    ids: set[int] = set()
    for window in windows:
        value = _number(window.get("test_total_return_pct"))
        if (positive and value > 0) or (not positive and value < 0):
            ids.add(int(_number(window.get("window_id"))))
    return ids


def _overlap_rate(left: set[int], right: set[int]) -> float:
    denominator = min(len(left), len(right))
    if denominator == 0:
        return 0.0
    return round((len(left & right) / denominator) * 100, 10)


def _first_existing(fieldnames: set[str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in fieldnames:
            return candidate
    return None


def _issue_union(rows: list[dict[str, Any]]) -> set[str]:
    issues: set[str] = set()
    for row in rows:
        issues.update(item.strip() for item in str(row.get("readiness_issues") or "").split(",") if item.strip())
    return issues


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


def _most_common(values: list[str]) -> str | None:
    if not values:
        return None
    return max(set(values), key=values.count)


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 10)


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


def _stamp_from_path(path: str | Path) -> str:
    stem = Path(path).stem
    return stem.rsplit("_", 1)[-1]


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value)).strip("_").lower() or "item"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "" or value == "null":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
