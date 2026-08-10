from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from crypto_bot.cross_sectional_portfolio_mechanism import (
    ValidatedCrossSectionalPortfolioMechanism,
    validate_cross_sectional_portfolio_mechanism,
)
from crypto_bot.errors import MarketDataError


SCHEMA_VERSION = 1
AUDIT_STATUS = "verified_cross_sectional_variant_family_preregistration"
DEFAULT_CONFIG_FILENAME = "config.cross-sectional-variant-preregistration.example.yaml"
VARIANT_FIELDS = (
    "family_member_id",
    "mechanism_variant_id",
    "factor_name",
    "horizon_bars",
    "rank_direction",
    "reporting_order",
    "selection_prohibited",
    "direction_selection_status",
    "future_multiple_testing_scope",
    "pnl_computation_authorized",
)
POLICY_FIELDS = ("key", "value")


@dataclass(frozen=True)
class VariantPreregistrationResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_cross_sectional_variant_preregistration(
    mechanism_report_path: str | Path,
    execution_mapping_report_path: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> VariantPreregistrationResult:
    mechanism_path = Path(mechanism_report_path).resolve()
    repo = _repo_root(mechanism_path)
    config = load_variant_preregistration_config(config_path, repo)
    mechanism = validate_cross_sectional_portfolio_mechanism(mechanism_path)
    _validate_mechanism_pin(mechanism, mechanism_path, config)
    mapping_path = Path(execution_mapping_report_path).resolve()
    if _repo_root(mapping_path) != repo:
        raise MarketDataError("variant_preregistration_mapping_repo_mismatch")
    mapping = _validate_mapping_pin(mapping_path, config, repo)
    rows = _variant_rows(mechanism, config)
    policy_rows = [
        {"key": key, "value": json.dumps(value, ensure_ascii=False, sort_keys=True)}
        for key, value in _flatten_policy(config).items()
    ]
    variants_bytes = _csv_bytes(rows, VARIANT_FIELDS)
    policy_bytes = _csv_bytes(policy_rows, POLICY_FIELDS)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": config["protocol_id"],
        "config": {"filename": Path(config_path).name, "sha256": _sha256(Path(config_path))},
        "mechanism": {
            "mechanism_sha256": config["mechanism_sha256"],
            "report_sha256": _sha256(mechanism_path),
            "variants_sha256": mechanism.report["artifacts"]["variants"]["sha256"],
            "variant_count": mechanism.report["variant_count"],
        },
        "execution_mapping": {
            "audit_sha256": config["execution_mapping_sha256"],
            "report_sha256": _sha256(mapping_path),
            "execution_price_mapping_feasible": mapping["feasibility"]["execution_price_mapping_feasible"],
            "pnl_prerequisite_timestamp_mapping_satisfied": mapping["feasibility"][
                "pnl_prerequisite_timestamp_mapping_satisfied"
            ],
        },
        "family": {
            "scope": config["family_scope"],
            "size": len(rows),
            "factor_names": config["factor_names"],
            "horizons": config["horizons"],
            "rank_directions": config["rank_directions"],
            "reporting_order": config["primary_reporting_order"],
        },
        "policies": {
            "direction_policy": config["direction_policy"],
            "future_multiple_testing_method": config["future_multiple_testing_method"],
            "missing_variant_policy": config["missing_variant_policy"],
            "result_driven_reconfiguration_prohibited": config[
                "result_driven_reconfiguration_prohibited"
            ],
            "prior_results_disclosure": config["prior_results_disclosure"],
            "global_preregistration": config["global_preregistration"],
        },
        "claims": config["claims"],
        "artifacts": {
            "variants_sha256": _digest(variants_bytes),
            "policy_sha256": _digest(policy_bytes),
        },
    }
    preregistration_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"cross-sectional-variant-preregistration.{preregistration_sha}"
    variants_path = output / f"{stem}.variants.csv"
    policy_path = output / f"{stem}.policy.csv"
    report_path = output / f"{stem}.json"
    _commit_bytes(variants_path, variants_bytes)
    _commit_bytes(policy_path, policy_bytes)
    report = {
        "schema_version": SCHEMA_VERSION,
        "preregistration_sha256": preregistration_sha,
        "audit_status": AUDIT_STATUS,
        "identity": identity,
        "family_size": len(rows),
        "selection_prohibited": True,
        "feasibility": {
            "execution_price_mapping_feasible": True,
            "pnl_computation_authorized": False,
        },
        "warnings": [
            "prospective_economic_evaluation_preregistration_after_prior_statistical_evidence",
            "not_global_preregistration",
            "all_36_variants_must_remain_in_family",
            "both_rank_directions_registered_without_selection",
            "holm_applies_to_future_primary_endpoint_family",
            "missing_variant_fails_entire_family",
            "no_returns_turnover_cost_or_pnl_computation",
            "no_profitability_or_execution_claim",
            "no_automatic_readiness_upgrade",
        ],
        "profitability_evidence": False,
        "readiness_changed": False,
        "automatic_factor_approval": False,
        "artifacts": {
            "variants": {
                "filename": variants_path.name,
                "sha256": identity["artifacts"]["variants_sha256"],
                "row_count": len(rows),
            },
            "policy": {
                "filename": policy_path.name,
                "sha256": identity["artifacts"]["policy_sha256"],
                "row_count": len(policy_rows),
            },
            "report": {"filename": report_path.name},
        },
    }
    _commit_bytes(report_path, _pretty_json_bytes(report))
    return VariantPreregistrationResult(
        report,
        {"variants": str(variants_path), "policy": str(policy_path), "report": str(report_path)},
    )


def load_variant_preregistration_config(
    path: str | Path, repo: Path | None = None
) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if not config_file.is_file():
        raise FileNotFoundError(f"variant preregistration config not found: {config_file}")
    if config_file.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("variant preregistration config filename is not frozen")
    try:
        value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("variant preregistration config YAML is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("variant preregistration config must be a mapping")
    frozen_path = Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("variant preregistration config must equal the frozen config")
    if repo is not None and not config_file.is_relative_to(repo.resolve()):
        raise MarketDataError("variant_preregistration_config_path_escape")
    if value["family_size"] != 36 or value["horizons"] != [4, 16, 64] or len(value["factor_names"]) != 6:
        raise MarketDataError("variant_preregistration_family_config_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_variant_preregistration(result: VariantPreregistrationResult) -> str:
    report = result.report
    return "\n".join(
        [
            f"audit_status: {report['audit_status']}",
            f"preregistration_sha256: {report['preregistration_sha256']}",
            f"family_size: {report['family_size']}",
            "selection_prohibited: true",
            "future_multiple_testing_method: holm",
            "execution_price_mapping_feasible: true",
            "pnl_computation_authorized: false",
            "readiness_changed: false",
        ]
    )


def _validate_mechanism_pin(
    mechanism: ValidatedCrossSectionalPortfolioMechanism,
    path: Path,
    config: dict[str, Any],
) -> None:
    report = mechanism.report
    if (
        report.get("mechanism_sha256") != config["mechanism_sha256"]
        or _sha256(path) != config["mechanism_report_sha256"]
        or report.get("variant_count") != config["family_size"]
        or report.get("feasibility", {}).get("pnl_computation_authorized") is not False
        or report.get("identity", {}).get("rank_directions") != config["rank_directions"]
        or report.get("identity", {}).get("horizons") != config["horizons"]
        or report.get("identity", {}).get("factor_names") != config["factor_names"]
    ):
        raise MarketDataError("variant_preregistration_mechanism_pin_mismatch")


def _validate_mapping_pin(path: Path, config: dict[str, Any], repo: Path) -> dict[str, Any]:
    expected_dir = (repo / "reports" / "okx-direct-six-1h-execution-mapping").resolve()
    if path.parent != expected_dir or path.name != f"okx-direct-six-1h-execution-mapping.{config['execution_mapping_sha256']}.json":
        raise MarketDataError("variant_preregistration_mapping_pin_mismatch")
    report = _load_json(path)
    identity = report.get("identity")
    feasibility = report.get("feasibility")
    if not isinstance(identity, dict) or not isinstance(feasibility, dict):
        raise MarketDataError("variant_preregistration_mapping_identity_mismatch")
    if (
        report.get("audit_sha256") != config["execution_mapping_sha256"]
        or _digest(_canonical_json_bytes(identity)) != config["execution_mapping_sha256"]
        or report.get("audit_status") != "verified_okx_direct_six_1h_execution_mapping_feasibility"
        or feasibility.get("execution_price_mapping_feasible") is not True
        or feasibility.get("pnl_prerequisite_timestamp_mapping_satisfied") is not True
        or feasibility.get("pnl_computation_authorized") is not False
    ):
        raise MarketDataError("variant_preregistration_mapping_identity_mismatch")
    return report


def _variant_rows(
    mechanism: ValidatedCrossSectionalPortfolioMechanism, config: dict[str, Any]
) -> list[dict[str, Any]]:
    artifact = mechanism.artifact_paths["variants"]
    try:
        with artifact.open(encoding="utf-8", newline="") as handle:
            source_rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("variant_preregistration_invalid_variants_artifact") from exc
    if len(source_rows) != config["family_size"]:
        raise MarketDataError("variant_preregistration_family_size_mismatch")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, source in enumerate(source_rows, start=1):
        variant_id = source.get("variant_id")
        factor = source.get("factor_name")
        direction = source.get("rank_direction")
        try:
            horizon = int(str(source.get("horizon_bars")))
        except (TypeError, ValueError) as exc:
            raise MarketDataError("variant_preregistration_invalid_variant_horizon") from exc
        if (
            not variant_id
            or variant_id in seen
            or factor not in config["factor_names"]
            or horizon not in config["horizons"]
            or direction not in config["rank_directions"]
            or source.get("selection_prohibited") != "true"
        ):
            raise MarketDataError("variant_preregistration_variant_family_mismatch")
        seen.add(variant_id)
        rows.append(
            {
                "family_member_id": f"family_member_{index:02d}",
                "mechanism_variant_id": variant_id,
                "factor_name": factor,
                "horizon_bars": horizon,
                "rank_direction": direction,
                "reporting_order": index,
                "selection_prohibited": True,
                "direction_selection_status": "registered_without_selection",
                "future_multiple_testing_scope": "all_36_primary_family",
                "pnl_computation_authorized": False,
            }
        )
    expected = {
        (factor, horizon, direction)
        for factor in config["factor_names"]
        for horizon in config["horizons"]
        for direction in config["rank_directions"]
    }
    observed = {(row["factor_name"], row["horizon_bars"], row["rank_direction"]) for row in rows}
    if observed != expected:
        raise MarketDataError("variant_preregistration_variant_family_mismatch")
    return rows


def _flatten_policy(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten_policy(value[key], child))
        return result
    if isinstance(value, list):
        return {prefix: value}
    return {prefix: value}


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row[field]) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _repo_root(path: Path) -> Path:
    for candidate in (path.parent, *path.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").is_file():
            return candidate.resolve()
    raise MarketDataError("variant_preregistration_repo_missing")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    try:
        output.relative_to((repo / "reports").resolve())
    except ValueError as exc:
        raise ValueError("variant preregistration output must stay inside reports") from exc
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("variant_preregistration_invalid_json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("variant_preregistration_invalid_json")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"variant_preregistration_content_addressed_collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".variant-preregistration-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
