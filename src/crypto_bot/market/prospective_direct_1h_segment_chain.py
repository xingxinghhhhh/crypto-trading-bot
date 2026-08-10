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
POLICY_ID = "prospective_direct_1h_append_only_segment_chain_v1"
AUDIT_STATUS = "verified_prospective_direct_1h_append_only_segment_chain"
DEFAULT_CONFIG_FILENAME = "config.prospective-direct-1h-segment-chain.example.yaml"
INST_IDS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT")
EXTENSION_SHA = "b0cbcb119a24ee786a81a6aed7adf4ddb8b11cf93891c19ca1fb21c4d3848146"
CAPTURE_SHA = "12e2022e6d0abb6fdb36ea9757fe9c2b32bdf5cdb93f9a0ad85981aa21b37629"
MIGRATION_SHA = "67fb338366f01f6ac03e0280f374fb8c6c5578029d7756286d5315ac1cc3b698"
MEMBERSHIP_SHA = "737e3da3a1e2db42b87709a23bc5f754bab2ae2a38af36210b28282cfb3d9f48"
DATASET_FIELDS = ("segment_ordinal", "extension_sha256", "capture_sha256", "membership_gate_sha256", "canonical_start", "canonical_end", "rows_per_asset", "previous_tail", "continuity_status", "immutable")
ASSET_FIELDS = ("segment_ordinal", "inst_id", "baseline_canonical_sha256", "segment_canonical_sha256", "raw_bundle_sha256", "canonical_start", "canonical_end", "row_count", "gap_count", "overlap_count")
CONSTRAINT_FIELDS = ("key", "value")


@dataclass(frozen=True)
class SegmentChainResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_prospective_direct_1h_segment_chain(
    accumulation_policy: str | Path,
    sample_maturity: str | Path,
    latest_readiness: str | Path,
    extension_reports: list[str | Path],
    config_path: str | Path,
    output_dir: str | Path,
) -> SegmentChainResult:
    if not extension_reports:
        raise ValueError("at least one extension report is required")
    policy_path = Path(accumulation_policy).resolve()
    maturity_path = Path(sample_maturity).resolve()
    readiness_path = Path(latest_readiness).resolve()
    extension_paths = [Path(path).resolve() for path in extension_reports]
    repo = _repo_root(policy_path)
    load_segment_chain_config(config_path, repo)
    policy = _marker(policy_path, "prospective-epoch-accumulation-policy", repo)
    maturity = _marker(maturity_path, "prospective-economic-sample-maturity", repo)
    readiness = _marker(readiness_path, "prospective-economic-readiness", repo)
    _validate_policy_inputs(policy, maturity, readiness, policy_path, maturity_path, readiness_path)
    baseline = _validate_migration(repo)
    segments: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    previous_tail = _parse_iso("2026-08-02T14:00:00Z")
    seen_windows: set[tuple[datetime, datetime]] = set()
    for ordinal, extension_path in enumerate(extension_paths, 1):
        extension = _marker(extension_path, "prospective-direct-1h-extension", repo)
        capture, capture_path = _validate_extension(extension, extension_path, repo, ordinal)
        dataset_rows = _read_csv(_sibling(extension_path, extension["artifacts"]["datasets"]["filename"]))
        if len(dataset_rows) != len(INST_IDS):
            raise MarketDataError("segment chain dataset row count mismatch")
        rows_by_asset = {str(row.get("inst_id")): row for row in dataset_rows}
        if tuple(rows_by_asset) != INST_IDS or set(rows_by_asset) != set(INST_IDS):
            raise MarketDataError("segment chain asset order mismatch")
        start = _parse_iso(extension["identity"].get("append_start", ""))
        end = _parse_iso(extension["identity"].get("append_end", ""))
        if start != previous_tail + timedelta(hours=1) or end < start:
            raise MarketDataError("segment chain canonical gap_or_overlap")
        if (start, end) in seen_windows:
            raise MarketDataError("segment chain duplicate segment")
        seen_windows.add((start, end))
        if extension["identity"].get("append_row_count_per_asset") != 164 or extension["identity"].get("append_row_count_total") != 984:
            raise MarketDataError("segment chain extension shape mismatch")
        capture_assets = {str(item["inst_id"]): item for item in capture["identity"].get("assets", [])}
        if tuple(capture_assets) != INST_IDS:
            raise MarketDataError("segment chain capture asset order mismatch")
        segment_assets: list[dict[str, Any]] = []
        for inst_id in INST_IDS:
            row = rows_by_asset[inst_id]
            if row.get("append_head") != _iso(start) or row.get("append_tail") != _iso(end) or row.get("append_row_count") != "164" or row.get("continuity_verified") != "true":
                raise MarketDataError(f"segment chain asset continuity mismatch:{inst_id}")
            capture_item = capture_assets[inst_id]
            segment_assets.append({"segment_ordinal": ordinal, "inst_id": inst_id, "baseline_canonical_sha256": baseline[inst_id]["canonical_sha256"], "segment_canonical_sha256": capture_item["canonical_sha256"], "raw_bundle_sha256": capture_item["raw_sha256"], "canonical_start": _iso(start), "canonical_end": _iso(end), "row_count": 164, "gap_count": 0, "overlap_count": 0})
        assets.extend(segment_assets)
        segments.append({"segment_ordinal": ordinal, "extension_sha256": extension["extension_sha256"], "capture_sha256": capture["capture_sha256"], "membership_gate_sha256": extension["identity"].get("membership_gate_sha256"), "canonical_start": _iso(start), "canonical_end": _iso(end), "rows_per_asset": 164, "previous_tail": _iso(previous_tail), "continuity_status": "exact_plus_one_hour", "immutable": True})
        previous_tail = end
    next_start = previous_tail + timedelta(hours=1)
    segments_bytes = _csv_bytes(segments, DATASET_FIELDS)
    assets_bytes = _csv_bytes(assets, ASSET_FIELDS)
    constraints_values = {"segment_count": len(segments), "current_chain_tail": _iso(previous_tail), "next_canonical_segment_start": _iso(next_start), "next_segment_end_resolved": False, "current_samples": 160, "remaining_samples": 340, "sample_maturity_met": False, "market_data_segments_are_not_samples": True, "historical_recapture_prohibited": True, "economic_computation_authorized": False, "turnover_rows": 0, "cost_amount_rows": 0, "capacity_pass_fail_rows": 0, "return_rows": 0, "pnl_rows": 0, "profitability_evidence": False, "readiness_changed": False}
    constraints_rows = _rows(constraints_values)
    constraints_bytes = _csv_bytes(constraints_rows, CONSTRAINT_FIELDS)
    identity = {"schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID, "accumulation_policy_sha256": policy["policy_sha256"], "sample_maturity_sha256": maturity["maturity_sha256"], "latest_readiness_sha256": readiness["readiness_sha256"], "baseline_migration_sha256": MIGRATION_SHA, "segments": segments, "assets": assets, "current_chain_tail": _iso(previous_tail), "next_canonical_segment_start": _iso(next_start), "next_segment_end_resolved": False, "current_sample_count": 160, "minimum_sample_count": 500, "remaining_sample_count": 340, "canonical_overlap_count": 0, "canonical_gap_count": 0, "claims": {"raw_capture_is_immutable": True, "offline_replay_deterministic": True, "network_recapture_byte_determinism_required": False, "baseline_history_replacement_prohibited": True, "prospective_segment_replacement_prohibited": True, "prospective_market_data_version_pinned": True, "future_only_membership_evidence": True, "historical_point_in_time_membership": False, "survivorship_bias_resolved": False, "profitability_evidence": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}, "artifacts": {"segments_sha256": _digest(segments_bytes), "assets_sha256": _digest(assets_bytes), "constraints_sha256": _digest(constraints_bytes)}}
    chain_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-direct-1h-segment-chain.{chain_sha}"
    paths = {"segments": output / f"{stem}.segments.csv", "assets": output / f"{stem}.assets.csv", "constraints": output / f"{stem}.constraints.csv", "report": output / f"{stem}.json"}
    for key, content in (("segments", segments_bytes), ("assets", assets_bytes), ("constraints", constraints_bytes)):
        _commit_bytes(paths[key], content)
    report = {"schema_version": SCHEMA_VERSION, "chain_sha256": chain_sha, "audit_status": AUDIT_STATUS, "segment_count": len(segments), "asset_segment_rows": len(assets), "segment_1_start": segments[0]["canonical_start"], "segment_1_end": segments[0]["canonical_end"], "segment_1_rows_per_asset": 164, "canonical_overlap_count": 0, "canonical_gap_count": 0, "current_chain_tail": _iso(previous_tail), "next_canonical_segment_start": _iso(next_start), "next_segment_end_resolved": False, "unique_closed_execution_intervals": 160, "minimum": 500, "remaining": 340, "sample_maturity_met": False, "turnover_rows": 0, "cost_amount_rows": 0, "capacity_pass_fail_rows": 0, "return_rows": 0, "pnl_rows": 0, "identity": identity, "artifacts": {"segments": {"filename": paths["segments"].name, "sha256": identity["artifacts"]["segments_sha256"], "row_count": len(segments)}, "assets": {"filename": paths["assets"].name, "sha256": identity["artifacts"]["assets_sha256"], "row_count": len(assets)}, "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints_rows)}, "report": {"filename": paths["report"].name}}, "economic_computation_authorized": False, "pnl_computation_authorized": False, "profitability_evidence": False, "readiness_changed": False}
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return SegmentChainResult(report, {key: str(value) for key, value in paths.items()})


def load_segment_chain_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if config_file.name != DEFAULT_CONFIG_FILENAME or not config_file.is_file():
        raise ValueError("segment chain config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen or (repo is not None and not config_file.is_relative_to(repo.resolve())):
        raise MarketDataError("segment chain config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_segment_chain_result(result: SegmentChainResult) -> str:
    report = result.report
    return "\n".join((f"audit_status: {report['audit_status']}", f"chain_sha256: {report['chain_sha256']}", f"segment_count: {report['segment_count']}", f"asset_segment_rows: {report['asset_segment_rows']}", f"current_chain_tail: {report['current_chain_tail']}", f"next_canonical_segment_start: {report['next_canonical_segment_start']}", "next_segment_end_resolved: false", "unique_closed_execution_intervals: 160", "minimum: 500", "remaining: 340", "sample_maturity_met: false", "economic_computation_authorized: false", "readiness_changed: false"))


def _validate_policy_inputs(policy: dict[str, Any], maturity: dict[str, Any], readiness: dict[str, Any], policy_path: Path, maturity_path: Path, readiness_path: Path) -> None:
    if policy.get("policy_sha256") != "8f4a90632bcf73df04c7e6f44924924db682cdc8373ed04c1f726cb6ce353092" or _digest(_canonical_json_bytes(policy.get("identity", {}))) != policy.get("policy_sha256"):
        raise MarketDataError("segment chain accumulation policy identity mismatch")
    if maturity.get("maturity_sha256") != "32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178" or readiness.get("readiness_sha256") != "0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b":
        raise MarketDataError("segment chain maturity/readiness identity mismatch")
    if policy_path.parent.name != "prospective-epoch-accumulation-policy" or policy_path.name != f"prospective-epoch-accumulation-policy.{policy['policy_sha256']}.json" or maturity_path.parent.name != "prospective-economic-sample-maturity" or maturity_path.name != f"prospective-economic-sample-maturity.{maturity['maturity_sha256']}.json" or readiness_path.parent.name != "prospective-economic-readiness" or readiness_path.name != f"prospective-economic-readiness.{readiness['readiness_sha256']}.json":
        raise MarketDataError("segment chain marker path mismatch")
    if maturity.get("unique_closed_interval_count") != 160 or maturity.get("sample_maturity_met") is not False or readiness.get("economic_value_computation_authorized") is not False:
        raise MarketDataError("segment chain sample or authorization mismatch")
    if maturity.get("identity", {}).get("readiness_reports", [{}])[-1].get("readiness_sha256") != readiness.get("readiness_sha256"):
        raise MarketDataError("segment chain latest readiness mismatch")


def _validate_migration(repo: Path) -> dict[str, dict[str, str]]:
    path = repo / "reports/okx-direct-six-asset-1h-migration/okx-direct-six-migration.67fb338366f01f6ac03e0280f374fb8c6c5578029d7756286d5315ac1cc3b698.json"
    report = _load_json(path)
    if report.get("migration_sha256") != MIGRATION_SHA or report.get("identity", {}).get("claims", {}).get("strategy_approval") is not False:
        raise MarketDataError("segment chain migration mismatch")
    result: dict[str, dict[str, str]] = {}
    for item in report.get("identity", {}).get("datasets", []):
        inst_id = str(item.get("inst_id"))
        if inst_id not in INST_IDS:
            continue
        csv_path = (repo / str(item["destination_repo_relative_path"])).resolve()
        if not csv_path.is_relative_to(repo.resolve()) or not csv_path.is_file():
            raise MarketDataError(f"segment chain baseline artifact mismatch:{inst_id}")
        rows = _read_csv(csv_path)
        if len(rows) != 40191 or rows[-1].get("timestamp") != "2026-08-02T14:00:00+00:00":
            raise MarketDataError(f"segment chain baseline tail mismatch:{inst_id}")
        result[inst_id] = {"canonical_sha256": str(item["destination_canonical_sha256"]), "tail": "2026-08-02T14:00:00Z"}
    if tuple(result) != INST_IDS:
        raise MarketDataError("segment chain baseline asset set mismatch")
    return result


def _validate_extension(extension: dict[str, Any], path: Path, repo: Path, ordinal: int) -> tuple[dict[str, Any], Path]:
    extension_sha = extension.get("extension_sha256")
    if not isinstance(extension_sha, str) or _digest(_canonical_json_bytes(extension.get("identity", {}))) != extension_sha or extension.get("audit_status") != "verified_prospective_direct_okx_1h_extension":
        raise MarketDataError("segment chain extension identity mismatch")
    if ordinal == 1 and extension_sha != EXTENSION_SHA:
        raise MarketDataError("segment chain first extension identity mismatch")
    membership_sha = extension.get("identity", {}).get("membership_gate_sha256")
    if not isinstance(membership_sha, str) or (ordinal == 1 and membership_sha != MEMBERSHIP_SHA) or extension.get("identity", {}).get("migration_sha256") != MIGRATION_SHA:
        raise MarketDataError("segment chain extension dependency mismatch")
    capture_sha = extension["identity"].get("capture_sha256")
    if not isinstance(capture_sha, str) or (ordinal == 1 and capture_sha != CAPTURE_SHA):
        raise MarketDataError("segment chain capture dependency mismatch")
    capture_path = repo / f"reports/prospective-direct-1h-capture/prospective-direct-1h-capture.{capture_sha}.json"
    capture = _load_json(capture_path)
    if capture.get("capture_sha256") != capture_sha or _digest(_canonical_json_bytes(capture.get("identity", {}))) != capture_sha:
        raise MarketDataError("segment chain capture identity mismatch")
    if capture.get("identity", {}).get("append_start") != "2026-08-02T15:00:00Z" or capture.get("identity", {}).get("append_end") != "2026-08-09T10:00:00Z":
        raise MarketDataError("segment chain capture window mismatch")
    return capture, capture_path


def _marker(path: Path, directory: str, repo: Path) -> dict[str, Any]:
    if path.parent != (repo / "reports" / directory).resolve() or not path.is_file():
        raise MarketDataError("segment chain marker path escape")
    return _load_json(path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("segment chain csv read failed") from exc


def _sibling(marker_path: Path, filename: str) -> Path:
    path = (marker_path.parent / filename).resolve()
    if not path.is_relative_to(marker_path.parent.resolve()) or not path.is_file():
        raise MarketDataError("segment chain sibling artifact missing")
    return path


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
    raise MarketDataError("segment chain repo root not found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("segment chain output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("segment chain invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise MarketDataError("segment chain timestamp missing timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("segment chain json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("segment chain json shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"segment chain content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".segment-chain-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    return _digest(path.read_bytes())


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
