from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from crypto_bot.cross_sectional_factor_research import (
    CrossSectionalResearchResult,
    run_cross_sectional_factor_research,
)
from crypto_bot.cross_sectional_ic_calibration import (
    CrossSectionalICCalibrationResult,
    load_validated_cross_sectional_ic_evidence,
    run_cross_sectional_ic_calibration,
)
from crypto_bot.cross_sectional_oos_stability import (
    MINIMUM_OOS_VALID_COUNT,
    MINIMUM_TRAIN_VALID_COUNT,
    CrossSectionalOOSStabilityResult,
    run_cross_sectional_oos_stability,
)
from crypto_bot.errors import MarketDataError
from crypto_bot.factors import DEFAULT_FACTOR_SPECS
from crypto_bot.market.dataset_panel import build_dataset_panel
from crypto_bot.market.okx_universe_history_extension import (
    ValidatedFrozenUniverse1hPromotion,
    validate_okx_frozen_universe_1h_promotion,
)


PROMOTED_1H_OOS_SCHEMA_VERSION = 1
PROMOTED_1H_OOS_STATUS = "promotion_aware_six_asset_1h_rank_ic_hac_oos_evidence_only"
EXPECTED_PROMOTION_SHA256 = "36739b6fcff8ffa8150b3fdd268150cc7fc0c997d90d9fb028f3f3c096d0c24e"
EXPECTED_PROMOTION_REPORT_SHA256 = "68664799613953ee7dd279c0e9c2292178bef9306d1f1858fa2249deaa29e849"
EXPECTED_REGISTRY_SHA256 = "51ebbf3f97658fa8f36e5cd8f37742047727887957b347ba0399459b9de22975"
EXPECTED_PANEL_CONFIG_SHA256 = "9e5a03f60327ce77a417471f513c256b740bf440e2bfa950d953a937ca068ff0"
EXPECTED_PANEL_SHA256 = "b7cdebac94f728a3588cb090391fb6024cb6783ec480b1fedc830e3bd4a32054"
EXPECTED_PANEL_ID = "btc_eth_sol_knc_swftc_bico_1h_v1"
EXPECTED_DATASET_IDS = (
    "okx_bico_usdt_1h_frozen",
    "btc_usdt_1h_v1",
    "eth_usdt_1h_v1",
    "okx_knc_usdt_1h_frozen",
    "sol_usdt_1h_v1",
    "okx_swftc_usdt_1h_frozen",
)
EXPECTED_HORIZON_MAPPING = (
    {
        "source_timeframe": "4h",
        "source_horizon_bars": 1,
        "target_timeframe": "1h",
        "target_horizon_bars": 4,
    },
    {
        "source_timeframe": "4h",
        "source_horizon_bars": 4,
        "target_timeframe": "1h",
        "target_horizon_bars": 16,
    },
    {
        "source_timeframe": "4h",
        "source_horizon_bars": 16,
        "target_timeframe": "1h",
        "target_horizon_bars": 64,
    },
)
EXPECTED_HORIZONS = (4, 16, 64)
EXPECTED_POOLED_COUNTS = {4: 10_172, 16: 10_052, 64: 9_572}
EXPECTED_FOLD_COUNTS = {
    4: (1018, 1018, 1017, 1017, 1017, 1017, 1017, 1017, 1017, 1017),
    16: (1006, 1006, 1005, 1005, 1005, 1005, 1005, 1005, 1005, 1005),
    64: (958, 958, 957, 957, 957, 957, 957, 957, 957, 957),
}
OOS_POLICY_ID = "purged_expanding_half_ten_fold_v1"


@dataclass(frozen=True)
class PromotedCrossSectional1hOOSEvidenceResult:
    report: dict[str, Any]
    research: CrossSectionalResearchResult
    calibration: CrossSectionalICCalibrationResult
    oos: CrossSectionalOOSStabilityResult
    export_paths: dict[str, str]


@dataclass(frozen=True)
class ValidatedPromotedCrossSectional1hOOSEvidence:
    report_path: Path
    report: dict[str, Any]
    promotion: ValidatedFrozenUniverse1hPromotion
    research_report_path: Path
    calibration_report_path: Path
    oos_report_path: Path
    artifact_paths: dict[str, Path]


def analyze_promoted_cross_sectional_oos_evidence(
    promotion_report_path: str | Path,
    research_config_path: str | Path,
    output_dir: str | Path,
) -> PromotedCrossSectional1hOOSEvidenceResult:
    """Run the preregistered six-asset 1h Rank IC, HAC/Holm, and OOS chain."""
    promotion = validate_okx_frozen_universe_1h_promotion(promotion_report_path)
    config = _load_fixed_config(research_config_path)
    promotion_context = _promotion_context(promotion, research_config_path, config)
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / f".pc1h.{uuid4().hex[:12]}.tmp"
    temporary.mkdir()
    try:
        runtime_config = temporary / "fixed-runtime-research-config.yaml"
        _write_runtime_research_config(runtime_config, config)
        research = run_cross_sectional_factor_research(
            promotion.registry_path,
            promotion.panels_config_path,
            EXPECTED_PANEL_ID,
            runtime_config,
            temporary,
            promotion_context=promotion_context,
        )
        research_path = Path(research.export_paths["report"])
        calibration = run_cross_sectional_ic_calibration(research_path, temporary)
        calibration_path = Path(calibration.export_paths["report"])
        oos = run_cross_sectional_oos_stability(research_path, temporary)
        oos_path = Path(oos.export_paths["report"])
        identity = _chain_identity(
            promotion_context,
            research,
            research_path,
            calibration,
            calibration_path,
            oos,
            oos_path,
        )
        chain_sha256 = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
        chain_name = f"promoted-cross-sectional-1h-oos-chain.{chain_sha256}.json"
        report = _chain_report(identity, chain_sha256, chain_name)
        staged_chain = temporary / chain_name
        staged_chain.write_bytes(_pretty_json_bytes(report))

        staged = (
            ("research_ic_time_series", Path(research.export_paths["ic_time_series"])),
            ("research_valid_observations", Path(research.export_paths["valid_observations"])),
            ("research_report", research_path),
            ("calibration_hypotheses", Path(calibration.export_paths["hypotheses"])),
            ("calibration_report", calibration_path),
            ("oos_folds", Path(oos.export_paths["folds"])),
            ("oos_summaries", Path(oos.export_paths["summaries"])),
            ("oos_report", oos_path),
            ("chain", staged_chain),
        )
        exports: dict[str, str] = {}
        for name, source in staged:
            destination = target / source.name
            _commit_staged_file(source, destination)
            exports[name] = str(destination)
        return PromotedCrossSectional1hOOSEvidenceResult(
            report=report,
            research=research,
            calibration=calibration,
            oos=oos,
            export_paths=exports,
        )
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def format_promoted_cross_sectional_oos_evidence(
    result: PromotedCrossSectional1hOOSEvidenceResult,
) -> str:
    report = result.report
    return "\n".join(
        [
            f"evidence_status: {report['evidence_status']}",
            f"chain_sha256: {report['chain_sha256']}",
            f"promotion_sha256: {report['promotion']['promotion_sha256']}",
            f"research_sha256: {report['research']['research_sha256']}",
            f"calibration_sha256: {report['calibration']['calibration_sha256']}",
            f"oos_analysis_sha256: {report['oos']['analysis_sha256']}",
            f"candidate_timestamp_count: {report['research']['candidate_timestamp_count']}",
            "oos_analysis_included: true",
            "profitability_evidence: false",
            "readiness_changed: false",
        ]
    )


def validate_promoted_cross_sectional_oos_evidence(
    report_path: str | Path,
) -> ValidatedPromotedCrossSectional1hOOSEvidence:
    """Fully replay a completed six-asset 1h evidence chain without network access."""
    path = Path(report_path).resolve()
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("promoted_1h_oos_invalid_chain_report") from exc
    if not isinstance(report, dict):
        raise MarketDataError("promoted_1h_oos_invalid_chain_report")
    chain_sha256 = report.get("chain_sha256")
    if (
        not isinstance(chain_sha256, str)
        or path.name != f"promoted-cross-sectional-1h-oos-chain.{chain_sha256}.json"
        or report.get("evidence_status") != PROMOTED_1H_OOS_STATUS
        or report.get("oos_analysis_included") is not True
        or report.get("profitability_evidence") is not False
        or report.get("readiness_changed") is not False
        or report.get("automatic_factor_approval") is not False
    ):
        raise MarketDataError("promoted_1h_oos_chain_identity_mismatch")
    promotion_context = _mapping(report.get("promotion"), "chain_promotion")
    promotion_marker = _locate_repo_dependency(
        path,
        Path("reports/okx-frozen-universe-1h-promotion")
        / str(promotion_context.get("promotion_report_filename")),
        str(promotion_context.get("promotion_report_sha256")),
    )
    promotion = validate_okx_frozen_universe_1h_promotion(promotion_marker)
    design = _mapping(promotion_context.get("design"), "chain_design")
    config_path = promotion.repo_root / str(design.get("config_filename"))
    config = _load_fixed_config(config_path)
    if _promotion_context(promotion, config_path, config) != promotion_context:
        raise MarketDataError("promoted_1h_oos_chain_promotion_context_mismatch")

    research_info = _mapping(report.get("research"), "chain_research")
    calibration_info = _mapping(report.get("calibration"), "chain_calibration")
    oos_info = _mapping(report.get("oos"), "chain_oos")
    research_path = _sibling(path, research_info.get("report_filename"))
    calibration_path = _sibling(path, calibration_info.get("report_filename"))
    oos_path = _sibling(path, oos_info.get("report_filename"))
    artifact_paths = {
        "research_ic_time_series": _sibling(
            path,
            _mapping(research_info.get("ic_time_series"), "chain_research_ic").get("filename"),
        ),
        "research_valid_observations": _sibling(
            path,
            _mapping(
                research_info.get("valid_observations"), "chain_research_observations"
            ).get("filename"),
        ),
        "calibration_hypotheses": _sibling(
            path,
            _mapping(calibration_info.get("hypotheses"), "chain_hypotheses").get("filename"),
        ),
        "oos_folds": _sibling(
            path,
            _mapping(oos_info.get("folds"), "chain_oos_folds").get("filename"),
        ),
        "oos_summaries": _sibling(
            path,
            _mapping(oos_info.get("summaries"), "chain_oos_summaries").get("filename"),
        ),
    }
    _validate_chain_file_hashes(
        research_info,
        calibration_info,
        oos_info,
        research_path,
        calibration_path,
        oos_path,
        artifact_paths,
    )
    research_evidence = load_validated_cross_sectional_ic_evidence(research_path)
    research_result = CrossSectionalResearchResult(
        report=research_evidence.source_report,
        export_paths={
            "report": str(research_path),
            "ic_time_series": str(artifact_paths["research_ic_time_series"]),
            "valid_observations": str(artifact_paths["research_valid_observations"]),
        },
    )
    with tempfile.TemporaryDirectory(prefix="validate-promoted-1h-oos-") as temporary:
        replay_calibration = run_cross_sectional_ic_calibration(research_path, temporary)
        replay_oos = run_cross_sectional_oos_stability(research_path, temporary)
        calibration_report = _load_json(calibration_path, "promoted_1h_oos_invalid_calibration")
        oos_report = _load_json(oos_path, "promoted_1h_oos_invalid_oos")
        if replay_calibration.report != calibration_report:
            raise MarketDataError("promoted_1h_oos_calibration_replay_mismatch")
        if replay_oos.report != oos_report:
            raise MarketDataError("promoted_1h_oos_stability_replay_mismatch")
        identity = _chain_identity(
            promotion_context,
            research_result,
            research_path,
            replay_calibration,
            calibration_path,
            replay_oos,
            oos_path,
        )
    recomputed = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    expected_report = _chain_report(identity, recomputed, path.name)
    if recomputed != chain_sha256 or report != expected_report:
        raise MarketDataError("promoted_1h_oos_chain_replay_mismatch")
    return ValidatedPromotedCrossSectional1hOOSEvidence(
        report_path=path,
        report=report,
        promotion=promotion,
        research_report_path=research_path,
        calibration_report_path=calibration_path,
        oos_report_path=oos_path,
        artifact_paths=artifact_paths,
    )


def _load_fixed_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"promoted 1h OOS config not found: {config_path}")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"promoted 1h OOS config YAML is invalid: {config_path}") from exc
    if not isinstance(value, dict):
        raise ValueError("promoted 1h OOS config must be a mapping")
    if set(value) != {"schema_version", "factors", "horizon_mapping", "oos_policy_id"}:
        raise ValueError("promoted 1h OOS config fields are not frozen")
    if value.get("schema_version") != PROMOTED_1H_OOS_SCHEMA_VERSION:
        raise ValueError("promoted 1h OOS schema_version mismatch")
    factors = value.get("factors")
    expected_factors = [asdict(spec) for spec in DEFAULT_FACTOR_SPECS]
    if factors != expected_factors:
        raise MarketDataError("promoted_1h_oos_factor_specs_not_fixed")
    mapping = value.get("horizon_mapping")
    if mapping != list(EXPECTED_HORIZON_MAPPING):
        raise MarketDataError("promoted_1h_oos_horizon_mapping_not_fixed")
    if value.get("oos_policy_id") != OOS_POLICY_ID:
        raise MarketDataError("promoted_1h_oos_policy_not_fixed")
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _write_runtime_research_config(path: Path, config: dict[str, Any]) -> None:
    payload = {
        "schema_version": 1,
        "factors": config["factors"],
        "horizons": list(EXPECTED_HORIZONS),
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8", newline="\n")


def _promotion_context(
    promotion: ValidatedFrozenUniverse1hPromotion,
    config_path: str | Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    report = promotion.report
    identity = _mapping(report.get("identity"), "promotion_identity")
    registry = _mapping(identity.get("promoted_registry"), "promoted_registry")
    panel_info = _mapping(identity.get("promoted_panel"), "promoted_panel")
    policy = _mapping(identity.get("policy"), "promotion_policy")
    semantics = _mapping(identity.get("timestamp_semantics"), "timestamp_semantics")
    claims = _mapping(identity.get("claims"), "promotion_claims")
    if (
        report.get("promotion_sha256") != EXPECTED_PROMOTION_SHA256
        or _sha256(promotion.report_path) != EXPECTED_PROMOTION_REPORT_SHA256
        or registry.get("sha256") != EXPECTED_REGISTRY_SHA256
        or panel_info.get("config_sha256") != EXPECTED_PANEL_CONFIG_SHA256
        or panel_info.get("panel_sha256") != EXPECTED_PANEL_SHA256
        or policy.get("target_panel_id") != EXPECTED_PANEL_ID
        or policy.get("timeframe") != "1h"
        or policy.get("expected_panel", {}).get("intersection_bar_count") != 20_424
        or semantics.get("generic_panel_audit_status") != "unverified"
        or semantics.get("aggregate_status") != "mixed_unverified"
        or semantics.get("timestamp_semantics_uniform") is not False
        or claims.get("historical_point_in_time_membership") is not False
        or claims.get("survivorship_bias_resolved") is not False
        or claims.get("profitability_evidence") is not False
        or claims.get("strategy_approval") is not False
    ):
        raise MarketDataError("promoted_1h_oos_promotion_identity_mismatch")
    panel = build_dataset_panel(
        promotion.registry_path,
        promotion.panels_config_path,
        EXPECTED_PANEL_ID,
    ).report
    if (
        tuple(panel.get("dataset_ids", ())) != EXPECTED_DATASET_IDS
        or panel.get("panel_sha256") != EXPECTED_PANEL_SHA256
        or panel.get("timeframe") != "1h"
        or panel.get("alignment_summary", {}).get("intersection_bar_count") != 20_424
    ):
        raise MarketDataError("promoted_1h_oos_panel_identity_mismatch")
    lineage = {
        str(item["dataset_id"]): {
            "lineage_sha256": item["lineage_sha256"],
            "destination_repo_relative_path": item["destination_repo_relative_path"],
        }
        for item in _list_of_mappings(identity.get("datasets"), "promotion_datasets")
    }
    components = []
    for item in _list_of_mappings(panel.get("datasets"), "panel_datasets"):
        component = {
            "dataset_id": item["dataset_id"],
            "symbol": item["symbol"],
            "raw_sha256": item["raw_sha256"],
            "canonical_sha256": item["canonical_sha256"],
        }
        component.update(lineage.get(str(item["dataset_id"]), {}))
        components.append(component)
    return {
        "promotion_sha256": report["promotion_sha256"],
        "promotion_report_filename": promotion.report_path.name,
        "promotion_report_sha256": _sha256(promotion.report_path),
        "capture_sha256": identity["capture_sha256"],
        "capture_report_sha256": identity["capture_report_sha256"],
        "registry": {
            "filename": registry["filename"],
            "sha256": registry["sha256"],
            "file_sha256": _sha256(promotion.registry_path),
            "audit_filename": registry["audit_filename"],
            "audit_sha256": registry["audit_sha256"],
        },
        "panel": {
            "panel_id": EXPECTED_PANEL_ID,
            "config_filename": panel_info["config_filename"],
            "config_sha256": panel_info["config_sha256"],
            "config_file_sha256": _sha256(promotion.panels_config_path),
            "panel_sha256": panel_info["panel_sha256"],
            "audit_filename": panel_info["audit_filename"],
            "audit_sha256": panel_info["audit_sha256"],
            "alignment_summary": panel_info["alignment_summary"],
        },
        "datasets": components,
        "timestamp_semantics": semantics,
        "claims": claims,
        "design": {
            "config_filename": Path(config_path).name,
            "config_sha256": _sha256(Path(config_path)),
            "factor_parameters": "unchanged_bar_parameters",
            "factor_direction": "encoded_by_unchanged_factor_implementation",
            "horizon_mapping": config["horizon_mapping"],
            "target_horizons": list(EXPECTED_HORIZONS),
            "oos_policy_id": config["oos_policy_id"],
            "minimum_train_valid_count": MINIMUM_TRAIN_VALID_COUNT,
            "minimum_oos_valid_count": MINIMUM_OOS_VALID_COUNT,
            "prior_related_results_exist": True,
            "design_frozen_before_six_asset_1h_result_generation": True,
            "global_preregistration": False,
        },
    }


def _chain_identity(
    promotion: dict[str, Any],
    research: CrossSectionalResearchResult,
    research_path: Path,
    calibration: CrossSectionalICCalibrationResult,
    calibration_path: Path,
    oos: CrossSectionalOOSStabilityResult,
    oos_path: Path,
) -> dict[str, Any]:
    research_report = research.report
    calibration_report = calibration.report
    oos_report = oos.report
    _validate_result_shapes(research_report, calibration_report, oos_report, promotion)
    return {
        "schema_version": PROMOTED_1H_OOS_SCHEMA_VERSION,
        "promotion": promotion,
        "research": {
            "research_sha256": research_report["research_sha256"],
            "report_filename": research_path.name,
            "report_sha256": _sha256(research_path),
            "ic_time_series": research_report["artifacts"]["ic_time_series"],
            "valid_observations": research_report["artifacts"]["valid_observations"],
            "candidate_timestamp_count": 20_424,
            "factor_specs": research_report["factor_specs"],
            "horizons": research_report["horizons"],
            "policies": research_report["policies"],
        },
        "calibration": {
            "calibration_sha256": calibration_report["calibration_sha256"],
            "report_filename": calibration_path.name,
            "report_sha256": _sha256(calibration_path),
            "hypotheses": calibration_report["artifacts"]["hypotheses"],
            "hypothesis_count": calibration_report["hypothesis_count"],
            "policies": calibration_report["policies"],
        },
        "oos": {
            "analysis_sha256": oos_report["analysis_sha256"],
            "report_filename": oos_path.name,
            "report_sha256": _sha256(oos_path),
            "folds": oos_report["artifacts"]["folds"],
            "summaries": oos_report["artifacts"]["summaries"],
            "fold_row_count": oos_report["fold_row_count"],
            "summary_row_count": oos_report["summary_row_count"],
            "policies": oos_report["policies"],
        },
        "policies": {
            "network_access": "forbidden_offline_only",
            "candidate_reselection": "forbidden",
            "factor_or_direction_selection": "forbidden",
            "horizon_mapping": "fixed_before_six_asset_1h_result_generation",
            "output_directory_in_identity": False,
            "randomness": "none",
            "chain_marker_commit_order": "last",
        },
    }


def _validate_result_shapes(
    research: dict[str, Any],
    calibration: dict[str, Any],
    oos: dict[str, Any],
    promotion: dict[str, Any],
) -> None:
    summaries = research.get("summaries")
    if not isinstance(summaries, list) or len(summaries) != 18:
        raise MarketDataError("promoted_1h_oos_research_shape_mismatch")
    if (
        {item.get("candidate_timestamp_count") for item in summaries} != {20_424}
        or research.get("required_asset_count") != 6
        or research.get("horizons") != list(EXPECTED_HORIZONS)
        or research.get("artifacts", {}).get("ic_time_series", {}).get("row_count") != 367_632
        or research.get("promotion") != promotion
    ):
        raise MarketDataError("promoted_1h_oos_research_identity_mismatch")
    valid_count = sum(int(item["valid_ic_timestamp_count"]) for item in summaries)
    if research["artifacts"]["valid_observations"]["row_count"] != valid_count * 6:
        raise MarketDataError("promoted_1h_oos_observation_count_mismatch")
    if calibration.get("hypothesis_count") != 18 or calibration.get("promotion") != promotion:
        raise MarketDataError("promoted_1h_oos_calibration_shape_mismatch")
    if (
        oos.get("fold_row_count") != 180
        or oos.get("summary_row_count") != 18
        or oos.get("promotion") != promotion
    ):
        raise MarketDataError("promoted_1h_oos_stability_shape_mismatch")
    folds = oos.get("folds")
    oos_summaries = oos.get("summaries")
    if not isinstance(folds, list) or not isinstance(oos_summaries, list):
        raise MarketDataError("promoted_1h_oos_stability_rows_missing")
    factor_names = {spec["name"] for spec in research["factor_specs"]}
    for horizon in EXPECTED_HORIZONS:
        horizon_folds = [item for item in folds if item.get("horizon_bars") == horizon]
        if len(horizon_folds) != 60:
            raise MarketDataError("promoted_1h_oos_fold_horizon_shape_mismatch")
        for factor_name in factor_names:
            counts = tuple(
                int(item["oos_valid_count"])
                for item in horizon_folds
                if item.get("factor_name") == factor_name
            )
            if counts != EXPECTED_FOLD_COUNTS[horizon]:
                raise MarketDataError("promoted_1h_oos_fold_count_mismatch")
        horizon_summaries = [item for item in oos_summaries if item.get("horizon_bars") == horizon]
        if len(horizon_summaries) != 6 or {
            int(item["pooled_oos_valid_count"]) for item in horizon_summaries
        } != {EXPECTED_POOLED_COUNTS[horizon]}:
            raise MarketDataError("promoted_1h_oos_pooled_count_mismatch")


def _chain_report(identity: dict[str, Any], chain_sha256: str, filename: str) -> dict[str, Any]:
    return {
        "schema_version": PROMOTED_1H_OOS_SCHEMA_VERSION,
        "chain_sha256": chain_sha256,
        "evidence_status": PROMOTED_1H_OOS_STATUS,
        "promotion": identity["promotion"],
        "research": identity["research"],
        "calibration": identity["calibration"],
        "oos": identity["oos"],
        "policies": identity["policies"],
        "artifacts": {"chain": {"filename": filename}},
        "oos_analysis_included": True,
        "warnings": [
            "timestamp_semantics_not_uniformly_verified",
            "current_live_convenience_sample_only",
            "historical_point_in_time_membership_not_proven",
            "survivorship_bias_not_resolved",
            "prior_related_results_exist",
            "global_preregistration_not_claimed",
            "rank_ic_hac_and_oos_are_not_portfolio_pnl",
            "no_cost_adjusted_profitability_or_execution_claim",
            "no_automatic_readiness_upgrade",
        ],
        "profitability_evidence": False,
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
        raise MarketDataError(f"promoted_1h_oos_invalid_{name}")
    return value


def _list_of_mappings(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise MarketDataError(f"promoted_1h_oos_invalid_{name}")
    return value


def _validate_chain_file_hashes(
    research: dict[str, Any],
    calibration: dict[str, Any],
    oos: dict[str, Any],
    research_path: Path,
    calibration_path: Path,
    oos_path: Path,
    artifacts: dict[str, Path],
) -> None:
    expected = {
        research_path: research.get("report_sha256"),
        artifacts["research_ic_time_series"]: _mapping(
            research.get("ic_time_series"), "chain_research_ic"
        ).get("sha256"),
        artifacts["research_valid_observations"]: _mapping(
            research.get("valid_observations"), "chain_research_observations"
        ).get("sha256"),
        calibration_path: calibration.get("report_sha256"),
        artifacts["calibration_hypotheses"]: _mapping(
            calibration.get("hypotheses"), "chain_hypotheses"
        ).get("sha256"),
        oos_path: oos.get("report_sha256"),
        artifacts["oos_folds"]: _mapping(oos.get("folds"), "chain_oos_folds").get(
            "sha256"
        ),
        artifacts["oos_summaries"]: _mapping(
            oos.get("summaries"), "chain_oos_summaries"
        ).get("sha256"),
    }
    if any(not isinstance(value, str) or _sha256(path) != value for path, value in expected.items()):
        raise MarketDataError("promoted_1h_oos_chain_artifact_hash_mismatch")


def _locate_repo_dependency(report: Path, relative: Path, expected_sha256: str) -> Path:
    for parent in report.parents:
        candidate = (parent / relative).resolve()
        if candidate.is_file() and _sha256(candidate) == expected_sha256:
            return candidate
    raise MarketDataError("promoted_1h_oos_chain_dependency_not_found")


def _sibling(report: Path, filename: Any) -> Path:
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise MarketDataError("promoted_1h_oos_chain_artifact_path_escape")
    path = (report.parent / filename).resolve()
    if path.parent != report.parent or not path.is_file():
        raise MarketDataError("promoted_1h_oos_chain_artifact_missing")
    return path


def _load_json(path: Path, error: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError(error) from exc
    if not isinstance(value, dict):
        raise MarketDataError(error)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
