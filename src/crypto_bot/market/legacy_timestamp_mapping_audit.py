from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import yaml

from crypto_bot.cross_sectional_portfolio_mechanism import (
    ValidatedCrossSectionalPortfolioMechanism,
    validate_cross_sectional_portfolio_mechanism,
)
from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_panel import build_dataset_panel
from crypto_bot.market.dataset_registry import canonicalize_ohlcv_frame, load_dataset_registry


SCHEMA_VERSION = 1
AUDIT_STATUS_BLOCKED = "common_next_open_structurally_verified_semantics_blocked_no_pnl"
AUDIT_STATUS_AUTHORIZED = "common_next_open_mapping_semantically_authorized_no_pnl"
EXPECTED_MECHANISM_SHA256 = "dcc8e9364efd900487608090ba879194a8a84195cbeab2dc63d0266df97406d5"
EXPECTED_PANEL_SHA256 = "b7cdebac94f728a3588cb090391fb6024cb6783ec480b1fedc830e3bd4a32054"
EXPECTED_PANEL_ID = "btc_eth_sol_knc_swftc_bico_1h_v1"
EXPECTED_SIGNAL_TIMESTAMP_COUNT = 20_424
EXPECTED_MAPPING_ROW_COUNT = 122_544
EXPECTED_STRUCTURAL_VALID_COUNT = 122_532
EXPECTED_TAIL_COUNT = 12
EXPECTED_CONFIG = {
    "schema_version": 1,
    "audit_policy_id": "legacy_1h_exact_common_next_open_v1",
    "required_semantics": "verified_open_time",
    "signal_completion_delay_bars": 1,
    "execution_delay_after_completion_bars": 1,
    "execution_offset_bars": 2,
    "execution_price_field": "open",
    "mapping_policy": "exact_timestamp_no_fill",
    "required_asset_count": 6,
    "required_dataset_ids": [
        "okx_bico_usdt_1h_frozen",
        "btc_usdt_1h_v1",
        "eth_usdt_1h_v1",
        "okx_knc_usdt_1h_frozen",
        "sol_usdt_1h_v1",
        "okx_swftc_usdt_1h_frozen",
    ],
    "timestamp_semantics_report": {
        "repo_relative_artifact": (
            "reports/timestamp-semantics/"
            "timestamp-semantics.fc5f911cf333e6557547b19dc126f464df1d45e0d5e5d8eff99f35d5903a0190.json"
        ),
        "artifact_sha256": "cee944dadd8d678ba89061b03b06dbbc3bbc83b01e3ecfc293e831070d6c1220",
        "semantics_sha256": "fc5f911cf333e6557547b19dc126f464df1d45e0d5e5d8eff99f35d5903a0190",
    },
    "legacy_datasets": [
        {
            "dataset_id": "btc_usdt_1h_v1",
            "expected_status": "partial_unverified",
            "evidence": [
                {
                    "repo_relative_artifact": "downloads/BTCUSDT-1h-2024-2026-05-15-binance-klines.csv",
                    "artifact_sha256": "bbb9e0ca2c5b3cc047a7841379ebff6c8d9083fd347bfbb2d017eec778dec724",
                },
                {
                    "repo_relative_artifact": "config.history.example.yaml",
                    "artifact_sha256": "5f985a126a951b73978d8c4fcf2dcb36a59d015bad8e82650f818981cb30823e",
                },
            ],
        },
        {"dataset_id": "eth_usdt_1h_v1", "expected_status": "unknown", "evidence": []},
        {"dataset_id": "sol_usdt_1h_v1", "expected_status": "unknown", "evidence": []},
    ],
}
_DATASET_FIELDS = (
    "dataset_id",
    "symbol",
    "source_status",
    "timestamp_semantics_status",
    "semantic_authorized",
    "raw_sha256",
    "canonical_sha256",
    "first_timestamp",
    "last_timestamp",
    "bar_count",
    "lineage_coverage",
    "evidence_count",
    "evidence_artifact_hashes",
)
_MAPPING_FIELDS = (
    "signal_timestamp",
    "signal_completion_timestamp",
    "execution_timestamp",
    "dataset_id",
    "symbol",
    "execution_price_field",
    "structural_status",
    "mapped_source_timestamp",
    "mapped_open",
    "exact_row_exists",
    "finite_open",
    "timestamp_semantics_status",
    "semantic_authorized",
)
_CONSTRAINT_FIELDS = (
    "constraint_name",
    "status",
    "observed",
    "required",
    "blocks_execution_price_mapping",
    "blocks_pnl_computation",
)


@dataclass(frozen=True)
class LegacyTimestampMappingAuditResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def audit_legacy_1h_next_open_mapping(
    mechanism_report_path: str | Path,
    evidence_config_path: str | Path,
    output_dir: str | Path,
) -> LegacyTimestampMappingAuditResult:
    mechanism = validate_cross_sectional_portfolio_mechanism(mechanism_report_path)
    config_path = Path(evidence_config_path).resolve()
    config = load_legacy_timestamp_mapping_config(config_path)
    repo = mechanism.chain.promotion.repo_root
    if config_path.parent != repo:
        raise MarketDataError("legacy_timestamp_mapping_config_must_be_at_repo_root")
    mechanism_context = _mechanism_context(mechanism)
    semantics_report = _validate_semantics_evidence(repo, config)
    registry = load_dataset_registry(mechanism.chain.promotion.registry_path)
    panel = build_dataset_panel(
        mechanism.chain.promotion.registry_path,
        mechanism.chain.promotion.panels_config_path,
        EXPECTED_PANEL_ID,
    )
    if panel.report["panel_sha256"] != EXPECTED_PANEL_SHA256:
        raise MarketDataError("legacy_timestamp_mapping_panel_mismatch")
    timestamps = tuple(pd.Timestamp(value) for value in panel.frame["timestamp"])
    _validate_signal_grid(timestamps)
    status_by_dataset = {
        str(item["dataset_id"]): str(item["status"])
        for item in mechanism.report["source_chain"]["timestamp_semantics"]["components"]
    }
    _validate_legacy_statuses(config, semantics_report, status_by_dataset)
    frames = {
        entry.dataset_id: canonicalize_ohlcv_frame(pd.read_csv(entry.resolved_path))
        for entry in registry.entries
        if entry.dataset_id in config["required_dataset_ids"]
    }
    entries = tuple(registry.get(dataset_id) for dataset_id in config["required_dataset_ids"])
    datasets = _dataset_rows(entries, frames, status_by_dataset, config, repo)
    mappings = _mapping_rows(entries, frames, timestamps, status_by_dataset, config)
    counts = _mapping_counts(timestamps, mappings)
    authorization = assess_mapping_authorization(
        status_by_dataset,
        structural_valid_count=counts["structural_valid_count"],
        expected_structural_valid_count=EXPECTED_STRUCTURAL_VALID_COUNT,
        internal_missing_count=counts["internal_missing_count"],
    )
    constraints = _constraint_rows(counts, status_by_dataset, authorization)
    dataset_bytes = _serialize_rows(datasets, _DATASET_FIELDS)
    mapping_bytes = _serialize_rows(mappings, _MAPPING_FIELDS)
    constraint_bytes = _serialize_rows(constraints, _CONSTRAINT_FIELDS)
    artifact_hashes = {
        "datasets_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "mappings_sha256": hashlib.sha256(mapping_bytes).hexdigest(),
        "constraints_sha256": hashlib.sha256(constraint_bytes).hexdigest(),
    }
    config_sha256 = _sha256(config_path)
    blockers = [] if authorization else [
        "legacy_timestamp_semantics_not_verified",
        "timestamp_semantics_not_uniform",
    ]
    identity = {
        "schema_version": SCHEMA_VERSION,
        "audit_policy_id": config["audit_policy_id"],
        "mechanism": mechanism_context,
        "chain_sha256": mechanism.chain.report["chain_sha256"],
        "promotion_sha256": mechanism.chain.promotion.report["promotion_sha256"],
        "registry_sha256": mechanism.chain.report["promotion"]["registry"]["sha256"],
        "panel_sha256": panel.report["panel_sha256"],
        "panel_id": EXPECTED_PANEL_ID,
        "config": {"filename": config_path.name, "sha256": config_sha256},
        "timestamp_semantics_evidence": {
            "filename": Path(config["timestamp_semantics_report"]["repo_relative_artifact"]).name,
            "report_sha256": config["timestamp_semantics_report"]["artifact_sha256"],
            "semantics_sha256": semantics_report["semantics_sha256"],
        },
        "mapping_policy": {
            "signal_completion_delay_bars": 1,
            "execution_delay_after_completion_bars": 1,
            "execution_offset_bars": 2,
            "execution_price_field": "open",
            "mapping_policy": "exact_timestamp_no_fill",
            "tail_policy": "final_two_panel_timestamps_are_structural_tail_for_all_assets",
        },
        "counts": counts,
        "component_statuses": [
            {"dataset_id": row["dataset_id"], "status": row["timestamp_semantics_status"]}
            for row in datasets
        ],
        "datasets": datasets,
        "feasibility": {
            "structural_common_next_open_mapping_verified": True,
            "timestamp_semantics_uniformly_verified": authorization,
            "execution_price_mapping_feasible": authorization,
            "pnl_prerequisite_satisfied": authorization,
            "pnl_computation_authorized": False,
        },
        "blockers": blockers,
        "artifacts": artifact_hashes,
    }
    audit_sha256 = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    stem = f"legacy-1h-next-open-audit.{audit_sha256}"
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "datasets": target / f"{stem}.datasets.csv",
        "mappings": target / f"{stem}.mappings.csv",
        "constraints": target / f"{stem}.constraints.csv",
        "report": target / f"{stem}.json",
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "audit_sha256": audit_sha256,
        "audit_status": AUDIT_STATUS_AUTHORIZED if authorization else AUDIT_STATUS_BLOCKED,
        "identity": identity,
        "mechanism": mechanism_context,
        "counts": counts,
        "feasibility": identity["feasibility"],
        "blockers": blockers,
        "artifacts": {
            name: {
                "filename": paths[name].name,
                "sha256": artifact_hashes[f"{name}_sha256"],
                "row_count": len(rows),
            }
            for name, rows in (
                ("datasets", datasets),
                ("mappings", mappings),
                ("constraints", constraints),
            )
        }
        | {"report": {"filename": paths["report"].name}},
        "warnings": [
            "structural_mapping_does_not_prove_timestamp_meaning",
            "legacy_btc_partial_eth_sol_unknown",
            "no_fill_resample_carry_or_nearest_timestamp",
            "no_returns_turnover_cost_or_pnl_computation",
            "no_profitability_or_execution_claim",
            "no_automatic_readiness_upgrade",
        ],
        "profitability_evidence": False,
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    _commit_bytes(paths["datasets"], dataset_bytes)
    _commit_bytes(paths["mappings"], mapping_bytes)
    _commit_bytes(paths["constraints"], constraint_bytes)
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return LegacyTimestampMappingAuditResult(report, {key: str(value) for key, value in paths.items()})


def load_legacy_timestamp_mapping_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"legacy timestamp mapping config not found: {config_path}")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"legacy timestamp mapping config YAML is invalid: {config_path}") from exc
    if not isinstance(value, dict):
        raise ValueError("legacy timestamp mapping config must be a mapping")
    if value != EXPECTED_CONFIG:
        raise MarketDataError("legacy_timestamp_mapping_config_not_frozen")
    return json.loads(json.dumps(value, sort_keys=True))


def assess_mapping_authorization(
    dataset_statuses: dict[str, str],
    *,
    structural_valid_count: int,
    expected_structural_valid_count: int,
    internal_missing_count: int,
) -> bool:
    if any(status == "conflict" for status in dataset_statuses.values()):
        raise MarketDataError("legacy_timestamp_mapping_conflicting_semantics")
    return (
        len(dataset_statuses) == 6
        and set(dataset_statuses.values()) == {"verified_open_time"}
        and structural_valid_count == expected_structural_valid_count
        and internal_missing_count == 0
    )


def format_legacy_timestamp_mapping_audit(result: LegacyTimestampMappingAuditResult) -> str:
    report = result.report
    return "\n".join(
        [
            f"audit_status: {report['audit_status']}",
            f"audit_sha256: {report['audit_sha256']}",
            f"signal_timestamp_count: {report['counts']['signal_timestamp_count']}",
            f"mapping_row_count: {report['counts']['mapping_row_count']}",
            f"structural_valid_count: {report['counts']['structural_valid_count']}",
            f"tail_count: {report['counts']['tail_count']}",
            "execution_price_mapping_feasible: "
            f"{str(report['feasibility']['execution_price_mapping_feasible']).lower()}",
            "pnl_computation_authorized: false",
            "readiness_changed: false",
        ]
    )


def _mechanism_context(mechanism: ValidatedCrossSectionalPortfolioMechanism) -> dict[str, Any]:
    report = mechanism.report
    if (
        report.get("mechanism_sha256") != EXPECTED_MECHANISM_SHA256
        or report.get("variant_count") != 36
        or report.get("feasibility", {}).get("pnl_computation_authorized") is not False
    ):
        raise MarketDataError("legacy_timestamp_mapping_mechanism_mismatch")
    return {
        "mechanism_sha256": report["mechanism_sha256"],
        "report_filename": mechanism.report_path.name,
        "report_sha256": _sha256(mechanism.report_path),
    }


def _validate_semantics_evidence(repo: Path, config: dict[str, Any]) -> dict[str, Any]:
    info = config["timestamp_semantics_report"]
    path = _repo_file(repo, info["repo_relative_artifact"])
    if _sha256(path) != info["artifact_sha256"]:
        raise MarketDataError("legacy_timestamp_mapping_semantics_report_hash_mismatch")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("legacy_timestamp_mapping_invalid_semantics_report") from exc
    if (
        not isinstance(report, dict)
        or report.get("semantics_sha256") != info["semantics_sha256"]
        or report.get("assessment_status") != "independent_timestamp_semantics_evidence_only"
    ):
        raise MarketDataError("legacy_timestamp_mapping_invalid_semantics_report")
    for artifact in report.get("artifacts", {}).values():
        if not isinstance(artifact, dict):
            raise MarketDataError("legacy_timestamp_mapping_invalid_semantics_artifact")
        sibling = path.parent / str(artifact.get("filename"))
        if not sibling.is_file() or _sha256(sibling) != artifact.get("sha256"):
            raise MarketDataError("legacy_timestamp_mapping_semantics_artifact_hash_mismatch")
    for dataset in config["legacy_datasets"]:
        for evidence in dataset["evidence"]:
            evidence_path = _repo_file(repo, evidence["repo_relative_artifact"])
            if _sha256(evidence_path) != evidence["artifact_sha256"]:
                raise MarketDataError("legacy_timestamp_mapping_evidence_hash_mismatch")
    return report


def _validate_legacy_statuses(
    config: dict[str, Any],
    semantics_report: dict[str, Any],
    promotion_statuses: dict[str, str],
) -> None:
    reported = {
        str(item["dataset_id"]): str(item["status"])
        for item in semantics_report.get("datasets", [])
        if item.get("timeframe") == "1h"
    }
    for item in config["legacy_datasets"]:
        dataset_id = item["dataset_id"]
        expected = item["expected_status"]
        if reported.get(dataset_id) != expected or promotion_statuses.get(dataset_id) != expected:
            raise MarketDataError("legacy_timestamp_mapping_semantics_status_mismatch")
    if any(status == "conflict" for status in promotion_statuses.values()):
        raise MarketDataError("legacy_timestamp_mapping_conflicting_semantics")


def _validate_signal_grid(timestamps: tuple[pd.Timestamp, ...]) -> None:
    if len(timestamps) != EXPECTED_SIGNAL_TIMESTAMP_COUNT or len(set(timestamps)) != len(timestamps):
        raise MarketDataError("legacy_timestamp_mapping_signal_grid_shape_mismatch")
    if any(right - left != pd.Timedelta(hours=1) for left, right in zip(timestamps, timestamps[1:])):
        raise MarketDataError("legacy_timestamp_mapping_signal_grid_offgrid")


def _dataset_rows(
    entries: tuple[Any, ...],
    frames: dict[str, pd.DataFrame],
    statuses: dict[str, str],
    config: dict[str, Any],
    repo: Path,
) -> list[dict[str, Any]]:
    evidence_counts = {item["dataset_id"]: len(item["evidence"]) for item in config["legacy_datasets"]}
    rows = []
    for entry in entries:
        frame = frames[entry.dataset_id]
        evidence_hashes = [
            _sha256(_repo_file(repo, relative)) for relative in entry.source_evidence
        ]
        status = statuses[entry.dataset_id]
        rows.append(
            {
                "dataset_id": entry.dataset_id,
                "symbol": entry.symbol,
                "source_status": entry.source_status,
                "timestamp_semantics_status": status,
                "semantic_authorized": status == "verified_open_time",
                "raw_sha256": entry.expected["raw_sha256"],
                "canonical_sha256": entry.expected["canonical_sha256"],
                "first_timestamp": pd.Timestamp(frame["timestamp"].iloc[0]).isoformat(),
                "last_timestamp": pd.Timestamp(frame["timestamp"].iloc[-1]).isoformat(),
                "bar_count": len(frame),
                "lineage_coverage": (
                    "complete" if status == "verified_open_time" else "partial" if status == "partial_unverified" else "none"
                ),
                "evidence_count": evidence_counts.get(
                    entry.dataset_id, len(entry.source_evidence)
                ),
                "evidence_artifact_hashes": "|".join(evidence_hashes),
            }
        )
    return rows


def build_exact_next_open_mapping_rows(
    entries: tuple[Any, ...],
    frames: dict[str, pd.DataFrame],
    timestamps: tuple[pd.Timestamp, ...],
    statuses: dict[str, str],
    config: dict[str, Any],
    *,
    error_prefix: str = "legacy_timestamp_mapping",
) -> list[dict[str, Any]]:
    """Build the shared exact t+2h open mapping without filling or substitution."""
    common_last = timestamps[-1]
    indexes = {}
    for dataset_id, frame in frames.items():
        indexed = frame.set_index("timestamp")["open"]
        if not indexed.index.is_unique:
            raise MarketDataError(f"{error_prefix}_duplicate_timestamp")
        indexes[dataset_id] = indexed
    rows = []
    for signal_timestamp in timestamps:
        completion_timestamp = signal_timestamp + pd.Timedelta(
            hours=config["signal_completion_delay_bars"]
        )
        execution_timestamp = signal_timestamp + pd.Timedelta(hours=config["execution_offset_bars"])
        tail = execution_timestamp > common_last
        for entry in entries:
            exact = False
            finite = False
            mapped_open: float | str = ""
            if not tail:
                series = indexes[entry.dataset_id]
                exact = execution_timestamp in series.index
                if exact:
                    mapped_open = float(series.loc[execution_timestamp])
                    finite = math.isfinite(mapped_open)
                if not exact or not finite:
                    raise MarketDataError(f"{error_prefix}_internal_exact_open_missing")
            status = "tail_outside_common_panel" if tail else "exact_finite_open"
            rows.append(
                {
                    "signal_timestamp": signal_timestamp.isoformat(),
                    "signal_completion_timestamp": completion_timestamp.isoformat(),
                    "execution_timestamp": execution_timestamp.isoformat(),
                    "dataset_id": entry.dataset_id,
                    "symbol": entry.symbol,
                    "execution_price_field": "open",
                    "structural_status": status,
                    "mapped_source_timestamp": "" if tail else execution_timestamp.isoformat(),
                    "mapped_open": mapped_open,
                    "exact_row_exists": exact,
                    "finite_open": finite,
                    "timestamp_semantics_status": statuses[entry.dataset_id],
                    "semantic_authorized": (not tail and statuses[entry.dataset_id] == "verified_open_time"),
                }
            )
    return rows


def _mapping_rows(
    entries: tuple[Any, ...],
    frames: dict[str, pd.DataFrame],
    timestamps: tuple[pd.Timestamp, ...],
    statuses: dict[str, str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    return build_exact_next_open_mapping_rows(entries, frames, timestamps, statuses, config)


def _mapping_counts(timestamps: tuple[pd.Timestamp, ...], rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "signal_timestamp_count": len(timestamps),
        "mapping_row_count": len(rows),
        "structural_valid_count": sum(row["structural_status"] == "exact_finite_open" for row in rows),
        "tail_count": sum(row["structural_status"] == "tail_outside_common_panel" for row in rows),
        "internal_missing_count": sum(row["structural_status"] not in {"exact_finite_open", "tail_outside_common_panel"} for row in rows),
    }
    if counts != {
        "signal_timestamp_count": EXPECTED_SIGNAL_TIMESTAMP_COUNT,
        "mapping_row_count": EXPECTED_MAPPING_ROW_COUNT,
        "structural_valid_count": EXPECTED_STRUCTURAL_VALID_COUNT,
        "tail_count": EXPECTED_TAIL_COUNT,
        "internal_missing_count": 0,
    }:
        raise MarketDataError("legacy_timestamp_mapping_count_mismatch")
    return counts


def _constraint_rows(counts: dict[str, int], statuses: dict[str, str], authorized: bool) -> list[dict[str, Any]]:
    observed_statuses = "|".join(f"{key}:{statuses[key]}" for key in sorted(statuses))
    return [
        _constraint("required_asset_count", "pass", len(statuses), 6, False, False),
        _constraint("signal_timestamp_count", "pass", counts["signal_timestamp_count"], EXPECTED_SIGNAL_TIMESTAMP_COUNT, False, False),
        _constraint("exact_structural_mapping", "pass", counts["structural_valid_count"], EXPECTED_STRUCTURAL_VALID_COUNT, False, False),
        _constraint("tail_rows", "disclosed", counts["tail_count"], EXPECTED_TAIL_COUNT, False, False),
        _constraint("internal_missing_rows", "pass", counts["internal_missing_count"], 0, True, True),
        _constraint("mapping_policy", "pass", "exact_timestamp_no_fill", "exact_timestamp_no_fill", False, False),
        _constraint("legacy_timestamp_semantics", "pass" if authorized else "blocker", observed_statuses, "all_verified_open_time", True, True),
        _constraint("execution_price_mapping", "pass" if authorized else "blocker", authorized, True, True, True),
        _constraint("membership_mode", "limitation", "static_current_snapshot_convenience_sample", "historical_point_in_time", False, True),
        _constraint("historical_point_in_time_membership", "limitation", False, True, False, True),
        _constraint("survivorship_bias_resolved", "limitation", False, True, False, True),
        _constraint("prior_related_results_exist", "disclosed", True, True, False, False),
        _constraint("profitability_evidence", "prohibited", False, False, False, False),
        _constraint("pnl_computation", "prohibited", False, False, False, False),
    ]


def _constraint(name: str, status: str, observed: Any, required: Any, blocks_execution: bool, blocks_pnl: bool) -> dict[str, Any]:
    return {
        "constraint_name": name,
        "status": status,
        "observed": observed,
        "required": required,
        "blocks_execution_price_mapping": blocks_execution,
        "blocks_pnl_computation": blocks_pnl,
    }


def _repo_file(repo: Path, relative: str) -> Path:
    candidate = (repo / relative).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError as exc:
        raise MarketDataError("legacy_timestamp_mapping_path_escape") from exc
    if not candidate.is_file():
        raise MarketDataError("legacy_timestamp_mapping_artifact_missing")
    return candidate


def _serialize_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row[field]) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return format(value, ".15g")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"content_addressed_collision:{path.name}")
        return
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
