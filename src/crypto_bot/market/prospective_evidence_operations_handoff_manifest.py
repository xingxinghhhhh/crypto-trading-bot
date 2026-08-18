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
from crypto_bot.market.prospective_evidence_operations_bundle import (
    SOURCE_STATE_FIELDS,
    validate_prospective_evidence_operations_bundle,
)
from crypto_bot.market.prospective_evidence_operations_bundle_admission import (
    validate_prospective_evidence_operations_bundle_admission,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_evidence_operations_handoff_manifest_v1"
CONTRACT_STATUS = "verified_prospective_evidence_operations_handoff_manifest"
DEFAULT_CONFIG_FILENAME = (
    "config.prospective-evidence-operations-handoff-manifest.example.yaml"
)
PREFIX = "prospective-evidence-operations-handoff-manifest"
DISPLAY_FIELDS = SOURCE_STATE_FIELDS[:11]
STATUS_FIELDS = (
    "manifest_schema_version",
    "status",
    "bundle_identity",
    "bundle_currentness_admission_identity",
    "source_operations_snapshot_identity",
    "governed_stage",
    "blocking_gate",
    "next_legal_action",
    "current_samples",
    "sample_threshold",
    "remaining_samples",
    "append_authorization_ready",
    "economic_authorized",
    "pnl_authorized",
    "paper_authorized",
    "live_authorized",
    "handoff_manifest_materialized",
    "consumer_readable",
    "consumer_action_authorized",
    "state_mutation_authorized",
)
KEY_FIELDS = ("key", "value")
POLICY_FIELDS = {
    "schema_version": SCHEMA_VERSION,
    "policy_id": POLICY_ID,
    "current_bundle_admission_required": True,
    "stale_bundle_consumption_prohibited": True,
    "source_projection_passthrough_only": True,
    "business_state_recomputation_prohibited": True,
    "read_only": True,
    "consumer_action_authorization_prohibited": True,
    "state_mutation_prohibited": True,
    "network_activity_prohibited": True,
}
CONSUMER_FLAGS = {
    "consumer_action_authorized": False,
    "state_mutation_authorized": False,
}


@dataclass(frozen=True)
class OperationsHandoffManifestResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_operations_handoff_manifest_config(
    path: str | Path, repo: Path | None = None
) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("operations handoff manifest config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME
        frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("operations handoff manifest config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("operations handoff manifest config policy mismatch")
    if not isinstance(value, dict) or value != POLICY_FIELDS:
        raise MarketDataError("operations handoff manifest config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def build_prospective_evidence_operations_handoff_manifest(
    bundle_admission: str | Path,
    operations_bundle: str | Path,
    config: str | Path,
    output_dir: str | Path,
) -> OperationsHandoffManifestResult:
    admission_path = Path(bundle_admission).resolve()
    bundle_path = Path(operations_bundle).resolve()
    repo = _repo_root(Path(config).resolve())
    policy = load_operations_handoff_manifest_config(config, repo)
    admission = validate_prospective_evidence_operations_bundle_admission(admission_path)
    bundle = validate_prospective_evidence_operations_bundle(bundle_path)
    derived = _derive_manifest(admission, bundle)
    status_bytes = _csv_bytes(
        [_status_row(derived)] if derived["status"] == "handoff_manifest_ready" else [],
        STATUS_FIELDS,
    )
    dependencies = {
        "bundle_sha256": bundle["bundle_sha256"],
        "bundle_currentness_admission_identity": admission["admission_sha256"],
        "source_operations_snapshot_identity": bundle[
            "source_operations_snapshot_identity"
        ],
    }
    dependencies_bytes = _csv_bytes(_key_value_rows(dependencies), KEY_FIELDS)
    constraints_bytes = _csv_bytes(_key_value_rows(policy), KEY_FIELDS)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "bundle_sha256": bundle["bundle_sha256"],
        "bundle_currentness_admission_identity": admission["admission_sha256"],
        "source_operations_snapshot_identity": bundle[
            "source_operations_snapshot_identity"
        ],
        "status": derived["status"],
        "manifest_state": derived["manifest_state"],
        "policy": policy,
        "claims": {
            "handoff_manifest_materialized": derived["handoff_manifest_materialized"],
            "consumer_readable": derived["consumer_readable"],
            **CONSUMER_FLAGS,
        },
        "artifacts": {
            "status_sha256": _digest(status_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    manifest_sha = _digest(_canonical(identity))
    output = _output_dir(repo, output_dir)
    stem = f"{PREFIX}.{manifest_sha}"
    paths = {
        "status": output / f"{stem}.status.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_status": CONTRACT_STATUS,
        "manifest_sha256": manifest_sha,
        **derived,
        "identity": identity,
        "artifacts": {
            "status": {
                "filename": paths["status"].name,
                "sha256": identity["artifacts"]["status_sha256"],
                "row_count": 1 if derived["status"] == "handoff_manifest_ready" else 0,
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
    _commit_bytes(paths["status"], status_bytes)
    _commit_bytes(paths["dependencies"], dependencies_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    _commit_bytes(paths["report"], _pretty(report))
    validate_prospective_evidence_operations_handoff_manifest(
        paths["report"], admission_path, bundle_path
    )
    return OperationsHandoffManifestResult(
        report, {key: str(value) for key, value in paths.items()}
    )


def validate_prospective_evidence_operations_handoff_manifest(
    path: str | Path,
    bundle_admission: str | Path | None = None,
    operations_bundle: str | Path | None = None,
) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _load_json(report_path)
    manifest_sha = report.get("manifest_sha256")
    identity = report.get("identity")
    if (
        not isinstance(manifest_sha, str)
        or report_path.name != f"{PREFIX}.{manifest_sha}.json"
        or not isinstance(identity, dict)
        or _digest(_canonical(identity)) != manifest_sha
        or report.get("schema_version") != SCHEMA_VERSION
        or report.get("contract_status") != CONTRACT_STATUS
    ):
        raise MarketDataError("operations handoff manifest identity mismatch")
    _validate_policy(identity)
    if bundle_admission is None or operations_bundle is None:
        raise ValueError("bundle admission and operations bundle inputs are required")
    admission = _load_and_validate_admission(bundle_admission, identity)
    bundle_path = Path(operations_bundle).resolve() if operations_bundle is not None else None
    bundle = _load_and_validate_bundle(identity, bundle_path)
    derived = _derive_manifest(admission, bundle)
    if any(report.get(key) != value for key, value in derived.items()):
        raise MarketDataError("operations handoff manifest projection mismatch")
    expected_identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "bundle_sha256": bundle["bundle_sha256"],
        "bundle_currentness_admission_identity": admission["admission_sha256"],
        "source_operations_snapshot_identity": bundle[
            "source_operations_snapshot_identity"
        ],
        "status": derived["status"],
        "manifest_state": derived["manifest_state"],
        "policy": identity["policy"],
        "claims": {
            "handoff_manifest_materialized": derived["handoff_manifest_materialized"],
            "consumer_readable": derived["consumer_readable"],
            **CONSUMER_FLAGS,
        },
    }
    artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MarketDataError("operations handoff manifest artifacts missing")
    sidecars = _validate_sidecars(report_path, report, artifacts)
    expected_identity["artifacts"] = {
        "status_sha256": _digest(sidecars["status"]),
        "dependencies_sha256": _digest(sidecars["dependencies"]),
        "constraints_sha256": _digest(sidecars["constraints"]),
    }
    if identity != expected_identity:
        raise MarketDataError("operations handoff manifest identity mismatch")
    status_rows = _read_csv_exact(sidecars["status"], STATUS_FIELDS)
    expected_rows = (
        [{field: _csv_value(_status_row(derived)[field]) for field in STATUS_FIELDS}]
        if derived["status"] == "handoff_manifest_ready"
        else []
    )
    if status_rows != expected_rows:
        raise MarketDataError("operations handoff manifest status artifact mismatch")
    dependencies = _read_key_values(sidecars["dependencies"])
    expected_dependencies = {
        "bundle_sha256": bundle["bundle_sha256"],
        "bundle_currentness_admission_identity": admission["admission_sha256"],
        "source_operations_snapshot_identity": bundle[
            "source_operations_snapshot_identity"
        ],
    }
    if dependencies != {key: _csv_value(value) for key, value in expected_dependencies.items()}:
        raise MarketDataError("operations handoff manifest dependency artifact mismatch")
    if _read_key_values(sidecars["constraints"]) != {
        key: _csv_value(value) for key, value in sorted(POLICY_FIELDS.items())
    }:
        raise MarketDataError("operations handoff manifest constraints mismatch")
    if bundle_path is not None and bundle["bundle_sha256"] != identity["bundle_sha256"]:
        raise MarketDataError("operations handoff manifest bundle identity mismatch")
    return report


def format_operations_handoff_manifest(result: OperationsHandoffManifestResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"manifest_sha256: {report['manifest_sha256']}",
            f"status: {report['status']}",
            f"bundle_sha256: {report['bundle_sha256']}",
            f"bundle_currentness_admission_identity: {report['bundle_currentness_admission_identity']}",
            f"source_operations_snapshot_identity: {report['source_operations_snapshot_identity']}",
            f"consumer_readable: {str(report['consumer_readable']).lower()}",
            f"consumer_action_authorized: {str(report['consumer_action_authorized']).lower()}",
            f"state_mutation_authorized: {str(report['state_mutation_authorized']).lower()}",
        )
    )


def _derive_manifest(
    admission: Mapping[str, Any], bundle: Mapping[str, Any]
) -> dict[str, Any]:
    bundle_identity = bundle.get("bundle_sha256")
    admission_bundle = admission.get("bundle_sha256")
    source_identity = bundle.get("source_operations_snapshot_identity")
    if admission_bundle != bundle_identity:
        raise MarketDataError("operations handoff manifest bundle identity mismatch")
    if admission.get("source_operations_snapshot_identity") != source_identity:
        raise MarketDataError("operations handoff manifest source identity mismatch")
    admission_projection = admission.get("source_projection")
    source_state_value = bundle.get("source_state")
    if not isinstance(source_state_value, Mapping):
        bundle_identity_value = bundle.get("identity")
        source_state_value = (
            bundle_identity_value.get("source_state")
            if isinstance(bundle_identity_value, Mapping)
            else None
        )
    source_state = source_state_value
    if not isinstance(admission_projection, Mapping) or not isinstance(source_state, Mapping):
        raise MarketDataError("operations handoff manifest source projection missing")
    if any(admission_projection.get(field) != source_state.get(field) for field in SOURCE_STATE_FIELDS):
        raise MarketDataError("operations handoff manifest source projection mismatch")
    status = admission.get("status")
    admitted = (
        status == "current_bundle_admitted"
        and admission.get("bundle_currentness_admitted") is True
        and admission.get("bundle_stale") is False
    )
    if not admitted and status != "blocked_stale_bundle":
        raise MarketDataError("operations handoff manifest admission status invalid")
    base: dict[str, Any] = {
        "bundle_sha256": bundle_identity,
        "bundle_currentness_admission_identity": admission.get("admission_sha256"),
        "source_operations_snapshot_identity": source_identity,
        "consumer_action_authorized": False,
        "state_mutation_authorized": False,
    }
    if not admitted:
        return {
            **base,
            "status": "blocked_bundle_not_current",
            "handoff_manifest_materialized": False,
            "consumer_readable": False,
            "manifest_state": None,
            **{field: None for field in DISPLAY_FIELDS},
        }
    state = {field: source_state.get(field) for field in DISPLAY_FIELDS}
    return {
        **base,
        "status": "handoff_manifest_ready",
        "handoff_manifest_materialized": True,
        "consumer_readable": True,
        "manifest_state": state,
        **state,
    }


def _status_row(derived: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "manifest_schema_version": SCHEMA_VERSION,
        "status": derived["status"],
        "bundle_identity": derived["bundle_sha256"],
        "bundle_currentness_admission_identity": derived[
            "bundle_currentness_admission_identity"
        ],
        "source_operations_snapshot_identity": derived[
            "source_operations_snapshot_identity"
        ],
        **{field: derived.get(field) for field in DISPLAY_FIELDS},
        "handoff_manifest_materialized": derived["handoff_manifest_materialized"],
        "consumer_readable": derived["consumer_readable"],
        "consumer_action_authorized": derived["consumer_action_authorized"],
        "state_mutation_authorized": derived["state_mutation_authorized"],
    }


def _load_and_validate_admission(
    admission_path: str | Path, identity: Mapping[str, Any]
) -> dict[str, Any]:
    admission = validate_prospective_evidence_operations_bundle_admission(admission_path)
    if admission.get("admission_sha256") != identity.get(
        "bundle_currentness_admission_identity"
    ):
        raise MarketDataError("operations handoff manifest admission identity mismatch")
    return admission


def _load_and_validate_bundle(
    identity: Mapping[str, Any], bundle_path: Path | None
) -> dict[str, Any]:
    if bundle_path is None:
        raise ValueError("operations bundle input is required")
    bundle = validate_prospective_evidence_operations_bundle(bundle_path)
    if bundle.get("bundle_sha256") != identity.get("bundle_sha256"):
        raise MarketDataError("operations handoff manifest bundle identity mismatch")
    return bundle


def _validate_sidecars(
    report_path: Path, report: Mapping[str, Any], identity_artifacts: Mapping[str, Any]
) -> dict[str, bytes]:
    sidecars: dict[str, bytes] = {}
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise MarketDataError("operations handoff manifest artifacts missing")
    for key in ("status", "dependencies", "constraints"):
        info = artifacts.get(key)
        expected_hash = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(info, Mapping) or info.get("sha256") != expected_hash:
            raise MarketDataError(f"operations handoff manifest artifact metadata:{key}")
        expected_rows = {
            "status": 1 if report.get("status") == "handoff_manifest_ready" else 0,
            "dependencies": 3,
            "constraints": len(POLICY_FIELDS),
        }[key]
        if info.get("row_count") != expected_rows:
            raise MarketDataError(f"operations handoff manifest artifact metadata:{key}")
        filename = info.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise MarketDataError(f"operations handoff manifest artifact filename:{key}")
        artifact_path = report_path.parent / filename
        if not artifact_path.is_file():
            raise MarketDataError(f"operations handoff manifest artifact missing:{key}")
        content = artifact_path.read_bytes()
        if _digest(content) != expected_hash:
            raise MarketDataError(f"operations handoff manifest artifact bytes:{key}")
        sidecars[key] = content
    return sidecars


def _validate_policy(identity: Mapping[str, Any]) -> None:
    if identity.get("policy_id") != POLICY_ID or identity.get("policy") != POLICY_FIELDS:
        raise MarketDataError("operations handoff manifest policy mismatch")
    claims = identity.get("claims")
    if not isinstance(claims, Mapping) or claims.get("consumer_action_authorized") is not False:
        raise MarketDataError("operations handoff manifest authorization claims mismatch")
    if claims.get("state_mutation_authorized") is not False:
        raise MarketDataError("operations handoff manifest mutation claims mismatch")


def _output_dir(repo: Path, output_dir: str | Path) -> Path:
    output = (
        (repo / output_dir).resolve()
        if not Path(output_dir).is_absolute()
        else Path(output_dir).resolve()
    )
    if output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("operations handoff manifest output must be outside source reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "crypto_bot").is_dir():
            return parent
    raise MarketDataError("operations handoff manifest repo root missing")


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
            raise MarketDataError("operations handoff manifest CSV schema mismatch")
        return list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise MarketDataError("operations handoff manifest CSV read failed") from exc


def _read_key_values(content: bytes) -> dict[str, str]:
    rows = _read_csv_exact(content, KEY_FIELDS)
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows):
        raise MarketDataError("operations handoff manifest duplicate key")
    return values


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    return str(value)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("operations handoff manifest json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("operations handoff manifest json shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    native = os.path.abspath(str(path))
    if os.path.lexists(native):
        if path.is_symlink() or path.read_bytes() != content:
            raise MarketDataError(f"operations handoff manifest content collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(
        dir=str(path.parent), prefix=".operations-handoff-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(str(temporary), native)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
