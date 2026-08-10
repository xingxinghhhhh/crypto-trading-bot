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

import pandas as pd
import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_registry import load_dataset_registry
from crypto_bot.market.okx_direct_six_asset_migration import (
    ALL_DATASET_IDS,
    validate_okx_direct_six_asset_1h_migration,
)


SCHEMA_VERSION = 1
AUDIT_STATUS = "verified_okx_direct_six_1h_execution_cost_evidence"
DEFAULT_CONFIG_FILENAME = "config.execution-cost-evidence.example.yaml"
FEE_FIELDS = (
    "market_type",
    "quote_ccy",
    "fee_policy_id",
    "fee_side_policy",
    "fee_rate_bps",
    "evidence_status",
    "evidence_artifact_sha256",
    "account_tier",
    "user_specific_fee_verified",
    "official_rate_claim",
)
STRESS_FIELDS = (
    "component",
    "policy",
    "value_bps",
    "evidence_status",
    "historical_observation",
    "applies_to",
)
CONSTRAINT_FIELDS = (
    "constraint_name",
    "status",
    "observed",
    "required",
    "blocks_pnl_computation",
)


@dataclass(frozen=True)
class ExecutionCostEvidenceResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_execution_cost_evidence(
    preregistration_report_path: str | Path,
    execution_mapping_report_path: str | Path,
    evidence_config_path: str | Path,
    output_dir: str | Path,
) -> ExecutionCostEvidenceResult:
    prereg_path = Path(preregistration_report_path).resolve()
    repo = _repo_root(prereg_path)
    config = load_execution_cost_evidence_config(evidence_config_path, repo)
    prereg = _validate_preregistration_pin(prereg_path, config, repo)
    mapping_path = Path(execution_mapping_report_path).resolve()
    mapping = _validate_mapping_pin(mapping_path, config, repo)
    migration_path = _locate_report(
        repo / "reports" / "okx-direct-six-asset-1h-migration",
        "okx-direct-six-migration",
        config["migration_sha256"],
        config["migration_report_sha256"],
    )
    migration = validate_okx_direct_six_asset_1h_migration(migration_path)
    _validate_migration_pin(migration, migration_path, config)
    fee_evidence_path = _repo_file(repo, config["fee_evidence_artifact"])
    fee_evidence = _validate_fee_evidence(fee_evidence_path, config)
    _validate_capacity_volume(migration, config)

    fee_rows = [
        {
            "market_type": config["market_type"],
            "quote_ccy": config["quote_ccy"],
            "fee_policy_id": config["fee_policy_id"],
            "fee_side_policy": config["fee_side_policy"],
            "fee_rate_bps": config["taker_fee_bps"],
            "evidence_status": config["fee_evidence_status"],
            "evidence_artifact_sha256": _sha256(fee_evidence_path),
            "account_tier": fee_evidence["account_tier"],
            "user_specific_fee_verified": fee_evidence["user_specific_fee_verified"],
            "official_rate_claim": fee_evidence["official_rate_claim"],
        }
    ]
    stress_rows = _stress_rows(config)
    constraints = _constraint_rows(config)
    fee_bytes = _csv_bytes(fee_rows, FEE_FIELDS)
    stress_bytes = _csv_bytes(stress_rows, STRESS_FIELDS)
    constraint_bytes = _csv_bytes(constraints, CONSTRAINT_FIELDS)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": config["policy_id"],
        "config": {"filename": Path(evidence_config_path).name, "sha256": _sha256(Path(evidence_config_path))},
        "preregistration": {
            "preregistration_sha256": config["preregistration_sha256"],
            "report_sha256": _sha256(prereg_path),
            "family_size": prereg["family_size"],
        },
        "execution_mapping": {
            "audit_sha256": config["execution_mapping_sha256"],
            "report_sha256": _sha256(mapping_path),
            "execution_price_mapping_feasible": mapping["feasibility"]["execution_price_mapping_feasible"],
        },
        "migration": {
            "migration_sha256": config["migration_sha256"],
            "report_sha256": _sha256(migration_path),
            "baseline_capture_sha256": config["baseline_capture_sha256"],
            "dataset_ids": list(ALL_DATASET_IDS),
            "capacity_volume_field": config["capacity_volume_field"],
        },
        "fee": {
            "fee_policy_id": config["fee_policy_id"],
            "fee_side_policy": config["fee_side_policy"],
            "fee_rate_bps": config["taker_fee_bps"],
            "evidence_status": config["fee_evidence_status"],
            "evidence_sha256": _sha256(fee_evidence_path),
        },
        "spread": {
            "policy": config["spread_policy"],
            "historical_spread_directly_observed": config["historical_spread_directly_observed"],
            "stress_bps": config["spread_stress_bps"],
        },
        "slippage": {
            "policy": config["slippage_policy"],
            "stress_bps": config["slippage_stress_bps"],
        },
        "capacity": {
            "policy": config["capacity_policy"],
            "volume_field": config["capacity_volume_field"],
            "max_participation_rate": config["max_participation_rate"],
            "historical_volume_version_pinned": config["historical_volume_version_pinned"],
            "capacity_evidence_version_sensitive": config["capacity_evidence_version_sensitive"],
            "capacity_not_execution_guarantee": config["capacity_not_execution_guarantee"],
        },
        "uniformity": {"cost_uniform_across_variants": config["cost_uniform_across_variants"]},
        "claims": config["claims"],
        "artifacts": {
            "fee_sha256": _digest(fee_bytes),
            "stress_sha256": _digest(stress_bytes),
            "constraints_sha256": _digest(constraint_bytes),
        },
    }
    cost_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"execution-cost-evidence.{cost_sha}"
    fee_path = output / f"{stem}.fee.csv"
    stress_path = output / f"{stem}.stress.csv"
    constraints_path = output / f"{stem}.constraints.csv"
    report_path = output / f"{stem}.json"
    _commit_bytes(fee_path, fee_bytes)
    _commit_bytes(stress_path, stress_bytes)
    _commit_bytes(constraints_path, constraint_bytes)
    report = {
        "schema_version": SCHEMA_VERSION,
        "cost_sha256": cost_sha,
        "audit_status": AUDIT_STATUS,
        "identity": identity,
        "constraints": {
            "fee_evidence_status": config["fee_evidence_status"],
            "historical_spread_directly_observed": False,
            "historical_volume_version_pinned": True,
            "capacity_evidence_version_sensitive": True,
            "capacity_not_execution_guarantee": True,
            "cost_uniform_across_variants": True,
        },
        "feasibility": {
            "execution_price_mapping_feasible": True,
            "cost_contract_frozen": True,
            "pnl_computation_authorized": False,
        },
        "warnings": [
            "fee_is_conservative_policy_assumption_not_user_specific_rate",
            "historical_spread_directly_observed_false",
            "slippage_is_uniform_policy_stress_not_observed_market_impact",
            "capacity_uses_canonical_base_volume_only",
            "raw_volccy_and_volccyquote_recapture_mutability_observed",
            "capacity_not_execution_guarantee",
            "no_returns_turnover_cost_amount_or_pnl_computation",
            "no_profitability_or_execution_claim",
            "no_automatic_readiness_upgrade",
        ],
        "profitability_evidence": False,
        "readiness_changed": False,
        "automatic_factor_approval": False,
        "artifacts": {
            "fee": {"filename": fee_path.name, "sha256": identity["artifacts"]["fee_sha256"], "row_count": len(fee_rows)},
            "stress": {"filename": stress_path.name, "sha256": identity["artifacts"]["stress_sha256"], "row_count": len(stress_rows)},
            "constraints": {"filename": constraints_path.name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)},
            "report": {"filename": report_path.name},
        },
    }
    _commit_bytes(report_path, _pretty_json_bytes(report))
    return ExecutionCostEvidenceResult(
        report,
        {"fee": str(fee_path), "stress": str(stress_path), "constraints": str(constraints_path), "report": str(report_path)},
    )


def load_execution_cost_evidence_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"execution cost evidence config not found: {config_path}")
    if config_path.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("execution cost evidence config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("execution cost evidence config YAML is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("execution cost evidence config must be a mapping")
    frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("execution cost evidence config must equal the frozen config")
    if repo is not None and not config_path.is_relative_to(repo.resolve()):
        raise MarketDataError("execution_cost_config_path_escape")
    if (
        value["market_type"] != "spot"
        or value["capacity_volume_field"] != "volume"
        or value["cost_uniform_across_variants"] is not True
        or value["historical_spread_directly_observed"] is not False
        or value["max_participation_rate"] <= 0
        or value["max_participation_rate"] > 1
        or value["spread_stress_bps"] != sorted(set(value["spread_stress_bps"]))
        or value["slippage_stress_bps"] != sorted(set(value["slippage_stress_bps"]))
    ):
        raise MarketDataError("execution_cost_policy_contract_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_execution_cost_evidence(result: ExecutionCostEvidenceResult) -> str:
    report = result.report
    return "\n".join(
        [
            f"audit_status: {report['audit_status']}",
            f"cost_sha256: {report['cost_sha256']}",
            "fee_evidence_status: conservative_policy_assumption",
            "historical_spread_directly_observed: false",
            "historical_volume_version_pinned: true",
            "capacity_evidence_version_sensitive: true",
            "capacity_not_execution_guarantee: true",
            "cost_uniform_across_variants: true",
            "execution_price_mapping_feasible: true",
            "pnl_computation_authorized: false",
            "readiness_changed: false",
        ]
    )


def _validate_preregistration_pin(path: Path, config: dict[str, Any], repo: Path) -> dict[str, Any]:
    expected_dir = (repo / "reports" / "cross-sectional-variant-preregistration").resolve()
    if path.parent != expected_dir or path.name != f"cross-sectional-variant-preregistration.{config['preregistration_sha256']}.json":
        raise MarketDataError("execution_cost_preregistration_pin_mismatch")
    report = _load_json(path)
    identity = report.get("identity")
    if not isinstance(identity, dict) or (
        report.get("preregistration_sha256") != config["preregistration_sha256"]
        or _digest(_canonical_json_bytes(identity)) != config["preregistration_sha256"]
        or report.get("family_size") != 36
        or report.get("selection_prohibited") is not True
        or report.get("feasibility", {}).get("pnl_computation_authorized") is not False
    ):
        raise MarketDataError("execution_cost_preregistration_identity_mismatch")
    return report


def _validate_mapping_pin(path: Path, config: dict[str, Any], repo: Path) -> dict[str, Any]:
    expected_dir = (repo / "reports" / "okx-direct-six-1h-execution-mapping").resolve()
    if path.parent != expected_dir or path.name != f"okx-direct-six-1h-execution-mapping.{config['execution_mapping_sha256']}.json":
        raise MarketDataError("execution_cost_mapping_pin_mismatch")
    report = _load_json(path)
    identity = report.get("identity")
    if not isinstance(identity, dict) or (
        report.get("audit_sha256") != config["execution_mapping_sha256"]
        or _digest(_canonical_json_bytes(identity)) != config["execution_mapping_sha256"]
        or report.get("feasibility", {}).get("execution_price_mapping_feasible") is not True
        or report.get("feasibility", {}).get("pnl_computation_authorized") is not False
    ):
        raise MarketDataError("execution_cost_mapping_identity_mismatch")
    return report


def _validate_migration_pin(migration: Any, path: Path, config: dict[str, Any]) -> None:
    if (
        migration.report.get("migration_sha256") != config["migration_sha256"]
        or _sha256(path) != config["migration_report_sha256"]
        or migration.report["identity"].get("capture_sha256") != config["baseline_capture_sha256"]
    ):
        raise MarketDataError("execution_cost_migration_pin_mismatch")


def _validate_fee_evidence(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    if _sha256(path) != config["fee_evidence_sha256"]:
        raise MarketDataError("execution_cost_fee_evidence_hash_mismatch")
    evidence = _load_json(path)
    if (
        evidence.get("evidence_id") != "okx_spot_fee_policy_assumption_v1"
        or evidence.get("evidence_status") != "conservative_policy_assumption"
        or evidence.get("market_type") != "spot"
        or evidence.get("fee_side") != "taker"
        or evidence.get("fee_rate_bps") != config["taker_fee_bps"]
        or evidence.get("private_api_used") is not False
        or evidence.get("user_specific_fee_verified") is not False
        or evidence.get("official_rate_claim") is not False
    ):
        raise MarketDataError("execution_cost_fee_evidence_contract_mismatch")
    return evidence


def _validate_capacity_volume(migration: Any, config: dict[str, Any]) -> None:
    registry = load_dataset_registry(migration.registry_path)
    for dataset_id in ALL_DATASET_IDS:
        entry = registry.get(dataset_id)
        header = pd.read_csv(entry.resolved_path, nrows=0).columns.tolist()
        if config["capacity_volume_field"] not in header:
            raise MarketDataError(f"execution_cost_capacity_volume_field_missing:{dataset_id}")


def _stress_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for value in config["spread_stress_bps"]:
        rows.append({
            "component": "spread",
            "policy": config["spread_policy"],
            "value_bps": value,
            "evidence_status": "policy_assumption",
            "historical_observation": False,
            "applies_to": "all_36_variants",
        })
    for value in config["slippage_stress_bps"]:
        rows.append({
            "component": "slippage",
            "policy": config["slippage_policy"],
            "value_bps": value,
            "evidence_status": "policy_assumption",
            "historical_observation": False,
            "applies_to": "all_36_variants",
        })
    return rows


def _constraint_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _constraint("fee_source", "disclosed_assumption", config["fee_evidence_status"], "conservative_policy_assumption"),
        _constraint("fee_side", "pass", config["fee_side_policy"], "taker_conservative"),
        _constraint("variant_cost_uniformity", "pass", config["cost_uniform_across_variants"], True),
        _constraint("historical_spread", "limitation", False, False),
        _constraint("slippage_stress", "pass", config["slippage_stress_bps"], config["slippage_stress_bps"]),
        _constraint("capacity_volume_field", "pass", config["capacity_volume_field"], "volume"),
        _constraint("max_participation_rate", "pass", config["max_participation_rate"], config["max_participation_rate"]),
        _constraint("historical_volume_version", "disclosed", True, True),
        _constraint("capacity_execution_guarantee", "limitation", False, False),
        _constraint("pnl_computation", "prohibited", False, False),
    ]


def _constraint(name: str, status: str, observed: Any, required: Any) -> dict[str, Any]:
    return {
        "constraint_name": name,
        "status": status,
        "observed": observed,
        "required": required,
        "blocks_pnl_computation": True,
    }


def _repo_root(path: Path) -> Path:
    for candidate in (path.parent, *path.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").is_file():
            return candidate.resolve()
    raise MarketDataError("execution_cost_repo_missing")


def _repo_file(repo: Path, relative: str) -> Path:
    candidate = (repo / relative).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError as exc:
        raise MarketDataError("execution_cost_path_escape") from exc
    if not candidate.is_file():
        raise MarketDataError("execution_cost_artifact_missing")
    return candidate


def _locate_report(directory: Path, prefix: str, expected_identity: str, expected_sha: str) -> Path:
    path = directory / f"{prefix}.{expected_identity}.json"
    if not path.is_file() or _sha256(path) != expected_sha:
        raise MarketDataError("execution_cost_migration_report_missing")
    return path


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    try:
        output.relative_to((repo / "reports").resolve())
    except ValueError as exc:
        raise ValueError("execution cost evidence output must stay inside reports") from exc
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("execution_cost_invalid_json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("execution_cost_invalid_json")
    return value


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
            raise MarketDataError(f"execution_cost_content_addressed_collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".execution-cost-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
