from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.factors import DEFAULT_FACTOR_SPECS
from crypto_bot.promoted_cross_sectional_oos_evidence import (
    ValidatedPromotedCrossSectional1hOOSEvidence,
    validate_promoted_cross_sectional_oos_evidence,
)


PORTFOLIO_MECHANISM_SCHEMA_VERSION = 1
PORTFOLIO_MECHANISM_STATUS = "portfolio_mechanism_contract_execution_timing_blocked_no_pnl"
EXPECTED_CHAIN_SHA256 = "06d1ec6bbb6bfc5222cb674227d3eaf926c2c970c1c2dcb8cc635a48f652a001"
EXPECTED_CHAIN_REPORT_SHA256 = "6bf835ce8a466c325a7c36ab1dc3d5cd96d384ecfa63600e90e607b374395f26"
EXPECTED_RESEARCH_SHA256 = "43e7b004b0302d20c1f8612f2b7cb77077fa8de9877325d0d86b6d18d6ef3eb3"
EXPECTED_CALIBRATION_SHA256 = "531dfd51af0d5738ec2370c2b315a929f2c589e3646142f19fc3d8cfb67be7cf"
EXPECTED_OOS_SHA256 = "150d6ffb685d2f31b894940751df21c4eb7b2061feb51e683e92d46e7a469e7a"
EXPECTED_PROMOTION_SHA256 = "36739b6fcff8ffa8150b3fdd268150cc7fc0c997d90d9fb028f3f3c096d0c24e"
EXPECTED_PANEL_SHA256 = "b7cdebac94f728a3588cb090391fb6024cb6783ec480b1fedc830e3bd4a32054"
EXPECTED_HORIZONS = (4, 16, 64)
RANK_DIRECTIONS = ("high_rank_selected", "low_rank_selected")
EXPECTED_CONFIG = {
    "schema_version": 1,
    "mechanism_policy_id": "long_only_spot_top2_equal_weight_v1",
    "portfolio_type": "long_only_spot",
    "gross_exposure": 1.0,
    "net_exposure": 1.0,
    "leverage": 1.0,
    "top_k": 2,
    "weighting": "equal_weight",
    "rebalance_every_bars": 1,
    "direction_policy": "evaluate_both_without_selection",
    "signal_completion_delay_bars": 1,
    "execution_delay_after_completion_bars": 1,
    "insufficient_assets_policy": "all_cash",
}
BLOCKERS = (
    "timestamp_semantics_not_uniform",
    "legacy_components_not_verified_open_time",
    "common_next_open_mapping_unverified",
)
_VARIANT_FIELDS = (
    "variant_id",
    "factor_name",
    "horizon_bars",
    "rank_direction",
    "portfolio_type",
    "top_k",
    "selected_weight",
    "unselected_weight",
    "gross_exposure",
    "net_exposure",
    "leverage",
    "weighting",
    "rebalance_every_bars",
    "signal_completion_delay_bars",
    "execution_delay_after_completion_bars",
    "intended_execution_offset_bars",
    "execution_price_mapping_feasible",
    "selection_prohibited",
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
class LongOnlyTargetWeights:
    status: str
    reason: str
    weights: tuple[tuple[str, float], ...]
    gross_exposure: float
    net_exposure: float
    cash_weight: float


@dataclass(frozen=True)
class CrossSectionalPortfolioMechanismResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class ValidatedCrossSectionalPortfolioMechanism:
    report_path: Path
    report: dict[str, Any]
    chain: ValidatedPromotedCrossSectional1hOOSEvidence
    artifact_paths: dict[str, Path]


def audit_cross_sectional_portfolio_mechanism(
    evidence_chain_path: str | Path,
    mechanism_config_path: str | Path,
    output_dir: str | Path,
) -> CrossSectionalPortfolioMechanismResult:
    chain = validate_promoted_cross_sectional_oos_evidence(evidence_chain_path)
    config = load_portfolio_mechanism_config(mechanism_config_path)
    chain_context = _validated_chain_context(chain)
    factor_names = tuple(item["name"] for item in chain.report["research"]["factor_specs"])
    variants = _variant_rows(factor_names, config)
    constraints = _constraint_rows(chain_context, config)
    variants_bytes = _serialize_rows(variants, _VARIANT_FIELDS)
    constraints_bytes = _serialize_rows(constraints, _CONSTRAINT_FIELDS)
    variants_sha256 = hashlib.sha256(variants_bytes).hexdigest()
    constraints_sha256 = hashlib.sha256(constraints_bytes).hexdigest()
    identity = {
        "schema_version": PORTFOLIO_MECHANISM_SCHEMA_VERSION,
        "mechanism_policy_id": config["mechanism_policy_id"],
        "source_chain": chain_context,
        "mechanism": _mechanism_identity(config),
        "factor_names": list(factor_names),
        "horizons": list(EXPECTED_HORIZONS),
        "rank_directions": list(RANK_DIRECTIONS),
        "membership": {
            "membership_mode": "static_current_snapshot_convenience_sample",
            "historical_point_in_time_membership": False,
            "survivorship_bias_resolved": False,
            "delisted_assets_recovered": False,
        },
        "feasibility": _feasibility(),
        "blockers": list(BLOCKERS),
        "artifacts": {
            "variants_sha256": variants_sha256,
            "constraints_sha256": constraints_sha256,
        },
    }
    mechanism_sha256 = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    stem = f"cross-sectional-portfolio-mechanism.{mechanism_sha256}"
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    variants_path = destination / f"{stem}.variants.csv"
    constraints_path = destination / f"{stem}.constraints.csv"
    report_path = destination / f"{stem}.json"
    report = {
        "schema_version": PORTFOLIO_MECHANISM_SCHEMA_VERSION,
        "mechanism_sha256": mechanism_sha256,
        "audit_status": PORTFOLIO_MECHANISM_STATUS,
        "identity": identity,
        "source_chain": chain_context,
        "variant_count": len(variants),
        "constraint_count": len(constraints),
        "feasibility": identity["feasibility"],
        "blockers": identity["blockers"],
        "artifacts": {
            "variants": {
                "filename": variants_path.name,
                "sha256": variants_sha256,
                "row_count": len(variants),
            },
            "constraints": {
                "filename": constraints_path.name,
                "sha256": constraints_sha256,
                "row_count": len(constraints),
            },
            "report": {"filename": report_path.name},
        },
        "warnings": [
            "mechanism_contract_only_no_returns_or_pnl",
            "rank_directions_registered_without_selection",
            "timestamp_semantics_not_uniform",
            "current_live_convenience_sample_only",
            "historical_point_in_time_membership_not_proven",
            "survivorship_bias_not_resolved",
            "prior_related_results_exist",
            "no_profitability_or_execution_claim",
            "no_automatic_readiness_upgrade",
        ],
        "profitability_evidence": False,
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    _commit_bytes(variants_path, variants_bytes)
    _commit_bytes(constraints_path, constraints_bytes)
    _commit_bytes(report_path, _pretty_json_bytes(report))
    return CrossSectionalPortfolioMechanismResult(
        report=report,
        export_paths={
            "report": str(report_path),
            "variants": str(variants_path),
            "constraints": str(constraints_path),
        },
    )


def load_portfolio_mechanism_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"portfolio mechanism config not found: {config_path}")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"portfolio mechanism config YAML is invalid: {config_path}") from exc
    if not isinstance(value, dict):
        raise ValueError("portfolio mechanism config must be a mapping")
    if value != EXPECTED_CONFIG:
        raise MarketDataError("cross_sectional_portfolio_mechanism_config_not_frozen")
    return json.loads(json.dumps(value, sort_keys=True))


def validate_cross_sectional_portfolio_mechanism(
    report_path: str | Path,
) -> ValidatedCrossSectionalPortfolioMechanism:
    """Replay and validate a content-addressed mechanism marker offline."""
    path = Path(report_path).resolve()
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("cross_sectional_portfolio_mechanism_invalid_report") from exc
    if not isinstance(report, dict):
        raise MarketDataError("cross_sectional_portfolio_mechanism_invalid_report")
    mechanism_sha256 = report.get("mechanism_sha256")
    if (
        not isinstance(mechanism_sha256, str)
        or path.name != f"cross-sectional-portfolio-mechanism.{mechanism_sha256}.json"
        or report.get("audit_status") != PORTFOLIO_MECHANISM_STATUS
        or report.get("profitability_evidence") is not False
        or report.get("readiness_changed") is not False
        or report.get("automatic_factor_approval") is not False
    ):
        raise MarketDataError("cross_sectional_portfolio_mechanism_identity_mismatch")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MarketDataError("cross_sectional_portfolio_mechanism_invalid_artifacts")
    artifact_paths: dict[str, Path] = {}
    for name in ("variants", "constraints"):
        info = artifacts.get(name)
        if not isinstance(info, dict):
            raise MarketDataError("cross_sectional_portfolio_mechanism_invalid_artifacts")
        artifact = _sibling(path, info.get("filename"))
        if _sha256(artifact) != info.get("sha256"):
            raise MarketDataError("cross_sectional_portfolio_mechanism_artifact_hash_mismatch")
        artifact_paths[name] = artifact
    source_chain = report.get("source_chain")
    if not isinstance(source_chain, dict):
        raise MarketDataError("cross_sectional_portfolio_mechanism_invalid_source_chain")
    chain_path = _locate_dependency(
        path,
        Path("reports/promoted-cross-sectional-1h-oos-evidence")
        / str(source_chain.get("report_filename")),
        str(source_chain.get("report_sha256")),
    )
    chain = validate_promoted_cross_sectional_oos_evidence(chain_path)
    config_path = chain.promotion.repo_root / "config.cross-sectional-portfolio-mechanism.example.yaml"
    with tempfile.TemporaryDirectory(prefix="validate-portfolio-mechanism-") as temporary:
        replay = audit_cross_sectional_portfolio_mechanism(chain_path, config_path, temporary)
        if replay.report != report:
            raise MarketDataError("cross_sectional_portfolio_mechanism_replay_mismatch")
        for name in ("variants", "constraints"):
            if Path(replay.export_paths[name]).read_bytes() != artifact_paths[name].read_bytes():
                raise MarketDataError("cross_sectional_portfolio_mechanism_replay_mismatch")
    return ValidatedCrossSectionalPortfolioMechanism(path, report, chain, artifact_paths)


def build_long_only_target_weights(
    signals: Mapping[str, float],
    required_symbols: tuple[str, ...],
    *,
    rank_direction: str,
) -> LongOnlyTargetWeights:
    if rank_direction not in RANK_DIRECTIONS:
        raise ValueError("unsupported rank direction")
    symbols = tuple(sorted(required_symbols))
    if len(symbols) != 6 or len(set(symbols)) != 6:
        raise ValueError("required_symbols must contain exactly six unique symbols")
    all_cash = tuple((symbol, 0.0) for symbol in symbols)
    if set(signals) != set(symbols):
        return LongOnlyTargetWeights("all_cash", "insufficient_assets", all_cash, 0.0, 0.0, 1.0)
    values = {symbol: float(signals[symbol]) for symbol in symbols}
    if any(not math.isfinite(value) for value in values.values()):
        return LongOnlyTargetWeights("all_cash", "non_finite_signal", all_cash, 0.0, 0.0, 1.0)
    reverse = rank_direction == "high_rank_selected"
    ranked = sorted(symbols, key=lambda symbol: (values[symbol], symbol), reverse=reverse)
    if values[ranked[1]] == values[ranked[2]]:
        return LongOnlyTargetWeights("all_cash", "ambiguous_top_k_tie", all_cash, 0.0, 0.0, 1.0)
    selected = set(ranked[:2])
    weights = tuple((symbol, 0.5 if symbol in selected else 0.0) for symbol in symbols)
    return LongOnlyTargetWeights("invested", "complete_unique_top_k", weights, 1.0, 1.0, 0.0)


def format_cross_sectional_portfolio_mechanism(
    result: CrossSectionalPortfolioMechanismResult,
) -> str:
    report = result.report
    feasibility = report["feasibility"]
    return "\n".join(
        [
            f"audit_status: {report['audit_status']}",
            f"mechanism_sha256: {report['mechanism_sha256']}",
            f"variant_count: {report['variant_count']}",
            f"signal_ranking_mechanism_feasible: {str(feasibility['signal_ranking_mechanism_feasible']).lower()}",
            f"portfolio_weight_mechanism_feasible: {str(feasibility['portfolio_weight_mechanism_feasible']).lower()}",
            f"execution_price_mapping_feasible: {str(feasibility['execution_price_mapping_feasible']).lower()}",
            f"pnl_computation_authorized: {str(feasibility['pnl_computation_authorized']).lower()}",
            "readiness_changed: false",
        ]
    )


def _validated_chain_context(
    validated: ValidatedPromotedCrossSectional1hOOSEvidence,
) -> dict[str, Any]:
    report = validated.report
    promotion = report["promotion"]
    research = report["research"]
    calibration = report["calibration"]
    oos = report["oos"]
    factor_names = tuple(item["name"] for item in research["factor_specs"])
    expected_factors = tuple(sorted(spec.name for spec in DEFAULT_FACTOR_SPECS))
    datasets = promotion["datasets"]
    if (
        report.get("chain_sha256") != EXPECTED_CHAIN_SHA256
        or _sha256(validated.report_path) != EXPECTED_CHAIN_REPORT_SHA256
        or research.get("research_sha256") != EXPECTED_RESEARCH_SHA256
        or calibration.get("calibration_sha256") != EXPECTED_CALIBRATION_SHA256
        or oos.get("analysis_sha256") != EXPECTED_OOS_SHA256
        or promotion.get("promotion_sha256") != EXPECTED_PROMOTION_SHA256
        or promotion.get("panel", {}).get("panel_sha256") != EXPECTED_PANEL_SHA256
        or factor_names != expected_factors
        or tuple(research.get("horizons", ())) != EXPECTED_HORIZONS
        or len(datasets) != 6
        or oos.get("fold_row_count") != 180
        or oos.get("summary_row_count") != 18
        or oos.get("policies", {}).get("minimum_oos_valid_count") != 500
        or promotion.get("design", {}).get("prior_related_results_exist") is not True
        or promotion.get("design", {}).get("design_frozen_before_six_asset_1h_result_generation")
        is not True
    ):
        raise MarketDataError("cross_sectional_portfolio_mechanism_source_chain_mismatch")
    return {
        "chain_sha256": report["chain_sha256"],
        "report_filename": validated.report_path.name,
        "report_sha256": _sha256(validated.report_path),
        "research_sha256": research["research_sha256"],
        "research_report_sha256": research["report_sha256"],
        "calibration_sha256": calibration["calibration_sha256"],
        "calibration_report_sha256": calibration["report_sha256"],
        "oos_analysis_sha256": oos["analysis_sha256"],
        "oos_report_sha256": oos["report_sha256"],
        "promotion_sha256": promotion["promotion_sha256"],
        "registry": promotion["registry"],
        "panel": promotion["panel"],
        "datasets": datasets,
        "timestamp_semantics": promotion["timestamp_semantics"],
        "claims": promotion["claims"],
        "prior_results": {
            "prior_related_results_exist": True,
            "design_frozen_before_six_asset_1h_result_generation": True,
            "global_preregistration": False,
        },
        "oos_evidence_shape": {
            "fold_row_count": 180,
            "summary_row_count": 18,
            "minimum_oos_valid_count": 500,
        },
    }


def _variant_rows(factor_names: tuple[str, ...], config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for factor_name in factor_names:
        for horizon in EXPECTED_HORIZONS:
            for direction in RANK_DIRECTIONS:
                rows.append(
                    {
                        "variant_id": f"{factor_name}.h{horizon}.{direction}",
                        "factor_name": factor_name,
                        "horizon_bars": horizon,
                        "rank_direction": direction,
                        "portfolio_type": config["portfolio_type"],
                        "top_k": config["top_k"],
                        "selected_weight": 0.5,
                        "unselected_weight": 0.0,
                        "gross_exposure": config["gross_exposure"],
                        "net_exposure": config["net_exposure"],
                        "leverage": config["leverage"],
                        "weighting": config["weighting"],
                        "rebalance_every_bars": config["rebalance_every_bars"],
                        "signal_completion_delay_bars": config["signal_completion_delay_bars"],
                        "execution_delay_after_completion_bars": config[
                            "execution_delay_after_completion_bars"
                        ],
                        "intended_execution_offset_bars": 2,
                        "execution_price_mapping_feasible": False,
                        "selection_prohibited": True,
                    }
                )
    if len(rows) != 36:
        raise MarketDataError("cross_sectional_portfolio_mechanism_variant_count_mismatch")
    return rows


def _constraint_rows(chain: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    semantics = chain["timestamp_semantics"]
    component_statuses = sorted({item["status"] for item in semantics["components"]})
    return [
        _constraint("asset_count", "pass", 6, 6, False, False),
        _constraint("factor_count", "pass", 6, 6, False, False),
        _constraint("horizon_family", "pass", "4|16|64", "4|16|64", False, False),
        _constraint("rank_directions", "pass", "high|low", "high|low", False, False),
        _constraint("portfolio_type", "pass", config["portfolio_type"], "long_only_spot", False, False),
        _constraint("shorting", "unsupported", False, False, False, False),
        _constraint("short_borrow", "unsupported", False, False, False, False),
        _constraint("leverage", "pass", config["leverage"], 1.0, False, False),
        _constraint("signal_completion_offset_bars", "pass", 1, 1, False, False),
        _constraint("intended_execution_offset_bars", "defined_not_verified", 2, 2, True, True),
        _constraint(
            "timestamp_semantics_uniform",
            "blocker",
            semantics["timestamp_semantics_uniform"],
            True,
            True,
            True,
        ),
        _constraint(
            "component_timestamp_semantics",
            "blocker",
            "|".join(component_statuses),
            "verified_open_time",
            True,
            True,
        ),
        _constraint("common_next_open_mapping", "blocker", "unverified", "verified", True, True),
        _constraint(
            "membership_mode",
            "limitation",
            "static_current_snapshot_convenience_sample",
            "historical_point_in_time",
            False,
            True,
        ),
        _constraint("survivorship_bias_resolved", "limitation", False, True, False, True),
        _constraint("prior_related_results_exist", "disclosed", True, True, False, False),
    ]


def _constraint(
    name: str,
    status: str,
    observed: Any,
    required: Any,
    blocks_execution: bool,
    blocks_pnl: bool,
) -> dict[str, Any]:
    return {
        "constraint_name": name,
        "status": status,
        "observed": observed,
        "required": required,
        "blocks_execution_price_mapping": blocks_execution,
        "blocks_pnl_computation": blocks_pnl,
    }


def _mechanism_identity(config: dict[str, Any]) -> dict[str, Any]:
    return config | {
        "required_asset_count": 6,
        "rank": "ascending_average_ties",
        "tie_boundary_policy": "all_cash_if_top_k_membership_is_not_unique",
        "signal_completion": "bar_t_signal_complete_at_t_plus_1h",
        "intended_execution": "common_next_open_at_t_plus_2h_requires_independent_proof",
        "missing_or_non_finite": "all_cash_no_carry_no_fill_no_substitution",
        "direction_selection": "prohibited_register_both_directions",
        "shorting": "unsupported",
        "short_borrow": "unsupported",
        "derivatives": "unsupported",
    }


def _feasibility() -> dict[str, bool]:
    return {
        "signal_ranking_mechanism_feasible": True,
        "portfolio_weight_mechanism_feasible": True,
        "execution_price_mapping_feasible": False,
        "pnl_computation_authorized": False,
    }


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


def _sibling(report: Path, filename: Any) -> Path:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise MarketDataError("cross_sectional_portfolio_mechanism_invalid_artifact_path")
    path = report.parent / filename
    if not path.is_file():
        raise MarketDataError("cross_sectional_portfolio_mechanism_artifact_missing")
    return path


def _locate_dependency(report: Path, relative: Path, expected_sha256: str) -> Path:
    for parent in report.parents:
        candidate = parent / relative
        if candidate.is_file() and _sha256(candidate) == expected_sha256:
            return candidate
    raise MarketDataError("cross_sectional_portfolio_mechanism_dependency_missing")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
