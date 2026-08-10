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


SCHEMA_VERSION = 1
POLICY_ID = "prospective_economic_sample_maturity_v1"
CONTRACT_STATUS = "verified_prospective_economic_sample_maturity_gate"
DEFAULT_CONFIG_FILENAME = "config.prospective-economic-sample-maturity.example.yaml"
READINESS_SHA256 = "0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b"
MINIMUM_INTERVALS = 500
FAMILY_SIZE = 36
STRATEGY_SLOTS = 5760
STRATEGY_FIELDS = ("family_member_id", "factor", "horizon", "direction", "interval_id", "execution_start", "execution_end", "target_available", "start_open_available", "end_open_available", "capacity_volume_available", "membership_valid", "cost_contract_valid")
BENCHMARK_FIELDS = ("benchmark_id", "interval_id", "execution_start", "execution_end", "start_open_available", "end_open_available", "capacity_volume_available", "cost_contract_valid")
EPOCH_FIELDS = ("ordinal", "readiness_identity", "first_execution_start", "last_execution_end", "closed_interval_count", "family_size", "protocol_fingerprint", "preceding_gap_class")
INTERVAL_FIELDS = ("sample_ordinal", "epoch_ordinal", "interval_id", "execution_start", "execution_end", "family_completeness", "benchmark_completeness", "membership_valid", "counted_as_sample")
POLICY_FIELDS = ("key", "value")
CONSTRAINT_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectiveEconomicSampleMaturityResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def audit_prospective_economic_sample_maturity(
    readiness_reports: list[str | Path], config_path: str | Path, output_dir: str | Path
) -> ProspectiveEconomicSampleMaturityResult:
    if not readiness_reports:
        raise ValueError("at least one readiness report is required")
    first_path = Path(readiness_reports[0]).resolve()
    repo = _repo_root(first_path)
    load_maturity_config(config_path, repo)
    reports = [_validate_readiness(Path(path).resolve(), repo) for path in readiness_reports]
    epochs = [_epoch_info(report, ordinal + 1) for ordinal, report in enumerate(reports)]
    _validate_epoch_order(epochs)
    intervals = _merge_intervals(reports)
    unique_count = len(intervals)
    remaining = max(0, MINIMUM_INTERVALS - unique_count)
    mature = unique_count >= MINIMUM_INTERVALS
    epoch_rows = _epoch_rows(epochs)
    interval_rows = _interval_rows(epochs, intervals)
    policy_rows = _policy_rows(unique_count, remaining, mature)
    constraint_rows = _constraint_rows(unique_count, remaining, mature)
    epoch_bytes = _csv_bytes(epoch_rows, EPOCH_FIELDS)
    interval_bytes = _csv_bytes(interval_rows, INTERVAL_FIELDS)
    policy_bytes = _csv_bytes(policy_rows, POLICY_FIELDS)
    constraint_bytes = _csv_bytes(constraint_rows, CONSTRAINT_FIELDS)
    epoch_sha, interval_sha, policy_sha, constraint_sha = map(_digest, (epoch_bytes, interval_bytes, policy_bytes, constraint_bytes))
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "sample_unit": "unique_closed_execution_interval",
        "minimum_closed_interval_count": MINIMUM_INTERVALS,
        "epoch_overlap_policy": "reject",
        "cross_epoch_gap_policy": "allow_only_explicit_membership_snapshot_boundary",
        "readiness_reports": [{"ordinal": item["ordinal"], "readiness_sha256": item["readiness_sha256"], "marker_sha256": item["marker_sha256"], "artifact_hashes": item["artifact_hashes"]} for item in epochs],
        "protocol_fingerprint": epochs[0]["protocol_fingerprint"],
        "counts": {"readiness_epoch_count": len(epochs), "family_size": FAMILY_SIZE, "unique_closed_interval_count": unique_count, "remaining_closed_interval_count": remaining, "strategy_slot_count_observed": sum(item["strategy_matrix_rows"] for item in epochs), "strategy_slots_count_as_samples": False},
        "claims": {"future_only_membership_evidence": True, "historical_point_in_time_membership": False, "historical_survivorship_bias_resolved": False, "prior_related_results_exist": True, "global_preregistration": False, "minimum_500_is_policy_floor_not_profitability_guarantee": True, "profitability_evidence": False, "readiness_changed": False},
        "authorization": {"turnover_value_rows": 0, "cost_amount_rows": 0, "capacity_pass_fail_rows": 0, "return_rows": 0, "pnl_rows": 0, "economic_value_computation_authorized": False, "turnover_computation_authorized": False, "cost_amount_computation_authorized": False, "capacity_pass_fail_authorized": False, "return_computation_authorized": False, "pnl_computation_authorized": False, "prospective_economic_evaluation_authorizable": mature, "readiness_changed": False},
        "artifacts": {"epochs_sha256": epoch_sha, "intervals_sha256": interval_sha, "policy_sha256": policy_sha, "constraints_sha256": constraint_sha},
    }
    maturity_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-economic-sample-maturity.{maturity_sha}"
    paths = {"epochs": output / f"{stem}.epochs.csv", "intervals": output / f"{stem}.intervals.csv", "policy": output / f"{stem}.policy.csv", "constraints": output / f"{stem}.constraints.csv", "report": output / f"{stem}.json"}
    for key, content in (("epochs", epoch_bytes), ("intervals", interval_bytes), ("policy", policy_bytes), ("constraints", constraint_bytes)):
        _commit_bytes(paths[key], content)
    report = {
        "schema_version": SCHEMA_VERSION, "maturity_sha256": maturity_sha, "contract_status": CONTRACT_STATUS,
        "readiness_epoch_count": len(epochs), "family_size": FAMILY_SIZE, "unique_closed_interval_count": unique_count, "minimum_closed_interval_count": MINIMUM_INTERVALS, "remaining_closed_interval_count": remaining, "strategy_slot_count_observed": sum(item["strategy_matrix_rows"] for item in epochs), "strategy_slots_count_as_samples": False, "sample_maturity_met": mature, "prospective_economic_evaluation_authorizable": mature,
        "turnover_value_rows": 0, "cost_amount_rows": 0, "capacity_pass_fail_rows": 0, "return_rows": 0, "pnl_rows": 0, "profitability_evidence": False, "readiness_changed": False,
        "future_only_membership_evidence": True, "historical_point_in_time_membership": False, "historical_survivorship_bias_resolved": False,
        "identity": identity,
        "artifacts": {"epochs": {"filename": paths["epochs"].name, "sha256": epoch_sha, "row_count": len(epoch_rows)}, "intervals": {"filename": paths["intervals"].name, "sha256": interval_sha, "row_count": len(interval_rows)}, "policy": {"filename": paths["policy"].name, "sha256": policy_sha, "row_count": len(policy_rows)}, "constraints": {"filename": paths["constraints"].name, "sha256": constraint_sha, "row_count": len(constraint_rows)}, "report": {"filename": paths["report"].name}},
    }
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveEconomicSampleMaturityResult(report, {key: str(value) for key, value in paths.items()})


def load_maturity_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if not config_file.is_file() or config_file.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("sample maturity config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("sample maturity config must equal the frozen config")
    if repo is not None and not config_file.is_relative_to(repo.resolve()):
        raise MarketDataError("sample maturity config path escape")
    required = {"schema_version": 1, "policy_id": POLICY_ID, "sample_unit": "unique_closed_execution_interval", "minimum_closed_interval_count": 500, "minimum_basis": "existing_project_fixed_oos_evidence_floor", "required_family_size": 36, "family_completeness_policy": "all_36_members_per_interval", "terminal_rows_count_as_samples": False, "strategy_slots_count_as_samples": False, "benchmark_slots_count_as_samples": False, "epoch_overlap_policy": "reject", "cross_epoch_gap_policy": "allow_only_explicit_membership_snapshot_boundary", "historical_interval_inclusion_prohibited": True, "protocol_drift_policy": "reject", "threshold_reduction_prohibited": True, "result_driven_threshold_change_prohibited": True, "economic_value_computation_authorized": False, "turnover_computation_authorized": False, "cost_amount_computation_authorized": False, "capacity_pass_fail_authorized": False, "return_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in required.items()):
        raise MarketDataError("sample maturity config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_prospective_economic_sample_maturity(result: ProspectiveEconomicSampleMaturityResult) -> str:
    report = result.report
    return "\n".join([f"contract_status: {report['contract_status']}", f"maturity_sha256: {report['maturity_sha256']}", f"readiness_epoch_count: {report['readiness_epoch_count']}", "family_size: 36", f"unique_closed_interval_count: {report['unique_closed_interval_count']}", "minimum_closed_interval_count: 500", f"remaining_closed_interval_count: {report['remaining_closed_interval_count']}", "strategy_slots_count_as_samples: false", f"sample_maturity_met: {str(report['sample_maturity_met']).lower()}", f"prospective_economic_evaluation_authorizable: {str(report['prospective_economic_evaluation_authorizable']).lower()}", "readiness_changed: false"])


def _validate_readiness(path: Path, repo: Path) -> dict[str, Any]:
    report = _load_json(path)
    readiness_sha = report.get("readiness_sha256")
    if not isinstance(readiness_sha, str) or path.parent != (repo / "reports/prospective-economic-readiness").resolve() or path.name != f"prospective-economic-readiness.{readiness_sha}.json" or report.get("contract_status") != "verified_prospective_economic_evaluation_readiness_gate":
        raise MarketDataError("sample maturity readiness marker mismatch")
    identity = report.get("identity")
    if not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != readiness_sha:
        raise MarketDataError("sample maturity readiness identity mismatch")
    if report.get("prospective_economic_inputs_structurally_ready") is not True or report.get("family_size") != 36 or report.get("closed_interval_count") != 160 or report.get("benchmark_matrix_rows") != 160 or report.get("strategy_matrix_rows") != 5760 or any(report.get(key) is not False for key in ("economic_value_computation_authorized", "turnover_computation_authorized", "cost_amount_computation_authorized", "capacity_pass_fail_authorized", "return_computation_authorized", "pnl_computation_authorized", "trading_readiness_changed")):
        raise MarketDataError("sample maturity readiness claims mismatch")
    artifacts = {key: _artifact(path, report, key) for key in ("strategy_matrix", "benchmark_matrix", "dependencies", "constraints")}
    strategy = _read_csv(path.parent / report["artifacts"]["strategy_matrix"]["filename"])
    benchmark = _read_csv(path.parent / report["artifacts"]["benchmark_matrix"]["filename"])
    _validate_matrix(strategy, benchmark)
    intervals = _unique_intervals(strategy)
    if len(intervals) != 160:
        raise MarketDataError("sample maturity readiness interval count mismatch")
    return {"readiness_sha256": readiness_sha, "marker_sha256": _sha256(path), "artifact_hashes": artifacts, "strategy_matrix_rows": len(strategy), "intervals": intervals, "protocol_fingerprint": _protocol_fingerprint(identity), "sources": identity.get("sources", {})}


def _validate_matrix(strategy: list[dict[str, str]], benchmark: list[dict[str, str]]) -> None:
    if len(strategy) != STRATEGY_SLOTS or len(benchmark) != 160:
        raise MarketDataError("sample maturity matrix row count mismatch")
    required_true = ("target_available", "start_open_available", "end_open_available", "capacity_volume_available", "membership_valid", "cost_contract_valid")
    if any(any(row.get(key) != "true" for key in required_true) for row in strategy):
        raise MarketDataError("sample maturity strategy matrix not ready")
    if any(any(row.get(key) != "true" for key in ("start_open_available", "end_open_available", "capacity_volume_available", "cost_contract_valid")) for row in benchmark):
        raise MarketDataError("sample maturity benchmark matrix not ready")


def _unique_intervals(strategy: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in strategy:
        key = (row["interval_id"], row["execution_start"], row["execution_end"])
        grouped.setdefault(key, []).append(row)
    if any(len(rows) != FAMILY_SIZE for rows in grouped.values()):
        raise MarketDataError("sample maturity family completeness mismatch")
    ordered = sorted(({"interval_id": key[0], "execution_start": key[1], "execution_end": key[2], "family_completeness": True, "benchmark_completeness": True, "membership_valid": True, "counted_as_sample": True} for key in grouped), key=lambda row: str(row["execution_start"]))
    if len(ordered) != 160 or len({(row["execution_start"], row["execution_end"]) for row in ordered}) != len(ordered):
        raise MarketDataError("sample maturity duplicate interval")
    return ordered


def _protocol_fingerprint(identity: dict[str, Any]) -> str:
    sources = identity.get("sources", {})
    static = {key: sources.get(key, {}) for key in ("accounting", "cost_scenarios")}
    return _digest(_canonical_json_bytes(static))


def _epoch_info(report: dict[str, Any], ordinal: int) -> dict[str, Any]:
    intervals = report["intervals"]
    return {"ordinal": ordinal, "readiness_sha256": report["readiness_sha256"], "readiness_identity": report["readiness_sha256"], "marker_sha256": report["marker_sha256"], "artifact_hashes": report["artifact_hashes"], "protocol_fingerprint": report["protocol_fingerprint"], "strategy_matrix_rows": report["strategy_matrix_rows"], "first_execution_start": intervals[0]["execution_start"], "last_execution_end": intervals[-1]["execution_end"], "closed_interval_count": len(intervals), "family_size": FAMILY_SIZE, "intervals": intervals, "sources": report["sources"], "preceding_gap_class": "none"}


def _validate_epoch_order(epochs: list[dict[str, Any]]) -> None:
    for previous, current in zip(epochs, epochs[1:]):
        if current["protocol_fingerprint"] != previous["protocol_fingerprint"]:
            raise MarketDataError("sample maturity protocol drift")
        previous_end = previous["last_execution_end"]
        current_start = current["first_execution_start"]
        if current_start <= previous_end:
            raise MarketDataError("sample maturity epoch overlap_or_reverse")
        if current["sources"].get("membership_gate", {}).get("identity") == previous["sources"].get("membership_gate", {}).get("identity"):
            raise MarketDataError("sample maturity unexplained_cross_epoch_gap")
        current["preceding_gap_class"] = "membership_snapshot_boundary_unscored"


def _merge_intervals(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for ordinal, report in enumerate(reports, 1):
        for row in report["intervals"]:
            key = (row["interval_id"], row["execution_start"], row["execution_end"])
            if key in seen:
                raise MarketDataError("sample maturity duplicate_interval_across_epochs")
            seen.add(key)
            copied = dict(row)
            copied["epoch_ordinal"] = ordinal
            merged.append(copied)
    return sorted(merged, key=lambda row: row["execution_start"])


def _epoch_rows(epochs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: epoch[key] for key in EPOCH_FIELDS} for epoch in epochs]


def _interval_rows(epochs: list[dict[str, Any]], intervals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"sample_ordinal": index, "epoch_ordinal": row["epoch_ordinal"], "interval_id": row["interval_id"], "execution_start": row["execution_start"], "execution_end": row["execution_end"], "family_completeness": True, "benchmark_completeness": True, "membership_valid": True, "counted_as_sample": True} for index, row in enumerate(intervals, 1)]


def _policy_rows(unique_count: int, remaining: int, mature: bool) -> list[dict[str, Any]]:
    values = {"minimum_closed_interval_count": MINIMUM_INTERVALS, "sample_unit": "unique_closed_execution_interval", "threshold_basis": "existing_project_fixed_oos_evidence_floor", "terminal_excluded": True, "strategy_slots_count_as_samples": False, "benchmark_slots_count_as_samples": False, "threshold_reduction_prohibited": True, "current_unique_closed_interval_count": unique_count, "remaining_closed_interval_count": remaining, "sample_maturity_met": mature}
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True)} for key in sorted(values)]


def _constraint_rows(unique_count: int, remaining: int, mature: bool) -> list[dict[str, Any]]:
    values = {"unique_closed_interval_count": unique_count, "remaining_closed_interval_count": remaining, "sample_maturity_met": mature, "prospective_economic_evaluation_authorizable": mature, "future_only_membership_evidence": True, "historical_point_in_time_membership": False, "historical_survivorship_bias_resolved": False, "turnover_value_rows": 0, "cost_amount_rows": 0, "capacity_pass_fail_rows": 0, "return_rows": 0, "pnl_rows": 0, "economic_value_computation_authorized": False, "turnover_computation_authorized": False, "cost_amount_computation_authorized": False, "capacity_pass_fail_authorized": False, "return_computation_authorized": False, "pnl_computation_authorized": False, "profitability_evidence": False, "readiness_changed": False}
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True)} for key in sorted(values)]


def _artifact(marker_path: Path, marker: dict[str, Any], name: str) -> str:
    info = marker.get("artifacts", {}).get(name)
    if not isinstance(info, dict) or not isinstance(info.get("filename"), str) or not isinstance(info.get("sha256"), str):
        raise MarketDataError("sample maturity artifact metadata mismatch")
    if _sha256(marker_path.parent / info["filename"]) != info["sha256"]:
        raise MarketDataError("sample maturity artifact hash mismatch")
    return info["sha256"]


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("sample maturity csv read failed") from exc


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("sample maturity json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("sample maturity json shape mismatch")
    return value


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("sample maturity repo root not found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("sample maturity output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


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
            raise MarketDataError(f"sample maturity content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sample-maturity-", suffix=".tmp", delete=False) as handle:
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
        raise MarketDataError("sample maturity hash failed") from exc


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
