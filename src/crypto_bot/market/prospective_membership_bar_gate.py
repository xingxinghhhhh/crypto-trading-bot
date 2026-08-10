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
from crypto_bot.market.direct_execution_mapping_audit import (
    DEFAULT_CONFIG_FILENAME as MAPPING_CONFIG_FILENAME,
    load_direct_execution_mapping_config,
)
from crypto_bot.market.okx_future_universe_archive import (
    DEFAULT_CONFIG_FILENAME as ARCHIVE_CONFIG_FILENAME,
    FutureUniverseSnapshot,
    _repo_root,
    _sha256,
    _sibling,
    validate_future_universe_snapshot,
)
from crypto_bot.market.okx_universe_intake import validate_okx_universe_capture


SCHEMA_VERSION = 1
GATE_STATUS = "verified_prospective_membership_1h_bar_gate"
DEFAULT_CONFIG_FILENAME = "config.prospective-membership-bar-gate.example.yaml"
DATASET_FIELDS = (
    "epoch_id", "inst_id", "dataset_id", "signal_timestamp", "completion_timestamp",
    "execution_timestamp", "previous_snapshot_membership_state",
    "current_snapshot_transition_state", "eligible_for_closed_epoch",
    "retroactive_change_applied",
)
EPOCH_FIELDS = (
    "epoch_id", "previous_snapshot_sha256", "current_snapshot_sha256",
    "previous_received_at", "current_received_at", "first_signal_timestamp",
    "last_signal_timestamp", "first_execution_timestamp", "last_execution_timestamp",
    "signal_count", "row_count", "policy_id",
)
CONSTRAINT_FIELDS = ("key", "value")
EXPECTED_INST_IDS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT"]


@dataclass(frozen=True)
class MembershipBarGateResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class ValidatedTransition:
    report_path: Path
    report: dict[str, Any]
    tracked: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ValidatedMembershipBarGate:
    report_path: Path
    report: dict[str, Any]
    epochs: tuple[dict[str, Any], ...]
    eligibility: tuple[dict[str, Any], ...]


def freeze_prospective_membership_bar_gate(
    transition_report: str | Path,
    execution_mapping_report: str | Path,
    policy_path: str | Path,
    output_dir: str | Path,
) -> MembershipBarGateResult:
    transition_path = Path(transition_report).resolve()
    repo = _repo_root(transition_path)
    config = load_membership_bar_gate_config(policy_path, repo)
    if transition_path != _repo_file(repo, config["transition_report"]):
        raise MarketDataError("membership_bar_gate_transition_path_mismatch")
    if Path(execution_mapping_report).resolve() != _repo_file(repo, config["execution_mapping_report"]):
        raise MarketDataError("membership_bar_gate_mapping_path_mismatch")
    transition = validate_okx_future_universe_transition(transition_path, repo / ARCHIVE_CONFIG_FILENAME)
    baseline_path = _repo_file(repo, config["baseline_universe_report"])
    current_path = _repo_file(repo, config["current_snapshot_report"])
    baseline = validate_okx_universe_capture(baseline_path)
    current = validate_future_universe_snapshot(current_path, repo / ARCHIVE_CONFIG_FILENAME)
    _validate_inputs(config, repo, transition, baseline, baseline_path, current, current_path, Path(execution_mapping_report))
    previous_received = _parse_iso(transition.report["identity"]["previous_received_at"])
    current_received = _parse_iso(transition.report["identity"]["current_received_at"])
    signals = _signal_grid(previous_received, current_received)
    baseline_membership = _baseline_membership(baseline, config["tracked_inst_ids"])
    current_state = _current_transition_state(transition.tracked, config["tracked_inst_ids"])
    eligibility = _eligibility_rows(signals, config, baseline_membership, current_state)
    if len(signals) != 161 or len(eligibility) != 966:
        raise MarketDataError("membership_bar_gate_expected_grid_shape_mismatch")
    first_execution = signals[0] + timedelta(hours=config["execution_offset_bars"])
    last_execution = signals[-1] + timedelta(hours=config["execution_offset_bars"])
    epochs = [{
        "epoch_id": "epoch-0001",
        "previous_snapshot_sha256": transition.report["identity"]["previous_snapshot_sha256"],
        "current_snapshot_sha256": transition.report["identity"]["current_snapshot_sha256"],
        "previous_received_at": _iso(previous_received),
        "current_received_at": _iso(current_received),
        "first_signal_timestamp": _iso(signals[0]),
        "last_signal_timestamp": _iso(signals[-1]),
        "first_execution_timestamp": _iso(first_execution),
        "last_execution_timestamp": _iso(last_execution),
        "signal_count": len(signals),
        "row_count": len(eligibility),
        "policy_id": config["policy_id"],
    }]
    constraints = [{"key": key, "value": json.dumps(value, ensure_ascii=False, sort_keys=True)} for key, value in _flatten(config).items()]
    epochs_bytes = _csv_bytes(epochs, EPOCH_FIELDS)
    eligibility_bytes = _csv_bytes(eligibility, DATASET_FIELDS)
    constraints_bytes = _csv_bytes(constraints, CONSTRAINT_FIELDS)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": config["policy_id"],
        "baseline_capture_sha256": config["baseline_capture_sha256"],
        "baseline_report_sha256": _sha256(baseline_path),
        "current_snapshot_sha256": config["current_snapshot_sha256"],
        "current_snapshot_report_sha256": _sha256(current_path),
        "transition_sha256": config["transition_sha256"],
        "transition_report_sha256": _sha256(transition_path),
        "execution_mapping_sha256": config["execution_mapping_sha256"],
        "execution_mapping_report_sha256": _sha256(Path(execution_mapping_report)),
        "tracked_inst_ids": config["tracked_inst_ids"],
        "dataset_ids": config["dataset_ids"],
        "timing": {"timeframe": config["timeframe"], "signal_completion_delay_bars": config["signal_completion_delay_bars"], "execution_offset_bars": config["execution_offset_bars"]},
        "epoch": {"first_signal_timestamp": _iso(signals[0]), "last_signal_timestamp": _iso(signals[-1]), "first_execution_timestamp": _iso(first_execution), "last_execution_timestamp": _iso(last_execution), "signal_count": len(signals), "row_count": len(eligibility)},
        "artifacts": {"epochs_sha256": _digest(epochs_bytes), "eligibility_sha256": _digest(eligibility_bytes), "constraints_sha256": _digest(constraints_bytes)},
        "claims": config["claims"],
    }
    gate_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-membership-bar-gate.{gate_sha}"
    epochs_path = output / f"{stem}.epochs.csv"
    eligibility_path = output / f"{stem}.eligibility.csv"
    constraints_path = output / f"{stem}.constraints.csv"
    report_path = output / f"{stem}.json"
    _commit_bytes(epochs_path, epochs_bytes)
    _commit_bytes(eligibility_path, eligibility_bytes)
    _commit_bytes(constraints_path, constraints_bytes)
    report = {
        "schema_version": SCHEMA_VERSION,
        "gate_sha256": gate_sha,
        "gate_status": GATE_STATUS,
        "identity": identity,
        "future_only_membership_evidence": True,
        "membership_policy_piecewise_constant_between_snapshots": True,
        "continuous_membership_directly_observed": False,
        "historical_point_in_time_membership": False,
        "historical_survivorship_bias_resolved": False,
        "historical_delisted_assets_recovered": False,
        "profitability_evidence": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "automatic_replacement": False,
        "retroactive_membership_change": False,
        "artifacts": {
            "epochs": {"filename": epochs_path.name, "sha256": identity["artifacts"]["epochs_sha256"], "row_count": len(epochs)},
            "eligibility": {"filename": eligibility_path.name, "sha256": identity["artifacts"]["eligibility_sha256"], "row_count": len(eligibility)},
            "constraints": {"filename": constraints_path.name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)},
            "report": {"filename": report_path.name},
        },
    }
    _commit_bytes(report_path, _pretty_json_bytes(report))
    return MembershipBarGateResult(report, {"epochs": str(epochs_path), "eligibility": str(eligibility_path), "constraints": str(constraints_path), "report": str(report_path)})


def validate_okx_future_universe_transition(report_path: str | Path, archive_config_path: str | Path) -> ValidatedTransition:
    path = Path(report_path).resolve()
    repo = _repo_root(path)
    archive_config = Path(archive_config_path).resolve()
    report = _load_json(path)
    transition_sha = report.get("transition_sha256")
    identity = report.get("identity")
    if not isinstance(transition_sha, str) or not isinstance(identity, dict) or path.name != f"okx-universe-transition.{transition_sha}.json" or _digest(_canonical_json_bytes(identity)) != transition_sha or report.get("audit_status") != "verified_okx_future_universe_transition":
        raise MarketDataError("okx_future_transition_identity_mismatch")
    if report.get("future_only_evidence") is not True or report.get("readiness_changed") is not False or report.get("profitability_evidence") is not False or report.get("automatic_replacement") is not False:
        raise MarketDataError("okx_future_transition_claims_mismatch")
    if identity.get("exit_policy", {}).get("automatic_replacement") is not False or identity.get("exit_policy", {}).get("historical_retroactive_exit") is not False:
        raise MarketDataError("okx_future_transition_exit_policy_mismatch")
    tracked_path = _sibling(path, report["artifacts"]["tracked"]["filename"])
    changes_path = _sibling(path, report["artifacts"]["changes"]["filename"])
    policy_path = _sibling(path, report["artifacts"]["policy"]["filename"])
    for artifact, sibling in (("tracked", tracked_path), ("changes", changes_path), ("policy", policy_path)):
        if _sha256(sibling) != report["artifacts"][artifact]["sha256"] or len(_read_csv(sibling)) != report["artifacts"][artifact]["row_count"]:
            raise MarketDataError("okx_future_transition_artifact_mismatch")
    if not archive_config.is_file() or not archive_config.is_relative_to(repo):
        raise MarketDataError("okx_future_transition_archive_config_path_mismatch")
    tracked = _read_csv(tracked_path)
    if [row.get("inst_id") for row in tracked] != EXPECTED_INST_IDS:
        raise MarketDataError("okx_future_transition_tracked_assets_mismatch")
    return ValidatedTransition(path, report, tuple(tracked))


def validate_prospective_membership_bar_gate(
    report_path: str | Path,
    policy_path: str | Path,
) -> ValidatedMembershipBarGate:
    path = Path(report_path).resolve()
    repo = _repo_root(path)
    config = load_membership_bar_gate_config(policy_path, repo)
    report = _load_json(path)
    gate_sha = report.get("gate_sha256")
    identity = report.get("identity")
    if not isinstance(gate_sha, str) or not isinstance(identity, dict) or path.name != f"prospective-membership-bar-gate.{gate_sha}.json" or _digest(_canonical_json_bytes(identity)) != gate_sha or report.get("gate_status") != GATE_STATUS:
        raise MarketDataError("membership_bar_gate_identity_mismatch")
    if report.get("future_only_membership_evidence") is not True or report.get("continuous_membership_directly_observed") is not False or report.get("historical_point_in_time_membership") is not False or report.get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False or report.get("retroactive_membership_change") is not False:
        raise MarketDataError("membership_bar_gate_claims_mismatch")
    if identity.get("policy_id") != config["policy_id"] or identity.get("tracked_inst_ids") != config["tracked_inst_ids"] or identity.get("dataset_ids") != config["dataset_ids"]:
        raise MarketDataError("membership_bar_gate_policy_identity_mismatch")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MarketDataError("membership_bar_gate_artifacts_missing")
    paths: dict[str, Path] = {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for kind in ("epochs", "eligibility", "constraints"):
        info = artifacts.get(kind)
        if not isinstance(info, dict):
            raise MarketDataError("membership_bar_gate_artifact_metadata_missing")
        artifact_path = _sibling(path, info.get("filename"))
        content = artifact_path.read_bytes()
        if _digest(content) != info.get("sha256"):
            raise MarketDataError("membership_bar_gate_artifact_hash_mismatch")
        parsed = _read_csv(artifact_path)
        if len(parsed) != info.get("row_count"):
            raise MarketDataError("membership_bar_gate_artifact_row_count_mismatch")
        paths[kind] = artifact_path
        rows[kind] = parsed
    eligibility = rows["eligibility"]
    if len(rows["epochs"]) != 1 or len(eligibility) != 966 or {row.get("retroactive_change_applied") for row in eligibility} != {"false"} or {row.get("inst_id") for row in eligibility} != set(config["tracked_inst_ids"]):
        raise MarketDataError("membership_bar_gate_grid_mismatch")
    return ValidatedMembershipBarGate(path, report, tuple(rows["epochs"]), tuple(eligibility))


def load_membership_bar_gate_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"membership bar gate config not found: {config_path}")
    if config_path.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("membership bar gate config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("membership bar gate config YAML is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("membership bar gate config must be a mapping")
    frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("membership bar gate config must equal the frozen config")
    if repo is not None and not config_path.is_relative_to(repo.resolve()):
        raise MarketDataError("membership_bar_gate_config_path_escape")
    if value["tracked_inst_ids"] != EXPECTED_INST_IDS or value["dataset_ids"] != ["okx_btc_usdt_1h_direct_frozen", "okx_eth_usdt_1h_direct_frozen", "okx_sol_usdt_1h_direct_frozen", "okx_knc_usdt_1h_frozen", "okx_swftc_usdt_1h_frozen", "okx_bico_usdt_1h_frozen"]:
        raise MarketDataError("membership_bar_gate_asset_order_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_membership_bar_gate_result(result: MembershipBarGateResult) -> str:
    report = result.report
    return "\n".join([f"gate_status: {report['gate_status']}", f"gate_sha256: {report['gate_sha256']}", f"signal_count: {report['identity']['epoch']['signal_count']}", f"eligibility_row_count: {report['identity']['epoch']['row_count']}", "future_only_membership_evidence: true", "retroactive_membership_change: false", "pnl_computation_authorized: false", "readiness_changed: false"])


def _validate_inputs(config: dict[str, Any], repo: Path, transition: ValidatedTransition, baseline: Any, baseline_path: Path, current: FutureUniverseSnapshot, current_path: Path, mapping_path: Path) -> None:
    if _sha256(baseline_path) != config["baseline_report_sha256"] or baseline.capture["capture_sha256"] != config["baseline_capture_sha256"] or baseline.capture["identity"]["snapshot"]["received_at"] != "2026-08-02T15:49:19.356645+00:00":
        raise MarketDataError("membership_bar_gate_baseline_mismatch")
    if _sha256(current_path) != config["current_snapshot_report_sha256"] or current.report["snapshot_sha256"] != config["current_snapshot_sha256"]:
        raise MarketDataError("membership_bar_gate_current_snapshot_mismatch")
    if _sha256(transition.report_path) != config["transition_report_sha256"] or transition.report["transition_sha256"] != config["transition_sha256"]:
        raise MarketDataError("membership_bar_gate_transition_mismatch")
    if _sha256(mapping_path) != config["execution_mapping_report_sha256"]:
        raise MarketDataError("membership_bar_gate_mapping_report_mismatch")
    mapping = _load_json(mapping_path)
    mapping_config = load_direct_execution_mapping_config(repo / MAPPING_CONFIG_FILENAME, repo)
    if mapping.get("audit_sha256") != config["execution_mapping_sha256"] or mapping.get("audit_status") != "verified_okx_direct_six_1h_execution_mapping_feasibility" or mapping.get("feasibility", {}).get("execution_price_mapping_feasible") is not True or mapping.get("feasibility", {}).get("pnl_computation_authorized") is not False or mapping.get("identity", {}).get("config", {}).get("sha256") != _sha256(repo / MAPPING_CONFIG_FILENAME) or mapping_config["execution_offset_bars"] != 2:
        raise MarketDataError("membership_bar_gate_mapping_identity_mismatch")
    if transition.report["identity"]["tracked_inst_ids"] != config["tracked_inst_ids"] or transition.report["identity"]["previous_received_at"] >= transition.report["identity"]["current_received_at"]:
        raise MarketDataError("membership_bar_gate_transition_semantics_mismatch")


def _baseline_membership(baseline: Any, tracked_ids: list[str]) -> dict[str, str]:
    decisions = baseline.capture["identity"]["snapshot"]["eligibility_decisions"]
    by_id = {str(row["inst_id"]): row for row in decisions}
    result: dict[str, str] = {}
    for inst_id in tracked_ids:
        row = by_id.get(inst_id)
        if row is None or row.get("state") != "live":
            raise MarketDataError("membership_bar_gate_baseline_asset_not_eligible")
        reasons = {part for part in str(row.get("exclusion_reasons", "")).split("|") if part}
        if row.get("eligible") is True or reasons in ({"existing_base_exclusion"}, {"existing_base_excluded"}):
            result[inst_id] = "retained"
        else:
            raise MarketDataError("membership_bar_gate_baseline_asset_not_eligible")
    return result


def _current_transition_state(rows: tuple[dict[str, Any], ...], tracked_ids: list[str]) -> dict[str, str]:
    by_id = {str(row["inst_id"]): row for row in rows}
    if set(by_id) != set(tracked_ids):
        raise MarketDataError("membership_bar_gate_current_tracked_set_mismatch")
    return {inst_id: str(by_id[inst_id]["tracked_membership_status"]) for inst_id in tracked_ids}


def _eligibility_rows(signals: list[datetime], config: dict[str, Any], baseline: dict[str, str], current: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for signal in signals:
        for inst_id, dataset_id in zip(config["tracked_inst_ids"], config["dataset_ids"], strict=True):
            rows.append({"epoch_id": "epoch-0001", "inst_id": inst_id, "dataset_id": dataset_id, "signal_timestamp": _iso(signal), "completion_timestamp": _iso(signal + timedelta(hours=1)), "execution_timestamp": _iso(signal + timedelta(hours=2)), "previous_snapshot_membership_state": baseline[inst_id], "current_snapshot_transition_state": current[inst_id], "eligible_for_closed_epoch": baseline[inst_id] == "retained", "retroactive_change_applied": False})
    return rows


def _signal_grid(previous: datetime, current: datetime) -> list[datetime]:
    first = _ceil_hour(previous)
    latest_execution = _floor_strict_hour(current)
    last = latest_execution - timedelta(hours=2)
    if first > last:
        raise MarketDataError("membership_bar_gate_empty_epoch")
    count = int((last - first).total_seconds() // 3600) + 1
    return [first + timedelta(hours=index) for index in range(count)]


def _ceil_hour(value: datetime) -> datetime:
    base = value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return base if value == base else base + timedelta(hours=1)


def _floor_strict_hour(value: datetime) -> datetime:
    base = value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return base - timedelta(hours=1) if value == base else base


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise MarketDataError("membership_bar_gate_timestamp_not_timezone_aware")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            result.update(_flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return result
    return {prefix: value}


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row[field]) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _read_csv(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("membership_bar_gate_invalid_csv") from exc


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("membership_bar_gate_invalid_json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("membership_bar_gate_invalid_json")
    return value


def _repo_file(repo: Path, relative: str) -> Path:
    candidate = (repo / relative).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError as exc:
        raise MarketDataError("membership_bar_gate_path_escape") from exc
    if not candidate.is_file():
        raise MarketDataError("membership_bar_gate_artifact_missing")
    return candidate


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    try:
        output.relative_to((repo / "reports").resolve())
    except ValueError as exc:
        raise ValueError("membership bar gate output must stay inside reports") from exc
    output.mkdir(parents=True, exist_ok=True)
    return output


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"membership_bar_gate_content_addressed_collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".membership-bar-gate-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
