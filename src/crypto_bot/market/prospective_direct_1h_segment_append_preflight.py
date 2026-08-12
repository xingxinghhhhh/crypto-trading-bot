from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_direct_1h_segment_append_plan import _validate_current_chain
from crypto_bot.market.prospective_direct_1h_segment_append_prepared import (
    READY_RESULT,
    recompute_proposed_post_identity,
    validate_prepared_segment,
)

SCHEMA_VERSION = 1
POLICY_ID = "prospective_direct_1h_segment_append_preflight_v1"
CONTRACT_STATUS = "verified_prospective_direct_1h_segment_append_preflight"
READY = "preflight_ready"
BLOCKED_PREPARED = "blocked_prepared_chain_not_ready"
DRIFT = "blocked_authoritative_chain_drift"
DEFAULT_CONFIG_FILENAME = "config.prospective-direct-1h-segment-append-preflight.example.yaml"
CAS_FIELDS = (
    "prepared_identity",
    "expected_parent_chain_identity",
    "observed_current_chain_identity",
    "parent_identity_match",
    "expected_parent_segment_count",
    "observed_current_segment_count",
    "expected_parent_tail",
    "observed_current_tail",
    "candidate_identity",
    "expected_segment_ordinal",
    "expected_post_chain_identity",
    "expected_post_segment_count",
    "expected_post_tail",
    "expected_post_next_start",
    "commit_preflight_ready",
    "commit_authorized",
)
WRITE_FIELDS = ("write_ordinal", "artifact_role", "operation", "source_identity", "expected_sha256")
KEY_FIELDS = ("key", "value")
FALSE_FLAGS = (
    "commit_authorized",
    "promotion_authorized",
    "authoritative_write_authorized",
    "chain_mutation_performed",
    "segment_appended",
    "network_activity_performed",
    "economic_computation_authorized",
    "pnl_computation_authorized",
    "readiness_changed",
)


@dataclass(frozen=True)
class PreflightResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_preflight_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("preflight config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME
        frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("preflight config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("preflight config policy mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("preflight config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def preflight_prospective_direct_1h_segment_append(
    prepared: str | Path,
    segment_chain: str | Path,
    config: str | Path,
    output_dir: str | Path,
) -> PreflightResult:
    prepared_path = Path(prepared).resolve()
    chain_path = Path(segment_chain).resolve()
    repo = _repo_root(prepared_path)
    policy = load_preflight_config(config, repo)
    prepared_report = validate_prepared_segment(prepared_path)
    current_chain = _validate_current_chain(chain_path)
    expected_parent = str(prepared_report.get("authoritative_pre_chain_identity"))
    observed_parent = str(current_chain.get("chain_sha256"))
    if expected_parent != observed_parent:
        status = DRIFT
    elif prepared_report.get("status") != READY_RESULT or prepared_report.get("prepared_chain_materialized") is not True:
        status = BLOCKED_PREPARED
    else:
        _verify_post_identity(prepared_report)
        status = READY
    state = _state(prepared_report, current_chain, status)
    return _write(repo, output_dir, policy, state)


def validate_preflight(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _load_json(report_path)
    if report.get("contract_status") != CONTRACT_STATUS or report.get("schema_version") != SCHEMA_VERSION:
        raise MarketDataError("preflight contract mismatch")
    identity = report.get("identity")
    preflight_sha = report.get("preflight_sha256")
    if not isinstance(identity, dict) or not _is_sha(preflight_sha) or _digest(_canon(identity)) != preflight_sha:
        raise MarketDataError("preflight identity mismatch")
    repo = _repo_root(report_path)
    policy = load_preflight_config(repo / DEFAULT_CONFIG_FILENAME, repo)
    if identity.get("policy_id") != POLICY_ID or identity.get("policy") != policy:
        raise MarketDataError("preflight policy mismatch")
    if _contains_forbidden_path_key(identity):
        raise MarketDataError("preflight identity contains path")
    status = report.get("status")
    if status not in (READY, BLOCKED_PREPARED, DRIFT):
        raise MarketDataError("preflight status mismatch")
    materialized: bool = status == READY
    if report.get("preflight_materialized") is not materialized or report.get("commit_preflight_ready") is not materialized:
        raise MarketDataError("preflight materialization mismatch")
    for field in FALSE_FLAGS:
        if report.get(field) is not False:
            raise MarketDataError(f"preflight forbidden claim:{field}")
    if report.get("current_samples") != 160 or report.get("remaining_samples") != 340 or report.get("new_samples_counted") != 0:
        raise MarketDataError("preflight sample constraint")
    expected_rows = {"cas": 1, "write_set": 3 if materialized else 0}
    for name, fields in (("cas", CAS_FIELDS), ("write_set", WRITE_FIELDS), ("dependencies", KEY_FIELDS), ("constraints", KEY_FIELDS)):
        info = _artifact(report_path, report.get("artifacts"), identity, name, fields, expected_rows.get(name, None))
        if name == "cas":
            cas_rows = _read(info)
            if len(cas_rows) != 1 or any(cas_rows[0].get(field) != _value(report.get(field)) for field in CAS_FIELDS):
                raise MarketDataError("preflight CAS row mismatch")
        elif name == "write_set":
            _validate_write_set(_read(info), materialized, report)
    if not materialized:
        for field in ("candidate_identity", "expected_segment_ordinal", "expected_parent_segment_count", "observed_current_segment_count", "expected_parent_tail", "observed_current_tail", "expected_post_chain_identity", "expected_post_segment_count", "expected_post_tail", "expected_post_next_start"):
            if report.get(field) is not None:
                raise MarketDataError(f"blocked preflight field:{field}")
    else:
        if report.get("parent_identity_match") is not True or report.get("expected_post_segment_count") != int(report["expected_parent_segment_count"]) + 1:
            raise MarketDataError("preflight parent/post count mismatch")
        if not all(_is_sha(report.get(field)) for field in ("prepared_identity", "expected_parent_chain_identity", "observed_current_chain_identity", "candidate_identity", "expected_post_chain_identity")):
            raise MarketDataError("preflight identity fields missing")
    expected_top = {key: identity.get(key) for key in _IDENTITY_FIELDS}
    if any(report.get(key) != value for key, value in expected_top.items()):
        raise MarketDataError("preflight identity/report mismatch")
    return report


def format_preflight(result: PreflightResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"preflight_sha256: {report['preflight_sha256']}",
            f"status: {report['status']}",
            f"preflight_materialized: {str(report['preflight_materialized']).lower()}",
            f"commit_preflight_ready: {str(report['commit_preflight_ready']).lower()}",
            "commit_authorized: false",
            "authoritative_write_authorized: false",
            "chain_mutation_performed: false",
            "segment_appended: false",
        )
    )


_IDENTITY_FIELDS = (
    "status",
    "preflight_materialized",
    "prepared_identity",
    "expected_parent_chain_identity",
    "observed_current_chain_identity",
    "parent_identity_match",
    "expected_parent_segment_count",
    "observed_current_segment_count",
    "expected_parent_tail",
    "observed_current_tail",
    "candidate_identity",
    "expected_segment_ordinal",
    "expected_post_chain_identity",
    "expected_post_segment_count",
    "expected_post_tail",
    "expected_post_next_start",
    "commit_preflight_ready",
    "commit_authorized",
)


def _verify_post_identity(prepared: Mapping[str, Any]) -> None:
    segments = prepared.get("segments")
    if not isinstance(segments, list):
        raise MarketDataError("preflight prepared segments missing")
    try:
        recomputed = recompute_proposed_post_identity(
            str(prepared["current_chain_identity"]),
            str(prepared["candidate_identity"]),
            segments,
            int(prepared["proposed_segment_count"]),
            str(prepared["proposed_chain_tail"]),
            str(prepared["proposed_next_start"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataError("preflight post identity inputs missing") from exc
    if recomputed != prepared.get("expected_post_append_chain_identity"):
        raise MarketDataError("preflight post identity mismatch")


def _state(prepared: Mapping[str, Any], chain: Mapping[str, Any], status: str) -> dict[str, Any]:
    ready = status == READY
    expected_parent = str(prepared.get("authoritative_pre_chain_identity"))
    observed = str(chain.get("chain_sha256"))
    state: dict[str, Any] = {
        "status": status,
        "preflight_materialized": ready,
        "prepared_identity": str(prepared.get("prepared_sha256")),
        "expected_parent_chain_identity": expected_parent,
        "observed_current_chain_identity": observed,
        "parent_identity_match": expected_parent == observed,
        "expected_parent_segment_count": int(chain["segment_count"]) if ready else None,
        "observed_current_segment_count": int(chain["segment_count"]) if ready else None,
        "expected_parent_tail": str(chain["current_chain_tail"]) if ready else None,
        "observed_current_tail": str(chain["current_chain_tail"]) if ready else None,
        "candidate_identity": str(prepared.get("candidate_identity")) if ready else None,
        "expected_segment_ordinal": _candidate_ordinal(prepared) if ready else None,
        "expected_post_chain_identity": str(prepared.get("expected_post_append_chain_identity")) if ready else None,
        "expected_post_segment_count": (int(chain["segment_count"]) + 1) if ready else None,
        "expected_post_tail": str(prepared.get("proposed_chain_tail")) if ready else None,
        "expected_post_next_start": str(prepared.get("proposed_next_start")) if ready else None,
        "commit_preflight_ready": ready,
        "commit_authorized": False,
        "prepared": prepared,
    }
    return state


def _candidate_ordinal(prepared: Mapping[str, Any]) -> int:
    segments = prepared.get("segments")
    if not isinstance(segments, list) or not segments or not isinstance(segments[-1], Mapping):
        raise MarketDataError("preflight candidate ordinal missing")
    try:
        ordinal = int(segments[-1]["ordinal"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataError("preflight candidate ordinal invalid") from exc
    if ordinal < 1 or ordinal != int(prepared.get("proposed_segment_count", 0)):
        raise MarketDataError("preflight candidate ordinal discontinuity")
    return ordinal


def _write(repo: Path, output_dir: str | Path, policy: Mapping[str, Any], state: Mapping[str, Any]) -> PreflightResult:
    output = _output(repo, output_dir)
    ready = bool(state["preflight_materialized"])
    write_set = _write_set(state) if ready else []
    cas_rows = [{field: state.get(field) for field in CAS_FIELDS}]
    dependencies = _rows(
        {
            "candidate_identity": state.get("candidate_identity"),
            "expected_parent_chain_identity": state["expected_parent_chain_identity"],
            "observed_current_chain_identity": state["observed_current_chain_identity"],
            "prepared_identity": state["prepared_identity"],
            "prepared_contract": "verified_prospective_direct_1h_segment_append_prepared",
            "post_identity_recomputed": ready,
        }
    )
    constraints = _rows(
        {
            "authoritative_write_authorized": False,
            "chain_mutation_performed": False,
            "commit_authorized": False,
            "commit_preflight_ready": ready,
            "current_samples": 160,
            "economic_computation_authorized": False,
            "market_data_segment_is_not_sample": True,
            "network_activity_performed": False,
            "new_samples_counted": 0,
            "pnl_computation_authorized": False,
            "promotion_authorized": False,
            "readiness_changed": False,
            "remaining_samples": 340,
            "segment_appended": False,
        }
    )
    artifact_contents = {
        "cas": _csv(cas_rows, CAS_FIELDS),
        "write_set": _csv(write_set, WRITE_FIELDS),
        "dependencies": _csv(dependencies, KEY_FIELDS),
        "constraints": _csv(constraints, KEY_FIELDS),
    }
    artifact_hashes = {name: _digest(content) for name, content in artifact_contents.items()}
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "policy": dict(policy),
        **{key: state.get(key) for key in _IDENTITY_FIELDS},
        "artifacts": {f"{name}_sha256": digest for name, digest in artifact_hashes.items()},
    }
    preflight_sha = _digest(_canon(identity))
    stem = f"prospective-direct-1h-segment-append-preflight.{preflight_sha}"
    paths = {
        "cas": output / f"{stem}.cas.csv",
        "write_set": output / f"{stem}.write-set.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    artifacts = {
        name: {"filename": paths[name].name, "sha256": artifact_hashes[name], "row_count": len(_read_csv_rows(content))}
        for name, content in artifact_contents.items()
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_status": CONTRACT_STATUS,
        "preflight_sha256": preflight_sha,
        **{key: state.get(key) for key in _IDENTITY_FIELDS},
        "promotion_authorized": False,
        "authoritative_write_authorized": False,
        "chain_mutation_performed": False,
        "segment_appended": False,
        "current_samples": 160,
        "sample_threshold": 500,
        "remaining_samples": 340,
        "new_samples_counted": 0,
        "network_activity_performed": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "identity": identity,
        "artifacts": artifacts,
    }
    for name, content in artifact_contents.items():
        _commit(paths[name], content)
    _commit(paths["report"], _pretty(report))
    return PreflightResult(report, {name: str(path) for name, path in paths.items()})


def _write_set(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    parent = str(state["expected_parent_chain_identity"])
    candidate = str(state["candidate_identity"])
    post = str(state["expected_post_chain_identity"])
    return [
        {"write_ordinal": 1, "artifact_role": "existing_segment_refs", "operation": "preserve", "source_identity": parent, "expected_sha256": parent},
        {"write_ordinal": 2, "artifact_role": "candidate_segment", "operation": "append_exactly_once", "source_identity": candidate, "expected_sha256": candidate},
        {"write_ordinal": 3, "artifact_role": "authoritative_chain_marker", "operation": "materialize_post_chain_marker_last", "source_identity": post, "expected_sha256": post},
    ]


def _validate_write_set(rows: list[dict[str, str]], ready: bool, report: Mapping[str, Any]) -> None:
    if not ready:
        if rows:
            raise MarketDataError("blocked preflight write-set not empty")
        return
    expected = _write_set(report)
    if rows != [{key: _value(row.get(key)) for key in WRITE_FIELDS} for row in expected]:
        raise MarketDataError("preflight write-set mismatch")


def _artifact(report_path: Path, artifacts: Any, identity: Mapping[str, Any], name: str, fields: tuple[str, ...], expected_count: int | None) -> Path:
    if not isinstance(artifacts, dict):
        raise MarketDataError("preflight artifacts missing")
    info = artifacts.get(name)
    expected_sha = identity.get("artifacts", {}).get(f"{name}_sha256") if isinstance(identity.get("artifacts"), dict) else None
    if not isinstance(info, dict) or info.get("sha256") != expected_sha:
        raise MarketDataError(f"preflight artifact metadata:{name}")
    filename = info.get("filename")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise MarketDataError(f"preflight artifact filename:{name}")
    artifact_path = (report_path.parent / filename).resolve()
    if artifact_path.parent != report_path.parent.resolve() or not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected_sha:
        raise MarketDataError(f"preflight artifact bytes:{name}")
    header, rows = _read_table(artifact_path)
    if header != fields or (expected_count is not None and len(rows) != expected_count):
        raise MarketDataError(f"preflight artifact schema/count:{name}")
    return artifact_path


def _read_table(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return tuple(reader.fieldnames or ()), list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("preflight csv read failed") from exc


def _read(path: Path) -> list[dict[str, str]]:
    return _read_table(path)[1]


def _read_csv_rows(content: bytes) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    return list(reader)


def _csv(rows: Sequence[Mapping[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _value(row.get(field)) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _rows(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": values[key]} for key in sorted(values)]


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("preflight json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("preflight json shape")
    return value


def _output(repo: Path, value: str | Path) -> Path:
    requested = Path(value)
    output = requested.resolve() if requested.is_absolute() else (repo / requested).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("preflight output outside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src/crypto_bot").is_dir():
            return parent
    raise MarketDataError("preflight repo root missing")


def _contains_forbidden_path_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower().endswith("path") or str(key).lower() in {"output_dir", "temporary_path", "pid", "duration", "wall_clock"}:
                return True
            if _contains_forbidden_path_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_path_key(item) for item in value)
    return False


def _is_sha(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _commit(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"preflight collision:{path.name}")
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".preflight-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
