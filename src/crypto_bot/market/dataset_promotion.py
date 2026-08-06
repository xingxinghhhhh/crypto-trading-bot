from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.data_quality import validate_ohlcv_csv
from crypto_bot.market.dataset_panel import (
    INNER_EXACT_ALIGNMENT,
    build_dataset_panel,
    load_dataset_panel_config,
)
from crypto_bot.market.dataset_registry import (
    audit_dataset_registry,
    canonical_ohlcv_sha256,
    load_dataset_registry,
)
from crypto_bot.market.okx_universe_intake import (
    OkxUniverseValidatedIntake,
    validate_okx_timestamp_semantics_report,
    validate_okx_universe_intake,
)


PROMOTION_SCHEMA_VERSION = 1
PROMOTION_POLICY_VERSION = "okx_convenience_candidate_promotion_v1"
CONVENIENCE_POLICY_VERSION = "current_okx_live_convenience_snapshot_v1"
STABLE_DATA_ROOT = "data/promoted/okx_convenience_v1"
BASE_PANEL_ID = "btc_eth_sol_4h_v1"
TARGET_PANEL_ID = "btc_eth_sol_knc_swftc_bico_4h_v1"
EXPECTED_BASE_DATASETS = (
    "btc_usdt_4h_v1",
    "eth_usdt_4h_v1",
    "sol_usdt_4h_v1",
)
EXPECTED_CANDIDATES = (
    ("KNC-USDT", "okx_knc_usdt_4h_b96aa6011796"),
    ("SWFTC-USDT", "okx_swftc_usdt_4h_b96aa6011796"),
    ("BICO-USDT", "okx_bico_usdt_4h_b96aa6011796"),
)
EXPECTED_PANEL = {
    "union_bar_count": 14135,
    "intersection_bar_count": 9486,
    "coverage_rate": 0.67110010612,
    "common_first_timestamp": "2022-01-01T00:00:00+00:00",
    "common_last_timestamp": "2026-04-30T20:00:00+00:00",
}
EXPECTED_BOUNDARY_DROPS = {
    "btc_usdt_4h_v1": (4088, 0),
    "eth_usdt_4h_v1": (4088, 0),
    "sol_usdt_4h_v1": (3047, 0),
    "okx_knc_usdt_4h_b96aa6011796": (0, 561),
    "okx_swftc_usdt_4h_b96aa6011796": (0, 561),
    "okx_bico_usdt_4h_b96aa6011796": (0, 561),
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class DatasetPromotionResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class ValidatedDatasetPromotion:
    report_path: Path
    report: dict[str, Any]
    repo_root: Path
    registry_path: Path
    panels_config_path: Path
    registry_audit_path: Path
    panel_audit_path: Path


def validate_dataset_promotion_report(
    report_path: str | Path,
) -> ValidatedDatasetPromotion:
    """Validate a promotion marker and all promoted dependencies without writes."""
    marker_path = Path(report_path).resolve()
    report = _load_json_mapping(marker_path, "promotion_invalid_report")
    if report.get("schema_version") != PROMOTION_SCHEMA_VERSION:
        raise MarketDataError("promotion_invalid_report_schema")
    identity = report.get("identity")
    if not isinstance(identity, dict):
        raise MarketDataError("promotion_identity_missing")
    promotion_sha = _require_sha256(report.get("promotion_sha256"), "promotion_sha256")
    if marker_path.name != f"okx-universe-promotion.{promotion_sha}.json":
        raise MarketDataError("promotion_report_filename_mismatch")
    if hashlib.sha256(_canonical_json_bytes(identity)).hexdigest() != promotion_sha:
        raise MarketDataError("promotion_identity_hash_mismatch")
    if (
        report.get("promotion_status") != "verified_offline_candidate_promotion"
        or report.get("claims") != _claims()
        or identity.get("claims") != _claims()
        or report.get("readiness_changed") is not False
        or report.get("automatic_factor_approval") is not False
    ):
        raise MarketDataError("promotion_report_safety_mismatch")

    promoted_registry = _required_mapping(identity.get("promoted_registry"), "promoted_registry")
    promoted_panel = _required_mapping(identity.get("promoted_panel"), "promoted_panel")
    registry_relative = _required_relative_path(
        promoted_registry.get("repo_relative_path"), "promoted_registry_path"
    )
    panels_relative = _required_relative_path(
        promoted_panel.get("config_repo_relative_path"), "promoted_panels_path"
    )
    repo_root = _locate_promotion_repo_root(
        marker_path,
        registry_relative,
        _require_sha256(promoted_registry.get("sha256"), "promoted_registry_sha256"),
    )
    registry_path = (repo_root / registry_relative).resolve()
    panels_path = (repo_root / panels_relative).resolve()
    if not panels_path.is_relative_to(repo_root) or _sha256(panels_path) != _require_sha256(
        promoted_panel.get("config_sha256"), "promoted_panels_sha256"
    ):
        raise MarketDataError("promotion_promoted_panels_hash_mismatch")

    artifacts = _required_mapping(report.get("artifacts"), "promotion_artifacts")
    registry_artifact = _required_mapping(
        artifacts.get("promoted_registry"), "promoted_registry_artifact"
    )
    panels_artifact = _required_mapping(
        artifacts.get("promoted_panels"), "promoted_panels_artifact"
    )
    if registry_artifact != {
        "repo_relative_path": registry_relative.as_posix(),
        "sha256": promoted_registry["sha256"],
    } or panels_artifact != {
        "repo_relative_path": panels_relative.as_posix(),
        "sha256": promoted_panel["config_sha256"],
    }:
        raise MarketDataError("promotion_stable_artifact_identity_mismatch")

    registry_audit_artifact = _required_mapping(
        artifacts.get("registry_audit"), "registry_audit_artifact"
    )
    panel_audit_artifact = _required_mapping(
        artifacts.get("panel_audit"), "panel_audit_artifact"
    )
    registry_audit_path = _safe_sibling(marker_path, registry_audit_artifact.get("filename"))
    panel_audit_path = _safe_sibling(marker_path, panel_audit_artifact.get("filename"))
    if _sha256(registry_audit_path) != _require_sha256(
        registry_audit_artifact.get("sha256"), "registry_audit_sha256"
    ):
        raise MarketDataError("promotion_registry_audit_hash_mismatch")
    if _sha256(panel_audit_path) != _require_sha256(
        panel_audit_artifact.get("sha256"), "panel_audit_sha256"
    ):
        raise MarketDataError("promotion_panel_audit_hash_mismatch")
    if registry_audit_artifact.get("filename") != promoted_registry.get("audit_filename"):
        raise MarketDataError("promotion_registry_audit_identity_mismatch")
    if panel_audit_artifact.get("filename") != promoted_panel.get("audit_filename"):
        raise MarketDataError("promotion_panel_audit_identity_mismatch")

    observed_registry_audit = audit_dataset_registry(registry_path)
    if _load_json_mapping(registry_audit_path, "promotion_invalid_registry_audit") != observed_registry_audit:
        raise MarketDataError("promotion_registry_audit_replay_mismatch")
    config = _required_mapping(identity.get("config"), "promotion_config")
    panel_result = build_dataset_panel(
        registry_path,
        panels_path,
        _required_string(config.get("target_panel_id"), "target_panel_id"),
    )
    if _load_json_mapping(panel_audit_path, "promotion_invalid_panel_audit") != panel_result.report:
        raise MarketDataError("promotion_panel_audit_replay_mismatch")
    if panel_result.report.get("panel_sha256") != promoted_panel.get("panel_sha256"):
        raise MarketDataError("promotion_panel_sha_mismatch")
    if panel_result.report.get("alignment_summary") != promoted_panel.get("alignment"):
        raise MarketDataError("promotion_panel_alignment_identity_mismatch")

    dataset_identities = identity.get("datasets")
    if not isinstance(dataset_identities, list) or len(dataset_identities) != 3:
        raise MarketDataError("promotion_dataset_identity_count_mismatch")
    for dataset in dataset_identities:
        item = _required_mapping(dataset, "promoted_dataset")
        destination_relative = _required_relative_path(
            item.get("destination_csv_repo_relative_path"), "destination_csv_path"
        )
        lineage_relative = _required_relative_path(
            item.get("lineage_repo_relative_path"), "lineage_path"
        )
        destination_path = (repo_root / destination_relative).resolve()
        lineage_path = (repo_root / lineage_relative).resolve()
        if not destination_path.is_relative_to(repo_root) or not lineage_path.is_relative_to(repo_root):
            raise MarketDataError("promotion_dataset_path_escape")
        if _sha256(destination_path) != _require_sha256(
            item.get("destination_raw_sha256"), "destination_raw_sha256"
        ):
            raise MarketDataError("promotion_destination_hash_mismatch")
        if canonical_ohlcv_sha256(destination_path) != _require_sha256(
            item.get("destination_canonical_sha256"), "destination_canonical_sha256"
        ):
            raise MarketDataError("promotion_destination_canonical_hash_mismatch")
        if _sha256(lineage_path) != _require_sha256(item.get("lineage_sha256"), "lineage_sha256"):
            raise MarketDataError("promotion_lineage_hash_mismatch")
        lineage = _load_json_mapping(lineage_path, "promotion_invalid_lineage_manifest")
        if (
            lineage.get("dataset_id") != item.get("dataset_id")
            or lineage.get("timestamp_semantics") != "verified_open_time"
            or lineage.get("claims") != _claims()
            or lineage.get("destination", {}).get("repo_relative_path")
            != destination_relative.as_posix()
        ):
            raise MarketDataError("promotion_lineage_identity_mismatch")

    semantics = _required_mapping(identity.get("timestamp_semantics"), "promotion_semantics")
    if (
        semantics.get("panel_sha256") != promoted_panel.get("panel_sha256")
        or semantics.get("generic_panel_audit_status") != "unverified"
        or semantics.get("aggregate_status") != "mixed_unverified"
        or semantics.get("timestamp_semantics_uniform") is not False
        or semantics.get("old_datasets_upgraded") is not False
    ):
        raise MarketDataError("promotion_timestamp_semantics_mismatch")
    return ValidatedDatasetPromotion(
        report_path=marker_path,
        report=report,
        repo_root=repo_root,
        registry_path=registry_path,
        panels_config_path=panels_path,
        registry_audit_path=registry_audit_path,
        panel_audit_path=panel_audit_path,
    )


def promote_okx_universe_candidates(
    base_registry_path: str | Path,
    base_panels_config_path: str | Path,
    semantics_report_path: str | Path,
    capture_report_path: str | Path,
    intake_report_path: str | Path,
    promotion_config_path: str | Path,
    output_dir: str | Path,
) -> DatasetPromotionResult:
    """Promote one frozen OKX intake into an independent six-asset Registry and Panel."""
    base_registry_path = Path(base_registry_path).resolve()
    if not base_registry_path.is_file():
        raise FileNotFoundError(f"base dataset Registry not found: {base_registry_path}")
    repo_root = base_registry_path.parent.resolve()
    base_panels_path = _existing_repo_file(repo_root, base_panels_config_path, "base panels config")
    semantics_path = _existing_repo_file(repo_root, semantics_report_path, "semantics report")
    capture_path = _existing_repo_file(repo_root, capture_report_path, "capture report")
    intake_path = _existing_repo_file(repo_root, intake_report_path, "intake report")
    promotion_config_path = _existing_repo_file(
        repo_root, promotion_config_path, "promotion config"
    )
    destination = _resolve_output_dir(repo_root, output_dir)

    config = _load_promotion_config(promotion_config_path)
    stable_root = _resolve_stable_root(repo_root, config["stable_data_root"])
    validated = validate_okx_universe_intake(capture_path, intake_path)
    semantics_identity = validate_okx_timestamp_semantics_report(semantics_path)
    semantics_report = _load_json_mapping(semantics_path, "promotion_invalid_semantics_report")
    capture_identity = validated.capture.capture["identity"]
    if capture_identity["semantics"] != semantics_identity:
        raise MarketDataError("promotion_capture_semantics_identity_mismatch")

    base_registry = load_dataset_registry(base_registry_path)
    base_registry_sha = _sha256(base_registry_path)
    if capture_identity["registry"]["sha256"] != base_registry_sha:
        raise MarketDataError("promotion_base_registry_capture_mismatch")
    base_registry_audit = audit_dataset_registry(base_registry_path)
    if not base_registry_audit["valid"]:
        raise MarketDataError("promotion_base_registry_invalid")
    base_panel_spec = load_dataset_panel_config(base_panels_path).get(config["base_panel_id"])
    if base_panel_spec.dataset_ids != EXPECTED_BASE_DATASETS:
        raise MarketDataError("promotion_base_panel_membership_mismatch")
    base_panel = build_dataset_panel(
        base_registry_path,
        base_panels_path,
        config["base_panel_id"],
    )
    _validate_base_semantics(
        semantics_report,
        base_panel.report["panel_sha256"],
        base_panel_spec.dataset_ids,
    )
    _validate_config_candidates(config, validated)

    candidate_by_inst = {row["inst_id"]: row for row in validated.capture.datasets}
    candidate_schema_by_id = {
        row["dataset_id"]: row for row in validated.capture.candidate_datasets
    }
    prepared = _prepare_promoted_datasets(
        repo_root,
        stable_root,
        validated,
        candidate_by_inst,
        candidate_schema_by_id,
    )

    old_entries = [base_registry.get(dataset_id) for dataset_id in EXPECTED_BASE_DATASETS]
    registry_payload = {
        "schema_version": 1,
        "datasets": [
            {"dataset_id": entry.dataset_id, **entry.declared_metadata()}
            for entry in old_entries
        ]
        + [item["registry_entry"] for item in prepared],
    }
    registry_bytes = _yaml_bytes(registry_payload)
    promoted_registry_sha = hashlib.sha256(registry_bytes).hexdigest()
    promoted_registry_path = repo_root / f"promoted-registry.{promoted_registry_sha}.yaml"

    panel_payload = {
        "schema_version": 1,
        "panels": [
            {
                "panel_id": config["target_panel_id"],
                "dataset_ids": [
                    *EXPECTED_BASE_DATASETS,
                    *(dataset_id for _, dataset_id in EXPECTED_CANDIDATES),
                ],
                "alignment": INNER_EXACT_ALIGNMENT,
            }
        ],
    }
    panel_config_bytes = _yaml_bytes(panel_payload)
    panel_config_sha = hashlib.sha256(panel_config_bytes).hexdigest()
    promoted_panels_path = repo_root / f"promoted-panels.{panel_config_sha}.yaml"

    for item in prepared:
        _commit_bytes(item["destination_path"], item["csv_bytes"])
    _validate_promoted_csvs(prepared)
    for item in prepared:
        _commit_bytes(item["lineage_path"], item["lineage_bytes"])
    _commit_bytes(promoted_registry_path, registry_bytes)

    promoted_registry_audit = audit_dataset_registry(promoted_registry_path)
    if not promoted_registry_audit["valid"] or promoted_registry_audit["dataset_count"] != 6:
        raise MarketDataError("promotion_promoted_registry_invalid")
    _validate_old_registry_entries(base_registry, promoted_registry_path)
    registry_audit_bytes = _json_bytes(promoted_registry_audit)
    registry_audit_sha = hashlib.sha256(registry_audit_bytes).hexdigest()
    registry_audit_path = destination / f"promoted-registry-audit.{registry_audit_sha}.json"
    _commit_bytes(registry_audit_path, registry_audit_bytes)

    _commit_bytes(promoted_panels_path, panel_config_bytes)
    promoted_panel = build_dataset_panel(
        promoted_registry_path,
        promoted_panels_path,
        config["target_panel_id"],
    )
    _validate_promoted_panel(promoted_panel.report)
    panel_audit_bytes = _json_bytes(promoted_panel.report)
    panel_audit_path = destination / f"promoted-panel.{promoted_panel.report['panel_sha256']}.json"
    _commit_bytes(panel_audit_path, panel_audit_bytes)

    semantics_assessment = _promotion_semantics_assessment(
        semantics_report,
        validated,
        promoted_panel.report["panel_sha256"],
    )
    claims = _claims()
    identity = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "promotion_id": config["promotion_id"],
        "promotion_policy_version": PROMOTION_POLICY_VERSION,
        "config": config,
        "config_sha256": _sha256(promotion_config_path),
        "inputs": {
            "base_registry": {
                "filename": base_registry_path.name,
                "sha256": base_registry_sha,
                "dataset_count": base_registry_audit["dataset_count"],
            },
            "base_panels_config": {
                "filename": base_panels_path.name,
                "sha256": _sha256(base_panels_path),
                "base_panel_sha256": base_panel.report["panel_sha256"],
            },
            "semantics": semantics_identity,
            "capture": {
                "capture_sha256": validated.capture.capture["capture_sha256"],
                "report_sha256": _sha256(capture_path),
            },
            "intake": {
                "intake_sha256": validated.intake_report["intake_sha256"],
                "report_sha256": _sha256(intake_path),
                "registry_candidates_sha256": _sha256(
                    validated.artifact_paths["registry_candidates"]
                ),
            },
        },
        "datasets": [item["identity"] for item in prepared],
        "promoted_registry": {
            "repo_relative_path": _repo_relative(repo_root, promoted_registry_path),
            "sha256": promoted_registry_sha,
            "audit_filename": registry_audit_path.name,
            "audit_sha256": registry_audit_sha,
        },
        "promoted_panel": {
            "config_repo_relative_path": _repo_relative(repo_root, promoted_panels_path),
            "config_sha256": panel_config_sha,
            "audit_filename": panel_audit_path.name,
            "audit_sha256": hashlib.sha256(panel_audit_bytes).hexdigest(),
            "panel_sha256": promoted_panel.report["panel_sha256"],
            "alignment": promoted_panel.report["alignment_summary"],
            "components": _panel_component_identity(promoted_panel.report),
        },
        "timestamp_semantics": semantics_assessment,
        "claims": claims,
        "policies": {
            "network_access": "forbidden_offline_only",
            "candidate_reselection": "forbidden",
            "old_registry_or_panel_mutation": "forbidden",
            "marker_required_for_consumption": True,
            "randomness": "none",
        },
    }
    promotion_sha = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    report = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "promotion_sha256": promotion_sha,
        "promotion_status": "verified_offline_candidate_promotion",
        "identity": identity,
        "claims": claims,
        "readiness_changed": False,
        "automatic_factor_approval": False,
        "artifacts": {
            "promoted_registry": {
                "repo_relative_path": _repo_relative(repo_root, promoted_registry_path),
                "sha256": promoted_registry_sha,
            },
            "promoted_panels": {
                "repo_relative_path": _repo_relative(repo_root, promoted_panels_path),
                "sha256": panel_config_sha,
            },
            "registry_audit": {
                "filename": registry_audit_path.name,
                "sha256": registry_audit_sha,
            },
            "panel_audit": {
                "filename": panel_audit_path.name,
                "sha256": hashlib.sha256(panel_audit_bytes).hexdigest(),
            },
        },
        "warnings": [
            "current_live_convenience_sample_only",
            "historical_point_in_time_membership_not_proven",
            "survivorship_bias_not_resolved",
            "mixed_timestamp_semantics_not_uniform",
            "no_profitability_or_strategy_approval",
        ],
    }
    report_path = destination / f"okx-universe-promotion.{promotion_sha}.json"
    _commit_bytes(report_path, _json_bytes(report))
    paths = {
        "promoted_registry": promoted_registry_path,
        "promoted_panels": promoted_panels_path,
        "registry_audit": registry_audit_path,
        "panel_audit": panel_audit_path,
        "report": report_path,
    }
    for item in prepared:
        paths[f"{item['inst_id']}_csv"] = item["destination_path"]
        paths[f"{item['inst_id']}_lineage"] = item["lineage_path"]
    return DatasetPromotionResult(
        report=report,
        export_paths={name: str(path) for name, path in paths.items()},
    )


def format_dataset_promotion(result: DatasetPromotionResult) -> str:
    identity = result.report["identity"]
    panel = identity["promoted_panel"]
    semantics = identity["timestamp_semantics"]
    return "\n".join(
        [
            f"promotion_sha256: {result.report['promotion_sha256']}",
            f"promotion_status: {result.report['promotion_status']}",
            f"registry_sha256: {identity['promoted_registry']['sha256']}",
            f"panel_sha256: {panel['panel_sha256']}",
            f"intersection_bar_count: {panel['alignment']['intersection_bar_count']}",
            f"union_bar_count: {panel['alignment']['union_bar_count']}",
            f"timestamp_semantics: {semantics['aggregate_status']}",
            f"timestamp_semantics_uniform: {str(semantics['timestamp_semantics_uniform']).lower()}",
            "historical_point_in_time_membership: false",
            "survivorship_bias_resolved: false",
            "profitability_evidence: false",
            "strategy_approval: false",
        ]
    )


def _load_promotion_config(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("promotion config YAML is invalid") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != PROMOTION_SCHEMA_VERSION:
        raise ValueError("promotion config schema_version is invalid")
    expected = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "promotion_id": "okx_convenience_six_asset_4h_v1",
        "base_panel_id": BASE_PANEL_ID,
        "target_panel_id": TARGET_PANEL_ID,
        "stable_data_root": STABLE_DATA_ROOT,
        "alignment": INNER_EXACT_ALIGNMENT,
        "convenience_policy_version": CONVENIENCE_POLICY_VERSION,
        "expected_candidates": [
            {"inst_id": inst_id, "dataset_id": dataset_id}
            for inst_id, dataset_id in EXPECTED_CANDIDATES
        ],
        "expected_panel": {
            **EXPECTED_PANEL,
            "boundary_drops": {
                dataset_id: {
                    "before_common_start": before,
                    "after_common_end": after,
                    "missing_inside_common_window": 0,
                }
                for dataset_id, (before, after) in EXPECTED_BOUNDARY_DROPS.items()
            },
        },
    }
    normalized = json.loads(json.dumps(raw, sort_keys=True))
    if normalized != json.loads(json.dumps(expected, sort_keys=True)):
        raise ValueError("promotion config does not match the frozen policy")
    return normalized


def _validate_config_candidates(
    config: dict[str, Any], validated: OkxUniverseValidatedIntake
) -> None:
    expected = [(row["inst_id"], row["dataset_id"]) for row in config["expected_candidates"]]
    observed = [(row["inst_id"], row["dataset_id"]) for row in validated.capture.datasets]
    if observed != expected:
        raise MarketDataError("promotion_candidate_membership_or_order_mismatch")


def _prepare_promoted_datasets(
    repo_root: Path,
    stable_root: Path,
    validated: OkxUniverseValidatedIntake,
    candidate_by_inst: dict[str, dict[str, Any]],
    candidate_schema_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for inst_id, dataset_id in EXPECTED_CANDIDATES:
        row = candidate_by_inst[inst_id]
        candidate = candidate_schema_by_id[dataset_id]
        source_path = validated.capture.capture_path.parent / row["csv_filename"]
        bundle_path = validated.capture.capture_path.parent / row["history_bundle_filename"]
        source_path = source_path.resolve()
        bundle_path = bundle_path.resolve()
        source_bytes = source_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != row["raw_sha256"]:
            raise MarketDataError(f"promotion_source_csv_hash_mismatch:{inst_id}")
        destination_name = f"{dataset_id}.{row['raw_sha256']}.csv"
        destination_path = (stable_root / destination_name).resolve()
        if not destination_path.is_relative_to(stable_root):
            raise MarketDataError("promotion_destination_path_escape")
        destination_relative = _repo_relative(repo_root, destination_path)
        lineage_payload = {
            "schema_version": 1,
            "dataset_id": dataset_id,
            "inst_id": inst_id,
            "symbol": row["symbol"],
            "timeframe": row["timeframe"],
            "capture": {
                "capture_sha256": validated.capture.capture["capture_sha256"],
                "report_sha256": _sha256(validated.capture.capture_path),
                "source_csv_repo_relative_path": _repo_relative(repo_root, source_path),
                "source_history_bundle_repo_relative_path": _repo_relative(repo_root, bundle_path),
                "source_history_bundle_sha256": row["history_bundle_sha256"],
            },
            "intake": {
                "intake_sha256": validated.intake_report["intake_sha256"],
                "report_sha256": _sha256(validated.intake_path),
            },
            "destination": {
                "repo_relative_path": destination_relative,
                "raw_sha256": row["raw_sha256"],
                "canonical_sha256": row["canonical_sha256"],
                "bar_count": row["bar_count"],
                "first_timestamp": row["first_timestamp"],
                "last_timestamp": row["last_timestamp"],
            },
            "timestamp_semantics": "verified_open_time",
            "lineage_status": "complete_direct_okx_public",
            "claims": _claims(),
        }
        lineage_bytes = _json_bytes(lineage_payload)
        lineage_sha = hashlib.sha256(lineage_bytes).hexdigest()
        lineage_path = (stable_root / f"lineage.{dataset_id}.{lineage_sha}.json").resolve()
        lineage_relative = _repo_relative(repo_root, lineage_path)
        registry_entry = {
            "dataset_id": dataset_id,
            "symbol": candidate["symbol"],
            "timeframe": candidate["timeframe"],
            "path": destination_relative,
            "source": {
                "status": "verified",
                "evidence": [lineage_relative],
                "provider": "okx_public_history_candles",
                "lineage": "complete_direct_okx_public",
                "timestamp_semantics": "verified_open_time",
                "capture_sha256": validated.capture.capture["capture_sha256"],
                "intake_sha256": validated.intake_report["intake_sha256"],
            },
            "quality": {"validator": "validate_ohlcv_csv"},
            "expected": dict(candidate["expected"]),
        }
        identity = {
            "dataset_id": dataset_id,
            "inst_id": inst_id,
            "source_csv_repo_relative_path": _repo_relative(repo_root, source_path),
            "source_raw_sha256": row["raw_sha256"],
            "source_canonical_sha256": row["canonical_sha256"],
            "destination_csv_repo_relative_path": destination_relative,
            "destination_raw_sha256": row["raw_sha256"],
            "destination_canonical_sha256": row["canonical_sha256"],
            "lineage_repo_relative_path": lineage_relative,
            "lineage_sha256": lineage_sha,
        }
        prepared.append(
            {
                "inst_id": inst_id,
                "csv_bytes": source_bytes,
                "destination_path": destination_path,
                "lineage_path": lineage_path,
                "lineage_bytes": lineage_bytes,
                "registry_entry": registry_entry,
                "identity": identity,
            }
        )
    return prepared


def _validate_promoted_csvs(prepared: list[dict[str, Any]]) -> None:
    for item in prepared:
        identity = item["identity"]
        path = item["destination_path"]
        if _sha256(path) != identity["destination_raw_sha256"]:
            raise MarketDataError(f"promotion_destination_raw_hash_mismatch:{item['inst_id']}")
        quality = validate_ohlcv_csv(path, "4h")
        if not quality.valid:
            raise MarketDataError(f"promotion_destination_quality_invalid:{item['inst_id']}")
        if canonical_ohlcv_sha256(path) != identity["destination_canonical_sha256"]:
            raise MarketDataError(f"promotion_destination_canonical_hash_mismatch:{item['inst_id']}")


def _validate_old_registry_entries(base_registry: Any, promoted_path: Path) -> None:
    promoted = load_dataset_registry(promoted_path)
    for dataset_id in EXPECTED_BASE_DATASETS:
        if promoted.get(dataset_id).declared_metadata() != base_registry.get(dataset_id).declared_metadata():
            raise MarketDataError(f"promotion_old_registry_entry_changed:{dataset_id}")


def _validate_base_semantics(
    report: dict[str, Any], base_panel_sha: str, dataset_ids: tuple[str, ...]
) -> None:
    datasets = {
        row.get("dataset_id"): row
        for row in report.get("datasets", [])
        if isinstance(row, dict)
    }
    if any(datasets.get(dataset_id, {}).get("status") != "partial_unverified" for dataset_id in dataset_ids):
        raise MarketDataError("promotion_base_dataset_semantics_mismatch")
    panels = [
        row
        for row in report.get("panels", [])
        if isinstance(row, dict) and row.get("panel_id") == BASE_PANEL_ID
    ]
    if len(panels) != 1 or panels[0].get("panel_sha256") != base_panel_sha:
        raise MarketDataError("promotion_base_panel_semantics_mismatch")


def _promotion_semantics_assessment(
    report: dict[str, Any],
    validated: OkxUniverseValidatedIntake,
    panel_sha256: str,
) -> dict[str, Any]:
    report_datasets = {
        row["dataset_id"]: row
        for row in report["datasets"]
        if isinstance(row, dict) and isinstance(row.get("dataset_id"), str)
    }
    components = [
        {
            "dataset_id": dataset_id,
            "status": report_datasets[dataset_id]["status"],
        }
        for dataset_id in EXPECTED_BASE_DATASETS
    ]
    components.extend(
        {"dataset_id": row["dataset_id"], "status": "verified_open_time"}
        for row in validated.capture.datasets
    )
    return {
        "panel_sha256": panel_sha256,
        "generic_panel_audit_status": "unverified",
        "components": components,
        "aggregate_status": "mixed_unverified",
        "timestamp_semantics_uniform": False,
        "old_datasets_upgraded": False,
    }


def _validate_promoted_panel(report: dict[str, Any]) -> None:
    if report["timestamp_semantics"]["status"] != "unverified":
        raise MarketDataError("promotion_generic_panel_semantics_changed")
    if report["alignment_summary"] != EXPECTED_PANEL:
        raise MarketDataError("promotion_panel_alignment_mismatch")
    summaries = {row["dataset_id"]: row for row in report["datasets"]}
    if set(summaries) != set(EXPECTED_BOUNDARY_DROPS):
        raise MarketDataError("promotion_panel_component_membership_mismatch")
    for dataset_id, (before, after) in EXPECTED_BOUNDARY_DROPS.items():
        row = summaries[dataset_id]
        if (
            row["dropped_before_common_start"] != before
            or row["dropped_after_common_end"] != after
            or row["dropped_inside_common_window"] != 0
            or row["missing_inside_common_window"] != 0
        ):
            raise MarketDataError(f"promotion_panel_boundary_mismatch:{dataset_id}")


def _panel_component_identity(report: dict[str, Any]) -> list[dict[str, Any]]:
    fields = (
        "dataset_id",
        "raw_sha256",
        "canonical_sha256",
        "source_bar_count",
        "aligned_bar_count",
        "dropped_before_common_start",
        "dropped_after_common_end",
        "dropped_inside_common_window",
        "missing_inside_common_window",
    )
    return [{field: row[field] for field in fields} for row in report["datasets"]]


def _claims() -> dict[str, Any]:
    return {
        "universe_kind": "current_okx_live_convenience_snapshot",
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "profitability_evidence": False,
        "strategy_approval": False,
    }


def _existing_repo_file(repo_root: Path, path: str | Path, label: str) -> Path:
    candidate = Path(path)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(repo_root):
        raise ValueError(f"{label} must be inside the repository root")
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def _resolve_stable_root(repo_root: Path, declared: str) -> Path:
    if declared != STABLE_DATA_ROOT or Path(declared).is_absolute() or ".." in Path(declared).parts:
        raise ValueError("promotion stable_data_root does not match the frozen path")
    resolved = (repo_root / declared).resolve()
    if not resolved.is_relative_to(repo_root) or resolved == repo_root:
        raise ValueError("promotion stable_data_root escapes repository root")
    if "reports" in {part.lower() for part in Path(declared).parts}:
        raise ValueError("promotion stable_data_root cannot be inside reports")
    return resolved


def _resolve_output_dir(repo_root: Path, path: str | Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(repo_root):
        raise ValueError("promotion output_dir must be inside repository root")
    relative = resolved.relative_to(repo_root)
    if not relative.parts or relative.parts[0].lower() != "reports":
        raise ValueError("promotion output_dir must be inside reports")
    return resolved


def _repo_relative(repo_root: Path, path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(repo_root):
        raise MarketDataError("promotion_repo_relative_path_escape")
    return resolved.relative_to(repo_root).as_posix()


def _load_json_mapping(path: Path, error: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError(error) from exc
    if not isinstance(payload, dict):
        raise MarketDataError(error)
    return payload


def _required_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MarketDataError(f"promotion_{name}_must_be_mapping")
    return value


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise MarketDataError(f"promotion_invalid_{name}")
    return value


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise MarketDataError(f"promotion_invalid_{name}")
    return value


def _required_relative_path(value: object, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise MarketDataError(f"promotion_invalid_{name}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise MarketDataError(f"promotion_unsafe_{name}")
    return path


def _safe_sibling(marker_path: Path, filename: object) -> Path:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise MarketDataError("promotion_unsafe_sibling_artifact")
    path = (marker_path.parent / filename).resolve()
    if path.parent != marker_path.parent.resolve() or not path.is_file():
        raise MarketDataError("promotion_missing_sibling_artifact")
    return path


def _locate_promotion_repo_root(
    marker_path: Path,
    registry_relative: Path,
    registry_sha256: str,
) -> Path:
    for ancestor in marker_path.parents:
        candidate = (ancestor / registry_relative).resolve()
        if candidate.is_relative_to(ancestor) and candidate.is_file() and _sha256(candidate) == registry_sha256:
            return ancestor.resolve()
    raise MarketDataError("promotion_repository_root_not_found")


def _yaml_bytes(payload: dict[str, Any]) -> bytes:
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        line_break="\n",
    ).encode("utf-8")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit_bytes(path: Path, payload: bytes) -> None:
    expected = hashlib.sha256(payload).hexdigest()
    if path.exists():
        if not path.is_file() or _sha256(path) != expected:
            raise MarketDataError(f"content_addressed_artifact_collision:{path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
