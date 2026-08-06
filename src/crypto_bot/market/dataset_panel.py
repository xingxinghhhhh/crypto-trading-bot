from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.csv_data import REQUIRED_COLUMNS
from crypto_bot.market.dataset_registry import (
    DatasetAudit,
    DatasetRegistryEntry,
    audit_dataset,
    canonicalize_ohlcv_frame,
    load_dataset_registry,
    write_json_atomically,
)


PANEL_SCHEMA_VERSION = 1
INNER_EXACT_ALIGNMENT = "inner_exact"
_PANEL_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")


@dataclass(frozen=True)
class DatasetPanelSpec:
    panel_id: str
    dataset_ids: tuple[str, ...]
    alignment: str


@dataclass(frozen=True)
class DatasetPanelConfig:
    specs: tuple[DatasetPanelSpec, ...]

    def get(self, panel_id: str) -> DatasetPanelSpec:
        for spec in self.specs:
            if spec.panel_id == panel_id:
                return spec
        raise ValueError(f"panel_id not found in config: {panel_id}")


@dataclass(frozen=True)
class DatasetPanelResult:
    frame: pd.DataFrame
    report: dict[str, Any]


def load_dataset_panel_config(path: str | Path) -> DatasetPanelConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"dataset panel config not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"dataset panel config YAML is invalid: {config_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("dataset panel config must be a mapping")
    if raw.get("schema_version") != PANEL_SCHEMA_VERSION:
        raise ValueError(f"dataset panel schema_version must be {PANEL_SCHEMA_VERSION}")
    raw_specs = raw.get("panels")
    if not isinstance(raw_specs, list) or not raw_specs:
        raise ValueError("dataset panel config must contain at least one panel")
    specs = tuple(_parse_panel_spec(item) for item in raw_specs)
    panel_ids = [spec.panel_id for spec in specs]
    if len(panel_ids) != len(set(panel_ids)):
        raise ValueError("dataset panel panel_id values must be unique")
    return DatasetPanelConfig(specs=specs)


def build_dataset_panel(
    registry_path: str | Path,
    panels_config_path: str | Path,
    panel_id: str,
    *,
    export_path: str | Path | None = None,
) -> DatasetPanelResult:
    registry = load_dataset_registry(registry_path)
    spec = load_dataset_panel_config(panels_config_path).get(panel_id)
    entries = tuple(sorted((registry.get(dataset_id) for dataset_id in spec.dataset_ids), key=_entry_sort_key))
    _validate_entries(entries)

    audited = [(entry, audit_dataset(entry)) for entry in entries]
    invalid = [(entry, audit) for entry, audit in audited if not audit.valid]
    if invalid:
        entry, audit = invalid[0]
        reasons = list(audit.observed.issues)
        reasons.extend(
            f"expected_{item['field']}_mismatch"
            for item in audit.declared_vs_observed["mismatches"]
        )
        if audit.canonical_sha256 is None:
            reasons.append("canonical_hash_unavailable")
        detail = ",".join(reasons) or "unknown"
        raise MarketDataError(f"dataset_panel_source_invalid:{entry.dataset_id}:{detail}")

    frames = {
        entry.dataset_id: canonicalize_ohlcv_frame(pd.read_csv(entry.resolved_path))
        for entry, _ in audited
    }
    timestamp_sets = {
        dataset_id: set(frame["timestamp"])
        for dataset_id, frame in frames.items()
    }
    union_timestamps = set.union(*timestamp_sets.values())
    intersection_timestamps = set.intersection(*timestamp_sets.values())
    if not intersection_timestamps:
        raise MarketDataError("dataset_panel_no_common_timestamps")

    common_first = min(intersection_timestamps)
    common_last = max(intersection_timestamps)
    panel_frame = _inner_exact_panel(entries, frames)
    component_summaries = [
        _component_summary(
            entry,
            audit,
            timestamp_sets[entry.dataset_id],
            union_timestamps,
            intersection_timestamps,
            common_first,
            common_last,
        )
        for entry, audit in audited
    ]
    alignment_summary = {
        "union_bar_count": len(union_timestamps),
        "intersection_bar_count": len(intersection_timestamps),
        "coverage_rate": round(len(intersection_timestamps) / len(union_timestamps), 12),
        "common_first_timestamp": pd.Timestamp(common_first).isoformat(),
        "common_last_timestamp": pd.Timestamp(common_last).isoformat(),
    }
    panel_sha256 = _panel_sha256(spec, entries, component_summaries, panel_frame)
    report: dict[str, Any] = {
        "schema_version": PANEL_SCHEMA_VERSION,
        "panel_id": spec.panel_id,
        "panel_sha256": panel_sha256,
        "alignment": spec.alignment,
        "timeframe": entries[0].timeframe,
        "symbols": [entry.symbol for entry in entries],
        "dataset_ids": [entry.dataset_id for entry in entries],
        "timestamp_semantics": {
            "status": "unverified",
            "note": (
                "Exact UTC values are aligned, but the registry does not prove that every source uses the same "
                "bar-open or bar-close timestamp convention."
            ),
        },
        "alignment_summary": alignment_summary,
        "datasets": component_summaries,
        "research_status": "data_alignment_evidence_only",
        "readiness_changed": False,
        "automatic_factor_approval": False,
        "warnings": [
            "timestamp_semantics_unverified",
            "inner_join_discards_non_common_history",
            "no_factor_or_profitability_conclusion",
            "no_automatic_readiness_upgrade",
        ],
    }
    if export_path is not None:
        write_json_atomically(export_path, report)
    return DatasetPanelResult(frame=panel_frame, report=report)


def format_dataset_panel_audit(result: DatasetPanelResult) -> str:
    report = result.report
    alignment = report["alignment_summary"]
    return "\n".join(
        [
            f"dataset_panel_id: {report['panel_id']}",
            f"dataset_panel_sha256: {report['panel_sha256']}",
            f"timeframe: {report['timeframe']}",
            f"symbols: {','.join(report['symbols'])}",
            f"intersection_bar_count: {alignment['intersection_bar_count']}",
            f"union_bar_count: {alignment['union_bar_count']}",
            f"coverage_rate: {alignment['coverage_rate']}",
            f"common_first_timestamp: {alignment['common_first_timestamp']}",
            f"common_last_timestamp: {alignment['common_last_timestamp']}",
            f"timestamp_semantics: {report['timestamp_semantics']['status']}",
        ]
    )


def _parse_panel_spec(raw: object) -> DatasetPanelSpec:
    if not isinstance(raw, dict):
        raise ValueError("dataset panel entries must be mappings")
    panel_id = raw.get("panel_id")
    if not isinstance(panel_id, str) or _PANEL_ID_PATTERN.fullmatch(panel_id.strip()) is None:
        raise ValueError("dataset panel panel_id must be a lowercase identifier")
    dataset_ids = raw.get("dataset_ids")
    if not isinstance(dataset_ids, list) or len(dataset_ids) < 2:
        raise ValueError(f"dataset panel {panel_id} must contain at least two dataset_ids")
    if any(not isinstance(item, str) or not item.strip() for item in dataset_ids):
        raise ValueError(f"dataset panel {panel_id} dataset_ids must be non-empty strings")
    normalized_ids = tuple(item.strip() for item in dataset_ids)
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ValueError(f"dataset panel {panel_id} dataset_ids must be unique")
    alignment = raw.get("alignment")
    if alignment != INNER_EXACT_ALIGNMENT:
        raise ValueError(f"dataset panel {panel_id} alignment must be {INNER_EXACT_ALIGNMENT}")
    return DatasetPanelSpec(
        panel_id=panel_id.strip(),
        dataset_ids=normalized_ids,
        alignment=alignment,
    )


def _validate_entries(entries: tuple[DatasetRegistryEntry, ...]) -> None:
    symbols = [entry.symbol for entry in entries]
    if len(symbols) != len(set(symbols)):
        raise MarketDataError("dataset_panel_symbols_must_be_unique")
    timeframes = {entry.timeframe for entry in entries}
    if len(timeframes) != 1:
        raise MarketDataError("dataset_panel_timeframes_must_match")
    labels = [_symbol_label(symbol) for symbol in symbols]
    if len(labels) != len(set(labels)):
        raise MarketDataError("dataset_panel_symbol_labels_must_be_unique")


def _entry_sort_key(entry: DatasetRegistryEntry) -> tuple[str, str]:
    return entry.symbol, entry.dataset_id


def _symbol_label(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", symbol).strip("_")


def _inner_exact_panel(
    entries: tuple[DatasetRegistryEntry, ...],
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    panel: pd.DataFrame | None = None
    columns = ["timestamp"]
    for entry in entries:
        label = _symbol_label(entry.symbol)
        renamed = frames[entry.dataset_id].rename(
            columns={column: f"{label}.{column}" for column in REQUIRED_COLUMNS if column != "timestamp"}
        )
        selected_columns = ["timestamp"] + [f"{label}.{column}" for column in REQUIRED_COLUMNS[1:]]
        columns.extend(selected_columns[1:])
        panel = (
            renamed[selected_columns]
            if panel is None
            else panel.merge(renamed[selected_columns], on="timestamp", how="inner", validate="one_to_one")
        )
    assert panel is not None
    return panel[columns].sort_values("timestamp", kind="stable").reset_index(drop=True)


def _component_summary(
    entry: DatasetRegistryEntry,
    audit: DatasetAudit,
    timestamps: set[pd.Timestamp],
    union_timestamps: set[pd.Timestamp],
    intersection_timestamps: set[pd.Timestamp],
    common_first: pd.Timestamp,
    common_last: pd.Timestamp,
) -> dict[str, Any]:
    union_inside = {
        timestamp
        for timestamp in union_timestamps
        if common_first <= timestamp <= common_last
    }
    timestamps_inside = {
        timestamp
        for timestamp in timestamps
        if common_first <= timestamp <= common_last
    }
    return {
        "dataset_id": entry.dataset_id,
        "symbol": entry.symbol,
        "timeframe": entry.timeframe,
        "provenance": audit.declared["source"],
        "raw_sha256": audit.raw_sha256,
        "canonical_sha256": audit.canonical_sha256,
        "source_bar_count": len(timestamps),
        "aligned_bar_count": len(intersection_timestamps),
        "dropped_before_common_start": sum(timestamp < common_first for timestamp in timestamps),
        "dropped_after_common_end": sum(timestamp > common_last for timestamp in timestamps),
        "dropped_inside_common_window": len(timestamps_inside - intersection_timestamps),
        "missing_inside_common_window": len(union_inside - timestamps_inside),
        "quality": {
            "valid": audit.observed.valid,
            "duplicate_timestamp_count": audit.observed.duplicate_timestamp_count,
            "time_gap_count": audit.observed.time_gap_count,
            "missing_bar_count": audit.observed.missing_bar_count,
        },
    }


def _panel_sha256(
    spec: DatasetPanelSpec,
    entries: tuple[DatasetRegistryEntry, ...],
    component_summaries: list[dict[str, Any]],
    panel: pd.DataFrame,
) -> str:
    metadata = {
        "schema_version": PANEL_SCHEMA_VERSION,
        "panel_id": spec.panel_id,
        "timeframe": entries[0].timeframe,
        "alignment": spec.alignment,
        "components": [
            {
                "dataset_id": summary["dataset_id"],
                "symbol": summary["symbol"],
                "canonical_sha256": summary["canonical_sha256"],
            }
            for summary in component_summaries
        ],
    }
    canonical_panel = panel.copy()
    canonical_panel["timestamp"] = pd.to_datetime(canonical_panel["timestamp"], utc=True).map(
        lambda value: value.isoformat()
    )
    serialized_metadata = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    serialized_panel = canonical_panel.to_csv(index=False, lineterminator="\n", float_format="%.15g")
    return hashlib.sha256(f"{serialized_metadata}\n{serialized_panel}".encode("utf-8")).hexdigest()
