from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import pandas as pd

from crypto_bot.errors import MarketDataError
from crypto_bot.market.csv_data import REQUIRED_COLUMNS
from crypto_bot.market.realtime_gate import timeframe_duration


@dataclass(frozen=True)
class ReplayDatasetReport:
    exchange: str
    symbol: str
    source_timeframe: str
    target_timeframe: str
    manifest_count: int
    input_bar_count: int
    unique_source_bar_count: int
    duplicate_bar_count: int
    gap_count: int
    dropped_incomplete_source_bar_count: int
    replay_bar_count: int
    first_replay_bar: str
    last_replay_bar: str
    dataset_sha256: str
    output_csv: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ReplayDataset:
    bars: pd.DataFrame
    report: ReplayDatasetReport


def build_replay_dataset(
    input_dir: str | Path,
    *,
    exchange: str,
    symbol: str,
    source_timeframe: str,
    target_timeframe: str,
    output_csv: str | Path | None = None,
) -> ReplayDataset:
    manifests = _load_matching_manifests(
        Path(input_dir),
        exchange=exchange,
        symbol=symbol,
        source_timeframe=source_timeframe,
    )
    frames = [_load_verified_normalized_frame(path, payload) for path, payload in manifests]
    combined = pd.concat(frames, ignore_index=True)
    normalized = _normalize_source_bars(combined, source_timeframe)
    duplicate_count = int(normalized.duplicated(subset=["timestamp"]).sum())
    _reject_conflicting_duplicates(normalized)
    unique = (
        normalized.drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    gap_count = _count_gaps(unique, source_timeframe)
    if gap_count:
        raise MarketDataError(f"replay_source_gap_detected:{gap_count}")

    replay_bars, dropped_count = _resample_complete_bars(
        unique,
        source_timeframe=source_timeframe,
        target_timeframe=target_timeframe,
    )
    canonical_csv = _canonical_csv(replay_bars)
    dataset_sha256 = hashlib.sha256(canonical_csv.encode("utf-8")).hexdigest()
    output_path = Path(output_csv) if output_csv is not None else None
    if output_path is not None:
        _write_text_atomically(output_path, canonical_csv)

    report = ReplayDatasetReport(
        exchange=exchange,
        symbol=symbol,
        source_timeframe=source_timeframe,
        target_timeframe=target_timeframe,
        manifest_count=len(manifests),
        input_bar_count=len(normalized),
        unique_source_bar_count=len(unique),
        duplicate_bar_count=duplicate_count,
        gap_count=gap_count,
        dropped_incomplete_source_bar_count=dropped_count,
        replay_bar_count=len(replay_bars),
        first_replay_bar=pd.Timestamp(replay_bars.iloc[0]["timestamp"]).isoformat(),
        last_replay_bar=pd.Timestamp(replay_bars.iloc[-1]["timestamp"]).isoformat(),
        dataset_sha256=dataset_sha256,
        output_csv=str(output_path) if output_path is not None else None,
    )
    return ReplayDataset(bars=replay_bars, report=report)


def _load_matching_manifests(
    root: Path,
    *,
    exchange: str,
    symbol: str,
    source_timeframe: str,
) -> list[tuple[Path, dict]]:
    matching: list[tuple[Path, dict]] = []
    for manifest_path in sorted(root.rglob("manifest_*.json")):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MarketDataError(f"replay_manifest_invalid:{manifest_path}") from exc
        if (
            str(payload.get("exchange")) == exchange
            and str(payload.get("symbol")) == symbol
            and str(payload.get("timeframe")) == source_timeframe
        ):
            matching.append((manifest_path, payload))
    if not matching:
        raise MarketDataError("replay_no_matching_manifests")
    return matching


def _load_verified_normalized_frame(manifest_path: Path, payload: dict) -> pd.DataFrame:
    raw_path = _manifest_member(manifest_path, payload, "raw_file")
    normalized_path = _manifest_member(manifest_path, payload, "normalized_file")
    _verify_checksum(raw_path, payload.get("raw_sha256"))
    _verify_checksum(normalized_path, payload.get("normalized_sha256"))
    try:
        frame = pd.read_csv(normalized_path)
    except Exception as exc:
        raise MarketDataError(f"replay_csv_read_failed:{normalized_path}") from exc
    if list(frame.columns) != REQUIRED_COLUMNS:
        raise MarketDataError(f"replay_invalid_columns:{normalized_path}")
    expected_rows = payload.get("closed_row_count")
    if not isinstance(expected_rows, int) or expected_rows != len(frame):
        raise MarketDataError(f"replay_row_count_mismatch:{normalized_path}")
    return frame


def _manifest_member(manifest_path: Path, payload: dict, key: str) -> Path:
    member = payload.get(key)
    if not isinstance(member, str) or not member or Path(member).name != member:
        raise MarketDataError(f"replay_manifest_member_invalid:{manifest_path}:{key}")
    path = manifest_path.parent / member
    if not path.is_file():
        raise MarketDataError(f"replay_manifest_member_missing:{path}")
    return path


def _verify_checksum(path: Path, expected: object) -> None:
    if not isinstance(expected, str) or _sha256(path) != expected:
        raise MarketDataError(f"replay_checksum_mismatch:{path}")


def _normalize_source_bars(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    normalized = frame[REQUIRED_COLUMNS].copy()
    normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if normalized[REQUIRED_COLUMNS].isna().any().any():
        raise MarketDataError("replay_missing_values")
    if (normalized["volume"] < 0).any():
        raise MarketDataError("replay_invalid_volume")
    invalid_ohlc = (
        (normalized["high"] < normalized["open"])
        | (normalized["high"] < normalized["close"])
        | (normalized["high"] < normalized["low"])
        | (normalized["low"] > normalized["open"])
        | (normalized["low"] > normalized["close"])
    )
    if invalid_ohlc.any():
        raise MarketDataError("replay_invalid_ohlc")

    duration = timeframe_duration(timeframe)
    if (normalized["timestamp"].dt.floor(duration) != normalized["timestamp"]).any():
        raise MarketDataError("replay_unaligned_source_timestamp")
    return normalized


def _reject_conflicting_duplicates(frame: pd.DataFrame) -> None:
    duplicates = frame[frame.duplicated(subset=["timestamp"], keep=False)]
    for timestamp, group in duplicates.groupby("timestamp"):
        distinct = group[["open", "high", "low", "close", "volume"]].drop_duplicates()
        if len(distinct) > 1:
            raise MarketDataError(f"replay_conflicting_duplicate:{pd.Timestamp(timestamp).isoformat()}")


def _count_gaps(frame: pd.DataFrame, timeframe: str) -> int:
    intervals = frame["timestamp"].diff().dropna()
    return int((intervals != timeframe_duration(timeframe)).sum())


def _resample_complete_bars(
    frame: pd.DataFrame,
    *,
    source_timeframe: str,
    target_timeframe: str,
) -> tuple[pd.DataFrame, int]:
    source_duration = timeframe_duration(source_timeframe)
    target_duration = timeframe_duration(target_timeframe)
    if target_duration < source_duration or target_duration.value % source_duration.value != 0:
        raise MarketDataError("replay_target_timeframe_must_be_an_exact_coarser_multiple")
    if target_duration == source_duration:
        return frame[REQUIRED_COLUMNS].reset_index(drop=True), 0

    expected_count = target_duration.value // source_duration.value
    indexed = frame.set_index("timestamp")
    grouped = indexed.resample(
        target_duration,
        origin="epoch",
        closed="left",
        label="left",
    )
    aggregated = grouped.agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    counts = grouped["close"].count()
    complete = counts == expected_count
    replay = aggregated.loc[complete].dropna().reset_index()
    if replay.empty:
        raise MarketDataError("replay_no_complete_target_bars")
    included_source_rows = int(counts.loc[complete].sum())
    dropped_count = len(frame) - included_source_rows
    return replay[REQUIRED_COLUMNS], dropped_count


def _canonical_csv(frame: pd.DataFrame) -> str:
    canonical = frame[REQUIRED_COLUMNS].copy()
    canonical["timestamp"] = pd.to_datetime(canonical["timestamp"], utc=True).map(
        lambda value: value.isoformat()
    )
    return canonical.to_csv(
        index=False,
        lineterminator="\n",
        float_format="%.15g",
    )


def _write_text_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
