from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from crypto_bot.market.csv_data import REQUIRED_COLUMNS
from crypto_bot.market.data_quality import DataQualityReport, validate_ohlcv_csv


@dataclass(frozen=True)
class NormalizeCsvResult:
    input_path: Path
    output_path: Path
    input_rows: int
    output_rows: int
    dropped_duplicate_count: int
    validation: DataQualityReport


def normalize_ohlcv_csv(
    input_path: str | Path,
    output_path: str | Path,
    timeframe: str,
    validation_report_path: str | Path | None = None,
) -> NormalizeCsvResult:
    source_path = Path(input_path)
    target_path = Path(output_path)
    if not source_path.exists():
        raise FileNotFoundError(f"CSV input file not found: {source_path}")

    source = pd.read_csv(source_path)
    column_map = _resolve_columns(source.columns)
    normalized = pd.DataFrame(
        {
            "timestamp": _parse_timestamp_series(source[column_map["timestamp"]]),
            "open": pd.to_numeric(source[column_map["open"]], errors="coerce"),
            "high": pd.to_numeric(source[column_map["high"]], errors="coerce"),
            "low": pd.to_numeric(source[column_map["low"]], errors="coerce"),
            "close": pd.to_numeric(source[column_map["close"]], errors="coerce"),
            "volume": pd.to_numeric(source[column_map["volume"]], errors="coerce"),
        }
    )
    before_dedup = len(normalized)
    normalized = normalized.dropna(subset=REQUIRED_COLUMNS)
    normalized = normalized.drop_duplicates(subset=["timestamp"], keep="last")
    normalized = normalized.sort_values("timestamp").reset_index(drop=True)
    dropped_duplicate_count = before_dedup - len(normalized)

    csv_frame = normalized[REQUIRED_COLUMNS].copy()
    csv_frame["timestamp"] = pd.to_datetime(csv_frame["timestamp"], utc=True).map(lambda item: item.isoformat())
    _write_csv_atomically(csv_frame, target_path)
    validation = validate_ohlcv_csv(target_path, timeframe, export_path=validation_report_path)
    return NormalizeCsvResult(
        input_path=source_path,
        output_path=target_path,
        input_rows=len(source),
        output_rows=len(csv_frame),
        dropped_duplicate_count=dropped_duplicate_count,
        validation=validation,
    )


def _resolve_columns(columns) -> dict[str, str]:
    normalized_to_original = {_normalize_name(column): column for column in columns}
    resolved: dict[str, str] = {}
    aliases = {
        "timestamp": ["timestamp", "date", "datetime", "time", "opentime", "open_time", "unix"],
        "open": ["open", "o"],
        "high": ["high", "h"],
        "low": ["low", "l"],
        "close": ["close", "c"],
        "volume": ["volume", "vol", "basevolume", "base_volume"],
    }
    for required, names in aliases.items():
        for name in names:
            key = _normalize_name(name)
            if key in normalized_to_original:
                resolved[required] = normalized_to_original[key]
                break
        if required not in resolved:
            raise ValueError(f"CSV missing recognizable {required} column")
    return resolved


def _normalize_name(value: str) -> str:
    return "".join(char for char in str(value).strip().lower() if char.isalnum())


def _parse_timestamp_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        numeric = pd.to_numeric(series, errors="coerce")
        return _parse_numeric_timestamps(numeric)

    stripped = series.astype(str).str.strip()
    numeric = pd.to_numeric(stripped, errors="coerce")
    numeric_ratio = numeric.notna().mean() if len(numeric) else 0
    if numeric_ratio > 0.9:
        return _parse_numeric_timestamps(numeric)
    return pd.to_datetime(stripped, utc=True, errors="coerce")


def _parse_numeric_timestamps(numeric: pd.Series) -> pd.Series:
    parsed = pd.Series(pd.NaT, index=numeric.index, dtype="datetime64[ns, UTC]")
    microseconds = numeric >= 1_000_000_000_000_000
    milliseconds = (numeric >= 10_000_000_000) & ~microseconds
    seconds = numeric.notna() & ~microseconds & ~milliseconds
    parsed.loc[microseconds] = pd.to_datetime(numeric.loc[microseconds], unit="us", utc=True, errors="coerce")
    parsed.loc[milliseconds] = pd.to_datetime(numeric.loc[milliseconds], unit="ms", utc=True, errors="coerce")
    parsed.loc[seconds] = pd.to_datetime(numeric.loc[seconds], unit="s", utc=True, errors="coerce")
    return parsed


def _write_csv_atomically(frame: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.tmp")
    try:
        frame.to_csv(temporary_path, index=False)
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
