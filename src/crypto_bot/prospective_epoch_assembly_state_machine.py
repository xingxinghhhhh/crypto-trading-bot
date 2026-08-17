from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from crypto_bot.errors import MarketDataError


SCHEMA_VERSION = 1
POLICY_ID = "prospective_future_epoch_assembly_v1"
CONTRACT_STATUS = "verified_prospective_future_epoch_assembly_state_machine"
DEFAULT_CONFIG_FILENAME = "config.prospective-epoch-assembly.example.yaml"
POLICY_SHA = "8f4a90632bcf73df04c7e6f44924924db682cdc8373ed04c1f726cb6ce353092"
MATURITY_SHA = "32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178"
READINESS_SHA = "0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b"
CHAIN_SHA = "c6e3ccad96735923760931afc3b81e16068e880d64a97ec4d34d84908a681f05"
SNAPSHOT_SHA = "d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e"
STATES = ("awaiting_capture_window", "snapshot_accepted", "transition_verified", "membership_gate_verified", "market_segment_verified", "segment_chain_appended", "portfolio_ledger_verified", "economic_readiness_verified", "maturity_appendable", "maturity_counted")
STATE_FIELDS = ("ordinal", "state", "required_parent_artifact", "admission_predicate", "next_state", "skip_prohibited")
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class EpochAssemblyResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_prospective_epoch_assembly_state_machine(
    accumulation_policy: str | Path,
    sample_maturity: str | Path,
    latest_readiness: str | Path,
    segment_chain: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> EpochAssemblyResult:
    policy_path, maturity_path, readiness_path, chain_path = map(lambda x: Path(x).resolve(), (accumulation_policy, sample_maturity, latest_readiness, segment_chain))
    repo = _repo_root(policy_path)
    load_assembly_config(config_path, repo)
    policy = _marker(policy_path, "prospective-epoch-accumulation-policy", repo)
    maturity = _marker(maturity_path, "prospective-economic-sample-maturity", repo)
    readiness = _marker(readiness_path, "prospective-economic-readiness", repo)
    chain = _marker(chain_path, "prospective-direct-1h-segment-chain", repo)
    _validate_inputs(policy, maturity, readiness, chain, policy_path, maturity_path, readiness_path, chain_path)
    window_start = _parse_iso(policy["first_governed_window_start"])
    window_end = _parse_iso(policy["first_governed_window_end"])
    chain_start = chain["next_canonical_segment_start"]
    state_rows = _state_rows()
    next_values = {"epoch_ordinal": 2, "current_state": STATES[0], "governed_capture_window_start": _iso(window_start), "governed_capture_window_end": _iso(window_end), "previous_snapshot_sha256": SNAPSHOT_SHA, "next_segment_start": chain_start, "segment_end_resolved": False, "new_samples_counted": 0}
    protocol_values = {"required_family_size": 36, "sample_threshold": 500, "static_protocol_fingerprint": maturity["identity"]["protocol_fingerprint"], "accumulation_policy_sha256": policy["policy_sha256"], "maturity_policy_id": maturity["identity"]["policy_id"], "readiness_policy_id": readiness["identity"]["policy_id"], "segment_chain_sha256": chain["chain_sha256"], "state_order": "strict", "market_segment_start": "validated_chain_next_canonical_start", "maturity_append_policy": "chronological_unique_closed_intervals"}
    constraint_values = {"current_samples": 160, "remaining_samples": 340, "sample_maturity_met": False, "state_machine_complete": False, "epoch_2_countable": False, "turnover_rows": 0, "cost_amount_rows": 0, "capacity_pass_fail_rows": 0, "return_rows": 0, "pnl_rows": 0, "historical_backfill_prohibited": True, "economic_computation_authorized": False, "profitability_evidence": False, "trading_readiness_changed": False}
    states_bytes = _csv_bytes(state_rows, STATE_FIELDS)
    next_bytes = _csv_bytes(_rows(next_values), KEY_VALUE_FIELDS)
    protocol_bytes = _csv_bytes(_rows(protocol_values), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraint_values), KEY_VALUE_FIELDS)
    identity = {"schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID, "accumulation_policy_sha256": policy["policy_sha256"], "sample_maturity_sha256": maturity["maturity_sha256"], "latest_readiness_sha256": readiness["readiness_sha256"], "segment_chain_sha256": chain["chain_sha256"], "next_epoch_ordinal": 2, "current_state": STATES[0], "governed_capture_window": {"start": _iso(window_start), "end": _iso(window_end)}, "expected_previous_snapshot_sha256": SNAPSHOT_SHA, "expected_market_segment_start": chain_start, "expected_market_segment_end_status": "awaiting_future_membership_gate", "states": list(STATES), "claims": {"first_epoch_was_not_collected_under_cadence_policy": True, "future_epoch_2_is_first_fully_policy_governed_epoch": True, "future_only_membership_evidence": True, "historical_point_in_time_membership": False, "survivorship_bias_resolved": False, "prior_related_results_exist": True, "global_preregistration": False, "profitability_evidence": False, "economic_computation_authorized": False, "trading_readiness_changed": False}, "artifacts": {"states_sha256": _digest(states_bytes), "next_epoch_sha256": _digest(next_bytes), "protocol_sha256": _digest(protocol_bytes), "constraints_sha256": _digest(constraints_bytes)}}
    assembly_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-epoch-assembly.{assembly_sha}"
    paths = {"states": output / f"{stem}.states.csv", "next_epoch": output / f"{stem}.next-epoch.csv", "protocol": output / f"{stem}.protocol.csv", "constraints": output / f"{stem}.constraints.csv", "report": output / f"{stem}.json"}
    for key, content in (("states", states_bytes), ("next_epoch", next_bytes), ("protocol", protocol_bytes), ("constraints", constraints_bytes)):
        _commit_bytes(paths[key], content)
    report = {"schema_version": SCHEMA_VERSION, "assembly_sha256": assembly_sha, "contract_status": CONTRACT_STATUS, "current_state": STATES[0], "next_epoch_ordinal": 2, "next_capture_window_start": _iso(window_start), "next_capture_window_end": _iso(window_end), "expected_previous_snapshot": SNAPSHOT_SHA, "expected_next_market_segment_start": chain_start, "market_segment_end_resolved": False, "current_samples": 160, "remaining_samples": 340, "new_samples_counted": 0, "epoch_2_countable": False, "state_machine_complete": False, "turnover_rows": 0, "cost_amount_rows": 0, "capacity_pass_fail_rows": 0, "return_rows": 0, "pnl_rows": 0, "identity": identity, "artifacts": {key: {"filename": path.name, "sha256": identity["artifacts"][f"{key}_sha256"], "row_count": len(state_rows) if key == "states" else len(_rows(next_values if key == "next_epoch" else protocol_values if key == "protocol" else constraint_values))} for key, path in paths.items() if key != "report"}, "economic_computation_authorized": False, "profitability_evidence": False, "trading_readiness_changed": False}
    report["artifacts"]["report"] = {"filename": paths["report"].name}
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return EpochAssemblyResult(report, {key: str(value) for key, value in paths.items()})


def load_assembly_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("epoch assembly config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("epoch assembly config mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_epoch_assembly_result(result: EpochAssemblyResult) -> str:
    report = result.report
    return "\n".join((f"contract_status: {report['contract_status']}", f"assembly_sha256: {report['assembly_sha256']}", f"current_state: {report['current_state']}", f"next_epoch_ordinal: {report['next_epoch_ordinal']}", f"next_capture_window_start: {report['next_capture_window_start']}", f"next_capture_window_end: {report['next_capture_window_end']}", f"expected_previous_snapshot: {report['expected_previous_snapshot']}", f"expected_next_market_segment_start: {report['expected_next_market_segment_start']}", "market_segment_end_resolved: false", "current_samples: 160", "remaining_samples: 340", "new_samples_counted: 0", "epoch_2_countable: false", "economic_computation_authorized: false", "trading_readiness_changed: false"))


def validate_epoch_assembly_state_machine(path: str | Path) -> dict[str, Any]:
    """Replay one immutable epoch-assembly marker without creating artifacts."""

    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if report_path.parent != (repo / "reports" / "prospective-epoch-assembly").resolve():
        raise MarketDataError("epoch assembly marker path escape")
    report = _load_json(report_path)
    assembly_sha = report.get("assembly_sha256")
    identity = report.get("identity")
    if (
        report.get("schema_version") != SCHEMA_VERSION
        or report.get("contract_status") != CONTRACT_STATUS
        or not isinstance(assembly_sha, str)
        or report_path.name != f"prospective-epoch-assembly.{assembly_sha}.json"
        or not isinstance(identity, dict)
        or _digest(_canonical_json_bytes(identity)) != assembly_sha
    ):
        raise MarketDataError("epoch assembly identity mismatch")
    expected_identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "accumulation_policy_sha256": POLICY_SHA,
        "sample_maturity_sha256": MATURITY_SHA,
        "latest_readiness_sha256": READINESS_SHA,
        "segment_chain_sha256": CHAIN_SHA,
        "next_epoch_ordinal": 2,
        "current_state": STATES[0],
        "expected_previous_snapshot_sha256": SNAPSHOT_SHA,
        "expected_market_segment_end_status": "awaiting_future_membership_gate",
    }
    for key, expected in expected_identity.items():
        if identity.get(key) != expected:
            raise MarketDataError(f"epoch assembly identity field mismatch:{key}")
    if (
        report.get("current_state") != STATES[0]
        or report.get("next_epoch_ordinal") != 2
        or report.get("current_samples") != 160
        or report.get("remaining_samples") != 340
        or report.get("market_segment_end_resolved") is not False
        or report.get("epoch_2_countable") is not False
        or report.get("new_samples_counted") != 0
        or report.get("economic_computation_authorized") is not False
        or report.get("profitability_evidence") is not False
        or report.get("trading_readiness_changed") is not False
    ):
        raise MarketDataError("epoch assembly state mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("epoch assembly artifacts missing")
    expected_fields = {
        "states": STATE_FIELDS,
        "next_epoch": KEY_VALUE_FIELDS,
        "protocol": KEY_VALUE_FIELDS,
        "constraints": KEY_VALUE_FIELDS,
    }
    for name, fields in expected_fields.items():
        info = artifacts.get(name)
        expected_sha = identity_artifacts.get(f"{name}_sha256")
        if not isinstance(info, dict) or info.get("sha256") != expected_sha:
            raise MarketDataError(f"epoch assembly artifact metadata:{name}")
        filename = info.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise MarketDataError(f"epoch assembly artifact filename:{name}")
        artifact_path = (report_path.parent / filename).resolve()
        if artifact_path.parent != report_path.parent or not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected_sha:
            raise MarketDataError(f"epoch assembly artifact bytes:{name}")
        with artifact_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        header = tuple(rows[0].keys()) if rows else ()
        if header != fields:
            raise MarketDataError(f"epoch assembly artifact schema:{name}")
        if info.get("row_count") != len(rows):
            raise MarketDataError(f"epoch assembly artifact count:{name}")
    return report


def _validate_inputs(policy: dict[str, Any], maturity: dict[str, Any], readiness: dict[str, Any], chain: dict[str, Any], policy_path: Path, maturity_path: Path, readiness_path: Path, chain_path: Path) -> None:
    if policy.get("policy_sha256") != POLICY_SHA or _digest(_canonical_json_bytes(policy.get("identity", {}))) != POLICY_SHA or maturity.get("maturity_sha256") != MATURITY_SHA or readiness.get("readiness_sha256") != READINESS_SHA or chain.get("chain_sha256") != CHAIN_SHA:
        raise MarketDataError("epoch assembly input identity mismatch")
    if any(path.parent.name != directory for path, directory in ((policy_path, "prospective-epoch-accumulation-policy"), (maturity_path, "prospective-economic-sample-maturity"), (readiness_path, "prospective-economic-readiness"), (chain_path, "prospective-direct-1h-segment-chain"))):
        raise MarketDataError("epoch assembly marker path mismatch")
    if policy.get("current_unique_closed_intervals") != 160 or policy.get("sample_maturity_met") is not False or maturity.get("unique_closed_interval_count") != 160 or maturity.get("sample_maturity_met") is not False or readiness.get("economic_value_computation_authorized") is not False:
        raise MarketDataError("epoch assembly maturity state mismatch")
    if maturity.get("identity", {}).get("readiness_reports", [{}])[-1].get("readiness_sha256") != readiness.get("readiness_sha256"):
        raise MarketDataError("epoch assembly latest readiness mismatch")
    if chain.get("current_chain_tail") != "2026-08-09T10:00:00Z" or chain.get("next_canonical_segment_start") != "2026-08-09T11:00:00Z" or chain.get("next_segment_end_resolved") is not False:
        raise MarketDataError("epoch assembly segment chain state mismatch")
    if policy.get("identity", {}).get("last_accepted_snapshot", {}).get("snapshot_sha256") != SNAPSHOT_SHA:
        raise MarketDataError("epoch assembly snapshot parent mismatch")


def _state_rows() -> list[dict[str, Any]]:
    parents = ("accumulation_policy", "accumulation_policy", "universe_transition", "membership_gate", "market_segment", "segment_chain", "portfolio_ledger", "economic_readiness", "sample_maturity", "sample_maturity")
    predicates = ("frozen_window_is_next_and_not_started", "first_validator_complete_capture_in_window", "previous_and_current_snapshot_parent_match", "gate_binds_transition_without_retroactive_change", "start_is_chain_next_start_and_end_is_gate_anchor", "zero_gap_zero_overlap_immutable_chain", "36_family_epoch_only_no_winner_selection", "structural_ready_without_economic_values", "chronological_unique_intervals_no_overlap", "only_unique_closed_intervals_count")
    return [{"ordinal": i + 1, "state": state, "required_parent_artifact": parents[i], "admission_predicate": predicates[i], "next_state": STATES[i + 1] if i + 1 < len(STATES) else "terminal", "skip_prohibited": True} for i, state in enumerate(STATES)]


def _marker(path: Path, directory: str, repo: Path) -> dict[str, Any]:
    if path.parent != (repo / "reports" / directory).resolve() or not path.is_file():
        raise MarketDataError("epoch assembly marker path escape")
    return _load_json(path)


def _rows(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True) if not isinstance(values[key], str) else values[key]} for key in sorted(values)]


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("epoch assembly repo root not found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("epoch assembly output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("epoch assembly timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise MarketDataError("epoch assembly timestamp missing timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("epoch assembly json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("epoch assembly json shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"epoch assembly content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".epoch-assembly-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
