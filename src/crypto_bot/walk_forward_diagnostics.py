from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median, stdev
from typing import Any


@dataclass(frozen=True)
class DiagnosticWindow:
    window_id: int
    test_start: str
    test_end: str
    selected_fast_window: int
    selected_slow_window: int
    selected_parameter_label: str
    test_return_pct: float
    test_max_drawdown_pct: float
    test_trade_count: int
    test_profit_factor: float | None


@dataclass(frozen=True)
class WalkForwardDiagnosis:
    window_count: int
    positive_test_window_count: int
    negative_test_window_count: int
    zero_or_flat_window_count: int
    average_test_return_pct: float | None
    median_test_return_pct: float | None
    standard_deviation_test_return_pct: float | None
    worst_test_return_pct: float | None
    best_test_return_pct: float | None
    percentage_of_profit_from_top_20pct_windows: float | None
    selected_parameter_distribution: dict[str, int]
    most_common_parameter: str | None
    most_common_parameter_share_pct: float | None
    number_of_unique_parameter_sets: int
    parameter_switching_detected: bool
    worst_windows: list[DiagnosticWindow]
    best_windows: list[DiagnosticWindow]
    stability_rating: str
    flags: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    source_files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def diagnose_walk_forward(
    walk_forward_results_path: str | Path,
    equity_curve_path: str | Path | None = None,
    trades_path: str | Path | None = None,
    data_quality_path: str | Path | None = None,
    export_path: str | Path | None = None,
) -> WalkForwardDiagnosis:
    path = Path(walk_forward_results_path)
    windows = _load_windows(path)
    returns = [window.test_return_pct for window in windows]
    positive_count = sum(1 for value in returns if value > 0)
    negative_count = sum(1 for value in returns if value < 0)
    flat_count = len(returns) - positive_count - negative_count
    distribution = Counter(window.selected_parameter_label for window in windows)
    most_common_parameter, most_common_count = distribution.most_common(1)[0]
    most_common_share = round((most_common_count / len(windows)) * 100, 10)
    concentration_pct = _profit_concentration_pct(returns)
    parameter_switching = most_common_share < 50 or len(distribution) > max(3, len(windows) // 3)
    flags = _flags(
        windows=windows,
        positive_count=positive_count,
        concentration_pct=concentration_pct,
        parameter_switching=parameter_switching,
    )
    diagnosis = WalkForwardDiagnosis(
        window_count=len(windows),
        positive_test_window_count=positive_count,
        negative_test_window_count=negative_count,
        zero_or_flat_window_count=flat_count,
        average_test_return_pct=_average(returns),
        median_test_return_pct=round(float(median(returns)), 10),
        standard_deviation_test_return_pct=round(float(stdev(returns)), 10) if len(returns) > 1 else 0.0,
        worst_test_return_pct=min(returns),
        best_test_return_pct=max(returns),
        percentage_of_profit_from_top_20pct_windows=concentration_pct,
        selected_parameter_distribution=dict(distribution),
        most_common_parameter=most_common_parameter,
        most_common_parameter_share_pct=most_common_share,
        number_of_unique_parameter_sets=len(distribution),
        parameter_switching_detected=parameter_switching,
        worst_windows=sorted(windows, key=lambda window: window.test_return_pct)[:5],
        best_windows=sorted(windows, key=lambda window: window.test_return_pct, reverse=True)[:5],
        stability_rating=_rating(windows, flags, positive_count),
        flags=flags,
        recommendations=_recommendations(flags, windows),
        source_files=_source_files(path, equity_curve_path, trades_path, data_quality_path),
    )
    _export(diagnosis, export_path)
    return diagnosis


def format_walk_forward_diagnosis(report: WalkForwardDiagnosis) -> str:
    return "\n".join(
        [
            f"stability_rating: {report.stability_rating}",
            f"window_count: {report.window_count}",
            f"positive_test_window_count: {report.positive_test_window_count}",
            f"negative_test_window_count: {report.negative_test_window_count}",
            f"zero_or_flat_window_count: {report.zero_or_flat_window_count}",
            f"average_test_return_pct: {_nullable(report.average_test_return_pct)}",
            f"median_test_return_pct: {_nullable(report.median_test_return_pct)}",
            f"standard_deviation_test_return_pct: {_nullable(report.standard_deviation_test_return_pct)}",
            f"worst_test_return_pct: {_nullable(report.worst_test_return_pct)}",
            f"best_test_return_pct: {_nullable(report.best_test_return_pct)}",
            "percentage_of_profit_from_top_20pct_windows: "
            f"{_nullable(report.percentage_of_profit_from_top_20pct_windows)}",
            f"most_common_parameter: {_nullable(report.most_common_parameter)}",
            f"most_common_parameter_share_pct: {_nullable(report.most_common_parameter_share_pct)}",
            f"number_of_unique_parameter_sets: {report.number_of_unique_parameter_sets}",
            f"parameter_switching_detected: {str(report.parameter_switching_detected).lower()}",
            f"flags: {_list(report.flags)}",
            f"recommendations: {_list(report.recommendations)}",
        ]
    )


def _load_windows(path: Path) -> list[DiagnosticWindow]:
    if not path.exists():
        raise FileNotFoundError(f"walk_forward_results not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"walk_forward_results is empty: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"walk_forward_results is empty: {path}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"walk_forward_results is empty: {path}")
    missing = _missing_required_columns(reader.fieldnames)
    if missing:
        raise ValueError(f"walk_forward_results missing required columns: {', '.join(missing)}")
    return [_window_from_row(row) for row in rows]


def _missing_required_columns(fieldnames: list[str]) -> list[str]:
    missing = []
    for key in _REQUIRED_COLUMN_KEYS:
        english, chinese = _COLUMN_ALIASES[key]
        if english not in fieldnames and chinese not in fieldnames:
            missing.append(english)
    return missing


def _window_from_row(row: dict[str, str]) -> DiagnosticWindow:
    fast = int(float(_value(row, "selected_fast_window")))
    slow = int(float(_value(row, "selected_slow_window")))
    return DiagnosticWindow(
        window_id=int(float(_value(row, "window_id"))),
        test_start=str(_value(row, "test_start")),
        test_end=str(_value(row, "test_end")),
        selected_fast_window=fast,
        selected_slow_window=slow,
        selected_parameter_label=_selected_parameter_label(row, fast, slow),
        test_return_pct=float(_value(row, "test_total_return_pct")),
        test_max_drawdown_pct=float(_value(row, "test_max_drawdown_pct")),
        test_trade_count=int(float(_value(row, "test_trade_count"))),
        test_profit_factor=_parse_optional_float(_value(row, "test_profit_factor")),
    )


def _selected_parameter_label(row: dict[str, str], fast: int, slow: int) -> str:
    entry = _optional_value(row, "selected_entry_window")
    exit_ = _optional_value(row, "selected_exit_window")
    atr_window = _optional_value(row, "selected_atr_window")
    atr_multiplier = _optional_value(row, "selected_atr_multiplier")
    if entry not in {None, "", "null"} and exit_ not in {None, "", "null"}:
        return f"entry={entry}/exit={exit_}/atr={atr_window}/mult={atr_multiplier}"
    rsi_window = _optional_value(row, "selected_rsi_window")
    if rsi_window not in {None, "", "null"}:
        buy = _optional_value(row, "selected_buy_threshold")
        sell = _optional_value(row, "selected_sell_threshold")
        return f"rsi={rsi_window}/buy={buy}/sell={sell}"
    bollinger_window = _optional_value(row, "selected_window")
    if bollinger_window not in {None, "", "null"}:
        num_std = _optional_value(row, "selected_num_std")
        return f"window={bollinger_window}/std={num_std}"
    trend_ema = _optional_value(row, "selected_trend_ema_window")
    if trend_ema not in {None, "", "null"}:
        pullback_ema = _optional_value(row, "selected_pullback_ema_window")
        return f"trend={trend_ema}/pullback={pullback_ema}"
    return f"{fast}/{slow}"


_COLUMN_ALIASES = {
    "window_id": ("window_id", "\u7a97\u53e3ID"),
    "test_start": ("test_start", "\u6d4b\u8bd5\u5f00\u59cb"),
    "test_end": ("test_end", "\u6d4b\u8bd5\u7ed3\u675f"),
    "selected_fast_window": ("selected_fast_window", "\u9009\u62e9\u5feb\u5747\u7ebf\u7a97\u53e3"),
    "selected_slow_window": ("selected_slow_window", "\u9009\u62e9\u6162\u5747\u7ebf\u7a97\u53e3"),
    "selected_entry_window": ("selected_entry_window", "\u9009\u62e9Donchian\u5165\u573a\u7a97\u53e3"),
    "selected_exit_window": ("selected_exit_window", "\u9009\u62e9Donchian\u51fa\u573a\u7a97\u53e3"),
    "selected_atr_window": ("selected_atr_window", "\u9009\u62e9ATR\u7a97\u53e3"),
    "selected_atr_multiplier": ("selected_atr_multiplier", "\u9009\u62e9ATR\u500d\u6570"),
    "selected_rsi_window": ("selected_rsi_window", "\u9009\u62e9RSI\u7a97\u53e3"),
    "selected_buy_threshold": ("selected_buy_threshold", "\u9009\u62e9\u4e70\u5165\u9608\u503c"),
    "selected_sell_threshold": ("selected_sell_threshold", "\u9009\u62e9\u5356\u51fa\u9608\u503c"),
    "selected_window": ("selected_window", "\u9009\u62e9\u5e03\u6797\u5e26\u7a97\u53e3"),
    "selected_num_std": ("selected_num_std", "\u9009\u62e9\u6807\u51c6\u5dee\u500d\u6570"),
    "selected_trend_ema_window": ("selected_trend_ema_window", "\u9009\u62e9\u8d8b\u52bfEMA\u7a97\u53e3"),
    "selected_pullback_ema_window": ("selected_pullback_ema_window", "\u9009\u62e9\u56de\u8e29EMA\u7a97\u53e3"),
    "test_total_return_pct": (
        "test_total_return_pct",
        "\u6d4b\u8bd5\u603b\u6536\u76ca\u7387\u767e\u5206\u6bd4",
    ),
    "test_max_drawdown_pct": (
        "test_max_drawdown_pct",
        "\u6d4b\u8bd5\u6700\u5927\u56de\u64a4\u767e\u5206\u6bd4",
    ),
    "test_trade_count": ("test_trade_count", "\u6d4b\u8bd5\u4ea4\u6613\u6b21\u6570"),
    "test_profit_factor": ("test_profit_factor", "\u6d4b\u8bd5\u76c8\u4e8f\u56e0\u5b50"),
}

_REQUIRED_COLUMN_KEYS = [
    "window_id",
    "test_start",
    "test_end",
    "selected_fast_window",
    "selected_slow_window",
    "test_total_return_pct",
    "test_max_drawdown_pct",
    "test_trade_count",
    "test_profit_factor",
]


def _value(row: dict[str, str], key: str) -> str:
    english, chinese = _COLUMN_ALIASES[key]
    value = row.get(english) if english in row else row.get(chinese)
    if value is None:
        raise ValueError(f"walk_forward_results missing required value: {english}")
    return value


def _optional_value(row: dict[str, str], key: str) -> str | None:
    english, chinese = _COLUMN_ALIASES[key]
    return row.get(english) if english in row else row.get(chinese)


def _parse_optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"null", "none", "nan", "infinity", "inf"}:
        return None
    return float(text)


def _profit_concentration_pct(returns: list[float]) -> float | None:
    profits = sorted([value for value in returns if value > 0], reverse=True)
    total_profit = sum(profits)
    if total_profit <= 0:
        return None
    top_count = max(1, int(len(returns) * 0.2))
    return round((sum(profits[:top_count]) / total_profit) * 100, 10)


def _flags(
    windows: list[DiagnosticWindow],
    positive_count: int,
    concentration_pct: float | None,
    parameter_switching: bool,
) -> list[str]:
    flags: list[str] = []
    if len(windows) < 10 or sum(window.test_trade_count for window in windows) < 100:
        flags.append("insufficient_evidence")
    if concentration_pct is not None and concentration_pct >= 60:
        flags.append("returns_are_concentrated_in_top_windows")
    if parameter_switching:
        flags.append("parameters_switch_frequently")
    if positive_count <= len(windows) * 0.5:
        flags.append("too_few_positive_windows")
    low_trade_windows = sum(1 for window in windows if window.test_trade_count < 5)
    if low_trade_windows:
        flags.append("low_trade_count_windows")
    return flags


def _rating(windows: list[DiagnosticWindow], flags: list[str], positive_count: int) -> str:
    if "returns_are_concentrated_in_top_windows" in flags:
        return "concentrated"
    actionable_flags = [flag for flag in flags if flag != "insufficient_evidence"]
    if actionable_flags:
        return "unstable"
    if "insufficient_evidence" in flags:
        return "insufficient_evidence"
    if positive_count >= len(windows) * 0.6:
        return "stable"
    return "unstable"


def _recommendations(flags: list[str], windows: list[DiagnosticWindow]) -> list[str]:
    recommendations = [
        "\u9700\u8981\u66f4\u591a\u8d44\u4ea7\u9a8c\u8bc1",
        "\u9700\u8981\u66f4\u957f\u65f6\u95f4\u6bb5",
    ]
    if "parameters_switch_frequently" in flags:
        recommendations.append("\u9700\u8981\u52a0\u5165\u8d8b\u52bf\u8fc7\u6ee4")
        recommendations.append("\u9700\u8981\u52a0\u5165\u9707\u8361\u8fc7\u6ee4")
    if "low_trade_count_windows" in flags or sum(window.test_trade_count for window in windows) < 100:
        recommendations.append("\u9700\u8981\u63d0\u9ad8\u6bcf\u4e2a\u7a97\u53e3\u4ea4\u6613\u6570")
    if "returns_are_concentrated_in_top_windows" in flags:
        recommendations.append(
            "\u9700\u8981\u68c0\u67e5\u6536\u76ca\u662f\u5426\u96c6\u4e2d"
            "\u5728\u5c11\u6570\u884c\u60c5\u9636\u6bb5"
        )
    recommendations.append("\u9700\u8981\u964d\u4f4e\u91cd\u590d\u4fe1\u53f7")
    return list(dict.fromkeys(recommendations))


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 10)


def _source_files(
    walk_forward_path: Path,
    equity_curve_path: str | Path | None,
    trades_path: str | Path | None,
    data_quality_path: str | Path | None,
) -> dict[str, str]:
    files = {"walk_forward_results": str(walk_forward_path)}
    if equity_curve_path:
        files["equity_curve"] = str(equity_curve_path)
    if trades_path:
        files["trades"] = str(trades_path)
    if data_quality_path:
        files["data_quality"] = str(data_quality_path)
    return files


def _export(report: WalkForwardDiagnosis, export_path: str | Path | None) -> None:
    if export_path is None:
        return
    path = Path(export_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _nullable(value) -> str:
    return "null" if value is None else str(value)


def _list(values: list[str]) -> str:
    return "none" if not values else ", ".join(values)
