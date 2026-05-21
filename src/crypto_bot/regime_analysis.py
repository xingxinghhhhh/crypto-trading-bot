from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from crypto_bot.market.csv_data import REQUIRED_COLUMNS, load_ohlcv_csv


FEATURE_NAMES = [
    "window_return_pct",
    "volatility_pct",
    "max_drawdown_pct",
    "trend_strength",
    "moving_average_slope",
    "price_above_slow_ma_pct",
    "range_bound_score",
    "volume_change_pct",
]


@dataclass(frozen=True)
class RegimeWindow:
    window_id: int
    test_start: str
    test_end: str
    selected_fast_window: int
    selected_slow_window: int
    test_return_pct: float
    test_max_drawdown_pct: float
    test_trade_count: int
    test_profit_factor: float | None
    features: dict[str, float | None]


@dataclass(frozen=True)
class RegimeAnalysisReport:
    window_count: int
    profitable_window_count: int
    losing_window_count: int
    flat_window_count: int
    windows: list[RegimeWindow]
    profitable_window_regime_summary: dict[str, dict[str, float | None]]
    losing_window_regime_summary: dict[str, dict[str, float | None]]
    feature_difference_summary: dict[str, dict[str, float | None]]
    likely_discriminating_features: list[str]
    candidate_filter_rules: list[str]
    notes: list[str] = field(default_factory=list)
    source_files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_regimes(
    csv_path: str | Path,
    walk_forward_results_path: str | Path,
    trades_path: str | Path | None = None,
    equity_curve_path: str | Path | None = None,
    export_path: str | Path | None = None,
) -> RegimeAnalysisReport:
    bars = _load_bars(csv_path)
    windows = _load_walk_forward_windows(Path(walk_forward_results_path))
    regime_windows = [_match_window(bars, window) for window in windows]
    profitable = [window for window in regime_windows if window.test_return_pct > 0]
    losing = [window for window in regime_windows if window.test_return_pct < 0]
    flat = [window for window in regime_windows if window.test_return_pct == 0]
    profitable_summary = _feature_summary(profitable)
    losing_summary = _feature_summary(losing)
    differences = _feature_differences(profitable_summary, losing_summary)
    discriminators = _likely_discriminators(differences)
    report = RegimeAnalysisReport(
        window_count=len(regime_windows),
        profitable_window_count=len(profitable),
        losing_window_count=len(losing),
        flat_window_count=len(flat),
        windows=regime_windows,
        profitable_window_regime_summary=profitable_summary,
        losing_window_regime_summary=losing_summary,
        feature_difference_summary=differences,
        likely_discriminating_features=discriminators,
        candidate_filter_rules=_candidate_rules(profitable_summary, losing_summary, discriminators),
        notes=[
            "regime analysis is offline research only",
            "candidate filters require fresh walk-forward validation before use",
        ],
        source_files=_source_files(csv_path, walk_forward_results_path, trades_path, equity_curve_path),
    )
    _export(report, export_path)
    return report


def format_regime_analysis_report(report: RegimeAnalysisReport) -> str:
    return "\n".join(
        [
            f"regime_analysis_window_count: {report.window_count}",
            f"profitable_window_count: {report.profitable_window_count}",
            f"losing_window_count: {report.losing_window_count}",
            f"flat_window_count: {report.flat_window_count}",
            f"likely_discriminating_features: {_list(report.likely_discriminating_features)}",
            f"candidate_filter_rules: {_list(report.candidate_filter_rules)}",
            f"notes: {_list(report.notes)}",
        ]
    )


def _load_bars(csv_path: str | Path) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"ohlcv csv not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"ohlcv csv is empty: {path}")
    try:
        frame = load_ohlcv_csv(path)
    except pd.errors.EmptyDataError as exc:
        raise ValueError(f"ohlcv csv is empty: {path}") from exc
    except ValueError as exc:
        raise ValueError(str(exc).replace("CSV", "ohlcv csv")) from exc
    if frame.empty:
        raise ValueError(f"ohlcv csv is empty: {path}")
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"ohlcv csv missing required columns: {', '.join(missing)}")
    return frame


def _load_walk_forward_windows(path: Path) -> list[dict[str, Any]]:
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
    missing = _missing_walk_forward_columns(reader.fieldnames)
    if missing:
        raise ValueError(f"walk_forward_results missing required columns: {', '.join(missing)}")
    return rows


def _missing_walk_forward_columns(fieldnames: list[str]) -> list[str]:
    missing = []
    for english, chinese in _WALK_FORWARD_ALIASES.values():
        if english not in fieldnames and chinese not in fieldnames:
            missing.append(english)
    return missing


def _match_window(bars: pd.DataFrame, row: dict[str, str]) -> RegimeWindow:
    test_start = pd.to_datetime(_wf_value(row, "test_start"), utc=True)
    test_end = pd.to_datetime(_wf_value(row, "test_end"), utc=True)
    window_bars = bars[(bars["timestamp"] >= test_start) & (bars["timestamp"] <= test_end)].copy()
    if window_bars.empty:
        raise ValueError(f"no OHLCV bars found for test window {test_start.isoformat()} to {test_end.isoformat()}")
    return RegimeWindow(
        window_id=int(float(_wf_value(row, "window_id"))),
        test_start=test_start.isoformat(),
        test_end=test_end.isoformat(),
        selected_fast_window=int(float(_wf_value(row, "selected_fast_window"))),
        selected_slow_window=int(float(_wf_value(row, "selected_slow_window"))),
        test_return_pct=round(float(_wf_value(row, "test_total_return_pct")), 10),
        test_max_drawdown_pct=round(float(_wf_value(row, "test_max_drawdown_pct")), 10),
        test_trade_count=int(float(_wf_value(row, "test_trade_count"))),
        test_profit_factor=_parse_optional_float(_wf_value(row, "test_profit_factor")),
        features=_calculate_features(window_bars),
    )


def _calculate_features(frame: pd.DataFrame) -> dict[str, float | None]:
    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    first_close = float(ordered["close"].iloc[0])
    last_close = float(ordered["close"].iloc[-1])
    first_volume = float(ordered["volume"].iloc[0])
    window_return_pct = _pct_change(first_close, last_close)
    close_returns = ordered["close"].pct_change().dropna() * 100
    volatility_pct = round(float(close_returns.std(ddof=0)), 10) if not close_returns.empty else 0.0
    moving_average = ordered["close"].rolling(window=min(30, len(ordered)), min_periods=1).mean()
    moving_average_slope = _pct_change(float(moving_average.iloc[0]), float(moving_average.iloc[-1]))
    price_above_slow_ma_pct = round(float((ordered["close"] > moving_average).mean() * 100), 10)
    high_low_range_pct = _pct_change(first_close, float(ordered["high"].max()), float(ordered["low"].min()))
    abs_return = abs(window_return_pct)
    range_bound_score = None if high_low_range_pct is None else round(high_low_range_pct / max(abs_return, 0.01), 10)
    return {
        "window_return_pct": window_return_pct,
        "volatility_pct": volatility_pct,
        "max_drawdown_pct": _max_drawdown_pct(ordered["close"]),
        "trend_strength": None if volatility_pct == 0 else round(abs_return / volatility_pct, 10),
        "moving_average_slope": moving_average_slope,
        "price_above_slow_ma_pct": price_above_slow_ma_pct,
        "range_bound_score": range_bound_score,
        "volume_change_pct": _pct_change(first_volume, float(ordered["volume"].iloc[-1])),
    }


def _pct_change(base: float, value: float, low_value: float | None = None) -> float | None:
    if base == 0:
        return None
    if low_value is None:
        return round(((value - base) / base) * 100, 10)
    return round(((value - low_value) / base) * 100, 10)


def _max_drawdown_pct(closes: pd.Series) -> float:
    peak = closes.cummax()
    drawdown = ((closes - peak) / peak) * 100
    return round(abs(float(drawdown.min())), 10)


def _feature_summary(windows: list[RegimeWindow]) -> dict[str, dict[str, float | None]]:
    summary = {}
    for feature in FEATURE_NAMES:
        values = [window.features[feature] for window in windows if window.features[feature] is not None]
        numeric = [float(value) for value in values]
        summary[feature] = _summary_values(numeric)
    return summary


def _summary_values(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"average": None, "median": None, "minimum": None, "maximum": None}
    return {
        "average": round(sum(values) / len(values), 10),
        "median": round(float(median(values)), 10),
        "minimum": round(min(values), 10),
        "maximum": round(max(values), 10),
    }


def _feature_differences(
    profitable_summary: dict[str, dict[str, float | None]],
    losing_summary: dict[str, dict[str, float | None]],
) -> dict[str, dict[str, float | None]]:
    differences = {}
    for feature in FEATURE_NAMES:
        profitable_average = profitable_summary[feature]["average"]
        losing_average = losing_summary[feature]["average"]
        if profitable_average is None or losing_average is None:
            difference = None
        else:
            difference = round(float(profitable_average) - float(losing_average), 10)
        differences[feature] = {
            "profitable_average": profitable_average,
            "losing_average": losing_average,
            "profitable_minus_losing_average": difference,
            "absolute_difference": None if difference is None else round(abs(difference), 10),
        }
    return differences


def _likely_discriminators(differences: dict[str, dict[str, float | None]]) -> list[str]:
    ranked = [
        (feature, _difference_score(values))
        for feature, values in differences.items()
        if values["absolute_difference"] is not None
    ]
    ranked.sort(key=lambda item: float(item[1]), reverse=True)
    return [feature for feature, _ in ranked[:4]]


def _difference_score(values: dict[str, float | None]) -> float:
    absolute_difference = float(values["absolute_difference"] or 0.0)
    profitable_average = values["profitable_average"]
    losing_average = values["losing_average"]
    scale_values = [abs(float(value)) for value in [profitable_average, losing_average] if value is not None]
    scale = max(sum(scale_values) / len(scale_values), 1.0) if scale_values else 1.0
    return round(absolute_difference / scale, 10)


def _candidate_rules(
    profitable_summary: dict[str, dict[str, float | None]],
    losing_summary: dict[str, dict[str, float | None]],
    discriminators: list[str],
) -> list[str]:
    rules: list[str] = []
    return_threshold = _midpoint(
        profitable_summary["window_return_pct"]["median"],
        losing_summary["window_return_pct"]["median"],
    )
    trend_threshold = _midpoint(
        profitable_summary["trend_strength"]["median"],
        losing_summary["trend_strength"]["median"],
    )
    slope_threshold = _midpoint(
        profitable_summary["moving_average_slope"]["median"],
        losing_summary["moving_average_slope"]["median"],
    )
    range_threshold = _midpoint(
        profitable_summary["range_bound_score"]["median"],
        losing_summary["range_bound_score"]["median"],
    )
    volatility_threshold = _midpoint(
        profitable_summary["volatility_pct"]["median"],
        losing_summary["volatility_pct"]["median"],
    )
    drawdown_threshold = _midpoint(
        profitable_summary["max_drawdown_pct"]["median"],
        losing_summary["max_drawdown_pct"]["median"],
    )
    above_ma_threshold = _midpoint(
        profitable_summary["price_above_slow_ma_pct"]["median"],
        losing_summary["price_above_slow_ma_pct"]["median"],
    )
    volume_threshold = _midpoint(
        profitable_summary["volume_change_pct"]["median"],
        losing_summary["volume_change_pct"]["median"],
    )
    if "window_return_pct" in discriminators and return_threshold is not None:
        rules.append(f"research_only prefer_windows_when window_return_pct > {return_threshold}")
    if "trend_strength" in discriminators and trend_threshold is not None:
        rules.append(f"only_trade_when trend_strength > {trend_threshold}")
    if "moving_average_slope" in discriminators and slope_threshold is not None:
        rules.append(f"only_trade_when moving_average_slope > {slope_threshold}")
    if "range_bound_score" in discriminators and range_threshold is not None:
        rules.append(f"avoid_when range_bound_score > {range_threshold}")
    if "volatility_pct" in discriminators and volatility_threshold is not None:
        rules.append(f"avoid_when volatility_pct too high or too low around {volatility_threshold}")
    if "max_drawdown_pct" in discriminators and drawdown_threshold is not None:
        rules.append(f"avoid_when market_window_max_drawdown_pct > {drawdown_threshold}")
    if "price_above_slow_ma_pct" in discriminators and above_ma_threshold is not None:
        rules.append(f"only_trade_when price_above_slow_ma_pct > {above_ma_threshold}")
    if "volume_change_pct" in discriminators and volume_threshold is not None:
        rules.append(f"research_only compare volume_change_pct around {volume_threshold}")
    if trend_threshold is not None:
        rules.append(f"research_candidate only_trade_when trend_strength > {trend_threshold}")
    if range_threshold is not None:
        rules.append(f"research_candidate avoid_when range_bound_score > {range_threshold}")
    if volatility_threshold is not None:
        rules.append(f"research_candidate avoid_when volatility_pct too high or too low around {volatility_threshold}")
    if above_ma_threshold is not None:
        rules.append(f"research_candidate only_trade_when price_above_slow_ma_pct > {above_ma_threshold}")
    if not rules:
        rules.append("collect_more_windows_before_defining_filters")
    rules.append("do_not_apply_filters_until_a_new_walk_forward_validation_passes")
    return list(dict.fromkeys(rules))


def _midpoint(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round((float(left) + float(right)) / 2, 10)


_WALK_FORWARD_ALIASES = {
    "window_id": ("window_id", "\u7a97\u53e3ID"),
    "test_start": ("test_start", "\u6d4b\u8bd5\u5f00\u59cb"),
    "test_end": ("test_end", "\u6d4b\u8bd5\u7ed3\u675f"),
    "selected_fast_window": ("selected_fast_window", "\u9009\u62e9\u5feb\u5747\u7ebf\u7a97\u53e3"),
    "selected_slow_window": ("selected_slow_window", "\u9009\u62e9\u6162\u5747\u7ebf\u7a97\u53e3"),
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


def _wf_value(row: dict[str, str], key: str) -> str:
    english, chinese = _WALK_FORWARD_ALIASES[key]
    value = row.get(english) if english in row else row.get(chinese)
    if value is None:
        raise ValueError(f"walk_forward_results missing required value: {english}")
    return value


def _parse_optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"null", "none", "nan", "infinity", "inf"}:
        return None
    return round(float(text), 10)


def _source_files(
    csv_path: str | Path,
    walk_forward_results_path: str | Path,
    trades_path: str | Path | None,
    equity_curve_path: str | Path | None,
) -> dict[str, str]:
    files = {
        "csv": str(csv_path),
        "walk_forward_results": str(walk_forward_results_path),
    }
    if trades_path:
        files["trades"] = str(trades_path)
    if equity_curve_path:
        files["equity_curve"] = str(equity_curve_path)
    return files


def _export(report: RegimeAnalysisReport, export_path: str | Path | None) -> None:
    if export_path is None:
        return
    path = Path(export_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _list(values: list[str]) -> str:
    return "none" if not values else ", ".join(values)
