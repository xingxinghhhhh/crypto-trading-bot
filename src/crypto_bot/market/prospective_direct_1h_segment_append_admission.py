from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_direct_1h_segment_evidence import (
    INST_IDS,
    validate_prospective_direct_1h_segment_evidence,
)

SCHEMA_VERSION = 1
POLICY_ID = "prospective_direct_1h_segment_append_admission_v1"
CONTRACT_STATUS = "verified_prospective_direct_1h_segment_append_admission"
ADMITTED_STATUS = "admitted"
BLOCKED_STATUS = "blocked_segment_candidate_not_materialized"
REJECTED_STATUS = "rejected_append_admission"
DEFAULT_CONFIG_FILENAME = "config.prospective-direct-1h-segment-append-admission.example.yaml"
ADMISSION_FIELDS = (
    "segment_evidence_identity",
    "current_segment_chain_identity",
    "candidate_start",
    "candidate_end",
    "current_chain_tail",
    "current_chain_next_start",
    "current_segment_count",
    "expected_segment_ordinal",
    "exact_continuity",
    "stale_candidate",
    "duplicate_candidate",
    "append_admission_eligible",
    "append_admission_status",
    "segment_appended",
    "chain_mutation_performed",
)
ASSET_FIELDS = (
    "ordinal",
    "inst_id",
    "candidate_canonical_sha256",
    "candidate_bar_count",
    "candidate_start",
    "candidate_end",
    "predecessor_chain_identity",
    "request_match",
    "quality_valid",
)
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class SegmentAppendAdmissionResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_segment_append_admission_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("segment append admission config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("segment append admission config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("segment append admission config policy mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("segment append admission config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def freeze_prospective_direct_1h_segment_append_admission(
    segment_evidence: str | Path,
    segment_chain: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> SegmentAppendAdmissionResult:
    evidence_path = Path(segment_evidence).resolve()
    chain_path = Path(segment_chain).resolve()
    repo = _repo_root(evidence_path)
    config = load_segment_append_admission_config(config_path, repo)
    evidence = _validate_evidence_parent(evidence_path)
    chain = _validate_chain_parent(chain_path, repo)
    state = _build_state(evidence, chain)
    return _write_result(repo, output_dir, config, state)


def validate_prospective_direct_1h_segment_append_admission(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _load_json(report_path)
    if report.get("contract_status") != CONTRACT_STATUS or report.get("schema_version") != SCHEMA_VERSION:
        raise MarketDataError("segment append admission contract mismatch")
    candidate_sha = report.get("admission_sha256")
    identity = report.get("identity")
    if not _is_sha256(candidate_sha) or not isinstance(identity, dict) or _digest(_canonical(identity)) != candidate_sha:
        raise MarketDataError("segment append admission identity mismatch")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MarketDataError("segment append admission artifacts missing")
    _validate_artifact(report_path, artifacts, identity, "admission", ADMISSION_FIELDS, 1)
    expected_assets = 6 if report.get("append_admission_eligible") else 0
    _validate_artifact(report_path, artifacts, identity, "assets", ASSET_FIELDS, expected_assets)
    _validate_artifact(report_path, artifacts, identity, "dependencies", KEY_VALUE_FIELDS, int(artifacts["dependencies"].get("row_count", -1)))
    _validate_artifact(report_path, artifacts, identity, "constraints", KEY_VALUE_FIELDS, int(artifacts["constraints"].get("row_count", -1)))
    evidence_path = _resolve_parent(report_path, identity.get("segment_evidence_path"))
    chain_path = _resolve_parent(report_path, identity.get("segment_chain_path"))
    evidence = _validate_evidence_parent(evidence_path)
    chain = _validate_chain_parent(chain_path, _repo_root(report_path))
    state = _build_state(evidence, chain)
    for key in ("segment_evidence_identity", "current_segment_chain_identity", "candidate_start", "candidate_end", "current_chain_tail", "current_chain_next_start", "current_segment_count", "expected_segment_ordinal", "exact_continuity", "stale_candidate", "duplicate_candidate", "append_admission_eligible", "append_admission_status"):
        if report.get(key) != state[key]:
            raise MarketDataError(f"segment append admission report drift:{key}")
    if report.get("segment_appended") is not False or report.get("chain_mutation_performed") is not False:
        raise MarketDataError("segment append admission mutation claim")
    if report.get("current_samples") != 160 or report.get("sample_threshold") != 500 or report.get("remaining_samples") != 340 or report.get("new_samples_counted") != 0:
        raise MarketDataError("segment append admission sample constraint")
    for key in ("network_activity_performed", "network_capture_authorized", "economic_computation_authorized", "pnl_computation_authorized", "readiness_changed"):
        if report.get(key) is not False:
            raise MarketDataError(f"segment append admission forbidden claim:{key}")
    return report


def format_segment_append_admission_result(result: SegmentAppendAdmissionResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"admission_sha256: {report['admission_sha256']}",
            f"append_admission_eligible: {str(report['append_admission_eligible']).lower()}",
            f"append_admission_status: {report['append_admission_status']}",
            f"expected_segment_ordinal: {report['expected_segment_ordinal']}",
            f"asset_rows: {report['asset_rows']}",
            "segment_appended: false",
            "chain_mutation_performed: false",
            "current_samples: 160",
            "remaining_samples: 340",
            "new_samples_counted: 0",
            "network_activity_performed: false",
        )
    )


def _validate_evidence_parent(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.suffix != ".json":
        raise MarketDataError("segment append admission evidence marker missing")
    report = validate_prospective_direct_1h_segment_evidence(path)
    if report.get("segment_appended") is not False or report.get("new_samples_counted") != 0:
        raise MarketDataError("segment append admission evidence mutation")
    if report.get("network_activity_performed") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("segment append admission evidence forbidden claim")
    info = report.get("artifacts", {}).get("assets", {})
    if not isinstance(info, dict):
        raise MarketDataError("segment append admission evidence assets missing")
    asset_path = (path.parent / str(info.get("filename", ""))).resolve()
    if asset_path.parent != path.parent.resolve() or not asset_path.is_file() or _digest(asset_path.read_bytes()) != info.get("sha256"):
        raise MarketDataError("segment append admission evidence assets mismatch")
    report["_asset_rows"] = _read_csv(asset_path)
    report["_path"] = str(path)
    return report


def _validate_chain_parent(path: Path, repo: Path) -> dict[str, Any]:
    if not path.is_file() or path.parent.name != "prospective-direct-1h-segment-chain":
        raise MarketDataError("segment append admission chain marker path mismatch")
    report = _load_json(path)
    identity = report.get("identity")
    if report.get("audit_status") != "verified_prospective_direct_1h_append_only_segment_chain" or not isinstance(identity, dict):
        raise MarketDataError("segment append admission chain contract mismatch")
    chain_sha = report.get("chain_sha256")
    if not _is_sha256(chain_sha) or _digest(_canonical(identity)) != chain_sha or path.name != f"prospective-direct-1h-segment-chain.{chain_sha}.json":
        raise MarketDataError("segment append admission chain identity mismatch")
    for key in ("current_chain_tail", "next_canonical_segment_start"):
        if report.get(key) != identity.get(key):
            raise MarketDataError(f"segment append admission chain report drift:{key}")
    if report.get("segment_count") != len(identity.get("segments", [])):
        raise MarketDataError("segment append admission chain report count drift")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MarketDataError("segment append admission chain artifacts missing")
    segments_path = _validate_artifact(path, artifacts, identity, "segments", ("segment_ordinal", "extension_sha256", "capture_sha256", "membership_gate_sha256", "canonical_start", "canonical_end", "rows_per_asset", "previous_tail", "continuity_status", "immutable"), int(report.get("segment_count", -1)))
    assets_path = _validate_artifact(path, artifacts, identity, "assets", ("segment_ordinal", "inst_id", "baseline_canonical_sha256", "segment_canonical_sha256", "raw_bundle_sha256", "canonical_start", "canonical_end", "row_count", "gap_count", "overlap_count"), int(report.get("asset_segment_rows", -1)))
    _validate_artifact(path, artifacts, identity, "constraints", ("key", "value"), int(artifacts["constraints"].get("row_count", -1)))
    segments = _read_csv(segments_path)
    assets = _read_csv(assets_path)
    if tuple(str(row.get("inst_id")) for row in assets[:6]) != INST_IDS or len(assets) != int(report.get("segment_count", 0)) * 6:
        raise MarketDataError("segment append admission chain asset order mismatch")
    if int(report.get("segment_count", 0)) != len(segments) or not segments:
        raise MarketDataError("segment append admission chain segment count mismatch")
    for index, row in enumerate(segments, 1):
        if row.get("segment_ordinal") != str(index) or row.get("continuity_status") != "exact_plus_one_hour" or row.get("immutable") not in {"True", "true"}:
            raise MarketDataError("segment append admission chain segment shape mismatch")
    for key in ("economic_computation_authorized", "pnl_computation_authorized", "profitability_evidence", "readiness_changed"):
        if report.get(key) is not False:
            raise MarketDataError(f"segment append admission chain forbidden claim:{key}")
    report["_path"] = str(path)
    return report


def _build_state(evidence: Mapping[str, Any], chain: Mapping[str, Any]) -> dict[str, Any]:
    evidence_identity = str(evidence.get("candidate_sha256"))
    chain_identity = str(chain.get("chain_sha256"))
    materialized = bool(evidence.get("segment_candidate_materialized") and evidence.get("validated_segment_candidate"))
    current_count = int(chain.get("segment_count", 0))
    tail = str(chain.get("current_chain_tail"))
    next_start = str(chain.get("next_canonical_segment_start"))
    candidate_start = str(evidence.get("segment_start")) if materialized else ""
    candidate_end = str(evidence.get("segment_end")) if materialized else ""
    expected_ordinal = current_count + 1
    exact = materialized and candidate_start == next_start
    stale = False
    duplicate = False
    if materialized:
        candidate_dt = _parse_iso(candidate_start)
        tail_dt = _parse_iso(tail)
        stale = candidate_dt <= tail_dt or candidate_start != next_start
        prior_assets = chain.get("identity", {}).get("assets", []) if isinstance(chain.get("identity"), dict) else []
        candidate_assets = evidence.get("_asset_rows", [])
        prior_hashes = {str(item.get("segment_canonical_sha256")) for item in prior_assets if isinstance(item, dict)}
        candidate_hashes = {str(item.get("canonical_sha256")) for item in candidate_assets if isinstance(item, dict)}
        duplicate = bool(candidate_hashes) and candidate_hashes.issubset(prior_hashes)
    eligible = materialized and exact and not stale and not duplicate
    status = ADMITTED_STATUS if eligible else (BLOCKED_STATUS if not materialized else REJECTED_STATUS)
    return {
        "segment_evidence_identity": evidence_identity,
        "current_segment_chain_identity": chain_identity,
        "candidate_start": candidate_start,
        "candidate_end": candidate_end,
        "current_chain_tail": tail,
        "current_chain_next_start": next_start,
        "current_segment_count": current_count,
        "expected_segment_ordinal": expected_ordinal,
        "exact_continuity": exact,
        "stale_candidate": stale,
        "duplicate_candidate": duplicate,
        "append_admission_eligible": eligible,
        "append_admission_status": status,
        "asset_rows": 6 if eligible else 0,
        "evidence": evidence,
        "chain": chain,
    }


def _write_result(repo: Path, output_dir: str | Path, config: Mapping[str, Any], state: Mapping[str, Any]) -> SegmentAppendAdmissionResult:
    output = _output_dir(repo, output_dir)
    materialized = bool(state["append_admission_eligible"])
    evidence = state["evidence"]
    chain = state["chain"]
    assets = _candidate_asset_rows(evidence, state) if materialized else []
    admission_row = {key: state.get(key) for key in ADMISSION_FIELDS}
    admission = _csv_bytes([admission_row], ADMISSION_FIELDS)
    assets_bytes = _csv_bytes(assets, ASSET_FIELDS)
    dependencies_values = {
        "segment_evidence_identity": state["segment_evidence_identity"],
        "segment_evidence_path": _relative(repo, Path(evidence.get("_path", ""))) if evidence.get("_path") else "",
        "current_segment_chain_identity": state["current_segment_chain_identity"],
        "current_segment_chain_path": _relative(repo, Path(chain.get("_path", ""))) if chain.get("_path") else "",
        "candidate_capture_sha256": evidence.get("capture_sha256") or "",
        "candidate_asset_rows": len(assets),
    }
    constraints_values = {
        "append_admission_eligible": materialized,
        "segment_appended": False,
        "chain_mutation_performed": False,
        "current_samples": 160,
        "sample_threshold": 500,
        "remaining_samples": 340,
        "new_samples_counted": 0,
        "network_activity_performed": False,
        "network_capture_authorized": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    dependencies = _rows(dependencies_values)
    constraints = _rows(constraints_values)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "policy": dict(config),
        "segment_evidence_identity": state["segment_evidence_identity"],
        "segment_chain_identity": state["current_segment_chain_identity"],
        "segment_evidence_path": evidence.get("_path", ""),
        "segment_chain_path": chain.get("_path", ""),
        **{key: state[key] for key in ("candidate_start", "candidate_end", "current_chain_tail", "current_chain_next_start", "current_segment_count", "expected_segment_ordinal", "exact_continuity", "stale_candidate", "duplicate_candidate", "append_admission_eligible", "append_admission_status")},
        "asset_rows": len(assets),
        "artifacts": {
            "admission_sha256": _digest(admission),
            "assets_sha256": _digest(assets_bytes),
            "dependencies_sha256": _digest(_csv_bytes(dependencies, KEY_VALUE_FIELDS)),
            "constraints_sha256": _digest(_csv_bytes(constraints, KEY_VALUE_FIELDS)),
        },
    }
    admission_sha = _digest(_canonical(identity))
    stem = f"prospective-direct-1h-segment-append-admission.{admission_sha}"
    paths = {name: output / f"{stem}.{name}.csv" for name in ("admission", "assets", "dependencies", "constraints")}
    paths["report"] = output / f"{stem}.json"
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract_status": CONTRACT_STATUS,
        "admission_sha256": admission_sha,
        **{key: state[key] for key in ("segment_evidence_identity", "current_segment_chain_identity", "candidate_start", "candidate_end", "current_chain_tail", "current_chain_next_start", "current_segment_count", "expected_segment_ordinal", "exact_continuity", "stale_candidate", "duplicate_candidate", "append_admission_eligible", "append_admission_status")},
        "asset_rows": len(assets),
        "segment_appended": False,
        "chain_mutation_performed": False,
        "current_samples": 160,
        "sample_threshold": 500,
        "remaining_samples": 340,
        "new_samples_counted": 0,
        "network_activity_performed": False,
        "network_capture_authorized": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "identity": identity,
        "artifacts": {name: {"filename": paths[name].name, "sha256": identity["artifacts"][f"{name}_sha256"], "row_count": len(rows)} for name, rows in (("admission", [admission_row]), ("assets", assets), ("dependencies", dependencies), ("constraints", constraints))},
    }
    for name, content in (("admission", admission), ("assets", assets_bytes), ("dependencies", _csv_bytes(dependencies, KEY_VALUE_FIELDS)), ("constraints", _csv_bytes(constraints, KEY_VALUE_FIELDS))):
        _commit(paths[name], content)
    _commit(paths["report"], _pretty(report))
    report["identity"]["report_path"] = str(paths["report"])
    return SegmentAppendAdmissionResult(report, {key: str(value) for key, value in paths.items()})


def _candidate_asset_rows(evidence: Mapping[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    assets = evidence.get("_asset_rows", [])
    if len(assets) != 6:
        raise MarketDataError("segment append admission candidate assets missing")
    rows: list[dict[str, Any]] = []
    for ordinal, item in enumerate(assets, 1):
        if not isinstance(item, dict) or item.get("inst_id") != INST_IDS[ordinal - 1] or item.get("quality_valid") not in (True, "true") or item.get("request_match") not in (True, "true"):
            raise MarketDataError("segment append admission candidate asset mismatch")
        rows.append({"ordinal": ordinal, "inst_id": item["inst_id"], "candidate_canonical_sha256": item.get("canonical_sha256"), "candidate_bar_count": item.get("bar_count"), "candidate_start": state["candidate_start"], "candidate_end": state["candidate_end"], "predecessor_chain_identity": state["current_segment_chain_identity"], "request_match": True, "quality_valid": True})
    return rows


def _validate_artifact(report_path: Path, artifacts: Mapping[str, Any], identity: Mapping[str, Any], name: str, fields: tuple[str, ...], expected_rows: int) -> Path:
    info = artifacts.get(name)
    expected_identity_sha = identity.get("artifacts", {}).get(f"{name}_sha256") if isinstance(identity.get("artifacts"), dict) else None
    if not isinstance(info, dict) or (expected_identity_sha is not None and info.get("sha256") != expected_identity_sha):
        raise MarketDataError(f"segment append admission artifact metadata mismatch:{name}")
    path = (report_path.parent / str(info.get("filename", ""))).resolve()
    if path.parent != report_path.parent.resolve() or not path.is_file() or _digest(path.read_bytes()) != info.get("sha256"):
        raise MarketDataError(f"segment append admission artifact bytes mismatch:{name}")
    rows = _read_csv(path)
    if rows and tuple(rows[0]) != fields:
        raise MarketDataError(f"segment append admission artifact schema mismatch:{name}")
    if len(rows) != expected_rows:
        raise MarketDataError(f"segment append admission artifact row count mismatch:{name}")
    return path


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("segment append admission CSV read failed") from exc


def _csv_bytes(rows: Sequence[Mapping[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row.get(field)) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _rows(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": values[key]} for key in sorted(values)]


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("segment append admission JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("segment append admission JSON shape mismatch")
    return value


def _resolve_parent(report: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise MarketDataError("segment append admission parent path missing")
    path = Path(value).resolve()
    if not path.is_file():
        raise MarketDataError("segment append admission parent missing")
    return path


def _output_dir(repo: Path, value: str | Path) -> Path:
    output = Path(value).resolve() if Path(value).is_absolute() else (repo / value).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("segment append admission output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src/crypto_bot").is_dir():
            return parent
    raise MarketDataError("segment append admission repo root not found")


def _relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("segment append admission timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise MarketDataError("segment append admission timestamp timezone missing")
    return parsed.astimezone(timezone.utc)


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _commit(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"segment append admission artifact collision:{path.name}")
        return
    path.write_bytes(content)
