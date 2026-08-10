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
POLICY_ID = "prospective_epoch_snapshot_admission_v1"
CONTRACT_STATUS = "verified_prospective_epoch_snapshot_admission_ticket"
DEFAULT_CONFIG_FILENAME = "config.prospective-epoch-capture-admission.example.yaml"
ASSEMBLY_SHA = "674116b95e03705b478f285012148f0143c2ef82befccda5627ce2ab901bcc79"
SNAPSHOT_SHA = "d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e"
TICKET_FIELDS = ("epoch_ordinal", "current_state", "window_start", "window_end", "previous_snapshot_identity", "next_segment_start", "accepted_snapshot_policy", "max_accepted_snapshots", "ticket_status")
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class CaptureAdmissionResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_prospective_epoch_capture_admission(
    assembly_report: str | Path, config_path: str | Path, output_dir: str | Path
) -> CaptureAdmissionResult:
    assembly_path = Path(assembly_report).resolve()
    repo = _repo_root(assembly_path)
    config = load_capture_admission_config(config_path, repo)
    assembly = _marker(assembly_path, "prospective-epoch-assembly", repo)
    _validate_assembly(assembly, assembly_path)
    snapshot = _snapshot_request_policy(repo)
    identity = assembly["identity"]
    ticket_row = {"epoch_ordinal": 2, "current_state": "awaiting_capture_window", "window_start": assembly["next_capture_window_start"], "window_end": assembly["next_capture_window_end"], "previous_snapshot_identity": SNAPSHOT_SHA, "next_segment_start": assembly["expected_next_market_segment_start"], "accepted_snapshot_policy": config["accepted_snapshot_policy"], "max_accepted_snapshots": 1, "ticket_status": "pending_future_window"}
    request_values = {"endpoint_class": "OKX public instruments", "endpoint": snapshot["endpoint"], "instrument_type": snapshot["request_params"]["instType"], "authentication": "none", "account_context": "none", "public_only": True, "private_api_prohibited": True, "authentication_prohibited": True, "request_policy_identity": snapshot["policy_file_sha256"], "accepted_snapshot_policy": config["accepted_snapshot_policy"], "retry_policy": config["retry_policy"], "retry_after_valid_capture": False, "out_of_window_policy": config["out_of_window_policy"], "missed_window_policy": config["missed_window_policy"], "ticket_reuse_policy": config["ticket_reuse_policy"]}
    constraint_values = {"current_samples": 160, "remaining_samples": 340, "sample_maturity_met": False, "accepted_snapshot_count": 0, "new_samples_counted": 0, "network_activity_performed": False, "snapshot_created": False, "economic_rows": 0, "historical_backfill_prohibited": True, "economic_computation_authorized": False, "profitability_evidence": False, "readiness_changed": False}
    ticket_bytes = _csv_bytes([ticket_row], TICKET_FIELDS)
    request_bytes = _csv_bytes(_rows(request_values), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraint_values), KEY_VALUE_FIELDS)
    ticket_identity = {"schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID, "assembly_sha256": assembly["assembly_sha256"], "assembly_identity_sha256": _digest(_canonical_json_bytes(identity)), "epoch_ordinal": 2, "current_state": "awaiting_capture_window", "window_start": assembly["next_capture_window_start"], "window_end": assembly["next_capture_window_end"], "previous_snapshot_sha256": SNAPSHOT_SHA, "next_segment_start": assembly["expected_next_market_segment_start"], "request_policy": request_values, "claims": {"epoch_2_is_first_fully_policy_governed_epoch": True, "first_epoch_was_not_collected_under_cadence_policy": True, "capture_time_result_driven": False, "valid_capture_version_selection_prohibited": True, "historical_backfill_prohibited": True, "future_only_membership_evidence": True, "historical_point_in_time_membership": False, "survivorship_bias_resolved": False, "sample_threshold": 500, "profitability_evidence": False, "economic_computation_authorized": False, "readiness_changed": False}, "artifacts": {"ticket_sha256": _digest(ticket_bytes), "request_policy_sha256": _digest(request_bytes), "constraints_sha256": _digest(constraints_bytes)}}
    ticket_sha = _digest(_canonical_json_bytes(ticket_identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-epoch-capture-admission.{ticket_sha}"
    paths = {"ticket": output / f"{stem}.ticket.csv", "request_policy": output / f"{stem}.request-policy.csv", "constraints": output / f"{stem}.constraints.csv", "report": output / f"{stem}.json"}
    for key, content in (("ticket", ticket_bytes), ("request_policy", request_bytes), ("constraints", constraints_bytes)):
        _commit_bytes(paths[key], content)
    report = {"schema_version": SCHEMA_VERSION, "ticket_sha256": ticket_sha, "contract_status": CONTRACT_STATUS, "epoch_ordinal": 2, "current_state": "awaiting_capture_window", "window_start": assembly["next_capture_window_start"], "window_end": assembly["next_capture_window_end"], "previous_snapshot": SNAPSHOT_SHA, "next_segment_start": assembly["expected_next_market_segment_start"], "ticket_status": "pending_future_window", "current_samples": 160, "remaining_samples": 340, "sample_maturity_met": False, "accepted_snapshot_count": 0, "new_samples_counted": 0, "network_activity_performed": False, "snapshot_created": False, "economic_rows": 0, "identity": ticket_identity, "artifacts": {"ticket": {"filename": paths["ticket"].name, "sha256": ticket_identity["artifacts"]["ticket_sha256"], "row_count": 1}, "request_policy": {"filename": paths["request_policy"].name, "sha256": ticket_identity["artifacts"]["request_policy_sha256"], "row_count": len(_rows(request_values))}, "constraints": {"filename": paths["constraints"].name, "sha256": ticket_identity["artifacts"]["constraints_sha256"], "row_count": len(_rows(constraint_values))}, "report": {"filename": paths["report"].name}}, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return CaptureAdmissionResult(report, {key: str(value) for key, value in paths.items()})


def load_capture_admission_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("capture admission config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("capture admission config mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def validate_capture_admission_ticket(path: str | Path) -> dict[str, Any]:
    """Replay and validate one immutable epoch-2 admission ticket marker."""

    ticket_path = Path(path).resolve()
    repo = _repo_root(ticket_path)
    if ticket_path.parent != (repo / "reports" / "prospective-epoch-capture-admission").resolve():
        raise MarketDataError("capture admission ticket path escape")
    report = _load_json(ticket_path)
    ticket_sha = report.get("ticket_sha256")
    if not isinstance(ticket_sha, str) or ticket_path.name != f"prospective-epoch-capture-admission.{ticket_sha}.json":
        raise MarketDataError("capture admission ticket filename mismatch")
    identity = report.get("identity")
    if not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != ticket_sha:
        raise MarketDataError("capture admission ticket identity mismatch")
    if (
        report.get("schema_version") != SCHEMA_VERSION
        or report.get("contract_status") != CONTRACT_STATUS
        or report.get("ticket_status") != "pending_future_window"
        or report.get("epoch_ordinal") != 2
        or report.get("current_state") != "awaiting_capture_window"
        or report.get("window_start") != "2026-08-16T10:00:00Z"
        or report.get("window_end") != "2026-08-16T11:00:00Z"
        or report.get("previous_snapshot") != SNAPSHOT_SHA
        or report.get("next_segment_start") != "2026-08-09T11:00:00Z"
        or report.get("current_samples") != 160
        or report.get("remaining_samples") != 340
        or report.get("sample_maturity_met") is not False
        or report.get("accepted_snapshot_count") != 0
        or report.get("new_samples_counted") != 0
        or report.get("network_activity_performed") is not False
        or report.get("snapshot_created") is not False
        or report.get("economic_rows") != 0
        or report.get("economic_computation_authorized") is not False
        or report.get("pnl_computation_authorized") is not False
        or report.get("readiness_changed") is not False
    ):
        raise MarketDataError("capture admission ticket state mismatch")
    expected_identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "assembly_sha256": ASSEMBLY_SHA,
        "assembly_identity_sha256": ASSEMBLY_SHA,
        "epoch_ordinal": 2,
        "current_state": "awaiting_capture_window",
        "window_start": "2026-08-16T10:00:00Z",
        "window_end": "2026-08-16T11:00:00Z",
        "previous_snapshot_sha256": SNAPSHOT_SHA,
        "next_segment_start": "2026-08-09T11:00:00Z",
    }
    for key, expected in expected_identity.items():
        if identity.get(key) != expected:
            raise MarketDataError(f"capture admission ticket identity field mismatch:{key}")
    assembly_path = repo / f"reports/prospective-epoch-assembly/prospective-epoch-assembly.{ASSEMBLY_SHA}.json"
    assembly = _marker(assembly_path, "prospective-epoch-assembly", repo)
    _validate_assembly(assembly, assembly_path)
    snapshot = _snapshot_request_policy(repo)
    request_policy = identity.get("request_policy")
    if not isinstance(request_policy, dict):
        raise MarketDataError("capture admission request policy shape mismatch")
    if (
        request_policy.get("endpoint") != snapshot["endpoint"]
        or request_policy.get("request_policy_identity") != snapshot["policy_file_sha256"]
        or request_policy.get("public_only") is not True
        or request_policy.get("authentication") != "none"
        or request_policy.get("account_context") != "none"
        or request_policy.get("retry_after_valid_capture") is not False
        or request_policy.get("out_of_window_policy") != "reject"
        or request_policy.get("missed_window_policy") != "no_backfill"
        or request_policy.get("ticket_reuse_policy") != "single_epoch_only"
    ):
        raise MarketDataError("capture admission request policy mismatch")
    claims = identity.get("claims")
    if not isinstance(claims, dict) or any(
        claims.get(key) != expected
        for key, expected in {
            "capture_time_result_driven": False,
            "valid_capture_version_selection_prohibited": True,
            "historical_backfill_prohibited": True,
            "sample_threshold": 500,
            "economic_computation_authorized": False,
            "readiness_changed": False,
        }.items()
    ):
        raise MarketDataError("capture admission ticket bias claims mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("capture admission ticket artifacts shape mismatch")
    for key in ("ticket", "request_policy", "constraints"):
        artifact = artifacts.get(key)
        expected_sha = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(artifact, dict) or artifact.get("sha256") != expected_sha:
            raise MarketDataError(f"capture admission ticket artifact metadata mismatch:{key}")
        artifact_path = ticket_path.parent / str(artifact.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected_sha:
            raise MarketDataError(f"capture admission ticket artifact bytes mismatch:{key}")
    return report


def format_capture_admission_result(result: CaptureAdmissionResult) -> str:
    report = result.report
    return "\n".join((f"contract_status: {report['contract_status']}", f"ticket_sha256: {report['ticket_sha256']}", "epoch_ordinal: 2", "current_state: awaiting_capture_window", f"window_start: {report['window_start']}", f"window_end: {report['window_end']}", f"previous_snapshot: {report['previous_snapshot']}", f"next_segment_start: {report['next_segment_start']}", "ticket_status: pending_future_window", "current_samples: 160", "remaining_samples: 340", "accepted_snapshot_count: 0", "network_activity_performed: false", "snapshot_created: false", "economic_computation_authorized: false", "readiness_changed: false"))


def _validate_assembly(assembly: dict[str, Any], path: Path) -> None:
    if path.parent.name != "prospective-epoch-assembly" or path.name != f"prospective-epoch-assembly.{ASSEMBLY_SHA}.json" or assembly.get("assembly_sha256") != ASSEMBLY_SHA or _digest(_canonical_json_bytes(assembly.get("identity", {}))) != ASSEMBLY_SHA:
        raise MarketDataError("capture admission assembly identity mismatch")
    if assembly.get("current_state") != "awaiting_capture_window" or assembly.get("next_epoch_ordinal") != 2 or assembly.get("current_samples") != 160 or assembly.get("remaining_samples") != 340 or assembly.get("market_segment_end_resolved") is not False or assembly.get("epoch_2_countable") is not False:
        raise MarketDataError("capture admission assembly state mismatch")
    if assembly.get("next_capture_window_start") != "2026-08-16T10:00:00Z" or assembly.get("next_capture_window_end") != "2026-08-16T11:00:00Z" or assembly.get("expected_previous_snapshot") != SNAPSHOT_SHA or assembly.get("expected_next_market_segment_start") != "2026-08-09T11:00:00Z":
        raise MarketDataError("capture admission assembly derivation mismatch")


def _snapshot_request_policy(repo: Path) -> dict[str, Any]:
    path = repo / f"reports/okx-future-universe-snapshot/okx-universe-snapshot.{SNAPSHOT_SHA}.json"
    snapshot = _load_json(path)
    identity = snapshot.get("identity", {})
    if snapshot.get("snapshot_sha256") != SNAPSHOT_SHA and snapshot.get("identity", {}).get("snapshot_sha256") != SNAPSHOT_SHA:
        if snapshot.get("capture_status") != "complete_okx_future_only_universe_snapshot":
            raise MarketDataError("capture admission snapshot identity mismatch")
    if identity.get("endpoint") != "https://www.okx.com/api/v5/public/instruments" or identity.get("request_params") != {"instType": "SPOT"} or identity.get("policy_id") != "okx_future_only_membership_archive_v1":
        raise MarketDataError("capture admission request policy mismatch")
    return identity


def _marker(path: Path, directory: str, repo: Path) -> dict[str, Any]:
    if path.parent != (repo / "reports" / directory).resolve() or not path.is_file():
        raise MarketDataError("capture admission marker path escape")
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
    raise MarketDataError("capture admission repo root not found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("capture admission output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("capture admission json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("capture admission json shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"capture admission content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".capture-admission-", suffix=".tmp", delete=False) as handle:
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
