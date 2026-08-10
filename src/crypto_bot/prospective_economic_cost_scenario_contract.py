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
from crypto_bot.market.spread_application_semantics import (
    _artifact_sha,
    _validate_source_contract,
)
from crypto_bot.prospective_economic_accounting_contract import (
    _validate_cost,
    load_accounting_config,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_economic_cost_scenarios_v1"
CONTRACT_STATUS = "verified_prospective_economic_cost_scenario_contract"
DEFAULT_CONFIG_FILENAME = "config.prospective-economic-cost-scenarios.example.yaml"
ACCOUNTING_SHA256 = "a519807c88ffee4f8e902f5c282babf0c9b8c1a97555228ac644c8d3e2de71f2"
COST_SHA256 = "5633858d1e4377c507953df65f3014c30b3d98d1ec4f36c72bf1fa4c5274dc21"
SPREAD_SEMANTICS_SHA256 = "6622da645ef1b665fcb49357faefecc483ac61c2d2b1faf89a9f1249f17e8380"
TIERS = (0, 5, 10)
FEE_BPS = 100
PARTICIPATION_RATE = 0.01
SCENARIO_FIELDS = ("scenario_id", "fee_bps", "spread_bps", "slippage_bps", "symbolic_total_friction_bps", "primary", "sensitivity_only")
APPLICATION_FIELDS = ("component", "semantics", "multiplier", "basis", "historical_observed", "policy_assumption", "benchmark_uses_same_policy")
CAPACITY_FIELDS = ("key", "value")
CONSTRAINT_FIELDS = ("key", "value")
SEMANTICS = "one_way_execution_friction_per_asset_trade_notional"


@dataclass(frozen=True)
class ProspectiveEconomicCostScenarioResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_prospective_economic_cost_scenarios(
    accounting_contract: str | Path,
    cost_evidence: str | Path,
    spread_semantics: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveEconomicCostScenarioResult:
    accounting_path = Path(accounting_contract).resolve()
    repo = _repo_root(accounting_path)
    load_scenario_config(config_path, repo)
    cost_path = Path(cost_evidence).resolve()
    spread_path = Path(spread_semantics).resolve()
    accounting_config = load_accounting_config(repo / "config.prospective-economic-accounting.example.yaml", repo)
    _pin_path(repo, accounting_path, f"reports/prospective-economic-accounting/prospective-economic-accounting.{ACCOUNTING_SHA256}.json")
    _pin_path(repo, cost_path, accounting_config["cost_evidence_report"])
    _pin_path(repo, spread_path, f"reports/spread-application-semantics/spread-application-semantics.{SPREAD_SEMANTICS_SHA256}.json")
    accounting = _validate_accounting_marker(accounting_path)
    cost = _validate_cost(cost_path, repo, accounting_config)
    spread = _validate_spread_marker(spread_path)
    _validate_inputs(accounting, cost, spread)
    scenarios = _scenario_rows()
    application = _application_rows()
    capacity = _capacity_rows()
    constraints = _constraint_rows(accounting, cost, spread)
    scenario_bytes = _csv_bytes(scenarios, SCENARIO_FIELDS)
    application_bytes = _csv_bytes(application, APPLICATION_FIELDS)
    capacity_bytes = _csv_bytes(capacity, CAPACITY_FIELDS)
    constraint_bytes = _csv_bytes(constraints, CONSTRAINT_FIELDS)
    scenario_sha = _digest(scenario_bytes)
    application_sha = _digest(application_bytes)
    capacity_sha = _digest(capacity_bytes)
    constraint_sha = _digest(constraint_bytes)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "scenario_topology": "full_cartesian_product",
        "primary_scenario_policy": "max_spread_max_slippage",
        "sources": {
            "accounting_sha256": accounting["accounting_sha256"],
            "accounting_report_sha256": _sha256(accounting_path),
            "cost_sha256": cost["cost_sha256"],
            "cost_report_sha256": _sha256(cost_path),
            "spread_semantics_sha256": spread["semantics_sha256"],
            "spread_semantics_report_sha256": _sha256(spread_path),
            "accounting_constraints_sha256": _artifact_sha(accounting_path, accounting, "constraints"),
            "accounting_cost_policy_sha256": _artifact_sha(accounting_path, accounting, "cost_policy"),
            "cost_stress_sha256": _artifact_sha(cost_path, cost, "stress"),
            "spread_tiers_sha256": _artifact_sha(spread_path, spread, "tiers"),
            "spread_constraints_sha256": _artifact_sha(spread_path, spread, "constraints"),
        },
        "scenarios": {
            "fee_bps": FEE_BPS,
            "spread_bps": list(TIERS),
            "slippage_bps": list(TIERS),
            "scenario_count": 9,
            "primary_scenario": {"fee_bps": 100, "spread_bps": 10, "slippage_bps": 10},
            "scenario_ids": [row["scenario_id"] for row in scenarios],
        },
        "application_policy": {
            "fee_application": SEMANTICS,
            "spread_application": SEMANTICS,
            "slippage_application": SEMANTICS,
            "fee_multiplier": 1,
            "spread_multiplier": 1,
            "slippage_multiplier": 1,
            "component_combination": "additive_bps",
            "intra_execution_compounding": False,
        },
        "capacity_policy": {
            "capacity_role": "hard_scale_constraint_not_cost",
            "capacity_volume_field": "canonical_base_volume",
            "max_participation_rate": PARTICIPATION_RATE,
            "capacity_price_anchor": "execution_open",
            "enforcement": "per_asset_per_execution_event_no_cross_asset_netting",
            "capacity_amount_computation_authorized": False,
            "capacity_pass_fail_authorized": False,
        },
        "claims": {
            "historical_spread_directly_observed": False,
            "historical_slippage_directly_observed": False,
            "market_impact_model_verified": False,
            "capacity_not_execution_guarantee": True,
            "historical_volume_version_pinned": True,
            "historical_point_in_time_membership": False,
            "survivorship_bias_resolved": False,
            "selection_prohibited": True,
        },
        "authorization": {
            "turnover_computation_authorized": False,
            "cost_amount_computation_authorized": False,
            "return_computation_authorized": False,
            "pnl_computation_authorized": False,
            "profitability_evidence": False,
            "readiness_changed": False,
        },
        "artifacts": {
            "scenarios_sha256": scenario_sha,
            "application_policy_sha256": application_sha,
            "capacity_policy_sha256": capacity_sha,
            "constraints_sha256": constraint_sha,
        },
    }
    contract_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-economic-cost-scenarios.{contract_sha}"
    paths = {
        "scenarios": output / f"{stem}.scenarios.csv",
        "application_policy": output / f"{stem}.application-policy.csv",
        "capacity_policy": output / f"{stem}.capacity-policy.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    for key, content in (("scenarios", scenario_bytes), ("application_policy", application_bytes), ("capacity_policy", capacity_bytes), ("constraints", constraint_bytes)):
        _commit_bytes(paths[key], content)
    report = {
        "schema_version": SCHEMA_VERSION,
        "scenario_contract_sha256": contract_sha,
        "contract_status": CONTRACT_STATUS,
        "identity": identity,
        "scenario_count": 9,
        "primary_scenario_count": 1,
        "primary_fee_bps": FEE_BPS,
        "primary_spread_bps": 10,
        "primary_slippage_bps": 10,
        "cost_components": 3,
        "capacity_role": "hard_scale_constraint_not_cost",
        "family_size": 36,
        "closed_interval_count": 160,
        "terminal_unscored_count": 1,
        "turnover_value_rows": 0,
        "cost_amount_rows": 0,
        "capacity_pass_fail_rows": 0,
        "return_rows": 0,
        "pnl_rows": 0,
        "turnover_computation_authorized": False,
        "cost_amount_computation_authorized": False,
        "capacity_amount_computation_authorized": False,
        "capacity_pass_fail_authorized": False,
        "return_computation_authorized": False,
        "pnl_computation_authorized": False,
        "profitability_evidence": False,
        "readiness_changed": False,
        "artifacts": {
            "scenarios": {"filename": paths["scenarios"].name, "sha256": scenario_sha, "row_count": 9},
            "application_policy": {"filename": paths["application_policy"].name, "sha256": application_sha, "row_count": 3},
            "capacity_policy": {"filename": paths["capacity_policy"].name, "sha256": capacity_sha, "row_count": len(capacity)},
            "constraints": {"filename": paths["constraints"].name, "sha256": constraint_sha, "row_count": len(constraints)},
            "report": {"filename": paths["report"].name},
        },
    }
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveEconomicCostScenarioResult(report, {key: str(value) for key, value in paths.items()})


def load_scenario_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if not config_file.is_file() or config_file.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("economic scenario config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("economic scenario config must equal the frozen config")
    if repo is not None and not config_file.is_relative_to(repo.resolve()):
        raise MarketDataError("economic scenario config path escape")
    required = {
        "schema_version": 1, "policy_id": POLICY_ID, "scenario_topology": "full_cartesian_product",
        "primary_scenario_policy": "max_spread_max_slippage", "fee_application": SEMANTICS,
        "fee_multiplier": 1, "spread_application": SEMANTICS, "spread_multiplier": 1,
        "slippage_application": SEMANTICS, "slippage_multiplier": 1, "component_combination": "additive_bps",
        "intra_execution_compounding": False, "capacity_role": "hard_scale_constraint_not_cost",
        "capacity_volume_field": "canonical_base_volume", "max_participation_rate": PARTICIPATION_RATE,
        "capacity_price_anchor": "execution_open", "scenario_selection_prohibited": True,
        "all_scenarios_mandatory": True, "turnover_computation_authorized": False,
        "cost_amount_computation_authorized": False, "return_computation_authorized": False,
        "pnl_computation_authorized": False,
    }
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in required.items()):
        raise MarketDataError("economic scenario config policy mismatch")
    if value.get("source_spread_stress_bps") not in (None, list(TIERS)):
        raise MarketDataError("economic scenario source tiers mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_prospective_economic_cost_scenarios(result: ProspectiveEconomicCostScenarioResult) -> str:
    report = result.report
    return "\n".join([
        f"contract_status: {report['contract_status']}", f"scenario_contract_sha256: {report['scenario_contract_sha256']}",
        "scenario_count: 9", "primary_scenario_count: 1", "primary_fee_bps: 100", "primary_spread_bps: 10", "primary_slippage_bps: 10",
        "cost_components: 3", "capacity_role: hard_scale_constraint_not_cost", "turnover_value_rows: 0", "cost_amount_rows: 0", "capacity_pass_fail_rows: 0", "readiness_changed: false",
    ])


def symbolic_total_friction_bps(fee_bps: int, spread_bps: int, slippage_bps: int) -> int:
    if fee_bps != FEE_BPS or spread_bps not in TIERS or slippage_bps not in TIERS:
        raise MarketDataError("economic scenario formula input invalid")
    return fee_bps + spread_bps + slippage_bps


def capacity_base_quantity(canonical_base_volume: float) -> float:
    if canonical_base_volume < 0:
        raise MarketDataError("economic scenario capacity input invalid")
    return canonical_base_volume * PARTICIPATION_RATE


def _scenario_rows() -> list[dict[str, Any]]:
    rows = []
    for spread in TIERS:
        for slippage in TIERS:
            primary = spread == 10 and slippage == 10
            rows.append({"scenario_id": f"fee100_spread{spread}_slippage{slippage}", "fee_bps": FEE_BPS, "spread_bps": spread, "slippage_bps": slippage, "symbolic_total_friction_bps": symbolic_total_friction_bps(FEE_BPS, spread, slippage), "primary": primary, "sensitivity_only": not primary})
    return rows


def _application_rows() -> list[dict[str, Any]]:
    return [{"component": component, "semantics": SEMANTICS, "multiplier": 1, "basis": "asset_trade_notional_fraction", "historical_observed": False, "policy_assumption": True, "benchmark_uses_same_policy": True} for component in ("fee", "spread", "slippage")]


def _capacity_rows() -> list[dict[str, Any]]:
    values = {
        "capacity_role": "hard_scale_constraint_not_cost", "capacity_volume_field": "canonical_base_volume",
        "max_participation_rate": PARTICIPATION_RATE, "capacity_price_anchor": "execution_open",
        "enforcement": "per_asset_per_execution_event_no_cross_asset_netting", "version_sensitive": True,
        "capacity_not_execution_guarantee": True, "capacity_amount_computation_authorized": False,
        "capacity_pass_fail_authorized": False,
    }
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True)} for key in sorted(values)]


def _constraint_rows(accounting: dict[str, Any], cost: dict[str, Any], spread: dict[str, Any]) -> list[dict[str, Any]]:
    values = {
        "accounting_sha256": accounting["accounting_sha256"], "cost_sha256": cost["cost_sha256"], "spread_semantics_sha256": spread["semantics_sha256"],
        "scenario_topology": "full_cartesian_product", "scenario_count": 9, "primary_scenario_count": 1,
        "primary_scenario": {"fee_bps": 100, "spread_bps": 10, "slippage_bps": 10}, "cost_components": 3,
        "capacity_role": "hard_scale_constraint_not_cost", "max_participation_rate": PARTICIPATION_RATE,
        "capacity_pass_fail_authorized": False, "turnover_computation_authorized": False,
        "cost_amount_computation_authorized": False, "return_computation_authorized": False,
        "pnl_computation_authorized": False, "profitability_evidence": False, "readiness_changed": False,
    }
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True)} for key in sorted(values)]


def _validate_accounting_marker(path: Path) -> dict[str, Any]:
    report = _load_json(path)
    if report.get("accounting_sha256") != ACCOUNTING_SHA256 or report.get("contract_status") != "verified_prospective_execution_to_execution_accounting_contract":
        raise MarketDataError("economic scenario accounting marker mismatch")
    if report.get("spread_application_semantics_resolved") is not False or report.get("economic_cost_application_ready") is not False or report.get("return_rows") != 0 or report.get("turnover_value_rows") != 0 or report.get("cost_amount_rows") != 0 or report.get("pnl_rows") != 0:
        raise MarketDataError("economic scenario accounting state mismatch")
    for key in ("intervals", "family_policy", "cost_policy", "benchmark", "constraints"):
        _artifact_sha(path, report, key)
    return report


def _validate_spread_marker(path: Path) -> dict[str, Any]:
    report = _load_json(path)
    identity = report.get("identity")
    if path.name != f"spread-application-semantics.{SPREAD_SEMANTICS_SHA256}.json" or report.get("semantics_sha256") != SPREAD_SEMANTICS_SHA256 or report.get("contract_status") != "verified_prospective_spread_application_semantics" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != SPREAD_SEMANTICS_SHA256:
        raise MarketDataError("economic scenario spread marker mismatch")
    if report.get("spread_application_semantics_resolved") is not True or report.get("economic_cost_application_ready") is not True or report.get("spread_application_multiplier") != 1 or report.get("half_spread_conversion") is not False or report.get("full_spread_conversion") is not False or report.get("quoted_spread_width_claim") is not False:
        raise MarketDataError("economic scenario spread state mismatch")
    for key in ("tiers", "constraints"):
        _artifact_sha(path, report, key)
    return report


def _validate_inputs(accounting: dict[str, Any], cost: dict[str, Any], spread: dict[str, Any]) -> None:
    if accounting.get("accounting_sha256") != ACCOUNTING_SHA256 or cost.get("cost_sha256") != COST_SHA256 or spread.get("semantics_sha256") != SPREAD_SEMANTICS_SHA256:
        raise MarketDataError("economic scenario source identity mismatch")
    _validate_source_contract(accounting, cost)
    cost_identity = cost.get("identity", {})
    if cost_identity.get("fee", {}).get("fee_rate_bps") != FEE_BPS or cost_identity.get("slippage", {}).get("stress_bps") != list(TIERS):
        raise MarketDataError("economic scenario cost tiers mismatch")
    capacity = cost_identity.get("capacity", {})
    if capacity.get("volume_field") != "volume" or capacity.get("max_participation_rate") != PARTICIPATION_RATE or capacity.get("capacity_not_execution_guarantee") is not True or capacity.get("historical_volume_version_pinned") is not True:
        raise MarketDataError("economic scenario capacity source mismatch")
    if spread.get("identity", {}).get("application", {}).get("semantics") != SEMANTICS:
        raise MarketDataError("economic scenario spread semantics mismatch")


def _pin_path(repo: Path, actual: Path, relative: str) -> None:
    if actual != (repo / relative).resolve():
        raise MarketDataError("economic scenario input path mismatch")


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("economic scenario repo root not found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("economic scenario output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("economic scenario invalid json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("economic scenario invalid json")
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
            raise MarketDataError(f"economic scenario content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".economic-scenario-", suffix=".tmp", delete=False) as handle:
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
        raise MarketDataError("economic scenario artifact hash failed") from exc


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
