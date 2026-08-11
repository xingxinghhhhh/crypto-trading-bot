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
from typing import Any, Mapping

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_capture_window_closeout import (
    validate_capture_window_closeout,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_epoch_closeout_rollover_v1"
CONTRACT_STATUS = "verified_prospective_epoch_closeout_rollover"
DEFAULT_CONFIG_FILENAME = "config.prospective-epoch-closeout-rollover.example.yaml"
ASSEMBLY_SHA = "674116b95e03705b478f285012148f0143c2ef82befccda5627ce2ab901bcc79"
ACCUMULATION_POLICY_SHA = "8f4a90632bcf73df04c7e6f44924924db682cdc8373ed04c1f726cb6ce353092"
MATURITY_SHA = "32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178"
SNAPSHOT_SHA = "d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e"
SEGMENT_START = "2026-08-09T11:00:00Z"
WINDOW_START = "2026-08-16T10:00:00Z"
WINDOW_END = "2026-08-16T11:00:00Z"
NEXT_WINDOW_START = "2026-08-23T10:00:00Z"
NEXT_WINDOW_END = "2026-08-23T11:00:00Z"
STATE_FIELDS = (
    "epoch_ordinal",
    "governed_window_start",
    "governed_window_end",
    "closeout_sha256",
    "derived_final_state",
    "action",
    "epoch_2_closed_without_sample",
    "sample_credit",
    "current_samples",
    "new_samples_counted",
    "remaining_samples",
    "next_epoch_ordinal",
    "next_window_start",
    "next_window_end",
    "latest_accepted_snapshot_identity",
    "next_segment_start",
)
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectiveEpochCloseoutRolloverResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def audit_prospective_epoch_closeout_rollover(
    closeout_report: str | Path,
    assembly_report: str | Path,
    accumulation_policy: str | Path,
    sample_maturity: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveEpochCloseoutRolloverResult:
    closeout_path = Path(closeout_report).resolve()
    repo = _repo_root(closeout_path)
    config = load_closeout_rollover_config(config_path, repo)
    closeout = validate_capture_window_closeout(closeout_path)
    assembly = _validate_assembly(Path(assembly_report).resolve(), repo)
    policy = _validate_accumulation_policy(Path(accumulation_policy).resolve(), repo)
    maturity = _validate_maturity(Path(sample_maturity).resolve(), repo)
    _validate_parent_binding(closeout, assembly, policy, maturity, config)
    derived = _derive_action(closeout, assembly, policy)
    dependencies = _dependency_values(closeout, assembly, policy, maturity)
    constraints = _constraint_values(derived)
    state = _state_row(closeout, assembly, derived)
    state_bytes = _csv_bytes([state], STATE_FIELDS)
    dependencies_bytes = _csv_bytes(_rows(dependencies), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraints), KEY_VALUE_FIELDS)
    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "closeout_sha256": closeout["closeout_sha256"],
        "closeout_report_filename": closeout_path.name,
        "assembly_sha256": assembly["assembly_sha256"],
        "accumulation_policy_sha256": policy["policy_sha256"],
        "sample_maturity_sha256": maturity["maturity_sha256"],
        "epoch_ordinal_basis": "governed_capture_window",
        "derived_state": derived,
        "policy": config,
        "claims": _claims(),
        "artifacts": {
            "state_sha256": _digest(state_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    rollover_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-epoch-closeout-rollover.{rollover_sha}"
    paths = {
        "state": output / f"{stem}.state.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "rollover_sha256": rollover_sha,
        "contract_status": CONTRACT_STATUS,
        "epoch_ordinal": 2,
        "governed_window_start": WINDOW_START,
        "governed_window_end": WINDOW_END,
        "closeout_sha256": closeout["closeout_sha256"],
        **derived,
        "network_activity_performed": False,
        "current_samples": 160,
        "new_samples_counted": 0,
        "remaining_samples": 340,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "identity": identity,
        "artifacts": {
            "state": {"filename": paths["state"].name, "sha256": identity["artifacts"]["state_sha256"], "row_count": 1},
            "dependencies": {"filename": paths["dependencies"].name, "sha256": identity["artifacts"]["dependencies_sha256"], "row_count": len(dependencies)},
            "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)},
            "report": {"filename": paths["report"].name},
        },
    }
    _commit_bytes(paths["state"], state_bytes)
    _commit_bytes(paths["dependencies"], dependencies_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveEpochCloseoutRolloverResult(report, {key: str(value) for key, value in paths.items()})


def load_closeout_rollover_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("closeout rollover config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("closeout rollover config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("closeout rollover config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def validate_prospective_epoch_closeout_rollover(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if not _report_dir_allowed(report_path, repo):
        raise MarketDataError("closeout rollover report path escape")
    report = _load_json(report_path)
    rollover_sha = report.get("rollover_sha256")
    identity = report.get("identity")
    if not isinstance(rollover_sha, str) or report_path.name != f"prospective-epoch-closeout-rollover.{rollover_sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != rollover_sha:
        raise MarketDataError("closeout rollover identity mismatch")
    if report.get("contract_status") != CONTRACT_STATUS:
        raise MarketDataError("closeout rollover contract status mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("closeout rollover artifacts shape mismatch")
    for key in ("state", "dependencies", "constraints"):
        artifact = artifacts.get(key)
        expected = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(artifact, dict) or artifact.get("sha256") != expected:
            raise MarketDataError(f"closeout rollover artifact metadata mismatch:{key}")
        artifact_path = report_path.parent / str(artifact.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected:
            raise MarketDataError(f"closeout rollover artifact bytes mismatch:{key}")
    closeout_path = _locate_closeout_report(repo, str(identity.get("closeout_sha256")), str(identity.get("closeout_report_filename")))
    closeout = validate_capture_window_closeout(closeout_path)
    assembly = _validate_assembly(repo / "reports/prospective-epoch-assembly" / f"prospective-epoch-assembly.{identity.get('assembly_sha256')}.json", repo)
    policy = _validate_accumulation_policy(repo / "reports/prospective-epoch-accumulation-policy" / f"prospective-epoch-accumulation-policy.{identity.get('accumulation_policy_sha256')}.json", repo)
    maturity = _validate_maturity(repo / "reports/prospective-economic-sample-maturity" / f"prospective-economic-sample-maturity.{identity.get('sample_maturity_sha256')}.json", repo)
    config = identity.get("policy")
    if not isinstance(config, dict) or config != load_closeout_rollover_config(repo / DEFAULT_CONFIG_FILENAME, repo):
        raise MarketDataError("closeout rollover policy mismatch")
    _validate_parent_binding(closeout, assembly, policy, maturity, config)
    derived = _derive_action(closeout, assembly, policy)
    if identity.get("derived_state") != derived:
        raise MarketDataError("closeout rollover derived state mismatch")
    state = _state_row(closeout, assembly, derived)
    state_rows = _read_csv(report_path.parent / str(artifacts["state"]["filename"]), STATE_FIELDS)
    if len(state_rows) != 1 or state_rows[0] != {key: _csv_value(state[key]) for key in STATE_FIELDS}:
        raise MarketDataError("closeout rollover state artifact mismatch")
    dependencies = _dependency_values(closeout, assembly, policy, maturity)
    constraints = _constraint_values(derived)
    if _read_key_values(report_path.parent / str(artifacts["dependencies"]["filename"])) != dependencies:
        raise MarketDataError("closeout rollover dependency artifact mismatch")
    if _read_key_values(report_path.parent / str(artifacts["constraints"]["filename"])) != {key: _csv_value(value) for key, value in constraints.items()}:
        raise MarketDataError("closeout rollover constraint artifact mismatch")
    expected = {"epoch_ordinal": 2, "governed_window_start": WINDOW_START, "governed_window_end": WINDOW_END, "closeout_sha256": closeout["closeout_sha256"], **derived, "network_activity_performed": False, "current_samples": 160, "new_samples_counted": 0, "remaining_samples": 340, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}
    for key, value in expected.items():
        if report.get(key) != value:
            raise MarketDataError(f"closeout rollover identity mismatch/report:{key}")
    return report


def format_prospective_epoch_closeout_rollover(result: ProspectiveEpochCloseoutRolloverResult) -> str:
    report = result.report
    return "\n".join((f"contract_status: {report['contract_status']}", f"rollover_sha256: {report['rollover_sha256']}", f"closeout_sha256: {report['closeout_sha256']}", f"derived_final_state: {report['derived_final_state']}", f"action: {report['action']}", f"epoch_2_closed_without_sample: {str(report['epoch_2_closed_without_sample']).lower()}", f"sample_credit: {report['sample_credit']}", f"next_epoch_ordinal: {report['next_epoch_ordinal']}", f"next_window_start: {report['next_window_start']}", f"next_window_end: {report['next_window_end']}", "network_activity_performed: false", "current_samples: 160", "new_samples_counted: 0", "remaining_samples: 340", "economic_computation_authorized: false", "readiness_changed: false"))


def _validate_parent_binding(closeout: dict[str, Any], assembly: dict[str, Any], policy: dict[str, Any], maturity: dict[str, Any], config: dict[str, Any]) -> None:
    if closeout.get("epoch_ordinal") != 2 or closeout.get("window_start") != WINDOW_START or closeout.get("window_end") != WINDOW_END or closeout.get("current_samples") != 160 or closeout.get("remaining_samples") != 340 or closeout.get("network_activity_performed") is not False:
        raise MarketDataError("closeout rollover closeout binding mismatch")
    if assembly.get("next_epoch_ordinal") != 2 or assembly.get("next_capture_window_start") != WINDOW_START or assembly.get("next_capture_window_end") != WINDOW_END or assembly.get("expected_previous_snapshot") != SNAPSHOT_SHA or assembly.get("expected_next_market_segment_start") != SEGMENT_START or assembly.get("current_samples") != 160 or assembly.get("remaining_samples") != 340 or assembly.get("new_samples_counted") != 0:
        raise MarketDataError("closeout rollover assembly binding mismatch")
    cadence = policy.get("identity", {}).get("cadence", {}) if isinstance(policy.get("identity"), dict) else {}
    if policy.get("policy_sha256") != ACCUMULATION_POLICY_SHA or policy.get("first_governed_window_start") != WINDOW_START or policy.get("first_governed_window_end") != WINDOW_END or policy.get("cadence") != "weekly" or policy.get("capture_window_utc") != "[10:00:00,11:00:00)" or cadence.get("schedule_shift_after_miss") is not False or policy.get("current_unique_closed_intervals") != 160 or policy.get("remaining_closed_intervals") != 340 or policy.get("sample_maturity_met") is not False:
        raise MarketDataError("closeout rollover accumulation binding mismatch")
    if maturity.get("maturity_sha256") != MATURITY_SHA or maturity.get("unique_closed_interval_count") != 160 or maturity.get("remaining_closed_interval_count") != 340 or maturity.get("sample_maturity_met") is not False:
        raise MarketDataError("closeout rollover maturity binding mismatch")
    if config.get("required_sample_threshold") != 500 or config.get("missed_epoch_sample_credit") != 0 or config.get("schedule_shift_after_miss") is not False or config.get("historical_backfill_prohibited") is not True or config.get("economic_computation_authorized") is not False or config.get("pnl_computation_authorized") is not False or config.get("readiness_changed") is not False:
        raise MarketDataError("closeout rollover policy binding mismatch")


def _derive_action(closeout: Mapping[str, Any], assembly: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    final_state = closeout.get("derived_final_state")
    if final_state == "pending_window_end":
        return {"derived_final_state": final_state, "action": "hold", "epoch_2_closed_without_sample": False, "sample_credit": 0, "next_epoch_ordinal": 2, "next_window_start": None, "next_window_end": None, "latest_accepted_snapshot_identity": assembly["expected_previous_snapshot"], "next_segment_start": assembly["expected_next_market_segment_start"]}
    if final_state == "accepted_closed":
        accepted = closeout.get("accepted_snapshot_identity")
        if not isinstance(accepted, str) or not accepted:
            raise MarketDataError("closeout rollover accepted identity missing")
        return {"derived_final_state": final_state, "action": "transition_eligible", "epoch_2_closed_without_sample": False, "sample_credit": 0, "next_epoch_ordinal": 2, "next_window_start": None, "next_window_end": None, "latest_accepted_snapshot_identity": accepted, "next_segment_start": assembly["expected_next_market_segment_start"]}
    if final_state == "missed_no_backfill":
        if closeout.get("accepted_snapshot_identity") is not None or closeout.get("retry_permitted") is not False:
            raise MarketDataError("closeout rollover missed state is not closed")
        start = _parse_utc(str(assembly["next_capture_window_start"])) + timedelta(days=7)
        end = start + timedelta(hours=1)
        cadence = policy.get("identity", {}).get("cadence", {}) if isinstance(policy.get("identity"), dict) else {}
        if _iso(start) != NEXT_WINDOW_START or _iso(end) != NEXT_WINDOW_END or cadence.get("schedule_shift_after_miss") is not False:
            raise MarketDataError("closeout rollover weekly cadence mismatch")
        return {"derived_final_state": final_state, "action": "rollover_next_window", "epoch_2_closed_without_sample": True, "sample_credit": 0, "next_epoch_ordinal": 3, "next_window_start": _iso(start), "next_window_end": _iso(end), "latest_accepted_snapshot_identity": assembly["expected_previous_snapshot"], "next_segment_start": assembly["expected_next_market_segment_start"]}
    raise MarketDataError("closeout rollover final state mismatch")


def _state_row(closeout: Mapping[str, Any], assembly: Mapping[str, Any], derived: Mapping[str, Any]) -> dict[str, Any]:
    return {"epoch_ordinal": 2, "governed_window_start": assembly["next_capture_window_start"], "governed_window_end": assembly["next_capture_window_end"], "closeout_sha256": closeout["closeout_sha256"], **derived, "current_samples": 160, "new_samples_counted": 0, "remaining_samples": 340}


def _dependency_values(closeout: Mapping[str, Any], assembly: Mapping[str, Any], policy: Mapping[str, Any], maturity: Mapping[str, Any]) -> dict[str, str]:
    return {"closeout_sha256": str(closeout["closeout_sha256"]), "assembly_sha256": str(assembly["assembly_sha256"]), "accumulation_policy_sha256": str(policy["policy_sha256"]), "sample_maturity_sha256": str(maturity["maturity_sha256"]), "governed_window_start": WINDOW_START, "governed_window_end": WINDOW_END, "next_segment_start": SEGMENT_START}


def _constraint_values(derived: Mapping[str, Any]) -> dict[str, Any]:
    return {"current_samples": 160, "new_samples_counted": 0, "remaining_samples": 340, "sample_credit": 0, "epoch_2_closed_without_sample": derived["epoch_2_closed_without_sample"], "missed_window_backfill_prohibited": True, "historical_backfill_prohibited": True, "schedule_shift_after_miss": False, "network_activity_performed": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}


def _claims() -> dict[str, Any]:
    return {"pending_blocks_progress": True, "accepted_only_transition": True, "missed_rollover_zero_sample_credit": True, "epoch_ordinal_basis_governed_capture_window": True, "missed_window_backfill_prohibited": True, "schedule_shift_after_miss": False, "future_only": True, "profitability_evidence": False, "economic_computation_authorized": False, "readiness_changed": False}


def _validate_assembly(path: Path, repo: Path) -> dict[str, Any]:
    if path.parent != (repo / "reports/prospective-epoch-assembly").resolve() or path.name != f"prospective-epoch-assembly.{ASSEMBLY_SHA}.json":
        raise MarketDataError("closeout rollover assembly path mismatch")
    report = _load_json(path)
    if report.get("assembly_sha256") != ASSEMBLY_SHA or report.get("contract_status") != "verified_prospective_future_epoch_assembly_state_machine" or _digest(_canonical_json_bytes(report.get("identity", {}))) != ASSEMBLY_SHA:
        raise MarketDataError("closeout rollover assembly identity mismatch")
    _validate_artifacts(path, report, ("states", "next_epoch", "protocol", "constraints"))
    return report


def _validate_accumulation_policy(path: Path, repo: Path) -> dict[str, Any]:
    if path.parent != (repo / "reports/prospective-epoch-accumulation-policy").resolve() or path.name != f"prospective-epoch-accumulation-policy.{ACCUMULATION_POLICY_SHA}.json":
        raise MarketDataError("closeout rollover accumulation path mismatch")
    report = _load_json(path)
    if report.get("policy_sha256") != ACCUMULATION_POLICY_SHA or report.get("contract_status") != "verified_prospective_epoch_accumulation_policy" or _digest(_canonical_json_bytes(report.get("identity", {}))) != ACCUMULATION_POLICY_SHA:
        raise MarketDataError("closeout rollover accumulation identity mismatch")
    _validate_artifacts(path, report, ("schedule", "protocol", "constraints"))
    return report


def _validate_maturity(path: Path, repo: Path) -> dict[str, Any]:
    if path.parent != (repo / "reports/prospective-economic-sample-maturity").resolve() or path.name != f"prospective-economic-sample-maturity.{MATURITY_SHA}.json":
        raise MarketDataError("closeout rollover maturity path mismatch")
    report = _load_json(path)
    if report.get("maturity_sha256") != MATURITY_SHA or report.get("contract_status") != "verified_prospective_economic_sample_maturity_gate" or _digest(_canonical_json_bytes(report.get("identity", {}))) != MATURITY_SHA:
        raise MarketDataError("closeout rollover maturity identity mismatch")
    _validate_artifacts(path, report, ("epochs", "intervals", "policy", "constraints"))
    return report


def _validate_artifacts(path: Path, report: Mapping[str, Any], keys: tuple[str, ...]) -> None:
    artifacts = report.get("artifacts")
    identity_artifacts = report.get("identity", {}).get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("closeout rollover parent artifacts shape mismatch")
    for key in keys:
        info = artifacts.get(key)
        expected = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(info, dict) or info.get("sha256") != expected:
            raise MarketDataError(f"closeout rollover parent artifact metadata mismatch:{key}")
        artifact_path = path.parent / str(info.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected:
            raise MarketDataError(f"closeout rollover parent artifact bytes mismatch:{key}")


def _locate_closeout_report(repo: Path, closeout_sha: str, filename: str) -> Path:
    if not _is_sha256(closeout_sha) or filename != f"prospective-capture-window-closeout.{closeout_sha}.json":
        raise MarketDataError("closeout rollover closeout reference mismatch")
    canonical = repo / "reports/prospective-capture-window-closeout" / filename
    if canonical.is_file():
        return canonical
    matches = [path for path in (repo / "reports").rglob(filename) if path.is_file() and path.parent.name.startswith("prospective-capture-window-closeout-")]
    if len(matches) != 1:
        raise MarketDataError("closeout rollover closeout reference ambiguous")
    return matches[0]


def _report_dir_allowed(path: Path, repo: Path) -> bool:
    canonical = (repo / "reports" / "prospective-epoch-closeout-rollover").resolve()
    return path.parent == canonical or (path.parent.parent == (repo / "reports").resolve() and path.parent.name.startswith("prospective-epoch-closeout-rollover-"))


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("closeout rollover output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fields):
                raise MarketDataError("closeout rollover CSV schema mismatch")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("closeout rollover CSV read failed") from exc
    if any(any(row.get(field) is None for field in fields) for row in rows):
        raise MarketDataError("closeout rollover CSV row mismatch")
    return rows


def _read_key_values(path: Path) -> dict[str, str]:
    rows = _read_csv(path, KEY_VALUE_FIELDS)
    result = {row["key"]: row["value"] for row in rows}
    if len(result) != len(rows):
        raise MarketDataError("closeout rollover key collision")
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
        raise MarketDataError("closeout rollover timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataError("closeout rollover timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("closeout rollover repo root not found")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("closeout rollover JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("closeout rollover JSON shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"closeout rollover content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".closeout-rollover-", suffix=".tmp", delete=False) as handle:
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
