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
from crypto_bot.market.okx_future_universe_archive import (
    validate_future_universe_snapshot,
)
from crypto_bot.market.prospective_capture_attempt_receipt_chain import (
    load_validated_capture_attempt_receipts,
)
from crypto_bot.market.prospective_capture_window_closeout import (
    validate_capture_window_closeout,
)
from crypto_bot.prospective_epoch_closeout_rollover import (
    _validate_assembly,
    validate_prospective_epoch_closeout_rollover,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_snapshot_transition_admission_v1"
CONTRACT_STATUS = "verified_prospective_snapshot_transition_admission"
DEFAULT_CONFIG_FILENAME = "config.prospective-snapshot-transition-admission.example.yaml"
SNAPSHOT_CONFIG_FILENAME = "config.okx-future-universe-archive.example.yaml"
WINDOW_START = "2026-08-16T10:00:00Z"
WINDOW_END = "2026-08-16T11:00:00Z"
EXPECTED_PREVIOUS_SNAPSHOT = "d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e"
ADMISSION_FIELDS = (
    "epoch_ordinal",
    "closeout_state",
    "rollover_action",
    "transition_admission_eligible",
    "transition_admission_status",
    "previous_snapshot_identity",
    "current_snapshot_identity",
    "previous_received_at",
    "current_received_at",
    "future_only",
    "transition_created",
)
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class SnapshotTransitionAdmissionResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_prospective_snapshot_transition_admission(
    rollover_report: str | Path,
    closeout_report: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> SnapshotTransitionAdmissionResult:
    rollover_path = Path(rollover_report).resolve()
    closeout_path = Path(closeout_report).resolve()
    repo = _repo_root(rollover_path)
    config = load_snapshot_transition_admission_config(config_path, repo)
    rollover = validate_prospective_epoch_closeout_rollover(rollover_path)
    closeout = validate_capture_window_closeout(closeout_path)
    _validate_closeout_binding(rollover, closeout, closeout_path)
    assembly = _validated_assembly_for_rollover(rollover, repo)
    derived = _derive_admission(rollover, closeout, assembly, repo, config)
    dependencies = _dependency_values(rollover, closeout, assembly, derived)
    constraints = _constraint_values(derived)
    admission_bytes = _csv_bytes([_admission_row(derived)], ADMISSION_FIELDS)
    dependencies_bytes = _csv_bytes(_rows(dependencies), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraints), KEY_VALUE_FIELDS)
    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "rollover_sha256": rollover["rollover_sha256"],
        "rollover_report_filename": rollover_path.name,
        "closeout_sha256": closeout["closeout_sha256"],
        "closeout_report_filename": closeout_path.name,
        "assembly_sha256": str(rollover["identity"]["assembly_sha256"]),
        "epoch_ordinal": 2,
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "derived": derived,
        "policy": config,
        "claims": _claims(),
        "artifacts": {
            "admission_sha256": _digest(admission_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    admission_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-snapshot-transition-admission.{admission_sha}"
    paths = {
        "admission": output / f"{stem}.admission.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "admission_sha256": admission_sha,
        "contract_status": CONTRACT_STATUS,
        "epoch_ordinal": 2,
        "closeout_state": derived["closeout_state"],
        "rollover_action": derived["rollover_action"],
        "transition_admission_eligible": derived["transition_admission_eligible"],
        "transition_admission_status": derived["transition_admission_status"],
        "previous_snapshot_identity": derived["previous_snapshot_identity"],
        "current_snapshot_identity": derived["current_snapshot_identity"],
        "previous_received_at": derived["previous_received_at"],
        "current_received_at": derived["current_received_at"],
        "future_only": True,
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
            "admission": {"filename": paths["admission"].name, "sha256": identity["artifacts"]["admission_sha256"], "row_count": 1},
            "dependencies": {"filename": paths["dependencies"].name, "sha256": identity["artifacts"]["dependencies_sha256"], "row_count": len(dependencies)},
            "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)},
            "report": {"filename": paths["report"].name},
        },
    }
    _commit_bytes(paths["admission"], admission_bytes)
    _commit_bytes(paths["dependencies"], dependencies_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return SnapshotTransitionAdmissionResult(report, {key: str(value) for key, value in paths.items()})


def load_snapshot_transition_admission_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("snapshot transition admission config filename is not frozen")
    try:
        value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("snapshot transition admission config is invalid") from exc
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("snapshot transition admission config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("snapshot transition admission config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def validate_prospective_snapshot_transition_admission(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if not _report_dir_allowed(report_path, repo):
        raise MarketDataError("snapshot transition admission report path escape")
    report = _load_json(report_path)
    admission_sha = report.get("admission_sha256")
    identity = report.get("identity")
    if not isinstance(admission_sha, str) or report_path.name != f"prospective-snapshot-transition-admission.{admission_sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != admission_sha:
        raise MarketDataError("snapshot transition admission identity mismatch")
    if report.get("contract_status") != CONTRACT_STATUS:
        raise MarketDataError("snapshot transition admission contract status mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("snapshot transition admission artifacts shape mismatch")
    for key in ("admission", "dependencies", "constraints"):
        artifact = artifacts.get(key)
        expected = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(artifact, dict) or artifact.get("sha256") != expected:
            raise MarketDataError(f"snapshot transition admission artifact metadata mismatch:{key}")
        artifact_path = report_path.parent / str(artifact.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected:
            raise MarketDataError(f"snapshot transition admission artifact bytes mismatch:{key}")
    rollover_path = _locate_rollover_report(repo, str(identity.get("rollover_sha256")), str(identity.get("rollover_report_filename")))
    closeout_path = _locate_closeout_report(repo, str(identity.get("closeout_sha256")), str(identity.get("closeout_report_filename")))
    rollover = validate_prospective_epoch_closeout_rollover(rollover_path)
    closeout = validate_capture_window_closeout(closeout_path)
    config = identity.get("policy")
    if not isinstance(config, dict) or config != load_snapshot_transition_admission_config(repo / DEFAULT_CONFIG_FILENAME, repo):
        raise MarketDataError("snapshot transition admission policy mismatch")
    _validate_closeout_binding(rollover, closeout, closeout_path)
    assembly = _validated_assembly_for_rollover(rollover, repo)
    derived = _derive_admission(rollover, closeout, assembly, repo, config)
    if identity.get("derived") != derived:
        raise MarketDataError("snapshot transition admission derived state mismatch")
    expected = {
        "epoch_ordinal": 2,
        "closeout_state": derived["closeout_state"],
        "rollover_action": derived["rollover_action"],
        "transition_admission_eligible": derived["transition_admission_eligible"],
        "transition_admission_status": derived["transition_admission_status"],
        "previous_snapshot_identity": derived["previous_snapshot_identity"],
        "current_snapshot_identity": derived["current_snapshot_identity"],
        "previous_received_at": derived["previous_received_at"],
        "current_received_at": derived["current_received_at"],
        "future_only": True,
        "transition_created": False,
        "current_samples": 160,
        "new_samples_counted": 0,
        "remaining_samples": 340,
        "network_activity_performed": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise MarketDataError(f"snapshot transition admission identity mismatch/report:{key}")
    admission_rows = _read_csv(report_path.parent / str(artifacts["admission"]["filename"]), ADMISSION_FIELDS)
    if admission_rows != [{field: _csv_value(_admission_row(derived)[field]) for field in ADMISSION_FIELDS}]:
        raise MarketDataError("snapshot transition admission decision artifact mismatch")
    if _read_key_values(report_path.parent / str(artifacts["dependencies"]["filename"])) != _dependency_values(rollover, closeout, assembly, derived):
        raise MarketDataError("snapshot transition admission dependency artifact mismatch")
    if _read_key_values(report_path.parent / str(artifacts["constraints"]["filename"])) != {key: _csv_value(value) for key, value in _constraint_values(derived).items()}:
        raise MarketDataError("snapshot transition admission constraint artifact mismatch")
    return report


def format_prospective_snapshot_transition_admission(result: SnapshotTransitionAdmissionResult) -> str:
    report = result.report
    return "\n".join((
        f"contract_status: {report['contract_status']}",
        f"admission_sha256: {report['admission_sha256']}",
        f"closeout_state: {report['closeout_state']}",
        f"rollover_action: {report['rollover_action']}",
        f"transition_admission_eligible: {str(report['transition_admission_eligible']).lower()}",
        f"transition_admission_status: {report['transition_admission_status']}",
        f"previous_snapshot_identity: {report['previous_snapshot_identity']}",
        f"current_snapshot_identity: {report['current_snapshot_identity']}",
        f"transition_created: {str(report['transition_created']).lower()}",
        "current_samples: 160",
        "new_samples_counted: 0",
        "remaining_samples: 340",
        "network_activity_performed: false",
        "economic_computation_authorized: false",
        "readiness_changed: false",
    ))


def _validate_closeout_binding(rollover: Mapping[str, Any], closeout: Mapping[str, Any], closeout_path: Path) -> None:
    identity = rollover.get("identity")
    if not isinstance(identity, dict) or identity.get("closeout_sha256") != closeout.get("closeout_sha256") or identity.get("closeout_report_filename") != closeout_path.name:
        raise MarketDataError("snapshot transition admission closeout binding mismatch")
    if closeout.get("epoch_ordinal") != 2 or closeout.get("window_start") != WINDOW_START or closeout.get("window_end") != WINDOW_END or closeout.get("current_samples") != 160 or closeout.get("remaining_samples") != 340 or closeout.get("network_activity_performed") is not False:
        raise MarketDataError("snapshot transition admission closeout state mismatch")
    if rollover.get("epoch_ordinal") != 2 or rollover.get("current_samples") != 160 or rollover.get("new_samples_counted") != 0 or rollover.get("remaining_samples") != 340 or rollover.get("network_activity_performed") is not False:
        raise MarketDataError("snapshot transition admission rollover state mismatch")


def _validated_assembly_for_rollover(rollover: Mapping[str, Any], repo: Path) -> dict[str, Any]:
    identity = rollover.get("identity")
    if not isinstance(identity, dict) or not _is_sha256(identity.get("assembly_sha256")):
        raise MarketDataError("snapshot transition admission assembly reference mismatch")
    path = repo / "reports/prospective-epoch-assembly" / f"prospective-epoch-assembly.{identity['assembly_sha256']}.json"
    return _validate_assembly(path, repo)


def _derive_admission(rollover: Mapping[str, Any], closeout: Mapping[str, Any], assembly: Mapping[str, Any], repo: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    _validate_policy(config)
    action = rollover.get("action")
    state = closeout.get("derived_final_state")
    previous = assembly.get("expected_previous_snapshot")
    if not isinstance(previous, str) or not _is_sha256(previous):
        raise MarketDataError("snapshot transition admission previous snapshot identity mismatch")
    if action == "hold" and state == "pending_window_end":
        return _blocked_state(state, action, "blocked_pending_closeout", previous)
    if action == "rollover_next_window" and state == "missed_no_backfill":
        return _blocked_state(state, action, "blocked_missed_epoch", previous)
    if action != "transition_eligible" or state != "accepted_closed":
        raise MarketDataError("snapshot transition admission closeout/action mismatch")
    accepted = closeout.get("accepted_snapshot_identity")
    if not isinstance(accepted, str) or not _is_sha256(accepted) or accepted != rollover.get("latest_accepted_snapshot_identity"):
        raise MarketDataError("snapshot transition admission accepted identity mismatch")
    previous_snapshot = _validate_snapshot_by_identity(repo, previous)
    current_snapshot = _validated_accepted_snapshot(repo, closeout, accepted)
    previous_received = _snapshot_received_at(previous_snapshot)
    current_received = _snapshot_received_at(current_snapshot)
    if accepted == previous or current_received <= previous_received:
        raise MarketDataError("snapshot transition admission snapshot ordering mismatch")
    return {
        "closeout_state": state,
        "rollover_action": action,
        "transition_admission_eligible": True,
        "transition_admission_status": "admitted",
        "previous_snapshot_identity": previous,
        "current_snapshot_identity": accepted,
        "previous_received_at": _iso(previous_received),
        "current_received_at": _iso(current_received),
        "future_only": True,
        "transition_created": False,
    }


def _blocked_state(state: str, action: str, status: str, previous: str) -> dict[str, Any]:
    return {
        "closeout_state": state,
        "rollover_action": action,
        "transition_admission_eligible": False,
        "transition_admission_status": status,
        "previous_snapshot_identity": previous,
        "current_snapshot_identity": None,
        "previous_received_at": None,
        "current_received_at": None,
        "future_only": True,
        "transition_created": False,
    }


def _validated_accepted_snapshot(repo: Path, closeout: Mapping[str, Any], accepted: str) -> Any:
    identity = closeout.get("identity")
    if not isinstance(identity, dict) or not _is_sha256(identity.get("receipt_chain_sha256")):
        raise MarketDataError("snapshot transition admission receipt-chain reference mismatch")
    filename = identity.get("receipt_chain_report_filename")
    chain_path = _locate_chain_report(repo, str(identity["receipt_chain_sha256"]), str(filename))
    chain_report, receipts = load_validated_capture_attempt_receipts(chain_path)
    if chain_report.get("epoch_ordinal") != 2 or chain_report.get("accepted_attempt_count") != 1 or chain_report.get("accepted_snapshot_identity") != accepted:
        raise MarketDataError("snapshot transition admission accepted chain mismatch")
    accepted_rows = [row for row in receipts if row.get("accepted") is True]
    if len(accepted_rows) != 1 or accepted_rows[0].get("snapshot_identity") != accepted:
        raise MarketDataError("snapshot transition admission accepted receipt mismatch")
    marker_value = accepted_rows[0].get("snapshot_marker_path")
    marker_path = _repo_relative_path(repo, marker_value)
    return validate_future_universe_snapshot(marker_path, repo / SNAPSHOT_CONFIG_FILENAME)


def _validate_snapshot_by_identity(repo: Path, snapshot_sha: str) -> Any:
    path = repo / "reports/okx-future-universe-snapshot" / f"okx-universe-snapshot.{snapshot_sha}.json"
    if not path.is_file():
        raise MarketDataError("snapshot transition admission previous snapshot missing")
    return validate_future_universe_snapshot(path, repo / SNAPSHOT_CONFIG_FILENAME)


def _snapshot_received_at(snapshot: Any) -> datetime:
    report = getattr(snapshot, "report", None)
    if not isinstance(report, dict):
        raise MarketDataError("snapshot transition admission snapshot report mismatch")
    return _parse_utc(str(report.get("identity", {}).get("received_at", "")))


def _validate_policy(config: Mapping[str, Any]) -> None:
    expected = {
        "schema_version": 1,
        "policy_id": POLICY_ID,
        "required_closeout_state": "accepted_closed",
        "required_rollover_action": "transition_eligible",
        "previous_snapshot_source": "validated_pre_epoch_latest_accepted_snapshot",
        "current_snapshot_source": "validated_closeout_first_accepted_snapshot",
        "manual_snapshot_override_prohibited": True,
        "accepted_snapshot_replacement_prohibited": True,
        "same_snapshot_transition_prohibited": True,
        "current_received_at_must_be_after_previous": True,
        "pending_transition_prohibited": True,
        "missed_transition_prohibited": True,
        "historical_backfill_prohibited": True,
        "sample_threshold": 500,
        "new_samples_counted": 0,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    if dict(config) != expected:
        raise MarketDataError("snapshot transition admission policy semantics mismatch")


def _admission_row(derived: Mapping[str, Any]) -> dict[str, Any]:
    return {"epoch_ordinal": 2, **derived}


def _dependency_values(rollover: Mapping[str, Any], closeout: Mapping[str, Any], assembly: Mapping[str, Any], derived: Mapping[str, Any]) -> dict[str, str]:
    identity = rollover["identity"]
    closeout_identity = closeout["identity"]
    return {
        "rollover_sha256": str(rollover["rollover_sha256"]),
        "closeout_sha256": str(closeout["closeout_sha256"]),
        "assembly_sha256": str(identity["assembly_sha256"]),
        "receipt_chain_sha256": str(closeout_identity["receipt_chain_sha256"]),
        "journal_contract_sha256": str(closeout["journal_contract_sha256"]),
        "admission_ticket_sha256": str(closeout["admission_ticket_sha256"]),
        "previous_snapshot_identity": str(derived["previous_snapshot_identity"]),
        "current_snapshot_identity": str(derived["current_snapshot_identity"] or ""),
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
    }


def _constraint_values(derived: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "current_samples": 160,
        "new_samples_counted": 0,
        "remaining_samples": 340,
        "future_only": True,
        "transition_created": False,
        "manual_snapshot_override_prohibited": True,
        "accepted_snapshot_replacement_prohibited": True,
        "same_snapshot_transition_prohibited": True,
        "historical_backfill_prohibited": True,
        "network_activity_performed": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "transition_admission_eligible": derived["transition_admission_eligible"],
    }


def _claims() -> dict[str, Any]:
    return {
        "future_only": True,
        "first_validator_pass_is_mandatory_acceptance": True,
        "accepted_snapshot_replacement_prohibited": True,
        "response_cherry_picking_prohibited": True,
        "historical_backfill_prohibited": True,
        "profitability_evidence": False,
        "economic_computation_authorized": False,
        "readiness_changed": False,
    }


def _locate_rollover_report(repo: Path, sha: str, filename: str) -> Path:
    if not _is_sha256(sha) or filename != f"prospective-epoch-closeout-rollover.{sha}.json":
        raise MarketDataError("snapshot transition admission rollover reference mismatch")
    return _locate_report(repo, "prospective-epoch-closeout-rollover", filename, "rollover")


def _locate_closeout_report(repo: Path, sha: str, filename: str) -> Path:
    if not _is_sha256(sha) or filename != f"prospective-capture-window-closeout.{sha}.json":
        raise MarketDataError("snapshot transition admission closeout reference mismatch")
    return _locate_report(repo, "prospective-capture-window-closeout", filename, "closeout")


def _locate_chain_report(repo: Path, sha: str, filename: str) -> Path:
    if not _is_sha256(sha) or filename != f"prospective-capture-attempt-receipt-chain.{sha}.json":
        raise MarketDataError("snapshot transition admission receipt-chain reference mismatch")
    return _locate_report(repo, "prospective-capture-attempt-receipt-chain", filename, "receipt-chain")


def _locate_report(repo: Path, directory: str, filename: str, label: str) -> Path:
    canonical = repo / "reports" / directory / filename
    if canonical.is_file():
        return canonical
    matches = [path for path in (repo / "reports").rglob(filename) if path.is_file() and path.parent.name.startswith(f"{directory}-")]
    if len(matches) != 1:
        raise MarketDataError(f"snapshot transition admission {label} reference ambiguous")
    return matches[0]


def _repo_relative_path(repo: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise MarketDataError("snapshot transition admission snapshot path mismatch")
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (repo / raw).resolve()
    if not path.is_relative_to(repo.resolve()) or not path.is_file():
        raise MarketDataError("snapshot transition admission snapshot path escape")
    return path


def _report_dir_allowed(path: Path, repo: Path) -> bool:
    canonical = (repo / "reports" / "prospective-snapshot-transition-admission").resolve()
    return path.parent == canonical or (path.parent.parent == (repo / "reports").resolve() and path.parent.name.startswith("prospective-snapshot-transition-admission-"))


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("snapshot transition admission output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fields):
                raise MarketDataError("snapshot transition admission CSV schema mismatch")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("snapshot transition admission CSV read failed") from exc
    if any(any(row.get(field) is None for field in fields) for row in rows):
        raise MarketDataError("snapshot transition admission CSV row mismatch")
    return rows


def _read_key_values(path: Path) -> dict[str, str]:
    rows = _read_csv(path, KEY_VALUE_FIELDS)
    result = {row["key"]: row["value"] for row in rows}
    if len(result) != len(rows):
        raise MarketDataError("snapshot transition admission key collision")
    return result


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


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("snapshot transition admission timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataError("snapshot transition admission timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("snapshot transition admission repo root not found")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("snapshot transition admission JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("snapshot transition admission JSON shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"snapshot transition admission content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".snapshot-admission-", suffix=".tmp", delete=False) as handle:
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


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
