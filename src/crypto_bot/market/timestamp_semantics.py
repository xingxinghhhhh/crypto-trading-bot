from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import pandas as pd
import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_panel import build_dataset_panel, load_dataset_panel_config
from crypto_bot.market.dataset_registry import audit_dataset, load_dataset_registry, write_json_atomically
from crypto_bot.market.realtime_gate import timeframe_duration


TIMESTAMP_SEMANTICS_SCHEMA_VERSION = 1
OKX_TIMESTAMP_PROBE_SCHEMA_VERSION = 1
OKX_CANDLES_ENDPOINT = "https://www.okx.com/api/v5/market/candles"
OKX_PROBE_LIMIT = 5
DEFAULT_OKX_CONTRACT_EVIDENCE = Path("docs/evidence/okx_candles_contract_v1.json")

_MEANINGS = {"bar_open_time", "bar_close_time", "unverified"}
_LINEAGE_COVERAGE = {"complete", "partial", "none"}
_VERIFIED_STATUSES = {"verified_open_time", "verified_close_time"}
_OKX_BAR_MILLISECONDS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1H": 3_600_000,
    "2H": 7_200_000,
    "4H": 14_400_000,
}
_PRODUCER_FIELDS = (
    "producer_id",
    "provider",
    "market_type",
    "timestamp_field",
    "claimed_meaning",
    "requires_probe",
    "status",
    "contract_sha256",
    "probe_sha256",
)
_DATASET_FIELDS = (
    "dataset_id",
    "symbol",
    "timeframe",
    "source_status",
    "lineage_coverage",
    "status",
    "reason",
    "first_timestamp",
    "last_timestamp",
    "raw_sha256",
    "canonical_sha256",
    "evidence_count",
    "evidence_ids",
)
_PANEL_FIELDS = (
    "panel_id",
    "timeframe",
    "panel_sha256",
    "status",
    "dataset_count",
    "dataset_ids",
    "dataset_statuses",
)


@dataclass(frozen=True)
class OkxTimestampProbeResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class TimestampSemanticsAuditResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class _Producer:
    producer_id: str
    provider: str
    market_type: str
    timestamp_field: str
    claimed_meaning: str
    requires_probe: bool
    contract_artifact: Path | None
    contract_relative_path: str | None
    contract_sha256: str | None


@dataclass(frozen=True)
class _Evidence:
    evidence_id: str
    producer_id: str
    covered_from: pd.Timestamp | None
    covered_to: pd.Timestamp | None
    artifact: Path
    relative_artifact: str
    artifact_sha256: str


@dataclass(frozen=True)
class _DatasetEvidence:
    dataset_id: str
    lineage_coverage: str
    evidence: tuple[_Evidence, ...]


@dataclass(frozen=True)
class _EvidenceConfig:
    normalized: dict[str, Any]
    producers: dict[str, _Producer]
    datasets: dict[str, _DatasetEvidence]


def capture_okx_timestamp_probe(
    inst_id: str,
    bar: str,
    output_dir: str | Path,
    *,
    contract_evidence_path: str | Path = DEFAULT_OKX_CONTRACT_EVIDENCE,
    fetcher: Callable[[str], bytes] | None = None,
    received_at: datetime | None = None,
) -> OkxTimestampProbeResult:
    if not isinstance(inst_id, str) or not inst_id or "/" in inst_id or "?" in inst_id:
        raise ValueError("OKX inst_id must be a non-empty instrument ID")
    if bar not in _OKX_BAR_MILLISECONDS:
        raise ValueError(f"unsupported OKX probe bar: {bar}")
    contract_path = Path(contract_evidence_path)
    contract, contract_sha256 = _load_okx_contract(contract_path)
    params = (("instId", inst_id), ("bar", bar), ("limit", str(OKX_PROBE_LIMIT)))
    request_url = f"{OKX_CANDLES_ENDPOINT}?{urlencode(params)}"
    try:
        response_bytes = (fetcher or _fetch_public_bytes)(request_url)
    except Exception as exc:
        raise MarketDataError(f"okx_timestamp_probe_fetch_failed:{exc}") from exc
    captured_at = received_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() != timezone.utc.utcoffset(captured_at):
        raise ValueError("received_at must be timezone-aware UTC")
    response, rows = _validate_okx_response(
        response_bytes,
        bar=bar,
        received_at=captured_at,
    )
    response_sha256 = hashlib.sha256(response_bytes).hexdigest()
    closed_rows = [row for row in rows if row[8] == "1"]
    mapped = [
        {
            "ts": row[0],
            "normalized_timestamp": pd.Timestamp(int(row[0]), unit="ms", tz="UTC").isoformat(),
        }
        for row in closed_rows
    ]
    identity = {
        "schema_version": OKX_TIMESTAMP_PROBE_SCHEMA_VERSION,
        "endpoint": OKX_CANDLES_ENDPOINT,
        "request_params": {key: value for key, value in params},
        "received_at": captured_at.isoformat(),
        "contract_sha256": contract_sha256,
        "response_sha256": response_sha256,
        "response_code": response["code"],
        "response_message": response["msg"],
        "response_row_count": len(rows),
        "closed_row_count": len(closed_rows),
        "timestamp_mapping": mapped,
        "policies": _probe_policies(),
    }
    probe_sha256 = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stem = f"okx-timestamp-probe.{probe_sha256}"
    response_path = destination / f"{stem}.response.json"
    report_path = destination / f"{stem}.json"
    report = {
        "schema_version": OKX_TIMESTAMP_PROBE_SCHEMA_VERSION,
        "probe_sha256": probe_sha256,
        "probe_status": "verified_open_time_public_probe_only",
        "endpoint": OKX_CANDLES_ENDPOINT,
        "request_params": identity["request_params"],
        "received_at": identity["received_at"],
        "contract": {
            "filename": contract_path.name,
            "sha256": contract_sha256,
            "official_url": contract["official_url"],
            "timestamp_meaning": contract["timestamp_meaning"],
        },
        "response": {
            "filename": response_path.name,
            "sha256": response_sha256,
            "code": response["code"],
            "msg": response["msg"],
            "row_count": len(rows),
            "closed_row_count": len(closed_rows),
            "order": "strictly_descending_newest_first",
        },
        "timestamp_mapping": mapped,
        "policies": _probe_policies(),
        "private_api_used": False,
        "trading_api_used": False,
        "readiness_changed": False,
    }
    _commit_bytes(response_path, response_bytes, response_sha256)
    _commit_report(report_path, report)
    return OkxTimestampProbeResult(
        report=report,
        export_paths={"report": str(report_path), "response": str(response_path)},
    )


def audit_timestamp_semantics(
    registry_path: str | Path,
    panels_config_path: str | Path,
    evidence_config_path: str | Path,
    probe_report_path: str | Path,
    output_dir: str | Path,
) -> TimestampSemanticsAuditResult:
    registry = load_dataset_registry(registry_path)
    evidence_config = _load_evidence_config(Path(evidence_config_path), registry)
    probe, probe_file_sha256 = _load_probe(Path(probe_report_path))
    producer_rows = _assess_producers(evidence_config, probe)
    producer_statuses = {row["producer_id"]: row["status"] for row in producer_rows}
    dataset_rows: list[dict[str, Any]] = []
    for entry in registry.entries:
        audit = audit_dataset(entry)
        if not audit.valid or audit.raw_sha256 is None or audit.canonical_sha256 is None:
            raise MarketDataError(f"timestamp_semantics_invalid_dataset:{entry.dataset_id}")
        configured = evidence_config.datasets[entry.dataset_id]
        _validate_dataset_evidence_membership(entry.source_evidence, configured)
        status, reason = _assess_dataset(configured, audit, producer_statuses, entry.timeframe)
        dataset_rows.append(
            {
                "dataset_id": entry.dataset_id,
                "symbol": entry.symbol,
                "timeframe": entry.timeframe,
                "source_status": entry.source_status,
                "lineage_coverage": configured.lineage_coverage,
                "status": status,
                "reason": reason,
                "first_timestamp": audit.observed.start_time,
                "last_timestamp": audit.observed.end_time,
                "raw_sha256": audit.raw_sha256,
                "canonical_sha256": audit.canonical_sha256,
                "evidence_count": len(configured.evidence),
                "evidence_ids": "|".join(item.evidence_id for item in configured.evidence),
            }
        )
    dataset_statuses = {row["dataset_id"]: row["status"] for row in dataset_rows}
    panels_config = load_dataset_panel_config(panels_config_path)
    panel_rows: list[dict[str, Any]] = []
    for spec in panels_config.specs:
        panel = build_dataset_panel(registry_path, panels_config_path, spec.panel_id)
        statuses = [dataset_statuses[dataset_id] for dataset_id in spec.dataset_ids]
        panel_status = _aggregate_panel_status(statuses)
        panel_rows.append(
            {
                "panel_id": spec.panel_id,
                "timeframe": panel.report["timeframe"],
                "panel_sha256": panel.report["panel_sha256"],
                "status": panel_status,
                "dataset_count": len(spec.dataset_ids),
                "dataset_ids": "|".join(spec.dataset_ids),
                "dataset_statuses": "|".join(statuses),
            }
        )
    producer_rows.sort(key=lambda row: str(row["producer_id"]))
    dataset_rows.sort(key=lambda row: str(row["dataset_id"]))
    panel_rows.sort(key=lambda row: str(row["panel_id"]))
    producers_bytes = _serialize_rows(producer_rows, _PRODUCER_FIELDS)
    datasets_bytes = _serialize_rows(dataset_rows, _DATASET_FIELDS)
    panels_bytes = _serialize_rows(panel_rows, _PANEL_FIELDS)
    producer_sha = hashlib.sha256(producers_bytes).hexdigest()
    dataset_sha = hashlib.sha256(datasets_bytes).hexdigest()
    panel_sha = hashlib.sha256(panels_bytes).hexdigest()
    identity = {
        "schema_version": TIMESTAMP_SEMANTICS_SCHEMA_VERSION,
        "policy_version": evidence_config.normalized["policy_version"],
        "evidence_config_sha256": hashlib.sha256(
            _canonical_json_bytes(evidence_config.normalized)
        ).hexdigest(),
        "probe_sha256": probe["probe_sha256"],
        "probe_report_sha256": probe_file_sha256,
        "datasets": [
            {
                "dataset_id": row["dataset_id"],
                "raw_sha256": row["raw_sha256"],
                "canonical_sha256": row["canonical_sha256"],
            }
            for row in dataset_rows
        ],
        "panels": [
            {
                "panel_id": row["panel_id"],
                "panel_sha256": row["panel_sha256"],
                "dataset_ids": row["dataset_ids"],
            }
            for row in panel_rows
        ],
        "evidence_artifacts": sorted(
            (
                {
                    item.relative_artifact: item.artifact_sha256
                    for dataset in evidence_config.datasets.values()
                    for item in dataset.evidence
                }
                | {
                    producer.contract_relative_path: producer.contract_sha256
                    for producer in evidence_config.producers.values()
                    if producer.contract_relative_path is not None
                    and producer.contract_sha256 is not None
                }
            ).items()
        ),
        "policies": _audit_policies(),
        "artifacts": {
            "producers_sha256": producer_sha,
            "datasets_sha256": dataset_sha,
            "panels_sha256": panel_sha,
        },
    }
    semantics_sha256 = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stem = f"timestamp-semantics.{semantics_sha256}"
    producers_path = destination / f"{stem}.producers.csv"
    datasets_path = destination / f"{stem}.datasets.csv"
    panels_path = destination / f"{stem}.panels.csv"
    report_path = destination / f"{stem}.json"
    report = {
        "schema_version": TIMESTAMP_SEMANTICS_SCHEMA_VERSION,
        "semantics_sha256": semantics_sha256,
        "assessment_status": "independent_timestamp_semantics_evidence_only",
        "probe": {
            "probe_sha256": probe["probe_sha256"],
            "report_filename": Path(probe_report_path).name,
            "report_sha256": probe_file_sha256,
        },
        "policies": _audit_policies(),
        "producers": producer_rows,
        "datasets": dataset_rows,
        "panels": panel_rows,
        "artifacts": {
            "producers": {
                "filename": producers_path.name,
                "sha256": producer_sha,
                "row_count": len(producer_rows),
            },
            "datasets": {
                "filename": datasets_path.name,
                "sha256": dataset_sha,
                "row_count": len(dataset_rows),
            },
            "panels": {
                "filename": panels_path.name,
                "sha256": panel_sha,
                "row_count": len(panel_rows),
            },
        },
        "warnings": [
            "assessment_does_not_modify_existing_dataset_or_panel_identity",
            "producer_probe_does_not_upgrade_unattributed_historical_datasets",
            "existing_research_artifacts_remain_timestamp_semantics_unverified",
            "no_profitability_or_execution_claim",
            "no_automatic_readiness_upgrade",
        ],
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    _commit_bytes(producers_path, producers_bytes, producer_sha)
    _commit_bytes(datasets_path, datasets_bytes, dataset_sha)
    _commit_bytes(panels_path, panels_bytes, panel_sha)
    _commit_report(report_path, report)
    return TimestampSemanticsAuditResult(
        report=report,
        export_paths={
            "report": str(report_path),
            "producers": str(producers_path),
            "datasets": str(datasets_path),
            "panels": str(panels_path),
        },
    )


def format_okx_timestamp_probe(result: OkxTimestampProbeResult) -> str:
    report = result.report
    return "\n".join(
        [
            f"probe_status: {report['probe_status']}",
            f"probe_sha256: {report['probe_sha256']}",
            f"response_row_count: {report['response']['row_count']}",
            f"closed_row_count: {report['response']['closed_row_count']}",
            "private_api_used: false",
            "trading_api_used: false",
        ]
    )


def format_timestamp_semantics_audit(result: TimestampSemanticsAuditResult) -> str:
    report = result.report
    return "\n".join(
        [
            f"assessment_status: {report['assessment_status']}",
            f"semantics_sha256: {report['semantics_sha256']}",
            f"producer_count: {len(report['producers'])}",
            f"dataset_count: {len(report['datasets'])}",
            f"panel_count: {len(report['panels'])}",
            "readiness_changed: false",
            "automatic_factor_approval: false",
        ]
    )


def _fetch_public_bytes(url: str) -> bytes:
    request = Request(  # noqa: S310 - fixed HTTPS host and public endpoint above.
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "crypto-trading-bot/0.1 timestamp-semantics-audit",
        },
        method="GET",
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS host and path.
        if response.status != 200:
            raise MarketDataError(f"okx_timestamp_probe_http_status:{response.status}")
        return response.read()


def _load_okx_contract(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(f"OKX contract evidence not found: {path}")
    payload_bytes = path.read_bytes()
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("okx_timestamp_probe_invalid_contract_json") from exc
    if not isinstance(payload, dict):
        raise MarketDataError("okx_timestamp_probe_contract_must_be_mapping")
    expected = {
        "provider": "okx",
        "market_type": "spot",
        "endpoint": OKX_CANDLES_ENDPOINT,
        "http_method": "GET",
        "authentication": "none_public_market_data",
        "timestamp_field": "ts",
        "timestamp_unit": "unix_milliseconds",
        "timestamp_meaning": "bar_open_time",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise MarketDataError("okx_timestamp_probe_contract_mismatch")
    if payload.get("response_shape") != [
        "ts",
        "o",
        "h",
        "l",
        "c",
        "vol",
        "volCcy",
        "volCcyQuote",
        "confirm",
    ]:
        raise MarketDataError("okx_timestamp_probe_contract_response_shape_mismatch")
    return payload, hashlib.sha256(payload_bytes).hexdigest()


def _validate_okx_response(
    payload_bytes: bytes,
    *,
    bar: str,
    received_at: datetime,
) -> tuple[dict[str, Any], list[list[str]]]:
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("okx_timestamp_probe_invalid_response_json") from exc
    if not isinstance(payload, dict) or payload.get("code") != "0" or payload.get("msg") != "":
        raise MarketDataError("okx_timestamp_probe_unsuccessful_response")
    raw_rows = payload.get("data")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise MarketDataError("okx_timestamp_probe_empty_data")
    rows: list[list[str]] = []
    timestamps: list[int] = []
    duration_ms = _OKX_BAR_MILLISECONDS[bar]
    received_ms = int(received_at.timestamp() * 1000)
    closed_count = 0
    for raw in raw_rows:
        if not isinstance(raw, list) or len(raw) != 9 or any(not isinstance(value, str) for value in raw):
            raise MarketDataError("okx_timestamp_probe_invalid_row_shape")
        try:
            timestamp = int(raw[0])
            numeric = [float(value) for value in raw[1:8]]
        except ValueError as exc:
            raise MarketDataError("okx_timestamp_probe_invalid_row_value") from exc
        if timestamp <= 0 or str(timestamp) != raw[0] or any(not math_is_finite(value) for value in numeric):
            raise MarketDataError("okx_timestamp_probe_invalid_row_value")
        if timestamp % duration_ms != 0:
            raise MarketDataError("okx_timestamp_probe_off_grid_timestamp")
        if raw[8] not in {"0", "1"}:
            raise MarketDataError("okx_timestamp_probe_invalid_confirm")
        if raw[8] == "1":
            closed_count += 1
            if timestamp + duration_ms > received_ms + 5_000:
                raise MarketDataError("okx_timestamp_probe_confirmed_bar_not_closed")
        rows.append(raw)
        timestamps.append(timestamp)
    if len(timestamps) != len(set(timestamps)) or any(
        left <= right for left, right in zip(timestamps, timestamps[1:])
    ):
        raise MarketDataError("okx_timestamp_probe_not_strictly_descending")
    if closed_count == 0:
        raise MarketDataError("okx_timestamp_probe_no_closed_rows")
    return payload, rows


def math_is_finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}


def _probe_policies() -> dict[str, Any]:
    return {
        "endpoint_scope": "okx_public_market_candles_only",
        "fixed_limit": OKX_PROBE_LIMIT,
        "response_shape": "nine_string_fields",
        "response_order": "strictly_descending_newest_first",
        "timestamp_unit": "unix_milliseconds",
        "timestamp_meaning": "bar_open_time_from_official_contract",
        "closed_candle": "confirm_equals_one_and_open_plus_duration_not_after_received_at",
        "grid": "timestamp_modulo_bar_duration_equals_zero",
        "randomness": "none",
    }


def _audit_policies() -> dict[str, Any]:
    return {
        "network_access": "forbidden_offline_probe_consumption_only",
        "verified_dataset": "complete_lineage_coverage_and_consistent_verified_meaning",
        "partial_dataset": "partial_unverified",
        "no_evidence_dataset": "unknown",
        "conflicting_meanings": "fail_closed",
        "grid_inference": "never_upgrades_semantics",
        "panel_verified": "all_constituents_verified_with_same_meaning",
        "producer_probe_upgrade_scope": "producer_only_never_historical_dataset",
        "existing_hashes": "immutable_not_recomputed_as_semantics_identity",
        "randomness": "none",
    }


def _load_evidence_config(path: Path, registry: Any) -> _EvidenceConfig:
    if not path.is_file():
        raise FileNotFoundError(f"timestamp semantics evidence config not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("timestamp semantics evidence config YAML is invalid") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != TIMESTAMP_SEMANTICS_SCHEMA_VERSION:
        raise ValueError("timestamp semantics evidence config schema_version is invalid")
    if raw.get("policy_version") != 1:
        raise ValueError("timestamp semantics evidence config policy_version is invalid")
    root = path.resolve().parent
    raw_producers = raw.get("producers")
    if not isinstance(raw_producers, list) or not raw_producers:
        raise ValueError("timestamp semantics producers must be a non-empty list")
    producers: dict[str, _Producer] = {}
    for item in raw_producers:
        if not isinstance(item, dict):
            raise ValueError("timestamp semantics producer must be a mapping")
        producer_id = _required_string(item, "producer_id")
        if producer_id in producers:
            raise ValueError("timestamp semantics producer IDs must be unique")
        claimed = _required_string(item, "claimed_meaning")
        if claimed not in _MEANINGS:
            raise ValueError("timestamp semantics producer meaning is invalid")
        requires_probe = item.get("requires_probe")
        if not isinstance(requires_probe, bool):
            raise ValueError("timestamp semantics requires_probe must be boolean")
        contract_relative = item.get("contract_artifact")
        contract_sha = item.get("contract_sha256")
        contract_path: Path | None = None
        if contract_relative is not None or contract_sha is not None:
            if not isinstance(contract_relative, str):
                raise ValueError("timestamp semantics contract artifact must be a path")
            contract_path = _resolve_relative(root, contract_relative)
            if _sha256(contract_path) != _require_sha256(contract_sha, "contract_sha256"):
                raise MarketDataError("timestamp_semantics_contract_hash_mismatch")
        if claimed != "unverified" and contract_path is None:
            raise ValueError("verified producer meaning requires contract evidence")
        producers[producer_id] = _Producer(
            producer_id=producer_id,
            provider=_required_string(item, "provider"),
            market_type=_required_string(item, "market_type"),
            timestamp_field=_required_string(item, "timestamp_field"),
            claimed_meaning=claimed,
            requires_probe=requires_probe,
            contract_artifact=contract_path,
            contract_relative_path=contract_relative,
            contract_sha256=contract_sha,
        )
    raw_datasets = raw.get("datasets")
    if not isinstance(raw_datasets, list):
        raise ValueError("timestamp semantics datasets must be a list")
    datasets: dict[str, _DatasetEvidence] = {}
    evidence_ids: set[str] = set()
    for item in raw_datasets:
        if not isinstance(item, dict):
            raise ValueError("timestamp semantics dataset must be a mapping")
        dataset_id = _required_string(item, "dataset_id")
        if dataset_id in datasets:
            raise ValueError("timestamp semantics dataset IDs must be unique")
        coverage = _required_string(item, "lineage_coverage")
        if coverage not in _LINEAGE_COVERAGE:
            raise ValueError("timestamp semantics lineage coverage is invalid")
        raw_evidence = item.get("evidence")
        if not isinstance(raw_evidence, list):
            raise ValueError("timestamp semantics evidence must be a list")
        parsed: list[_Evidence] = []
        for raw_item in raw_evidence:
            if not isinstance(raw_item, dict):
                raise ValueError("timestamp semantics evidence entry must be a mapping")
            evidence_id = _required_string(raw_item, "evidence_id")
            if evidence_id in evidence_ids:
                raise ValueError("timestamp semantics evidence IDs must be globally unique")
            evidence_ids.add(evidence_id)
            producer_id = _required_string(raw_item, "producer_id")
            if producer_id not in producers:
                raise ValueError("timestamp semantics evidence references unknown producer")
            relative = _required_string(raw_item, "repo_relative_artifact")
            artifact = _resolve_relative(root, relative)
            artifact_sha = _require_sha256(raw_item.get("artifact_sha256"), "artifact_sha256")
            if _sha256(artifact) != artifact_sha:
                raise MarketDataError("timestamp_semantics_evidence_hash_mismatch")
            covered_from = _optional_timestamp(raw_item.get("covered_from"), "covered_from")
            covered_to = _optional_timestamp(raw_item.get("covered_to"), "covered_to")
            if (covered_from is None) != (covered_to is None):
                raise ValueError("timestamp semantics coverage bounds must both be set or null")
            if covered_from is not None and covered_from > covered_to:
                raise ValueError("timestamp semantics coverage range is reversed")
            parsed.append(
                _Evidence(
                    evidence_id,
                    producer_id,
                    covered_from,
                    covered_to,
                    artifact,
                    relative,
                    artifact_sha,
                )
            )
        if coverage == "none" and parsed:
            raise ValueError("lineage coverage none cannot contain evidence")
        if coverage != "none" and not parsed:
            raise ValueError("lineage coverage requires evidence")
        datasets[dataset_id] = _DatasetEvidence(dataset_id, coverage, tuple(parsed))
    expected_ids = {entry.dataset_id for entry in registry.entries}
    if set(datasets) != expected_ids:
        raise ValueError("timestamp semantics config must assess every registry dataset exactly once")
    normalized = json.loads(json.dumps(raw, sort_keys=True))
    return _EvidenceConfig(normalized, producers, datasets)


def _load_probe(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(f"timestamp probe report not found: {path}")
    payload_bytes = path.read_bytes()
    try:
        report = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("timestamp_semantics_invalid_probe_json") from exc
    if not isinstance(report, dict) or report.get("schema_version") != OKX_TIMESTAMP_PROBE_SCHEMA_VERSION:
        raise MarketDataError("timestamp_semantics_invalid_probe_schema")
    probe_sha = _require_sha256(report.get("probe_sha256"), "probe_sha256")
    if path.name != f"okx-timestamp-probe.{probe_sha}.json":
        raise MarketDataError("timestamp_semantics_probe_filename_mismatch")
    response = report.get("response")
    if not isinstance(response, dict):
        raise MarketDataError("timestamp_semantics_invalid_probe_response")
    response_path = _sibling_artifact(path, response.get("filename"))
    if _sha256(response_path) != _require_sha256(response.get("sha256"), "response_sha256"):
        raise MarketDataError("timestamp_semantics_probe_response_hash_mismatch")
    identity = {
        "schema_version": report["schema_version"],
        "endpoint": report.get("endpoint"),
        "request_params": report.get("request_params"),
        "received_at": report.get("received_at"),
        "contract_sha256": report.get("contract", {}).get("sha256"),
        "response_sha256": response.get("sha256"),
        "response_code": response.get("code"),
        "response_message": response.get("msg"),
        "response_row_count": response.get("row_count"),
        "closed_row_count": response.get("closed_row_count"),
        "timestamp_mapping": report.get("timestamp_mapping"),
        "policies": report.get("policies"),
    }
    if hashlib.sha256(_canonical_json_bytes(identity)).hexdigest() != probe_sha:
        raise MarketDataError("timestamp_semantics_probe_identity_mismatch")
    if report.get("private_api_used") is not False or report.get("trading_api_used") is not False:
        raise MarketDataError("timestamp_semantics_unsafe_probe")
    return report, hashlib.sha256(payload_bytes).hexdigest()


def _assess_producers(config: _EvidenceConfig, probe: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for producer in config.producers.values():
        status = "unknown"
        probe_sha: str | None = None
        if producer.claimed_meaning != "unverified":
            status = f"verified_{producer.claimed_meaning.removeprefix('bar_')}"
        if producer.requires_probe:
            if producer.provider != "okx" or producer.claimed_meaning != "bar_open_time":
                raise MarketDataError("timestamp_semantics_invalid_probe_producer")
            if probe.get("probe_status") != "verified_open_time_public_probe_only":
                raise MarketDataError("timestamp_semantics_unverified_probe")
            if probe.get("contract", {}).get("sha256") != producer.contract_sha256:
                raise MarketDataError("timestamp_semantics_probe_contract_mismatch")
            status = "verified_open_time"
            probe_sha = probe["probe_sha256"]
        rows.append(
            {
                "producer_id": producer.producer_id,
                "provider": producer.provider,
                "market_type": producer.market_type,
                "timestamp_field": producer.timestamp_field,
                "claimed_meaning": producer.claimed_meaning,
                "requires_probe": producer.requires_probe,
                "status": status,
                "contract_sha256": producer.contract_sha256 or "",
                "probe_sha256": probe_sha or "",
            }
        )
    return rows


def _validate_dataset_evidence_membership(
    registry_evidence: tuple[str, ...], configured: _DatasetEvidence
) -> None:
    allowed = set(registry_evidence)
    for evidence in configured.evidence:
        if evidence.relative_artifact not in allowed:
            raise MarketDataError("timestamp_semantics_evidence_not_registered_for_dataset")


def _assess_dataset(
    configured: _DatasetEvidence,
    audit: Any,
    producer_statuses: dict[str, str],
    timeframe: str,
) -> tuple[str, str]:
    if configured.lineage_coverage == "none":
        return "unknown", "no_applicable_semantics_evidence"
    if configured.lineage_coverage == "partial":
        return "partial_unverified", "lineage_or_time_range_not_fully_covered"
    start = pd.Timestamp(audit.observed.start_time)
    end = pd.Timestamp(audit.observed.end_time)
    intervals = sorted(
        (
            (item.covered_from, item.covered_to)
            for item in configured.evidence
            if item.covered_from is not None and item.covered_to is not None
        ),
        key=lambda item: item[0],
    )
    if len(intervals) != len(configured.evidence) or not _covers_range(
        intervals, start, end, timeframe
    ):
        return "partial_unverified", "declared_complete_lineage_has_coverage_gap"
    meanings = {
        producer_statuses[item.producer_id]
        for item in configured.evidence
        if producer_statuses[item.producer_id] in _VERIFIED_STATUSES
    }
    if len(meanings) > 1:
        raise MarketDataError("timestamp_semantics_conflicting_dataset_meanings")
    if len(meanings) == 1 and all(
        producer_statuses[item.producer_id] in _VERIFIED_STATUSES for item in configured.evidence
    ):
        return next(iter(meanings)), "complete_lineage_with_consistent_verified_meaning"
    return "partial_unverified", "complete_lineage_but_semantics_not_verified"


def _covers_range(
    intervals: list[tuple[pd.Timestamp | None, pd.Timestamp | None]],
    start: pd.Timestamp,
    end: pd.Timestamp,
    timeframe: str,
) -> bool:
    first_start, first_end = intervals[0]
    assert first_start is not None and first_end is not None
    if first_start > start:
        return False
    covered_to = first_end
    step = pd.Timedelta(timeframe_duration(timeframe))
    for next_start, next_end in intervals[1:]:
        assert next_start is not None and next_end is not None
        if next_start > covered_to + step:
            return False
        covered_to = max(covered_to, next_end)
    return covered_to >= end


def _aggregate_panel_status(statuses: list[str]) -> str:
    verified = {status for status in statuses if status in _VERIFIED_STATUSES}
    if len(verified) > 1:
        raise MarketDataError("timestamp_semantics_conflicting_panel_meanings")
    if len(verified) == 1 and all(status in _VERIFIED_STATUSES for status in statuses):
        return next(iter(verified))
    return "unverified"


def _resolve_relative(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ValueError("timestamp semantics evidence paths must be relative")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("timestamp semantics evidence path is missing or escapes config root")
    return resolved


def _sibling_artifact(report_path: Path, filename: object) -> Path:
    if not isinstance(filename, str):
        raise MarketDataError("timestamp_semantics_invalid_probe_artifact_filename")
    candidate = Path(filename)
    if candidate.is_absolute() or candidate.name != filename or "/" in filename or "\\" in filename:
        raise MarketDataError("timestamp_semantics_probe_artifact_path_escape")
    resolved = (report_path.resolve().parent / candidate).resolve()
    if resolved.parent != report_path.resolve().parent or not resolved.is_file():
        raise MarketDataError("timestamp_semantics_probe_artifact_missing")
    return resolved


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"timestamp semantics field {key} must be a non-empty string")
    return value.strip()


def _optional_timestamp(value: object, name: str) -> pd.Timestamp | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"timestamp semantics {name} must be a UTC timestamp or null")
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"timestamp semantics {name} must be a UTC timestamp or null")
    return pd.Timestamp(parsed)


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"timestamp semantics {name} must be a lowercase SHA-256")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _serialize_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _format_csv_value(row[field]) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _commit_bytes(path: Path, payload: bytes, expected_sha256: str) -> None:
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise MarketDataError(f"content_addressed_artifact_collision:{path.name}")
        return
    temporary = path.parent / f".{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _commit_report(path: Path, report: dict[str, Any]) -> None:
    expected = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if path.exists():
        if path.read_bytes() != expected:
            raise MarketDataError(f"content_addressed_artifact_collision:{path.name}")
        return
    write_json_atomically(path, report)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _format_csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    return value
