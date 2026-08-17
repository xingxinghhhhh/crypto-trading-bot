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
from typing import Any

import yaml

from crypto_bot.errors import MarketDataError


SCHEMA_VERSION = 1
POLICY_ID = "prospective_economic_evaluation_readiness_v1"
CONTRACT_STATUS = "verified_prospective_economic_evaluation_readiness_gate"
DEFAULT_CONFIG_FILENAME = "config.prospective-economic-readiness-gate.example.yaml"
LEDGER_SHA256 = "010881f9749218f129fab363ed9ebfc313182df2c4459ddafdbbed5465a2e65c"
ACCOUNTING_SHA256 = "a519807c88ffee4f8e902f5c282babf0c9b8c1a97555228ac644c8d3e2de71f2"
COST_SHA256 = "5633858d1e4377c507953df65f3014c30b3d98d1ec4f36c72bf1fa4c5274dc21"
SPREAD_SHA256 = "6622da645ef1b665fcb49357faefecc483ac61c2d2b1faf89a9f1249f17e8380"
SCENARIO_SHA256 = "5f4ec5239cac34821d3a2d0de3f1635bef027b034b50a0c3872be033ec4d3c88"
EXTENSION_SHA256 = "b0cbcb119a24ee786a81a6aed7adf4ddb8b11cf93891c19ca1fb21c4d3848146"
MEMBERSHIP_SHA256 = "737e3da3a1e2db42b87709a23bc5f754bab2ae2a38af36210b28282cfb3d9f48"
MAPPING_SHA256 = "48df5c8d1d8f2ee76e45f85920ded6ce37dc25422a14e1b54626668f8aec9ddb"
INST_IDS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT")
HORIZONS = (4, 16, 64)
DIRECTIONS = ("high_rank_selected", "low_rank_selected")
STRATEGY_FIELDS = ("family_member_id", "factor", "horizon", "direction", "interval_id", "execution_start", "execution_end", "target_available", "start_open_available", "end_open_available", "capacity_volume_available", "membership_valid", "cost_contract_valid")
BENCHMARK_FIELDS = ("benchmark_id", "interval_id", "execution_start", "execution_end", "start_open_available", "end_open_available", "capacity_volume_available", "cost_contract_valid")
DEPENDENCY_FIELDS = ("type", "identity", "marker_sha256", "artifact_hashes", "validation_status")
CONSTRAINT_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectiveEconomicReadinessGateResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def audit_prospective_economic_readiness(
    portfolio_ledger: str | Path,
    accounting_contract: str | Path,
    cost_scenarios: str | Path,
    market_data_extension: str | Path,
    membership_gate: str | Path,
    execution_mapping: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveEconomicReadinessGateResult:
    ledger_path = Path(portfolio_ledger).resolve()
    repo = _repo_root(ledger_path)
    load_readiness_config(config_path, repo)
    accounting_path = Path(accounting_contract).resolve()
    scenarios_path = Path(cost_scenarios).resolve()
    extension_path = Path(market_data_extension).resolve()
    membership_path = Path(membership_gate).resolve()
    mapping_path = Path(execution_mapping).resolve()
    _pin_inputs(repo, ledger_path, accounting_path, scenarios_path, extension_path, membership_path, mapping_path)
    ledger = _validate_ledger(ledger_path)
    accounting = _validate_accounting(accounting_path)
    scenarios = _validate_scenarios(scenarios_path)
    extension = _validate_extension(extension_path)
    membership = _validate_membership(membership_path)
    mapping = _validate_mapping(mapping_path)
    bars = _load_market_bars(repo, extension)
    intervals = _closed_intervals(accounting)
    families = _families(ledger)
    membership_by_signal = _membership_by_signal(membership)
    strategy_rows = _strategy_rows(families, intervals, bars, membership_by_signal, scenarios)
    benchmark_rows = _benchmark_rows(intervals, bars, scenarios)
    dependencies = _dependency_rows((ledger, ledger_path, "portfolio_ledger"), (accounting, accounting_path, "accounting"), (scenarios, scenarios_path, "cost_scenarios"), (extension, extension_path, "market_data_extension"), (membership, membership_path, "membership_gate"), (mapping, mapping_path, "execution_mapping"))
    constraints = _constraint_rows()
    strategy_bytes = _csv_bytes(strategy_rows, STRATEGY_FIELDS)
    benchmark_bytes = _csv_bytes(benchmark_rows, BENCHMARK_FIELDS)
    dependency_bytes = _csv_bytes(dependencies, DEPENDENCY_FIELDS)
    constraint_bytes = _csv_bytes(constraints, CONSTRAINT_FIELDS)
    strategy_sha, benchmark_sha, dependency_sha, constraint_sha = map(_digest, (strategy_bytes, benchmark_bytes, dependency_bytes, constraint_bytes))
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "sources": {row["type"]: {"identity": row["identity"], "marker_sha256": row["marker_sha256"], "artifact_hashes": json.loads(row["artifact_hashes"])} for row in dependencies},
        "counts": {"family_size": 36, "closed_interval_count": 160, "strategy_matrix_rows": 5760, "benchmark_matrix_rows": 160, "scenario_count": 9, "primary_scenario_count": 1},
        "flags": {"family_alignment_ready": True, "interval_alignment_ready": True, "execution_price_inputs_ready": True, "capacity_volume_inputs_ready": True, "cost_application_semantics_ready": True, "benchmark_alignment_ready": True, "prospective_economic_inputs_structurally_ready": True},
        "authorization": {"economic_value_computation_authorized": False, "turnover_computation_authorized": False, "cost_amount_computation_authorized": False, "capacity_pass_fail_authorized": False, "return_computation_authorized": False, "pnl_computation_authorized": False, "profitability_evidence": False, "trading_readiness_changed": False},
        "artifacts": {"strategy_matrix_sha256": strategy_sha, "benchmark_matrix_sha256": benchmark_sha, "dependencies_sha256": dependency_sha, "constraints_sha256": constraint_sha},
    }
    readiness_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-economic-readiness.{readiness_sha}"
    paths = {"strategy_matrix": output / f"{stem}.strategy-matrix.csv", "benchmark_matrix": output / f"{stem}.benchmark-matrix.csv", "dependencies": output / f"{stem}.dependencies.csv", "constraints": output / f"{stem}.constraints.csv", "report": output / f"{stem}.json"}
    for key, content in (("strategy_matrix", strategy_bytes), ("benchmark_matrix", benchmark_bytes), ("dependencies", dependency_bytes), ("constraints", constraint_bytes)):
        _commit_bytes(paths[key], content)
    report = {
        "schema_version": SCHEMA_VERSION, "readiness_sha256": readiness_sha, "contract_status": CONTRACT_STATUS, "identity": identity,
        "family_size": 36, "closed_interval_count": 160, "strategy_matrix_rows": len(strategy_rows), "benchmark_matrix_rows": len(benchmark_rows), "scenario_count": 9, "primary_scenario_count": 1,
        "missing_execution_open": 0, "missing_capacity_volume": 0, "invalid_membership_rows": 0, "duplicate_strategy_slots": 0, "terminal_scored_rows": 0,
        "family_alignment_ready": True, "interval_alignment_ready": True, "execution_price_inputs_ready": True, "capacity_volume_inputs_ready": True, "cost_application_semantics_ready": True, "benchmark_alignment_ready": True, "prospective_economic_inputs_structurally_ready": True,
        "economic_value_computation_authorized": False, "turnover_computation_authorized": False, "cost_amount_computation_authorized": False, "capacity_pass_fail_authorized": False, "return_computation_authorized": False, "pnl_computation_authorized": False, "profitability_evidence": False, "trading_readiness_changed": False,
        "artifacts": {"strategy_matrix": {"filename": paths["strategy_matrix"].name, "sha256": strategy_sha, "row_count": len(strategy_rows)}, "benchmark_matrix": {"filename": paths["benchmark_matrix"].name, "sha256": benchmark_sha, "row_count": len(benchmark_rows)}, "dependencies": {"filename": paths["dependencies"].name, "sha256": dependency_sha, "row_count": len(dependencies)}, "constraints": {"filename": paths["constraints"].name, "sha256": constraint_sha, "row_count": len(constraints)}, "report": {"filename": paths["report"].name}},
    }
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveEconomicReadinessGateResult(report, {key: str(value) for key, value in paths.items()})


def load_readiness_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if not config_file.is_file() or config_file.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("readiness config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("readiness config must equal the frozen config")
    if repo is not None and not config_file.is_relative_to(repo.resolve()):
        raise MarketDataError("readiness config path escape")
    required = {"schema_version": 1, "policy_id": POLICY_ID, "required_family_size": 36, "required_closed_interval_count": 160, "required_terminal_unscored_count": 1, "required_scenario_count": 9, "required_primary_scenario_count": 1, "require_exact_execution_open": True, "require_canonical_base_volume": True, "require_future_membership_gate": True, "require_cost_uniform_across_variants": True, "require_benchmark_same_cost_policy": True, "terminal_scoring_prohibited": True, "variant_selection_prohibited": True, "economic_value_computation_authorized": False, "turnover_computation_authorized": False, "cost_amount_computation_authorized": False, "capacity_pass_fail_authorized": False, "return_computation_authorized": False, "pnl_computation_authorized": False, "trading_readiness_changed": False}
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in required.items()):
        raise MarketDataError("readiness config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_prospective_economic_readiness(result: ProspectiveEconomicReadinessGateResult) -> str:
    report = result.report
    return "\n".join([f"contract_status: {report['contract_status']}", f"readiness_sha256: {report['readiness_sha256']}", "family_size: 36", "closed_interval_count: 160", "strategy_matrix_rows: 5760", "benchmark_matrix_rows: 160", "scenario_count: 9", "primary_scenario_count: 1", "missing_execution_open: 0", "missing_capacity_volume: 0", "terminal_scored_rows: 0", "prospective_economic_inputs_structurally_ready: true", "economic_value_computation_authorized: false", "trading_readiness_changed: false"])


def validate_prospective_economic_readiness(path: str | Path) -> dict[str, Any]:
    """Replay one immutable economic-readiness marker without writing reports."""

    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if report_path.parent != (repo / "reports" / "prospective-economic-readiness").resolve():
        raise MarketDataError("readiness marker path escape")
    report = _load_json(report_path)
    readiness_sha = report.get("readiness_sha256")
    identity = report.get("identity")
    if (
        report.get("schema_version") != SCHEMA_VERSION
        or report.get("contract_status") != CONTRACT_STATUS
        or not isinstance(readiness_sha, str)
        or report_path.name != f"prospective-economic-readiness.{readiness_sha}.json"
        or not isinstance(identity, dict)
        or _digest(_canonical_json_bytes(identity)) != readiness_sha
    ):
        raise MarketDataError("readiness identity mismatch")
    if (
        identity.get("schema_version") != SCHEMA_VERSION
        or identity.get("policy_id") != POLICY_ID
        or identity.get("counts") != {"family_size": 36, "closed_interval_count": 160, "strategy_matrix_rows": 5760, "benchmark_matrix_rows": 160, "scenario_count": 9, "primary_scenario_count": 1}
        or identity.get("flags", {}).get("prospective_economic_inputs_structurally_ready") is not True
    ):
        raise MarketDataError("readiness identity state mismatch")
    if (
        report.get("family_size") != 36
        or report.get("closed_interval_count") != 160
        or report.get("strategy_matrix_rows") != 5760
        or report.get("benchmark_matrix_rows") != 160
        or report.get("scenario_count") != 9
        or report.get("primary_scenario_count") != 1
        or report.get("prospective_economic_inputs_structurally_ready") is not True
        or any(
            report.get(key) is not False
            for key in (
                "economic_value_computation_authorized",
                "turnover_computation_authorized",
                "cost_amount_computation_authorized",
                "capacity_pass_fail_authorized",
                "return_computation_authorized",
                "pnl_computation_authorized",
                "profitability_evidence",
                "trading_readiness_changed",
            )
        )
    ):
        raise MarketDataError("readiness state mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("readiness artifacts missing")
    expected_rows = {"strategy_matrix": 5760, "benchmark_matrix": 160, "dependencies": 6, "constraints": 22}
    for name, row_count in expected_rows.items():
        info = artifacts.get(name)
        expected_sha = identity_artifacts.get(f"{name}_sha256")
        if not isinstance(info, dict) or info.get("sha256") != expected_sha:
            raise MarketDataError(f"readiness artifact metadata:{name}")
        filename = info.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise MarketDataError(f"readiness artifact filename:{name}")
        artifact_path = (report_path.parent / filename).resolve()
        if artifact_path.parent != report_path.parent or not artifact_path.is_file() or _sha256(artifact_path) != expected_sha:
            raise MarketDataError(f"readiness artifact bytes:{name}")
        rows = _read_csv(artifact_path)
        if len(rows) != row_count or info.get("row_count") != len(rows):
            raise MarketDataError(f"readiness artifact count:{name}")
    strategy = _read_csv(report_path.parent / artifacts["strategy_matrix"]["filename"])
    benchmark = _read_csv(report_path.parent / artifacts["benchmark_matrix"]["filename"])
    required_strategy = ("target_available", "start_open_available", "end_open_available", "capacity_volume_available", "membership_valid", "cost_contract_valid")
    required_benchmark = ("start_open_available", "end_open_available", "capacity_volume_available", "cost_contract_valid")
    if any(any(row.get(key) != "true" for key in required_strategy) for row in strategy) or any(any(row.get(key) != "true" for key in required_benchmark) for row in benchmark):
        raise MarketDataError("readiness matrix state mismatch")
    source_paths = (
        repo / "reports/prospective-portfolio-ledger" / f"prospective-portfolio-ledger.{LEDGER_SHA256}.json",
        repo / "reports/prospective-economic-accounting" / f"prospective-economic-accounting.{ACCOUNTING_SHA256}.json",
        repo / "reports/prospective-economic-cost-scenarios" / f"prospective-economic-cost-scenarios.{SCENARIO_SHA256}.json",
        repo / "reports/prospective-direct-1h-extension" / f"prospective-direct-1h-extension.{EXTENSION_SHA256}.json",
        repo / "reports/prospective-membership-bar-gate" / f"prospective-membership-bar-gate.{MEMBERSHIP_SHA256}.json",
        repo / "reports/okx-direct-six-1h-execution-mapping" / f"okx-direct-six-1h-execution-mapping.{MAPPING_SHA256}.json",
    )
    if any(not item.is_file() for item in source_paths):
        raise MarketDataError("readiness source parent missing")
    _pin_inputs(repo, *source_paths)
    for validator, source in zip(
        (_validate_ledger, _validate_accounting, _validate_scenarios, _validate_extension, _validate_membership, _validate_mapping),
        source_paths,
        strict=True,
    ):
        validator(source)
    return report


def _pin_inputs(repo: Path, *paths: Path) -> None:
    expected = (f"reports/prospective-portfolio-ledger/prospective-portfolio-ledger.{LEDGER_SHA256}.json", f"reports/prospective-economic-accounting/prospective-economic-accounting.{ACCOUNTING_SHA256}.json", f"reports/prospective-economic-cost-scenarios/prospective-economic-cost-scenarios.{SCENARIO_SHA256}.json", f"reports/prospective-direct-1h-extension/prospective-direct-1h-extension.{EXTENSION_SHA256}.json", f"reports/prospective-membership-bar-gate/prospective-membership-bar-gate.{MEMBERSHIP_SHA256}.json", f"reports/okx-direct-six-1h-execution-mapping/okx-direct-six-1h-execution-mapping.{MAPPING_SHA256}.json")
    if len(paths) != len(expected) or any(actual != (repo / rel).resolve() for actual, rel in zip(paths, expected, strict=True)):
        raise MarketDataError("readiness input path mismatch")


def _validate_ledger(path: Path) -> dict[str, Any]:
    report = _load_json(path)
    if report.get("ledger_sha256") != LEDGER_SHA256 or report.get("ledger_status") != "verified_prospective_closed_epoch_portfolio_ledger" or report.get("family_count") != 36 or report.get("signal_timestamp_count") != 161 or report.get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("readiness ledger mismatch")
    identity = report.get("identity")
    if not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != LEDGER_SHA256:
        raise MarketDataError("readiness ledger identity mismatch")
    for key in ("factor_scores", "decisions", "weights", "constraints"):
        _artifact(path, report, key)
    return report


def _validate_accounting(path: Path) -> dict[str, Any]:
    report = _load_json(path)
    if report.get("accounting_sha256") != ACCOUNTING_SHA256 or report.get("closed_interval_count") != 160 or report.get("terminal_unscored_count") != 1 or report.get("return_rows") != 0 or report.get("turnover_value_rows") != 0 or report.get("cost_amount_rows") != 0 or report.get("pnl_rows") != 0 or report.get("readiness_changed") is not False:
        raise MarketDataError("readiness accounting mismatch")
    if not isinstance(report.get("identity"), dict) or _digest(_canonical_json_bytes(report["identity"])) != ACCOUNTING_SHA256:
        raise MarketDataError("readiness accounting identity mismatch")
    for key in ("intervals", "family_policy", "cost_policy", "benchmark", "constraints"):
        _artifact(path, report, key)
    return report


def _validate_scenarios(path: Path) -> dict[str, Any]:
    report = _load_json(path)
    if report.get("scenario_contract_sha256") != SCENARIO_SHA256 or report.get("scenario_count") != 9 or report.get("primary_scenario_count") != 1 or report.get("primary_fee_bps") != 100 or report.get("primary_spread_bps") != 10 or report.get("primary_slippage_bps") != 10 or report.get("capacity_role") != "hard_scale_constraint_not_cost" or any(report.get(key) is not False for key in ("turnover_computation_authorized", "cost_amount_computation_authorized", "capacity_pass_fail_authorized", "return_computation_authorized", "pnl_computation_authorized", "readiness_changed")):
        raise MarketDataError("readiness scenario mismatch")
    identity = report.get("identity")
    if not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != SCENARIO_SHA256:
        raise MarketDataError("readiness scenario identity mismatch")
    for key in ("scenarios", "application_policy", "capacity_policy", "constraints"):
        _artifact(path, report, key)
    return report


def _validate_extension(path: Path) -> dict[str, Any]:
    report = _load_json(path)
    if report.get("extension_sha256") != EXTENSION_SHA256 or report.get("audit_status") != "verified_prospective_direct_okx_1h_extension" or report.get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False or report.get("prospective_market_data_version_pinned") is not True:
        raise MarketDataError("readiness extension mismatch")
    for key in ("datasets", "coverage", "constraints"):
        _artifact(path, report, key)
    return report


def _validate_membership(path: Path) -> dict[str, Any]:
    report = _load_json(path)
    if report.get("gate_sha256") != MEMBERSHIP_SHA256 or report.get("gate_status") != "verified_prospective_membership_1h_bar_gate" or report.get("future_only_membership_evidence") is not True or report.get("retroactive_membership_change") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("readiness membership mismatch")
    if report.get("identity", {}).get("epoch", {}).get("signal_count") != 161 or report.get("identity", {}).get("epoch", {}).get("row_count") != 966:
        raise MarketDataError("readiness membership grid mismatch")
    for key in ("epochs", "eligibility", "constraints"):
        _artifact(path, report, key)
    return report


def _validate_mapping(path: Path) -> dict[str, Any]:
    report = _load_json(path)
    if report.get("audit_sha256") != MAPPING_SHA256 or report.get("audit_status") != "verified_okx_direct_six_1h_execution_mapping_feasibility" or report.get("feasibility", {}).get("execution_price_mapping_feasible") is not True or report.get("feasibility", {}).get("pnl_computation_authorized") is not False:
        raise MarketDataError("readiness mapping mismatch")
    _artifact(path, report, "mappings")
    return report


def _closed_intervals(accounting: dict[str, Any]) -> list[dict[str, str]]:
    path = Path(__file__).resolve().parents[2] / "reports/prospective-economic-accounting" / accounting["artifacts"]["intervals"]["filename"]
    rows = _read_csv(path)
    closed = [row for row in rows if row.get("economic_interval_closed") == "true"]
    if len(rows) != 161 or len(closed) != 160 or any(row.get("terminal_unscored") == "true" for row in closed):
        raise MarketDataError("readiness interval grid mismatch")
    if closed[0]["execution_timestamp"] != "2026-08-02T18:00:00Z" or closed[-1]["next_execution_timestamp"] != "2026-08-09T10:00:00Z":
        raise MarketDataError("readiness execution grid endpoints mismatch")
    return closed


def _families(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    factors = ledger.get("identity", {}).get("family", {}).get("factors")
    if not isinstance(factors, list) or len(factors) != 6:
        raise MarketDataError("readiness family mismatch")
    rows = [{"family_member_id": f"{factor}.h{horizon}.{direction}", "factor": factor, "horizon": horizon, "direction": direction} for factor in factors for horizon in HORIZONS for direction in DIRECTIONS]
    if len(rows) != 36:
        raise MarketDataError("readiness family count mismatch")
    return rows


def _membership_by_signal(report: dict[str, Any]) -> dict[str, bool]:
    path = Path(__file__).resolve().parents[2] / "reports/prospective-membership-bar-gate" / report["artifacts"]["eligibility"]["filename"]
    rows = _read_csv(path)
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["signal_timestamp"], []).append(row)
    result = {signal: len(items) == 6 and all(item.get("eligible_for_closed_epoch") == "true" and item.get("retroactive_change_applied") == "false" for item in items) for signal, items in grouped.items()}
    if len(result) != 161 or not all(result.values()):
        raise MarketDataError("readiness membership eligibility mismatch")
    return result


def _load_market_bars(repo: Path, extension: dict[str, Any]) -> dict[str, dict[str, bool]]:
    capture_sha = extension.get("identity", {}).get("capture_sha256")
    capture_path = repo / "reports/prospective-direct-1h-capture" / f"prospective-direct-1h-capture.{capture_sha}.json"
    capture = _load_json(capture_path)
    migration = _load_json(repo / "reports/okx-direct-six-asset-1h-migration/okx-direct-six-migration.67fb338366f01f6ac03e0280f374fb8c6c5578029d7756286d5315ac1cc3b698.json")
    dataset_paths = {item["inst_id"]: repo / item["destination_repo_relative_path"] for item in migration["identity"]["datasets"]}
    append_paths = {item["inst_id"]: capture_path.parent / item["append_filename"] for item in capture["identity"]["assets"]}
    result: dict[str, dict[str, bool]] = {}
    for inst_id in INST_IDS:
        rows = _read_csv(dataset_paths[inst_id]) + _read_csv(append_paths[inst_id])
        result[inst_id] = {}
        for row in rows:
            try:
                result[inst_id][row["timestamp"].replace("+00:00", "Z")] = math.isfinite(float(row["open"])) and math.isfinite(float(row["volume"]))
            except (KeyError, TypeError, ValueError):
                result[inst_id][row.get("timestamp", "")] = False
    return result


def _slot_flags(start: str, end: str, bars: dict[str, dict[str, bool]]) -> tuple[bool, bool]:
    starts = [bars[inst].get(start, False) for inst in INST_IDS]
    ends = [bars[inst].get(end, False) for inst in INST_IDS]
    return all(starts), all(ends)


def _strategy_rows(families: list[dict[str, Any]], intervals: list[dict[str, str]], bars: dict[str, dict[str, bool]], membership: dict[str, bool], scenarios: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for interval in intervals:
        start, end = interval["execution_timestamp"], interval["next_execution_timestamp"]
        start_ok, end_ok = _slot_flags(start, end, bars)
        for family in families:
            rows.append({**family, "interval_id": interval["target_index"], "execution_start": start, "execution_end": end, "target_available": True, "start_open_available": start_ok, "end_open_available": end_ok, "capacity_volume_available": start_ok and end_ok, "membership_valid": membership.get(interval["signal_timestamp"], False), "cost_contract_valid": scenarios["scenario_count"] == 9})
    if len(rows) != 5760 or any(not row["membership_valid"] or not row["start_open_available"] or not row["end_open_available"] for row in rows):
        raise MarketDataError("readiness strategy matrix incomplete")
    return rows


def _benchmark_rows(intervals: list[dict[str, str]], bars: dict[str, dict[str, bool]], scenarios: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for interval in intervals:
        start_ok, end_ok = _slot_flags(interval["execution_timestamp"], interval["next_execution_timestamp"], bars)
        rows.append({"benchmark_id": "equal_weight_six_asset_hourly_rebalanced", "interval_id": interval["target_index"], "execution_start": interval["execution_timestamp"], "execution_end": interval["next_execution_timestamp"], "start_open_available": start_ok, "end_open_available": end_ok, "capacity_volume_available": start_ok and end_ok, "cost_contract_valid": scenarios["scenario_count"] == 9})
    if len(rows) != 160 or any(not row["start_open_available"] or not row["end_open_available"] for row in rows):
        raise MarketDataError("readiness benchmark matrix incomplete")
    return rows


def _dependency_rows(*items: tuple[dict[str, Any], Path, str]) -> list[dict[str, Any]]:
    rows = []
    for report, path, kind in items:
        artifacts = {name: info["sha256"] for name, info in report.get("artifacts", {}).items() if name != "report" and isinstance(info, dict) and "sha256" in info}
        identity_key = next((key for key in ("ledger_sha256", "accounting_sha256", "cost_sha256", "semantics_sha256", "scenario_contract_sha256", "extension_sha256", "gate_sha256", "audit_sha256") if key in report), "")
        rows.append({"type": kind, "identity": report[identity_key], "marker_sha256": _sha256(path), "artifact_hashes": json.dumps(artifacts, sort_keys=True), "validation_status": "verified"})
    return rows


def _constraint_rows() -> list[dict[str, Any]]:
    values = {"family_alignment_ready": True, "interval_alignment_ready": True, "execution_price_inputs_ready": True, "capacity_volume_inputs_ready": True, "cost_application_semantics_ready": True, "benchmark_alignment_ready": True, "prospective_economic_inputs_structurally_ready": True, "economic_value_computation_authorized": False, "turnover_computation_authorized": False, "cost_amount_computation_authorized": False, "capacity_pass_fail_authorized": False, "return_computation_authorized": False, "pnl_computation_authorized": False, "profitability_evidence": False, "trading_readiness_changed": False, "future_only_membership_evidence": True, "historical_point_in_time_membership": False, "historical_survivorship_bias_resolved": False, "historical_spread_directly_observed": False, "historical_slippage_directly_observed": False, "market_impact_model_verified": False, "variant_selection_prohibited": True}
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True)} for key in sorted(values)]


def _artifact(marker_path: Path, marker: dict[str, Any], name: str) -> str:
    info = marker.get("artifacts", {}).get(name)
    if not isinstance(info, dict) or not isinstance(info.get("filename"), str) or not isinstance(info.get("sha256"), str):
        raise MarketDataError("readiness artifact metadata mismatch")
    artifact = marker_path.parent / info["filename"]
    if _sha256(artifact) != info["sha256"]:
        raise MarketDataError("readiness artifact hash mismatch")
    return info["sha256"]


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("readiness csv read failed") from exc


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("readiness json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("readiness json shape mismatch")
    return value


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("readiness output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("readiness repo root not found")


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
            raise MarketDataError(f"readiness content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".readiness-", suffix=".tmp", delete=False) as handle:
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
        raise MarketDataError("readiness hash failed") from exc


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
