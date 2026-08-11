from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import yaml

from crypto_bot.market.csv_data import REQUIRED_COLUMNS
from crypto_bot.market.data_quality import DataQualityReport, validate_ohlcv_csv
from crypto_bot.market.realtime_gate import timeframe_duration


REGISTRY_SCHEMA_VERSION = 1
SOURCE_STATUSES = {"verified", "partial", "unknown"}
_DATASET_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")


@dataclass(frozen=True)
class DatasetRegistryEntry:
    dataset_id: str
    symbol: str
    timeframe: str
    declared_path: str
    resolved_path: Path
    source_status: str
    source_evidence: tuple[str, ...]
    source_details: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)

    def declared_metadata(self) -> dict[str, Any]:
        return {
            "path": self.declared_path,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source": {
                "status": self.source_status,
                "evidence": list(self.source_evidence),
                **self.source_details,
            },
            "expected": self.expected,
            "quality": {"validator": "validate_ohlcv_csv"},
        }


@dataclass(frozen=True)
class DatasetRegistry:
    registry_path: Path
    root: Path
    entries: tuple[DatasetRegistryEntry, ...]

    def get(self, dataset_id: str) -> DatasetRegistryEntry:
        for entry in self.entries:
            if entry.dataset_id == dataset_id:
                return entry
        raise ValueError(f"dataset_id not found in registry: {dataset_id}")


@dataclass(frozen=True)
class DatasetAudit:
    dataset_id: str
    declared: dict[str, Any]
    observed: DataQualityReport
    raw_sha256: str | None
    canonical_sha256: str | None
    declared_vs_observed: dict[str, Any]
    valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "declared": self.declared,
            "observed": self.observed.to_dict(),
            "hashes": {
                "raw_sha256": self.raw_sha256,
                "canonical_sha256": self.canonical_sha256,
            },
            "declared_vs_observed": self.declared_vs_observed,
            "valid": self.valid,
        }


def load_dataset_registry(path: str | Path) -> DatasetRegistry:
    registry_path = Path(path).resolve()
    if not registry_path.is_file():
        raise FileNotFoundError(f"dataset registry not found: {registry_path}")
    try:
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"dataset registry YAML is invalid: {registry_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("dataset registry must be a mapping")
    if raw.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"dataset registry schema_version must be {REGISTRY_SCHEMA_VERSION}")
    raw_entries = raw.get("datasets")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("dataset registry datasets must contain at least one entry")

    root = registry_path.parent.resolve()
    entries = tuple(_parse_entry(item, root) for item in raw_entries)
    identifiers = [entry.dataset_id for entry in entries]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("dataset registry dataset_id values must be unique")
    return DatasetRegistry(registry_path=registry_path, root=root, entries=entries)


def audit_dataset(entry: DatasetRegistryEntry) -> DatasetAudit:
    quality = validate_ohlcv_csv(entry.resolved_path, entry.timeframe)
    raw_sha256 = _raw_sha256(entry.resolved_path) if entry.resolved_path.is_file() else None
    canonical_sha256 = canonical_ohlcv_sha256(entry.resolved_path) if quality.columns_ok else None
    observed_values = {
        "bar_count": quality.bar_count,
        "first_timestamp": quality.start_time,
        "last_timestamp": quality.end_time,
        "raw_sha256": raw_sha256,
        "canonical_sha256": canonical_sha256,
    }
    mismatches = [
        {
            "field": key,
            "declared": declared_value,
            "observed": observed_values.get(key),
        }
        for key, declared_value in entry.expected.items()
        if observed_values.get(key) != declared_value
    ]
    comparison = {
        "matches": not mismatches,
        "mismatches": mismatches,
    }
    return DatasetAudit(
        dataset_id=entry.dataset_id,
        declared=entry.declared_metadata(),
        observed=quality,
        raw_sha256=raw_sha256,
        canonical_sha256=canonical_sha256,
        declared_vs_observed=comparison,
        valid=quality.valid and canonical_sha256 is not None and not mismatches,
    )


def audit_dataset_registry(
    registry_path: str | Path,
    export_path: str | Path | None = None,
) -> dict[str, Any]:
    registry = load_dataset_registry(registry_path)
    audits = [audit_dataset(entry) for entry in registry.entries]
    payload = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "registry_path": str(registry.registry_path),
        "valid": all(audit.valid for audit in audits),
        "dataset_count": len(audits),
        "datasets": [audit.to_dict() for audit in audits],
    }
    if export_path is not None:
        write_json_atomically(export_path, payload)
    return payload


def normalize_dataset_registry_audit_paths(
    payload: dict[str, Any],
    repo_root: str | Path,
) -> dict[str, Any]:
    """Return an audit payload with host-specific filesystem paths normalized.

    Frozen audit reports are generated on one host and replayed on another in
    CI.  The quality report includes absolute ``csv_path`` values and the
    top-level audit includes an absolute ``registry_path``; those values are
    provenance metadata, not content identity.  Normalize them to stable
    repository-relative paths before comparing a frozen report with a replay.
    """

    normalized = deepcopy(payload)
    root = Path(repo_root).resolve()
    registry_path = normalized.get("registry_path")
    if isinstance(registry_path, str):
        normalized["registry_path"] = _portable_audit_path(registry_path, root)
    for dataset in normalized.get("datasets", []):
        if not isinstance(dataset, dict):
            continue
        observed = dataset.get("observed")
        if isinstance(observed, dict) and isinstance(observed.get("csv_path"), str):
            observed["csv_path"] = _portable_audit_path(observed["csv_path"], root)
    return normalized


def _portable_audit_path(value: str, repo_root: Path) -> str:
    text = value.replace("\\", "/")
    root_text = repo_root.as_posix().rstrip("/")
    if text == root_text:
        return "."
    prefix = f"{root_text}/"
    if text.startswith(prefix):
        return text[len(prefix) :]

    # A frozen Windows report can carry a different drive/root than the
    # current runner.  Recognize repository-owned path anchors without
    # guessing at arbitrary external paths.
    for anchor in ("data/", "downloads/", "reports/", "promoted-registry", "promoted-panels"):
        index = text.lower().find(anchor.lower())
        if index >= 0:
            return text[index:]
    return text


def format_dataset_registry_audit(payload: dict[str, Any]) -> str:
    lines = [
        f"dataset_registry_valid: {str(bool(payload['valid'])).lower()}",
        f"registry_path: {payload['registry_path']}",
        f"dataset_count: {payload['dataset_count']}",
    ]
    for dataset in payload["datasets"]:
        observed = dataset["observed"]
        lines.append(
            "dataset: "
            f"{dataset['dataset_id']} valid={str(bool(dataset['valid'])).lower()} "
            f"bars={observed['bar_count']} duplicates={observed['duplicate_timestamp_count']} "
            f"gaps={observed['time_gap_count']} missing_bars={observed['missing_bar_count']}"
        )
    return "\n".join(lines)


def _parse_entry(raw: object, root: Path) -> DatasetRegistryEntry:
    if not isinstance(raw, dict):
        raise ValueError("dataset registry entries must be mappings")
    dataset_id = _required_string(raw, "dataset_id")
    if _DATASET_ID_PATTERN.fullmatch(dataset_id) is None:
        raise ValueError(f"invalid dataset_id: {dataset_id}")
    symbol = _required_string(raw, "symbol")
    if "/" not in symbol:
        raise ValueError(f"invalid dataset symbol: {symbol}")
    timeframe = _required_string(raw, "timeframe").lower()
    timeframe_duration(timeframe)
    declared_path = _required_string(raw, "path")
    resolved_path = _resolve_inside_root(root, declared_path, field_name="path")

    source = raw.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"dataset {dataset_id} source must be a mapping")
    source_status = _required_string(source, "status")
    if source_status not in SOURCE_STATUSES:
        raise ValueError(f"dataset {dataset_id} source.status must be one of {sorted(SOURCE_STATUSES)}")
    evidence = source.get("evidence", [])
    if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
        raise ValueError(f"dataset {dataset_id} source.evidence must be a list of paths")
    if source_status != "unknown" and not evidence:
        raise ValueError(f"dataset {dataset_id} source.evidence is required for {source_status} provenance")
    source_evidence = tuple(item.strip() for item in evidence)
    for evidence_path in source_evidence:
        resolved_evidence = _resolve_inside_root(root, evidence_path, field_name="source.evidence")
        if not resolved_evidence.is_file():
            raise ValueError(f"dataset {dataset_id} source evidence not found: {evidence_path}")
    source_details = {
        key: value
        for key, value in source.items()
        if key not in {"status", "evidence"}
    }

    quality = raw.get("quality")
    if quality != {"validator": "validate_ohlcv_csv"}:
        raise ValueError(f"dataset {dataset_id} quality.validator must be validate_ohlcv_csv")
    expected = raw.get("expected", {})
    if not isinstance(expected, dict):
        raise ValueError(f"dataset {dataset_id} expected must be a mapping")
    allowed_expected = {
        "bar_count",
        "first_timestamp",
        "last_timestamp",
        "raw_sha256",
        "canonical_sha256",
    }
    unexpected = set(expected) - allowed_expected
    if unexpected:
        raise ValueError(f"dataset {dataset_id} has unsupported expected fields: {sorted(unexpected)}")
    _validate_expected(dataset_id, expected)
    return DatasetRegistryEntry(
        dataset_id=dataset_id,
        symbol=symbol,
        timeframe=timeframe,
        declared_path=declared_path,
        resolved_path=resolved_path,
        source_status=source_status,
        source_evidence=source_evidence,
        source_details=source_details,
        expected=dict(expected),
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"dataset registry field {key} must be a non-empty string")
    return value.strip()


def _validate_expected(dataset_id: str, expected: dict[str, Any]) -> None:
    bar_count = expected.get("bar_count")
    if bar_count is not None and (not isinstance(bar_count, int) or isinstance(bar_count, bool) or bar_count < 0):
        raise ValueError(f"dataset {dataset_id} expected.bar_count must be a non-negative integer")
    for key in ("first_timestamp", "last_timestamp"):
        value = expected.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"dataset {dataset_id} expected.{key} must be a timestamp string")
    for key in ("raw_sha256", "canonical_sha256"):
        value = expected.get(key)
        if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None):
            raise ValueError(f"dataset {dataset_id} expected.{key} must be a lowercase SHA-256")


def _resolve_inside_root(root: Path, declared: str, *, field_name: str) -> Path:
    candidate = Path(declared)
    if candidate.is_absolute():
        raise ValueError(f"dataset registry {field_name} must be relative: {declared}")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"dataset registry {field_name} escapes registry root: {declared}")
    return resolved


def _raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonicalize_ohlcv_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if list(frame.columns) != REQUIRED_COLUMNS:
        raise ValueError("canonical_ohlcv_invalid_columns")
    canonical = frame[REQUIRED_COLUMNS].copy()
    canonical["timestamp"] = pd.to_datetime(canonical["timestamp"], utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        canonical[column] = pd.to_numeric(canonical[column], errors="coerce").astype(float)
    if canonical[REQUIRED_COLUMNS].isna().any().any():
        raise ValueError("canonical_ohlcv_missing_values")
    return canonical.sort_values("timestamp", kind="stable").reset_index(drop=True)


def canonical_ohlcv_sha256(path: str | Path) -> str | None:
    try:
        frame = pd.read_csv(path)
    except Exception:
        return None
    try:
        canonical = canonicalize_ohlcv_frame(frame)
    except ValueError:
        return None
    canonical["timestamp"] = canonical["timestamp"].map(lambda value: value.isoformat())
    serialized = canonical.to_csv(index=False, lineterminator="\n", float_format="%.15g")
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def write_json_atomically(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
