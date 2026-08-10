from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.okx_future_universe_archive import validate_future_universe_snapshot
from crypto_bot.market.prospective_capture_attempt_journal import (
    validate_capture_attempt_journal,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_capture_attempt_receipt_chain_v1"
CONTRACT_STATUS = "verified_prospective_capture_attempt_receipt_chain"
DEFAULT_CONFIG_FILENAME = "config.prospective-epoch-capture-attempt-receipt-chain.example.yaml"
SNAPSHOT_CONFIG_FILENAME = "config.okx-future-universe-archive.example.yaml"
RECEIPT_FIELDS = (
    "schema_version",
    "receipt_policy_id",
    "journal_contract_sha256",
    "admission_ticket_sha256",
    "epoch_ordinal",
    "attempt_number",
    "previous_receipt_sha256",
    "attempt_started_at",
    "outcome",
    "request_policy_sha256",
    "response_received",
    "response_sha256",
    "snapshot_marker_sha256",
    "snapshot_identity",
    "validator_status",
    "failure_code",
    "accepted",
    "snapshot_marker_path",
)
RECEIPT_CSV_FIELDS = ("receipt_sha256",) + RECEIPT_FIELDS
STATE_FIELDS = (
    "epoch_ordinal",
    "receipt_count",
    "next_attempt_number",
    "chain_status",
    "accepted_attempt_count",
    "accepted_snapshot_identity",
    "next_attempt_permitted",
)
KEY_VALUE_FIELDS = ("key", "value")
ALLOWED_OUTCOMES = ("transport_failed", "validation_failed", "validation_passed")
RECEIPT_HASH_FIELDS = set(RECEIPT_FIELDS)


class ReceiptChainResult:
    def __init__(self, report: dict[str, Any], export_paths: dict[str, str]) -> None:
        self.report = report
        self.export_paths = export_paths


def audit_prospective_capture_attempt_receipt_chain(
    journal_contract: str | Path,
    receipt_paths: Sequence[str | Path],
    config_path: str | Path,
    output_dir: str | Path,
) -> ReceiptChainResult:
    journal_path = Path(journal_contract).resolve()
    repo = _repo_root(journal_path)
    config = load_receipt_chain_config(config_path, repo)
    journal = validate_capture_attempt_journal(journal_path)
    _validate_config_and_journal(config, journal)
    receipts = [_load_json(Path(path).resolve()) for path in receipt_paths]
    ticket_sha = str(journal["ticket_sha256"])
    ticket_path = repo / "reports/prospective-epoch-capture-admission" / f"prospective-epoch-capture-admission.{ticket_sha}.json"
    ticket = _load_json(ticket_path)
    request_policy_sha = str(ticket["identity"]["request_policy"]["request_policy_identity"])
    state = _replay_receipts(receipts, config, journal, ticket, request_policy_sha, repo)
    receipt_hashes = [_receipt_sha(receipt) for receipt in receipts]
    receipts_bytes = _receipt_csv_bytes(receipts, receipt_hashes)
    state_row = {
        "epoch_ordinal": 2,
        "receipt_count": state["receipt_count"],
        "next_attempt_number": state["next_attempt_number"],
        "chain_status": state["chain_status"],
        "accepted_attempt_count": state["accepted_attempt_count"],
        "accepted_snapshot_identity": state["accepted_snapshot_identity"] or "",
        "next_attempt_permitted": state["next_attempt_permitted"],
    }
    state_bytes = _csv_bytes([state_row], STATE_FIELDS)
    constraints = {
        "network_activity_performed": False,
        "actual_receipt_count": len(receipts),
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
    }
    constraints_bytes = _csv_bytes(_rows(constraints), KEY_VALUE_FIELDS)
    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "journal_contract_sha256": journal["journal_sha256"],
        "admission_ticket_sha256": ticket_sha,
        "epoch_ordinal": 2,
        "window_start": journal["window_start"],
        "window_end": journal["window_end"],
        "receipt_order": receipt_hashes,
        "receipt_count": len(receipts),
        "state": state,
        "policy": config,
        "claims": {
            "first_validator_pass_is_mandatory_acceptance": True,
            "later_valid_response_selection_prohibited": True,
            "recapture_until_matching_prohibited": True,
            "receipt_deletion_prohibited": True,
            "receipt_reclassification_prohibited": True,
            "epoch_2_policy_governed": True,
            "historical_backfill_prohibited": True,
            "current_samples": 160,
            "sample_threshold": 500,
            "profitability_evidence": False,
            "economic_computation_authorized": False,
            "readiness_changed": False,
        },
        "artifacts": {
            "receipts_sha256": _digest(receipts_bytes),
            "state_sha256": _digest(state_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    chain_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-capture-attempt-receipt-chain.{chain_sha}"
    paths = {
        "receipts": output / f"{stem}.receipts.csv",
        "state": output / f"{stem}.state.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    for key, content in (("receipts", receipts_bytes), ("state", state_bytes), ("constraints", constraints_bytes)):
        _commit_bytes(paths[key], content)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "chain_sha256": chain_sha,
        "contract_status": CONTRACT_STATUS,
        "journal_contract_sha256": journal["journal_sha256"],
        "admission_ticket_sha256": ticket_sha,
        "epoch_ordinal": 2,
        "window_start": journal["window_start"],
        "window_end": journal["window_end"],
        **state,
        "network_activity_performed": False,
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
        "identity": identity,
        "artifacts": {
            "receipts": {"filename": paths["receipts"].name, "sha256": identity["artifacts"]["receipts_sha256"], "row_count": len(receipts)},
            "state": {"filename": paths["state"].name, "sha256": identity["artifacts"]["state_sha256"], "row_count": 1},
            "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(_rows(constraints))},
            "report": {"filename": paths["report"].name},
        },
    }
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ReceiptChainResult(report, {key: str(value) for key, value in paths.items()})


def load_receipt_chain_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("receipt chain config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("receipt chain config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("receipt chain config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def validate_receipt_chain_report(path: str | Path) -> dict[str, Any]:
    report = validate_capture_attempt_receipt_chain(path)
    if report.get("receipt_count") != 0 or report.get("chain_status") != "awaiting_attempt":
        raise MarketDataError("receipt chain report is not zero-receipt")
    return report


def validate_capture_attempt_receipt_chain(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if not _receipt_chain_report_dir_allowed(report_path, repo):
        raise MarketDataError("receipt chain report path escape")
    report = _load_json(report_path)
    chain_sha = report.get("chain_sha256")
    identity = report.get("identity")
    if not isinstance(chain_sha, str) or report_path.name != f"prospective-capture-attempt-receipt-chain.{chain_sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != chain_sha:
        raise MarketDataError("receipt chain identity mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("receipt chain artifacts shape mismatch")
    for key in ("receipts", "state", "constraints"):
        artifact = artifacts.get(key)
        expected = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(artifact, dict) or artifact.get("sha256") != expected:
            raise MarketDataError(f"receipt chain artifact metadata mismatch:{key}")
        artifact_path = report_path.parent / str(artifact.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected:
            raise MarketDataError(f"receipt chain artifact bytes mismatch:{key}")
    receipts_path = report_path.parent / str(artifacts["receipts"]["filename"])
    receipts = _read_receipt_rows(receipts_path)
    receipt_hashes = [_receipt_sha(receipt) for receipt in receipts]
    declared_order = identity.get("receipt_order")
    if report.get("contract_status") != CONTRACT_STATUS:
        raise MarketDataError("receipt chain state mismatch")
    if (
        report.get("receipt_count") != len(receipts)
        or identity.get("receipt_count") != len(receipts)
        or declared_order != receipt_hashes
    ):
        raise MarketDataError("receipt chain receipt order mismatch")
    if len(receipts) == 0:
        state = {
            "receipt_count": 0,
            "next_attempt_number": 1,
            "chain_status": "awaiting_attempt",
            "accepted_attempt_count": 0,
            "accepted_attempt_number": None,
            "accepted_snapshot_identity": None,
            "next_attempt_permitted": True,
        }
    else:
        journal_path = repo / "reports/prospective-capture-attempt-journal" / f"prospective-capture-attempt-journal.{report.get('journal_contract_sha256')}.json"
        journal = validate_capture_attempt_journal(journal_path)
        ticket_path = repo / "reports/prospective-epoch-capture-admission" / f"prospective-epoch-capture-admission.{report.get('admission_ticket_sha256')}.json"
        ticket = _load_json(ticket_path)
        request_policy_sha = str(ticket["identity"]["request_policy"]["request_policy_identity"])
        state = _replay_receipts(receipts, identity.get("policy", {}), journal, ticket, request_policy_sha, repo)
    state_fields = (
        "receipt_count",
        "next_attempt_number",
        "chain_status",
        "accepted_attempt_count",
        "accepted_attempt_number",
        "accepted_snapshot_identity",
        "next_attempt_permitted",
    )
    if any(report.get(field) != state.get(field) for field in state_fields) or identity.get("state") != state:
        raise MarketDataError("receipt chain state mismatch")
    if len(receipts) == 0 and receipts_path.read_bytes() != b"\n".join((",".join(RECEIPT_CSV_FIELDS).encode("utf-8"), b"")):
        raise MarketDataError("receipt chain zero-receipt table mismatch")
    return report


def load_validated_capture_attempt_receipts(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = validate_capture_attempt_receipt_chain(path)
    report_path = Path(path).resolve()
    receipts_path = report_path.parent / str(report["artifacts"]["receipts"]["filename"])
    return report, _read_receipt_rows(receipts_path)


def format_receipt_chain_result(result: ReceiptChainResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"chain_sha256: {report['chain_sha256']}",
            f"journal_contract_sha256: {report['journal_contract_sha256']}",
            "epoch_ordinal: 2",
            f"window_start: {report['window_start']}",
            f"window_end: {report['window_end']}",
            f"receipt_count: {report['receipt_count']}",
            f"chain_status: {report['chain_status']}",
            f"next_attempt_number: {report['next_attempt_number']}",
            f"accepted_attempt_count: {report['accepted_attempt_count']}",
            "network_activity_performed: false",
            "current_samples: 160",
            "remaining_samples: 340",
            "economic_computation_authorized: false",
            "readiness_changed: false",
        )
    )


def _validate_config_and_journal(config: dict[str, Any], journal: dict[str, Any]) -> None:
    expected = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "receipt_order": "strict_input_order",
        "attempt_numbering": "strict_monotonic_from_1",
        "receipt_mutability": "immutable",
        "receipt_chain": "append_only",
        "allowed_outcomes": ["transport_failed", "validation_failed", "validation_passed"],
        "validation_pass_implies_accepted": True,
        "accepted_attempt_count_max": 1,
        "retry_after_transport_failed": True,
        "retry_after_validation_failed": True,
        "retry_after_validation_passed": False,
        "retry_after_accepted": False,
        "receipt_deletion_prohibited": True,
        "receipt_reordering_prohibited": True,
        "receipt_reclassification_prohibited": True,
        "response_selection_prohibited": True,
        "sample_threshold": 500,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    if config != expected:
        raise MarketDataError("receipt chain config semantics mismatch")
    if journal.get("journal_status") != "awaiting_future_attempt" or journal.get("attempt_count") != 0 or journal.get("accepted_attempt_count") != 0 or journal.get("current_samples") != 160 or journal.get("remaining_samples") != 340:
        raise MarketDataError("receipt chain base journal must be zero-attempt")


def _replay_receipts(
    receipts: Sequence[dict[str, Any]],
    config: dict[str, Any],
    journal: dict[str, Any],
    ticket: dict[str, Any],
    request_policy_sha: str,
    repo: Path,
) -> dict[str, Any]:
    accepted = False
    accepted_number: int | None = None
    accepted_identity: str | None = None
    previous_sha: str | None = None
    for number, receipt in enumerate(receipts, start=1):
        _validate_receipt_shape(receipt)
        if receipt["schema_version"] != SCHEMA_VERSION or receipt["receipt_policy_id"] != POLICY_ID or receipt["journal_contract_sha256"] != journal["journal_sha256"] or receipt["admission_ticket_sha256"] != ticket["ticket_sha256"] or receipt["epoch_ordinal"] != 2 or receipt["attempt_number"] != number:
            raise MarketDataError("receipt chain binding or numbering mismatch")
        if receipt["previous_receipt_sha256"] != previous_sha:
            raise MarketDataError("receipt chain previous hash mismatch")
        started = _parse_utc(str(receipt["attempt_started_at"]))
        if started < _parse_utc(journal["window_start"]) or started >= _parse_utc(journal["window_end"]):
            raise MarketDataError("receipt attempt outside governed window")
        if receipt["request_policy_sha256"] != request_policy_sha:
            raise MarketDataError("receipt request policy identity mismatch")
        if accepted:
            raise MarketDataError("receipt after accepted attempt")
        _validate_outcome(receipt, repo, journal, ticket)
        if receipt["outcome"] == "validation_passed":
            accepted = True
            accepted_number = number
            accepted_identity = str(receipt["snapshot_identity"])
        previous_sha = _receipt_sha(receipt)
    return {
        "receipt_count": len(receipts),
        "next_attempt_number": len(receipts) + 1,
        "chain_status": "accepted_closed" if accepted else ("retry_open" if receipts else "awaiting_attempt"),
        "accepted_attempt_count": 1 if accepted else 0,
        "accepted_attempt_number": accepted_number,
        "accepted_snapshot_identity": accepted_identity,
        "next_attempt_permitted": not accepted,
    }


def _validate_receipt_shape(receipt: dict[str, Any]) -> None:
    missing = RECEIPT_HASH_FIELDS - set(receipt)
    extra = set(receipt) - RECEIPT_HASH_FIELDS
    if missing or extra:
        raise MarketDataError("receipt schema fields mismatch")
    if receipt.get("snapshot_marker_path") is not None and not isinstance(receipt.get("snapshot_marker_path"), str):
        raise MarketDataError("receipt snapshot marker path mismatch")
    if not isinstance(receipt.get("attempt_number"), int) or isinstance(receipt.get("attempt_number"), bool):
        raise MarketDataError("receipt attempt number mismatch")
    if receipt.get("accepted") not in (True, False) or receipt.get("response_received") not in (True, False):
        raise MarketDataError("receipt boolean field mismatch")


def _validate_outcome(receipt: dict[str, Any], repo: Path, journal: dict[str, Any], ticket: dict[str, Any]) -> None:
    outcome = receipt["outcome"]
    if outcome not in ALLOWED_OUTCOMES:
        raise MarketDataError("receipt outcome mismatch")
    if outcome == "transport_failed":
        if receipt["response_received"] is not False or receipt["accepted"] is not False or receipt["validator_status"] != "not_run" or not isinstance(receipt["failure_code"], str) or not receipt["failure_code"] or any(receipt.get(key) not in (None, "") for key in ("response_sha256", "snapshot_marker_sha256", "snapshot_identity", "snapshot_marker_path")):
            raise MarketDataError("transport failure receipt semantics mismatch")
        return
    if outcome == "validation_failed":
        if receipt["response_received"] is not True or receipt["accepted"] is not False or receipt["validator_status"] != "failed" or not isinstance(receipt["failure_code"], str) or not receipt["failure_code"] or not _is_sha256(receipt.get("response_sha256")) or any(receipt.get(key) not in (None, "") for key in ("snapshot_marker_sha256", "snapshot_identity", "snapshot_marker_path")):
            raise MarketDataError("validation failure receipt semantics mismatch")
        return
    if receipt["response_received"] is not True or receipt["accepted"] is not True or receipt["validator_status"] != "passed" or receipt["failure_code"] not in (None, "") or not _is_sha256(receipt.get("response_sha256")) or not _is_sha256(receipt.get("snapshot_marker_sha256")) or not isinstance(receipt.get("snapshot_identity"), str) or not receipt["snapshot_identity"] or not isinstance(receipt.get("snapshot_marker_path"), str) or not receipt["snapshot_marker_path"]:
        raise MarketDataError("validation pass receipt semantics mismatch")
    _validate_snapshot_marker(repo, receipt, journal, ticket)


def _validate_snapshot_marker(repo: Path, receipt: dict[str, Any], journal: dict[str, Any], ticket: dict[str, Any]) -> None:
    marker_value = Path(str(receipt["snapshot_marker_path"]))
    marker = (repo / marker_value).resolve() if not marker_value.is_absolute() else marker_value.resolve()
    if not marker.is_relative_to(repo.resolve()) or not marker.is_file():
        raise MarketDataError("receipt snapshot marker path escape")
    config = repo / SNAPSHOT_CONFIG_FILENAME
    validate_future_universe_snapshot(marker, config)
    report = _load_json(marker)
    if report.get("snapshot_sha256") != receipt["snapshot_marker_sha256"] or receipt["snapshot_identity"] != report.get("snapshot_sha256"):
        raise MarketDataError("receipt snapshot marker identity mismatch")
    received_at = _parse_utc(str(report.get("identity", {}).get("received_at", "")))
    if received_at < _parse_utc(journal["window_start"]) or received_at >= _parse_utc(journal["window_end"]):
        raise MarketDataError("receipt snapshot received outside governed window")
    if ticket.get("ticket_status") != "pending_future_window":
        raise MarketDataError("receipt ticket is not pending")


def _receipt_sha(receipt: Mapping[str, Any]) -> str:
    return _digest(_canonical_json_bytes(receipt))


def _receipt_csv_bytes(receipts: Sequence[dict[str, Any]], hashes: Sequence[str]) -> bytes:
    rows: list[dict[str, Any]] = []
    for receipt, receipt_hash in zip(receipts, hashes):
        row = {"receipt_sha256": receipt_hash}
        row.update({field: _csv_value(receipt.get(field)) for field in RECEIPT_FIELDS})
        rows.append(row)
    return _csv_bytes(rows, RECEIPT_CSV_FIELDS)


def _read_receipt_rows(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(RECEIPT_CSV_FIELDS):
                raise MarketDataError("receipt chain receipt table schema mismatch")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("receipt chain receipt table read failed") from exc
    receipts: list[dict[str, Any]] = []
    for row in rows:
        if any(row.get(field) is None for field in RECEIPT_CSV_FIELDS):
            raise MarketDataError("receipt chain receipt table row mismatch")
        receipt: dict[str, Any] = {}
        for field in RECEIPT_FIELDS:
            value = row[field]
            if field in {"schema_version", "epoch_ordinal", "attempt_number"}:
                try:
                    receipt[field] = int(value)
                except ValueError as exc:
                    raise MarketDataError("receipt chain receipt integer mismatch") from exc
            elif field in {"response_received", "accepted"}:
                if value not in {"true", "false"}:
                    raise MarketDataError("receipt chain receipt boolean mismatch")
                receipt[field] = value == "true"
            elif field in {"previous_receipt_sha256", "response_sha256", "snapshot_marker_sha256", "snapshot_identity", "failure_code", "snapshot_marker_path"} and value == "":
                receipt[field] = None
            else:
                receipt[field] = value
        if row["receipt_sha256"] != _receipt_sha(receipt):
            raise MarketDataError("receipt chain receipt hash mismatch")
        receipts.append(receipt)
    return receipts


def _rows(values: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True) if not isinstance(values[key], str) else values[key]} for key in sorted(values)]


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
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
        raise MarketDataError("receipt timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataError("receipt timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("receipt chain repo root not found")


def _receipt_chain_report_dir_allowed(path: Path, repo: Path) -> bool:
    parent = path.parent
    canonical = (repo / "reports" / "prospective-capture-attempt-receipt-chain").resolve()
    return parent == canonical or (
        parent.parent == (repo / "reports").resolve()
        and parent.name.startswith("prospective-capture-attempt-receipt-chain-")
    )


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("receipt chain output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("receipt chain json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("receipt chain json shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"receipt chain content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".receipt-chain-", suffix=".tmp", delete=False) as handle:
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
