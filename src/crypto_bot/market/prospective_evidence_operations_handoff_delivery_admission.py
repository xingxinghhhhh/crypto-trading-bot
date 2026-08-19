from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_bundle import SOURCE_STATE_FIELDS
from crypto_bot.market.prospective_evidence_operations_handoff_delivery import (
    ValidatedHandoffDeliveryInspection,
    inspect_prospective_evidence_operations_handoff_delivery,
)
from crypto_bot.market.prospective_evidence_operations_snapshot import (
    validate_prospective_evidence_operations_snapshot,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_evidence_operations_handoff_delivery_admission_v1"
CONTRACT_STATUS = (
    "verified_prospective_evidence_operations_handoff_delivery_admission"
)
DEFAULT_CONFIG_FILENAME = (
    "config.prospective-evidence-operations-handoff-delivery-admission.example.yaml"
)
PREFIX = "prospective-evidence-operations-handoff-delivery-admission"
ADMISSION_FIELDS = (
    "delivery_identity",
    "delivery_source_snapshot_identity",
    "current_snapshot_identity",
    "source_identity_match",
    "source_projection_match",
    "delivery_stale",
    "delivery_currentness_admitted",
    "safe_to_consume_current_read_only",
    "status",
)
KEY_FIELDS = ("key", "value")
POLICY_FIELDS = {
    "schema_version": SCHEMA_VERSION,
    "policy_id": POLICY_ID,
    "valid_delivery_required": True,
    "exact_source_snapshot_identity_required": True,
    "source_projection_match_required": True,
    "stale_delivery_current_consumption_prohibited": True,
    "automatic_delivery_refresh_prohibited": True,
    "manual_currentness_override_prohibited": True,
    "read_only": True,
    "action_authorization_prohibited": True,
    "state_mutation_prohibited": True,
    "network_activity_prohibited": True,
}
ADMISSION_FLAGS = {
    "delivery_action_authorized": False,
    "state_mutation_authorized": False,
    "state_changed": False,
    "network_activity_performed": False,
    "new_samples_counted": 0,
}


@dataclass(frozen=True)
class OperationsHandoffDeliveryAdmissionResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_operations_handoff_delivery_admission_config(
    path: str | Path, repo: Path | None = None
) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("operations handoff delivery admission config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME
        frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("operations handoff delivery admission config read failed") from exc
    if value != frozen or (
        repo is not None and not config_path.is_relative_to(repo.resolve())
    ):
        raise MarketDataError("operations handoff delivery admission config policy mismatch")
    if not isinstance(value, dict) or value != POLICY_FIELDS:
        raise MarketDataError("operations handoff delivery admission config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def freeze_prospective_evidence_operations_handoff_delivery_admission(
    handoff_delivery: str | Path,
    current_operations_snapshot: str | Path,
    config: str | Path,
    output_dir: str | Path,
) -> OperationsHandoffDeliveryAdmissionResult:
    config_path = Path(config).resolve()
    repo = _repo_root(config_path)
    policy = load_operations_handoff_delivery_admission_config(config_path, repo)
    delivery_path = Path(handoff_delivery).resolve()
    current_path = Path(current_operations_snapshot).resolve()
    inspection = inspect_prospective_evidence_operations_handoff_delivery(
        delivery_path
    )
    current = validate_prospective_evidence_operations_snapshot(current_path)
    derived = _derive_admission(inspection, current)
    output = _output_dir(repo, output_dir, delivery_path)
    admission_bytes = _csv_bytes([_admission_row(derived)], ADMISSION_FIELDS)
    dependencies = {
        "delivery_identity": derived["delivery_identity"],
        "delivery_source_snapshot_identity": derived[
            "delivery_source_snapshot_identity"
        ],
        "current_snapshot_identity": derived["current_snapshot_identity"],
        "source_projection_sha256": derived["source_projection_sha256"],
        "current_projection_sha256": derived["current_projection_sha256"],
    }
    dependencies_bytes = _csv_bytes(_key_value_rows(dependencies), KEY_FIELDS)
    constraints_bytes = _csv_bytes(_key_value_rows(policy), KEY_FIELDS)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        **{key: derived[key] for key in _IDENTITY_DECISION_FIELDS},
        "source_projection_sha256": derived["source_projection_sha256"],
        "current_projection_sha256": derived["current_projection_sha256"],
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
        **derived,
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
        },
    }
    _commit_bytes(paths["admission"], admission_bytes)
    _commit_bytes(paths["dependencies"], dependencies_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    _commit_bytes(paths["report"], _pretty(report))
    validate_prospective_evidence_operations_handoff_delivery_admission(
        paths["report"], delivery_path, current_path
    )
    return OperationsHandoffDeliveryAdmissionResult(
        report, {key: str(value) for key, value in paths.items()}
    )


def validate_prospective_evidence_operations_handoff_delivery_admission(
    path: str | Path,
    handoff_delivery: str | Path | None = None,
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
        raise MarketDataError("operations handoff delivery admission identity mismatch")
    _validate_policy(identity)
    claims = identity.get("claims")
    if claims != ADMISSION_FLAGS:
        raise MarketDataError("operations handoff delivery admission claims mismatch")
    derived = identity.get("derived")
    if not isinstance(derived, dict):
        raise MarketDataError("operations handoff delivery admission derived state missing")
    if any(report.get(key) != value for key, value in derived.items()):
        raise MarketDataError("operations handoff delivery admission report mismatch")
    if any(identity.get(key) != derived.get(key) for key in _IDENTITY_DECISION_FIELDS):
        raise MarketDataError("operations handoff delivery admission identity mismatch")
    if report.get("identity") != identity:
        raise MarketDataError("operations handoff delivery admission identity mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("operations handoff delivery admission artifacts missing")
    sidecars: dict[str, bytes] = {}
    for key in ("admission", "dependencies", "constraints"):
        info = artifacts.get(key)
        expected_hash = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(info, dict) or info.get("sha256") != expected_hash:
            raise MarketDataError(
                f"operations handoff delivery admission artifact metadata:{key}"
            )
        filename = info.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise MarketDataError(
                f"operations handoff delivery admission artifact filename:{key}"
            )
        artifact_path = report_path.parent / filename
        if not artifact_path.is_file():
            raise MarketDataError(
                f"operations handoff delivery admission artifact missing:{key}"
            )
        content = artifact_path.read_bytes()
        if _digest(content) != expected_hash:
            raise MarketDataError(
                f"operations handoff delivery admission artifact bytes:{key}"
            )
        sidecars[key] = content
    admission_rows = _read_csv_exact(sidecars["admission"], ADMISSION_FIELDS)
    if admission_rows != [
        {field: _csv_value(_admission_row(derived)[field]) for field in ADMISSION_FIELDS}
    ]:
        raise MarketDataError("operations handoff delivery admission decision mismatch")
    dependencies = _read_key_values(sidecars["dependencies"])
    expected_dependencies = {
        "delivery_identity": derived["delivery_identity"],
        "delivery_source_snapshot_identity": derived[
            "delivery_source_snapshot_identity"
        ],
        "current_snapshot_identity": derived["current_snapshot_identity"],
        "source_projection_sha256": derived["source_projection_sha256"],
        "current_projection_sha256": derived["current_projection_sha256"],
    }
    if dependencies != {key: _csv_value(value) for key, value in expected_dependencies.items()}:
        raise MarketDataError("operations handoff delivery admission dependency mismatch")
    if _read_key_values(sidecars["constraints"]) != {
        key: _csv_value(value) for key, value in sorted(POLICY_FIELDS.items())
    }:
        raise MarketDataError("operations handoff delivery admission constraints mismatch")
    if (handoff_delivery is None) != (current_operations_snapshot is None):
        raise ValueError("both currentness validation inputs are required together")
    if handoff_delivery is None or current_operations_snapshot is None:
        raise ValueError("delivery and current operations snapshot inputs are required")
    inspection = inspect_prospective_evidence_operations_handoff_delivery(
        handoff_delivery
    )
    current = validate_prospective_evidence_operations_snapshot(
        current_operations_snapshot
    )
    derived_now = _derive_admission(inspection, current)
    if derived_now != derived:
        raise MarketDataError("operations handoff delivery admission currentness mismatch")
    return report


def format_operations_handoff_delivery_admission(
    result: OperationsHandoffDeliveryAdmissionResult,
) -> str:
    report = result.report
    return json.dumps(
        {
            "status": report["status"],
            "admission_identity": report["admission_sha256"],
            "delivery_identity": report["delivery_identity"],
            "delivery_source_snapshot_identity": report[
                "delivery_source_snapshot_identity"
            ],
            "current_snapshot_identity": report["current_snapshot_identity"],
            "source_identity_match": report["source_identity_match"],
            "source_projection_match": report["source_projection_match"],
            "delivery_stale": report["delivery_stale"],
            "delivery_currentness_admitted": report[
                "delivery_currentness_admitted"
            ],
            "safe_to_consume_current_read_only": report[
                "safe_to_consume_current_read_only"
            ],
            "delivery_action_authorized": report["delivery_action_authorized"],
            "state_mutation_authorized": report["state_mutation_authorized"],
            "network_activity_performed": report["network_activity_performed"],
            "new_samples_counted": report["new_samples_counted"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


_IDENTITY_DECISION_FIELDS = (
    "delivery_identity",
    "delivery_source_snapshot_identity",
    "current_snapshot_identity",
    "source_identity_match",
    "source_projection_match",
    "source_projection_sha256",
    "current_projection_sha256",
    "delivery_stale",
    "delivery_currentness_admitted",
    "safe_to_consume_current_read_only",
)


def _derive_admission(
    inspection: ValidatedHandoffDeliveryInspection,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    source_identity = inspection.source_operations_snapshot_identity
    current_identity = current.get("snapshot_sha256")
    if not isinstance(current_identity, str):
        raise MarketDataError("operations snapshot identity missing")
    source_projection = inspection.source_projection
    current_projection = _projection(current)
    source_identity_match = source_identity == current_identity
    source_projection_match = source_projection == current_projection
    if source_identity_match and not source_projection_match:
        raise MarketDataError("operations handoff delivery admission projection mismatch")
    admitted = source_identity_match and source_projection_match
    return {
        "status": "current_delivery_admitted" if admitted else "blocked_stale_delivery",
        "delivery_currentness_admitted": admitted,
        "delivery_stale": not admitted,
        "safe_to_consume_current_read_only": admitted,
        "delivery_identity": inspection.delivery_identity,
        "delivery_source_snapshot_identity": source_identity,
        "current_snapshot_identity": current_identity,
        "source_identity_match": source_identity_match,
        "source_projection_match": source_projection_match,
        "source_projection_sha256": _digest(_canonical(source_projection)),
        "current_projection_sha256": _digest(_canonical(current_projection)),
        **ADMISSION_FLAGS,
    }


def _projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return {field: value.get(field) for field in SOURCE_STATE_FIELDS}


def _admission_row(derived: Mapping[str, Any]) -> dict[str, Any]:
    return {field: derived[field] for field in ADMISSION_FIELDS}


def _validate_policy(identity: Mapping[str, Any]) -> None:
    if identity.get("policy_id") != POLICY_ID or identity.get("policy") != POLICY_FIELDS:
        raise MarketDataError("operations handoff delivery admission policy mismatch")


def _output_dir(repo: Path, output_dir: str | Path, delivery_path: Path) -> Path:
    output = (
        (repo / output_dir).resolve()
        if not Path(output_dir).is_absolute()
        else Path(output_dir).resolve()
    )
    if output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("operations handoff delivery admission output must be outside reports")
    if output.is_relative_to(delivery_path.parent):
        raise ValueError("operations handoff delivery admission output must not modify delivery package")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "crypto_bot").is_dir():
            return parent
    raise MarketDataError("operations handoff delivery admission repo root missing")


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
            raise MarketDataError("operations handoff delivery admission CSV schema mismatch")
        return list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise MarketDataError("operations handoff delivery admission CSV read failed") from exc


def _key_value_rows(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": value} for key, value in sorted(values.items())]


def _read_key_values(content: bytes) -> dict[str, str]:
    rows = _read_csv_exact(content, KEY_FIELDS)
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows):
        raise MarketDataError("operations handoff delivery admission duplicate key")
    return values


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    return str(value)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("operations handoff delivery admission json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("operations handoff delivery admission json shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(
                f"operations handoff delivery admission content collision:{path.name}"
            )
        return
    with tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        prefix=".operations-handoff-delivery-admission-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        temporary.replace(path)
    except FileExistsError:
        if path.read_bytes() != content:
            raise MarketDataError(
                f"operations handoff delivery admission content collision:{path.name}"
            )
        temporary.unlink(missing_ok=True)
