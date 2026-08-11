from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_membership_bar_gate import (
    _ceil_hour,
    _floor_strict_hour,
    _iso,
    _parse_iso,
)
from crypto_bot.market.prospective_membership_epoch_materializer import (
    MEMBERSHIP_FIELDS,
    validate_prospective_membership_epoch_materialization,
)
from crypto_bot.market.prospective_snapshot_transition_materializer import (
    CONTRACT_STATUS as TRANSITION_STATUS,
    validate_prospective_snapshot_transition_materialization,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_membership_epoch_closure_v1"
CONTRACT_STATUS = "verified_prospective_membership_epoch_closure"
DEFAULT_CONFIG_FILENAME = "config.prospective-membership-epoch-closure.example.yaml"
TIMING_FIELDS = (
    "epoch_id",
    "source_transition_identity",
    "current_snapshot_received_at",
    "membership_effective_at",
    "epoch_end_received_at",
    "first_signal_timestamp",
    "first_completion_timestamp",
    "first_execution_timestamp",
    "last_signal_timestamp",
    "last_completion_timestamp",
    "last_execution_timestamp",
    "epoch_end_resolved",
    "closed_interval_count",
    "status",
)
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectiveMembershipEpochClosureResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def close_prospective_membership_epoch(
    open_membership_epoch: str | Path,
    next_snapshot_transition: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveMembershipEpochClosureResult:
    open_path = Path(open_membership_epoch).resolve()
    repo = _repo_root(open_path)
    config = load_membership_epoch_closure_config(config_path, repo)
    state = _derive_state(open_path, Path(next_snapshot_transition), config, repo)
    membership_bytes = _csv_bytes(state["membership_rows"], MEMBERSHIP_FIELDS)
    timing_bytes = _csv_bytes(state["timing_rows"], TIMING_FIELDS)
    dependencies = _dependency_values(state)
    constraints = _constraint_values(state)
    dependencies_bytes = _csv_bytes(_rows(dependencies), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraints), KEY_VALUE_FIELDS)
    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "open_membership_epoch_sha256": state["open_epoch_sha256"],
        "next_transition_sha256": state["next_transition_sha256"],
        "epoch_id": state["epoch_id"],
        "membership_epoch_closed": state["membership_epoch_closed"],
        "status": state["status"],
        "current_snapshot_received_at": state["current_snapshot_received_at"],
        "membership_effective_at": state["membership_effective_at"],
        "epoch_end_received_at": state["epoch_end_received_at"],
        "first_signal_timestamp": state["first_signal_timestamp"],
        "last_signal_timestamp": state["last_signal_timestamp"],
        "last_execution_timestamp": state["last_execution_timestamp"],
        "closed_interval_count": state["closed_interval_count"],
        "policy": config,
        "claims": _claims(),
        "artifacts": {
            "membership_sha256": _digest(membership_bytes),
            "timing_sha256": _digest(timing_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    closure_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-membership-epoch-closure.{closure_sha}"
    paths = {
        "membership": output / f"{stem}.membership.csv",
        "timing": output / f"{stem}.timing.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "closure_sha256": closure_sha,
        "contract_status": CONTRACT_STATUS,
        "epoch_id": state["epoch_id"],
        "status": state["status"],
        "membership_epoch_closed": state["membership_epoch_closed"],
        "open_membership_epoch_sha256": state["open_epoch_sha256"],
        "next_transition_sha256": state["next_transition_sha256"],
        "current_snapshot_received_at": state["current_snapshot_received_at"],
        "membership_effective_at": state["membership_effective_at"],
        "epoch_end_received_at": state["epoch_end_received_at"],
        "first_signal_timestamp": state["first_signal_timestamp"],
        "last_signal_timestamp": state["last_signal_timestamp"],
        "last_execution_timestamp": state["last_execution_timestamp"],
        "epoch_end_resolved": state["epoch_end_resolved"],
        "membership_rows": len(state["membership_rows"]),
        "timing_rows": len(state["timing_rows"]),
        "closed_interval_count": state["closed_interval_count"],
        "sample_credit": 0,
        "current_samples": 160,
        "new_samples_counted": 0,
        "remaining_samples": 340,
        "future_only": True,
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "historical_backfill_prohibited": True,
        "retroactive_membership_change_prohibited": True,
        "closed_epoch_membership_immutable": True,
        "automatic_replacement_prohibited": True,
        "next_transition_changes_apply_to_next_epoch_only": True,
        "network_activity_performed": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "identity": identity,
        "artifacts": {
            "membership": {"filename": paths["membership"].name, "sha256": identity["artifacts"]["membership_sha256"], "row_count": len(state["membership_rows"])},
            "timing": {"filename": paths["timing"].name, "sha256": identity["artifacts"]["timing_sha256"], "row_count": len(state["timing_rows"])},
            "dependencies": {"filename": paths["dependencies"].name, "sha256": identity["artifacts"]["dependencies_sha256"], "row_count": len(dependencies)},
            "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)},
            "report": {"filename": paths["report"].name},
        },
    }
    for key, content in (("membership", membership_bytes), ("timing", timing_bytes), ("dependencies", dependencies_bytes), ("constraints", constraints_bytes)):
        _commit_bytes(paths[key], content)
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveMembershipEpochClosureResult(report, {key: str(value) for key, value in paths.items()})


def validate_prospective_membership_epoch_closure(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if not _report_dir_allowed(report_path, repo):
        raise MarketDataError("membership epoch closure report path escape")
    report = _load_json(report_path)
    closure_sha = report.get("closure_sha256")
    identity = report.get("identity")
    if not isinstance(closure_sha, str) or report_path.name != f"prospective-membership-epoch-closure.{closure_sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != closure_sha:
        raise MarketDataError("membership epoch closure identity mismatch")
    if report.get("contract_status") != CONTRACT_STATUS:
        raise MarketDataError("membership epoch closure contract status mismatch")
    config = identity.get("policy")
    if not isinstance(config, dict) or config != load_membership_epoch_closure_config(repo / DEFAULT_CONFIG_FILENAME, repo):
        raise MarketDataError("membership epoch closure policy mismatch")
    state = _derive_state(
        _locate_open_epoch(repo, str(identity.get("open_membership_epoch_sha256"))),
        _locate_transition(repo, str(identity.get("next_transition_sha256"))),
        config,
        repo,
    )
    expected = {
        "epoch_id": state["epoch_id"],
        "status": state["status"],
        "membership_epoch_closed": state["membership_epoch_closed"],
        "open_membership_epoch_sha256": state["open_epoch_sha256"],
        "next_transition_sha256": state["next_transition_sha256"],
        "current_snapshot_received_at": state["current_snapshot_received_at"],
        "membership_effective_at": state["membership_effective_at"],
        "epoch_end_received_at": state["epoch_end_received_at"],
        "first_signal_timestamp": state["first_signal_timestamp"],
        "last_signal_timestamp": state["last_signal_timestamp"],
        "last_execution_timestamp": state["last_execution_timestamp"],
        "epoch_end_resolved": state["epoch_end_resolved"],
        "membership_rows": len(state["membership_rows"]),
        "timing_rows": len(state["timing_rows"]),
        "closed_interval_count": state["closed_interval_count"],
        "sample_credit": 0,
        "current_samples": 160,
        "new_samples_counted": 0,
        "remaining_samples": 340,
        "future_only": True,
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "historical_backfill_prohibited": True,
        "retroactive_membership_change_prohibited": True,
        "closed_epoch_membership_immutable": True,
        "automatic_replacement_prohibited": True,
        "next_transition_changes_apply_to_next_epoch_only": True,
        "network_activity_performed": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise MarketDataError(f"membership epoch closure report mismatch:{key}")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("membership epoch closure artifacts shape mismatch")
    for key, fields, rows in (("membership", MEMBERSHIP_FIELDS, state["membership_rows"]), ("timing", TIMING_FIELDS, state["timing_rows"])):
        info = artifacts.get(key)
        if not isinstance(info, dict) or info.get("sha256") != identity_artifacts.get(f"{key}_sha256"):
            raise MarketDataError(f"membership epoch closure artifact metadata mismatch:{key}")
        artifact = report_path.parent / str(info.get("filename", ""))
        if not artifact.is_file() or _digest(artifact.read_bytes()) != info.get("sha256"):
            raise MarketDataError(f"membership epoch closure artifact bytes mismatch:{key}")
        actual = _read_csv(artifact, fields)
        expected_rows = [{field: _csv_value(row.get(field)) for field in fields} for row in rows]
        if actual != expected_rows or len(actual) != info.get("row_count"):
            raise MarketDataError(f"membership epoch closure {key} artifact mismatch")
    for key, expected_values in (("dependencies", _dependency_values(state)), ("constraints", _constraint_values(state))):
        info = artifacts.get(key)
        if not isinstance(info, dict) or info.get("sha256") != identity_artifacts.get(f"{key}_sha256"):
            raise MarketDataError(f"membership epoch closure artifact metadata mismatch:{key}")
        artifact = report_path.parent / str(info.get("filename", ""))
        if not artifact.is_file() or _digest(artifact.read_bytes()) != info.get("sha256") or _read_key_values(artifact) != {k: _csv_value(v) for k, v in expected_values.items()}:
            raise MarketDataError(f"membership epoch closure {key} artifact mismatch")
    return report


def load_membership_epoch_closure_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("membership epoch closure config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("membership epoch closure config is invalid") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("membership epoch closure config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("membership epoch closure config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_prospective_membership_epoch_closure(result: ProspectiveMembershipEpochClosureResult) -> str:
    report = result.report
    return "\n".join((
        f"contract_status: {report['contract_status']}",
        f"closure_sha256: {report['closure_sha256']}",
        f"status: {report['status']}",
        f"membership_epoch_closed: {str(report['membership_epoch_closed']).lower()}",
        f"epoch_id: {report['epoch_id']}",
        f"first_signal_timestamp: {report['first_signal_timestamp']}",
        f"last_signal_timestamp: {report['last_signal_timestamp']}",
        f"last_execution_timestamp: {report['last_execution_timestamp']}",
        f"epoch_end_resolved: {str(report['epoch_end_resolved']).lower()}",
        f"closed_interval_count: {report['closed_interval_count']}",
        "sample_credit: 0",
        "current_samples: 160",
        "new_samples_counted: 0",
        "remaining_samples: 340",
        "network_activity_performed: false",
        "economic_computation_authorized: false",
        "readiness_changed: false",
    ))


def _derive_state(open_path: Path, transition_path: Path, config: Mapping[str, Any], repo: Path) -> dict[str, Any]:
    _validate_policy(config)
    open_report = _validated_open(open_path, repo)
    transition = _validated_transition(transition_path, repo)
    open_sha = str(open_report["membership_epoch_sha256"])
    next_sha = str(transition["transition_sha256"])
    if open_report.get("membership_epoch_materialized") is not True or open_report.get("status") != "open_pending_future_close":
        timing = [_blocked_timing(next_sha, "blocked_no_open_membership_epoch")]
        return _state_base(open_report, transition, open_sha, next_sha, False, "blocked_no_open_membership_epoch", None, None, None, None, None, None, None, 0, [], timing)
    if transition.get("transition_materialized") is not True:
        timing = [_blocked_timing(next_sha, "blocked_next_transition_not_materialized")]
        return _state_base(open_report, transition, open_sha, next_sha, False, "blocked_next_transition_not_materialized", open_report.get("current_snapshot_identity"), None, open_report.get("current_snapshot_received_at"), None, None, None, None, 0, [], timing)
    current_identity = open_report.get("current_snapshot_identity")
    if transition.get("previous_snapshot_identity") != current_identity:
        raise MarketDataError("membership epoch closure transition lineage mismatch")
    current_received = _parse_iso(str(open_report.get("current_snapshot_received_at")))
    next_received = _parse_iso(str(transition.get("current_received_at")))
    if next_received <= current_received:
        raise MarketDataError("membership epoch closure snapshot ordering mismatch")
    effective = _parse_iso(str(open_report.get("membership_effective_at")))
    if effective < _ceil_hour(current_received):
        raise MarketDataError("membership epoch closure effective start mismatch")
    last_execution = _floor_strict_hour(next_received)
    last_signal = last_execution - timedelta(hours=2)
    first_signal = effective
    if last_signal < first_signal:
        raise MarketDataError("membership epoch closure empty interval")
    count = int((last_signal - first_signal).total_seconds() // 3600) + 1
    open_membership = _membership_rows(open_path, open_report)
    timing = [{"epoch_id": str(open_report["epoch_id"]), "source_transition_identity": next_sha, "current_snapshot_received_at": _iso(current_received), "membership_effective_at": _iso(effective), "epoch_end_received_at": _iso(next_received), "first_signal_timestamp": _iso(first_signal), "first_completion_timestamp": _iso(first_signal + timedelta(hours=1)), "first_execution_timestamp": _iso(first_signal + timedelta(hours=2)), "last_signal_timestamp": _iso(last_signal), "last_completion_timestamp": _iso(last_signal + timedelta(hours=1)), "last_execution_timestamp": _iso(last_execution), "epoch_end_resolved": True, "closed_interval_count": count, "status": "closed_future_epoch"}]
    return _state_base(open_report, transition, open_sha, next_sha, True, "closed_future_epoch", current_identity, transition.get("current_snapshot_identity"), _iso(current_received), _iso(effective), _iso(next_received), _iso(first_signal), _iso(last_signal), count, open_membership, timing)


def _state_base(open_report: Mapping[str, Any], transition: Mapping[str, Any], open_sha: str, next_sha: str, closed: bool, status: str, current_identity: str | None, next_identity: str | None, current_received: str | None, effective: str | None, end_received: str | None, first_signal: str | None, last_signal: str | None, count: int, membership: list[dict[str, Any]], timing: list[dict[str, Any]]) -> dict[str, Any]:
    return {"open_epoch_sha256": open_sha, "next_transition_sha256": next_sha, "epoch_id": str(open_report.get("epoch_id", "epoch-0002")), "membership_epoch_closed": closed, "status": status, "current_snapshot_received_at": current_received, "membership_effective_at": effective, "epoch_end_received_at": end_received, "first_signal_timestamp": first_signal, "last_signal_timestamp": last_signal, "last_execution_timestamp": (timing[0].get("last_execution_timestamp") if timing else None), "epoch_end_resolved": closed, "closed_interval_count": count, "membership_rows": membership, "timing_rows": timing, "current_snapshot_identity": current_identity, "next_snapshot_identity": next_identity}


def _blocked_timing(next_sha: str, status: str) -> dict[str, Any]:
    return {"epoch_id": "epoch-0002", "source_transition_identity": next_sha, "current_snapshot_received_at": "", "membership_effective_at": "", "epoch_end_received_at": "", "first_signal_timestamp": "", "first_completion_timestamp": "", "first_execution_timestamp": "", "last_signal_timestamp": "", "last_completion_timestamp": "", "last_execution_timestamp": "", "epoch_end_resolved": False, "closed_interval_count": 0, "status": status}


def _validated_open(path: Path, repo: Path) -> dict[str, Any]:
    if not path.is_relative_to((repo / "reports").resolve()) or not path.name.startswith("prospective-membership-epoch."):
        raise MarketDataError("membership epoch closure open path escape")
    return validate_prospective_membership_epoch_materialization(path)


def _validated_transition(path: Path, repo: Path) -> dict[str, Any]:
    if not path.is_relative_to((repo / "reports").resolve()) or not path.name.startswith("prospective-snapshot-transition."):
        raise MarketDataError("membership epoch closure transition path escape")
    return validate_prospective_snapshot_transition_materialization(path)


def _membership_rows(path: Path, report: Mapping[str, Any]) -> list[dict[str, Any]]:
    info = report.get("artifacts", {}).get("membership")
    if not isinstance(info, dict):
        raise MarketDataError("membership epoch closure open membership artifact missing")
    return _read_csv(path.parent / str(info["filename"]), MEMBERSHIP_FIELDS)


def _validate_policy(config: Mapping[str, Any]) -> None:
    expected = {"schema_version": 1, "policy_id": POLICY_ID, "required_open_epoch_status": "open_pending_future_close", "required_transition_status": TRANSITION_STATUS, "timeframe": "1h", "signal_completion_delay_bars": 1, "execution_offset_bars": 2, "strict_execution_before_next_snapshot": True, "manual_epoch_boundary_override_prohibited": True, "manual_timing_override_prohibited": True, "closed_epoch_membership_immutable": True, "next_transition_changes_apply_to_next_epoch_only": True, "historical_backfill_prohibited": True, "retroactive_membership_change_prohibited": True, "automatic_replacement": False, "sample_threshold": 500, "sample_credit_authorized": False, "new_samples_counted": 0, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}
    if dict(config) != expected:
        raise MarketDataError("membership epoch closure policy semantics mismatch")


def _dependency_values(state: Mapping[str, Any]) -> dict[str, str]:
    return {"open_membership_epoch_sha256": str(state["open_epoch_sha256"]), "next_transition_sha256": str(state["next_transition_sha256"]), "epoch_id": str(state["epoch_id"]), "current_snapshot_received_at": str(state["current_snapshot_received_at"] or ""), "epoch_end_received_at": str(state["epoch_end_received_at"] or ""), "last_execution_timestamp": str(state["last_execution_timestamp"] or "")}


def _constraint_values(state: Mapping[str, Any]) -> dict[str, Any]:
    return {"membership_epoch_closed": state["membership_epoch_closed"], "status": state["status"], "epoch_end_resolved": state["epoch_end_resolved"], "closed_interval_count": state["closed_interval_count"], "sample_credit": 0, "current_samples": 160, "new_samples_counted": 0, "remaining_samples": 340, "future_only": True, "historical_point_in_time_membership": False, "survivorship_bias_resolved": False, "historical_backfill_prohibited": True, "retroactive_membership_change_prohibited": True, "closed_epoch_membership_immutable": True, "automatic_replacement_prohibited": True, "next_transition_changes_apply_to_next_epoch_only": True, "network_activity_performed": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}


def _claims() -> dict[str, Any]:
    return {"future_only": True, "closure_boundary_from_next_transition": True, "strict_execution_before_next_snapshot": True, "closed_epoch_membership_immutable": True, "next_transition_changes_apply_to_next_epoch_only": True, "sample_credit_authorized": False, "historical_backfill_prohibited": True, "retroactive_membership_change_prohibited": True, "profitability_evidence": False, "economic_computation_authorized": False, "readiness_changed": False}


def _locate_open_epoch(repo: Path, sha: str) -> Path:
    if not _is_sha256(sha):
        raise MarketDataError("membership epoch closure open reference mismatch")
    name = f"prospective-membership-epoch.{sha}.json"
    matches = [p for p in (repo / "reports").rglob(name) if p.is_file()]
    if len(matches) != 1:
        raise MarketDataError("membership epoch closure open reference ambiguous")
    return matches[0]


def _locate_transition(repo: Path, sha: str) -> Path:
    if not _is_sha256(sha):
        raise MarketDataError("membership epoch closure transition reference mismatch")
    name = f"prospective-snapshot-transition.{sha}.json"
    for candidate in (repo / "reports").rglob(name):
        try:
            validate_prospective_snapshot_transition_materialization(candidate)
        except (FileNotFoundError, OSError, ValueError, MarketDataError):
            continue
        return candidate
    raise MarketDataError("membership epoch closure transition reference ambiguous")


def _report_dir_allowed(path: Path, repo: Path) -> bool:
    return path.is_relative_to((repo / "reports").resolve()) and path.name.startswith("prospective-membership-epoch-closure.")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("membership epoch closure output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fields):
                raise MarketDataError("membership epoch closure CSV schema mismatch")
            return list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("membership epoch closure CSV read failed") from exc


def _read_key_values(path: Path) -> dict[str, str]:
    rows = _read_csv(path, KEY_VALUE_FIELDS)
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows):
        raise MarketDataError("membership epoch closure key collision")
    return values


def _rows(values: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True) if not isinstance(values[key], str) else values[key]} for key in sorted(values)]


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows({field: _csv_value(row.get(field)) for field in fields} for row in rows)
    return buffer.getvalue().encode("utf-8")


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("membership epoch closure JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("membership epoch closure JSON shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"membership epoch closure content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".membership-epoch-closure-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("membership epoch closure repo root not found")
