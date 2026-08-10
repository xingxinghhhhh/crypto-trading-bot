from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_epoch_capture_admission import (
    validate_capture_admission_ticket,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_epoch_capture_attempt_journal_v1"
CONTRACT_STATUS = "verified_prospective_capture_attempt_journal_contract"
DEFAULT_CONFIG_FILENAME = "config.prospective-capture-attempt-journal.example.yaml"
JOURNAL_FIELDS = (
    "epoch_ordinal",
    "journal_status",
    "attempt_count",
    "accepted_attempt_count",
    "accepted_snapshot_identity",
)
KEY_VALUE_FIELDS = ("key", "value")
ATTEMPT_STATUSES = ("transport_failed", "validation_failed", "validation_passed")
FORBIDDEN_ATTEMPT_FIELDS = {
    "selected_response",
    "response_rank",
    "manual_selection",
    "version_selected",
}


@dataclass(frozen=True)
class CaptureAttemptJournalResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_prospective_capture_attempt_journal(
    admission_ticket: str | Path, config_path: str | Path, output_dir: str | Path
) -> CaptureAttemptJournalResult:
    ticket_path = Path(admission_ticket).resolve()
    repo = _repo_root(ticket_path)
    config = load_capture_attempt_journal_config(config_path, repo)
    ticket = validate_capture_admission_ticket(ticket_path)
    _validate_config_for_ticket(config, ticket)
    journal_row = {
        "epoch_ordinal": 2,
        "journal_status": "awaiting_future_attempt",
        "attempt_count": 0,
        "accepted_attempt_count": 0,
        "accepted_snapshot_identity": "",
    }
    policy_values: dict[str, Any] = {
        "policy_id": POLICY_ID,
        "epoch_ordinal": 2,
        "ticket_sha256": ticket["ticket_sha256"],
        "window_start": ticket["window_start"],
        "window_end": ticket["window_end"],
        "attempt_numbering": config["attempt_numbering"],
        "attempt_mutability": config["attempt_mutability"],
        "retry_after_transport_failure": config["retry_after_transport_failure"],
        "retry_after_validation_failure": config["retry_after_validation_failure"],
        "retry_after_validation_pass": config["retry_after_validation_pass"],
        "retry_after_accepted": config["retry_after_accepted"],
        "acceptance_policy": config["acceptance_policy"],
        "accepted_attempt_count_max": config["accepted_attempt_count_max"],
        "out_of_window_attempt_admissible": config["out_of_window_attempt_admissible"],
        "attempt_without_ticket_prohibited": config["attempt_without_ticket_prohibited"],
        "ticket_cross_epoch_reuse_prohibited": config["ticket_cross_epoch_reuse_prohibited"],
        "response_selection_prohibited": config["response_selection_prohibited"],
        "manual_version_selection_prohibited": config["manual_version_selection_prohibited"],
        "attempt_deletion_prohibited": config["attempt_deletion_prohibited"],
        "attempt_reordering_prohibited": config["attempt_reordering_prohibited"],
    }
    constraint_values: dict[str, Any] = {
        "current_samples": 160,
        "remaining_samples": 340,
        "network_activity_performed": False,
        "actual_attempt_rows": 0,
        "accepted_snapshot_count": 0,
        "new_samples_counted": 0,
        "turnover_rows": 0,
        "cost_amount_rows": 0,
        "capacity_pass_fail_rows": 0,
        "return_rows": 0,
        "pnl_rows": 0,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    journal_bytes = _csv_bytes([journal_row], JOURNAL_FIELDS)
    policy_bytes = _csv_bytes(_rows(policy_values), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraint_values), KEY_VALUE_FIELDS)
    journal_identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "ticket_sha256": ticket["ticket_sha256"],
        "ticket_identity_sha256": _digest(_canonical_json_bytes(ticket["identity"])),
        "epoch_ordinal": 2,
        "window_start": ticket["window_start"],
        "window_end": ticket["window_end"],
        "journal_status": "awaiting_future_attempt",
        "attempt_count": 0,
        "accepted_attempt_count": 0,
        "accepted_snapshot_identity": None,
        "attempt_state_machine": [
            "attempt_started",
            "transport_failed",
            "response_received",
            "validation_failed",
            "validation_passed",
            "accepted",
        ],
        "policy": policy_values,
        "claims": {
            "epoch_2_policy_governed": True,
            "capture_attempts_result_independent": True,
            "first_valid_response_policy": True,
            "response_cherry_picking_prohibited": True,
            "recapture_until_matching_prohibited": True,
            "manual_response_selection_prohibited": True,
            "historical_backfill_prohibited": True,
            "sample_threshold": 500,
            "profitability_evidence": False,
            "economic_computation_authorized": False,
            "readiness_changed": False,
        },
        "artifacts": {
            "journal_sha256": _digest(journal_bytes),
            "policy_sha256": _digest(policy_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    journal_sha = _digest(_canonical_json_bytes(journal_identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-capture-attempt-journal.{journal_sha}"
    paths = {
        "journal": output / f"{stem}.journal.csv",
        "policy": output / f"{stem}.policy.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    for key, content in (
        ("journal", journal_bytes),
        ("policy", policy_bytes),
        ("constraints", constraints_bytes),
    ):
        _commit_bytes(paths[key], content)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "journal_sha256": journal_sha,
        "contract_status": CONTRACT_STATUS,
        "ticket_sha256": ticket["ticket_sha256"],
        "epoch_ordinal": 2,
        "window_start": ticket["window_start"],
        "window_end": ticket["window_end"],
        "journal_status": "awaiting_future_attempt",
        "attempt_count": 0,
        "accepted_attempt_count": 0,
        "accepted_snapshot_identity": None,
        "current_samples": 160,
        "remaining_samples": 340,
        "network_activity_performed": False,
        "actual_attempt_rows": 0,
        "accepted_snapshot_count": 0,
        "new_samples_counted": 0,
        "turnover_rows": 0,
        "cost_amount_rows": 0,
        "capacity_pass_fail_rows": 0,
        "return_rows": 0,
        "pnl_rows": 0,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "identity": journal_identity,
        "artifacts": {
            "journal": {"filename": paths["journal"].name, "sha256": journal_identity["artifacts"]["journal_sha256"], "row_count": 1},
            "policy": {"filename": paths["policy"].name, "sha256": journal_identity["artifacts"]["policy_sha256"], "row_count": len(_rows(policy_values))},
            "constraints": {"filename": paths["constraints"].name, "sha256": journal_identity["artifacts"]["constraints_sha256"], "row_count": len(_rows(constraint_values))},
            "report": {"filename": paths["report"].name},
        },
    }
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return CaptureAttemptJournalResult(report, {key: str(value) for key, value in paths.items()})


def load_capture_attempt_journal_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("capture attempt journal config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("capture attempt journal config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("capture attempt journal config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def validate_capture_attempt_sequence(
    attempts: Sequence[Mapping[str, Any]], window_start: str, window_end: str
) -> dict[str, Any]:
    """Validate future receipt semantics without network or snapshot creation."""

    start = _parse_utc(window_start)
    end = _parse_utc(window_end)
    accepted = False
    accepted_identity: str | None = None
    for expected_number, attempt in enumerate(attempts, start=1):
        if set(attempt).intersection(FORBIDDEN_ATTEMPT_FIELDS):
            raise MarketDataError("capture attempt response selection prohibited")
        if attempt.get("attempt_number") != expected_number:
            raise MarketDataError("capture attempt numbering is not strict")
        received_at = _parse_utc(str(attempt.get("received_at", "")))
        if received_at < start or received_at >= end:
            raise MarketDataError("capture attempt outside governed window")
        status = attempt.get("status")
        if status not in ATTEMPT_STATUSES:
            raise MarketDataError("capture attempt status mismatch")
        if accepted:
            raise MarketDataError("capture attempt after accepted response")
        marked_accepted = attempt.get("accepted", False)
        if status == "validation_passed":
            if marked_accepted is not True:
                raise MarketDataError("validation-passed attempt must be accepted")
            snapshot_identity = attempt.get("snapshot_identity")
            if not isinstance(snapshot_identity, str) or not snapshot_identity:
                raise MarketDataError("accepted attempt snapshot identity missing")
            accepted = True
            accepted_identity = snapshot_identity
        elif marked_accepted is True:
            raise MarketDataError("failed attempt cannot be accepted")
    return {
        "attempt_count": len(attempts),
        "accepted_attempt_count": 1 if accepted else 0,
        "accepted_snapshot_identity": accepted_identity,
    }


def validate_capture_attempt_journal(path: str | Path) -> dict[str, Any]:
    journal_path = Path(path).resolve()
    repo = _repo_root(journal_path)
    if journal_path.parent != (repo / "reports" / "prospective-capture-attempt-journal").resolve():
        raise MarketDataError("capture attempt journal path escape")
    report = _load_json(journal_path)
    journal_sha = report.get("journal_sha256")
    if not isinstance(journal_sha, str) or journal_path.name != f"prospective-capture-attempt-journal.{journal_sha}.json":
        raise MarketDataError("capture attempt journal filename mismatch")
    identity = report.get("identity")
    if not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != journal_sha:
        raise MarketDataError("capture attempt journal identity mismatch")
    ticket_path = repo / "reports/prospective-epoch-capture-admission" / f"prospective-epoch-capture-admission.{report.get('ticket_sha256')}.json"
    ticket = validate_capture_admission_ticket(ticket_path)
    if (
        report.get("schema_version") != SCHEMA_VERSION
        or report.get("contract_status") != CONTRACT_STATUS
        or report.get("ticket_sha256") != ticket["ticket_sha256"]
        or report.get("epoch_ordinal") != 2
        or report.get("journal_status") != "awaiting_future_attempt"
        or report.get("attempt_count") != 0
        or report.get("accepted_attempt_count") != 0
        or report.get("accepted_snapshot_identity") is not None
        or report.get("current_samples") != 160
        or report.get("remaining_samples") != 340
        or report.get("network_activity_performed") is not False
        or report.get("actual_attempt_rows") != 0
        or report.get("accepted_snapshot_count") != 0
        or report.get("new_samples_counted") != 0
        or report.get("economic_computation_authorized") is not False
        or report.get("pnl_computation_authorized") is not False
        or report.get("readiness_changed") is not False
    ):
        raise MarketDataError("capture attempt journal state mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("capture attempt journal artifacts shape mismatch")
    for key in ("journal", "policy", "constraints"):
        artifact = artifacts.get(key)
        expected_sha = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(artifact, dict) or artifact.get("sha256") != expected_sha:
            raise MarketDataError(f"capture attempt journal artifact metadata mismatch:{key}")
        artifact_path = journal_path.parent / str(artifact.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected_sha:
            raise MarketDataError(f"capture attempt journal artifact bytes mismatch:{key}")
    journal_rows = _read_csv(journal_path.parent / str(artifacts["journal"]["filename"]))
    if journal_rows != [{
        "epoch_ordinal": "2",
        "journal_status": "awaiting_future_attempt",
        "attempt_count": "0",
        "accepted_attempt_count": "0",
        "accepted_snapshot_identity": "",
    }]:
        raise MarketDataError("capture attempt journal sentinel row mismatch")
    return report


def format_capture_attempt_journal_result(result: CaptureAttemptJournalResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"journal_sha256: {report['journal_sha256']}",
            f"ticket_sha256: {report['ticket_sha256']}",
            "epoch_ordinal: 2",
            f"window_start: {report['window_start']}",
            f"window_end: {report['window_end']}",
            "journal_status: awaiting_future_attempt",
            "attempt_count: 0",
            "accepted_attempt_count: 0",
            "network_activity_performed: false",
            "current_samples: 160",
            "remaining_samples: 340",
            "economic_computation_authorized: false",
            "readiness_changed: false",
        )
    )


def _validate_config_for_ticket(config: dict[str, Any], ticket: dict[str, Any]) -> None:
    expected = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "required_epoch_ordinal": 2,
        "required_ticket_status": "pending_future_window",
        "attempt_numbering": "strict_monotonic_from_1",
        "attempt_mutability": "immutable_append_only",
        "retry_after_transport_failure": True,
        "retry_after_validation_failure": True,
        "retry_after_validation_pass": False,
        "retry_after_accepted": False,
        "acceptance_policy": "first_validation_passed_attempt",
        "accepted_attempt_count_max": 1,
        "out_of_window_attempt_admissible": False,
        "attempt_without_ticket_prohibited": True,
        "ticket_cross_epoch_reuse_prohibited": True,
        "response_selection_prohibited": True,
        "manual_version_selection_prohibited": True,
        "attempt_deletion_prohibited": True,
        "attempt_reordering_prohibited": True,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    if config != expected:
        raise MarketDataError("capture attempt journal config semantics mismatch")
    if (
        ticket.get("ticket_status") != "pending_future_window"
        or ticket.get("epoch_ordinal") != 2
        or ticket.get("current_samples") != 160
        or ticket.get("remaining_samples") != 340
        or ticket.get("sample_maturity_met") is not False
    ):
        raise MarketDataError("capture attempt journal ticket state mismatch")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("capture attempt timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataError("capture attempt timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _rows(values: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "key": key,
            "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True)
            if not isinstance(values[key], str)
            else values[key],
        }
        for key in sorted(values)
    ]


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("capture attempt journal repo root not found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("capture attempt journal output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("capture attempt journal json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("capture attempt journal json shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"capture attempt journal content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".capture-attempt-journal-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
