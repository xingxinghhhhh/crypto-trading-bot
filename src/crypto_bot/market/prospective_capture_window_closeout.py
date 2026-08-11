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
from crypto_bot.market.prospective_capture_attempt_journal import (
    validate_capture_attempt_journal,
)
from crypto_bot.market.prospective_capture_attempt_receipt_chain import (
    load_validated_capture_attempt_receipts,
)
from crypto_bot.market.prospective_epoch_capture_admission import (
    validate_capture_admission_ticket,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_capture_window_closeout_v1"
CONTRACT_STATUS = "verified_prospective_capture_window_closeout"
DEFAULT_CONFIG_FILENAME = "config.prospective-capture-window-closeout.example.yaml"
RECEIPT_CHAIN_PREFIX = "prospective-capture-attempt-receipt-chain."
CLOSEOUT_PREFIX = "prospective-capture-window-closeout."
EVIDENCE_FIELDS = ("schema_version", "observed_at")
STATE_FIELDS = (
    "epoch_ordinal",
    "window_start",
    "window_end",
    "observed_at",
    "window_status",
    "receipt_chain_sha256",
    "replayed_chain_status",
    "receipt_count",
    "accepted_attempt_count",
    "replayed_next_attempt_permitted",
    "closeout_eligible",
    "derived_final_state",
    "accepted_snapshot_identity",
    "retry_permitted",
)
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class CaptureWindowCloseoutResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def audit_prospective_capture_window_closeout(
    receipt_chain: str | Path,
    admission_ticket: str | Path,
    closeout_evidence: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> CaptureWindowCloseoutResult:
    """Derive an epoch-2 window closeout from validated local artifacts only."""

    receipt_chain_path = Path(receipt_chain).resolve()
    repo = _repo_root(receipt_chain_path)
    config = load_capture_window_closeout_config(config_path, repo)
    chain_report, _receipts = load_validated_capture_attempt_receipts(receipt_chain_path)
    ticket_path = Path(admission_ticket).resolve()
    ticket = validate_capture_admission_ticket(ticket_path)
    journal_path = repo / "reports/prospective-capture-attempt-journal" / (
        f"prospective-capture-attempt-journal.{chain_report['journal_contract_sha256']}.json"
    )
    journal = validate_capture_attempt_journal(journal_path)
    _validate_dependencies(chain_report, ticket, journal, ticket_path, repo, config)
    evidence_path = Path(closeout_evidence).resolve()
    evidence = _load_json(evidence_path)
    _validate_evidence(evidence)
    derived = _derive_closeout(chain_report, ticket, evidence, config)
    evidence_value = {field: evidence[field] for field in EVIDENCE_FIELDS}
    evidence_sha = _digest(_canonical_json_bytes(evidence_value))
    receipt_chain_name = receipt_chain_path.name
    dependencies = _dependency_values(
        chain_report,
        ticket,
        journal,
        receipt_chain_name,
        evidence_sha,
    )
    constraints = _constraint_values(derived)
    state = _state_row(chain_report, ticket, evidence, derived)
    state_bytes = _csv_bytes([state], STATE_FIELDS)
    dependencies_bytes = _csv_bytes(_rows(dependencies), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraints), KEY_VALUE_FIELDS)
    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "epoch_ordinal": 2,
        "receipt_chain_sha256": chain_report["chain_sha256"],
        "receipt_chain_report_filename": receipt_chain_name,
        "journal_contract_sha256": journal["journal_sha256"],
        "admission_ticket_sha256": ticket["ticket_sha256"],
        "window_start": ticket["window_start"],
        "window_end": ticket["window_end"],
        "closeout_evidence_sha256": evidence_sha,
        "closeout_evidence": evidence_value,
        "replayed_chain_state": _replayed_state(chain_report),
        "derived_closeout_state": derived,
        "policy": config,
        "claims": _claims(),
        "artifacts": {
            "state_sha256": _digest(state_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    closeout_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"{CLOSEOUT_PREFIX}{closeout_sha}"
    paths = {
        "state": output / f"{stem}.state.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "closeout_sha256": closeout_sha,
        "contract_status": CONTRACT_STATUS,
        "epoch_ordinal": 2,
        "window_start": ticket["window_start"],
        "window_end": ticket["window_end"],
        "observed_at": evidence["observed_at"],
        "receipt_chain_sha256": chain_report["chain_sha256"],
        "admission_ticket_sha256": ticket["ticket_sha256"],
        "journal_contract_sha256": journal["journal_sha256"],
        **_replayed_state(chain_report),
        **derived,
        "network_activity_performed": False,
        "new_samples_counted": 0,
        "current_samples": 160,
        "remaining_samples": 340,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "identity": identity,
        "artifacts": {
            "state": {
                "filename": paths["state"].name,
                "sha256": identity["artifacts"]["state_sha256"],
                "row_count": 1,
            },
            "dependencies": {
                "filename": paths["dependencies"].name,
                "sha256": identity["artifacts"]["dependencies_sha256"],
                "row_count": len(dependencies),
            },
            "constraints": {
                "filename": paths["constraints"].name,
                "sha256": identity["artifacts"]["constraints_sha256"],
                "row_count": len(constraints),
            },
            "report": {"filename": paths["report"].name},
        },
    }
    # Marker-last: all deterministic CSVs are committed before the JSON marker.
    _commit_bytes(paths["state"], state_bytes)
    _commit_bytes(paths["dependencies"], dependencies_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return CaptureWindowCloseoutResult(
        report, {key: str(value) for key, value in paths.items()}
    )


def load_capture_window_closeout_config(
    path: str | Path, repo: Path | None = None
) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("capture window closeout config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("capture window closeout config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("capture window closeout config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def validate_capture_window_closeout(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if not _closeout_report_dir_allowed(report_path, repo):
        raise MarketDataError("capture window closeout report path escape")
    report = _load_json(report_path)
    closeout_sha = report.get("closeout_sha256")
    identity = report.get("identity")
    if (
        not isinstance(closeout_sha, str)
        or report_path.name != f"{CLOSEOUT_PREFIX}{closeout_sha}.json"
        or not isinstance(identity, dict)
        or _digest(_canonical_json_bytes(identity)) != closeout_sha
    ):
        raise MarketDataError("capture window closeout identity mismatch")
    if report.get("contract_status") != CONTRACT_STATUS:
        raise MarketDataError("capture window closeout contract status mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("capture window closeout artifacts shape mismatch")
    for key in ("state", "dependencies", "constraints"):
        artifact = artifacts.get(key)
        expected = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(artifact, dict) or artifact.get("sha256") != expected:
            raise MarketDataError(f"capture window closeout artifact metadata mismatch:{key}")
        artifact_path = report_path.parent / str(artifact.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected:
            raise MarketDataError(f"capture window closeout artifact bytes mismatch:{key}")
    _validate_evidence(identity.get("closeout_evidence"))
    if identity.get("closeout_evidence_sha256") != _digest(
        _canonical_json_bytes(identity["closeout_evidence"])
    ):
        raise MarketDataError("capture window closeout evidence hash mismatch")
    chain_path = _locate_receipt_chain_report(
        repo, str(identity.get("receipt_chain_sha256")), str(identity.get("receipt_chain_report_filename"))
    )
    chain_report, _receipts = load_validated_capture_attempt_receipts(chain_path)
    ticket_path = repo / "reports/prospective-epoch-capture-admission" / (
        f"prospective-epoch-capture-admission.{identity.get('admission_ticket_sha256')}.json"
    )
    ticket = validate_capture_admission_ticket(ticket_path)
    journal_path = repo / "reports/prospective-capture-attempt-journal" / (
        f"prospective-capture-attempt-journal.{identity.get('journal_contract_sha256')}.json"
    )
    journal = validate_capture_attempt_journal(journal_path)
    config = identity.get("policy")
    if not isinstance(config, dict) or config != load_capture_window_closeout_config(
        repo / DEFAULT_CONFIG_FILENAME, repo
    ):
        raise MarketDataError("capture window closeout policy mismatch")
    _validate_dependencies(chain_report, ticket, journal, ticket_path, repo, config)
    derived = _derive_closeout(chain_report, ticket, identity["closeout_evidence"], config)
    replayed = _replayed_state(chain_report)
    if identity.get("replayed_chain_state") != replayed or identity.get("derived_closeout_state") != derived:
        raise MarketDataError("capture window closeout derived state mismatch")
    state_rows = _read_csv(report_path.parent / str(artifacts["state"]["filename"]), STATE_FIELDS)
    if len(state_rows) != 1:
        raise MarketDataError("capture window closeout state row mismatch")
    expected_state = _state_row(
        chain_report,
        ticket,
        identity["closeout_evidence"],
        derived,
    )
    if state_rows[0] != {key: _csv_value(expected_state[key]) for key in STATE_FIELDS}:
        raise MarketDataError("capture window closeout state artifact mismatch")
    dependencies = _dependency_values(
        chain_report,
        ticket,
        journal,
        str(identity["receipt_chain_report_filename"]),
        str(identity["closeout_evidence_sha256"]),
    )
    constraints = _constraint_values(derived)
    if _read_key_values(report_path.parent / str(artifacts["dependencies"]["filename"])) != dependencies:
        raise MarketDataError("capture window closeout dependency artifact mismatch")
    if _read_key_values(report_path.parent / str(artifacts["constraints"]["filename"])) != {
        key: _csv_value(value) for key, value in constraints.items()
    }:
        raise MarketDataError("capture window closeout constraint artifact mismatch")
    expected_projection = {
        "epoch_ordinal": 2,
        "window_start": ticket["window_start"],
        "window_end": ticket["window_end"],
        "observed_at": identity["closeout_evidence"]["observed_at"],
        "receipt_chain_sha256": chain_report["chain_sha256"],
        "admission_ticket_sha256": ticket["ticket_sha256"],
        "journal_contract_sha256": journal["journal_sha256"],
        **replayed,
        **derived,
        "network_activity_performed": False,
        "new_samples_counted": 0,
        "current_samples": 160,
        "remaining_samples": 340,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    for key, expected in expected_projection.items():
        if report.get(key) != expected:
            raise MarketDataError(f"capture window closeout report field mismatch:{key}")
    return report


def format_capture_window_closeout_result(
    result: CaptureWindowCloseoutResult,
) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"closeout_sha256: {report['closeout_sha256']}",
            f"receipt_chain_sha256: {report['receipt_chain_sha256']}",
            f"window_start: {report['window_start']}",
            f"window_end: {report['window_end']}",
            f"observed_at: {report['observed_at']}",
            f"replayed_chain_status: {report['replayed_chain_status']}",
            f"receipt_count: {report['receipt_count']}",
            f"closeout_eligible: {str(report['closeout_eligible']).lower()}",
            f"derived_final_state: {report['derived_final_state']}",
            f"retry_permitted: {str(report['retry_permitted']).lower()}",
            "network_activity_performed: false",
            "current_samples: 160",
            "remaining_samples: 340",
            "economic_computation_authorized: false",
            "readiness_changed: false",
        )
    )


def _validate_dependencies(
    chain_report: dict[str, Any],
    ticket: dict[str, Any],
    journal: dict[str, Any],
    ticket_path: Path,
    repo: Path,
    config: dict[str, Any],
) -> None:
    if ticket_path.parent != (repo / "reports/prospective-epoch-capture-admission").resolve():
        raise MarketDataError("capture window closeout admission ticket path escape")
    if (
        chain_report.get("chain_sha256") != chain_report.get("identity", {}).get("chain_sha256")
        and chain_report.get("identity", {}).get("chain_sha256") is not None
    ):
        raise MarketDataError("capture window closeout chain identity mismatch")
    if (
        chain_report.get("epoch_ordinal") != 2
        or chain_report.get("journal_contract_sha256") != journal.get("journal_sha256")
        or chain_report.get("admission_ticket_sha256") != ticket.get("ticket_sha256")
        or ticket.get("epoch_ordinal") != 2
        or journal.get("epoch_ordinal") != 2
        or ticket.get("window_start") != journal.get("window_start")
        or ticket.get("window_end") != journal.get("window_end")
        or chain_report.get("window_start") != ticket.get("window_start")
        or chain_report.get("window_end") != ticket.get("window_end")
        or ticket.get("current_samples") != 160
        or ticket.get("remaining_samples") != 340
        or chain_report.get("current_samples") != 160
        or chain_report.get("remaining_samples") != 340
        or journal.get("current_samples") != 160
        or journal.get("remaining_samples") != 340
        or config.get("sample_threshold") != 500
        or config.get("economic_computation_authorized") is not False
        or config.get("pnl_computation_authorized") is not False
        or config.get("readiness_changed") is not False
    ):
        raise MarketDataError("capture window closeout dependency state mismatch")
    if ticket.get("ticket_status") != "pending_future_window" or ticket.get("current_state") != "awaiting_capture_window":
        raise MarketDataError("capture window closeout ticket is not pending")
    if journal.get("journal_status") != "awaiting_future_attempt":
        raise MarketDataError("capture window closeout journal state mismatch")


def _derive_closeout(
    chain_report: dict[str, Any],
    ticket: dict[str, Any],
    evidence: Mapping[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    status = chain_report.get("chain_status")
    if status not in {"awaiting_attempt", "retry_open", "accepted_closed"}:
        raise MarketDataError("capture window closeout chain status mismatch")
    observed = _parse_utc(str(evidence["observed_at"]))
    window_start = _parse_utc(str(ticket["window_start"]))
    window_end = _parse_utc(str(ticket["window_end"]))
    if observed < window_start:
        window_status = "open_future"
    elif observed < window_end:
        window_status = "open"
    else:
        window_status = "ended"
    accepted = status == "accepted_closed"
    if accepted:
        final_state = "accepted_closed"
        eligible = True
        retry_permitted = False
    elif observed >= window_end:
        final_state = "missed_no_backfill"
        eligible = True
        retry_permitted = False
    else:
        final_state = "pending_window_end"
        eligible = False
        retry_permitted = True
    if final_state not in config["allowed_final_states"] and final_state != "pending_window_end":
        raise MarketDataError("capture window closeout final state policy mismatch")
    return {
        "window_status": window_status,
        "closeout_eligible": eligible,
        "derived_final_state": final_state,
        "retry_permitted": retry_permitted,
        "accepted_snapshot_identity": chain_report.get("accepted_snapshot_identity"),
    }


def _replayed_state(chain_report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "replayed_chain_status": chain_report["chain_status"],
        "receipt_count": chain_report["receipt_count"],
        "accepted_attempt_count": chain_report["accepted_attempt_count"],
        "replayed_next_attempt_permitted": chain_report["next_attempt_permitted"],
    }


def _state_row(
    chain_report: Mapping[str, Any],
    ticket: Mapping[str, Any],
    evidence: Mapping[str, Any],
    derived: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "epoch_ordinal": 2,
        "window_start": ticket["window_start"],
        "window_end": ticket["window_end"],
        "observed_at": evidence["observed_at"],
        "receipt_chain_sha256": chain_report["chain_sha256"],
        **_replayed_state(chain_report),
        **derived,
    }


def _dependency_values(
    chain_report: Mapping[str, Any],
    ticket: Mapping[str, Any],
    journal: Mapping[str, Any],
    receipt_chain_name: str,
    evidence_sha: str,
) -> dict[str, str]:
    return {
        "admission_ticket_sha256": str(ticket["ticket_sha256"]),
        "journal_contract_sha256": str(journal["journal_sha256"]),
        "receipt_chain_report_filename": receipt_chain_name,
        "receipt_chain_sha256": str(chain_report["chain_sha256"]),
        "closeout_evidence_sha256": evidence_sha,
        "window_start": str(ticket["window_start"]),
        "window_end": str(ticket["window_end"]),
    }


def _constraint_values(derived: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "network_activity_performed": False,
        "real_capture_performed": False,
        "new_samples_counted": 0,
        "current_samples": 160,
        "remaining_samples": 340,
        "turnover_rows": 0,
        "cost_amount_rows": 0,
        "capacity_pass_fail_rows": 0,
        "return_rows": 0,
        "pnl_rows": 0,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "retry_permitted": derived["retry_permitted"],
        "missed_window_backfill_prohibited": True,
        "retroactive_capture_prohibited": True,
        "schedule_shift_after_miss": False,
    }


def _claims() -> dict[str, Any]:
    return {
        "first_validator_pass_is_mandatory_acceptance": True,
        "response_cherry_picking_prohibited": True,
        "retroactive_capture_prohibited": True,
        "missed_window_backfill_prohibited": True,
        "schedule_shift_after_miss": False,
        "future_only": True,
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "profitability_evidence": False,
        "economic_computation_authorized": False,
        "readiness_changed": False,
    }


def _validate_evidence(evidence: Any) -> None:
    if not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE_FIELDS):
        raise MarketDataError("capture window closeout evidence schema mismatch")
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise MarketDataError("capture window closeout evidence version mismatch")
    if not isinstance(evidence.get("observed_at"), str) or not evidence["observed_at"]:
        raise MarketDataError("capture window closeout observed_at missing")
    _parse_utc(evidence["observed_at"])


def _locate_receipt_chain_report(repo: Path, chain_sha: str, filename: str) -> Path:
    if not _is_sha256(chain_sha) or filename != f"{RECEIPT_CHAIN_PREFIX}{chain_sha}.json":
        raise MarketDataError("capture window closeout receipt chain reference mismatch")
    reports = repo / "reports"
    canonical = reports / "prospective-capture-attempt-receipt-chain" / filename
    if canonical.is_file():
        return canonical
    matches = [
        path
        for path in reports.rglob(filename)
        if path.is_file()
        and path.parent.name.startswith("prospective-capture-attempt-receipt-chain-")
    ]
    if len(matches) != 1:
        raise MarketDataError("capture window closeout receipt chain reference ambiguous")
    return matches[0]


def _closeout_report_dir_allowed(path: Path, repo: Path) -> bool:
    canonical = (repo / "reports" / "prospective-capture-window-closeout").resolve()
    return path.parent == canonical or (
        path.parent.parent == (repo / "reports").resolve()
        and path.parent.name.startswith("prospective-capture-window-closeout-")
    )


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("capture window closeout output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fields):
                raise MarketDataError("capture window closeout CSV schema mismatch")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("capture window closeout CSV read failed") from exc
    if any(any(row.get(field) is None for field in fields) for row in rows):
        raise MarketDataError("capture window closeout CSV row mismatch")
    return rows


def _read_key_values(path: Path) -> dict[str, str]:
    rows = _read_csv(path, KEY_VALUE_FIELDS)
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows):
        raise MarketDataError("capture window closeout key collision")
    return values


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
        raise MarketDataError("capture window closeout timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataError("capture window closeout timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("capture window closeout repo root not found")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("capture window closeout JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("capture window closeout JSON shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"capture window closeout content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".capture-window-closeout-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
