from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


MIN_BAR_COUNT = 8000
MIN_WALK_FORWARD_WINDOWS = 10
MIN_TEST_TRADES = 100
MIN_AVERAGE_TEST_PROFIT_FACTOR = 1.2
MIN_PASSING_WINDOW_RATIO = 0.6


@dataclass(frozen=True)
class StrategyReadinessReport:
    conclusion: str
    data_quality_valid: bool
    bar_count: int
    walk_forward_window_count: int
    test_trade_count_total: int
    average_test_profit_factor: float | None
    average_test_max_drawdown_pct: float | None
    max_drawdown_threshold_pct: float
    passing_window_count: int
    required_passing_window_count: int
    no_losing_trades_window_count: int
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_strategy_readiness(
    *,
    backtest_summary_path: str | Path,
    optimization_summary_path: str | Path,
    walk_forward_summary_path: str | Path,
    walk_forward_results_path: str | Path,
    data_quality_path: str | Path,
    max_drawdown_pct: float,
    export_path: str | Path | None = None,
) -> StrategyReadinessReport:
    paths = {
        "backtest_summary": Path(backtest_summary_path),
        "optimization_summary": Path(optimization_summary_path),
        "walk_forward_summary": Path(walk_forward_summary_path),
        "walk_forward_results": Path(walk_forward_results_path),
        "data_quality": Path(data_quality_path),
    }
    backtest_summary = _load_json(paths["backtest_summary"])
    optimization_summary = _load_json(paths["optimization_summary"])
    walk_forward_summary = _load_json(paths["walk_forward_summary"])
    data_quality = _load_json(paths["data_quality"])
    rows = _load_walk_forward_rows(paths["walk_forward_results"])

    del backtest_summary, optimization_summary

    threshold_pct = _normalize_drawdown_threshold(max_drawdown_pct)
    data_quality_valid = bool(data_quality.get("valid", False))
    bar_count = int(data_quality.get("bar_count") or 0)
    window_count = int(walk_forward_summary.get("window_count") or len(rows))
    test_trade_count_total = sum(row.test_trade_count for row in rows)
    numeric_profit_factors = [row.test_profit_factor for row in rows if row.test_profit_factor is not None]
    average_test_profit_factor = (
        round(sum(numeric_profit_factors) / len(numeric_profit_factors), 10)
        if numeric_profit_factors
        else None
    )
    average_test_max_drawdown_pct = (
        round(sum(row.test_max_drawdown_pct for row in rows) / len(rows), 10)
        if rows
        else None
    )
    no_losing_count = sum(1 for row in rows if row.test_profit_factor_note == "no_losing_trades")
    required_passing_windows = max(2, math.ceil(window_count * MIN_PASSING_WINDOW_RATIO)) if window_count else 0
    passing_window_count = sum(
        1
        for row in rows
        if row.test_profit_factor is not None
        and row.test_profit_factor > MIN_AVERAGE_TEST_PROFIT_FACTOR
        and row.test_max_drawdown_pct < threshold_pct
    )

    issues = _collect_issues(
        data_quality_valid=data_quality_valid,
        bar_count=bar_count,
        window_count=window_count,
        test_trade_count_total=test_trade_count_total,
        average_test_profit_factor=average_test_profit_factor,
        average_test_max_drawdown_pct=average_test_max_drawdown_pct,
        threshold_pct=threshold_pct,
        passing_window_count=passing_window_count,
        required_passing_windows=required_passing_windows,
    )
    warnings = _collect_warnings(
        no_losing_count=no_losing_count,
        numeric_profit_factor_count=len(numeric_profit_factors),
        row_count=len(rows),
    )
    conclusion = _conclusion(issues, warnings)
    report = StrategyReadinessReport(
        conclusion=conclusion,
        data_quality_valid=data_quality_valid,
        bar_count=bar_count,
        walk_forward_window_count=window_count,
        test_trade_count_total=test_trade_count_total,
        average_test_profit_factor=average_test_profit_factor,
        average_test_max_drawdown_pct=average_test_max_drawdown_pct,
        max_drawdown_threshold_pct=threshold_pct,
        passing_window_count=passing_window_count,
        required_passing_window_count=required_passing_windows,
        no_losing_trades_window_count=no_losing_count,
        issues=issues,
        warnings=warnings,
        source_files={name: str(path) for name, path in paths.items()},
    )
    _export_report(report, export_path)
    return report


def evaluate_latest_strategy_readiness(
    reports_dir: str | Path,
    max_drawdown_pct: float,
    export_path: str | Path | None = None,
) -> StrategyReadinessReport:
    reports_path = Path(reports_dir)
    return evaluate_strategy_readiness(
        backtest_summary_path=_latest(reports_path, "backtest_summary_*.json"),
        optimization_summary_path=_latest(reports_path, "optimization_summary_*.json"),
        walk_forward_summary_path=_latest(reports_path, "walk_forward_summary_*.json"),
        walk_forward_results_path=_latest(reports_path, "walk_forward_results_*.csv"),
        data_quality_path=_latest(reports_path, "data_quality*.json"),
        max_drawdown_pct=max_drawdown_pct,
        export_path=export_path,
    )


def format_strategy_readiness_report(report: StrategyReadinessReport) -> str:
    return "\n".join(
        [
            f"conclusion: {report.conclusion}",
            f"data_quality_valid: {str(report.data_quality_valid).lower()}",
            f"bar_count: {report.bar_count}",
            f"walk_forward_window_count: {report.walk_forward_window_count}",
            f"test_trade_count_total: {report.test_trade_count_total}",
            f"average_test_profit_factor: {_format_nullable(report.average_test_profit_factor)}",
            f"no_losing_trades_window_count: {report.no_losing_trades_window_count}",
            f"average_test_max_drawdown_pct: {_format_nullable(report.average_test_max_drawdown_pct)}",
            f"max_drawdown_threshold_pct: {report.max_drawdown_threshold_pct}",
            f"passing_window_count: {report.passing_window_count}",
            f"required_passing_window_count: {report.required_passing_window_count}",
            f"issues: {_format_list(report.issues)}",
            f"warnings: {_format_list(report.warnings)}",
        ]
    )


@dataclass(frozen=True)
class _WalkForwardReadinessRow:
    test_trade_count: int
    test_profit_factor: float | None
    test_profit_factor_note: str
    test_max_drawdown_pct: float


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Report file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_walk_forward_rows(path: Path) -> list[_WalkForwardReadinessRow]:
    if not path.exists():
        raise FileNotFoundError(f"Report file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        _WalkForwardReadinessRow(
            test_trade_count=int(float(_value(row, "test_trade_count", "测试交易次数") or 0)),
            test_profit_factor=_parse_optional_float(_value(row, "test_profit_factor", "测试盈亏因子")),
            test_profit_factor_note=str(_value(row, "test_profit_factor_note", "测试盈亏因子说明") or ""),
            test_max_drawdown_pct=float(_value(row, "test_max_drawdown_pct", "测试最大回撤百分比") or 0),
        )
        for row in rows
    ]


def _value(row: dict[str, str], english: str, chinese: str) -> str | None:
    if english in row:
        return row.get(english)
    if chinese in row:
        return row.get(chinese)
    actual_chinese = _ACTUAL_CHINESE_ALIASES.get(english)
    return row.get(actual_chinese) if actual_chinese else None


_ACTUAL_CHINESE_ALIASES = {
    "test_trade_count": "\u6d4b\u8bd5\u4ea4\u6613\u6b21\u6570",
    "test_profit_factor": "\u6d4b\u8bd5\u76c8\u4e8f\u56e0\u5b50",
    "test_profit_factor_note": "\u6d4b\u8bd5\u76c8\u4e8f\u56e0\u5b50\u8bf4\u660e",
    "test_max_drawdown_pct": "\u6d4b\u8bd5\u6700\u5927\u56de\u64a4\u767e\u5206\u6bd4",
}


def _parse_optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"null", "none", "nan"}:
        return None
    return float(text)


def _normalize_drawdown_threshold(max_drawdown_pct: float) -> float:
    return max_drawdown_pct * 100 if 0 < max_drawdown_pct <= 1 else max_drawdown_pct


def _collect_issues(
    *,
    data_quality_valid: bool,
    bar_count: int,
    window_count: int,
    test_trade_count_total: int,
    average_test_profit_factor: float | None,
    average_test_max_drawdown_pct: float | None,
    threshold_pct: float,
    passing_window_count: int,
    required_passing_windows: int,
) -> list[str]:
    issues: list[str] = []
    if not data_quality_valid:
        issues.append("data_quality_invalid")
    if bar_count < MIN_BAR_COUNT:
        issues.append("insufficient_bar_count")
    if window_count < MIN_WALK_FORWARD_WINDOWS:
        issues.append("insufficient_walk_forward_windows")
    if test_trade_count_total < MIN_TEST_TRADES:
        issues.append("insufficient_test_trades")
    if average_test_profit_factor is None:
        issues.append("missing_numeric_test_profit_factor")
    elif average_test_profit_factor <= MIN_AVERAGE_TEST_PROFIT_FACTOR:
        issues.append("weak_average_test_profit_factor")
    if average_test_max_drawdown_pct is None:
        issues.append("missing_test_drawdown")
    elif average_test_max_drawdown_pct >= threshold_pct:
        issues.append("excessive_average_test_drawdown")
    if required_passing_windows and passing_window_count < required_passing_windows:
        issues.append("window_performance_concentrated")
    return issues


def _collect_warnings(
    *,
    no_losing_count: int,
    numeric_profit_factor_count: int,
    row_count: int,
) -> list[str]:
    warnings: list[str] = []
    if no_losing_count:
        warnings.append("no_losing_trades_present")
    if numeric_profit_factor_count < row_count:
        warnings.append("some_profit_factors_not_numeric")
    return warnings


def _conclusion(issues: list[str], warnings: list[str]) -> str:
    if issues:
        return "not_ready"
    if warnings:
        return "review_required"
    return "paper_ready"


def _latest(reports_path: Path, pattern: str) -> Path:
    matches = sorted(reports_path.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not matches:
        raise FileNotFoundError(f"No report matching {pattern} in {reports_path}")
    return matches[0]


def _format_nullable(value: float | None) -> str:
    return "null" if value is None else str(value)


def _format_list(values: list[str]) -> str:
    return "none" if not values else ", ".join(values)


def _export_report(report: StrategyReadinessReport, export_path: str | Path | None) -> None:
    if export_path is None:
        return
    path = Path(export_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
