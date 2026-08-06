from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import pandas as pd
import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.data_quality import validate_ohlcv_csv
from crypto_bot.market.dataset_registry import (
    audit_dataset,
    canonical_ohlcv_sha256,
    load_dataset_registry,
    write_json_atomically,
)


INTAKE_POLICY_SCHEMA_VERSION = 1
OKX_UNIVERSE_CAPTURE_SCHEMA_VERSION = 1
OKX_UNIVERSE_AUDIT_SCHEMA_VERSION = 1
OKX_INSTRUMENTS_ENDPOINT = "https://www.okx.com/api/v5/public/instruments"
OKX_HISTORY_ENDPOINT = "https://www.okx.com/api/v5/market/history-candles"
OKX_HISTORY_LIMIT = 300
FOUR_HOURS_MS = 14_400_000
SNAPSHOT_SAFETY_LAG_MS = 5_000
DEFAULT_CONTRACT_EVIDENCE = Path(
    "docs/evidence/okx_public_instruments_history_contract_v1.json"
)

_HISTORY_FIELDS = (
    "ts",
    "o",
    "h",
    "l",
    "c",
    "vol",
    "volCcy",
    "volCcyQuote",
    "confirm",
)
_ELIGIBILITY_FIELDS = (
    "inst_id",
    "base_ccy",
    "quote_ccy",
    "inst_type",
    "state",
    "rule_type",
    "inst_category",
    "list_time",
    "cont_td_sw_time",
    "effective_continuous_start",
    "eligible",
    "exclusion_reasons",
    "selection_sha256",
    "selected_rank",
)
_DATASET_FIELDS = (
    "dataset_id",
    "inst_id",
    "symbol",
    "timeframe",
    "first_timestamp",
    "last_timestamp",
    "bar_count",
    "history_bundle_filename",
    "history_bundle_sha256",
    "csv_filename",
    "raw_sha256",
    "canonical_sha256",
    "timestamp_semantics",
    "lineage_status",
)


@dataclass(frozen=True)
class OkxUniverseCaptureResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class OkxUniverseAuditResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class OkxUniverseValidatedCapture:
    capture_path: Path
    capture: dict[str, Any]
    policy: dict[str, Any]
    selected_inst_ids: tuple[str, ...]
    eligibility: tuple[dict[str, Any], ...]
    datasets: tuple[dict[str, Any], ...]
    candidate_datasets: tuple[dict[str, Any], ...]
    eligibility_bytes: bytes
    datasets_bytes: bytes
    candidates_bytes: bytes


@dataclass(frozen=True)
class OkxUniverseValidatedIntake:
    capture: OkxUniverseValidatedCapture
    intake_path: Path
    intake_report: dict[str, Any]
    artifact_paths: dict[str, Path]


def download_okx_public_history(
    inst_id: str,
    history_start_ms: int,
    end_open_ms: int,
    *,
    okx_bar: str,
    bar_duration_ms: int,
    fetcher: Callable[[str], bytes] | None = None,
    request_interval_seconds: float = 0.12,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[bytes, list[dict[str, Any]], list[list[str]]]:
    """Download and validate a fixed OKX public history window without universe selection."""
    if request_interval_seconds < 0 or not math.isfinite(request_interval_seconds):
        raise ValueError("request_interval_seconds must be finite and non-negative")
    if bar_duration_ms <= 0:
        raise ValueError("bar_duration_ms must be positive")
    return _download_history(
        inst_id,
        history_start_ms,
        end_open_ms,
        {"okx_bar": okx_bar},
        fetcher or _fetch_public_bytes,
        request_interval_seconds,
        sleeper,
        bar_duration_ms,
    )


def replay_okx_public_history(
    bundle_bytes: bytes,
    inst_id: str,
    history_start_ms: int,
    end_open_ms: int,
    *,
    okx_bar: str,
    bar_duration_ms: int,
) -> tuple[list[list[str]], list[dict[str, Any]]]:
    """Replay one content-addressed OKX public history bundle without writes."""
    return _replay_history_bundle(
        bundle_bytes,
        inst_id,
        history_start_ms,
        end_open_ms,
        {"okx_bar": okx_bar},
        bar_duration_ms,
    )


def okx_history_rows_to_csv_bytes(rows: list[list[str]]) -> bytes:
    return _rows_to_csv_bytes(rows)


def capture_okx_universe_intake(
    registry_path: str | Path,
    policy_path: str | Path,
    semantics_report_path: str | Path,
    output_dir: str | Path,
    *,
    contract_evidence_path: str | Path = DEFAULT_CONTRACT_EVIDENCE,
    fetcher: Callable[[str], bytes] | None = None,
    received_at: datetime | None = None,
    request_interval_seconds: float = 0.12,
    sleeper: Callable[[float], None] = time.sleep,
) -> OkxUniverseCaptureResult:
    """Capture a frozen, current-live convenience sample from public OKX data only."""
    if request_interval_seconds < 0 or not math.isfinite(request_interval_seconds):
        raise ValueError("request_interval_seconds must be finite and non-negative")
    policy = _load_policy(Path(policy_path))
    contract_path = Path(contract_evidence_path)
    contract, contract_sha256 = _load_contract(contract_path)
    semantics = _load_semantics_report(Path(semantics_report_path))
    registry = load_dataset_registry(registry_path)
    registry_datasets: list[dict[str, Any]] = []
    for entry in registry.entries:
        audit = audit_dataset(entry)
        if not audit.valid or audit.raw_sha256 is None or audit.canonical_sha256 is None:
            raise MarketDataError(f"okx_universe_invalid_registry_dataset:{entry.dataset_id}")
        registry_datasets.append(
            {
                "dataset_id": entry.dataset_id,
                "raw_sha256": audit.raw_sha256,
                "canonical_sha256": audit.canonical_sha256,
            }
        )
    registry_identity = {
        "filename": registry.registry_path.name,
        "sha256": _sha256(registry.registry_path),
        "dataset_count": len(registry_datasets),
        "datasets": sorted(registry_datasets, key=lambda row: row["dataset_id"]),
    }

    captured_at = received_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() != timezone.utc.utcoffset(captured_at):
        raise ValueError("received_at must be timezone-aware UTC")
    received_at_ms = int(captured_at.timestamp() * 1000)
    history_start_ms = _timestamp_ms(policy["history_start"])
    end_open_ms = _compute_end_open_ms(received_at_ms)
    if end_open_ms < history_start_ms:
        raise MarketDataError("okx_universe_history_window_empty")

    public_fetch = fetcher or _fetch_public_bytes
    snapshot_params = {"instType": "SPOT"}
    snapshot_url = f"{OKX_INSTRUMENTS_ENDPOINT}?{urlencode(snapshot_params)}"
    snapshot_bytes = _fetch(public_fetch, snapshot_url, "instrument_snapshot")
    decisions, selected = _evaluate_instrument_snapshot(
        snapshot_bytes,
        policy,
        history_start_ms=history_start_ms,
    )
    if len(selected) != policy["target_count"]:
        raise MarketDataError("okx_universe_insufficient_eligible_instruments")

    prepared_histories: list[dict[str, Any]] = []
    for inst_id in selected:
        bundle_bytes, page_summaries, rows = _download_history(
            inst_id,
            history_start_ms,
            end_open_ms,
            policy,
            public_fetch,
            request_interval_seconds,
            sleeper,
        )
        csv_bytes = _rows_to_csv_bytes(rows)
        quality, canonical_sha256 = _validate_csv_bytes(
            csv_bytes,
            Path(output_dir),
            timeframe=policy["timeframe"],
        )
        bundle_sha256 = hashlib.sha256(bundle_bytes).hexdigest()
        csv_sha256 = hashlib.sha256(csv_bytes).hexdigest()
        slug = inst_id.lower().replace("-", "_")
        bundle_filename = f"okx-universe-history.{slug}.{bundle_sha256}.jsonl"
        csv_filename = f"okx-universe-history.{slug}.{csv_sha256}.csv"
        prepared_histories.append(
            {
                "inst_id": inst_id,
                "symbol": inst_id.replace("-", "/"),
                "bundle_bytes": bundle_bytes,
                "csv_bytes": csv_bytes,
                "identity": {
                    "inst_id": inst_id,
                    "symbol": inst_id.replace("-", "/"),
                    "timeframe": policy["timeframe"],
                    "okx_bar": policy["okx_bar"],
                    "first_timestamp": _iso_ms(history_start_ms),
                    "last_timestamp": _iso_ms(end_open_ms),
                    "bar_count": len(rows),
                    "expected_bar_count": ((end_open_ms - history_start_ms) // FOUR_HOURS_MS) + 1,
                    "page_count": len(page_summaries),
                    "pages": page_summaries,
                    "history_bundle": {
                        "filename": bundle_filename,
                        "sha256": bundle_sha256,
                    },
                    "csv": {
                        "filename": csv_filename,
                        "raw_sha256": csv_sha256,
                        "canonical_sha256": canonical_sha256,
                    },
                    "quality": quality,
                    "timestamp_semantics": "verified_open_time",
                    "lineage_status": "complete_direct_okx_public",
                },
            }
        )

    snapshot_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
    snapshot_filename = f"okx-universe-instruments.{snapshot_sha256}.response.json"
    histories_identity = [item["identity"] for item in prepared_histories]
    identity = {
        "schema_version": OKX_UNIVERSE_CAPTURE_SCHEMA_VERSION,
        "policy": policy,
        "contract": {
            "filename": contract_path.name,
            "sha256": contract_sha256,
            "official_url": contract["official_url"],
        },
        "semantics": semantics,
        "registry": registry_identity,
        "snapshot": {
            "endpoint": OKX_INSTRUMENTS_ENDPOINT,
            "request_params": snapshot_params,
            "received_at": captured_at.isoformat(),
            "response": {
                "filename": snapshot_filename,
                "sha256": snapshot_sha256,
            },
            "instrument_count": len(decisions),
            "eligibility_decisions": decisions,
            "selected_inst_ids": selected,
        },
        "history_window": {
            "history_start": _iso_ms(history_start_ms),
            "end_open": _iso_ms(end_open_ms),
            "end_policy": "last_fully_closed_4h_bar_before_snapshot_received_at_minus_5s",
        },
        "histories": histories_identity,
        "claims": _claims(),
        "safety": {
            "public_api_only": True,
            "private_api_used": False,
            "trading_api_used": False,
            "registry_modified": False,
            "readiness_changed": False,
        },
    }
    capture_sha256 = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    report = {
        "schema_version": OKX_UNIVERSE_CAPTURE_SCHEMA_VERSION,
        "capture_sha256": capture_sha256,
        "capture_status": "complete_current_okx_live_convenience_snapshot",
        "identity": identity,
        "warnings": [
            "not_a_historical_point_in_time_universe",
            "survivorship_bias_not_resolved",
            "no_strategy_or_profitability_claim",
            "registry_candidates_require_explicit_future_review",
        ],
    }

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    export_paths: dict[str, str] = {}
    snapshot_path = destination / snapshot_filename
    _commit_bytes(snapshot_path, snapshot_bytes, snapshot_sha256)
    export_paths["snapshot"] = str(snapshot_path)
    for prepared in prepared_histories:
        history = prepared["identity"]
        bundle_path = destination / history["history_bundle"]["filename"]
        _commit_bytes(bundle_path, prepared["bundle_bytes"], history["history_bundle"]["sha256"])
        export_paths[f"{prepared['inst_id']}_history"] = str(bundle_path)
    for prepared in prepared_histories:
        history = prepared["identity"]
        csv_path = destination / history["csv"]["filename"]
        _commit_bytes(csv_path, prepared["csv_bytes"], history["csv"]["raw_sha256"])
        export_paths[f"{prepared['inst_id']}_csv"] = str(csv_path)
    report_path = destination / f"okx-universe-capture.{capture_sha256}.json"
    _commit_report(report_path, report)
    export_paths["report"] = str(report_path)
    return OkxUniverseCaptureResult(report=report, export_paths=export_paths)


def validate_okx_universe_capture(
    capture_report_path: str | Path,
) -> OkxUniverseValidatedCapture:
    """Fully replay a frozen capture without writing any artifacts."""
    capture_path = Path(capture_report_path).resolve()
    capture = _load_capture_report(capture_path)
    identity = capture["identity"]
    policy = _validate_normalized_policy(identity.get("policy"))
    history_start_ms = _timestamp_ms(identity["history_window"]["history_start"])
    end_open_ms = _timestamp_ms(identity["history_window"]["end_open"])

    snapshot_info = identity["snapshot"]
    if (
        snapshot_info.get("endpoint") != OKX_INSTRUMENTS_ENDPOINT
        or snapshot_info.get("request_params") != {"instType": "SPOT"}
        or snapshot_info.get("instrument_count")
        != len(snapshot_info.get("eligibility_decisions", []))
    ):
        raise MarketDataError("okx_universe_snapshot_identity_mismatch")
    received_at_ms = _timestamp_ms(snapshot_info.get("received_at"))
    if _compute_end_open_ms(received_at_ms) != end_open_ms:
        raise MarketDataError("okx_universe_history_end_policy_mismatch")
    snapshot_path = _sibling_artifact(capture_path, snapshot_info["response"]["filename"])
    if _sha256(snapshot_path) != snapshot_info["response"]["sha256"]:
        raise MarketDataError("okx_universe_snapshot_hash_mismatch")
    decisions, selected = _evaluate_instrument_snapshot(
        snapshot_path.read_bytes(),
        policy,
        history_start_ms=history_start_ms,
    )
    if decisions != snapshot_info["eligibility_decisions"] or selected != snapshot_info["selected_inst_ids"]:
        raise MarketDataError("okx_universe_snapshot_decisions_mismatch")

    dataset_rows: list[dict[str, Any]] = []
    candidate_datasets: list[dict[str, Any]] = []
    for history in identity["histories"]:
        inst_id = history["inst_id"]
        bundle_path = _sibling_artifact(capture_path, history["history_bundle"]["filename"])
        if _sha256(bundle_path) != history["history_bundle"]["sha256"]:
            raise MarketDataError(f"okx_universe_history_bundle_hash_mismatch:{inst_id}")
        rows, pages = _replay_history_bundle(
            bundle_path.read_bytes(),
            inst_id,
            history_start_ms,
            end_open_ms,
            policy,
        )
        if pages != history["pages"]:
            raise MarketDataError(f"okx_universe_history_page_identity_mismatch:{inst_id}")
        expected_history_summary = {
            "symbol": inst_id.replace("-", "/"),
            "timeframe": policy["timeframe"],
            "okx_bar": policy["okx_bar"],
            "first_timestamp": _iso_ms(history_start_ms),
            "last_timestamp": _iso_ms(end_open_ms),
            "bar_count": len(rows),
            "expected_bar_count": ((end_open_ms - history_start_ms) // FOUR_HOURS_MS) + 1,
            "page_count": len(pages),
            "timestamp_semantics": "verified_open_time",
            "lineage_status": "complete_direct_okx_public",
        }
        if any(history.get(key) != value for key, value in expected_history_summary.items()):
            raise MarketDataError(f"okx_universe_history_summary_mismatch:{inst_id}")
        rebuilt_csv = _rows_to_csv_bytes(rows)
        csv_path = _sibling_artifact(capture_path, history["csv"]["filename"])
        if csv_path.read_bytes() != rebuilt_csv:
            raise MarketDataError(f"okx_universe_csv_replay_mismatch:{inst_id}")
        raw_sha256 = hashlib.sha256(rebuilt_csv).hexdigest()
        if raw_sha256 != history["csv"]["raw_sha256"]:
            raise MarketDataError(f"okx_universe_csv_hash_mismatch:{inst_id}")
        quality_report = validate_ohlcv_csv(csv_path, policy["timeframe"])
        quality = _quality_identity(quality_report.to_dict())
        if not quality_report.valid or quality != history["quality"]:
            raise MarketDataError(f"okx_universe_csv_quality_mismatch:{inst_id}")
        canonical_sha256 = canonical_ohlcv_sha256(csv_path)
        if canonical_sha256 != history["csv"]["canonical_sha256"]:
            raise MarketDataError(f"okx_universe_csv_canonical_hash_mismatch:{inst_id}")
        dataset_id = (
            f"okx_{inst_id.lower().replace('-', '_')}_4h_"
            f"{capture['capture_sha256'][:12]}"
        )
        row = {
            "dataset_id": dataset_id,
            "inst_id": inst_id,
            "symbol": history["symbol"],
            "timeframe": policy["timeframe"],
            "first_timestamp": history["first_timestamp"],
            "last_timestamp": history["last_timestamp"],
            "bar_count": history["bar_count"],
            "history_bundle_filename": history["history_bundle"]["filename"],
            "history_bundle_sha256": history["history_bundle"]["sha256"],
            "csv_filename": history["csv"]["filename"],
            "raw_sha256": raw_sha256,
            "canonical_sha256": canonical_sha256,
            "timestamp_semantics": "verified_open_time",
            "lineage_status": "complete_direct_okx_public",
        }
        dataset_rows.append(row)
        candidate_datasets.append(_registry_candidate(row, capture["capture_sha256"]))

    eligibility_bytes = _serialize_rows(decisions, _ELIGIBILITY_FIELDS)
    datasets_bytes = _serialize_rows(dataset_rows, _DATASET_FIELDS)
    candidates_payload = {
        "schema_version": 1,
        "candidate_kind": "okx_universe_intake_registry_candidates",
        "capture_sha256": capture["capture_sha256"],
        "automatic_registry_merge": False,
        **_claims(),
        "datasets": candidate_datasets,
    }
    candidates_bytes = yaml.safe_dump(
        candidates_payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        line_break="\n",
    ).encode("utf-8")
    return OkxUniverseValidatedCapture(
        capture_path=capture_path,
        capture=capture,
        policy=policy,
        selected_inst_ids=tuple(selected),
        eligibility=tuple(decisions),
        datasets=tuple(dataset_rows),
        candidate_datasets=tuple(candidate_datasets),
        eligibility_bytes=eligibility_bytes,
        datasets_bytes=datasets_bytes,
        candidates_bytes=candidates_bytes,
    )


def audit_okx_universe_intake(
    capture_report_path: str | Path,
    output_dir: str | Path,
) -> OkxUniverseAuditResult:
    """Offline-replay a frozen capture and emit independent Registry candidates."""
    validated = validate_okx_universe_capture(capture_report_path)
    report, artifact_bytes = _build_intake_report(validated)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {
        "eligibility": destination / report["artifacts"]["eligibility"]["filename"],
        "datasets": destination / report["artifacts"]["datasets"]["filename"],
        "registry_candidates": destination
        / report["artifacts"]["registry_candidates"]["filename"],
        "report": destination / f"okx-universe-intake.{report['intake_sha256']}.json",
    }
    for name in ("eligibility", "datasets", "registry_candidates"):
        artifact = report["artifacts"][name]
        _commit_bytes(paths[name], artifact_bytes[name], artifact["sha256"])
    _commit_report(paths["report"], report)
    return OkxUniverseAuditResult(
        report=report,
        export_paths={name: str(path) for name, path in paths.items()},
    )


def validate_okx_universe_intake(
    capture_report_path: str | Path,
    intake_report_path: str | Path,
) -> OkxUniverseValidatedIntake:
    """Validate a frozen intake marker and every sibling artifact without writes."""
    validated = validate_okx_universe_capture(capture_report_path)
    expected_report, expected_bytes = _build_intake_report(validated)
    intake_path = Path(intake_report_path).resolve()
    if not intake_path.is_file():
        raise FileNotFoundError(f"OKX universe intake report not found: {intake_path}")
    expected_name = f"okx-universe-intake.{expected_report['intake_sha256']}.json"
    if intake_path.name != expected_name:
        raise MarketDataError("okx_universe_intake_filename_mismatch")
    try:
        observed_report = json.loads(intake_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("okx_universe_invalid_intake_report") from exc
    if observed_report != expected_report:
        raise MarketDataError("okx_universe_intake_report_mismatch")
    artifact_paths: dict[str, Path] = {}
    for name in ("eligibility", "datasets", "registry_candidates"):
        artifact = expected_report["artifacts"][name]
        artifact_path = _sibling_artifact(intake_path, artifact["filename"])
        if artifact_path.read_bytes() != expected_bytes[name]:
            raise MarketDataError(f"okx_universe_intake_artifact_mismatch:{name}")
        artifact_paths[name] = artifact_path
    return OkxUniverseValidatedIntake(
        capture=validated,
        intake_path=intake_path,
        intake_report=observed_report,
        artifact_paths=artifact_paths,
    )


def validate_okx_timestamp_semantics_report(path: str | Path) -> dict[str, Any]:
    """Validate the frozen semantics marker and its sibling artifacts without writes."""
    return _load_semantics_report(Path(path))


def _build_intake_report(
    validated: OkxUniverseValidatedCapture,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    capture = validated.capture
    capture_path = validated.capture_path
    selected = list(validated.selected_inst_ids)
    decisions = list(validated.eligibility)
    dataset_rows = list(validated.datasets)
    eligibility_bytes = validated.eligibility_bytes
    datasets_bytes = validated.datasets_bytes
    candidates_bytes = validated.candidates_bytes
    audit_identity = {
        "schema_version": OKX_UNIVERSE_AUDIT_SCHEMA_VERSION,
        "capture_sha256": capture["capture_sha256"],
        "capture_report_sha256": _sha256(capture_path),
        "eligibility_sha256": hashlib.sha256(eligibility_bytes).hexdigest(),
        "datasets_sha256": hashlib.sha256(datasets_bytes).hexdigest(),
        "registry_candidates_sha256": hashlib.sha256(candidates_bytes).hexdigest(),
        "dataset_count": len(dataset_rows),
        "selected_inst_ids": selected,
        "claims": _claims(),
        "policies": {
            "network_access": "forbidden_offline_capture_replay_only",
            "registry_merge": "forbidden_candidate_artifact_only",
            "randomness": "none",
        },
    }
    intake_sha256 = hashlib.sha256(_canonical_json_bytes(audit_identity)).hexdigest()
    stem = f"okx-universe-intake.{intake_sha256}"
    report = {
        "schema_version": OKX_UNIVERSE_AUDIT_SCHEMA_VERSION,
        "intake_sha256": intake_sha256,
        "audit_status": "verified_offline_capture_replay",
        "identity": audit_identity,
        "capture_sha256": capture["capture_sha256"],
        "selected_inst_ids": selected,
        "eligibility": decisions,
        "datasets": dataset_rows,
        "claims": _claims(),
        "automatic_registry_merge": False,
        "readiness_changed": False,
        "artifacts": {
            "eligibility": {
                "filename": f"{stem}.eligibility.csv",
                "sha256": audit_identity["eligibility_sha256"],
            },
            "datasets": {
                "filename": f"{stem}.datasets.csv",
                "sha256": audit_identity["datasets_sha256"],
            },
            "registry_candidates": {
                "filename": f"{stem}.registry-candidates.yaml",
                "sha256": audit_identity["registry_candidates_sha256"],
            },
        },
    }
    return report, {
        "eligibility": eligibility_bytes,
        "datasets": datasets_bytes,
        "registry_candidates": candidates_bytes,
    }


def format_okx_universe_capture(result: OkxUniverseCaptureResult) -> str:
    identity = result.report["identity"]
    return "\n".join(
        [
            f"capture_sha256: {result.report['capture_sha256']}",
            f"capture_status: {result.report['capture_status']}",
            f"snapshot_received_at: {identity['snapshot']['received_at']}",
            f"instrument_count: {identity['snapshot']['instrument_count']}",
            f"selected_inst_ids: {','.join(identity['snapshot']['selected_inst_ids'])}",
            f"history_start: {identity['history_window']['history_start']}",
            f"end_open: {identity['history_window']['end_open']}",
            "private_api_used: false",
            "trading_api_used: false",
            "registry_modified: false",
        ]
    )


def format_okx_universe_audit(result: OkxUniverseAuditResult) -> str:
    return "\n".join(
        [
            f"intake_sha256: {result.report['intake_sha256']}",
            f"audit_status: {result.report['audit_status']}",
            f"capture_sha256: {result.report['capture_sha256']}",
            f"dataset_count: {len(result.report['datasets'])}",
            f"selected_inst_ids: {','.join(result.report['selected_inst_ids'])}",
            "historical_point_in_time_membership: false",
            "survivorship_bias_resolved: false",
            "automatic_registry_merge: false",
        ]
    )


def _load_policy(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"OKX universe intake policy not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("OKX universe intake policy YAML is invalid") from exc
    return _validate_normalized_policy(raw)


def _validate_normalized_policy(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("OKX universe intake policy must be a mapping")
    expected_scalars = {
        "schema_version": INTAKE_POLICY_SCHEMA_VERSION,
        "policy_version": 1,
        "universe_kind": "current_okx_live_convenience_snapshot",
        "target_count": 3,
        "history_start": "2022-01-01T00:00:00Z",
        "timeframe": "4h",
        "okx_bar": "4H",
        "quote_currency": "USDT",
        "future_snapshot_exit_policy": "stop_future_intake_only_never_delete_or_rewrite_frozen_data",
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
    }
    for key, expected in expected_scalars.items():
        if raw.get(key) != expected:
            raise ValueError(f"OKX universe intake policy {key} must equal {expected!r}")
    if raw.get("instrument_requirements") != {
        "inst_type": "SPOT",
        "state": "live",
        "rule_type": "normal",
        "inst_category": "1",
    }:
        raise ValueError("OKX universe intake instrument_requirements mismatch")
    if raw.get("selection") != {
        "policy": "sha256_ascending",
        "version": "okx-convenience-universe-v1",
        "identity_prefix": "okx-convenience-universe-v1|",
    }:
        raise ValueError("OKX universe intake selection policy mismatch")
    normalized = json.loads(json.dumps(raw, sort_keys=True))
    for key in (
        "stablecoin_base_denylist",
        "leveraged_token_anchored_patterns",
        "existing_base_exclusions",
    ):
        values = raw.get(key)
        if not isinstance(values, list) or not values or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise ValueError(f"OKX universe intake policy {key} must be a non-empty string list")
        if values != sorted(set(values)):
            raise ValueError(f"OKX universe intake policy {key} must be sorted and unique")
    if raw["existing_base_exclusions"] != ["BTC", "ETH", "SOL"]:
        raise ValueError("OKX universe intake existing_base_exclusions mismatch")
    try:
        for pattern in raw["leveraged_token_anchored_patterns"]:
            compiled = re.compile(pattern)
            if not pattern.startswith("^") or not pattern.endswith("$"):
                raise ValueError("leveraged token patterns must be anchored")
            compiled.fullmatch("BTC3L")
    except re.error as exc:
        raise ValueError("OKX universe intake leveraged token pattern is invalid") from exc
    _timestamp_ms(raw["history_start"])
    return normalized


def _load_contract(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise FileNotFoundError(f"OKX public contract evidence not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("okx_universe_invalid_contract_json") from exc
    expected = {
        "schema_version": 1,
        "provider": "okx",
        "scope": "public_market_data_only",
        "private_api_required": False,
        "trading_api_required": False,
    }
    if not isinstance(payload, dict) or any(payload.get(k) != v for k, v in expected.items()):
        raise MarketDataError("okx_universe_contract_mismatch")
    instruments = payload.get("instruments")
    history = payload.get("history_candles")
    if not isinstance(instruments, dict) or not isinstance(history, dict):
        raise MarketDataError("okx_universe_contract_mismatch")
    if (
        instruments.get("endpoint") != OKX_INSTRUMENTS_ENDPOINT
        or instruments.get("continuous_start_rule")
        != "contTdSwTime_when_non_empty_else_listTime"
        or history.get("endpoint") != OKX_HISTORY_ENDPOINT
        or history.get("bar") != "4H"
        or history.get("maximum_limit") != OKX_HISTORY_LIMIT
        or history.get("after_semantics") != "records_strictly_earlier_than_ts"
        or history.get("response_order") != "newest_first"
        or history.get("response_shape") != list(_HISTORY_FIELDS)
        or history.get("timestamp_meaning") != "bar_open_time"
        or history.get("confirmed_value") != "1"
    ):
        raise MarketDataError("okx_universe_contract_mismatch")
    return payload, _sha256(path)


def _load_semantics_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"timestamp semantics report not found: {path}")
    report_bytes = path.read_bytes()
    try:
        report = json.loads(report_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("okx_universe_invalid_semantics_report") from exc
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise MarketDataError("okx_universe_invalid_semantics_report")
    semantics_sha = _require_sha256(report.get("semantics_sha256"), "semantics_sha256")
    if path.name != f"timestamp-semantics.{semantics_sha}.json":
        raise MarketDataError("okx_universe_semantics_filename_mismatch")
    if report.get("assessment_status") != "independent_timestamp_semantics_evidence_only":
        raise MarketDataError("okx_universe_semantics_status_mismatch")
    artifact_hashes: dict[str, str] = {}
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"producers", "datasets", "panels"}:
        raise MarketDataError("okx_universe_semantics_artifacts_invalid")
    for name, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            raise MarketDataError("okx_universe_semantics_artifacts_invalid")
        sibling = _sibling_artifact(path.resolve(), artifact.get("filename"))
        expected_hash = _require_sha256(artifact.get("sha256"), f"{name}_sha256")
        if _sha256(sibling) != expected_hash:
            raise MarketDataError("okx_universe_semantics_artifact_hash_mismatch")
        artifact_hashes[name] = expected_hash
    producers = report.get("producers")
    if not isinstance(producers, list):
        raise MarketDataError("okx_universe_semantics_producers_invalid")
    okx = [row for row in producers if isinstance(row, dict) and row.get("producer_id") == "okx_public_candles_v1"]
    if len(okx) != 1 or okx[0].get("status") != "verified_open_time":
        raise MarketDataError("okx_universe_okx_producer_not_verified_open_time")
    return {
        "semantics_sha256": semantics_sha,
        "report_filename": path.name,
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "artifact_hashes": artifact_hashes,
        "producer_id": "okx_public_candles_v1",
        "producer_status": "verified_open_time",
        "producer_contract_sha256": okx[0].get("contract_sha256"),
        "producer_probe_sha256": okx[0].get("probe_sha256"),
    }


def _evaluate_instrument_snapshot(
    response_bytes: bytes,
    policy: dict[str, Any],
    *,
    history_start_ms: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    payload = _parse_okx_payload(response_bytes, "instrument_snapshot")
    instruments = payload.get("data")
    if not isinstance(instruments, list) or not instruments:
        raise MarketDataError("okx_universe_empty_instrument_snapshot")
    denylist = set(policy["stablecoin_base_denylist"])
    existing = set(policy["existing_base_exclusions"])
    patterns = [re.compile(pattern) for pattern in policy["leveraged_token_anchored_patterns"]]
    seen: set[str] = set()
    decisions: list[dict[str, Any]] = []
    for raw in instruments:
        if not isinstance(raw, dict):
            raise MarketDataError("okx_universe_invalid_instrument_row")
        required = (
            "instType",
            "instId",
            "baseCcy",
            "quoteCcy",
            "state",
            "ruleType",
            "instCategory",
            "listTime",
            "contTdSwTime",
        )
        if any(not isinstance(raw.get(field), str) for field in required):
            raise MarketDataError("okx_universe_invalid_instrument_row")
        inst_id = raw["instId"]
        if not inst_id or inst_id in seen:
            raise MarketDataError("okx_universe_duplicate_or_empty_inst_id")
        seen.add(inst_id)
        base = raw["baseCcy"]
        quote = raw["quoteCcy"]
        continuous_raw = raw["contTdSwTime"] or raw["listTime"]
        continuous_ms = _optional_millisecond_timestamp(continuous_raw)
        reasons: list[str] = []
        requirements = policy["instrument_requirements"]
        if raw["instType"] != requirements["inst_type"]:
            reasons.append("inst_type_not_spot")
        if quote != policy["quote_currency"]:
            reasons.append("quote_not_usdt")
        if raw["state"] != requirements["state"]:
            reasons.append("state_not_live")
        if raw["ruleType"] != requirements["rule_type"]:
            reasons.append("rule_type_not_normal")
        if raw["instCategory"] != requirements["inst_category"]:
            reasons.append("inst_category_not_crypto")
        if not base or inst_id != f"{base}-{quote}":
            reasons.append("instrument_shape_mismatch")
        if continuous_ms is None:
            reasons.append("continuous_start_missing_or_invalid")
        elif continuous_ms > history_start_ms:
            reasons.append("continuous_start_after_history_start")
        if base in denylist:
            reasons.append("stablecoin_base_denied")
        if base in existing:
            reasons.append("existing_base_excluded")
        if any(pattern.fullmatch(base) or pattern.fullmatch(inst_id) for pattern in patterns):
            reasons.append("leveraged_token_pattern")
        selection_sha = hashlib.sha256(
            f"{policy['selection']['identity_prefix']}{inst_id}".encode("utf-8")
        ).hexdigest()
        decisions.append(
            {
                "inst_id": inst_id,
                "base_ccy": base,
                "quote_ccy": quote,
                "inst_type": raw["instType"],
                "state": raw["state"],
                "rule_type": raw["ruleType"],
                "inst_category": raw["instCategory"],
                "list_time": raw["listTime"],
                "cont_td_sw_time": raw["contTdSwTime"],
                "effective_continuous_start": _iso_ms(continuous_ms) if continuous_ms is not None else "",
                "eligible": not reasons,
                "exclusion_reasons": "|".join(reasons),
                "selection_sha256": selection_sha,
                "selected_rank": "",
            }
        )
    eligible = sorted(
        (row for row in decisions if row["eligible"]),
        key=lambda row: (row["selection_sha256"], row["inst_id"]),
    )
    selected_rows = eligible[: policy["target_count"]]
    selected = [row["inst_id"] for row in selected_rows]
    ranks = {inst_id: index + 1 for index, inst_id in enumerate(selected)}
    for row in decisions:
        row["selected_rank"] = ranks.get(row["inst_id"], "")
    decisions.sort(key=lambda row: row["inst_id"])
    return decisions, selected


def _download_history(
    inst_id: str,
    history_start_ms: int,
    end_open_ms: int,
    policy: dict[str, Any],
    fetcher: Callable[[str], bytes],
    request_interval_seconds: float,
    sleeper: Callable[[float], None],
    bar_duration_ms: int = FOUR_HOURS_MS,
) -> tuple[bytes, list[dict[str, Any]], list[list[str]]]:
    records: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []
    rows: list[list[str]] = []
    cursor = end_open_ms + bar_duration_ms
    previous_oldest: int | None = None
    page_index = 0
    while True:
        params = {
            "instId": inst_id,
            "bar": policy["okx_bar"],
            "after": str(cursor),
            "limit": str(OKX_HISTORY_LIMIT),
        }
        url = f"{OKX_HISTORY_ENDPOINT}?{urlencode(params)}"
        response_bytes = _fetch(fetcher, url, f"history:{inst_id}:page:{page_index}")
        page_rows = _validate_history_response(
            response_bytes,
            inst_id=inst_id,
            cursor=cursor,
            previous_oldest=previous_oldest,
            bar_duration_ms=bar_duration_ms,
        )
        newest = int(page_rows[0][0])
        oldest = int(page_rows[-1][0])
        if page_index == 0 and newest != end_open_ms:
            raise MarketDataError(f"okx_universe_history_end_boundary_mismatch:{inst_id}")
        response_sha = hashlib.sha256(response_bytes).hexdigest()
        try:
            response_text = response_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MarketDataError("okx_universe_response_not_utf8") from exc
        record = {
            "page_index": page_index,
            "endpoint": OKX_HISTORY_ENDPOINT,
            "request_params": params,
            "response_body": response_text,
            "response_sha256": response_sha,
        }
        records.append(record)
        page_summaries.append(
            {
                "page_index": page_index,
                "request_params": params,
                "response_sha256": response_sha,
                "row_count": len(page_rows),
                "newest_ts": str(newest),
                "oldest_ts": str(oldest),
            }
        )
        rows.extend(page_rows)
        if oldest <= history_start_ms:
            break
        previous_oldest = oldest
        cursor = oldest
        page_index += 1
        if page_index > 10_000:
            raise MarketDataError("okx_universe_history_page_limit_exceeded")
        if request_interval_seconds:
            sleeper(request_interval_seconds)
    filtered = [row for row in rows if history_start_ms <= int(row[0]) <= end_open_ms]
    _validate_complete_history(
        filtered, inst_id, history_start_ms, end_open_ms, bar_duration_ms=bar_duration_ms
    )
    filtered.sort(key=lambda row: int(row[0]))
    bundle = b"".join(_canonical_json_bytes(record) + b"\n" for record in records)
    return bundle, page_summaries, filtered


def _replay_history_bundle(
    bundle_bytes: bytes,
    inst_id: str,
    history_start_ms: int,
    end_open_ms: int,
    policy: dict[str, Any],
    bar_duration_ms: int = FOUR_HOURS_MS,
) -> tuple[list[list[str]], list[dict[str, Any]]]:
    try:
        lines = bundle_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise MarketDataError("okx_universe_invalid_history_bundle_utf8") from exc
    if not lines:
        raise MarketDataError("okx_universe_empty_history_bundle")
    all_rows: list[list[str]] = []
    summaries: list[dict[str, Any]] = []
    cursor = end_open_ms + bar_duration_ms
    previous_oldest: int | None = None
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MarketDataError("okx_universe_invalid_history_bundle_json") from exc
        params = {
            "instId": inst_id,
            "bar": policy["okx_bar"],
            "after": str(cursor),
            "limit": str(OKX_HISTORY_LIMIT),
        }
        if not isinstance(record, dict) or record.get("page_index") != index:
            raise MarketDataError("okx_universe_history_bundle_page_index_mismatch")
        if record.get("endpoint") != OKX_HISTORY_ENDPOINT or record.get("request_params") != params:
            raise MarketDataError("okx_universe_history_bundle_request_mismatch")
        response_text = record.get("response_body")
        if not isinstance(response_text, str):
            raise MarketDataError("okx_universe_history_bundle_response_invalid")
        response_bytes = response_text.encode("utf-8")
        response_sha = hashlib.sha256(response_bytes).hexdigest()
        if response_sha != record.get("response_sha256"):
            raise MarketDataError("okx_universe_history_bundle_response_hash_mismatch")
        page_rows = _validate_history_response(
            response_bytes,
            inst_id=inst_id,
            cursor=cursor,
            previous_oldest=previous_oldest,
            bar_duration_ms=bar_duration_ms,
        )
        newest = int(page_rows[0][0])
        oldest = int(page_rows[-1][0])
        if index == 0 and newest != end_open_ms:
            raise MarketDataError(f"okx_universe_history_end_boundary_mismatch:{inst_id}")
        summaries.append(
            {
                "page_index": index,
                "request_params": params,
                "response_sha256": response_sha,
                "row_count": len(page_rows),
                "newest_ts": str(newest),
                "oldest_ts": str(oldest),
            }
        )
        all_rows.extend(page_rows)
        previous_oldest = oldest
        cursor = oldest
    if int(all_rows[-1][0]) > history_start_ms:
        raise MarketDataError(f"okx_universe_history_bundle_stops_too_early:{inst_id}")
    filtered = [row for row in all_rows if history_start_ms <= int(row[0]) <= end_open_ms]
    _validate_complete_history(
        filtered, inst_id, history_start_ms, end_open_ms, bar_duration_ms=bar_duration_ms
    )
    filtered.sort(key=lambda row: int(row[0]))
    return filtered, summaries


def _validate_history_response(
    response_bytes: bytes,
    *,
    inst_id: str,
    cursor: int,
    previous_oldest: int | None,
    bar_duration_ms: int = FOUR_HOURS_MS,
) -> list[list[str]]:
    payload = _parse_okx_payload(response_bytes, f"history:{inst_id}")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise MarketDataError(f"okx_universe_empty_history_page:{inst_id}")
    rows: list[list[str]] = []
    timestamps: list[int] = []
    for raw in data:
        if not isinstance(raw, list) or len(raw) != len(_HISTORY_FIELDS) or any(
            not isinstance(value, str) for value in raw
        ):
            raise MarketDataError(f"okx_universe_invalid_history_row:{inst_id}")
        try:
            numeric = [Decimal(value) for value in raw[:8]]
        except InvalidOperation as exc:
            raise MarketDataError(f"okx_universe_invalid_history_value:{inst_id}") from exc
        if any(not value.is_finite() for value in numeric):
            raise MarketDataError(f"okx_universe_invalid_history_value:{inst_id}")
        if raw[8] != "1":
            raise MarketDataError(f"okx_universe_unconfirmed_history_bar:{inst_id}")
        try:
            timestamp = int(raw[0])
        except ValueError as exc:
            raise MarketDataError(f"okx_universe_invalid_history_timestamp:{inst_id}") from exc
        if str(timestamp) != raw[0] or timestamp % bar_duration_ms != 0:
            raise MarketDataError(f"okx_universe_history_off_grid:{inst_id}")
        if timestamp >= cursor:
            raise MarketDataError(f"okx_universe_history_cursor_not_exclusive:{inst_id}")
        timestamps.append(timestamp)
        rows.append(raw)
    if any(left <= right for left, right in zip(timestamps, timestamps[1:])):
        raise MarketDataError(f"okx_universe_history_not_strictly_descending:{inst_id}")
    if previous_oldest is not None and timestamps[0] >= previous_oldest:
        raise MarketDataError(f"okx_universe_history_cursor_did_not_advance:{inst_id}")
    return rows


def _validate_complete_history(
    rows: list[list[str]],
    inst_id: str,
    history_start_ms: int,
    end_open_ms: int,
    *,
    bar_duration_ms: int = FOUR_HOURS_MS,
) -> None:
    timestamps = sorted(int(row[0]) for row in rows)
    expected = list(range(history_start_ms, end_open_ms + bar_duration_ms, bar_duration_ms))
    if timestamps != expected:
        raise MarketDataError(f"okx_universe_history_not_exact_complete_window:{inst_id}")


def _rows_to_csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    for row in rows:
        writer.writerow([_iso_ms(int(row[0])), row[1], row[2], row[3], row[4], row[5]])
    return buffer.getvalue().encode("utf-8")


def _validate_csv_bytes(
    csv_bytes: bytes,
    output_dir: Path,
    *,
    timeframe: str,
) -> tuple[dict[str, Any], str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / f".okx-universe-validation.{uuid4().hex}.csv"
    try:
        temporary.write_bytes(csv_bytes)
        quality = validate_ohlcv_csv(temporary, timeframe)
        canonical_sha = canonical_ohlcv_sha256(temporary)
        if not quality.valid or canonical_sha is None:
            raise MarketDataError("okx_universe_normalized_csv_quality_failed")
        return _quality_identity(quality.to_dict()), canonical_sha
    finally:
        if temporary.exists():
            temporary.unlink()


def _quality_identity(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "csv_path"}


def _load_capture_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"OKX universe capture report not found: {path}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("okx_universe_invalid_capture_report") from exc
    if not isinstance(report, dict) or report.get("schema_version") != OKX_UNIVERSE_CAPTURE_SCHEMA_VERSION:
        raise MarketDataError("okx_universe_invalid_capture_report")
    capture_sha = _require_sha256(report.get("capture_sha256"), "capture_sha256")
    if path.name != f"okx-universe-capture.{capture_sha}.json":
        raise MarketDataError("okx_universe_capture_filename_mismatch")
    identity = report.get("identity")
    if not isinstance(identity, dict):
        raise MarketDataError("okx_universe_capture_identity_missing")
    if hashlib.sha256(_canonical_json_bytes(identity)).hexdigest() != capture_sha:
        raise MarketDataError("okx_universe_capture_identity_mismatch")
    safety = identity.get("safety")
    if not isinstance(safety, dict) or safety != {
        "public_api_only": True,
        "private_api_used": False,
        "trading_api_used": False,
        "registry_modified": False,
        "readiness_changed": False,
    }:
        raise MarketDataError("okx_universe_capture_safety_mismatch")
    claims = identity.get("claims")
    if claims != _claims():
        raise MarketDataError("okx_universe_capture_claims_mismatch")
    histories = identity.get("histories")
    if not isinstance(histories, list) or len(histories) != 3:
        raise MarketDataError("okx_universe_capture_history_count_mismatch")
    snapshot = identity.get("snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("selected_inst_ids"), list):
        raise MarketDataError("okx_universe_capture_snapshot_invalid")
    if [history.get("inst_id") for history in histories] != snapshot["selected_inst_ids"]:
        raise MarketDataError("okx_universe_capture_history_membership_mismatch")
    registry = identity.get("registry")
    if (
        not isinstance(registry, dict)
        or registry.get("dataset_count") != len(registry.get("datasets", []))
    ):
        raise MarketDataError("okx_universe_capture_registry_identity_mismatch")
    _require_sha256(registry.get("sha256"), "registry_sha256")
    contract = identity.get("contract")
    semantics = identity.get("semantics")
    if not isinstance(contract, dict) or not isinstance(semantics, dict):
        raise MarketDataError("okx_universe_capture_evidence_identity_mismatch")
    _require_sha256(contract.get("sha256"), "contract_sha256")
    _require_sha256(semantics.get("semantics_sha256"), "semantics_sha256")
    _require_sha256(semantics.get("report_sha256"), "semantics_report_sha256")
    return report


def _registry_candidate(row: dict[str, Any], capture_sha256: str) -> dict[str, Any]:
    return {
        "dataset_id": row["dataset_id"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "capture_artifact": row["csv_filename"],
        "source": {
            "status": "verified",
            "provider": "okx_public_history_candles",
            "lineage": "complete_direct_okx_public",
            "timestamp_semantics": "verified_open_time",
            "capture_sha256": capture_sha256,
            "history_bundle": row["history_bundle_filename"],
            "history_bundle_sha256": row["history_bundle_sha256"],
        },
        "quality": {"validator": "validate_ohlcv_csv"},
        "expected": {
            "bar_count": row["bar_count"],
            "first_timestamp": row["first_timestamp"],
            "last_timestamp": row["last_timestamp"],
            "raw_sha256": row["raw_sha256"],
            "canonical_sha256": row["canonical_sha256"],
        },
    }


def _claims() -> dict[str, Any]:
    return {
        "universe_kind": "current_okx_live_convenience_snapshot",
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "profitability_evidence": False,
        "strategy_approval": False,
    }


def _compute_end_open_ms(received_at_ms: int) -> int:
    cutoff = received_at_ms - SNAPSHOT_SAFETY_LAG_MS
    return (cutoff // FOUR_HOURS_MS) * FOUR_HOURS_MS - FOUR_HOURS_MS


def _parse_okx_payload(response_bytes: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(response_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError(f"okx_universe_invalid_response_json:{label}") from exc
    if not isinstance(payload, dict) or payload.get("code") != "0" or payload.get("msg") != "":
        raise MarketDataError(f"okx_universe_unsuccessful_response:{label}")
    return payload


def _fetch(fetcher: Callable[[str], bytes], url: str, label: str) -> bytes:
    try:
        payload = fetcher(url)
    except Exception as exc:
        raise MarketDataError(f"okx_universe_public_fetch_failed:{label}:{exc}") from exc
    if not isinstance(payload, bytes):
        raise MarketDataError(f"okx_universe_fetcher_must_return_bytes:{label}")
    return payload


def _fetch_public_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "crypto-trading-bot/0.1 public-market-research",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urlopen(request, timeout=30) as response:  # nosec B310 - endpoint is fixed above
        return response.read()


def _optional_millisecond_timestamp(value: str) -> int | None:
    if not value or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed >= 0 else None


def _timestamp_ms(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a UTC string")
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed) or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("timestamp must be a UTC string")
    return int(pd.Timestamp(parsed).timestamp() * 1000)


def _iso_ms(value: int) -> str:
    return pd.Timestamp(value, unit="ms", tz="UTC").isoformat()


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _sibling_artifact(report_path: Path, filename: object) -> Path:
    if not isinstance(filename, str):
        raise MarketDataError("okx_universe_invalid_artifact_filename")
    candidate = Path(filename)
    if candidate.is_absolute() or candidate.name != filename or "/" in filename or "\\" in filename:
        raise MarketDataError("okx_universe_artifact_path_escape")
    resolved = (report_path.parent / candidate).resolve()
    if resolved.parent != report_path.parent or not resolved.is_file():
        raise MarketDataError("okx_universe_artifact_missing")
    return resolved


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


def _format_csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


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
