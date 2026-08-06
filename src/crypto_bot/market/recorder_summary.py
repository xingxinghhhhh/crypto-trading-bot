from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from crypto_bot.market.realtime_gate import timeframe_duration


def summarize_market_recordings(
    input_dir: str | Path,
    day: date,
    *,
    export_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(input_dir)
    manifests = sorted(
        path
        for path in root.rglob("manifest_*.json")
        if path.parent.name == day.isoformat()
    )
    groups: dict[tuple[str, str, str], list[pd.Timestamp]] = defaultdict(list)
    raw_rows = 0
    closed_rows = 0
    checksum_failures: list[str] = []
    delays: list[float] = []

    for manifest_path in manifests:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_path = manifest_path.parent / str(payload["raw_file"])
        normalized_path = manifest_path.parent / str(payload["normalized_file"])
        if _sha256(raw_path) != payload["raw_sha256"]:
            checksum_failures.append(str(raw_path))
        normalized_valid = _sha256(normalized_path) == payload["normalized_sha256"]
        if not normalized_valid:
            checksum_failures.append(str(normalized_path))

        raw_rows += int(payload["raw_row_count"])
        closed_rows += int(payload["closed_row_count"])
        if not normalized_valid:
            continue
        frame = pd.read_csv(normalized_path)
        timestamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        key = (
            str(payload["exchange"]),
            str(payload["symbol"]),
            str(payload["timeframe"]),
        )
        groups[key].extend(timestamp for timestamp in timestamps if not pd.isna(timestamp))
        received_at = pd.Timestamp(payload["received_at"])
        last_closed = pd.Timestamp(payload["last_closed_bar"]) + timeframe_duration(key[2])
        delays.append(max(0.0, (received_at - last_closed).total_seconds()))

    duplicate_count = 0
    gap_count = 0
    group_summaries: list[dict[str, Any]] = []
    for (exchange, symbol, timeframe), timestamps in sorted(groups.items()):
        series = pd.Series(timestamps).sort_values(ignore_index=True)
        duplicate_count += int(series.duplicated().sum())
        unique = series.drop_duplicates().reset_index(drop=True)
        intervals = unique.diff().dropna()
        group_gap_count = int((intervals != timeframe_duration(timeframe)).sum())
        gap_count += group_gap_count
        group_summaries.append(
            {
                "exchange": exchange,
                "symbol": symbol,
                "timeframe": timeframe,
                "unique_closed_bar_count": len(unique),
                "duplicate_count": int(series.duplicated().sum()),
                "gap_count": group_gap_count,
                "first_bar": unique.iloc[0].isoformat() if not unique.empty else None,
                "last_bar": unique.iloc[-1].isoformat() if not unique.empty else None,
            }
        )

    report = {
        "day": day.isoformat(),
        "batch_count": len(manifests),
        "raw_row_count": raw_rows,
        "closed_row_count": closed_rows,
        "duplicate_count": duplicate_count,
        "gap_count": gap_count,
        "checksum_failure_count": len(checksum_failures),
        "checksum_failures": checksum_failures,
        "max_receive_delay_seconds": max(delays, default=None),
        "groups": group_summaries,
        "healthy": bool(manifests) and not checksum_failures and gap_count == 0,
    }
    if export_path is not None:
        path = Path(export_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
    return report


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
