from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.okx_future_universe_archive import (
    TRACKED_FIELDS,
    _repo_root,
    validate_future_universe_snapshot,
)
from crypto_bot.market.prospective_membership_bar_gate import (
    EXPECTED_INST_IDS,
    _ceil_hour,
    _iso,
    _parse_iso,
    validate_prospective_membership_bar_gate,
)
from crypto_bot.market.prospective_direct_1h_segment_chain import (
    AUDIT_STATUS as SEGMENT_CHAIN_STATUS,
)
from crypto_bot.market.prospective_snapshot_transition_materializer import (
    CONTRACT_STATUS as TRANSITION_STATUS,
    validate_prospective_snapshot_transition_materialization,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_membership_epoch_materializer_v1"
CONTRACT_STATUS = "verified_prospective_membership_epoch_materialization"
DEFAULT_CONFIG_FILENAME = "config.prospective-membership-epoch-materializer.example.yaml"
MEMBERSHIP_FIELDS = (
    "epoch_id",
    "epoch_ordinal",
    "inst_id",
    "membership_state",
    "source_transition_identity",
    "previous_membership_state",
    "automatic_replacement",
)
TIMING_FIELDS = (
    "epoch_id",
    "source_transition_identity",
    "current_snapshot_received_at",
    "membership_effective_at",
    "first_signal_timestamp",
    "first_completion_timestamp",
    "first_execution_timestamp",
    "epoch_end_resolved",
    "last_signal_timestamp",
    "last_execution_timestamp",
    "status",
)
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectiveMembershipEpochMaterializationResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def materialize_prospective_membership_epoch(
    snapshot_transition: str | Path,
    previous_membership_gate: str | Path,
    segment_chain: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveMembershipEpochMaterializationResult:
    transition_path = Path(snapshot_transition).resolve()
    repo = _repo_root(transition_path)
    config = load_membership_epoch_materializer_config(config_path, repo)
    state = _derive_state(transition_path, Path(previous_membership_gate), Path(segment_chain), config, repo)
    membership_bytes = _csv_bytes(state["membership_rows"], MEMBERSHIP_FIELDS)
    timing_bytes = _csv_bytes(state["timing_rows"], TIMING_FIELDS)
    dependencies = _dependency_values(state)
    constraints = _constraint_values(state)
    dependencies_bytes = _csv_bytes(_rows(dependencies), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraints), KEY_VALUE_FIELDS)
    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "transition_sha256": state["transition_sha256"],
        "previous_membership_gate_sha256": state["previous_gate_sha256"],
        "segment_chain_sha256": state["segment_chain_sha256"],
        "epoch_id": state["epoch_id"],
        "epoch_ordinal": 2,
        "membership_epoch_materialized": state["membership_epoch_materialized"],
        "status": state["status"],
        "previous_snapshot_identity": state["previous_snapshot_identity"],
        "current_snapshot_identity": state["current_snapshot_identity"],
        "current_snapshot_received_at": state["current_snapshot_received_at"],
        "membership_effective_at": state["membership_effective_at"],
        "epoch_end_resolved": False,
        "policy": config,
        "claims": _claims(),
        "artifacts": {
            "membership_sha256": _digest(membership_bytes),
            "timing_sha256": _digest(timing_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    epoch_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-membership-epoch.{epoch_sha}"
    paths = {
        "membership": output / f"{stem}.membership.csv",
        "timing": output / f"{stem}.timing.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "membership_epoch_sha256": epoch_sha,
        "contract_status": CONTRACT_STATUS,
        "epoch_id": state["epoch_id"],
        "epoch_ordinal": 2,
        "status": state["status"],
        "membership_epoch_materialized": state["membership_epoch_materialized"],
        "previous_snapshot_identity": state["previous_snapshot_identity"],
        "current_snapshot_identity": state["current_snapshot_identity"],
        "current_snapshot_received_at": state["current_snapshot_received_at"],
        "membership_effective_at": state["membership_effective_at"],
        "epoch_end_resolved": False,
        "membership_rows": len(state["membership_rows"]),
        "timing_rows": len(state["timing_rows"]),
        "previous_membership_gate_sha256": state["previous_gate_sha256"],
        "transition_sha256": state["transition_sha256"],
        "segment_chain_sha256": state["segment_chain_sha256"],
        "future_only": True,
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "historical_backfill_prohibited": True,
        "retroactive_membership_change_prohibited": True,
        "automatic_replacement_prohibited": True,
        "current_samples": 160,
        "new_samples_counted": 0,
        "remaining_samples": 340,
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
    return ProspectiveMembershipEpochMaterializationResult(report, {key: str(value) for key, value in paths.items()})


def validate_prospective_membership_epoch_materialization(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if not _report_dir_allowed(report_path, repo):
        raise MarketDataError("membership epoch report path escape")
    report = _load_json(report_path)
    epoch_sha = report.get("membership_epoch_sha256")
    identity = report.get("identity")
    if not isinstance(epoch_sha, str) or report_path.name != f"prospective-membership-epoch.{epoch_sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != epoch_sha:
        raise MarketDataError("membership epoch identity mismatch")
    if report.get("contract_status") != CONTRACT_STATUS:
        raise MarketDataError("membership epoch contract status mismatch")
    config = identity.get("policy")
    if not isinstance(config, dict) or config != load_membership_epoch_materializer_config(repo / DEFAULT_CONFIG_FILENAME, repo):
        raise MarketDataError("membership epoch policy mismatch")
    state = _derive_state(
        _locate_transition(repo, str(identity.get("transition_sha256"))),
        _locate_gate(repo, str(identity.get("previous_membership_gate_sha256"))),
        _locate_segment_chain(repo, str(identity.get("segment_chain_sha256"))),
        config,
        repo,
    )
    expected = {
        "epoch_id": state["epoch_id"],
        "epoch_ordinal": 2,
        "status": state["status"],
        "membership_epoch_materialized": state["membership_epoch_materialized"],
        "previous_snapshot_identity": state["previous_snapshot_identity"],
        "current_snapshot_identity": state["current_snapshot_identity"],
        "current_snapshot_received_at": state["current_snapshot_received_at"],
        "membership_effective_at": state["membership_effective_at"],
        "epoch_end_resolved": False,
        "membership_rows": len(state["membership_rows"]),
        "timing_rows": len(state["timing_rows"]),
        "previous_membership_gate_sha256": state["previous_gate_sha256"],
        "transition_sha256": state["transition_sha256"],
        "segment_chain_sha256": state["segment_chain_sha256"],
        "future_only": True,
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "historical_backfill_prohibited": True,
        "retroactive_membership_change_prohibited": True,
        "automatic_replacement_prohibited": True,
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
            raise MarketDataError(f"membership epoch report mismatch:{key}")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("membership epoch artifacts shape mismatch")
    for key, fields, rows in (("membership", MEMBERSHIP_FIELDS, state["membership_rows"]), ("timing", TIMING_FIELDS, state["timing_rows"])):
        info = artifacts.get(key)
        if not isinstance(info, dict) or info.get("sha256") != identity_artifacts.get(f"{key}_sha256"):
            raise MarketDataError(f"membership epoch artifact metadata mismatch:{key}")
        artifact_path = report_path.parent / str(info.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != info.get("sha256"):
            raise MarketDataError(f"membership epoch artifact bytes mismatch:{key}")
        actual = _read_csv(artifact_path, fields)
        expected_rows = [{field: _csv_value(row.get(field)) for field in fields} for row in rows]
        if actual != expected_rows or len(actual) != info.get("row_count"):
            raise MarketDataError(f"membership epoch {key} artifact mismatch")
    dependencies = _dependency_values(state)
    constraints = _constraint_values(state)
    for key in ("dependencies", "constraints"):
        info = artifacts.get(key)
        if not isinstance(info, dict) or info.get("sha256") != identity_artifacts.get(f"{key}_sha256"):
            raise MarketDataError(f"membership epoch artifact metadata mismatch:{key}")
        artifact_path = report_path.parent / str(info.get("filename", ""))
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != info.get("sha256"):
            raise MarketDataError(f"membership epoch artifact bytes mismatch:{key}")
        expected_values = dependencies if key == "dependencies" else constraints
        if _read_key_values(artifact_path) != {k: _csv_value(v) for k, v in expected_values.items()}:
            raise MarketDataError(f"membership epoch {key} artifact mismatch")
    return report


def load_membership_epoch_materializer_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("membership epoch materializer config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("membership epoch materializer config is invalid") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("membership epoch materializer config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("membership epoch materializer config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_prospective_membership_epoch_materialization(result: ProspectiveMembershipEpochMaterializationResult) -> str:
    report = result.report
    return "\n".join((
        f"contract_status: {report['contract_status']}",
        f"membership_epoch_sha256: {report['membership_epoch_sha256']}",
        f"status: {report['status']}",
        f"membership_epoch_materialized: {str(report['membership_epoch_materialized']).lower()}",
        f"epoch_id: {report['epoch_id']}",
        f"membership_effective_at: {report['membership_effective_at']}",
        "epoch_end_resolved: false",
        f"membership_rows: {report['membership_rows']}",
        "current_samples: 160",
        "new_samples_counted: 0",
        "remaining_samples: 340",
        "network_activity_performed: false",
        "economic_computation_authorized: false",
        "readiness_changed: false",
    ))


def _derive_state(transition_path: Path, gate_path: Path, chain_path: Path, config: Mapping[str, Any], repo: Path) -> dict[str, Any]:
    _validate_policy(config)
    transition = _validated_transition(transition_path, repo)
    gate = validate_prospective_membership_bar_gate(gate_path.resolve(), repo / "config.prospective-membership-bar-gate.example.yaml")
    chain = _validated_segment_chain(chain_path.resolve(), repo)
    transition_sha = str(transition["transition_sha256"])
    gate_sha = str(gate.report["gate_sha256"])
    chain_sha = str(chain["chain_sha256"])
    previous_identity = transition.get("previous_snapshot_identity")
    if previous_identity != gate.report.get("identity", {}).get("current_snapshot_sha256"):
        raise MarketDataError("membership epoch previous gate/transition lineage mismatch")
    if gate_sha != chain.get("identity", {}).get("segments", [{}])[0].get("membership_gate_sha256"):
        raise MarketDataError("membership epoch segment chain/gate lineage mismatch")
    if transition.get("transition_materialized") is not True:
        timing = [{"epoch_id": "epoch-0002", "source_transition_identity": transition_sha, "current_snapshot_received_at": "", "membership_effective_at": "", "first_signal_timestamp": "", "first_completion_timestamp": "", "first_execution_timestamp": "", "epoch_end_resolved": False, "last_signal_timestamp": "", "last_execution_timestamp": "", "status": "blocked_transition_not_materialized"}]
        return _state_base(transition, gate_sha, chain_sha, False, "blocked_transition_not_materialized", None, None, None, None, [], timing)
    current_identity = transition.get("current_snapshot_identity")
    received_at = _parse_iso(str(transition.get("current_received_at")))
    previous_received = _parse_iso(str(transition.get("previous_received_at")))
    if not isinstance(current_identity, str) or received_at <= previous_received:
        raise MarketDataError("membership epoch transition timestamp mismatch")
    previous_gate_received = _previous_gate_received_at(repo, gate.report)
    if previous_received != previous_gate_received:
        raise MarketDataError("membership epoch previous received_at mismatch")
    effective = _ceil_hour(received_at)
    if effective <= previous_gate_received:
        raise MarketDataError("membership epoch retroactive start")
    next_start = _parse_iso(str(chain["next_canonical_segment_start"]))
    if next_start <= _parse_iso(str(chain["current_chain_tail"])):
        raise MarketDataError("membership epoch segment chain tail mismatch")
    tracked_path = transition_path.parent / str(transition["artifacts"]["tracked"]["filename"])
    tracked = _read_csv(tracked_path, TRACKED_FIELDS)
    if [row.get("inst_id") for row in tracked] != EXPECTED_INST_IDS:
        raise MarketDataError("membership epoch tracked asset order mismatch")
    previous_states = {row.get("inst_id"): row.get("previous_snapshot_membership_state") for row in gate.eligibility if row.get("signal_timestamp") == gate.report["identity"]["epoch"]["first_signal_timestamp"]}
    membership = [{"epoch_id": "epoch-0002", "epoch_ordinal": 2, "inst_id": row["inst_id"], "membership_state": row["tracked_membership_status"], "source_transition_identity": transition_sha, "previous_membership_state": previous_states.get(row["inst_id"], "retained"), "automatic_replacement": False} for row in tracked]
    timing = [{"epoch_id": "epoch-0002", "source_transition_identity": transition_sha, "current_snapshot_received_at": _iso(received_at), "membership_effective_at": _iso(effective), "first_signal_timestamp": _iso(effective), "first_completion_timestamp": _iso(effective + timedelta(hours=1)), "first_execution_timestamp": _iso(effective + timedelta(hours=2)), "epoch_end_resolved": False, "last_signal_timestamp": "", "last_execution_timestamp": "", "status": "open_pending_future_close"}]
    return _state_base(transition, gate_sha, chain_sha, True, "open_pending_future_close", previous_identity, current_identity, _iso(received_at), _iso(effective), membership, timing)


def _state_base(transition: Mapping[str, Any], gate_sha: str, chain_sha: str, materialized: bool, status: str, previous_identity: str | None, current_identity: str | None, received_at: str | None, effective: str | None, membership: list[dict[str, Any]], timing: list[dict[str, Any]]) -> dict[str, Any]:
    return {"transition_sha256": str(transition["transition_sha256"]), "previous_gate_sha256": gate_sha, "segment_chain_sha256": chain_sha, "epoch_id": "epoch-0002", "membership_epoch_materialized": materialized, "status": status, "previous_snapshot_identity": previous_identity, "current_snapshot_identity": current_identity, "current_snapshot_received_at": received_at, "membership_effective_at": effective, "membership_rows": membership, "timing_rows": timing, "current_chain_tail": transition.get("current_chain_tail")}


def _validated_transition(path: Path, repo: Path) -> dict[str, Any]:
    if not _parent_allowed(path, repo, "prospective-snapshot-transition"):
        raise MarketDataError("membership epoch transition path escape")
    report = validate_prospective_snapshot_transition_materialization(path)
    if report.get("contract_status") != TRANSITION_STATUS:
        raise MarketDataError("membership epoch transition contract mismatch")
    return report


def _validated_segment_chain(path: Path, repo: Path) -> dict[str, Any]:
    if path.parent != (repo / "reports/prospective-direct-1h-segment-chain").resolve():
        raise MarketDataError("membership epoch segment chain path mismatch")
    report = _load_json(path)
    chain_sha = report.get("chain_sha256")
    identity = report.get("identity")
    if not isinstance(chain_sha, str) or path.name != f"prospective-direct-1h-segment-chain.{chain_sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != chain_sha or report.get("audit_status") != SEGMENT_CHAIN_STATUS:
        raise MarketDataError("membership epoch segment chain identity mismatch")
    if report.get("current_sample_count", report.get("unique_closed_execution_intervals")) != 160 or report.get("minimum", 500) != 500 or report.get("remaining", 340) != 340 or report.get("next_segment_end_resolved", True) is not False or report.get("economic_computation_authorized") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("membership epoch segment chain gate mismatch")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MarketDataError("membership epoch segment chain artifacts missing")
    for key in ("segments", "assets", "constraints"):
        info = artifacts.get(key)
        if not isinstance(info, dict):
            raise MarketDataError("membership epoch segment chain artifact metadata missing")
        artifact = path.parent / str(info.get("filename", ""))
        if not artifact.is_file() or _digest(artifact.read_bytes()) != info.get("sha256"):
            raise MarketDataError("membership epoch segment chain artifact mismatch")
    return report


def _previous_gate_received_at(repo: Path, gate_report: Mapping[str, Any]) -> datetime:
    identity = gate_report.get("identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("current_snapshot_sha256"), str):
        raise MarketDataError("membership epoch gate snapshot identity missing")
    path = repo / "reports/okx-future-universe-snapshot" / f"okx-universe-snapshot.{identity['current_snapshot_sha256']}.json"
    snapshot = validate_future_universe_snapshot(path, repo / "config.okx-future-universe-archive.example.yaml")
    return _parse_iso(str(snapshot.report["identity"]["received_at"]))


def _validate_policy(config: Mapping[str, Any]) -> None:
    expected = {"schema_version": 1, "policy_id": POLICY_ID, "required_transition_status": TRANSITION_STATUS, "required_previous_gate_status": "verified_prospective_membership_1h_bar_gate", "required_segment_chain_status": SEGMENT_CHAIN_STATUS, "next_epoch_ordinal": 2, "timeframe": "1h", "signal_completion_delay_bars": 1, "execution_offset_bars": 2, "open_epoch_end_prohibited": True, "manual_snapshot_override_prohibited": True, "manual_received_at_override_prohibited": True, "manual_membership_override_prohibited": True, "historical_backfill_prohibited": True, "retroactive_membership_change_prohibited": True, "automatic_replacement": False, "sample_threshold": 500, "new_samples_counted": 0, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}
    if dict(config) != expected:
        raise MarketDataError("membership epoch materializer policy semantics mismatch")


def _dependency_values(state: Mapping[str, Any]) -> dict[str, str]:
    return {"transition_sha256": str(state["transition_sha256"]), "previous_membership_gate_sha256": str(state["previous_gate_sha256"]), "segment_chain_sha256": str(state["segment_chain_sha256"]), "epoch_id": str(state["epoch_id"]), "current_snapshot_identity": str(state["current_snapshot_identity"] or ""), "membership_effective_at": str(state["membership_effective_at"] or "")}


def _constraint_values(state: Mapping[str, Any]) -> dict[str, Any]:
    return {"membership_epoch_materialized": state["membership_epoch_materialized"], "status": state["status"], "future_only": True, "historical_point_in_time_membership": False, "survivorship_bias_resolved": False, "historical_backfill_prohibited": True, "retroactive_membership_change_prohibited": True, "automatic_replacement_prohibited": True, "epoch_end_resolved": False, "current_samples": 160, "new_samples_counted": 0, "remaining_samples": 340, "network_activity_performed": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}


def _claims() -> dict[str, Any]:
    return {"future_only": True, "transition_gated": True, "open_epoch_only": True, "historical_point_in_time_membership": False, "survivorship_bias_resolved": False, "automatic_replacement": False, "retroactive_membership_change_prohibited": True, "historical_backfill_prohibited": True, "profitability_evidence": False, "economic_computation_authorized": False, "readiness_changed": False}


def _locate_transition(repo: Path, sha: str) -> Path:
    candidates = _marker_candidates(repo, sha, "prospective-snapshot-transition", "prospective-snapshot-transition")
    for candidate in candidates:
        try:
            validate_prospective_snapshot_transition_materialization(candidate)
        except (FileNotFoundError, OSError, ValueError, MarketDataError):
            continue
        return candidate
    raise MarketDataError("membership epoch transition reference ambiguous")


def _locate_gate(repo: Path, sha: str) -> Path:
    return _locate_marker(repo, sha, "prospective-membership-bar-gate", "prospective-membership-bar-gate")


def _locate_segment_chain(repo: Path, sha: str) -> Path:
    return _locate_marker(repo, sha, "prospective-direct-1h-segment-chain", "prospective-direct-1h-segment-chain")


def _locate_marker(repo: Path, sha: str, directory: str, prefix: str) -> Path:
    canonical = repo / "reports" / directory / f"{prefix}.{sha}.json"
    if canonical.is_file():
        return canonical
    candidates = _marker_candidates(repo, sha, directory, prefix)
    if len(candidates) != 1:
        raise MarketDataError("membership epoch parent reference ambiguous")
    return candidates[0]


def _marker_candidates(repo: Path, sha: str, directory: str, prefix: str) -> list[Path]:
    if not _is_sha256(sha):
        raise MarketDataError("membership epoch parent reference mismatch")
    canonical = repo / "reports" / directory / f"{prefix}.{sha}.json"
    matches = [p for p in (repo / "reports").rglob(canonical.name) if p.is_file()]
    if canonical.is_file() and canonical not in matches:
        matches.insert(0, canonical)
    if not matches:
        raise MarketDataError("membership epoch parent reference ambiguous")
    return matches


def _parent_allowed(path: Path, repo: Path, canonical_name: str) -> bool:
    reports = (repo / "reports").resolve()
    if not path.is_relative_to(reports):
        return False
    return path.name.startswith(canonical_name + ".")


def _report_dir_allowed(path: Path, repo: Path) -> bool:
    return _parent_allowed(path, repo, "prospective-membership-epoch")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("membership epoch output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fields):
                raise MarketDataError("membership epoch CSV schema mismatch")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("membership epoch CSV read failed") from exc
    return rows


def _read_key_values(path: Path) -> dict[str, str]:
    rows = _read_csv(path, KEY_VALUE_FIELDS)
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows):
        raise MarketDataError("membership epoch key collision")
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
        raise MarketDataError("membership epoch JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("membership epoch JSON shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"membership epoch content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".membership-epoch-", suffix=".tmp", delete=False) as handle:
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
