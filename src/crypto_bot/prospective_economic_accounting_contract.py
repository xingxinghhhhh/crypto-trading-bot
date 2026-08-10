from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.execution_cost_evidence import load_execution_cost_evidence_config


SCHEMA_VERSION = 1
CONTRACT_STATUS = "verified_prospective_execution_to_execution_accounting_contract"
DEFAULT_CONFIG_FILENAME = "config.prospective-economic-accounting.example.yaml"
INST_IDS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT")
HORIZONS = (4, 16, 64)
RANK_DIRECTIONS = ("high_rank_selected", "low_rank_selected")
INTERVAL_FIELDS = (
    "target_index", "signal_timestamp", "completion_timestamp", "execution_timestamp",
    "next_execution_timestamp", "interval_status", "target_exists", "economic_interval_closed",
    "economic_scoring_authorized", "terminal_unscored",
)
FAMILY_FIELDS = (
    "family_member_id", "factor", "horizon", "rank_direction", "horizon_role",
    "economic_holding_period_bars", "rebalance_frequency_bars", "selection_prohibited",
)
COST_FIELDS = ("component", "policy", "values", "application_unit", "semantic_status", "economic_cost_application_ready")
BENCHMARK_FIELDS = (
    "benchmark_id", "symbol", "weight", "rebalance_frequency_bars", "initial_state",
    "uses_same_execution_timestamps", "uses_same_cost_contract", "return_computation_authorized",
)
CONSTRAINT_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectiveEconomicAccountingContractResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_prospective_economic_accounting_contract(
    portfolio_ledger_report: str | Path,
    cost_evidence_report: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveEconomicAccountingContractResult:
    ledger_path = Path(portfolio_ledger_report).resolve()
    repo = _repo_root(ledger_path)
    config = load_accounting_config(config_path, repo)
    cost_path = Path(cost_evidence_report).resolve()
    _pin_path(repo, ledger_path, config["portfolio_ledger_report"])
    _pin_path(repo, cost_path, config["cost_evidence_report"])
    ledger = _validate_ledger(ledger_path, config)
    cost = _validate_cost(cost_path, repo, config)
    if ledger["ledger_sha256"] != config["portfolio_ledger_sha256"] or cost["cost_sha256"] != config["cost_evidence_sha256"]:
        raise MarketDataError("prospective_accounting_source_identity_mismatch")
    timestamps = list(ledger["identity"]["signal_timestamps"])
    if len(timestamps) != 161:
        raise MarketDataError("prospective_accounting_signal_count_mismatch")
    execution = [_shift_iso(value, 2) for value in timestamps]
    if execution != sorted(execution) or any(_parse(execution[i + 1]) - _parse(execution[i]) != timedelta(hours=1) for i in range(160)):
        raise MarketDataError("prospective_accounting_execution_grid_mismatch")
    intervals = _interval_rows(timestamps, execution)
    family_rows = _family_rows(ledger)
    cost_rows = _cost_rows(cost)
    benchmark_rows = _benchmark_rows()
    constraints = _constraint_rows(config, ledger, cost)
    interval_bytes = _csv_bytes(intervals, INTERVAL_FIELDS)
    family_bytes = _csv_bytes(family_rows, FAMILY_FIELDS)
    cost_bytes = _csv_bytes(cost_rows, COST_FIELDS)
    benchmark_bytes = _csv_bytes(benchmark_rows, BENCHMARK_FIELDS)
    constraint_bytes = _csv_bytes(constraints, CONSTRAINT_FIELDS)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": config["policy_id"],
        "sources": {
            "portfolio_ledger_sha256": config["portfolio_ledger_sha256"],
            "portfolio_ledger_report_sha256": _sha256(ledger_path),
            "cost_evidence_sha256": config["cost_evidence_sha256"],
            "cost_evidence_report_sha256": _sha256(cost_path),
            "preregistration_sha256": ledger["identity"]["sources"]["preregistration_sha256"],
            "mechanism_sha256": ledger["identity"]["sources"]["mechanism_sha256"],
            "membership_gate_sha256": ledger["identity"]["sources"]["membership_gate_sha256"],
            "market_data_extension_sha256": ledger["identity"]["sources"]["market_data_extension_sha256"],
            "execution_mapping_sha256": ledger["identity"]["sources"]["execution_mapping_sha256"],
        },
        "schedule": {"target_state_count": 161, "closed_interval_count": 160, "terminal_unscored_count": 1, "economic_holding_period_bars": 1},
        "policies": {
            "rebalance_frequency_bars": 1,
            "horizon_role": "research_family_label_only",
            "target_effective_policy": "at_frozen_execution_open",
            "initial_state": "all_cash",
            "initial_trade_policy": "transition_from_cash_to_first_target",
            "terminal_policy": "last_target_unscored_without_next_execution_anchor",
            "terminal_liquidation": False,
            "trade_notional_policy": "sum_abs_asset_weight_changes",
            "turnover_reporting_policy": "one_way_total_variation_including_cash",
            "cost_application_policy": "per_execution_trade_notional",
            "spread_application_semantics_resolved": False,
            "economic_cost_application_ready": False,
            "primary_benchmark": "equal_weight_six_asset_hourly_rebalanced",
        },
        "artifacts": {
            "intervals_sha256": _digest(interval_bytes),
            "family_policy_sha256": _digest(family_bytes),
            "cost_policy_sha256": _digest(cost_bytes),
            "benchmark_sha256": _digest(benchmark_bytes),
            "constraints_sha256": _digest(constraint_bytes),
        },
        "claims": config["claims"],
    }
    accounting_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-economic-accounting.{accounting_sha}"
    paths = {
        "intervals": output / f"{stem}.intervals.csv",
        "family_policy": output / f"{stem}.family-policy.csv",
        "cost_policy": output / f"{stem}.cost-policy.csv",
        "benchmark": output / f"{stem}.benchmark.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    for key, content in (("intervals", interval_bytes), ("family_policy", family_bytes), ("cost_policy", cost_bytes), ("benchmark", benchmark_bytes), ("constraints", constraint_bytes)):
        _commit_bytes(paths[key], content)
    report = {
        "schema_version": SCHEMA_VERSION,
        "accounting_sha256": accounting_sha,
        "contract_status": CONTRACT_STATUS,
        "identity": identity,
        "target_state_count": 161,
        "closed_interval_count": 160,
        "terminal_unscored_count": 1,
        "family_size": 36,
        "benchmark_asset_count": 6,
        "return_rows": 0,
        "turnover_value_rows": 0,
        "cost_amount_rows": 0,
        "pnl_rows": 0,
        "spread_application_semantics_resolved": False,
        "economic_cost_application_ready": False,
        "future_only_membership_evidence": True,
        "historical_point_in_time_membership": False,
        "historical_survivorship_bias_resolved": False,
        "selection_prohibited": True,
        "return_computation_authorized": False,
        "turnover_computation_authorized": False,
        "cost_amount_computation_authorized": False,
        "pnl_computation_authorized": False,
        "profitability_evidence": False,
        "readiness_changed": False,
        "artifacts": {
            "intervals": {"filename": paths["intervals"].name, "sha256": identity["artifacts"]["intervals_sha256"], "row_count": 161},
            "family_policy": {"filename": paths["family_policy"].name, "sha256": identity["artifacts"]["family_policy_sha256"], "row_count": 36},
            "cost_policy": {"filename": paths["cost_policy"].name, "sha256": identity["artifacts"]["cost_policy_sha256"], "row_count": len(cost_rows)},
            "benchmark": {"filename": paths["benchmark"].name, "sha256": identity["artifacts"]["benchmark_sha256"], "row_count": 6},
            "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)},
            "report": {"filename": paths["report"].name},
        },
    }
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveEconomicAccountingContractResult(report, {key: str(value) for key, value in paths.items()})


def load_accounting_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if not config_file.is_file() or config_file.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("prospective accounting config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("prospective accounting config must equal the frozen config")
    if repo is not None and not config_file.is_relative_to(repo.resolve()):
        raise MarketDataError("prospective_accounting_config_path_escape")
    if value["rebalance_frequency_bars"] != 1 or value["family_size"] != 36 or value["target_state_count"] != 161 or value["closed_interval_count"] != 160:
        raise MarketDataError("prospective_accounting_policy_shape_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_prospective_economic_accounting_contract(result: ProspectiveEconomicAccountingContractResult) -> str:
    report = result.report
    return "\n".join([
        f"contract_status: {report['contract_status']}",
        f"accounting_sha256: {report['accounting_sha256']}",
        "target_state_count: 161",
        "closed_interval_count: 160",
        "terminal_unscored_count: 1",
        "family_size: 36",
        "spread_application_semantics_resolved: false",
        "economic_cost_application_ready: false",
        "return_rows: 0",
        "turnover_value_rows: 0",
        "cost_amount_rows: 0",
        "pnl_rows: 0",
        "readiness_changed: false",
    ])


def _validate_ledger(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    report = _load_json(path)
    if path.name != f"prospective-portfolio-ledger.{config['portfolio_ledger_sha256']}.json" or _sha256(path) != config["portfolio_ledger_report_sha256"] or report.get("ledger_sha256") != config["portfolio_ledger_sha256"] or report.get("ledger_status") != "verified_prospective_closed_epoch_portfolio_ledger" or report.get("signal_timestamp_count") != 161 or report.get("family_count") != 36 or report.get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("prospective_accounting_ledger_claims_mismatch")
    identity = report.get("identity")
    if not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != config["portfolio_ledger_sha256"]:
        raise MarketDataError("prospective_accounting_ledger_identity_mismatch")
    for key in ("factor_scores", "decisions", "weights", "constraints"):
        info = report.get("artifacts", {}).get(key)
        if not isinstance(info, dict) or _sha256(path.parent / info["filename"]) != info["sha256"]:
            raise MarketDataError("prospective_accounting_ledger_artifact_mismatch")
    return report


def _validate_cost(path: Path, repo: Path, config: dict[str, Any]) -> dict[str, Any]:
    report = _load_json(path)
    if path.name != f"execution-cost-evidence.{config['cost_evidence_sha256']}.json" or _sha256(path) != config["cost_evidence_report_sha256"] or report.get("cost_sha256") != config["cost_evidence_sha256"] or report.get("audit_status") != "verified_okx_direct_six_1h_execution_cost_evidence" or report.get("feasibility", {}).get("cost_contract_frozen") is not True or report.get("feasibility", {}).get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("prospective_accounting_cost_claims_mismatch")
    cost_config = load_execution_cost_evidence_config(repo / "config.execution-cost-evidence.example.yaml", repo)
    if cost_config["spread_stress_bps"] != [0, 5, 10] or cost_config["slippage_stress_bps"] != [0, 5, 10] or cost_config["taker_fee_bps"] != 100 or cost_config["cost_uniform_across_variants"] is not True:
        raise MarketDataError("prospective_accounting_cost_policy_mismatch")
    for key in ("fee", "stress", "constraints"):
        info = report.get("artifacts", {}).get(key)
        if not isinstance(info, dict) or _sha256(path.parent / info["filename"]) != info["sha256"]:
            raise MarketDataError("prospective_accounting_cost_artifact_mismatch")
    return report


def _interval_rows(timestamps: list[str], execution: list[str]) -> list[dict[str, Any]]:
    rows = []
    for index, (signal, exec_time) in enumerate(zip(timestamps, execution, strict=True)):
        closed = index < len(execution) - 1
        rows.append({"target_index": index, "signal_timestamp": signal, "completion_timestamp": _shift_iso(signal, 1), "execution_timestamp": exec_time, "next_execution_timestamp": execution[index + 1] if closed else "", "interval_status": "closed_and_scoreable_in_future_evaluator" if closed else "terminal_unscored", "target_exists": True, "economic_interval_closed": closed, "economic_scoring_authorized": False, "terminal_unscored": not closed})
    return rows


def _family_rows(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for factor in ledger["identity"]["family"]["factors"]:
        for horizon in HORIZONS:
            for direction in RANK_DIRECTIONS:
                rows.append({"family_member_id": f"{factor}.h{horizon}.{direction}", "factor": factor, "horizon": horizon, "rank_direction": direction, "horizon_role": "research_family_label_only", "economic_holding_period_bars": 1, "rebalance_frequency_bars": 1, "selection_prohibited": True})
    if len(rows) != 36:
        raise MarketDataError("prospective_accounting_family_shape_mismatch")
    return rows


def _cost_rows(cost: dict[str, Any]) -> list[dict[str, Any]]:
    identity = cost["identity"]
    return [
        {"component": "fee", "policy": identity["fee"]["fee_policy_id"], "values": json.dumps([identity["fee"]["fee_rate_bps"]]), "application_unit": "per_execution_trade_notional", "semantic_status": "frozen_assumption", "economic_cost_application_ready": False},
        {"component": "spread", "policy": identity["spread"]["policy"], "values": json.dumps(identity["spread"]["stress_bps"]), "application_unit": "unresolved_one_way_or_full_quoted_spread", "semantic_status": "unresolved_blocker", "economic_cost_application_ready": False},
        {"component": "slippage", "policy": identity["slippage"]["policy"], "values": json.dumps(identity["slippage"]["stress_bps"]), "application_unit": "per_execution_trade_notional", "semantic_status": "frozen_stress_only", "economic_cost_application_ready": False},
        {"component": "capacity", "policy": identity["capacity"]["policy"], "values": json.dumps({"volume_field": "volume", "max_participation_rate": 0.01}, sort_keys=True), "application_unit": "capacity_evidence_only", "semantic_status": "not_execution_guarantee", "economic_cost_application_ready": False},
    ]


def _benchmark_rows() -> list[dict[str, Any]]:
    return [{"benchmark_id": "equal_weight_six_asset_hourly_rebalanced", "symbol": symbol, "weight": 1 / 6, "rebalance_frequency_bars": 1, "initial_state": "all_cash", "uses_same_execution_timestamps": True, "uses_same_cost_contract": True, "return_computation_authorized": False} for symbol in INST_IDS]


def _constraint_rows(config: dict[str, Any], ledger: dict[str, Any], cost: dict[str, Any]) -> list[dict[str, Any]]:
    values = {
        "portfolio_ledger_sha256": config["portfolio_ledger_sha256"], "cost_evidence_sha256": config["cost_evidence_sha256"],
        "target_state_count": 161, "closed_interval_count": 160, "terminal_unscored_count": 1, "family_size": 36,
        "rebalance_frequency_bars": 1, "horizon_role": "research_family_label_only", "economic_holding_period_bars": 1,
        "trade_notional_policy": "sum_abs_asset_weight_changes", "turnover_reporting_policy": "one_way_total_variation_including_cash",
        "spread_application_semantics_resolved": False, "economic_cost_application_ready": False,
        "return_computation_authorized": False, "turnover_computation_authorized": False, "cost_amount_computation_authorized": False, "pnl_computation_authorized": False, "profitability_evidence": False, "readiness_changed": False,
    }
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True)} for key in sorted(values)]


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _shift_iso(value: str, hours: int) -> str:
    return (_parse(value) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _pin_path(repo: Path, actual: Path, relative: str) -> None:
    if actual != (repo / relative).resolve():
        raise MarketDataError("prospective_accounting_input_path_mismatch")


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("prospective_accounting_repo_root_not_found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("prospective accounting output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("prospective_accounting_invalid_json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("prospective_accounting_invalid_json")
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
            raise MarketDataError(f"prospective_accounting_content_addressed_collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".prospective-accounting-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
