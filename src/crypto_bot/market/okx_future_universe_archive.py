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
from typing import Any, Callable

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.okx_universe_intake import (
    OKX_INSTRUMENTS_ENDPOINT,
    evaluate_okx_public_instrument_snapshot,
    fetch_okx_public_instruments_snapshot,
    validate_okx_universe_capture,
)


SCHEMA_VERSION = 1
SNAPSHOT_STATUS = "complete_okx_future_only_universe_snapshot"
TRANSITION_STATUS = "verified_okx_future_universe_transition"
DEFAULT_CONFIG_FILENAME = "config.okx-future-universe-archive.example.yaml"
ELIGIBILITY_FIELDS = (
    "inst_id", "base_ccy", "quote_ccy", "inst_type", "state", "rule_type",
    "inst_category", "list_time", "cont_td_sw_time", "effective_continuous_start",
    "eligible", "exclusion_reasons", "selection_sha256", "selected_rank",
)
TRACKED_FIELDS = (
    "inst_id", "snapshot_received_at", "present", "state", "policy_eligible",
    "tracked_membership_status", "exclusion_reasons", "effective_continuous_start",
)
CHANGE_FIELDS = (
    "inst_id", "change_type", "previous_present", "current_present",
    "previous_eligible", "current_eligible", "previous_reasons", "current_reasons",
)
POLICY_FIELDS = ("key", "value")


@dataclass(frozen=True)
class FutureUniverseSnapshot:
    report_path: Path
    report: dict[str, Any]
    eligibility: tuple[dict[str, Any], ...]
    tracked: tuple[dict[str, Any], ...]
    raw_path: Path


@dataclass(frozen=True)
class FutureUniverseResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def capture_okx_future_universe_snapshot(
    baseline_universe_report: str | Path,
    archive_config_path: str | Path,
    output_dir: str | Path,
    *,
    fetcher: Callable[[str], bytes] | None = None,
    received_at: datetime | None = None,
) -> FutureUniverseResult:
    config = load_future_universe_archive_config(archive_config_path)
    baseline_path = Path(baseline_universe_report).resolve()
    repo = _repo_root(baseline_path)
    config = load_future_universe_archive_config(archive_config_path, repo)
    baseline = validate_okx_universe_capture(baseline_path)
    _validate_baseline(baseline, baseline_path, config)
    policy = _load_policy(repo, config)
    raw = fetch_okx_public_instruments_snapshot(fetcher)
    decisions, selected = evaluate_okx_public_instrument_snapshot(raw, policy)
    now = received_at or datetime.now(timezone.utc)
    received = _iso(now)
    if received <= config["baseline_snapshot_received_at"]:
        raise MarketDataError("okx_future_snapshot_received_at_not_after_baseline")
    tracked = _tracked_rows(decisions, config["tracked_inst_ids"], received)
    return _write_snapshot(
        repo,
        output_dir,
        config,
        baseline,
        baseline_path,
        raw,
        decisions,
        selected,
        tracked,
        received,
    )


def audit_okx_future_universe_transition(
    previous_snapshot: str | Path,
    current_snapshot: str | Path,
    archive_config_path: str | Path,
    output_dir: str | Path,
) -> FutureUniverseResult:
    previous_path = Path(previous_snapshot).resolve()
    repo = _repo_root(previous_path)
    config = load_future_universe_archive_config(archive_config_path, repo)
    previous = _load_snapshot_or_legacy(previous_path, config, repo)
    current = validate_future_universe_snapshot(current_snapshot, archive_config_path)
    if _repo_root(current.report_path) != repo:
        raise MarketDataError("okx_future_transition_repo_mismatch")
    changes, tracked, policy_rows = build_future_universe_transition_rows(previous, current, config)
    changes_bytes = _csv_bytes(changes, CHANGE_FIELDS)
    tracked_bytes = _csv_bytes(tracked, TRACKED_FIELDS)
    policy_bytes = _csv_bytes(policy_rows, POLICY_FIELDS)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": config["policy_id"],
        "previous_snapshot_sha256": previous.report.get("snapshot_sha256") or previous.report["identity"].get("snapshot_sha256"),
        "current_snapshot_sha256": current.report["snapshot_sha256"],
        "previous_received_at": previous.report["identity"]["received_at"],
        "current_received_at": current.report["identity"]["received_at"],
        "baseline_capture_sha256": config["baseline_capture_sha256"],
        "policy_file_sha256": config["eligibility_policy_file_sha256"],
        "exit_policy": {
            "exit_signal_effective_at": config["exit_signal_effective_at"],
            "historical_retroactive_exit": config["historical_retroactive_exit"],
            "automatic_replacement": config["automatic_replacement"],
            "carry_membership_after_ineligibility": config["carry_membership_after_ineligibility"],
        },
        "tracked_inst_ids": config["tracked_inst_ids"],
        "counts": {
            "change_count": len(changes),
            "tracked_count": len(tracked),
        },
        "claims": config["claims"],
        "artifacts": {
            "changes_sha256": _digest(changes_bytes),
            "tracked_sha256": _digest(tracked_bytes),
            "policy_sha256": _digest(policy_bytes),
        },
    }
    transition_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"okx-universe-transition.{transition_sha}"
    changes_path = output / f"{stem}.changes.csv"
    tracked_path = output / f"{stem}.tracked-assets.csv"
    policy_path = output / f"{stem}.policy.csv"
    report_path = output / f"{stem}.json"
    _commit_bytes(changes_path, changes_bytes)
    _commit_bytes(tracked_path, tracked_bytes)
    _commit_bytes(policy_path, policy_bytes)
    report = {
        "schema_version": SCHEMA_VERSION,
        "transition_sha256": transition_sha,
        "audit_status": TRANSITION_STATUS,
        "identity": identity,
        "future_only_evidence": True,
        "profitability_evidence": False,
        "readiness_changed": False,
        "automatic_replacement": False,
        "artifacts": {
            "changes": {"filename": changes_path.name, "sha256": identity["artifacts"]["changes_sha256"], "row_count": len(changes)},
            "tracked": {"filename": tracked_path.name, "sha256": identity["artifacts"]["tracked_sha256"], "row_count": len(tracked)},
            "policy": {"filename": policy_path.name, "sha256": identity["artifacts"]["policy_sha256"], "row_count": len(policy_rows)},
            "report": {"filename": report_path.name},
        },
    }
    _commit_bytes(report_path, _pretty_json_bytes(report))
    return FutureUniverseResult(
        report,
        {"changes": str(changes_path), "tracked": str(tracked_path), "policy": str(policy_path), "report": str(report_path)},
    )


def build_future_universe_transition_rows(
    previous: FutureUniverseSnapshot,
    current: FutureUniverseSnapshot,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    """Build canonical transition rows from already validated snapshots.

    This is the shared pure diff seam for the legacy transition audit and the
    prospective admission-gated materializer. It performs no I/O and does not
    alter the established transition artifact identity.
    """
    if current.report["identity"]["baseline_capture_sha256"] != config["baseline_capture_sha256"]:
        raise MarketDataError("okx_future_transition_baseline_mismatch")
    if previous.report["identity"]["policy_file_sha256"] != current.report["identity"]["policy_file_sha256"]:
        raise MarketDataError("okx_future_transition_policy_mismatch")
    if current.report["identity"]["received_at"] <= previous.report["identity"]["received_at"]:
        raise MarketDataError("okx_future_transition_order_mismatch")
    changes = _changes(previous.eligibility, current.eligibility)
    tracked = _transition_tracked(previous.tracked, current.tracked)
    policy_rows = [
        {"key": key, "value": json.dumps(value, ensure_ascii=False, sort_keys=True)}
        for key, value in _flatten(config).items()
    ]
    return changes, tracked, policy_rows


def validate_future_universe_snapshot(
    report_path: str | Path,
    archive_config_path: str | Path,
) -> FutureUniverseSnapshot:
    path = Path(report_path).resolve()
    repo = _repo_root(path)
    config = load_future_universe_archive_config(archive_config_path, repo)
    report = _load_json(path)
    identity = report.get("identity")
    snapshot_sha = report.get("snapshot_sha256")
    if not isinstance(identity, dict) or not isinstance(snapshot_sha, str) or (
        path.name != f"okx-universe-snapshot.{snapshot_sha}.json"
        or _digest(_canonical_json_bytes(identity)) != snapshot_sha
        or report.get("capture_status") != SNAPSHOT_STATUS
        or report.get("readiness_changed") is not False
        or report.get("profitability_evidence") is not False
    ):
        raise MarketDataError("okx_future_snapshot_identity_mismatch")
    _validate_snapshot_identity(identity, config)
    raw_path = _sibling(path, identity["raw_filename"])
    eligible_path = _sibling(path, identity["eligible_filename"])
    tracked_path = _sibling(path, identity["tracked_filename"])
    if (
        _sha256(raw_path) != identity["raw_sha256"]
        or _sha256(eligible_path) != identity["eligibility_sha256"]
        or _sha256(tracked_path) != identity["tracked_sha256"]
    ):
        raise MarketDataError("okx_future_snapshot_artifact_hash_mismatch")
    policy = _load_policy(repo, config)
    decisions, selected = evaluate_okx_public_instrument_snapshot(raw_path.read_bytes(), policy)
    eligibility = _read_csv(eligible_path)
    tracked = _read_csv(tracked_path)
    expected_eligibility = [
        {field: _csv_text(row[field]) for field in ELIGIBILITY_FIELDS} for row in decisions
    ]
    expected_tracked = [
        {field: _csv_text(row[field]) for field in TRACKED_FIELDS} for row in tracked
    ]
    if expected_eligibility != eligibility or selected != identity["selected_inst_ids"]:
        raise MarketDataError("okx_future_snapshot_replay_mismatch")
    if tracked != expected_tracked:
        raise MarketDataError("okx_future_snapshot_tracked_replay_mismatch")
    return FutureUniverseSnapshot(path, report, tuple(eligibility), tuple(tracked), raw_path)


def format_future_universe_result(result: FutureUniverseResult) -> str:
    report = result.report
    if "snapshot_sha256" in report:
        return "\n".join([
            f"capture_status: {report['capture_status']}",
            f"snapshot_sha256: {report['snapshot_sha256']}",
            f"snapshot_received_at: {report['identity']['received_at']}",
            f"instrument_count: {report['identity']['instrument_count']}",
            "future_only_evidence: true",
            "automatic_replacement: false",
            "readiness_changed: false",
        ])
    return "\n".join([
        f"audit_status: {report['audit_status']}",
        f"transition_sha256: {report['transition_sha256']}",
        f"change_count: {report['identity']['counts']['change_count']}",
        "future_only_evidence: true",
        "automatic_replacement: false",
        "readiness_changed: false",
    ])


def load_future_universe_archive_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"future universe archive config not found: {config_path}")
    if config_path.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("future universe archive config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("future universe archive config YAML is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("future universe archive config must be a mapping")
    frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("future universe archive config must equal the frozen config")
    if repo is not None and not config_path.is_relative_to(repo.resolve()):
        raise MarketDataError("okx_future_archive_config_path_escape")
    if value["tracked_inst_ids"] != ["BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT"]:
        raise MarketDataError("okx_future_archive_tracked_assets_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def _write_snapshot(repo: Path, output_dir: str | Path, config: dict[str, Any], baseline: Any, baseline_path: Path, raw: bytes, decisions: list[dict[str, Any]], selected: list[str], tracked: list[dict[str, Any]], received: str) -> FutureUniverseResult:
    policy_file = _repo_file(repo, config["eligibility_policy_file"])
    eligibility_bytes = _csv_bytes(decisions, ELIGIBILITY_FIELDS)
    tracked_bytes = _csv_bytes(tracked, TRACKED_FIELDS)
    raw_sha = _digest(raw)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": config["policy_id"],
        "baseline_capture_sha256": config["baseline_capture_sha256"],
        "baseline_capture_report_sha256": _sha256(baseline_path),
        "policy_file_sha256": _sha256(policy_file),
        "received_at": received,
        "endpoint": OKX_INSTRUMENTS_ENDPOINT,
        "request_params": {"instType": "SPOT"},
        "raw_sha256": raw_sha,
        "instrument_count": len(decisions),
        "selected_inst_ids": selected,
        "eligibility_sha256": _digest(eligibility_bytes),
        "tracked_sha256": _digest(tracked_bytes),
        "tracked_assets": tracked,
        "raw_filename": f"okx-universe-snapshot.{raw_sha}.raw.json",
        "eligible_filename": f"okx-universe-snapshot.{raw_sha}.eligible.csv",
        "tracked_filename": f"okx-universe-snapshot.{raw_sha}.tracked-assets.csv",
        "claims": config["claims"],
    }
    snapshot_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"okx-universe-snapshot.{snapshot_sha}"
    raw_path = output / identity["raw_filename"]
    eligible_path = output / identity["eligible_filename"]
    tracked_path = output / identity["tracked_filename"]
    report = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_sha256": snapshot_sha,
        "capture_status": SNAPSHOT_STATUS,
        "identity": identity,
        "future_only_evidence": True,
        "profitability_evidence": False,
        "readiness_changed": False,
        "automatic_replacement": False,
        "artifacts": {
            "raw": {"filename": raw_path.name, "sha256": raw_sha},
            "eligible": {"filename": eligible_path.name, "sha256": identity["eligibility_sha256"], "row_count": len(decisions)},
            "tracked": {"filename": tracked_path.name, "sha256": identity["tracked_sha256"], "row_count": len(tracked)},
            "report": {"filename": f"{stem}.json"},
        },
    }
    report_path = output / f"{stem}.json"
    _commit_bytes(raw_path, raw)
    _commit_bytes(eligible_path, eligibility_bytes)
    _commit_bytes(tracked_path, tracked_bytes)
    _commit_bytes(report_path, _pretty_json_bytes(report))
    return FutureUniverseResult(report, {"raw": str(raw_path), "eligible": str(eligible_path), "tracked": str(tracked_path), "report": str(report_path)})


def _validate_baseline(baseline: Any, path: Path, config: dict[str, Any]) -> None:
    if (
        baseline.capture.get("capture_sha256") != config["baseline_capture_sha256"]
        or _sha256(path) != config["baseline_capture_report_sha256"]
        or baseline.capture["identity"]["snapshot"]["received_at"] != config["baseline_snapshot_received_at"]
    ):
        raise MarketDataError("okx_future_archive_baseline_mismatch")


def _load_policy(repo: Path, config: dict[str, Any]) -> dict[str, Any]:
    path = _repo_file(repo, config["eligibility_policy_file"])
    if _sha256(path) != config["eligibility_policy_file_sha256"]:
        raise MarketDataError("okx_future_archive_policy_hash_mismatch")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise MarketDataError("okx_future_archive_policy_invalid") from exc
    if not isinstance(value, dict):
        raise MarketDataError("okx_future_archive_policy_invalid")
    return value


def _tracked_rows(decisions: list[dict[str, Any]], tracked_ids: list[str], received: str) -> list[dict[str, Any]]:
    by_id = {str(row["inst_id"]): row for row in decisions}
    rows = []
    for inst_id in tracked_ids:
        decision = by_id.get(inst_id)
        if decision is None:
            rows.append({"inst_id": inst_id, "snapshot_received_at": received, "present": False, "state": "", "policy_eligible": False, "tracked_membership_status": "disappeared_from_snapshot", "exclusion_reasons": "", "effective_continuous_start": ""})
            continue
        rows.append({
            "inst_id": inst_id,
            "snapshot_received_at": received,
            "present": True,
            "state": decision["state"],
            "policy_eligible": decision["eligible"],
            "tracked_membership_status": _tracked_status(decision),
            "exclusion_reasons": decision["exclusion_reasons"],
            "effective_continuous_start": decision["effective_continuous_start"],
        })
    return rows


def _tracked_status(decision: dict[str, Any]) -> str:
    if decision["state"] != "live":
        return "no_longer_live"
    reasons = set(filter(None, str(decision["exclusion_reasons"]).split("|")))
    reasons.discard("existing_base_exclusion")
    return "retained" if not reasons else "became_ineligible"


def _changes(previous: tuple[dict[str, Any], ...], current: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    before = {row["inst_id"]: row for row in previous}
    after = {row["inst_id"]: row for row in current}
    rows = []
    for inst_id in sorted(set(before) | set(after)):
        left = before.get(inst_id)
        right = after.get(inst_id)
        if left is None:
            change = "added"
        elif right is None:
            change = "removed"
        elif _normalized_row(left) != _normalized_row(right):
            change = "eligibility_changed"
        else:
            continue
        rows.append({"inst_id": inst_id, "change_type": change, "previous_present": left is not None, "current_present": right is not None, "previous_eligible": _boolish(left.get("eligible", False)) if left else False, "current_eligible": _boolish(right.get("eligible", False)) if right else False, "previous_reasons": left.get("exclusion_reasons", "") if left else "", "current_reasons": right.get("exclusion_reasons", "") if right else ""})
    return rows


def _normalized_row(row: dict[str, Any]) -> dict[str, str]:
    return {field: _csv_text(row.get(field, "")) for field in ELIGIBILITY_FIELDS}


def _boolish(value: Any) -> bool:
    return value is True or value == "true"


def _transition_tracked(previous: tuple[dict[str, Any], ...], current: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    before = {row["inst_id"]: row for row in previous}
    rows = []
    for row in current:
        prior = before.get(row["inst_id"])
        status = row["tracked_membership_status"]
        if status == "retained" and prior is not None and prior["tracked_membership_status"] == "retained":
            status = "retained"
        rows.append(row | {"tracked_membership_status": status})
    return rows


def _load_snapshot_or_legacy(path: Path, config: dict[str, Any], repo: Path) -> FutureUniverseSnapshot:
    try:
        report = _load_json(path)
    except MarketDataError:
        report = {}
    if report.get("capture_status") == SNAPSHOT_STATUS:
        return validate_future_universe_snapshot(path, repo / DEFAULT_CONFIG_FILENAME)
    legacy = validate_okx_universe_capture(path)
    _validate_baseline(legacy, path, config)
    received = legacy.capture["identity"]["snapshot"]["received_at"]
    tracked = _tracked_rows(list(legacy.eligibility), config["tracked_inst_ids"], received)
    identity = {"snapshot_sha256": legacy.capture["capture_sha256"], "received_at": received, "policy_file_sha256": config["eligibility_policy_file_sha256"], "baseline_capture_sha256": config["baseline_capture_sha256"]}
    report = {"identity": identity}
    return FutureUniverseSnapshot(path, report, legacy.eligibility, tuple(tracked), Path(""))


def _validate_snapshot_identity(identity: dict[str, Any], config: dict[str, Any]) -> None:
    if (
        identity.get("baseline_capture_sha256") != config["baseline_capture_sha256"]
        or identity.get("policy_file_sha256") != config["eligibility_policy_file_sha256"]
        or identity.get("request_params") != {"instType": "SPOT"}
        or identity.get("endpoint") != OKX_INSTRUMENTS_ENDPOINT
        or identity.get("claims") != config["claims"]
    ):
        raise MarketDataError("okx_future_snapshot_policy_identity_mismatch")


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            result.update(_flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return result
    return {prefix: value}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("okx_future_snapshot_invalid_csv") from exc


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


def _csv_text(value: Any) -> str:
    rendered = _csv_value(value)
    return "" if rendered is None else str(rendered)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _repo_root(path: Path) -> Path:
    for candidate in (path.parent, *path.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").is_file():
            return candidate.resolve()
    raise MarketDataError("okx_future_archive_repo_missing")


def _repo_file(repo: Path, relative: str) -> Path:
    candidate = (repo / relative).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError as exc:
        raise MarketDataError("okx_future_archive_path_escape") from exc
    if not candidate.is_file():
        raise MarketDataError("okx_future_archive_artifact_missing")
    return candidate


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    try:
        output.relative_to((repo / "reports").resolve())
    except ValueError as exc:
        raise ValueError("future universe output must stay inside reports") from exc
    output.mkdir(parents=True, exist_ok=True)
    return output


def _sibling(report: Path, filename: Any) -> Path:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise MarketDataError("okx_future_snapshot_invalid_artifact_path")
    path = report.parent / filename
    if not path.is_file():
        raise MarketDataError("okx_future_snapshot_artifact_missing")
    return path


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("okx_future_archive_invalid_json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("okx_future_archive_invalid_json")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"okx_future_archive_content_addressed_collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".okx-future-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
