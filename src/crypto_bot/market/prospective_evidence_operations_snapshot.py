from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_direct_1h_segment_append_authorization import (
    validate_authorization,
)
from crypto_bot.prospective_economic_readiness_gate import (
    validate_prospective_economic_readiness,
)
from crypto_bot.prospective_economic_sample_maturity_gate import (
    validate_prospective_economic_sample_maturity,
)
from crypto_bot.prospective_epoch_assembly_state_machine import (
    STATES as EPOCH_STATES,
    validate_epoch_assembly_state_machine,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_evidence_operations_snapshot_v1"
CONTRACT_STATUS = "verified_prospective_evidence_operations_snapshot"
DEFAULT_CONFIG_FILENAME = "config.prospective-evidence-operations-snapshot.example.yaml"
KEY_FIELDS = ("key", "value")
STATUS_FIELDS = (
    "governed_stage",
    "blocking_gate",
    "blocking_reason",
    "next_legal_action",
    "epoch_state",
    "next_epoch_ordinal",
    "capture_evidence_available",
    "membership_epoch_closed",
    "segment_candidate_available",
    "append_authorization_ready",
    "authoritative_write_authorized",
    "current_samples",
    "sample_threshold",
    "remaining_samples",
    "sample_maturity_met",
    "economic_inputs_structurally_ready",
    "economic_authorized",
    "pnl_authorized",
    "readiness_ready",
    "paper_authorized",
    "live_authorized",
    "state_changed",
    "network_activity_performed",
    "capture_performed",
    "segment_appended",
    "chain_mutation_performed",
    "new_samples_counted",
    "economic_computation_performed",
    "pnl_computation_performed",
    "paper_execution_performed",
    "live_execution_performed",
)
PARENT_KEYS = ("epoch_assembly", "sample_maturity", "append_authorization", "economic_readiness")
FALSE_INVARIANTS = {
    "state_changed": False,
    "network_activity_performed": False,
    "capture_performed": False,
    "segment_appended": False,
    "chain_mutation_performed": False,
    "new_samples_counted": 0,
    "economic_computation_performed": False,
    "pnl_computation_performed": False,
    "paper_execution_performed": False,
    "live_execution_performed": False,
}


@dataclass(frozen=True)
class OperationsSnapshotResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_operations_snapshot_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("operations snapshot config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("operations snapshot config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("operations snapshot config policy mismatch")
    required = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "sample_threshold": 500,
        "market_evidence_required_for_append": True,
        "append_authorization_required": True,
        "sample_maturity_required_for_economic": True,
        "paper_requires_readiness": True,
        "live_requires_readiness": True,
        "state_mutation_prohibited": True,
        "network_activity_prohibited": True,
        "next_action_must_be_derived": True,
        "manual_stage_override_prohibited": True,
        "manual_next_action_override_prohibited": True,
        "manual_sample_override_prohibited": True,
        "manual_authorization_override_prohibited": True,
        "manual_readiness_override_prohibited": True,
    }
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in required.items()):
        raise MarketDataError("operations snapshot config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def normalize_operations_state(
    epoch: Mapping[str, Any],
    maturity: Mapping[str, Any],
    authorization: Mapping[str, Any],
    readiness: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize already validated parent reports; this function never writes or mutates state."""

    threshold = policy.get("sample_threshold")
    current_samples = maturity.get("unique_closed_interval_count")
    remaining_samples = maturity.get("remaining_closed_interval_count")
    if threshold != 500 or current_samples != 160 or remaining_samples != 340:
        raise MarketDataError("operations snapshot sample state mismatch")
    if authorization.get("sample_threshold") != threshold or authorization.get("current_samples") != current_samples or authorization.get("remaining_samples") != remaining_samples:
        raise MarketDataError("operations snapshot parent sample mismatch")
    epoch_state = epoch.get("current_state")
    if epoch_state not in EPOCH_STATES:
        raise MarketDataError("operations snapshot unknown epoch state")
    market_evidence = authorization.get("validated_market_evidence") is True
    candidate_available = market_evidence and authorization.get("candidate_lineage_match") is True
    append_ready = authorization.get("append_authorization_eligible") is True and authorization.get("authoritative_write_authorized") is True
    membership_closed = epoch_state in EPOCH_STATES[3:] and epoch.get("market_segment_end_resolved") is True
    maturity_met = maturity.get("sample_maturity_met") is True
    economic_authorized = (
        maturity.get("prospective_economic_evaluation_authorizable") is True
        and readiness.get("economic_value_computation_authorized") is True
    )
    pnl_authorized = maturity.get("pnl_computation_authorized") is True and readiness.get("pnl_computation_authorized") is True
    readiness_ready = readiness.get("trading_readiness_changed") is True
    normalized = {
        "epoch_state": epoch_state,
        "next_epoch_ordinal": epoch.get("next_epoch_ordinal"),
        "capture_evidence_available": market_evidence,
        "membership_epoch_closed": membership_closed,
        "segment_candidate_available": candidate_available,
        "append_authorization_ready": append_ready,
        "authoritative_write_authorized": authorization.get("authoritative_write_authorized") is True,
        "current_samples": current_samples,
        "sample_threshold": threshold,
        "remaining_samples": remaining_samples,
        "sample_maturity_met": maturity_met,
        "economic_inputs_structurally_ready": readiness.get("prospective_economic_inputs_structurally_ready") is True,
        "economic_authorized": economic_authorized,
        "pnl_authorized": pnl_authorized,
        "readiness_ready": readiness_ready,
        # There is no paper/live authorization parent in this repository. Keep both fail-closed.
        "paper_authorized": False,
        "live_authorized": False,
        "authorization_status": authorization.get("status"),
        "authorization_materialized": authorization.get("authorization_materialized"),
        "economic_source_authorized": readiness.get("economic_value_computation_authorized"),
    }
    return normalized


def reduce_operations_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Derive one governed stage, blocker, and next legal action from normalized state."""

    checks = (
        (not state["membership_epoch_closed"], "awaiting_real_membership_epoch_progress", "membership_epoch_progress", "epoch_state=" + str(state["epoch_state"]), "await_real_membership_epoch_progress"),
        (not state["capture_evidence_available"], "awaiting_validated_market_evidence", "market_evidence", "validated market evidence is unavailable", "await_validated_market_evidence"),
        (not state["append_authorization_ready"], "awaiting_append_authorization", "append_authorization", "append authorization is not ready", "await_append_authorization"),
        (not state["sample_maturity_met"], "awaiting_sample_maturity", "sample_maturity", "closed interval sample threshold is unmet", "await_sample_maturity"),
        (not state["economic_authorized"], "awaiting_economic_authorization", "economic_authorization", "economic computation is not authorized", "await_economic_authorization"),
        (not state["pnl_authorized"], "awaiting_pnl_authorization", "pnl_authorization", "PnL computation is not authorized", "await_pnl_authorization"),
        (not state["readiness_ready"], "awaiting_trading_readiness", "trading_readiness", "trading readiness has not changed to ready", "await_trading_readiness"),
        (not state["paper_authorized"], "awaiting_explicit_paper_authorization", "paper_authorization", "paper execution has no explicit authorization parent", "await_explicit_paper_authorization"),
        (not state["live_authorized"], "awaiting_explicit_live_authorization", "live_authorization", "live execution has no explicit authorization parent", "await_explicit_live_authorization"),
    )
    for blocked, stage, gate, reason, action in checks:
        if blocked:
            return {"governed_stage": stage, "blocking_gate": gate, "blocking_reason": reason, "next_legal_action": action}
    return {"governed_stage": "fully_authorized", "blocking_gate": "none", "blocking_reason": "all governed gates are satisfied", "next_legal_action": "no_legal_action"}


def build_prospective_evidence_operations_snapshot(
    epoch_assembly: str | Path,
    sample_maturity: str | Path,
    append_authorization: str | Path,
    economic_readiness: str | Path,
    config: str | Path,
    output_dir: str | Path,
) -> OperationsSnapshotResult:
    epoch_path = Path(epoch_assembly).resolve()
    repo = _repo_root(epoch_path)
    policy = load_operations_snapshot_config(config, repo)
    parents = _validate_parents(epoch_path, sample_maturity, append_authorization, economic_readiness)
    normalized = normalize_operations_state(*parents[:4], policy)
    projection = reduce_operations_state(normalized)
    parent_dirs: tuple[str, str, str, str] = (
        Path(epoch_assembly).resolve().parent.name,
        Path(sample_maturity).resolve().parent.name,
        Path(append_authorization).resolve().parent.name,
        Path(economic_readiness).resolve().parent.name,
    )
    return _write_snapshot(repo, policy, parents, normalized, projection, parent_dirs, output_dir)


def validate_prospective_evidence_operations_snapshot(path: str | Path) -> dict[str, Any]:
    """Replay a snapshot and reject any manually changed projection or parent state."""

    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if report_path.parent != (repo / "reports" / "prospective-evidence-operations-snapshot").resolve():
        raise MarketDataError("operations snapshot marker path escape")
    report = _load_json(report_path)
    snapshot_sha = report.get("snapshot_sha256")
    identity = report.get("identity")
    if (
        report.get("schema_version") != SCHEMA_VERSION
        or report.get("contract_status") != CONTRACT_STATUS
        or not isinstance(snapshot_sha, str)
        or report_path.name != f"prospective-evidence-operations-snapshot.{snapshot_sha}.json"
        or not isinstance(identity, dict)
        or _digest(_canonical(identity)) != snapshot_sha
    ):
        raise MarketDataError("operations snapshot identity mismatch")
    policy = load_operations_snapshot_config(repo / DEFAULT_CONFIG_FILENAME, repo)
    if identity.get("policy_id") != POLICY_ID or identity.get("policy") != policy:
        raise MarketDataError("operations snapshot policy mismatch")
    parent_files = identity.get("parent_reports")
    if not isinstance(parent_files, dict) or any(key not in parent_files for key in PARENT_KEYS):
        raise MarketDataError("operations snapshot parent reports missing")
    parent_paths = tuple(_resolve_parent(repo, parent_files[key], key) for key in PARENT_KEYS)
    parents = _validate_parents(*parent_paths)
    normalized = normalize_operations_state(*parents[:4], policy)
    projection = reduce_operations_state(normalized)
    expected = {**normalized, **projection, **FALSE_INVARIANTS}
    for field in STATUS_FIELDS:
        if report.get(field) != expected.get(field):
            raise MarketDataError(f"operations snapshot replay mismatch:{field}")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("operations snapshot artifacts missing")
    for name, fields, count in (("status", STATUS_FIELDS, 1), ("dependencies", KEY_FIELDS, len(PARENT_KEYS)), ("constraints", KEY_FIELDS, len(FALSE_INVARIANTS) + 1)):
        _validate_artifact(report_path, artifacts, identity_artifacts, name, fields, count)
    return report


def format_operations_snapshot(result: OperationsSnapshotResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"snapshot_sha256: {report['snapshot_sha256']}",
            f"governed_stage: {report['governed_stage']}",
            f"blocking_gate: {report['blocking_gate']}",
            f"next_legal_action: {report['next_legal_action']}",
            f"current_samples: {report['current_samples']}",
            f"sample_threshold: {report['sample_threshold']}",
            f"remaining_samples: {report['remaining_samples']}",
            f"append_authorization_ready: {str(report['append_authorization_ready']).lower()}",
            f"sample_maturity_met: {str(report['sample_maturity_met']).lower()}",
            f"economic_authorized: {str(report['economic_authorized']).lower()}",
            f"pnl_authorized: {str(report['pnl_authorized']).lower()}",
            f"paper_authorized: {str(report['paper_authorized']).lower()}",
            f"live_authorized: {str(report['live_authorized']).lower()}",
            "state_changed: false",
        )
    )


def _validate_parents(*paths: str | Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    if len(paths) != 4:
        raise ValueError("exactly four operations snapshot parents are required")
    epoch = validate_epoch_assembly_state_machine(paths[0])
    maturity = validate_prospective_economic_sample_maturity(paths[1])
    authorization = validate_authorization(paths[2])
    readiness = validate_prospective_economic_readiness(paths[3])
    return epoch, maturity, authorization, readiness


def _write_snapshot(
    repo: Path,
    policy: Mapping[str, Any],
    parents: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
    normalized: Mapping[str, Any],
    projection: Mapping[str, Any],
    parent_dirs: tuple[str, str, str, str],
    output_dir: str | Path,
) -> OperationsSnapshotResult:
    output = _reports_output(repo, output_dir)
    state = {**normalized, **projection, **FALSE_INVARIANTS}
    parent_names = (
        "prospective-epoch-assembly",
        "prospective-economic-sample-maturity",
        "prospective-direct-1h-segment-append-authorization",
        "prospective-economic-readiness",
    )
    parent_ids = ("assembly_sha256", "maturity_sha256", "authorization_sha256", "readiness_sha256")
    parent_reports = {key: {"directory": directory, "filename": f"{name}.{parent.get(identity)}.json", "sha256": parent.get(identity)} for key, directory, name, parent, identity in zip(PARENT_KEYS, parent_dirs, parent_names, parents, parent_ids, strict=True)}
    status_row = {field: state.get(field) for field in STATUS_FIELDS}
    dependencies = [{"key": key, "value": json.dumps(parent_reports[key], ensure_ascii=False, sort_keys=True)} for key in PARENT_KEYS]
    constraints = [{"key": key, "value": json.dumps(value, ensure_ascii=False, sort_keys=True)} for key, value in sorted({**FALSE_INVARIANTS, "policy_id": POLICY_ID}.items())]
    status_bytes = _csv_bytes([status_row], STATUS_FIELDS)
    dependency_bytes = _csv_bytes(dependencies, KEY_FIELDS)
    constraint_bytes = _csv_bytes(constraints, KEY_FIELDS)
    artifact_hashes = {"status_sha256": _digest(status_bytes), "dependencies_sha256": _digest(dependency_bytes), "constraints_sha256": _digest(constraint_bytes)}
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "policy": dict(policy),
        "parent_reports": parent_reports,
        "normalized_state": dict(normalized),
        "projection": dict(projection),
        "invariants": dict(FALSE_INVARIANTS),
        "artifacts": artifact_hashes,
    }
    snapshot_sha = _digest(_canonical(identity))
    stem = f"prospective-evidence-operations-snapshot.{snapshot_sha}"
    paths = {
        "status": output / f"{stem}.status.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    for key, content in (("status", status_bytes), ("dependencies", dependency_bytes), ("constraints", constraint_bytes)):
        _commit_bytes(paths[key], content)
    report = {"schema_version": SCHEMA_VERSION, "contract_status": CONTRACT_STATUS, "snapshot_sha256": snapshot_sha, **state, "identity": identity, "artifacts": {"status": {"filename": paths["status"].name, "sha256": artifact_hashes["status_sha256"], "row_count": 1}, "dependencies": {"filename": paths["dependencies"].name, "sha256": artifact_hashes["dependencies_sha256"], "row_count": len(dependencies)}, "constraints": {"filename": paths["constraints"].name, "sha256": artifact_hashes["constraints_sha256"], "row_count": len(constraints)}, "report": {"filename": paths["report"].name}}}
    _commit_bytes(paths["report"], _pretty(report))
    return OperationsSnapshotResult(report, {key: str(value) for key, value in paths.items()})


def _validate_artifact(report_path: Path, artifacts: Mapping[str, Any], identity_artifacts: Mapping[str, Any], name: str, fields: tuple[str, ...], count: int) -> None:
    info = artifacts.get(name)
    expected = identity_artifacts.get(f"{name}_sha256")
    if not isinstance(info, dict) or info.get("sha256") != expected:
        raise MarketDataError(f"operations snapshot artifact metadata:{name}")
    filename = info.get("filename")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise MarketDataError(f"operations snapshot artifact filename:{name}")
    artifact_path = (report_path.parent / filename).resolve()
    if artifact_path.parent != report_path.parent or not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected:
        raise MarketDataError(f"operations snapshot artifact bytes:{name}")
    with artifact_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        header = tuple(reader.fieldnames or ())
    if header != fields or len(rows) != count or info.get("row_count") != len(rows):
        raise MarketDataError(f"operations snapshot artifact schema:{name}")


def _resolve_parent(repo: Path, value: Any, key: str) -> Path:
    if not isinstance(value, dict) or not isinstance(value.get("filename"), str) or Path(value["filename"]).name != value["filename"]:
        raise MarketDataError(f"operations snapshot parent filename:{key}")
    directories = {"epoch_assembly": "prospective-epoch-assembly", "sample_maturity": "prospective-economic-sample-maturity", "append_authorization": "prospective-direct-1h-segment-append-authorization", "economic_readiness": "prospective-economic-readiness"}
    directory = value.get("directory")
    expected_directory = directories[key]
    allowed_directories = {expected_directory}
    if key == "append_authorization":
        allowed_directories.add(expected_directory + "-smoke")
    if not isinstance(directory, str) or Path(directory).name != directory or directory not in allowed_directories:
        raise MarketDataError(f"operations snapshot parent directory:{key}")
    path = (repo / "reports" / directory / value["filename"]).resolve()
    if not path.is_relative_to((repo / "reports" / directory).resolve()):
        raise MarketDataError(f"operations snapshot parent path escape:{key}")
    if not path.is_file() or value.get("sha256") not in path.name:
        raise MarketDataError(f"operations snapshot parent identity:{key}")
    return path


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("operations snapshot output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("operations snapshot repo root not found")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("operations snapshot json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("operations snapshot json shape mismatch")
    return value


def _csv_bytes(rows: Sequence[Mapping[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row.get(field)) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"operations snapshot content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".operations-snapshot-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
