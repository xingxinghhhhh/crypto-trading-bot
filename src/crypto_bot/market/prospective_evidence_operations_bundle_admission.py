from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_bundle import (
    FALSE_BUNDLE_FLAGS,
    SOURCE_STATE_FIELDS,
    validate_prospective_evidence_operations_bundle,
)
from crypto_bot.market.prospective_evidence_operations_snapshot import (
    validate_prospective_evidence_operations_snapshot,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_evidence_operations_bundle_admission_v1"
CONTRACT_STATUS = "verified_prospective_evidence_operations_bundle_admission"
DEFAULT_CONFIG_FILENAME = "config.prospective-evidence-operations-bundle-admission.example.yaml"
PREFIX = "prospective-evidence-operations-bundle-admission"
ADMISSION_FIELDS = (
    "status",
    "bundle_currentness_admitted",
    "bundle_stale",
    "bundle_sha256",
    "source_operations_snapshot_identity",
    "current_operations_snapshot_identity",
    "source_projection_sha256",
    "current_projection_sha256",
    "state_changed",
    "bundle_mutated",
    "authoritative_action_authorized",
    "network_activity_performed",
    "new_samples_counted",
)
KEY_VALUE_FIELDS = ("key", "value")
POLICY_FIELDS = {
    "schema_version": SCHEMA_VERSION,
    "policy_id": POLICY_ID,
    "exact_source_snapshot_identity_required": True,
    "source_projection_match_required": True,
    "stale_bundle_consumption_prohibited": True,
    "automatic_bundle_refresh_prohibited": True,
    "manual_currentness_override_prohibited": True,
    "bundle_mutation_prohibited": True,
    "state_mutation_prohibited": True,
    "authorization_escalation_prohibited": True,
    "network_activity_prohibited": True,
}
ADMISSION_FLAGS = {
    "state_changed": False,
    "bundle_mutated": False,
    "authoritative_action_authorized": False,
    "network_activity_performed": False,
    "new_samples_counted": 0,
}


@dataclass(frozen=True)
class OperationsBundleAdmissionResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_operations_bundle_admission_config(
    path: str | Path, repo: Path | None = None
) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("operations bundle admission config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME
        frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("operations bundle admission config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("operations bundle admission config policy mismatch")
    if not isinstance(value, dict) or value != POLICY_FIELDS:
        raise MarketDataError("operations bundle admission config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def freeze_prospective_evidence_operations_bundle_admission(
    operations_bundle: str | Path,
    current_operations_snapshot: str | Path,
    config: str | Path,
    output_dir: str | Path,
) -> OperationsBundleAdmissionResult:
    bundle_path = Path(operations_bundle).resolve()
    current_path = Path(current_operations_snapshot).resolve()
    repo = _repo_root(current_path)
    policy = load_operations_bundle_admission_config(config, repo)
    bundle = validate_prospective_evidence_operations_bundle(bundle_path)
    current = validate_prospective_evidence_operations_snapshot(current_path)
    derived = _derive_admission(bundle, current, policy)
    output = _output_dir(repo, output_dir)
    admission_bytes = _csv_bytes([_admission_row(derived)], ADMISSION_FIELDS)
    dependencies = {
        "bundle_sha256": bundle["bundle_sha256"],
        "source_operations_snapshot_identity": bundle["source_operations_snapshot_identity"],
        "current_operations_snapshot_identity": current["snapshot_sha256"],
    }
    dependencies_bytes = _csv_bytes(_key_value_rows(dependencies), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_key_value_rows(policy), KEY_VALUE_FIELDS)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "bundle_sha256": bundle["bundle_sha256"],
        "source_operations_snapshot_identity": bundle["source_operations_snapshot_identity"],
        "current_operations_snapshot_identity": current["snapshot_sha256"],
        "source_projection": _projection(bundle),
        "current_projection": _projection(current),
        "derived": derived,
        "policy": policy,
        "claims": dict(ADMISSION_FLAGS),
        "artifacts": {
            "admission_sha256": _digest(admission_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    admission_sha = _digest(_canonical(identity))
    stem = f"{PREFIX}.{admission_sha}"
    paths = {
        "admission": output / f"{stem}.admission.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_status": CONTRACT_STATUS,
        "admission_sha256": admission_sha,
        "status": derived["status"],
        "bundle_currentness_admitted": derived["bundle_currentness_admitted"],
        "bundle_stale": derived["bundle_stale"],
        **ADMISSION_FLAGS,
        "bundle_sha256": bundle["bundle_sha256"],
        "source_operations_snapshot_identity": bundle["source_operations_snapshot_identity"],
        "current_operations_snapshot_identity": current["snapshot_sha256"],
        "source_projection": _projection(bundle),
        "current_projection": _projection(current),
        "identity": identity,
        "artifacts": {
            "admission": {
                "filename": paths["admission"].name,
                "sha256": identity["artifacts"]["admission_sha256"],
                "row_count": 1,
            },
            "dependencies": {
                "filename": paths["dependencies"].name,
                "sha256": identity["artifacts"]["dependencies_sha256"],
                "row_count": len(dependencies),
            },
            "constraints": {
                "filename": paths["constraints"].name,
                "sha256": identity["artifacts"]["constraints_sha256"],
                "row_count": len(policy),
            },
            "report": {"filename": paths["report"].name},
        },
    }
    _commit_bytes(paths["admission"], admission_bytes)
    _commit_bytes(paths["dependencies"], dependencies_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    _commit_bytes(paths["report"], _pretty(report))
    validate_prospective_evidence_operations_bundle_admission(
        paths["report"], bundle_path, current_path
    )
    return OperationsBundleAdmissionResult(
        report, {key: str(value) for key, value in paths.items()}
    )


def validate_prospective_evidence_operations_bundle_admission(
    path: str | Path,
    operations_bundle: str | Path | None = None,
    current_operations_snapshot: str | Path | None = None,
) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _load_json(report_path)
    admission_sha = report.get("admission_sha256")
    identity = report.get("identity")
    if (
        not isinstance(admission_sha, str)
        or report_path.name != f"{PREFIX}.{admission_sha}.json"
        or not isinstance(identity, dict)
        or _digest(_canonical(identity)) != admission_sha
        or report.get("schema_version") != SCHEMA_VERSION
        or report.get("contract_status") != CONTRACT_STATUS
    ):
        raise MarketDataError("operations bundle admission identity mismatch")
    _validate_policy(identity)
    if report.get("identity") != identity:
        raise MarketDataError("operations bundle admission identity mismatch")
    for key, expected in ADMISSION_FLAGS.items():
        if report.get(key) != expected or identity.get("claims", {}).get(key) != expected:
            raise MarketDataError(f"operations bundle admission forbidden flag:{key}")
    derived = identity.get("derived")
    if not isinstance(derived, dict) or report.get("status") != derived.get("status"):
        raise MarketDataError("operations bundle admission derived state mismatch")
    if any(report.get(key) != derived.get(key) for key in ("bundle_currentness_admitted", "bundle_stale")):
        raise MarketDataError("operations bundle admission derived state mismatch")
    for key in (
        "bundle_sha256",
        "source_operations_snapshot_identity",
        "current_operations_snapshot_identity",
        "source_projection",
        "current_projection",
    ):
        if report.get(key) != identity.get(key):
            raise MarketDataError(f"operations bundle admission report mismatch:{key}")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("operations bundle admission artifacts missing")
    sidecars = {}
    for key in ("admission", "dependencies", "constraints"):
        info = artifacts.get(key)
        expected_hash: Any = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(info, dict) or info.get("sha256") != expected_hash:
            raise MarketDataError(f"operations bundle admission artifact metadata:{key}")
        filename = info.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise MarketDataError(f"operations bundle admission artifact filename:{key}")
        artifact_path = report_path.parent / filename
        if not artifact_path.is_file() or _digest(artifact_path.read_bytes()) != expected_hash:
            raise MarketDataError(f"operations bundle admission artifact bytes:{key}")
        sidecars[key] = artifact_path.read_bytes()
    admission_rows = _read_csv_exact(sidecars["admission"], ADMISSION_FIELDS)
    if admission_rows != [{field: _csv_value(_admission_row(derived)[field]) for field in ADMISSION_FIELDS}]:
        raise MarketDataError("operations bundle admission decision artifact mismatch")
    dependencies = _read_key_values(sidecars["dependencies"])
    expected_dependencies = {
        "bundle_sha256": identity.get("bundle_sha256"),
        "source_operations_snapshot_identity": identity.get("source_operations_snapshot_identity"),
        "current_operations_snapshot_identity": identity.get("current_operations_snapshot_identity"),
    }
    if dependencies != {key: _csv_value(value) for key, value in expected_dependencies.items()}:
        raise MarketDataError("operations bundle admission dependency artifact mismatch")
    if _read_key_values(sidecars["constraints"]) != {
        key: _csv_value(value) for key, value in sorted(POLICY_FIELDS.items())
    }:
        raise MarketDataError("operations bundle admission constraints mismatch")
    if (operations_bundle is None) != (current_operations_snapshot is None):
        raise ValueError("both currentness validation inputs are required together")
    if operations_bundle is not None and current_operations_snapshot is not None:
        bundle = validate_prospective_evidence_operations_bundle(operations_bundle)
        current = validate_prospective_evidence_operations_snapshot(current_operations_snapshot)
        derived_now = _derive_admission(bundle, current, identity["policy"])
        if derived_now != derived:
            raise MarketDataError("operations bundle admission currentness mismatch")
    return report


def format_operations_bundle_admission(result: OperationsBundleAdmissionResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"admission_sha256: {report['admission_sha256']}",
            f"status: {report['status']}",
            f"bundle_currentness_admitted: {str(report['bundle_currentness_admitted']).lower()}",
            f"bundle_stale: {str(report['bundle_stale']).lower()}",
            f"bundle_sha256: {report['bundle_sha256']}",
            f"source_operations_snapshot_identity: {report['source_operations_snapshot_identity']}",
            f"current_operations_snapshot_identity: {report['current_operations_snapshot_identity']}",
            "state_changed: false",
            "bundle_mutated: false",
            "authoritative_action_authorized: false",
            "network_activity_performed: false",
            "new_samples_counted: 0",
        )
    )


def _derive_admission(
    bundle: Mapping[str, Any], current: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    _validate_bundle_contract(bundle)
    source_identity = bundle.get("source_operations_snapshot_identity")
    current_identity = current.get("snapshot_sha256")
    source_projection = _projection(bundle)
    current_projection = _projection(current)
    projection_match = source_projection == current_projection
    identity_match = source_identity == current_identity
    if identity_match and not projection_match:
        raise MarketDataError("operations bundle admission projection mismatch")
    admitted = bool(identity_match and projection_match)
    return {
        "status": "current_bundle_admitted" if admitted else "blocked_stale_bundle",
        "bundle_currentness_admitted": admitted,
        "bundle_stale": not admitted,
        "bundle_sha256": bundle.get("bundle_sha256"),
        "source_operations_snapshot_identity": source_identity,
        "current_operations_snapshot_identity": current_identity,
        "source_projection": source_projection,
        "current_projection": current_projection,
        "source_projection_sha256": _digest(_canonical(source_projection)),
        "current_projection_sha256": _digest(_canonical(current_projection)),
        **ADMISSION_FLAGS,
    }


def _validate_bundle_contract(bundle: Mapping[str, Any]) -> None:
    for key, expected in FALSE_BUNDLE_FLAGS.items():
        if bundle.get(key) != expected:
            raise MarketDataError(f"operations bundle admission bundle flag:{key}")
    if bundle.get("source_snapshot_replay_valid") is not True:
        raise MarketDataError("operations bundle admission source replay invalid")


def _projection(value: Mapping[str, Any]) -> dict[str, Any]:
    source_value = value.get("source_state")
    source: Mapping[str, Any] = source_value if isinstance(source_value, Mapping) else value
    return {field: source.get(field) for field in SOURCE_STATE_FIELDS}


def _validate_policy(identity: Mapping[str, Any]) -> None:
    if identity.get("policy_id") != POLICY_ID or identity.get("policy") != POLICY_FIELDS:
        raise MarketDataError("operations bundle admission policy mismatch")
    claims = identity.get("claims")
    if claims != ADMISSION_FLAGS:
        raise MarketDataError("operations bundle admission claims mismatch")


def _output_dir(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("operations bundle admission output must be outside source reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "crypto_bot").is_dir():
            return parent
    raise MarketDataError("operations bundle admission repo root missing")


def _admission_row(derived: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": derived["status"],
        "bundle_currentness_admitted": derived["bundle_currentness_admitted"],
        "bundle_stale": derived["bundle_stale"],
        "bundle_sha256": derived.get("bundle_sha256", ""),
        "source_operations_snapshot_identity": derived.get("source_operations_snapshot_identity", ""),
        "current_operations_snapshot_identity": derived.get("current_operations_snapshot_identity", ""),
        "source_projection_sha256": derived["source_projection_sha256"],
        "current_projection_sha256": derived["current_projection_sha256"],
        **ADMISSION_FLAGS,
    }


def _key_value_rows(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": value} for key, value in sorted(values.items())]


def _csv_bytes(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row.get(field)) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _read_csv_exact(content: bytes, fields: Sequence[str]) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
        if tuple(reader.fieldnames or ()) != tuple(fields):
            raise MarketDataError("operations bundle admission CSV schema mismatch")
        return list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise MarketDataError("operations bundle admission CSV read failed") from exc


def _read_key_values(content: bytes) -> dict[str, str]:
    rows = _read_csv_exact(content, KEY_VALUE_FIELDS)
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows):
        raise MarketDataError("operations bundle admission duplicate key")
    return values


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    return str(value)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("operations bundle admission json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("operations bundle admission json shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"operations bundle admission content collision:{path.name}")
        return
    path.write_bytes(content)
