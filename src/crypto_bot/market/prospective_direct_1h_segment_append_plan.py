from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_direct_1h_segment_append_admission import (
    ADMITTED_STATUS,
    validate_prospective_direct_1h_segment_append_admission,
)

SCHEMA_VERSION = 1
POLICY_ID = "prospective_direct_1h_segment_append_plan_v1"
CONTRACT_STATUS = "verified_prospective_direct_1h_segment_append_plan"
READY_STATUS = "append_plan_ready"
BLOCKED_ADMISSION_STATUS = "blocked_append_admission_not_eligible"
CHAIN_DRIFT_STATUS = "blocked_current_chain_drift"
DEFAULT_CONFIG_FILENAME = "config.prospective-direct-1h-segment-append-plan.example.yaml"
PLAN_FIELDS = (
    "append_admission_identity",
    "current_chain_identity",
    "candidate_identity",
    "candidate_start",
    "candidate_end",
    "current_segment_count",
    "expected_segment_ordinal",
    "planned_segment_count",
    "planned_chain_tail",
    "planned_next_canonical_segment_start",
    "existing_segments_preserved",
    "chain_mutation_performed",
    "segment_appended",
    "append_plan_materialized",
    "status",
)
ASSET_FIELDS = (
    "ordinal",
    "inst_id",
    "candidate_canonical_sha256",
    "candidate_bar_count",
    "candidate_start",
    "candidate_end",
    "predecessor_chain_identity",
)
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class SegmentAppendPlanResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_segment_append_plan_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("segment append plan config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("segment append plan config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("segment append plan config policy mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("segment append plan config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def freeze_prospective_direct_1h_segment_append_plan(
    append_admission: str | Path,
    segment_chain: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> SegmentAppendPlanResult:
    admission_path = Path(append_admission).resolve()
    chain_path = Path(segment_chain).resolve()
    repo = _repo_root(admission_path)
    config = load_segment_append_plan_config(config_path, repo)
    admission = validate_prospective_direct_1h_segment_append_admission(admission_path)
    admission["_path"] = str(admission_path)
    current_chain = _validate_current_chain(chain_path)
    admission_chain_id = str(admission.get("current_segment_chain_identity"))
    current_chain_id = str(current_chain.get("chain_sha256"))
    if admission_chain_id != current_chain_id:
        state = _blocked_state(admission, CHAIN_DRIFT_STATUS, current_chain)
    elif admission.get("append_admission_status") != ADMITTED_STATUS or admission.get("append_admission_eligible") is not True:
        state = _blocked_state(admission, BLOCKED_ADMISSION_STATUS)
    else:
        state = _ready_state(admission, current_chain)
    state["segment_chain_path"] = str(chain_path)
    return _write_result(repo, output_dir, config, state)


def validate_prospective_direct_1h_segment_append_plan(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _load_json(report_path)
    if report.get("contract_status") != CONTRACT_STATUS or report.get("schema_version") != SCHEMA_VERSION:
        raise MarketDataError("segment append plan contract mismatch")
    identity = report.get("identity")
    plan_sha = report.get("plan_sha256")
    if not _is_sha256(plan_sha) or not isinstance(identity, dict) or _digest(_canonical(identity)) != plan_sha:
        raise MarketDataError("segment append plan identity mismatch")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MarketDataError("segment append plan artifacts missing")
    expected_assets = 6 if report.get("append_plan_materialized") else 0
    _validate_artifact(report_path, artifacts, identity, "plan", PLAN_FIELDS, 1)
    _validate_artifact(report_path, artifacts, identity, "assets", ASSET_FIELDS, expected_assets)
    _validate_artifact(report_path, artifacts, identity, "dependencies", KEY_VALUE_FIELDS, int(artifacts["dependencies"].get("row_count", -1)))
    _validate_artifact(report_path, artifacts, identity, "constraints", KEY_VALUE_FIELDS, int(artifacts["constraints"].get("row_count", -1)))
    admission_path = _resolve_parent(report_path, identity.get("append_admission_path"))
    chain_path = _resolve_parent(report_path, identity.get("segment_chain_path"))
    admission = validate_prospective_direct_1h_segment_append_admission(admission_path)
    chain = _validate_current_chain(chain_path)
    if report.get("append_admission_identity") != admission.get("admission_sha256"):
        raise MarketDataError("segment append plan admission drift")
    if report.get("current_chain_identity") != chain.get("chain_sha256"):
        raise MarketDataError("segment append plan current chain drift")
    expected = _blocked_state(admission, CHAIN_DRIFT_STATUS, chain) if admission.get("current_segment_chain_identity") != chain.get("chain_sha256") else (_blocked_state(admission, BLOCKED_ADMISSION_STATUS, chain) if admission.get("append_admission_status") != ADMITTED_STATUS else _ready_state(admission, chain))
    for key in ("candidate_identity", "candidate_start", "candidate_end", "current_segment_count", "expected_segment_ordinal", "planned_segment_count", "planned_chain_tail", "planned_next_canonical_segment_start", "existing_segments_preserved", "append_plan_materialized", "status"):
        if report.get(key) != expected.get(key):
            raise MarketDataError(f"segment append plan report drift:{key}")
    for key in ("chain_mutation_performed", "segment_appended", "new_samples_counted", "network_activity_performed", "economic_computation_authorized", "pnl_computation_authorized", "readiness_changed"):
        if report.get(key) not in (False, 0):
            raise MarketDataError(f"segment append plan forbidden claim:{key}")
    return report


def format_segment_append_plan_result(result: SegmentAppendPlanResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"plan_sha256: {report['plan_sha256']}",
            f"append_plan_materialized: {str(report['append_plan_materialized']).lower()}",
            f"status: {report['status']}",
            f"planned_segment_count: {report['planned_segment_count']}",
            f"planned_chain_tail: {report['planned_chain_tail']}",
            f"planned_next_canonical_segment_start: {report['planned_next_canonical_segment_start']}",
            "chain_mutation_performed: false",
            "segment_appended: false",
            "new_samples_counted: 0",
            "network_activity_performed: false",
        )
    )


def _validate_current_chain(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.parent.name != "prospective-direct-1h-segment-chain":
        raise MarketDataError("segment append plan chain marker path mismatch")
    report = _load_json(path)
    identity = report.get("identity")
    chain_sha = report.get("chain_sha256")
    if report.get("audit_status") != "verified_prospective_direct_1h_append_only_segment_chain" or not isinstance(identity, dict) or not _is_sha256(chain_sha) or _digest(_canonical(identity)) != chain_sha or path.name != f"prospective-direct-1h-segment-chain.{chain_sha}.json":
        raise MarketDataError("segment append plan chain identity mismatch")
    for key in ("current_chain_tail", "next_canonical_segment_start"):
        if report.get(key) != identity.get(key):
            raise MarketDataError(f"segment append plan chain report drift:{key}")
    if report.get("segment_count") != len(identity.get("segments", [])):
        raise MarketDataError("segment append plan chain report drift:segment_count")
    if report.get("economic_computation_authorized") is not False or report.get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("segment append plan chain forbidden claim")
    report["_path"] = str(path)
    return report


def _blocked_state(admission: Mapping[str, Any], status: str, chain: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "append_admission_identity": str(admission.get("admission_sha256")),
        "current_chain_identity": str((chain or {}).get("chain_sha256") or admission.get("current_segment_chain_identity")),
        "candidate_identity": str(admission.get("segment_evidence_identity")),
        "candidate_start": admission.get("candidate_start") or None,
        "candidate_end": admission.get("candidate_end") or None,
        "current_segment_count": admission.get("current_segment_count"),
        "expected_segment_ordinal": admission.get("expected_segment_ordinal"),
        "planned_segment_count": None,
        "planned_chain_tail": None,
        "planned_next_canonical_segment_start": None,
        "existing_segments_preserved": True,
        "append_plan_materialized": False,
        "status": status,
        "asset_rows": 0,
        "admission": admission,
    }


def _ready_state(admission: Mapping[str, Any], chain: Mapping[str, Any]) -> dict[str, Any]:
    candidate_end = str(admission.get("candidate_end"))
    end = _parse_iso(candidate_end)
    next_start = (end + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    current_count = int(chain["segment_count"])
    return {
        "append_admission_identity": str(admission["admission_sha256"]),
        "current_chain_identity": str(chain["chain_sha256"]),
        "candidate_identity": str(admission["segment_evidence_identity"]),
        "candidate_start": admission["candidate_start"],
        "candidate_end": candidate_end,
        "current_segment_count": current_count,
        "expected_segment_ordinal": int(admission["expected_segment_ordinal"]),
        "planned_segment_count": current_count + 1,
        "planned_chain_tail": candidate_end,
        "planned_next_canonical_segment_start": next_start,
        "existing_segments_preserved": True,
        "append_plan_materialized": True,
        "status": READY_STATUS,
        "asset_rows": 6,
        "admission": admission,
    }


def _write_result(repo: Path, output_dir: str | Path, config: Mapping[str, Any], state: Mapping[str, Any]) -> SegmentAppendPlanResult:
    output = _output_dir(repo, output_dir)
    admission = state["admission"]
    assets = _asset_rows(admission, state) if state["append_plan_materialized"] else []
    plan_row = {key: state.get(key) for key in PLAN_FIELDS}
    plan = _csv_bytes([plan_row], PLAN_FIELDS)
    assets_bytes = _csv_bytes(assets, ASSET_FIELDS)
    dependencies = _rows({"append_admission_identity": state["append_admission_identity"], "current_chain_identity": state["current_chain_identity"], "candidate_identity": state["candidate_identity"], "append_admission_path": state["admission"].get("_path", ""), "segment_chain_path": state.get("segment_chain_path", "")})
    constraints = _rows({"append_plan_materialized": state["append_plan_materialized"], "existing_segments_preserved": True, "chain_mutation_performed": False, "segment_appended": False, "current_samples": 160, "sample_threshold": 500, "remaining_samples": 340, "new_samples_counted": 0, "network_activity_performed": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False})
    identity = {"schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID, "policy": dict(config), "append_admission_identity": state["append_admission_identity"], "current_chain_identity": state["current_chain_identity"], "append_admission_path": state["admission"].get("_path", ""), "segment_chain_path": state.get("segment_chain_path", ""), **{key: state[key] for key in ("candidate_identity", "candidate_start", "candidate_end", "current_segment_count", "expected_segment_ordinal", "planned_segment_count", "planned_chain_tail", "planned_next_canonical_segment_start", "existing_segments_preserved", "append_plan_materialized", "status")}, "asset_rows": len(assets), "artifacts": {"plan_sha256": _digest(plan), "assets_sha256": _digest(assets_bytes), "dependencies_sha256": _digest(_csv_bytes(dependencies, KEY_VALUE_FIELDS)), "constraints_sha256": _digest(_csv_bytes(constraints, KEY_VALUE_FIELDS))}}
    plan_sha = _digest(_canonical(identity))
    stem = f"prospective-direct-1h-segment-append-plan.{plan_sha}"
    paths = {name: output / f"{stem}.{name}.csv" for name in ("plan", "assets", "dependencies", "constraints")}
    paths["report"] = output / f"{stem}.json"
    report = {"schema_version": SCHEMA_VERSION, "contract_status": CONTRACT_STATUS, "plan_sha256": plan_sha, **{key: state[key] for key in ("append_admission_identity", "current_chain_identity", "candidate_identity", "candidate_start", "candidate_end", "current_segment_count", "expected_segment_ordinal", "planned_segment_count", "planned_chain_tail", "planned_next_canonical_segment_start", "existing_segments_preserved", "append_plan_materialized", "status")}, "asset_rows": len(assets), "chain_mutation_performed": False, "segment_appended": False, "current_samples": 160, "sample_threshold": 500, "remaining_samples": 340, "new_samples_counted": 0, "network_activity_performed": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False, "identity": identity, "artifacts": {name: {"filename": paths[name].name, "sha256": identity["artifacts"][f"{name}_sha256"], "row_count": len(rows)} for name, rows in (("plan", [plan_row]), ("assets", assets), ("dependencies", dependencies), ("constraints", constraints))}}
    for name, content in (("plan", plan), ("assets", assets_bytes), ("dependencies", _csv_bytes(dependencies, KEY_VALUE_FIELDS)), ("constraints", _csv_bytes(constraints, KEY_VALUE_FIELDS))):
        _commit(paths[name], content)
    _commit(paths["report"], _pretty(report))
    return SegmentAppendPlanResult(report, {key: str(value) for key, value in paths.items()})


def _asset_rows(admission: Mapping[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    assets = admission.get("_asset_rows", [])
    if len(assets) != 6:
        raise MarketDataError("segment append plan candidate assets missing")
    return [{"ordinal": index, "inst_id": item["inst_id"], "candidate_canonical_sha256": item["candidate_canonical_sha256"], "candidate_bar_count": item["candidate_bar_count"], "candidate_start": state["candidate_start"], "candidate_end": state["candidate_end"], "predecessor_chain_identity": state["current_chain_identity"]} for index, item in enumerate(assets, 1)]


def _validate_artifact(report_path: Path, artifacts: Mapping[str, Any], identity: Mapping[str, Any], name: str, fields: tuple[str, ...], expected_rows: int) -> Path:
    info = artifacts.get(name)
    if not isinstance(info, dict) or info.get("sha256") != identity.get("artifacts", {}).get(f"{name}_sha256"):
        raise MarketDataError(f"segment append plan artifact metadata mismatch:{name}")
    path = (report_path.parent / str(info.get("filename", ""))).resolve()
    if path.parent != report_path.parent.resolve() or not path.is_file() or _digest(path.read_bytes()) != info.get("sha256"):
        raise MarketDataError(f"segment append plan artifact bytes mismatch:{name}")
    rows = _read_csv(path)
    if rows and tuple(rows[0]) != fields:
        raise MarketDataError(f"segment append plan artifact schema mismatch:{name}")
    if len(rows) != expected_rows:
        raise MarketDataError(f"segment append plan artifact row count mismatch:{name}")
    return path


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("segment append plan CSV read failed") from exc


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
        raise MarketDataError("segment append plan JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("segment append plan JSON shape mismatch")
    return value


def _resolve_parent(report: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise MarketDataError("segment append plan parent path missing")
    path = Path(value).resolve()
    if not path.is_file():
        raise MarketDataError("segment append plan parent missing")
    return path


def _output_dir(repo: Path, value: str | Path) -> Path:
    output = Path(value).resolve() if Path(value).is_absolute() else (repo / value).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("segment append plan output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src/crypto_bot").is_dir():
            return parent
    raise MarketDataError("segment append plan repo root not found")


def _parse_iso(value: str):
    from datetime import datetime
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("segment append plan timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise MarketDataError("segment append plan timestamp timezone missing")
    return parsed


def _resolve_report_path(path: Path) -> str:
    return str(path.resolve())


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
            raise MarketDataError(f"segment append plan artifact collision:{path.name}")
        return
    path.write_bytes(content)
