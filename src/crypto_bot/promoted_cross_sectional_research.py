from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from crypto_bot.cross_sectional_factor_research import (
    CrossSectionalResearchResult,
    load_cross_sectional_research_config,
    run_cross_sectional_factor_research,
)
from crypto_bot.cross_sectional_ic_calibration import (
    CrossSectionalICCalibrationResult,
    run_cross_sectional_ic_calibration,
)
from crypto_bot.errors import MarketDataError
from crypto_bot.factors import DEFAULT_FACTOR_SPECS
from crypto_bot.market.dataset_promotion import (
    TARGET_PANEL_ID,
    ValidatedDatasetPromotion,
    validate_dataset_promotion_report,
)


PROMOTED_EVIDENCE_SCHEMA_VERSION = 1
_FIXED_HORIZONS = (1, 4, 16)
_STATUS = "promotion_aware_cross_sectional_rank_ic_hac_holm_evidence_only"


@dataclass(frozen=True)
class PromotedCrossSectionalEvidenceResult:
    report: dict[str, Any]
    research: CrossSectionalResearchResult
    calibration: CrossSectionalICCalibrationResult
    export_paths: dict[str, str]


def analyze_promoted_cross_sectional_evidence(
    promotion_report_path: str | Path,
    research_config_path: str | Path,
    output_dir: str | Path,
) -> PromotedCrossSectionalEvidenceResult:
    """Replay the fixed six-asset Rank IC and HAC/Holm chain from a promotion marker."""
    promotion = validate_dataset_promotion_report(promotion_report_path)
    _validate_fixed_research_config(research_config_path)
    promotion_context = _promotion_context(promotion)
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / f".pce.{uuid4().hex[:12]}.tmp"
    temporary.mkdir()
    try:
        research = run_cross_sectional_factor_research(
            promotion.registry_path,
            promotion.panels_config_path,
            TARGET_PANEL_ID,
            research_config_path,
            temporary,
            promotion_context=promotion_context,
        )
        research_report_path = Path(research.export_paths["report"])
        calibration = run_cross_sectional_ic_calibration(research_report_path, temporary)
        calibration_report_path = Path(calibration.export_paths["report"])
        chain_identity = _chain_identity(
            promotion_context,
            research,
            research_report_path,
            calibration,
            calibration_report_path,
        )
        chain_sha256 = hashlib.sha256(_canonical_json_bytes(chain_identity)).hexdigest()
        chain_name = f"promoted-cross-sectional-evidence.{chain_sha256}.json"
        chain_report = _chain_report(chain_identity, chain_sha256, chain_name)
        staged_chain = temporary / chain_name
        staged_chain_temporary = temporary / ".chain.tmp"
        staged_chain_temporary.write_text(
            json.dumps(chain_report, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        os.replace(staged_chain_temporary, staged_chain)

        export_paths: dict[str, str] = {}
        staged = {
            "research_report": research_report_path,
            "research_ic_time_series": Path(research.export_paths["ic_time_series"]),
            "research_valid_observations": Path(research.export_paths["valid_observations"]),
            "calibration_report": calibration_report_path,
            "calibration_hypotheses": Path(calibration.export_paths["hypotheses"]),
            "chain": staged_chain,
        }
        for name, source in staged.items():
            destination = target / source.name
            _commit_staged_file(source, destination)
            export_paths[name] = str(destination)
        return PromotedCrossSectionalEvidenceResult(
            report=chain_report,
            research=research,
            calibration=calibration,
            export_paths=export_paths,
        )
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def format_promoted_cross_sectional_evidence(
    result: PromotedCrossSectionalEvidenceResult,
) -> str:
    report = result.report
    return "\n".join(
        [
            f"evidence_status: {report['evidence_status']}",
            f"chain_sha256: {report['chain_sha256']}",
            f"promotion_sha256: {report['promotion']['promotion_sha256']}",
            f"research_sha256: {report['research']['research_sha256']}",
            f"calibration_sha256: {report['calibration']['calibration_sha256']}",
            f"candidate_timestamp_count: {report['research']['candidate_timestamp_count']}",
            f"hypothesis_count: {report['calibration']['hypothesis_count']}",
            "oos_analysis_included: false",
            "readiness_changed: false",
        ]
    )


def _validate_fixed_research_config(path: str | Path) -> None:
    config = load_cross_sectional_research_config(path)
    expected_specs = tuple(sorted(DEFAULT_FACTOR_SPECS, key=lambda spec: spec.name))
    if config.specs != expected_specs or config.horizons != _FIXED_HORIZONS:
        raise MarketDataError("promoted_cross_sectional_research_config_not_fixed")


def _promotion_context(promotion: ValidatedDatasetPromotion) -> dict[str, Any]:
    report = promotion.report
    identity = _mapping(report.get("identity"), "promotion_identity")
    promoted_registry = _mapping(identity.get("promoted_registry"), "promoted_registry")
    promoted_panel = _mapping(identity.get("promoted_panel"), "promoted_panel")
    semantics = _mapping(identity.get("timestamp_semantics"), "timestamp_semantics")
    claims = _mapping(identity.get("claims"), "promotion_claims")
    candidate_lineage = {
        str(item["dataset_id"]): {
            "lineage_sha256": item["lineage_sha256"],
            "destination_raw_sha256": item["destination_raw_sha256"],
            "destination_canonical_sha256": item["destination_canonical_sha256"],
        }
        for item in _list_of_mappings(identity.get("datasets"), "promotion_datasets")
    }
    components: list[dict[str, Any]] = []
    for item in _list_of_mappings(promoted_panel.get("components"), "panel_components"):
        component = {
            "dataset_id": item["dataset_id"],
            "raw_sha256": item["raw_sha256"],
            "canonical_sha256": item["canonical_sha256"],
        }
        if item["dataset_id"] in candidate_lineage:
            component.update(candidate_lineage[str(item["dataset_id"])])
        components.append(component)
    return {
        "promotion_sha256": report["promotion_sha256"],
        "promotion_report_filename": promotion.report_path.name,
        "promotion_report_sha256": _sha256(promotion.report_path),
        "promotion_policy_version": identity["promotion_policy_version"],
        "promotion_id": identity["promotion_id"],
        "registry": {
            "repo_relative_path": promoted_registry["repo_relative_path"],
            "sha256": promoted_registry["sha256"],
            "file_sha256": _sha256(promotion.registry_path),
            "audit_filename": promotion.registry_audit_path.name,
            "audit_sha256": _sha256(promotion.registry_audit_path),
        },
        "panel": {
            "config_repo_relative_path": promoted_panel["config_repo_relative_path"],
            "config_sha256": promoted_panel["config_sha256"],
            "config_file_sha256": _sha256(promotion.panels_config_path),
            "panel_sha256": promoted_panel["panel_sha256"],
            "audit_filename": promotion.panel_audit_path.name,
            "audit_sha256": _sha256(promotion.panel_audit_path),
        },
        "datasets": components,
        "timestamp_semantics": semantics,
        "claims": claims,
    }


def _chain_identity(
    promotion: dict[str, Any],
    research: CrossSectionalResearchResult,
    research_report_path: Path,
    calibration: CrossSectionalICCalibrationResult,
    calibration_report_path: Path,
) -> dict[str, Any]:
    research_report = research.report
    calibration_report = calibration.report
    summaries = research_report["summaries"]
    candidate_counts = {int(item["candidate_timestamp_count"]) for item in summaries}
    if candidate_counts != {9486} or len(summaries) != 18:
        raise MarketDataError("promoted_cross_sectional_research_shape_mismatch")
    if research_report.get("required_asset_count") != 6:
        raise MarketDataError("promoted_cross_sectional_required_asset_count_mismatch")
    if calibration_report.get("hypothesis_count") != 18:
        raise MarketDataError("promoted_cross_sectional_calibration_shape_mismatch")
    return {
        "schema_version": PROMOTED_EVIDENCE_SCHEMA_VERSION,
        "promotion": promotion,
        "research": {
            "research_sha256": research_report["research_sha256"],
            "report_filename": research_report_path.name,
            "report_sha256": _sha256(research_report_path),
            "ic_time_series": research_report["artifacts"]["ic_time_series"],
            "valid_observations": research_report["artifacts"]["valid_observations"],
            "candidate_timestamp_count": next(iter(candidate_counts)),
            "factor_specs": research_report["factor_specs"],
            "horizons": research_report["horizons"],
            "policies": research_report["policies"],
        },
        "calibration": {
            "calibration_sha256": calibration_report["calibration_sha256"],
            "report_filename": calibration_report_path.name,
            "report_sha256": _sha256(calibration_report_path),
            "hypotheses": calibration_report["artifacts"]["hypotheses"],
            "hypothesis_count": calibration_report["hypothesis_count"],
            "policies": calibration_report["policies"],
        },
        "policies": {
            "network_access": "forbidden_offline_only",
            "candidate_reselection": "forbidden",
            "oos_analysis": "excluded_because_ten_fold_half_sample_is_below_500",
            "randomness": "none",
            "chain_marker_commit_order": "last",
        },
    }


def _chain_report(identity: dict[str, Any], chain_sha256: str, filename: str) -> dict[str, Any]:
    return {
        "schema_version": PROMOTED_EVIDENCE_SCHEMA_VERSION,
        "chain_sha256": chain_sha256,
        "evidence_status": _STATUS,
        "promotion": identity["promotion"],
        "research": identity["research"],
        "calibration": identity["calibration"],
        "policies": identity["policies"],
        "artifacts": {"chain": {"filename": filename}},
        "oos_analysis_included": False,
        "warnings": [
            "timestamp_semantics_not_uniformly_verified",
            "current_live_convenience_sample_only",
            "historical_point_in_time_membership_not_proven",
            "survivorship_bias_not_resolved",
            "statistical_evidence_is_not_portfolio_pnl",
            "no_profitability_or_execution_claim",
            "oos_not_run_because_existing_minimum_sample_policy_would_fail",
            "no_automatic_readiness_upgrade",
        ],
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }


def _commit_staged_file(source: Path, destination: Path) -> None:
    expected = _sha256(source)
    if destination.exists():
        if _sha256(destination) != expected:
            raise MarketDataError(f"content_addressed_collision:{destination.name}")
        source.unlink()
        return
    os.replace(source, destination)


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MarketDataError(f"promoted_cross_sectional_invalid_{name}")
    return value


def _list_of_mappings(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise MarketDataError(f"promoted_cross_sectional_invalid_{name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
