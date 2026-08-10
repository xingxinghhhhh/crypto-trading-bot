from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.okx_future_universe_archive import (
    validate_future_universe_snapshot,
)
from crypto_bot.market.prospective_capture_attempt_journal import (
    validate_capture_attempt_journal,
)
from crypto_bot.market.prospective_capture_attempt_receipt_chain import (
    audit_prospective_capture_attempt_receipt_chain,
    load_validated_capture_attempt_receipts,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_capture_attempt_evidence_adapter_v1"
CONTRACT_STATUS = "verified_prospective_capture_attempt_evidence_materialization"
DEFAULT_CONFIG_FILENAME = "config.prospective-capture-attempt-evidence-adapter.example.yaml"
RECEIPT_CHAIN_CONFIG_FILENAME = "config.prospective-epoch-capture-attempt-receipt-chain.example.yaml"
SNAPSHOT_CONFIG_FILENAME = "config.okx-future-universe-archive.example.yaml"
EVIDENCE_KINDS = (
    "transport_failure",
    "snapshot_validation_failure",
    "snapshot_validation_pass",
)
EVIDENCE_FIELDS = (
    "schema_version",
    "evidence_kind",
    "attempt_started_at",
    "request_policy_sha256",
    "failure_code",
    "exception_class",
    "response_artifact_path",
    "snapshot_marker_path",
)
VALIDATION_FIELDS = (
    "parent_chain_valid",
    "journal_valid",
    "ticket_valid",
    "window_valid",
    "request_policy_valid",
    "snapshot_validator_status",
    "derived_attempt_number",
    "receipt_chain_replay_valid",
)
CONSTRAINT_FIELDS = (
    "network_activity_performed",
    "real_capture_performed",
    "new_samples_counted",
    "current_samples",
    "remaining_samples",
    "turnover_rows",
    "cost_amount_rows",
    "capacity_pass_fail_rows",
    "return_rows",
    "pnl_rows",
    "economic_computation_authorized",
    "pnl_computation_authorized",
    "readiness_changed",
)


class ReceiptMaterializationResult:
    def __init__(self, report: dict[str, Any], export_paths: dict[str, str]) -> None:
        self.report = report
        self.export_paths = export_paths


def materialize_prospective_capture_attempt_receipt(
    receipt_chain: str | Path,
    attempt_evidence: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ReceiptMaterializationResult:
    parent_path = Path(receipt_chain).resolve()
    repo = _repo_root(parent_path)
    config = load_evidence_adapter_config(config_path, repo)
    chain_report, parent_receipts = load_validated_capture_attempt_receipts(parent_path)
    _validate_parent_chain(chain_report)
    journal_path = repo / "reports/prospective-capture-attempt-journal" / (
        f"prospective-capture-attempt-journal.{chain_report['journal_contract_sha256']}.json"
    )
    journal = validate_capture_attempt_journal(journal_path)
    ticket_path = repo / "reports/prospective-epoch-capture-admission" / (
        f"prospective-epoch-capture-admission.{chain_report['admission_ticket_sha256']}.json"
    )
    ticket = _load_json(ticket_path)
    evidence_path = Path(attempt_evidence).resolve()
    evidence = _load_json(evidence_path)
    _validate_evidence_shape(evidence)
    request_policy_sha = str(ticket["identity"]["request_policy"]["request_policy_identity"])
    receipt, snapshot_report = _build_receipt(
        evidence,
        chain_report,
        journal,
        ticket,
        request_policy_sha,
        repo,
    )
    receipt_sha = _digest(_canonical_json_bytes(receipt))

    validation_values: dict[str, Any] = {
        "parent_chain_valid": True,
        "journal_valid": True,
        "ticket_valid": True,
        "window_valid": True,
        "request_policy_valid": True,
        "snapshot_validator_status": "passed" if snapshot_report is not None else "not_run",
        "derived_attempt_number": receipt["attempt_number"],
        "receipt_chain_replay_valid": True,
    }
    constraints: dict[str, Any] = {
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
    }
    receipt_bytes = _pretty_json_bytes(receipt)
    validation_bytes = _csv_bytes(validation_values, VALIDATION_FIELDS)
    constraints_bytes = _csv_bytes(constraints, CONSTRAINT_FIELDS)
    evidence_sha = _digest(_canonical_json_bytes(_normalized_evidence(evidence, repo)))
    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "parent_receipt_chain_sha256": chain_report["chain_sha256"],
        "parent_journal_contract_sha256": chain_report["journal_contract_sha256"],
        "parent_admission_ticket_sha256": chain_report["admission_ticket_sha256"],
        "evidence_sha256": evidence_sha,
        "evidence_kind": evidence["evidence_kind"],
        "derived_attempt_number": receipt["attempt_number"],
        "previous_receipt_sha256": receipt["previous_receipt_sha256"],
        "generated_receipt_sha256": receipt_sha,
        "snapshot_marker_sha256": receipt["snapshot_marker_sha256"],
        "policy": config,
        "claims": {
            "fixture_only": True,
            "market_evidence": False,
            "sample_evidence": False,
            "profitability_evidence": False,
            "network_access_prohibited": True,
            "manual_outcome_override_prohibited": True,
            "manual_acceptance_override_prohibited": True,
            "economic_computation_authorized": False,
            "readiness_changed": False,
        },
        "artifacts": {
            "receipt_sha256": _digest(receipt_bytes),
            "validation_sha256": _digest(validation_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    materialization_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-capture-attempt-receipt.{materialization_sha}"
    paths = {
        "receipt": output / f"{stem}.receipt.json",
        "validation": output / f"{stem}.validation.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }

    # Replay through the existing chain validator before committing any final marker.
    with tempfile.TemporaryDirectory(dir=repo / "reports", prefix=".receipt-materialization-") as replay_dir:
        replay_root = Path(replay_dir)
        replay_receipts: list[Path] = []
        for index, parent_receipt in enumerate(parent_receipts, start=1):
            parent_path = replay_root / f"parent-{index}.json"
            parent_path.write_bytes(_pretty_json_bytes(parent_receipt))
            replay_receipts.append(parent_path)
        replay_receipt = replay_root / f"receipt-{receipt['attempt_number']}.json"
        replay_receipt.write_bytes(receipt_bytes)
        replay_receipts.append(replay_receipt)
        replay = audit_prospective_capture_attempt_receipt_chain(
            journal_path,
            replay_receipts,
            repo / RECEIPT_CHAIN_CONFIG_FILENAME,
            replay_root / "chain",
        )
        if replay.report.get("receipt_count") != len(replay_receipts) or replay.report.get("chain_status") != (
            "accepted_closed" if receipt["accepted"] else "retry_open"
        ):
            raise MarketDataError("receipt chain replay state mismatch")

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "materialization_sha256": materialization_sha,
        "contract_status": CONTRACT_STATUS,
        "parent_receipt_chain_sha256": chain_report["chain_sha256"],
        "parent_receipt_count": chain_report["receipt_count"],
        "next_attempt_number": receipt["attempt_number"],
        "real_attempt_materialized": False,
        "network_activity_performed": False,
        "current_samples": 160,
        "remaining_samples": 340,
        "new_samples_counted": 0,
        "fixture_only": True,
        "market_evidence": False,
        "sample_evidence": False,
        "profitability_evidence": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "identity": identity,
        "artifacts": {
            "receipt": {"filename": paths["receipt"].name, "sha256": identity["artifacts"]["receipt_sha256"]},
            "validation": {"filename": paths["validation"].name, "sha256": identity["artifacts"]["validation_sha256"]},
            "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"]},
            "report": {"filename": paths["report"].name},
        },
    }
    # Marker-last: receipt, validation and constraints are committed before the report.
    _commit_bytes(paths["receipt"], receipt_bytes)
    _commit_bytes(paths["validation"], validation_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ReceiptMaterializationResult(report, {key: str(value) for key, value in paths.items()})


def load_evidence_adapter_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("evidence adapter config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("evidence adapter config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("evidence adapter config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def validate_receipt_materialization(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if report_path.parent != (repo / "reports" / "prospective-capture-attempt-evidence-adapter").resolve() and not report_path.parent.name.startswith("prospective-capture-attempt-evidence-adapter"):
        raise MarketDataError("receipt materialization report path escape")
    report = _load_json(report_path)
    materialization_sha = report.get("materialization_sha256")
    identity = report.get("identity")
    if not isinstance(materialization_sha, str) or report_path.name != f"prospective-capture-attempt-receipt.{materialization_sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != materialization_sha:
        raise MarketDataError("receipt materialization identity mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("receipt materialization artifacts shape mismatch")
    for key in ("receipt", "validation", "constraints"):
        artifact = artifacts.get(key)
        expected = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(artifact, dict) or artifact.get("sha256") != expected:
            raise MarketDataError(f"receipt materialization artifact metadata mismatch:{key}")
        artifact_path = report_path.parent / str(artifact.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected:
            raise MarketDataError(f"receipt materialization artifact bytes mismatch:{key}")
    receipt = _load_json(report_path.parent / str(artifacts["receipt"]["filename"]))
    if _digest(_canonical_json_bytes(receipt)) != identity.get("generated_receipt_sha256"):
        raise MarketDataError("receipt materialization receipt identity mismatch")
    if report.get("fixture_only") is not True or report.get("market_evidence") is not False or report.get("real_attempt_materialized") is not False or report.get("network_activity_performed") is not False or report.get("current_samples") != 160 or report.get("remaining_samples") != 340:
        raise MarketDataError("receipt materialization safety state mismatch")
    return report


def format_receipt_materialization_result(result: ReceiptMaterializationResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"materialization_sha256: {report['materialization_sha256']}",
            f"parent_receipt_chain_sha256: {report['parent_receipt_chain_sha256']}",
            f"next_attempt_number: {report['next_attempt_number']}",
            "real_attempt_materialized: false",
            "network_activity_performed: false",
            "fixture_only: true",
            "market_evidence: false",
            "current_samples: 160",
            "remaining_samples: 340",
            "economic_computation_authorized: false",
            "readiness_changed: false",
        )
    )


def _validate_parent_chain(report: dict[str, Any]) -> None:
    if report.get("contract_status") != "verified_prospective_capture_attempt_receipt_chain":
        raise MarketDataError("receipt materialization parent chain is not appendable")
    identity = report.get("identity")
    if not isinstance(identity, dict) or not isinstance(report.get("receipt_count"), int):
        raise MarketDataError("receipt materialization parent chain tail mismatch")
    if report["receipt_count"] == 0:
        valid = (
            report.get("chain_status") == "awaiting_attempt"
            and report.get("next_attempt_number") == 1
            and report.get("next_attempt_permitted") is True
            and report.get("accepted_attempt_count") == 0
            and report.get("accepted_snapshot_identity") is None
            and identity.get("receipt_order") == []
        )
    else:
        valid = (
            report.get("chain_status") == "retry_open"
            and report.get("next_attempt_number") == report["receipt_count"] + 1
            and report.get("next_attempt_permitted") is True
            and report.get("accepted_attempt_count") == 0
            and isinstance(identity.get("receipt_order"), list)
            and len(identity["receipt_order"]) == report["receipt_count"]
        )
    if not valid:
        raise MarketDataError("receipt materialization parent chain is not retry-open")


def _validate_evidence_shape(evidence: dict[str, Any]) -> None:
    if set(evidence) != set(EVIDENCE_FIELDS):
        raise MarketDataError("capture evidence schema fields mismatch")
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise MarketDataError("capture evidence schema version mismatch")
    if evidence.get("evidence_kind") not in EVIDENCE_KINDS:
        raise MarketDataError("capture evidence kind mismatch")
    if not isinstance(evidence.get("attempt_started_at"), str) or not evidence["attempt_started_at"]:
        raise MarketDataError("capture evidence timestamp missing")
    if not isinstance(evidence.get("request_policy_sha256"), str) or not _is_sha256(evidence["request_policy_sha256"]):
        raise MarketDataError("capture evidence request policy mismatch")
    for key in ("failure_code", "exception_class", "response_artifact_path", "snapshot_marker_path"):
        value = evidence.get(key)
        if value is not None and not isinstance(value, str):
            raise MarketDataError(f"capture evidence field mismatch:{key}")
    kind = evidence["evidence_kind"]
    if kind == "transport_failure":
        if not isinstance(evidence.get("failure_code"), str) or not evidence["failure_code"] or any(evidence.get(key) not in (None, "") for key in ("response_artifact_path", "snapshot_marker_path")):
            raise MarketDataError("transport evidence semantics mismatch")
    elif kind == "snapshot_validation_failure":
        if not isinstance(evidence.get("failure_code"), str) or not evidence["failure_code"] or not isinstance(evidence.get("response_artifact_path"), str) or not evidence["response_artifact_path"] or evidence.get("snapshot_marker_path") not in (None, ""):
            raise MarketDataError("validation failure evidence semantics mismatch")
    elif evidence.get("failure_code") not in (None, "") or not isinstance(evidence.get("response_artifact_path"), str) or not evidence["response_artifact_path"] or not isinstance(evidence.get("snapshot_marker_path"), str) or not evidence["snapshot_marker_path"]:
        raise MarketDataError("validation pass evidence semantics mismatch")


def _build_receipt(
    evidence: dict[str, Any],
    chain_report: dict[str, Any],
    journal: dict[str, Any],
    ticket: dict[str, Any],
    request_policy_sha: str,
    repo: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if evidence["request_policy_sha256"] != request_policy_sha:
        raise MarketDataError("capture evidence request policy identity mismatch")
    started = _parse_utc(evidence["attempt_started_at"])
    if started < _parse_utc(str(journal["window_start"])) or started >= _parse_utc(str(journal["window_end"])):
        raise MarketDataError("capture evidence outside governed window")
    response_sha: str | None = None
    snapshot_sha: str | None = None
    snapshot_identity: str | None = None
    snapshot_path: str | None = None
    snapshot_report: dict[str, Any] | None = None
    response_path = evidence.get("response_artifact_path")
    if response_path not in (None, ""):
        response_file = _repo_path(repo, str(response_path))
        response_sha = _digest(response_file.read_bytes())
    if evidence["evidence_kind"] == "snapshot_validation_pass":
        marker = _repo_path(repo, str(evidence["snapshot_marker_path"]))
        validated = validate_future_universe_snapshot(marker, repo / SNAPSHOT_CONFIG_FILENAME)
        snapshot_report = validated.report
        snapshot_sha = str(snapshot_report["snapshot_sha256"])
        snapshot_identity = snapshot_sha
        snapshot_path = marker.relative_to(repo.resolve()).as_posix()
    if evidence["evidence_kind"] == "transport_failure":
        outcome = "transport_failed"
        response_received = False
        validator_status = "not_run"
        accepted = False
        failure_code: str | None = str(evidence["failure_code"])
    elif evidence["evidence_kind"] == "snapshot_validation_failure":
        outcome = "validation_failed"
        response_received = True
        validator_status = "failed"
        accepted = False
        failure_code = str(evidence["failure_code"])
    else:
        outcome = "validation_passed"
        response_received = True
        validator_status = "passed"
        accepted = True
        failure_code = None
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "receipt_policy_id": "prospective_capture_attempt_receipt_chain_v1",
        "journal_contract_sha256": chain_report["journal_contract_sha256"],
        "admission_ticket_sha256": chain_report["admission_ticket_sha256"],
        "epoch_ordinal": 2,
        "attempt_number": chain_report["next_attempt_number"],
        "previous_receipt_sha256": (
            str(chain_report["identity"]["receipt_order"][-1])
            if chain_report["identity"]["receipt_order"]
            else None
        ),
        "attempt_started_at": evidence["attempt_started_at"],
        "outcome": outcome,
        "request_policy_sha256": request_policy_sha,
        "response_received": response_received,
        "response_sha256": response_sha,
        "snapshot_marker_sha256": snapshot_sha,
        "snapshot_identity": snapshot_identity,
        "validator_status": validator_status,
        "failure_code": failure_code,
        "accepted": accepted,
        "snapshot_marker_path": snapshot_path,
    }
    return receipt, snapshot_report


def _normalized_evidence(evidence: Mapping[str, Any], repo: Path) -> dict[str, Any]:
    normalized = dict(evidence)
    for field in ("response_artifact_path", "snapshot_marker_path"):
        value = normalized.get(field)
        if value not in (None, ""):
            normalized[field] = _repo_path(repo, str(value)).relative_to(repo.resolve()).as_posix()
    return normalized


def _repo_path(repo: Path, value: str) -> Path:
    path = Path(value)
    candidate = (repo / path).resolve() if not path.is_absolute() else path.resolve()
    if not candidate.is_relative_to(repo.resolve()) or not candidate.is_file():
        raise MarketDataError("capture evidence artifact path escape or missing")
    return candidate


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("capture evidence timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataError("capture evidence timestamp must be timezone-aware")
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
    for parent in (path.parent, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent.resolve()
    raise MarketDataError("receipt materialization repo root not found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("receipt materialization output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("receipt materialization json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("receipt materialization json shape mismatch")
    return value


def _csv_bytes(values: Mapping[str, Any], fields: tuple[str, ...]) -> bytes:
    lines = [",".join(fields)]
    for field in fields:
        value = values[field]
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value
        lines.append(f"{field},{rendered}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"receipt materialization content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".receipt-materialization-", suffix=".tmp", delete=False) as handle:
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
