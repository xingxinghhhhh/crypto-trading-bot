from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_direct_1h_segment_append_plan import (
    READY_STATUS,
    _validate_current_chain,
    validate_prospective_direct_1h_segment_append_plan,
)

SCHEMA_VERSION = 1
POLICY_ID = "prospective_direct_1h_segment_append_prepared_v1"
CONTRACT_STATUS = "verified_prospective_direct_1h_segment_append_prepared"
READY_RESULT = "prepared_non_authoritative"
BLOCKED_PLAN_RESULT = "blocked_append_plan_not_ready"
CHAIN_DRIFT_RESULT = "blocked_current_chain_drift"
DEFAULT_CONFIG_FILENAME = "config.prospective-direct-1h-segment-append-prepared.example.yaml"
SEGMENT_FIELDS = (
    "ordinal",
    "source_type",
    "source_identity",
    "start",
    "end",
    "asset_count",
    "segment_sha256_reference",
)
ASSET_FIELDS = (
    "segment_ordinal",
    "inst_id",
    "canonical_sha256",
    "bar_count",
    "start",
    "end",
)
CHAIN_IMAGE_FIELDS = ("key", "value")
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class PreparedSegmentResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_prepared_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("prepared config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("prepared config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("prepared config policy mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("prepared config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def prepare_prospective_direct_1h_segment_append(
    append_plan: str | Path,
    segment_chain: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> PreparedSegmentResult:
    plan_path = Path(append_plan).resolve()
    chain_path = Path(segment_chain).resolve()
    repo = _repo_root(plan_path)
    config = load_prepared_config(config_path, repo)
    plan = validate_prospective_direct_1h_segment_append_plan(plan_path)
    chain = _validate_current_chain(chain_path)
    plan_chain_id = str(plan.get("current_chain_identity"))
    current_chain_id = str(chain.get("chain_sha256"))
    if plan_chain_id != current_chain_id:
        state = _blocked_state(plan, CHAIN_DRIFT_RESULT, chain)
    elif plan.get("status") != READY_STATUS or plan.get("append_plan_materialized") is not True:
        state = _blocked_state(plan, BLOCKED_PLAN_RESULT, chain)
    else:
        state = _ready_state(plan, chain, plan_path)
    state["plan_path"] = str(plan_path)
    state["chain_path"] = str(chain_path)
    return _write_result(repo, output_dir, config, state)


def validate_prepared_segment(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _load_json(report_path)
    if report.get("contract_status") != CONTRACT_STATUS or report.get("schema_version") != SCHEMA_VERSION:
        raise MarketDataError("prepared contract mismatch")
    identity = report.get("identity")
    prepared_sha = report.get("prepared_sha256")
    if not _is_sha256(prepared_sha) or not isinstance(identity, dict) or _digest(_canonical(identity)) != prepared_sha:
        raise MarketDataError("prepared identity mismatch")
    append_plan_path = _resolve_parent(identity.get("append_plan_path"))
    segment_chain_path = _resolve_parent(identity.get("segment_chain_path"))
    replayed_plan = validate_prospective_direct_1h_segment_append_plan(append_plan_path)
    replayed_chain = _validate_current_chain(segment_chain_path)
    if replayed_plan.get("plan_sha256") != report.get("append_plan_identity") or replayed_chain.get("chain_sha256") != report.get("current_chain_identity"):
        raise MarketDataError("prepared parent identity drift")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MarketDataError("prepared artifacts missing")
    expected_segments = int(report.get("proposed_segment_count") or 0)
    expected_assets = 6 if report.get("prepared_chain_materialized") else 0
    _validate_artifact(report_path, artifacts, identity, "segments", SEGMENT_FIELDS, expected_segments)
    _validate_artifact(report_path, artifacts, identity, "assets", ASSET_FIELDS, expected_assets)
    _validate_artifact(report_path, artifacts, identity, "chain_image", CHAIN_IMAGE_FIELDS, int(artifacts["chain_image"].get("row_count", -1)))
    _validate_artifact(report_path, artifacts, identity, "dependencies", KEY_VALUE_FIELDS, int(artifacts["dependencies"].get("row_count", -1)))
    _validate_artifact(report_path, artifacts, identity, "constraints", KEY_VALUE_FIELDS, int(artifacts["constraints"].get("row_count", -1)))
    if report.get("authoritative_chain_changed") is not False or report.get("chain_mutation_performed") is not False or report.get("segment_appended") is not False or report.get("promotion_authorized") is not False:
        raise MarketDataError("prepared mutation claim")
    for key in ("new_samples_counted", "current_samples", "sample_threshold", "remaining_samples"):
        expected = {"new_samples_counted": 0, "current_samples": 160, "sample_threshold": 500, "remaining_samples": 340}[key]
        if report.get(key) != expected:
            raise MarketDataError(f"prepared sample constraint:{key}")
    for key in ("network_activity_performed", "economic_computation_authorized", "pnl_computation_authorized", "readiness_changed"):
        if report.get(key) is not False:
            raise MarketDataError(f"prepared forbidden claim:{key}")
    if report.get("expected_identity_only") is not True or report.get("authoritative_identity_materialized") is not False:
        raise MarketDataError("prepared identity authority claim")
    if report.get("authoritative_pre_chain_identity") != report.get("current_chain_identity") or report.get("authoritative_post_chain_identity") is not None:
        raise MarketDataError("prepared authoritative identity claim")
    if report.get("existing_segment_rewrites") != 0 or report.get("existing_segment_replacements") != 0 or report.get("new_segment_count") != (1 if report.get("prepared_chain_materialized") else 0):
        raise MarketDataError("prepared segment mutation accounting")
    return report


def format_prepared_result(result: PreparedSegmentResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"prepared_sha256: {report['prepared_sha256']}",
            f"status: {report['status']}",
            f"prepared_chain_materialized: {str(report['prepared_chain_materialized']).lower()}",
            f"proposed_segment_count: {report['proposed_segment_count']}",
            f"proposed_chain_tail: {report['proposed_chain_tail']}",
            "authoritative_chain_changed: false",
            "chain_mutation_performed: false",
            "segment_appended: false",
        )
    )


def _blocked_state(plan: Mapping[str, Any], status: str, chain: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "append_plan_identity": str(plan.get("plan_sha256")),
        "current_chain_identity": str(chain.get("chain_sha256")),
        "candidate_identity": str(plan.get("candidate_identity")),
        "current_segment_count": int(chain.get("segment_count", 0)),
        "proposed_segment_count": None,
        "proposed_chain_tail": None,
        "proposed_next_start": None,
        "expected_post_append_chain_identity": None,
        "authoritative_pre_chain_identity": str(chain.get("chain_sha256")),
        "authoritative_post_chain_identity": None,
        "expected_identity_only": True,
        "authoritative_identity_materialized": False,
        "existing_segment_rewrites": 0,
        "existing_segment_replacements": 0,
        "new_segment_count": 0,
        "prepared_chain_materialized": False,
        "status": status,
        "segments": [],
        "assets": [],
        "plan": plan,
    }


def _ready_state(plan: Mapping[str, Any], chain: Mapping[str, Any], plan_path: Path) -> dict[str, Any]:
    plan_assets = _read_plan_assets(plan_path, plan)
    if len(plan_assets) != 6:
        raise MarketDataError("prepared candidate asset count mismatch")
    existing = chain.get("identity", {}).get("segments", []) if isinstance(chain.get("identity"), dict) else []
    if len(existing) != int(chain.get("segment_count", 0)):
        raise MarketDataError("prepared existing segment count mismatch")
    candidate_start = str(plan["candidate_start"])
    candidate_end = str(plan["candidate_end"])
    candidate_ordinal = int(plan["expected_segment_ordinal"])
    segments = [{"ordinal": int(item["segment_ordinal"]), "source_type": "existing_immutable", "source_identity": _segment_identity(item), "start": item["canonical_start"], "end": item["canonical_end"], "asset_count": 6, "segment_sha256_reference": _segment_identity(item)} for item in existing]
    segments.append({"ordinal": candidate_ordinal, "source_type": "validated_candidate", "source_identity": str(plan["candidate_identity"]), "start": candidate_start, "end": candidate_end, "asset_count": 6, "segment_sha256_reference": str(plan["candidate_identity"])})
    if tuple(int(item["ordinal"]) for item in segments) != tuple(range(1, len(segments) + 1)):
        raise MarketDataError("prepared segment ordinal discontinuity")
    proposed_count = len(segments)
    next_start = (_parse_iso(candidate_end) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    return {"append_plan_identity": str(plan["plan_sha256"]), "current_chain_identity": str(chain["chain_sha256"]), "candidate_identity": str(plan["candidate_identity"]), "current_segment_count": int(chain["segment_count"]), "proposed_segment_count": proposed_count, "proposed_chain_tail": candidate_end, "proposed_next_start": next_start, "expected_post_append_chain_identity": recompute_proposed_post_identity(chain["chain_sha256"], plan["candidate_identity"], segments, proposed_count, candidate_end, next_start), "authoritative_pre_chain_identity": str(chain["chain_sha256"]), "authoritative_post_chain_identity": None, "expected_identity_only": True, "authoritative_identity_materialized": False, "existing_segment_rewrites": 0, "existing_segment_replacements": 0, "new_segment_count": 1, "prepared_chain_materialized": True, "status": READY_RESULT, "segments": segments, "assets": [{"segment_ordinal": candidate_ordinal, "inst_id": row["inst_id"], "canonical_sha256": row["candidate_canonical_sha256"], "bar_count": row["candidate_bar_count"], "start": candidate_start, "end": candidate_end} for row in plan_assets], "plan": plan}


def recompute_proposed_post_identity(
    source_chain_identity: str,
    candidate_identity: str,
    segments: Sequence[Mapping[str, Any]],
    proposed_segment_count: int,
    proposed_chain_tail: str,
    proposed_next_canonical_segment_start: str,
) -> str:
    """Rebuild the prepared post-chain image using the single canonical algorithm."""
    proposed_identity = {
        "schema_version": SCHEMA_VERSION,
        "source_chain_identity": source_chain_identity,
        "candidate_identity": candidate_identity,
        "segments": [dict(segment) for segment in segments],
        "proposed_segment_count": proposed_segment_count,
        "proposed_chain_tail": proposed_chain_tail,
        "proposed_next_canonical_segment_start": proposed_next_canonical_segment_start,
    }
    return _digest(_canonical(proposed_identity))


def _write_result(repo: Path, output_dir: str | Path, config: Mapping[str, Any], state: Mapping[str, Any]) -> PreparedSegmentResult:
    output = _output_dir(repo, output_dir)
    segments = state["segments"]
    assets = state["assets"]
    chain_image = _rows({"authoritative": False, "may_be_used_as_current_chain": False, "promotion_authorized": False, "expected_post_append_chain_identity": state["expected_post_append_chain_identity"], "proposed_segment_count": state["proposed_segment_count"], "proposed_chain_tail": state["proposed_chain_tail"], "proposed_next_canonical_segment_start": state["proposed_next_start"]})
    dependencies = _rows({"append_plan_identity": state["append_plan_identity"], "current_chain_identity": state["current_chain_identity"], "candidate_identity": state["candidate_identity"], "append_plan_path": state.get("plan_path", ""), "segment_chain_path": state.get("chain_path", "")})
    constraints = _rows({"prepared_chain_materialized": state["prepared_chain_materialized"], "authoritative_chain_changed": False, "chain_mutation_performed": False, "segment_appended": False, "promotion_authorized": False, "existing_segments_preserved": True, "current_samples": 160, "sample_threshold": 500, "remaining_samples": 340, "new_samples_counted": 0, "network_activity_performed": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False})
    identity = {"schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID, "policy": dict(config), "append_plan_identity": state["append_plan_identity"], "current_chain_identity": state["current_chain_identity"], "candidate_identity": state["candidate_identity"], "append_plan_path": state.get("plan_path", ""), "segment_chain_path": state.get("chain_path", ""), "authoritative": False, "may_be_used_as_current_chain": False, "promotion_authorized": False, "prepared_chain_materialized": state["prepared_chain_materialized"], "proposed_segment_count": state["proposed_segment_count"], "proposed_chain_tail": state["proposed_chain_tail"], "proposed_next_canonical_segment_start": state["proposed_next_start"], "expected_post_append_chain_identity": state["expected_post_append_chain_identity"], "authoritative_pre_chain_identity": state["authoritative_pre_chain_identity"], "authoritative_post_chain_identity": state["authoritative_post_chain_identity"], "expected_identity_only": state["expected_identity_only"], "authoritative_identity_materialized": state["authoritative_identity_materialized"], "existing_segment_rewrites": state["existing_segment_rewrites"], "existing_segment_replacements": state["existing_segment_replacements"], "new_segment_count": state["new_segment_count"], "artifacts": {"segments_sha256": _digest(_csv_bytes(segments, SEGMENT_FIELDS)), "assets_sha256": _digest(_csv_bytes(assets, ASSET_FIELDS)), "chain_image_sha256": _digest(_csv_bytes(chain_image, CHAIN_IMAGE_FIELDS)), "dependencies_sha256": _digest(_csv_bytes(dependencies, KEY_VALUE_FIELDS)), "constraints_sha256": _digest(_csv_bytes(constraints, KEY_VALUE_FIELDS))}}
    prepared_sha = _digest(_canonical(identity))
    stem = f"prospective-direct-1h-segment-append-prepared.{prepared_sha}"
    paths = {name: output / f"{stem}.{name}.csv" for name in ("segments", "assets", "chain-image", "dependencies", "constraints")}
    paths["report"] = output / f"{stem}.json"
    artifact_rows = (("segments", "segments", segments), ("assets", "assets", assets), ("chain_image", "chain-image", chain_image), ("dependencies", "dependencies", dependencies), ("constraints", "constraints", constraints))
    report = {"schema_version": SCHEMA_VERSION, "contract_status": CONTRACT_STATUS, "prepared_sha256": prepared_sha, "status": state["status"], "prepared_chain_materialized": state["prepared_chain_materialized"], "append_plan_identity": state["append_plan_identity"], "current_chain_identity": state["current_chain_identity"], "candidate_identity": state["candidate_identity"], "current_segment_count": state["current_segment_count"], "proposed_segment_count": state["proposed_segment_count"], "proposed_chain_tail": state["proposed_chain_tail"], "proposed_next_canonical_segment_start": state["proposed_next_start"], "expected_post_append_chain_identity": state["expected_post_append_chain_identity"], "authoritative_pre_chain_identity": state["authoritative_pre_chain_identity"], "authoritative_post_chain_identity": state["authoritative_post_chain_identity"], "expected_identity_only": state["expected_identity_only"], "authoritative_identity_materialized": state["authoritative_identity_materialized"], "existing_segment_rewrites": state["existing_segment_rewrites"], "existing_segment_replacements": state["existing_segment_replacements"], "new_segment_count": state["new_segment_count"], "authoritative": False, "may_be_used_as_current_chain": False, "promotion_authorized": False, "authoritative_chain_changed": False, "chain_mutation_performed": False, "segment_appended": False, "current_samples": 160, "sample_threshold": 500, "remaining_samples": 340, "new_samples_counted": 0, "network_activity_performed": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False, "identity": identity, "artifacts": {logical: {"filename": paths[file_key].name, "sha256": identity["artifacts"][f"{logical}_sha256"], "row_count": len(rows)} for logical, file_key, rows in artifact_rows}}
    for name, content in (("segments", _csv_bytes(segments, SEGMENT_FIELDS)), ("assets", _csv_bytes(assets, ASSET_FIELDS)), ("chain-image", _csv_bytes(chain_image, CHAIN_IMAGE_FIELDS)), ("dependencies", _csv_bytes(dependencies, KEY_VALUE_FIELDS)), ("constraints", _csv_bytes(constraints, KEY_VALUE_FIELDS))):
        _commit(paths[name], content)
    _commit(paths["report"], _pretty(report))
    return PreparedSegmentResult(report, {key: str(value) for key, value in paths.items()})


def _read_plan_assets(plan_path: Path, plan: Mapping[str, Any]) -> list[dict[str, str]]:
    artifacts = plan.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get("assets"), dict):
        raise MarketDataError("prepared plan assets missing")
    path = (plan_path.parent / str(artifacts["assets"].get("filename", ""))).resolve()
    if path.parent != plan_path.parent.resolve() or not path.is_file():
        raise MarketDataError("prepared plan assets path mismatch")
    rows = _read_csv(path)
    if len(rows) != int(artifacts["assets"].get("row_count", -1)):
        raise MarketDataError("prepared plan assets row count mismatch")
    return rows


def _segment_identity(item: Mapping[str, Any]) -> str:
    return _digest(_canonical({"segment_ordinal": item.get("segment_ordinal"), "extension_sha256": item.get("extension_sha256"), "capture_sha256": item.get("capture_sha256"), "canonical_start": item.get("canonical_start"), "canonical_end": item.get("canonical_end")}))


def _validate_artifact(report_path: Path, artifacts: Mapping[str, Any], identity: Mapping[str, Any], name: str, fields: tuple[str, ...], expected_rows: int) -> Path:
    info = artifacts.get(name)
    identity_artifacts = identity.get("artifacts")
    expected_sha = identity_artifacts.get(f"{name}_sha256") if isinstance(identity_artifacts, dict) else None
    if not isinstance(info, dict) or info.get("sha256") != expected_sha:
        raise MarketDataError(f"prepared artifact metadata mismatch:{name}")
    path = (report_path.parent / str(info.get("filename", ""))).resolve()
    if path.parent != report_path.parent.resolve() or not path.is_file() or _digest(path.read_bytes()) != expected_sha:
        raise MarketDataError(f"prepared artifact bytes mismatch:{name}")
    rows = _read_csv(path)
    if rows and tuple(rows[0]) != fields:
        raise MarketDataError(f"prepared artifact schema mismatch:{name}")
    if len(rows) != expected_rows:
        raise MarketDataError(f"prepared artifact row count mismatch:{name}")
    return path


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("prepared CSV read failed") from exc


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
        raise MarketDataError("prepared JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("prepared JSON shape mismatch")
    return value


def _output_dir(repo: Path, value: str | Path) -> Path:
    output = Path(value).resolve() if Path(value).is_absolute() else (repo / value).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("prepared output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src/crypto_bot").is_dir():
            return parent
    raise MarketDataError("prepared repo root not found")


def _resolve_parent(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise MarketDataError("prepared parent path missing")
    path = Path(value).resolve()
    if not path.is_file():
        raise MarketDataError("prepared parent missing")
    return path


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("prepared timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise MarketDataError("prepared timestamp timezone missing")
    return parsed


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
            raise MarketDataError(f"prepared artifact collision:{path.name}")
        return
    path.write_bytes(content)
