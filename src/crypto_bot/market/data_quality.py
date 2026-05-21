from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from crypto_bot.market.csv_data import REQUIRED_COLUMNS


@dataclass(frozen=True)
class DataQualityReport:
    csv_path: str
    timeframe: str
    valid: bool
    exists: bool
    columns_ok: bool
    columns: list[str] = field(default_factory=list)
    bar_count: int = 0
    start_time: str | None = None
    end_time: str | None = None
    timestamp_parse_error_count: int = 0
    timestamp_monotonic_increasing: bool = False
    duplicate_timestamp_count: int = 0
    missing_value_count: int = 0
    ohlc_error_count: int = 0
    volume_error_count: int = 0
    time_gap_count: int = 0
    missing_bar_count: int = 0
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def validate_ohlcv_csv(
    csv_path: str | Path,
    timeframe: str,
    export_path: str | Path | None = None,
) -> DataQualityReport:
    path = Path(csv_path)
    if not path.exists():
        report = DataQualityReport(
            csv_path=str(path),
            timeframe=timeframe,
            valid=False,
            exists=False,
            columns_ok=False,
            issues=["csv_not_found"],
        )
        _export_report(report, export_path)
        return report

    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        report = DataQualityReport(
            csv_path=str(path),
            timeframe=timeframe,
            valid=False,
            exists=True,
            columns_ok=False,
            issues=[f"csv_read_failed:{exc}"],
        )
        _export_report(report, export_path)
        return report

    columns = list(frame.columns)
    columns_ok = columns == REQUIRED_COLUMNS
    if not columns_ok:
        report = DataQualityReport(
            csv_path=str(path),
            timeframe=timeframe,
            valid=False,
            exists=True,
            columns_ok=False,
            columns=columns,
            bar_count=len(frame),
            issues=["invalid_columns"],
        )
        _export_report(report, export_path)
        return report

    report = _validate_frame(path, timeframe, frame, columns)
    _export_report(report, export_path)
    return report


def format_data_quality_report(report: DataQualityReport) -> str:
    lines = [
        f"data_quality_valid: {str(report.valid).lower()}",
        f"csv_path: {report.csv_path}",
        f"timeframe: {report.timeframe}",
        f"exists: {str(report.exists).lower()}",
        f"columns_ok: {str(report.columns_ok).lower()}",
        f"start_time: {report.start_time}",
        f"end_time: {report.end_time}",
        f"bar_count: {report.bar_count}",
        f"missing_bar_count: {report.missing_bar_count}",
        f"duplicate_timestamp_count: {report.duplicate_timestamp_count}",
        f"timestamp_parse_error_count: {report.timestamp_parse_error_count}",
        f"timestamp_monotonic_increasing: {str(report.timestamp_monotonic_increasing).lower()}",
        f"missing_value_count: {report.missing_value_count}",
        f"ohlc_error_count: {report.ohlc_error_count}",
        f"volume_error_count: {report.volume_error_count}",
        f"time_gap_count: {report.time_gap_count}",
    ]
    if report.issues:
        lines.append(f"issues: {', '.join(report.issues)}")
    else:
        lines.append("issues: none")
    return "\n".join(lines)


def _validate_frame(path: Path, timeframe: str, frame: pd.DataFrame, columns: list[str]) -> DataQualityReport:
    working = frame[REQUIRED_COLUMNS].copy()
    parsed_timestamps = pd.to_datetime(working["timestamp"], utc=True, errors="coerce")
    timestamp_parse_error_count = int(parsed_timestamps.isna().sum())
    duplicate_timestamp_count = int(parsed_timestamps.dropna().duplicated().sum())
    timestamp_monotonic_increasing = bool(parsed_timestamps.dropna().is_monotonic_increasing)

    for column in ["open", "high", "low", "close", "volume"]:
        working[column] = pd.to_numeric(working[column], errors="coerce")
    missing_value_count = int(working[REQUIRED_COLUMNS].isna().sum().sum())

    high = working["high"]
    low = working["low"]
    open_ = working["open"]
    close = working["close"]
    ohlc_invalid = (
        (high < open_)
        | (high < close)
        | (high < low)
        | (low > open_)
        | (low > close)
        | (low > high)
    )
    ohlc_error_count = int(ohlc_invalid.fillna(False).sum())
    volume_error_count = int((working["volume"] < 0).fillna(False).sum())

    valid_timestamps = parsed_timestamps.dropna()
    start_time = None
    end_time = None
    time_gap_count = 0
    missing_bar_count = 0
    if not valid_timestamps.empty:
        unique_sorted = pd.Series(valid_timestamps.drop_duplicates().sort_values().reset_index(drop=True))
        start_time = pd.Timestamp(unique_sorted.iloc[0]).isoformat()
        end_time = pd.Timestamp(unique_sorted.iloc[-1]).isoformat()
        timeframe_delta = _timeframe_to_timedelta(timeframe)
        gaps = unique_sorted.diff().dropna()
        large_gaps = gaps[gaps > timeframe_delta]
        time_gap_count = int(len(large_gaps))
        if time_gap_count:
            missing_bar_count = int(((large_gaps / timeframe_delta) - 1).sum())

    issues = _collect_issues(
        timestamp_parse_error_count=timestamp_parse_error_count,
        timestamp_monotonic_increasing=timestamp_monotonic_increasing,
        duplicate_timestamp_count=duplicate_timestamp_count,
        missing_value_count=missing_value_count,
        ohlc_error_count=ohlc_error_count,
        volume_error_count=volume_error_count,
        time_gap_count=time_gap_count,
    )
    return DataQualityReport(
        csv_path=str(path),
        timeframe=timeframe,
        valid=not issues,
        exists=True,
        columns_ok=True,
        columns=columns,
        bar_count=len(frame),
        start_time=start_time,
        end_time=end_time,
        timestamp_parse_error_count=timestamp_parse_error_count,
        timestamp_monotonic_increasing=timestamp_monotonic_increasing,
        duplicate_timestamp_count=duplicate_timestamp_count,
        missing_value_count=missing_value_count,
        ohlc_error_count=ohlc_error_count,
        volume_error_count=volume_error_count,
        time_gap_count=time_gap_count,
        missing_bar_count=missing_bar_count,
        issues=issues,
    )


def _collect_issues(
    *,
    timestamp_parse_error_count: int,
    timestamp_monotonic_increasing: bool,
    duplicate_timestamp_count: int,
    missing_value_count: int,
    ohlc_error_count: int,
    volume_error_count: int,
    time_gap_count: int,
) -> list[str]:
    issues: list[str] = []
    if timestamp_parse_error_count:
        issues.append("timestamp_parse_failed")
    if not timestamp_monotonic_increasing:
        issues.append("timestamp_not_ascending")
    if duplicate_timestamp_count:
        issues.append("duplicate_timestamp")
    if missing_value_count:
        issues.append("missing_values")
    if ohlc_error_count:
        issues.append("invalid_ohlc")
    if volume_error_count:
        issues.append("invalid_volume")
    if time_gap_count:
        issues.append("time_gap_detected")
    return issues


def _timeframe_to_timedelta(timeframe: str) -> pd.Timedelta:
    unit = timeframe[-1]
    try:
        amount = int(timeframe[:-1])
    except ValueError as exc:
        raise ValueError(f"unsupported timeframe: {timeframe}") from exc
    if amount <= 0:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if unit == "m":
        return pd.Timedelta(minutes=amount)
    if unit == "h":
        return pd.Timedelta(hours=amount)
    if unit == "d":
        return pd.Timedelta(days=amount)
    raise ValueError(f"unsupported timeframe: {timeframe}")


def _export_report(report: DataQualityReport, export_path: str | Path | None) -> None:
    if export_path is None:
        return
    path = Path(export_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
