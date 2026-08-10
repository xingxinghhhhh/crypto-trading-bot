from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from crypto_bot.errors import MarketDataError


SCHEMA_VERSION = 1
POLICY_ID = "prospective_weekly_epoch_accumulation_v1"
CONTRACT_STATUS = "verified_prospective_epoch_accumulation_policy"
DEFAULT_CONFIG_FILENAME = "config.prospective-epoch-accumulation-policy.example.yaml"
MATURITY_POLICY_ID = "prospective_economic_sample_maturity_v1"
READINESS_POLICY_ID = "prospective_economic_evaluation_readiness_v1"
MINIMUM_INTERVALS = 500
FAMILY_SIZE = 36
SCHEDULE_FIELDS = ("key", "value")
PROTOCOL_FIELDS = ("key", "value")
CONSTRAINT_FIELDS = ("key", "value")
CURRENT_MATURITY_SHA = "32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178"
CURRENT_READINESS_SHA = "0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b"
CURRENT_SNAPSHOT_SHA = "d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e"


@dataclass(frozen=True)
class ProspectiveEpochAccumulationPolicyResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_prospective_epoch_accumulation_policy(
    sample_maturity: str | Path,
    latest_readiness: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveEpochAccumulationPolicyResult:
    maturity_path = Path(sample_maturity).resolve()
    readiness_path = Path(latest_readiness).resolve()
    repo = _repo_root(maturity_path)
    config = load_accumulation_config(config_path, repo)
    maturity = _load_marker(maturity_path, "prospective-economic-sample-maturity", repo)
    readiness = _load_marker(readiness_path, "prospective-economic-readiness", repo)
    _validate_inputs(maturity, readiness, maturity_path, readiness_path)
    snapshot = _latest_snapshot(repo, maturity, readiness)
    received_at = _parse_datetime(snapshot["received_at"])
    window_start, window_end = _next_window(received_at, config)
    schedule_rows = _rows(
        {
            "cadence": config["cadence"],
            "capture_weekday_utc": config["capture_weekday_utc"],
            "capture_window_start_utc": config["capture_window_start_utc"],
            "capture_window_end_utc": config["capture_window_end_utc"],
            "last_accepted_snapshot_sha256": snapshot["snapshot_sha256"],
            "last_accepted_snapshot_received_at": snapshot["received_at"],
            "first_governed_future_window_start": window_start.isoformat().replace("+00:00", "Z"),
            "first_governed_future_window_end": window_end.isoformat().replace("+00:00", "Z"),
            "recurrence_period_days": 7,
            "missed_window_policy": config["missed_window_policy"],
            "accepted_snapshot_policy": config["accepted_snapshot_policy"],
        }
    )
    protocol_rows = _rows(
        {
            "maturity_policy_id": MATURITY_POLICY_ID,
            "readiness_policy_id": READINESS_POLICY_ID,
            "minimum_closed_interval_count": MINIMUM_INTERVALS,
            "required_family_size": FAMILY_SIZE,
            "static_protocol_fingerprint": maturity["identity"]["protocol_fingerprint"],
            "stop_condition": config["stop_condition"],
            "threshold_reduction_prohibited": True,
            "early_stop_prohibited": True,
            "result_driven_cadence_change_prohibited": True,
            "result_driven_stop_change_prohibited": True,
        }
    )
    unique_count = maturity["unique_closed_interval_count"]
    constraint_rows = _rows(
        {
            "current_samples": unique_count,
            "remaining_samples": max(0, MINIMUM_INTERVALS - unique_count),
            "sample_maturity_met": maturity["sample_maturity_met"],
            "first_epoch_pre_policy": True,
            "historical_backfill_prohibited": True,
            "economic_computation_authorized": False,
            "profitability_evidence": False,
            "readiness_changed": False,
        }
    )
    schedule_bytes = _csv_bytes(schedule_rows, SCHEDULE_FIELDS)
    protocol_bytes = _csv_bytes(protocol_rows, PROTOCOL_FIELDS)
    constraint_bytes = _csv_bytes(constraint_rows, CONSTRAINT_FIELDS)
    schedule_sha, protocol_sha, constraint_sha = map(_digest, (schedule_bytes, protocol_bytes, constraint_bytes))
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "maturity_sha256": maturity["maturity_sha256"],
        "latest_readiness_sha256": readiness["readiness_sha256"],
        "latest_readiness_is_maturity_last_epoch": True,
        "last_accepted_snapshot": snapshot,
        "first_epoch_pre_policy": True,
        "first_policy_governed_future_window": {"start": window_start.isoformat().replace("+00:00", "Z"), "end": window_end.isoformat().replace("+00:00", "Z")},
        "cadence": config,
        "static_protocol_fingerprint": maturity["identity"]["protocol_fingerprint"],
        "counts": {"current_unique_closed_intervals": unique_count, "remaining_closed_intervals": max(0, MINIMUM_INTERVALS - unique_count), "family_size": FAMILY_SIZE},
        "claims": {"accumulation_policy_frozen_after_first_epoch": True, "global_preregistration": False, "first_epoch_was_not_collected_under_this_cadence_policy": True, "historical_backfill_prohibited": True, "economic_computation_authorized": False, "profitability_evidence": False, "readiness_changed": False},
        "artifacts": {"schedule_sha256": schedule_sha, "protocol_sha256": protocol_sha, "constraints_sha256": constraint_sha},
    }
    policy_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-epoch-accumulation-policy.{policy_sha}"
    paths = {"schedule": output / f"{stem}.schedule.csv", "protocol": output / f"{stem}.protocol.csv", "constraints": output / f"{stem}.constraints.csv", "report": output / f"{stem}.json"}
    for key, content in (("schedule", schedule_bytes), ("protocol", protocol_bytes), ("constraints", constraint_bytes)):
        _commit_bytes(paths[key], content)
    report = {
        "schema_version": SCHEMA_VERSION, "policy_sha256": policy_sha, "contract_status": CONTRACT_STATUS,
        "current_unique_closed_intervals": unique_count, "minimum_required": MINIMUM_INTERVALS, "remaining_closed_intervals": max(0, MINIMUM_INTERVALS - unique_count),
        "cadence": config["cadence"], "capture_window_utc": f"[{config['capture_window_start_utc']},{config['capture_window_end_utc']})", "first_governed_window_start": window_start.isoformat().replace("+00:00", "Z"), "first_governed_window_end": window_end.isoformat().replace("+00:00", "Z"),
        "sample_maturity_met": maturity["sample_maturity_met"], "accumulation_should_continue": not maturity["sample_maturity_met"], "turnover_rows": 0, "cost_amount_rows": 0, "capacity_pass_fail_rows": 0, "return_rows": 0, "pnl_rows": 0, "economic_computation_authorized": False, "profitability_evidence": False, "readiness_changed": False,
        "identity": identity,
        "artifacts": {key: {"filename": path.name, "sha256": _digest(content) if key != "report" else None, "row_count": len(rows)} for key, path, content, rows in (("schedule", paths["schedule"], schedule_bytes, schedule_rows), ("protocol", paths["protocol"], protocol_bytes, protocol_rows), ("constraints", paths["constraints"], constraint_bytes, constraint_rows))},
    }
    report["artifacts"]["report"] = {"filename": paths["report"].name}
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveEpochAccumulationPolicyResult(report, {key: str(value) for key, value in paths.items()})


def load_accumulation_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("accumulation config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("accumulation config policy mismatch")
    expected = {"schema_version": 1, "policy_id": POLICY_ID, "cadence": "weekly", "capture_weekday_utc": "sunday", "capture_window_start_utc": "10:00:00", "capture_window_end_utc": "11:00:00", "accepted_snapshot_policy": "first_validator_complete_capture_in_window", "max_accepted_snapshots_per_window": 1, "retry_policy": "transport_or_validation_failure_only_within_same_window", "retry_after_valid_capture": False, "missed_window_policy": "record_missed_no_backfill", "out_of_window_snapshot_policy": "reject_for_accumulation", "schedule_shift_after_miss": False, "sample_maturity_threshold": 500, "stop_condition": "sample_maturity_met", "early_stop_prohibited": True, "result_driven_cadence_change_prohibited": True, "result_driven_stop_change_prohibited": True, "historical_backfill_prohibited": True}
    if value != expected:
        raise MarketDataError("accumulation config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_prospective_epoch_accumulation_policy(result: ProspectiveEpochAccumulationPolicyResult) -> str:
    report = result.report
    return "\n".join((f"contract_status: {report['contract_status']}", f"policy_sha256: {report['policy_sha256']}", f"current_unique_closed_intervals: {report['current_unique_closed_intervals']}", f"minimum_required: {report['minimum_required']}", f"remaining_closed_intervals: {report['remaining_closed_intervals']}", f"cadence: {report['cadence']}", f"capture_window_utc: {report['capture_window_utc']}", f"first_governed_window_start: {report['first_governed_window_start']}", f"first_governed_window_end: {report['first_governed_window_end']}", f"sample_maturity_met: {str(report['sample_maturity_met']).lower()}", f"accumulation_should_continue: {str(report['accumulation_should_continue']).lower()}", "economic_computation_authorized: false", "readiness_changed: false"))


def _validate_inputs(maturity: dict[str, Any], readiness: dict[str, Any], maturity_path: Path, readiness_path: Path) -> None:
    if maturity["maturity_sha256"] != CURRENT_MATURITY_SHA or readiness["readiness_sha256"] != CURRENT_READINESS_SHA:
        raise MarketDataError("accumulation input identity mismatch")
    if maturity["identity"].get("policy_id") != "prospective_economic_sample_maturity_v1" or maturity["sample_maturity_met"] or maturity["minimum_closed_interval_count"] != MINIMUM_INTERVALS or maturity["family_size"] != FAMILY_SIZE:
        raise MarketDataError("accumulation maturity must remain below frozen threshold")
    if readiness["identity"].get("policy_id") != READINESS_POLICY_ID or not readiness["prospective_economic_inputs_structurally_ready"] or readiness["economic_value_computation_authorized"]:
        raise MarketDataError("accumulation readiness mismatch")
    if maturity_path.parent.name != "prospective-economic-sample-maturity" or readiness_path.parent.name != "prospective-economic-readiness":
        raise MarketDataError("accumulation marker path mismatch")
    last = maturity["identity"].get("readiness_reports", [])[-1]
    if last.get("readiness_sha256") != readiness["readiness_sha256"]:
        raise MarketDataError("latest readiness is not maturity last epoch")


def _latest_snapshot(repo: Path, maturity: dict[str, Any], readiness: dict[str, Any]) -> dict[str, str]:
    membership = readiness.get("identity", {}).get("sources", {}).get("membership_gate", {})
    snapshot_sha = maturity.get("identity", {}).get("readiness_reports", [{}])[-1].get("artifact_hashes", {}).get("membership_gate") or membership.get("identity")
    if membership.get("identity") != "737e3da3a1e2db42b87709a23bc5f754bab2ae2a38af36210b28282cfb3d9f48" or snapshot_sha is None:
        raise MarketDataError("latest membership evidence mismatch")
    for directory in (repo / "reports/okx-future-universe-transition", repo / "reports/okx-future-universe-transition-v2"):
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
            value = _load_json(path)
            identity = value.get("identity", {})
            if identity.get("current_snapshot_sha256") == CURRENT_SNAPSHOT_SHA:
                return {"snapshot_sha256": CURRENT_SNAPSHOT_SHA, "received_at": identity["current_received_at"], "transition_sha256": value.get("transition_sha256", path.stem)}
    raise MarketDataError("latest snapshot transition evidence not found")


def _next_window(received_at: datetime, config: dict[str, Any]) -> tuple[datetime, datetime]:
    if config["capture_weekday_utc"] != "sunday":
        raise MarketDataError("unsupported frozen weekday")
    days = (6 - received_at.weekday()) % 7
    candidate = (received_at + timedelta(days=days)).replace(hour=10, minute=0, second=0, microsecond=0)
    if candidate <= received_at:
        candidate += timedelta(days=7)
    return candidate, candidate + timedelta(hours=1)


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("snapshot received_at invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise MarketDataError("snapshot received_at must be UTC")
    return parsed.astimezone(timezone.utc)


def _load_marker(path: Path, directory: str, repo: Path) -> dict[str, Any]:
    if path.parent != (repo / "reports" / directory).resolve() or not path.is_file():
        raise MarketDataError("accumulation marker path escape")
    value = _load_json(path)
    return value


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("accumulation repo root not found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("accumulation output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _rows(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True) if not isinstance(values[key], str) else values[key]} for key in sorted(values)]


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"accumulation content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".accumulation-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("accumulation marker read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("accumulation marker shape mismatch")
    return value


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256(path: Path) -> str:
    return _digest(path.read_bytes())


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
