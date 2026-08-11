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
from typing import Any, Mapping

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market import prospective_snapshot_transition_admission as admission_contract
from crypto_bot.market.okx_future_universe_archive import (
    CHANGE_FIELDS,
    DEFAULT_CONFIG_FILENAME as ARCHIVE_CONFIG_FILENAME,
    FutureUniverseSnapshot,
    POLICY_FIELDS,
    TRACKED_FIELDS,
    build_future_universe_transition_rows,
    load_future_universe_archive_config,
    validate_future_universe_snapshot,
)
from crypto_bot.market.prospective_snapshot_transition_admission import (
    validate_prospective_snapshot_transition_admission,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_snapshot_transition_materializer_v1"
CONTRACT_STATUS = "verified_prospective_snapshot_transition_materialization"
DEFAULT_CONFIG_FILENAME = "config.prospective-snapshot-transition-materializer.example.yaml"
TRANSITION_FIELDS = ("key", "value")
WINDOW_START = "2026-08-16T10:00:00Z"
WINDOW_END = "2026-08-16T11:00:00Z"


@dataclass(frozen=True)
class ProspectiveSnapshotTransitionMaterializationResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def materialize_prospective_snapshot_transition(
    transition_admission: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveSnapshotTransitionMaterializationResult:
    admission_path = Path(transition_admission).resolve()
    repo = _repo_root(admission_path)
    config = load_snapshot_transition_materializer_config(config_path, repo)
    admission = validate_prospective_snapshot_transition_admission(admission_path)
    archive_config = load_future_universe_archive_config(repo / ARCHIVE_CONFIG_FILENAME, repo)
    state = _derive_materialization(admission, repo, archive_config, config)
    changes, tracked, policy_rows = state["changes"], state["tracked"], state["policy_rows"]
    changes_bytes = _csv_bytes(changes, CHANGE_FIELDS)
    tracked_bytes = _csv_bytes(tracked, TRACKED_FIELDS)
    policy_bytes = _csv_bytes(policy_rows, POLICY_FIELDS)
    dependencies = _dependency_values(admission, state)
    constraints = _constraint_values(state)
    dependencies_bytes = _csv_bytes(_rows(dependencies), TRANSITION_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraints), TRANSITION_FIELDS)
    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "admission_sha256": admission["admission_sha256"],
        "admission_report_filename": admission_path.name,
        "epoch_ordinal": 2,
        "closeout_state": admission["closeout_state"],
        "rollover_action": admission["rollover_action"],
        "previous_snapshot_identity": state["previous_snapshot_identity"],
        "current_snapshot_identity": state["current_snapshot_identity"],
        "previous_received_at": state["previous_received_at"],
        "current_received_at": state["current_received_at"],
        "transition_materialized": state["transition_materialized"],
        "policy": config,
        "claims": _claims(),
        "artifacts": {
            "changes_sha256": _digest(changes_bytes),
            "tracked_sha256": _digest(tracked_bytes),
            "policy_sha256": _digest(policy_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    transition_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-snapshot-transition.{transition_sha}"
    paths = {
        "changes": output / f"{stem}.changes.csv",
        "tracked": output / f"{stem}.tracked-assets.csv",
        "policy": output / f"{stem}.policy.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "transition_sha256": transition_sha,
        "contract_status": CONTRACT_STATUS,
        "epoch_ordinal": 2,
        "closeout_state": admission["closeout_state"],
        "rollover_action": admission["rollover_action"],
        "transition_admission_status": admission["transition_admission_status"],
        "transition_materialized": state["transition_materialized"],
        "previous_snapshot_identity": state["previous_snapshot_identity"],
        "current_snapshot_identity": state["current_snapshot_identity"],
        "previous_received_at": state["previous_received_at"],
        "current_received_at": state["current_received_at"],
        "change_rows": len(changes),
        "tracked_rows": len(tracked),
        "future_only": True,
        "replacement": False,
        "historical_backfill": False,
        "transition_created": False,
        "current_samples": 160,
        "new_samples_counted": 0,
        "remaining_samples": 340,
        "network_activity_performed": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "identity": identity,
        "artifacts": {
            "changes": {"filename": paths["changes"].name, "sha256": identity["artifacts"]["changes_sha256"], "row_count": len(changes)},
            "tracked": {"filename": paths["tracked"].name, "sha256": identity["artifacts"]["tracked_sha256"], "row_count": len(tracked)},
            "policy": {"filename": paths["policy"].name, "sha256": identity["artifacts"]["policy_sha256"], "row_count": len(policy_rows)},
            "dependencies": {"filename": paths["dependencies"].name, "sha256": identity["artifacts"]["dependencies_sha256"], "row_count": len(dependencies)},
            "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)},
            "report": {"filename": paths["report"].name},
        },
    }
    for key, content in (("changes", changes_bytes), ("tracked", tracked_bytes), ("policy", policy_bytes), ("dependencies", dependencies_bytes), ("constraints", constraints_bytes)):
        _commit_bytes(paths[key], content)
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveSnapshotTransitionMaterializationResult(report, {key: str(value) for key, value in paths.items()})


def load_snapshot_transition_materializer_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("snapshot transition materializer config filename is not frozen")
    try:
        value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("snapshot transition materializer config is invalid") from exc
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("snapshot transition materializer config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("snapshot transition materializer config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def validate_prospective_snapshot_transition_materialization(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if not _report_dir_allowed(report_path, repo):
        raise MarketDataError("snapshot transition materialization report path escape")
    report = _load_json(report_path)
    transition_sha = report.get("transition_sha256")
    identity = report.get("identity")
    if not isinstance(transition_sha, str) or report_path.name != f"prospective-snapshot-transition.{transition_sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != transition_sha:
        raise MarketDataError("snapshot transition materialization identity mismatch")
    if report.get("contract_status") != CONTRACT_STATUS:
        raise MarketDataError("snapshot transition materialization contract status mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("snapshot transition materialization artifacts shape mismatch")
    for key in ("changes", "tracked", "policy", "dependencies", "constraints"):
        artifact = artifacts.get(key)
        expected = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(artifact, dict) or artifact.get("sha256") != expected:
            raise MarketDataError(f"snapshot transition materialization artifact metadata mismatch:{key}")
        artifact_path = report_path.parent / str(artifact.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected:
            raise MarketDataError(f"snapshot transition materialization artifact bytes mismatch:{key}")
    admission_path = _locate_admission_report(repo, str(identity.get("admission_sha256")), str(identity.get("admission_report_filename")))
    admission = validate_prospective_snapshot_transition_admission(admission_path)
    config = identity.get("policy")
    if not isinstance(config, dict) or config != load_snapshot_transition_materializer_config(repo / DEFAULT_CONFIG_FILENAME, repo):
        raise MarketDataError("snapshot transition materialization policy mismatch")
    archive_config = load_future_universe_archive_config(repo / ARCHIVE_CONFIG_FILENAME, repo)
    state = _derive_materialization(admission, repo, archive_config, config)
    if identity.get("transition_materialized") != state["transition_materialized"] or identity.get("previous_snapshot_identity") != state["previous_snapshot_identity"] or identity.get("current_snapshot_identity") != state["current_snapshot_identity"]:
        raise MarketDataError("snapshot transition materialization derived state mismatch")
    expected_report = {
        "epoch_ordinal": 2,
        "closeout_state": admission["closeout_state"],
        "rollover_action": admission["rollover_action"],
        "transition_admission_status": admission["transition_admission_status"],
        "transition_materialized": state["transition_materialized"],
        "previous_snapshot_identity": state["previous_snapshot_identity"],
        "current_snapshot_identity": state["current_snapshot_identity"],
        "previous_received_at": state["previous_received_at"],
        "current_received_at": state["current_received_at"],
        "change_rows": len(state["changes"]),
        "tracked_rows": len(state["tracked"]),
        "future_only": True,
        "replacement": False,
        "historical_backfill": False,
        "transition_created": False,
        "current_samples": 160,
        "new_samples_counted": 0,
        "remaining_samples": 340,
        "network_activity_performed": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    for key, value in expected_report.items():
        if report.get(key) != value:
            raise MarketDataError(f"snapshot transition materialization identity mismatch/report:{key}")
    for key, fields, rows in (("changes", CHANGE_FIELDS, state["changes"]), ("tracked", TRACKED_FIELDS, state["tracked"]), ("policy", POLICY_FIELDS, state["policy_rows"])):
        actual = _read_csv(report_path.parent / str(artifacts[key]["filename"]), fields)
        expected_rows = [{field: _csv_value(row.get(field)) for field in fields} for row in rows]
        if actual != expected_rows:
            raise MarketDataError(f"snapshot transition materialization {key} artifact mismatch")
    dependencies = _dependency_values(admission, state)
    constraints = _constraint_values(state)
    if _read_key_values(report_path.parent / str(artifacts["dependencies"]["filename"])) != dependencies:
        raise MarketDataError("snapshot transition materialization dependency artifact mismatch")
    if _read_key_values(report_path.parent / str(artifacts["constraints"]["filename"])) != {key: _csv_value(value) for key, value in constraints.items()}:
        raise MarketDataError("snapshot transition materialization constraint artifact mismatch")
    return report


def format_prospective_snapshot_transition_materialization(result: ProspectiveSnapshotTransitionMaterializationResult) -> str:
    report = result.report
    return "\n".join((
        f"contract_status: {report['contract_status']}",
        f"transition_sha256: {report['transition_sha256']}",
        f"closeout_state: {report['closeout_state']}",
        f"rollover_action: {report['rollover_action']}",
        f"transition_materialized: {str(report['transition_materialized']).lower()}",
        f"change_rows: {report['change_rows']}",
        f"previous_snapshot_identity: {report['previous_snapshot_identity']}",
        f"current_snapshot_identity: {report['current_snapshot_identity']}",
        "future_only: true",
        "replacement: false",
        "historical_backfill: false",
        "transition_created: false",
        "current_samples: 160",
        "new_samples_counted: 0",
        "remaining_samples: 340",
        "network_activity_performed: false",
        "readiness_changed: false",
    ))


def _derive_materialization(admission: Mapping[str, Any], repo: Path, archive_config: dict[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    _validate_policy(config)
    status = admission.get("transition_admission_status")
    eligible = admission.get("transition_admission_eligible")
    previous = admission.get("previous_snapshot_identity")
    current = admission.get("current_snapshot_identity")
    if status in {"blocked_pending_closeout", "blocked_missed_epoch"} and eligible is False:
        changes: list[dict[str, Any]] = []
        tracked: list[dict[str, Any]] = []
        policy_rows = _policy_rows(archive_config)
        return {"transition_materialized": False, "transition_status": status, "previous_snapshot_identity": previous, "current_snapshot_identity": None, "previous_received_at": None, "current_received_at": None, "changes": changes, "tracked": tracked, "policy_rows": policy_rows}
    if status != "admitted" or eligible is not True or not isinstance(previous, str) or not isinstance(current, str):
        raise MarketDataError("snapshot transition materialization admission gate mismatch")
    previous_snapshot, current_snapshot = _validated_pair(admission, repo, previous, current)
    changes, tracked, policy_rows = build_future_universe_transition_rows(previous_snapshot, current_snapshot, archive_config)
    previous_received = _snapshot_received_at(previous_snapshot)
    current_received = _snapshot_received_at(current_snapshot)
    if current_received <= previous_received:
        raise MarketDataError("snapshot transition materialization snapshot ordering mismatch")
    return {"transition_materialized": True, "transition_status": status, "previous_snapshot_identity": previous, "current_snapshot_identity": current, "previous_received_at": _iso(previous_received), "current_received_at": _iso(current_received), "changes": changes, "tracked": tracked, "policy_rows": policy_rows}


def _validated_pair(admission: Mapping[str, Any], repo: Path, previous: str, current: str) -> tuple[FutureUniverseSnapshot, FutureUniverseSnapshot]:
    previous_path = repo / "reports/okx-future-universe-snapshot" / f"okx-universe-snapshot.{previous}.json"
    previous_snapshot = validate_future_universe_snapshot(previous_path, repo / ARCHIVE_CONFIG_FILENAME)
    identity = admission.get("identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("closeout_sha256"), str):
        raise MarketDataError("snapshot transition materialization closeout reference mismatch")
    closeout_path = admission_contract._locate_closeout_report(repo, str(identity["closeout_sha256"]), str(identity.get("closeout_report_filename")))
    closeout = admission_contract.validate_capture_window_closeout(closeout_path)
    current_snapshot = admission_contract._validated_accepted_snapshot(repo, closeout, current)
    if not isinstance(current_snapshot, FutureUniverseSnapshot) or current_snapshot.report.get("snapshot_sha256") != current:
        raise MarketDataError("snapshot transition materialization current snapshot identity mismatch")
    return previous_snapshot, current_snapshot


def _snapshot_received_at(snapshot: FutureUniverseSnapshot) -> datetime:
    try:
        value = str(snapshot.report["identity"]["received_at"])
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataError("snapshot transition materialization timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataError("snapshot transition materialization timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _validate_policy(config: Mapping[str, Any]) -> None:
    expected = {
        "schema_version": 1,
        "policy_id": POLICY_ID,
        "required_admission_status": "admitted",
        "required_transition_admission_eligible": True,
        "transition_source": "validated_admission_pair",
        "materialization_mode": "future_only_snapshot_transition",
        "manual_snapshot_override_prohibited": True,
        "membership_action_override_prohibited": True,
        "historical_backfill_prohibited": True,
        "automatic_replacement": False,
        "sample_threshold": 500,
        "new_samples_counted": 0,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    if dict(config) != expected:
        raise MarketDataError("snapshot transition materializer policy semantics mismatch")


def _policy_rows(config: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"key": key, "value": json.dumps(value, ensure_ascii=False, sort_keys=True)} for key, value in _flatten(config).items()]


def _dependency_values(admission: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, str]:
    return {
        "admission_sha256": str(admission["admission_sha256"]),
        "closeout_state": str(admission["closeout_state"]),
        "rollover_action": str(admission["rollover_action"]),
        "previous_snapshot_identity": str(state["previous_snapshot_identity"] or ""),
        "current_snapshot_identity": str(state["current_snapshot_identity"] or ""),
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
    }


def _constraint_values(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "transition_materialized": state["transition_materialized"],
        "future_only": True,
        "replacement": False,
        "historical_backfill": False,
        "current_samples": 160,
        "new_samples_counted": 0,
        "remaining_samples": 340,
        "network_activity_performed": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }


def _claims() -> dict[str, Any]:
    return {"future_only": True, "admission_required": True, "snapshot_pair_override_prohibited": True, "automatic_replacement": False, "historical_backfill_prohibited": True, "profitability_evidence": False, "economic_computation_authorized": False, "readiness_changed": False}


def _locate_admission_report(repo: Path, sha: str, filename: str) -> Path:
    if not admission_contract._is_sha256(sha) or filename != f"prospective-snapshot-transition-admission.{sha}.json":
        raise MarketDataError("snapshot transition materialization admission reference mismatch")
    canonical = repo / "reports/prospective-snapshot-transition-admission" / filename
    if canonical.is_file():
        return canonical
    matches = [path for path in (repo / "reports").rglob(filename) if path.is_file() and path.parent.name.startswith("prospective-snapshot-transition-admission-")]
    if len(matches) != 1:
        raise MarketDataError("snapshot transition materialization admission reference ambiguous")
    return matches[0]


def _report_dir_allowed(path: Path, repo: Path) -> bool:
    canonical = (repo / "reports/prospective-snapshot-transition").resolve()
    return path.parent == canonical or (path.parent.parent == (repo / "reports").resolve() and path.parent.name.startswith("prospective-snapshot-transition-"))


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("snapshot transition materializer output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fields):
                raise MarketDataError("snapshot transition materialization CSV schema mismatch")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("snapshot transition materialization CSV read failed") from exc
    if any(any(row.get(field) is None for field in fields) for row in rows):
        raise MarketDataError("snapshot transition materialization CSV row mismatch")
    return rows


def _read_key_values(path: Path) -> dict[str, str]:
    rows = _read_csv(path, TRANSITION_FIELDS)
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows):
        raise MarketDataError("snapshot transition materialization key collision")
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


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            result.update(_flatten(child, f"{prefix}.{key}" if prefix else str(key)))
    else:
        result[prefix] = value
    return result


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("snapshot transition materialization repo root not found")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("snapshot transition materialization JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("snapshot transition materialization JSON shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"snapshot transition materialization content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".snapshot-transition-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
