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

from crypto_bot.errors import MarketDataError
from crypto_bot.prospective_economic_accounting_contract import (
    _validate_cost,
    load_accounting_config,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_spread_application_semantics_v1"
CONTRACT_STATUS = "verified_prospective_spread_application_semantics"
DEFAULT_CONFIG_FILENAME = "config.spread-application-semantics.example.yaml"
ACCOUNTING_SHA256 = "a519807c88ffee4f8e902f5c282babf0c9b8c1a97555228ac644c8d3e2de71f2"
COST_SHA256 = "5633858d1e4377c507953df65f3014c30b3d98d1ec4f36c72bf1fa4c5274dc21"
STRESS_BPS = (0, 5, 10)
TIERS_FIELDS = ("stress_bps", "semantics", "multiplier")
CONSTRAINT_FIELDS = ("key", "value")
SEMANTICS = "one_way_execution_friction_per_asset_trade_notional"


@dataclass(frozen=True)
class SpreadApplicationSemanticsResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_spread_application_semantics(
    accounting_contract: str | Path,
    cost_evidence: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> SpreadApplicationSemanticsResult:
    accounting_path = Path(accounting_contract).resolve()
    repo = _repo_root(accounting_path)
    config = load_spread_application_semantics_config(config_path, repo)
    cost_path = Path(cost_evidence).resolve()
    accounting_config = load_accounting_config(repo / "config.prospective-economic-accounting.example.yaml", repo)
    _pin_path(repo, accounting_path, f"reports/prospective-economic-accounting/prospective-economic-accounting.{ACCOUNTING_SHA256}.json")
    # The accounting report path is pinned by its marker identity, not by a user-supplied config value.
    accounting = _validate_accounting_marker(accounting_path, repo)
    _pin_path(repo, cost_path, accounting_config["cost_evidence_report"])
    cost = _validate_cost(cost_path, repo, accounting_config)
    _validate_source_contract(accounting, cost)
    if config["source_spread_stress_bps"] != list(STRESS_BPS):
        raise MarketDataError("spread_semantics_source_tiers_mismatch")

    tiers = [{"stress_bps": value, "semantics": SEMANTICS, "multiplier": 1} for value in STRESS_BPS]
    constraints = _constraint_rows(accounting, cost)
    tiers_bytes = _csv_bytes(tiers, TIERS_FIELDS)
    constraints_bytes = _csv_bytes(constraints, CONSTRAINT_FIELDS)
    tiers_sha = _digest(tiers_bytes)
    constraints_sha = _digest(constraints_bytes)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "source_component": "spread",
        "source_spread_stress_bps": list(STRESS_BPS),
        "application": {
            "semantics": SEMANTICS,
            "application_multiplier": 1,
            "half_spread_conversion": False,
            "full_spread_conversion": False,
            "basis": "asset_trade_notional_fraction",
        },
        "sources": {
            "accounting_sha256": accounting["accounting_sha256"],
            "accounting_report_sha256": _sha256(accounting_path),
            "cost_sha256": cost["cost_sha256"],
            "cost_report_sha256": _sha256(cost_path),
            "accounting_constraints_sha256": _artifact_sha(accounting_path, accounting, "constraints"),
            "accounting_cost_policy_sha256": _artifact_sha(accounting_path, accounting, "cost_policy"),
            "cost_stress_sha256": _artifact_sha(cost_path, cost, "stress"),
        },
        "claims": {
            "historical_spread_directly_observed": False,
            "quoted_spread_width_claim": False,
            "policy_assumption": True,
            "cost_uniform_across_variants": True,
            "benchmark_uses_same_semantics": True,
        },
        "authorization": {
            "return_computation_authorized": False,
            "turnover_computation_authorized": False,
            "cost_amount_computation_authorized": False,
            "pnl_computation_authorized": False,
            "profitability_evidence": False,
            "readiness_changed": False,
        },
        "artifacts": {
            "tiers_sha256": tiers_sha,
            "constraints_sha256": constraints_sha,
        },
    }
    semantics_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"spread-application-semantics.{semantics_sha}"
    paths = {
        "tiers": output / f"{stem}.tiers.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    _commit_bytes(paths["tiers"], tiers_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    report = {
        "schema_version": SCHEMA_VERSION,
        "semantics_sha256": semantics_sha,
        "contract_status": CONTRACT_STATUS,
        "identity": identity,
        "spread_application_semantics_resolved": True,
        "spread_application_multiplier": 1,
        "economic_cost_application_ready": True,
        "half_spread_conversion": False,
        "full_spread_conversion": False,
        "historical_spread_directly_observed": False,
        "quoted_spread_width_claim": False,
        "resolves_accounting_blocker": True,
        "supersedes_old_artifact": False,
        "return_computation_authorized": False,
        "turnover_computation_authorized": False,
        "cost_amount_computation_authorized": False,
        "pnl_computation_authorized": False,
        "profitability_evidence": False,
        "readiness_changed": False,
        "artifacts": {
            "tiers": {"filename": paths["tiers"].name, "sha256": tiers_sha, "row_count": 3},
            "constraints": {"filename": paths["constraints"].name, "sha256": constraints_sha, "row_count": len(constraints)},
            "report": {"filename": paths["report"].name},
        },
    }
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return SpreadApplicationSemanticsResult(report, {key: str(value) for key, value in paths.items()})


def load_spread_application_semantics_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if not config_file.is_file() or config_file.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("spread semantics config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("spread semantics config must equal the frozen config")
    if repo is not None and not config_file.is_relative_to(repo.resolve()):
        raise MarketDataError("spread_semantics_config_path_escape")
    required = {
        "schema_version": 1,
        "policy_id": POLICY_ID,
        "source_component": "spread",
        "application_semantics": SEMANTICS,
        "application_multiplier": 1,
        "half_spread_conversion": False,
        "full_spread_conversion": False,
        "historical_spread_directly_observed": False,
        "quoted_spread_width_claim": False,
        "policy_assumption": True,
        "cost_uniform_across_variants": True,
        "benchmark_uses_same_semantics": True,
        "return_computation_authorized": False,
        "turnover_computation_authorized": False,
        "cost_amount_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in required.items()):
        raise MarketDataError("spread_semantics_config_policy_mismatch")
    if isinstance(value["application_multiplier"], bool) or value["application_multiplier"] != 1:
        raise MarketDataError("spread_semantics_multiplier_mismatch")
    if value.get("source_spread_stress_bps") != list(STRESS_BPS):
        raise MarketDataError("spread_semantics_source_tiers_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_spread_application_semantics(result: SpreadApplicationSemanticsResult) -> str:
    report = result.report
    return "\n".join([
        f"contract_status: {report['contract_status']}",
        f"semantics_sha256: {report['semantics_sha256']}",
        "tier_count: 3",
        "spread_application_semantics_resolved: true",
        "spread_application_multiplier: 1",
        "economic_cost_application_ready: true",
        "return_computation_authorized: false",
        "turnover_computation_authorized: false",
        "cost_amount_computation_authorized: false",
        "pnl_computation_authorized: false",
        "readiness_changed: false",
    ])


def spread_cost_fraction(asset_trade_notional_fraction: float, spread_bps: int, multiplier: int = 1) -> float:
    if asset_trade_notional_fraction < 0 or spread_bps not in STRESS_BPS or multiplier != 1:
        raise MarketDataError("spread_semantics_formula_input_invalid")
    return asset_trade_notional_fraction * spread_bps * 1e-4 * multiplier


def _validate_accounting_marker(path: Path, repo: Path) -> dict[str, Any]:
    report = _load_json(path)
    if path.parent != (repo / "reports" / "prospective-economic-accounting").resolve() or report.get("accounting_sha256") != ACCOUNTING_SHA256 or path.name != f"prospective-economic-accounting.{ACCOUNTING_SHA256}.json" or report.get("contract_status") != "verified_prospective_execution_to_execution_accounting_contract":
        raise MarketDataError("spread_semantics_accounting_marker_mismatch")
    identity = report.get("identity")
    if not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != ACCOUNTING_SHA256:
        raise MarketDataError("spread_semantics_accounting_identity_mismatch")
    expected_false = ("return_computation_authorized", "turnover_computation_authorized", "cost_amount_computation_authorized", "pnl_computation_authorized", "profitability_evidence", "readiness_changed")
    if report.get("spread_application_semantics_resolved") is not False or report.get("economic_cost_application_ready") is not False or report.get("return_rows") != 0 or report.get("turnover_value_rows") != 0 or report.get("cost_amount_rows") != 0 or report.get("pnl_rows") != 0 or any(report.get(key) is not False for key in expected_false):
        raise MarketDataError("spread_semantics_accounting_blocker_mismatch")
    for key in ("intervals", "family_policy", "cost_policy", "benchmark", "constraints"):
        _artifact_sha(path, report, key)
    return report


def _validate_source_contract(accounting: dict[str, Any], cost: dict[str, Any]) -> None:
    if cost.get("cost_sha256") != COST_SHA256 or accounting.get("identity", {}).get("sources", {}).get("cost_evidence_sha256") != COST_SHA256:
        raise MarketDataError("spread_semantics_source_identity_mismatch")
    spread = cost.get("identity", {}).get("spread", {})
    if spread.get("policy") != "fixed_stress_assumption_no_historical_observation" or spread.get("stress_bps") != list(STRESS_BPS) or spread.get("historical_spread_directly_observed") is not False:
        raise MarketDataError("spread_semantics_cost_spread_mismatch")
    if cost.get("identity", {}).get("uniformity", {}).get("cost_uniform_across_variants") is not True:
        raise MarketDataError("spread_semantics_cost_uniformity_mismatch")


def _constraint_rows(accounting: dict[str, Any], cost: dict[str, Any]) -> list[dict[str, Any]]:
    values = {
        "accounting_sha256": accounting["accounting_sha256"],
        "cost_sha256": cost["cost_sha256"],
        "source_component": "spread",
        "source_spread_stress_bps": list(STRESS_BPS),
        "application_semantics": SEMANTICS,
        "application_multiplier": 1,
        "half_spread_conversion": False,
        "full_spread_conversion": False,
        "historical_spread_directly_observed": False,
        "quoted_spread_width_claim": False,
        "policy_assumption": True,
        "cost_uniform_across_variants": True,
        "benchmark_uses_same_semantics": True,
        "resolves_accounting_blocker": True,
        "supersedes_old_artifact": False,
        "return_computation_authorized": False,
        "turnover_computation_authorized": False,
        "cost_amount_computation_authorized": False,
        "pnl_computation_authorized": False,
        "profitability_evidence": False,
        "readiness_changed": False,
    }
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True)} for key in sorted(values)]


def _artifact_sha(marker_path: Path, marker: dict[str, Any], name: str) -> str:
    info = marker.get("artifacts", {}).get(name)
    if not isinstance(info, dict) or not isinstance(info.get("filename"), str) or not isinstance(info.get("sha256"), str):
        raise MarketDataError("spread_semantics_source_artifact_mismatch")
    artifact = marker_path.parent / info["filename"]
    if _sha256(artifact) != info["sha256"]:
        raise MarketDataError("spread_semantics_source_artifact_mismatch")
    return info["sha256"]


def _pin_path(repo: Path, actual: Path, relative: str) -> None:
    if actual != (repo / relative).resolve():
        raise MarketDataError("spread_semantics_input_path_mismatch")


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("spread_semantics_repo_root_not_found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("spread semantics output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("spread_semantics_invalid_json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("spread_semantics_invalid_json")
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
    if isinstance(value, float):
        return format(value, ".15g")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"spread_semantics_content_addressed_collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".spread-semantics-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise MarketDataError("spread_semantics_source_artifact_mismatch") from exc


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
