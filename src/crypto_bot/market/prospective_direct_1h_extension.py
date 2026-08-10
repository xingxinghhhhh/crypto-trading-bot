from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.data_quality import validate_ohlcv_csv
from crypto_bot.market.okx_direct_six_asset_migration import validate_okx_direct_six_asset_1h_migration
from crypto_bot.market.okx_universe_intake import (
    download_okx_public_history,
    okx_history_rows_to_csv_bytes,
    replay_okx_public_history,
)
from crypto_bot.market.prospective_membership_bar_gate import (
    ValidatedMembershipBarGate,
    _repo_root,
    _sha256,
    _sibling,
    validate_prospective_membership_bar_gate,
)


SCHEMA_VERSION = 1
CAPTURE_STATUS = "complete_prospective_direct_okx_1h_extension_capture"
AUDIT_STATUS = "verified_prospective_direct_okx_1h_extension"
DEFAULT_CONFIG_FILENAME = "config.okx-prospective-direct-1h-extension.example.yaml"
INST_IDS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT")
DATASET_FIELDS = ("inst_id", "baseline_dataset_id", "baseline_tail", "append_head", "append_tail", "append_row_count", "baseline_replacement_prohibited", "continuity_verified")
COVERAGE_FIELDS = ("inst_id", "signal_timestamp", "completion_timestamp", "execution_timestamp", "membership_eligible", "signal_present", "completion_present", "execution_present", "execution_open_finite", "no_fill_or_substitution")
CONSTRAINT_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectiveDirectCaptureResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class ProspectiveDirectAuditResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def capture_prospective_direct_1h_extension(
    membership_gate: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
    *,
    fetcher: Callable[[str], bytes] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> ProspectiveDirectCaptureResult:
    gate_path = Path(membership_gate).resolve()
    repo = _repo_root(gate_path)
    config = load_prospective_direct_config(config_path, repo)
    gate = validate_prospective_membership_bar_gate(gate_path, repo / "config.prospective-membership-bar-gate.example.yaml")
    migration_path = _repo_file(repo, config["migration_report"])
    migration = validate_okx_direct_six_asset_1h_migration(migration_path)
    _validate_gate_and_migration(config, gate, gate_path, migration, migration_path)
    tails = _baseline_tails(migration, config["tracked_inst_ids"])
    start = _ceil_hour(_parse_iso(max(tails.values())) + timedelta(hours=1))
    end = _last_execution(gate)
    if start != _parse_iso("2026-08-02T15:00:00Z") or end != _parse_iso("2026-08-09T10:00:00Z"):
        raise MarketDataError("prospective_direct_extension_derived_window_mismatch")
    start_ms = _timestamp_ms(start)
    end_ms = _timestamp_ms(end)
    output = _reports_output(repo, output_dir)
    prepared: list[dict[str, Any]] = []
    for inst_id in config["tracked_inst_ids"]:
        bundle, pages, rows = download_okx_public_history(
            inst_id,
            start_ms,
            end_ms,
            okx_bar=config["bar"],
            bar_duration_ms=config["bar_duration_ms"],
            fetcher=fetcher,
            request_interval_seconds=0.0,
            sleeper=sleeper or (lambda _seconds: None),
        )
        if len(rows) != 164 or not rows or rows[0][0] != str(start_ms) or rows[-1][0] != str(end_ms):
            raise MarketDataError(f"prospective_direct_extension_shape_mismatch:{inst_id}")
        csv_bytes = okx_history_rows_to_csv_bytes(rows)
        quality = validate_ohlcv_csv_bytes(csv_bytes)
        raw_sha = _digest(bundle)
        canonical_sha = _digest(csv_bytes)
        slug = inst_id.lower().replace("-", "_")
        prepared.append({"inst_id": inst_id, "bundle": bundle, "csv": csv_bytes, "identity": {"inst_id": inst_id, "baseline_tail": tails[inst_id], "append_start": _iso(start), "append_end": _iso(end), "append_row_count": len(rows), "page_count": len(pages), "raw_sha256": raw_sha, "canonical_sha256": canonical_sha, "quality": quality, "raw_filename": f"{slug}.{raw_sha}.raw.jsonl", "append_filename": f"{slug}.{raw_sha}.append.csv"}})
    identity = {"schema_version": SCHEMA_VERSION, "policy_id": config["policy_id"], "membership_gate_sha256": config["membership_gate_sha256"], "migration_sha256": config["migration_sha256"], "tracked_inst_ids": config["tracked_inst_ids"], "append_start": _iso(start), "append_end": _iso(end), "append_row_count_per_asset": 164, "append_row_count_total": 984, "assets": [item["identity"] for item in prepared], "claims": config["claims"]}
    capture_sha = _digest(_canonical_json_bytes(identity))
    stem = f"prospective-direct-1h-capture.{capture_sha}"
    exports: dict[str, str] = {}
    output.mkdir(parents=True, exist_ok=True)
    for item in prepared:
        raw_path = output / item["identity"]["raw_filename"]
        append_path = output / item["identity"]["append_filename"]
        _commit_bytes(raw_path, item["bundle"])
        _commit_bytes(append_path, item["csv"])
        exports[f"{item['inst_id']}_raw"] = str(raw_path)
        exports[f"{item['inst_id']}_append"] = str(append_path)
    report = {"schema_version": SCHEMA_VERSION, "capture_sha256": capture_sha, "capture_status": CAPTURE_STATUS, "identity": identity, "future_only_membership_evidence": True, "profitability_evidence": False, "pnl_computation_authorized": False, "readiness_changed": False, "baseline_replacement_prohibited": True, "artifacts": {"report": {"filename": f"{stem}.json"}}}
    report_path = output / f"{stem}.json"
    _commit_bytes(report_path, _pretty_json_bytes(report))
    exports["report"] = str(report_path)
    return ProspectiveDirectCaptureResult(report, exports)


def audit_prospective_direct_1h_extension(
    capture_report: str | Path,
    membership_gate: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveDirectAuditResult:
    capture_path = Path(capture_report).resolve()
    repo = _repo_root(capture_path)
    config = load_prospective_direct_config(config_path, repo)
    gate = validate_prospective_membership_bar_gate(membership_gate, repo / "config.prospective-membership-bar-gate.example.yaml")
    migration_path = _repo_file(repo, config["migration_report"])
    migration = validate_okx_direct_six_asset_1h_migration(migration_path)
    capture = validate_prospective_direct_capture(capture_path, config, repo)
    _validate_gate_and_migration(config, gate, Path(membership_gate).resolve(), migration, migration_path)
    tails = _baseline_tails(migration, config["tracked_inst_ids"])
    start = _parse_iso(capture.report["identity"]["append_start"])
    end = _parse_iso(capture.report["identity"]["append_end"])
    datasets: list[dict[str, Any]] = []
    append_sets: dict[str, dict[str, list[str]]] = {}
    for item in capture.report["identity"]["assets"]:
        inst_id = item["inst_id"]
        raw_path = _sibling(capture_path, item["raw_filename"])
        append_path = _sibling(capture_path, item["append_filename"])
        rows, _pages = replay_okx_public_history(raw_path.read_bytes(), inst_id, _timestamp_ms(start), _timestamp_ms(end), okx_bar=config["bar"], bar_duration_ms=config["bar_duration_ms"])
        rebuilt = okx_history_rows_to_csv_bytes(rows)
        if rebuilt != append_path.read_bytes() or len(rows) != 164 or _digest(rebuilt) != item["canonical_sha256"]:
            raise MarketDataError(f"prospective_direct_extension_replay_mismatch:{inst_id}")
        quality = validate_ohlcv_csv_bytes(rebuilt)
        if not quality["valid"] or rows[0][0] != str(_timestamp_ms(start)) or rows[-1][0] != str(_timestamp_ms(end)):
            raise MarketDataError(f"prospective_direct_extension_quality_mismatch:{inst_id}")
        append_sets[inst_id] = {"timestamps": [row[0] for row in rows], "opens": [row[1] for row in rows]}
        datasets.append({"inst_id": inst_id, "baseline_dataset_id": _dataset_id(migration, inst_id), "baseline_tail": tails[inst_id], "append_head": _iso(start), "append_tail": _iso(end), "append_row_count": len(rows), "baseline_replacement_prohibited": True, "continuity_verified": True})
    coverage = _coverage_rows(gate, append_sets, config["tracked_inst_ids"])
    if len(coverage) != 966 or any(row["signal_present"] is not True or row["completion_present"] is not True or row["execution_present"] is not True or row["execution_open_finite"] is not True for row in coverage):
        raise MarketDataError("prospective_direct_extension_gate_coverage_mismatch")
    constraints = [{"key": key, "value": json.dumps(value, ensure_ascii=False, sort_keys=True)} for key, value in _flatten(config).items()]
    datasets_bytes = _csv_bytes(datasets, DATASET_FIELDS)
    coverage_bytes = _csv_bytes(coverage, COVERAGE_FIELDS)
    constraints_bytes = _csv_bytes(constraints, CONSTRAINT_FIELDS)
    identity = {"schema_version": SCHEMA_VERSION, "policy_id": config["policy_id"], "capture_sha256": capture.report["capture_sha256"], "capture_report_sha256": _sha256(capture_path), "membership_gate_sha256": config["membership_gate_sha256"], "migration_sha256": config["migration_sha256"], "execution_mapping_sha256": config["execution_mapping_sha256"], "tracked_inst_ids": config["tracked_inst_ids"], "append_start": _iso(start), "append_end": _iso(end), "append_row_count_per_asset": 164, "append_row_count_total": 984, "gate_coverage_row_count": len(coverage), "artifacts": {"datasets_sha256": _digest(datasets_bytes), "coverage_sha256": _digest(coverage_bytes), "constraints_sha256": _digest(constraints_bytes)}, "claims": config["claims"]}
    extension_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-direct-1h-extension.{extension_sha}"
    datasets_path = output / f"{stem}.datasets.csv"
    coverage_path = output / f"{stem}.gate-coverage.csv"
    constraints_path = output / f"{stem}.constraints.csv"
    report_path = output / f"{stem}.json"
    _commit_bytes(datasets_path, datasets_bytes)
    _commit_bytes(coverage_path, coverage_bytes)
    _commit_bytes(constraints_path, constraints_bytes)
    report = {"schema_version": SCHEMA_VERSION, "extension_sha256": extension_sha, "audit_status": AUDIT_STATUS, "identity": identity, "future_only_membership_evidence": True, "prospective_market_data_version_pinned": True, "baseline_replacement_prohibited": True, "profitability_evidence": False, "pnl_computation_authorized": False, "readiness_changed": False, "artifacts": {"datasets": {"filename": datasets_path.name, "sha256": identity["artifacts"]["datasets_sha256"], "row_count": 6}, "coverage": {"filename": coverage_path.name, "sha256": identity["artifacts"]["coverage_sha256"], "row_count": len(coverage)}, "constraints": {"filename": constraints_path.name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)}, "report": {"filename": report_path.name}}}
    _commit_bytes(report_path, _pretty_json_bytes(report))
    return ProspectiveDirectAuditResult(report, {"datasets": str(datasets_path), "coverage": str(coverage_path), "constraints": str(constraints_path), "report": str(report_path)})


def validate_prospective_direct_capture(report_path: str | Path, config: dict[str, Any], repo: Path) -> ProspectiveDirectCaptureResult:
    path = Path(report_path).resolve()
    report = _load_json(path)
    identity = report.get("identity")
    capture_sha = report.get("capture_sha256")
    if not isinstance(identity, dict) or not isinstance(capture_sha, str) or path.name != f"prospective-direct-1h-capture.{capture_sha}.json" or _digest(_canonical_json_bytes(identity)) != capture_sha or report.get("capture_status") != CAPTURE_STATUS or report.get("pnl_computation_authorized") is not False:
        raise MarketDataError("prospective_direct_capture_identity_mismatch")
    if identity.get("tracked_inst_ids") != config["tracked_inst_ids"] or identity.get("append_row_count_per_asset") != 164 or identity.get("append_row_count_total") != 984:
        raise MarketDataError("prospective_direct_capture_policy_mismatch")
    exports = {"report": str(path)}
    for item in identity.get("assets", []):
        for key in ("raw_filename", "append_filename"):
            sibling = _sibling(path, item.get(key))
            if not sibling.is_file():
                raise MarketDataError("prospective_direct_capture_artifact_missing")
            exports[f"{item['inst_id']}_{key}"] = str(sibling)
    return ProspectiveDirectCaptureResult(report, exports)


def load_prospective_direct_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"prospective direct extension config not found: {config_path}")
    if config_path.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("prospective direct extension config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("prospective direct extension config YAML is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("prospective direct extension config must be a mapping")
    frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("prospective direct extension config must equal the frozen config")
    if repo is not None and not config_path.is_relative_to(repo.resolve()):
        raise MarketDataError("prospective_direct_config_path_escape")
    if value["tracked_inst_ids"] != list(INST_IDS) or value["bar"] != "1H" or value["bar_duration_ms"] != 3600000 or value["limit"] != 300 or value["required_confirm"] != "1":
        raise MarketDataError("prospective_direct_config_policy_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_prospective_direct_result(result: ProspectiveDirectCaptureResult | ProspectiveDirectAuditResult) -> str:
    report = result.report
    if "capture_sha256" in report:
        return "\n".join([f"capture_status: {report['capture_status']}", f"capture_sha256: {report['capture_sha256']}", f"append_start: {report['identity']['append_start']}", f"append_end: {report['identity']['append_end']}", "future_only_membership_evidence: true", "pnl_computation_authorized: false"])
    return "\n".join([f"audit_status: {report['audit_status']}", f"extension_sha256: {report['extension_sha256']}", f"append_row_count_total: {report['identity']['append_row_count_total']}", f"gate_coverage_row_count: {report['identity']['gate_coverage_row_count']}", "future_only_membership_evidence: true", "pnl_computation_authorized: false"])


def validate_ohlcv_csv_bytes(content: bytes) -> dict[str, Any]:
    fd, temp_name = tempfile.mkstemp(prefix="prospective-direct-quality-", suffix=".csv")
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_bytes(content)
        return validate_ohlcv_csv(temp, "1h").to_dict()
    finally:
        temp.unlink(missing_ok=True)


def _validate_gate_and_migration(config: dict[str, Any], gate: ValidatedMembershipBarGate, gate_path: Path, migration: Any, migration_path: Path) -> None:
    if _sha256(gate_path) != config["membership_gate_marker_sha256"] or gate.report["gate_sha256"] != config["membership_gate_sha256"] or _sha256(migration_path) != config["migration_report_sha256"] or migration.report["migration_sha256"] != config["migration_sha256"]:
        raise MarketDataError("prospective_direct_extension_input_identity_mismatch")
    if len(gate.eligibility) != 966 or gate.report["identity"]["epoch"]["last_execution_timestamp"] != "2026-08-09T10:00:00Z":
        raise MarketDataError("prospective_direct_extension_gate_identity_mismatch")


def _baseline_tails(migration: Any, tracked_ids: list[str]) -> dict[str, str]:
    datasets = {str(item["inst_id"]): item for item in migration.report["identity"]["datasets"]}
    result: dict[str, str] = {}
    for inst_id in tracked_ids:
        item = datasets.get(inst_id)
        if item is None:
            raise MarketDataError("prospective_direct_extension_dataset_missing")
        path = _repo_file(migration.repo_root, item["destination_repo_relative_path"])
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != 40191 or not rows or rows[-1].get("timestamp") != "2026-08-02T14:00:00+00:00":
            raise MarketDataError(f"prospective_direct_extension_baseline_tail_mismatch:{inst_id}")
        result[inst_id] = rows[-1]["timestamp"].replace("+00:00", "Z")
    return result


def _dataset_id(migration: Any, inst_id: str) -> str:
    for item in migration.report["identity"]["datasets"]:
        if item["inst_id"] == inst_id:
            return str(item["dataset_id"])
    raise MarketDataError("prospective_direct_extension_dataset_missing")


def _coverage_rows(gate: ValidatedMembershipBarGate, append_sets: dict[str, dict[str, list[str]]], tracked_ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in gate.eligibility:
        inst_id = item["inst_id"]
        if inst_id not in tracked_ids:
            raise MarketDataError("prospective_direct_extension_gate_asset_mismatch")
        timestamps = set(append_sets[inst_id]["timestamps"])
        opens = dict(zip(append_sets[inst_id]["timestamps"], append_sets[inst_id]["opens"], strict=True))
        signal = _timestamp_ms(_parse_iso(item["signal_timestamp"]))
        completion = _timestamp_ms(_parse_iso(item["completion_timestamp"]))
        execution = _timestamp_ms(_parse_iso(item["execution_timestamp"]))
        signal_key, completion_key, execution_key = str(signal), str(completion), str(execution)
        rows.append({"inst_id": inst_id, "signal_timestamp": item["signal_timestamp"], "completion_timestamp": item["completion_timestamp"], "execution_timestamp": item["execution_timestamp"], "membership_eligible": item["eligible_for_closed_epoch"], "signal_present": signal_key in timestamps, "completion_present": completion_key in timestamps, "execution_present": execution_key in timestamps, "execution_open_finite": execution_key in opens and math.isfinite(float(opens[execution_key])), "no_fill_or_substitution": True})
    if len(rows) != 966:
        raise MarketDataError("prospective_direct_extension_gate_coverage_count_mismatch")
    return rows


def _last_execution(gate: ValidatedMembershipBarGate) -> datetime:
    return _parse_iso(gate.report["identity"]["epoch"]["last_execution_timestamp"])


def _ceil_hour(value: datetime) -> datetime:
    base = value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return base if value == base else base + timedelta(hours=1)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise MarketDataError("prospective_direct_extension_timestamp_not_timezone_aware")
    return parsed.astimezone(timezone.utc)


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


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
        raise MarketDataError("prospective_direct_extension_invalid_csv") from exc


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("prospective_direct_extension_invalid_json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("prospective_direct_extension_invalid_json")
    return value


def _repo_file(repo: Path, relative: str) -> Path:
    candidate = (repo / relative).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError as exc:
        raise MarketDataError("prospective_direct_extension_path_escape") from exc
    if not candidate.is_file():
        raise MarketDataError("prospective_direct_extension_artifact_missing")
    return candidate


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    try:
        output.relative_to((repo / "reports").resolve())
    except ValueError as exc:
        raise ValueError("prospective direct extension output must stay inside reports") from exc
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
            raise MarketDataError(f"prospective_direct_extension_content_addressed_collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".prospective-direct-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
