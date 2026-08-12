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
from crypto_bot.market.prospective_direct_1h_segment_append_preflight import (
    validate_preflight,
)
from crypto_bot.market.prospective_direct_1h_segment_evidence import (
    derive_prospective_direct_1h_segment_evidence_provenance,
    validate_prospective_direct_1h_segment_evidence,
)

SCHEMA_VERSION = 1
POLICY_ID = "prospective_direct_1h_segment_append_authorization_v1"
CONTRACT_STATUS = "verified_prospective_direct_1h_segment_append_authorization"
BLOCKED_PREFLIGHT = "blocked_preflight_not_ready"
BLOCKED_EVIDENCE = "blocked_non_real_market_evidence"
BLOCKED_LINEAGE = "blocked_candidate_lineage_mismatch"
BLOCKED_PROVENANCE = "blocked_insufficient_validated_provenance"
READY = "append_authorization_ready"
DEFAULT_CONFIG_FILENAME = "config.prospective-direct-1h-segment-append-authorization.example.yaml"
AUTH_FIELDS = (
    "preflight_identity",
    "segment_evidence_identity",
    "candidate_identity",
    "preflight_ready",
    "candidate_lineage_match",
    "validated_fixture_only",
    "validated_market_evidence",
    "validated_public_only_source",
    "append_authorization_eligible",
    "authoritative_write_authorized",
    "append_performed",
    "segment_appended",
    "chain_mutation_performed",
)
PROVENANCE_FIELDS = (
    "source_artifact_identity",
    "source_capture_identity",
    "source_provider",
    "source_mode",
    "fixture_only",
    "market_evidence",
    "public_only",
)
KEY_FIELDS = ("key", "value")
FALSE_FLAGS = (
    "network_activity_performed",
    "authorization_gate_network_activity_performed",
    "append_performed",
    "segment_appended",
    "chain_mutation_performed",
    "economic_computation_authorized",
    "pnl_computation_authorized",
    "readiness_changed",
)


@dataclass(frozen=True)
class AuthorizationResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_authorization_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("authorization config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("authorization config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("authorization config policy mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("authorization config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def authorize_prospective_direct_1h_segment_append(
    append_preflight: str | Path,
    segment_evidence: str | Path,
    config: str | Path,
    output_dir: str | Path,
) -> AuthorizationResult:
    preflight_path = Path(append_preflight).resolve()
    evidence_path = Path(segment_evidence).resolve()
    repo = _repo_root(preflight_path)
    policy = load_authorization_config(config, repo)
    preflight = validate_preflight(preflight_path)
    evidence = validate_prospective_direct_1h_segment_evidence(evidence_path)
    provenance = derive_prospective_direct_1h_segment_evidence_provenance(
        evidence_path, evidence
    )
    state = _derive_state(preflight, evidence, provenance)
    return _write(repo, output_dir, policy, state)


def evaluate_authorization_gate(values: Mapping[str, Any]) -> dict[str, Any]:
    """Pure predicate used for positive-branch tests; never persists evidence."""
    required = (
        values.get("preflight_ready") is True,
        values.get("candidate_lineage_match") is True,
        values.get("fixture_only") is False,
        values.get("market_evidence") is True,
        values.get("public_only") is True,
    )
    eligible = all(required)
    return {
        "append_authorization_eligible": eligible,
        "authoritative_write_authorized": eligible,
        "reason": "eligible" if eligible else "provenance_or_lineage_not_authorized",
    }


def validate_authorization(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _load_json(report_path)
    if report.get("contract_status") != CONTRACT_STATUS or report.get("schema_version") != SCHEMA_VERSION:
        raise MarketDataError("authorization contract mismatch")
    identity = report.get("identity")
    authorization_sha = report.get("authorization_sha256")
    if (
        not isinstance(identity, dict)
        or not _is_sha(authorization_sha)
        or report_path.name != f"prospective-direct-1h-segment-append-authorization.{authorization_sha}.json"
        or _digest(_canon(identity)) != authorization_sha
    ):
        raise MarketDataError("authorization identity mismatch")
    repo = _repo_root(report_path)
    policy = load_authorization_config(repo / DEFAULT_CONFIG_FILENAME, repo)
    if identity.get("policy_id") != POLICY_ID or identity.get("policy") != policy:
        raise MarketDataError("authorization policy mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("authorization artifacts missing")
    materialized = report.get("authorization_materialized")
    if not isinstance(materialized, bool):
        raise MarketDataError("authorization materialized flag missing")
    expected_materialized = report.get("status") != BLOCKED_PREFLIGHT
    if materialized is not expected_materialized:
        raise MarketDataError("authorization materialized status mismatch")
    provenance_count = 1 if materialized else 0
    for name, fields, count in (("authorization", AUTH_FIELDS, 1), ("provenance", PROVENANCE_FIELDS, provenance_count), ("dependencies", KEY_FIELDS, None), ("constraints", KEY_FIELDS, None)):
        _artifact(report_path, artifacts, identity_artifacts, name, fields, count)
    for field in FALSE_FLAGS:
        if report.get(field) is not False:
            raise MarketDataError(f"authorization forbidden claim:{field}")
    if report.get("current_samples") != 160 or report.get("sample_threshold") != 500 or report.get("remaining_samples") != 340 or report.get("new_samples_counted") != 0:
        raise MarketDataError("authorization sample constraint")
    if report.get("authoritative_write_authorized") is not (report.get("append_authorization_eligible") is True):
        raise MarketDataError("authorization permission mismatch")
    if report.get("status") == READY and report.get("append_authorization_eligible") is not True:
        raise MarketDataError("authorization ready status mismatch")
    if report.get("status") != READY and report.get("append_authorization_eligible") is not False:
        raise MarketDataError("authorization blocked status mismatch")
    parent_reports = identity.get("parent_reports")
    if not isinstance(parent_reports, dict):
        raise MarketDataError("authorization parent reports missing")
    preflight_path = _locate_parent_report(repo, parent_reports.get("preflight"), "prospective-direct-1h-segment-append-preflight")
    evidence_path = _locate_parent_report(repo, parent_reports.get("segment_evidence"), "prospective-direct-1h-segment-evidence")
    preflight = validate_preflight(preflight_path)
    evidence = validate_prospective_direct_1h_segment_evidence(evidence_path)
    provenance = derive_prospective_direct_1h_segment_evidence_provenance(evidence_path, evidence)
    expected = _derive_state(preflight, evidence, provenance)
    for field in ("status", "authorization_materialized", *AUTH_FIELDS):
        if report.get(field) != expected.get(field):
            raise MarketDataError(f"authorization replay mismatch:{field}")
    return report


def format_authorization(result: AuthorizationResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"authorization_sha256: {report['authorization_sha256']}",
            f"status: {report['status']}",
            f"authorization_materialized: {str(report['authorization_materialized']).lower()}",
            f"append_authorization_eligible: {str(report['append_authorization_eligible']).lower()}",
            f"authoritative_write_authorized: {str(report['authoritative_write_authorized']).lower()}",
            "network_activity_performed: false",
            "authorization_gate_network_activity_performed: false",
            "append_performed: false",
            "segment_appended: false",
            "chain_mutation_performed: false",
        )
    )


def _derive_state(
    preflight: Mapping[str, Any],
    evidence: Mapping[str, Any],
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    preflight_ready = preflight.get("status") == "preflight_ready" and preflight.get("commit_preflight_ready") is True
    preflight_candidate = preflight.get("candidate_identity")
    evidence_candidate = evidence.get("candidate_sha256")
    lineage_match = preflight_candidate == evidence_candidate
    validated = dict(provenance or {})
    fixture_only = validated.get("fixture_only") is True
    market_evidence = validated.get("market_evidence") is True
    public_only = validated.get("public_only") is True
    if not preflight_ready:
        status = BLOCKED_PREFLIGHT
    elif not lineage_match:
        status = BLOCKED_LINEAGE
    elif fixture_only or not market_evidence:
        status = BLOCKED_EVIDENCE
    elif not public_only:
        status = BLOCKED_PROVENANCE
    else:
        status = READY
    decision = evaluate_authorization_gate({"preflight_ready": preflight_ready, "candidate_lineage_match": lineage_match, "fixture_only": fixture_only, "market_evidence": market_evidence, "public_only": public_only})
    return {
        "status": status,
        "authorization_materialized": preflight_ready,
        "preflight_identity": preflight.get("preflight_sha256"),
        "segment_evidence_identity": evidence.get("candidate_sha256"),
        "candidate_identity": evidence_candidate,
        "preflight_ready": preflight_ready,
        "candidate_lineage_match": lineage_match,
        "validated_fixture_only": fixture_only,
        "validated_market_evidence": market_evidence,
        "validated_public_only_source": public_only,
        "append_authorization_eligible": decision["append_authorization_eligible"],
        "authoritative_write_authorized": decision["authoritative_write_authorized"],
        "append_performed": False,
        "segment_appended": False,
        "chain_mutation_performed": False,
        "preflight": preflight,
        "evidence": evidence,
        "provenance": validated,
    }


def _write(repo: Path, output_dir: str | Path, policy: Mapping[str, Any], state: Mapping[str, Any]) -> AuthorizationResult:
    output = _output(repo, output_dir)
    provenance_values = state["provenance"]
    provenance = [
        {field: provenance_values.get(field) for field in PROVENANCE_FIELDS}
    ] if state["preflight_ready"] else []
    authorization = [{key: state.get(key) for key in AUTH_FIELDS}]
    dependencies = _rows({
        "preflight_identity": state["preflight_identity"],
        "segment_evidence_identity": state["segment_evidence_identity"],
        "candidate_identity": state["candidate_identity"],
        "preflight_contract": "verified_prospective_direct_1h_segment_append_preflight",
        "evidence_contract": "verified_prospective_direct_1h_segment_evidence",
    })
    constraints = _rows({
        "append_authorization_eligible": state["append_authorization_eligible"],
        "append_performed": False,
        "authoritative_write_authorized": state["authoritative_write_authorized"],
        "chain_mutation_performed": False,
        "current_samples": 160,
        "economic_computation_authorized": False,
        "market_data_segment_is_not_sample": True,
        "network_activity_performed": False,
        "authorization_gate_network_activity_performed": False,
        "new_samples_counted": 0,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "remaining_samples": 340,
        "segment_appended": False,
    })
    contents = {
        "authorization": _csv(authorization, AUTH_FIELDS),
        "provenance": _csv(provenance, PROVENANCE_FIELDS),
        "dependencies": _csv(dependencies, KEY_FIELDS),
        "constraints": _csv(constraints, KEY_FIELDS),
    }
    hashes = {name: _digest(data) for name, data in contents.items()}
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "policy": dict(policy),
        **{key: state[key] for key in ("status", "authorization_materialized", *AUTH_FIELDS)},
        "provenance": provenance,
        "parent_reports": {
            "preflight": f"prospective-direct-1h-segment-append-preflight.{state['preflight_identity']}.json",
            "segment_evidence": f"prospective-direct-1h-segment-evidence.{state['segment_evidence_identity']}.json",
        },
        "network_activity_performed": False,
        "authorization_gate_network_activity_performed": False,
        "artifacts": {f"{name}_sha256": digest for name, digest in hashes.items()},
    }
    authorization_sha = _digest(_canon(identity))
    stem = f"prospective-direct-1h-segment-append-authorization.{authorization_sha}"
    paths = {name: output / f"{stem}.{name}.csv" for name in contents}
    paths["report"] = output / f"{stem}.json"
    artifacts = {name: {"filename": paths[name].name, "sha256": hashes[name], "row_count": len(_read_csv_bytes(data))} for name, data in contents.items()}
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_status": CONTRACT_STATUS,
        "authorization_sha256": authorization_sha,
        **{key: state[key] for key in ("status", "authorization_materialized", *AUTH_FIELDS)},
        "append_performed": False,
        "segment_appended": False,
        "chain_mutation_performed": False,
        "network_activity_performed": False,
        "authorization_gate_network_activity_performed": False,
        "current_samples": 160,
        "sample_threshold": 500,
        "remaining_samples": 340,
        "new_samples_counted": 0,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "identity": identity,
        "artifacts": artifacts,
    }
    for name, data in contents.items():
        _commit(paths[name], data)
    _commit(paths["report"], _pretty(report))
    return AuthorizationResult(report, {name: str(path) for name, path in paths.items()})


def _artifact(report_path: Path, artifacts: Any, identity_artifacts: Mapping[str, Any], name: str, fields: tuple[str, ...], count: int | None) -> Path:
    if not isinstance(artifacts, dict):
        raise MarketDataError("authorization artifacts shape")
    info = artifacts.get(name)
    expected = identity_artifacts.get(f"{name}_sha256")
    if not isinstance(info, dict) or info.get("sha256") != expected:
        raise MarketDataError(f"authorization artifact metadata:{name}")
    filename = info.get("filename")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise MarketDataError(f"authorization artifact filename:{name}")
    path = (report_path.parent / filename).resolve()
    if path.parent != report_path.parent.resolve() or not path.is_file() or _digest(path.read_bytes()) != expected:
        raise MarketDataError(f"authorization artifact bytes:{name}")
    header, rows = _read_table(path)
    if header != fields or (count is not None and len(rows) != count):
        raise MarketDataError(f"authorization artifact schema/count:{name}")
    return path


def _read_table(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return tuple(reader.fieldnames or ()), list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("authorization csv read failed") from exc


def _read_csv_bytes(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8"))))


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
        raise MarketDataError("authorization json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("authorization json shape")
    return value


def _output(repo: Path, value: str | Path) -> Path:
    requested = Path(value)
    output = requested.resolve() if requested.is_absolute() else (repo / requested).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("authorization output outside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src/crypto_bot").is_dir():
            return parent
    raise MarketDataError("authorization repo root missing")


def _locate_parent_report(repo: Path, filename: Any, prefix: str) -> Path:
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not filename.startswith(f"{prefix}.")
        or not filename.endswith(".json")
    ):
        raise MarketDataError("authorization parent report reference mismatch")
    matches = [
        path
        for path in (repo / "reports").rglob(filename)
        if path.is_file()
    ]
    if len(matches) != 1:
        raise MarketDataError("authorization parent report reference ambiguous")
    return matches[0]


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
            raise MarketDataError(f"authorization collision:{path.name}")
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".authorization-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
