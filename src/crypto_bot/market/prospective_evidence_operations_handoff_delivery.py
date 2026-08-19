from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_bundle import BUNDLE_PREFIX
from crypto_bot.market.prospective_evidence_operations_bundle_admission import (
    PREFIX as ADMISSION_PREFIX,
)
from crypto_bot.market.prospective_evidence_operations_handoff_manifest import (
    PREFIX as MANIFEST_PREFIX,
)
from crypto_bot.market.prospective_evidence_operations_handoff_verification import (
    VERIFICATION_STATUS,
    verify_prospective_evidence_operations_handoff_manifest,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_evidence_operations_handoff_delivery_v1"
CONTRACT_STATUS = "verified_prospective_evidence_operations_handoff_delivery"
DELIVERY_STATUS = "handoff_delivery_ready"
DEFAULT_CONFIG_FILENAME = (
    "config.prospective-evidence-operations-handoff-delivery.example.yaml"
)
PREFIX = "prospective-evidence-operations-handoff-delivery"
INVENTORY_FIELDS = ("ordinal", "logical_relative_path", "artifact_role", "sha256", "byte_count")
KEY_FIELDS = ("key", "value")
POLICY_FIELDS = {
    "schema_version": SCHEMA_VERSION,
    "policy_id": POLICY_ID,
    "consumer_verification_required": True,
    "package_self_contained_required": True,
    "original_bytes_required": True,
    "absolute_paths_prohibited": True,
    "undeclared_payload_files_prohibited": True,
    "external_dependency_fallback_prohibited": True,
    "read_only": True,
    "action_authorization_prohibited": True,
    "state_mutation_prohibited": True,
    "network_activity_prohibited": True,
}
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class OperationsHandoffDeliveryResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_operations_handoff_delivery_config(
    path: str | Path, repo: Path | None = None
) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("operations handoff delivery config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME
        frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("operations handoff delivery config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("operations handoff delivery config policy mismatch")
    if not isinstance(value, dict) or value != POLICY_FIELDS:
        raise MarketDataError("operations handoff delivery config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def build_prospective_evidence_operations_handoff_delivery(
    handoff_manifest: str | Path,
    bundle_admission: str | Path,
    operations_bundle: str | Path,
    config: str | Path,
    output_dir: str | Path,
) -> OperationsHandoffDeliveryResult:
    config_path = Path(config).resolve()
    repo = _repo_root(config_path)
    policy = load_operations_handoff_delivery_config(config_path, repo)
    verification = verify_prospective_evidence_operations_handoff_manifest(
        handoff_manifest, bundle_admission, operations_bundle
    )
    sources = {
        "manifest": Path(handoff_manifest).resolve(),
        "admission": Path(bundle_admission).resolve(),
        "bundle": Path(operations_bundle).resolve(),
    }
    for source in sources.values():
        if not source.is_file():
            raise MarketDataError("operations handoff delivery parent missing")
    file_bytes = _collect_parent_bytes(sources)
    inventory_bytes = _csv_bytes(_inventory_rows(file_bytes), INVENTORY_FIELDS)
    dependencies = {
        "manifest_path": "payload/manifest/" + sources["manifest"].name,
        "admission_path": "payload/admission/" + sources["admission"].name,
        "bundle_path": "payload/bundle/" + sources["bundle"].name,
        "handoff_manifest_identity": verification["handoff_manifest_identity"],
        "bundle_admission_identity": verification["bundle_admission_identity"],
        "bundle_identity": verification["bundle_identity"],
    }
    dependencies_bytes = _csv_bytes(_key_value_rows(dependencies), KEY_FIELDS)
    constraints_bytes = _csv_bytes(_key_value_rows(policy), KEY_FIELDS)
    identity = {
        "delivery_schema_version": SCHEMA_VERSION,
        "delivery_role": "read_only_operations_handoff",
        "policy_id": POLICY_ID,
        "policy": policy,
        "handoff_manifest_identity": verification["handoff_manifest_identity"],
        "bundle_admission_identity": verification["bundle_admission_identity"],
        "operations_bundle_identity": verification["bundle_identity"],
        "source_operations_snapshot_identity": verification["source_snapshot_identity"],
        "consumer_verification_status": VERIFICATION_STATUS,
        "safe_to_consume_read_only": True,
        "delivery_action_authorized": False,
        "state_mutation_authorized": False,
        "network_activity_performed": False,
        "payload_file_count": len(file_bytes),
        "artifacts": {
            "inventory_sha256": _digest(inventory_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
    }
    delivery_sha = _digest(_canonical(identity))
    output_root = _output_root(repo, output_dir)
    # The configured output directory is itself the single delivery-package
    # root. Keeping no additional hash-named directory leaves room for the
    # original content-addressed parent filenames on Windows.
    package_dir = output_root
    payload_root = package_dir / "payload"
    paths = {
        "package": package_dir,
        "inventory": package_dir / f"{PREFIX}.{delivery_sha}.inventory.csv",
        "dependencies": package_dir / f"{PREFIX}.{delivery_sha}.dependencies.csv",
        "constraints": package_dir / f"{PREFIX}.{delivery_sha}.constraints.csv",
        "report": package_dir / f"{PREFIX}.{delivery_sha}.json",
    }
    _materialize_payload(payload_root, file_bytes)
    _commit_bytes(paths["inventory"], inventory_bytes)
    _commit_bytes(paths["dependencies"], dependencies_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_status": CONTRACT_STATUS,
        "delivery_sha256": delivery_sha,
        "delivery_status": DELIVERY_STATUS,
        "identity": identity,
        "handoff_manifest_identity": verification["handoff_manifest_identity"],
        "bundle_admission_identity": verification["bundle_admission_identity"],
        "bundle_identity": verification["bundle_identity"],
        "source_snapshot_identity": verification["source_snapshot_identity"],
        "consumer_verification_status": VERIFICATION_STATUS,
        "safe_to_consume_read_only": True,
        "delivery_action_authorized": False,
        "state_mutation_authorized": False,
        "network_activity_performed": False,
        "artifacts": {
            "inventory": {
                "filename": paths["inventory"].name,
                "sha256": identity["artifacts"]["inventory_sha256"],
                "row_count": len(file_bytes),
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
            "payload": {"directory": "payload"},
        },
    }
    _commit_bytes(paths["report"], _pretty(report))
    validate_prospective_evidence_operations_handoff_delivery(paths["report"])
    return OperationsHandoffDeliveryResult(
        report, {key: str(value) for key, value in paths.items()}
    )


def validate_prospective_evidence_operations_handoff_delivery(
    path: str | Path,
) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _load_json(report_path)
    delivery_sha = report.get("delivery_sha256")
    identity = report.get("identity")
    if (
        not isinstance(delivery_sha, str)
        or not _SHA_RE.fullmatch(delivery_sha)
        or report_path.name != f"{PREFIX}.{delivery_sha}.json"
        or not isinstance(identity, dict)
        or _digest(_canonical(identity)) != delivery_sha
        or report.get("schema_version") != SCHEMA_VERSION
        or report.get("contract_status") != CONTRACT_STATUS
        or report.get("delivery_status") != DELIVERY_STATUS
    ):
        raise MarketDataError("operations handoff delivery identity mismatch")
    _validate_policy(identity)
    if any(report.get(key) != identity.get(key) for key in _PUBLIC_FIELDS):
        raise MarketDataError("operations handoff delivery report mismatch")
    if report.get("source_snapshot_identity") != identity.get(
        "source_operations_snapshot_identity"
    ):
        raise MarketDataError("operations handoff delivery report mismatch")
    if report.get("bundle_identity") != identity.get("operations_bundle_identity"):
        raise MarketDataError("operations handoff delivery report mismatch")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise MarketDataError("operations handoff delivery artifacts missing")
    sidecars = _validate_sidecars(report_path.parent, artifacts, identity.get("artifacts"))
    inventory = _read_csv_exact(sidecars["inventory"], INVENTORY_FIELDS)
    _validate_inventory(inventory, report_path.parent / "payload")
    dependencies = _read_key_values(sidecars["dependencies"])
    expected_dependencies = {
        "manifest_path": _find_payload_report(inventory, "manifest", MANIFEST_PREFIX, identity["handoff_manifest_identity"]),
        "admission_path": _find_payload_report(inventory, "admission", ADMISSION_PREFIX, identity["bundle_admission_identity"]),
        "bundle_path": _find_payload_report(inventory, "bundle", BUNDLE_PREFIX, identity["operations_bundle_identity"]),
        "handoff_manifest_identity": identity["handoff_manifest_identity"],
        "bundle_admission_identity": identity["bundle_admission_identity"],
        "bundle_identity": identity["operations_bundle_identity"],
    }
    if dependencies != {key: _csv_value(value) for key, value in expected_dependencies.items()}:
        raise MarketDataError("operations handoff delivery dependency mismatch")
    if _read_key_values(sidecars["constraints"]) != {
        key: _csv_value(value) for key, value in sorted(POLICY_FIELDS.items())
    }:
        raise MarketDataError("operations handoff delivery constraints mismatch")
    manifest_path = _package_payload_report(report_path.parent, expected_dependencies["manifest_path"])
    admission_path = _package_payload_report(report_path.parent, expected_dependencies["admission_path"])
    bundle_path = _package_payload_report(report_path.parent, expected_dependencies["bundle_path"])
    verification = verify_prospective_evidence_operations_handoff_manifest(
        manifest_path, admission_path, bundle_path
    )
    if any(
        verification.get(key) != identity.get(identity_key)
        for key, identity_key in _VERIFICATION_ID_FIELDS.items()
    ):
        raise MarketDataError("operations handoff delivery verification mismatch")
    return report


def format_operations_handoff_delivery(result: OperationsHandoffDeliveryResult) -> str:
    report = result.report
    return json.dumps(
        {
            "delivery_status": report["delivery_status"],
            "delivery_identity": report["delivery_sha256"],
            "safe_to_consume_read_only": report["safe_to_consume_read_only"],
            "delivery_action_authorized": report["delivery_action_authorized"],
            "state_mutation_authorized": report["state_mutation_authorized"],
            "network_activity_performed": report["network_activity_performed"],
            "handoff_manifest_identity": report["handoff_manifest_identity"],
            "bundle_admission_identity": report["bundle_admission_identity"],
            "bundle_identity": report["bundle_identity"],
            "source_snapshot_identity": report["source_snapshot_identity"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


_PUBLIC_FIELDS = (
    "handoff_manifest_identity",
    "bundle_admission_identity",
    "consumer_verification_status",
    "safe_to_consume_read_only",
    "delivery_action_authorized",
    "state_mutation_authorized",
    "network_activity_performed",
)
_VERIFICATION_ID_FIELDS = {
    "handoff_manifest_identity": "handoff_manifest_identity",
    "bundle_admission_identity": "bundle_admission_identity",
    "bundle_identity": "operations_bundle_identity",
    "source_snapshot_identity": "source_operations_snapshot_identity",
}


def _collect_parent_bytes(sources: Mapping[str, Path]) -> dict[str, bytes]:
    collected: dict[str, bytes] = {}
    for role, report_path in sources.items():
        root = report_path.parent
        native_root = _native_path(root)
        for current, _directories, filenames in os.walk(native_root):
            for filename in filenames:
                native_source = os.path.join(current, filename)
                if os.path.islink(native_source):
                    raise MarketDataError("operations handoff delivery symlink prohibited")
                relative = os.path.relpath(native_source, native_root).replace(os.sep, "/")
                with open(native_source, "rb") as handle:
                    content = handle.read()
                collected[f"payload/{role}/{relative}"] = content
    return dict(sorted(collected.items()))


def _inventory_rows(file_bytes: Mapping[str, bytes]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ordinal, (logical, content) in enumerate(sorted(file_bytes.items()), start=1):
        role = logical.split("/", 2)[1] + ("_report" if logical.endswith(".json") else "_artifact")
        rows.append(
            {
                "ordinal": ordinal,
                "logical_relative_path": logical,
                "artifact_role": role,
                "sha256": _digest(content),
                "byte_count": len(content),
            }
        )
    return rows


def _materialize_payload(payload: Path, file_bytes: Mapping[str, bytes]) -> None:
    for logical, content in file_bytes.items():
        _commit_bytes(_safe_payload_path(payload.parent, logical), content)
    _validate_inventory(_inventory_rows(file_bytes), payload)


def _validate_inventory(rows: Sequence[Mapping[str, str]], payload: Path) -> None:
    if not rows or [int(row["ordinal"]) for row in rows] != list(range(1, len(rows) + 1)):
        raise MarketDataError("operations handoff delivery inventory ordinal mismatch")
    expected: set[str] = set()
    for row in rows:
        logical = row.get("logical_relative_path", "")
        if not _safe_logical(logical) or logical in expected:
            raise MarketDataError("operations handoff delivery inventory path mismatch")
        if not _SHA_RE.fullmatch(row.get("sha256", "")):
            raise MarketDataError("operations handoff delivery inventory hash mismatch")
        if int(row.get("byte_count", "-1")) < 0:
            raise MarketDataError("operations handoff delivery inventory byte count mismatch")
        expected.add(logical)
    actual_rel, symlinked = _payload_file_logicals(payload)
    actual = {f"payload/{logical}" for logical in actual_rel}
    if symlinked:
        raise MarketDataError("operations handoff delivery symlink prohibited")
    if actual != expected:
        raise MarketDataError("operations handoff delivery undeclared payload file")
    for row in rows:
        content = _read_native(_safe_payload_path(payload.parent, row["logical_relative_path"]))
        if _digest(content) != row["sha256"] or len(content) != int(row["byte_count"]):
            raise MarketDataError("operations handoff delivery payload hash mismatch")


def _find_payload_report(
    rows: Sequence[Mapping[str, str]], role: str, prefix: str, identity: str
) -> str:
    expected = [
        row["logical_relative_path"]
        for row in rows
        if row["logical_relative_path"].startswith(f"payload/{role}/")
        and row["logical_relative_path"].endswith(f"{prefix}.{identity}.json")
    ]
    if len(expected) != 1:
        raise MarketDataError("operations handoff delivery bound report missing")
    return expected[0]


def _package_payload_report(package_dir: Path, logical: str) -> Path:
    if not logical.startswith("payload/") or not _safe_logical(logical):
        raise MarketDataError("operations handoff delivery bound path mismatch")
    return _safe_payload_path(package_dir, logical)


def _payload_file_logicals(root: Path) -> tuple[set[str], bool]:
    native_root = _native_path(root)
    if not os.path.isdir(native_root):
        raise MarketDataError("operations handoff delivery payload missing")
    logicals: set[str] = set()
    symlinked = False
    for current, _directories, filenames in os.walk(native_root):
        for filename in filenames:
            native_path = os.path.join(current, filename)
            symlinked = symlinked or os.path.islink(native_path)
            relative = os.path.relpath(native_path, native_root).replace(os.sep, "/")
            logicals.add(relative)
    return logicals, symlinked


def _read_native(path: Path) -> bytes:
    with open(_native_path(path), "rb") as handle:
        return handle.read()


def _validate_sidecars(
    package_dir: Path, artifacts: Mapping[str, Any], identity_artifacts: Any
) -> dict[str, bytes]:
    if not isinstance(identity_artifacts, Mapping):
        raise MarketDataError("operations handoff delivery identity artifacts missing")
    result: dict[str, bytes] = {}
    for key in ("inventory", "dependencies", "constraints"):
        info = artifacts.get(key)
        expected_hash = identity_artifacts.get(f"{key}_sha256")
        if not isinstance(info, Mapping) or info.get("sha256") != expected_hash:
            raise MarketDataError(f"operations handoff delivery artifact metadata:{key}")
        filename = info.get("filename")
        expected = package_dir / str(filename)
        if not isinstance(filename, str) or Path(filename).name != filename or not expected.is_file():
            raise MarketDataError(f"operations handoff delivery artifact missing:{key}")
        if key != "inventory" and info.get("row_count") != {
            "dependencies": 6,
            "constraints": len(POLICY_FIELDS),
        }[key]:
            raise MarketDataError(f"operations handoff delivery artifact metadata:{key}")
        content = expected.read_bytes()
        if _digest(content) != expected_hash:
            raise MarketDataError(f"operations handoff delivery artifact bytes:{key}")
        result[key] = content
    payload = artifacts.get("payload")
    if not isinstance(payload, Mapping) or payload.get("directory") != "payload":
        raise MarketDataError("operations handoff delivery payload metadata mismatch")
    if not (package_dir / "payload").is_dir():
        raise MarketDataError("operations handoff delivery payload missing")
    return result


def _validate_policy(identity: Mapping[str, Any]) -> None:
    if identity.get("policy_id") != POLICY_ID or identity.get("policy") != POLICY_FIELDS:
        raise MarketDataError("operations handoff delivery policy mismatch")
    for key, expected in {
        "safe_to_consume_read_only": True,
        "delivery_action_authorized": False,
        "state_mutation_authorized": False,
        "network_activity_performed": False,
        "consumer_verification_status": VERIFICATION_STATUS,
    }.items():
        if identity.get(key) != expected:
            raise MarketDataError("operations handoff delivery authorization mismatch")


def _output_root(repo: Path, output_dir: str | Path) -> Path:
    output = (
        (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    )
    if output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("operations handoff delivery output must be outside source reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "crypto_bot").is_dir():
            return parent
    raise MarketDataError("operations handoff delivery repo root missing")


def _safe_logical(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return False
    pure = PurePosixPath(value)
    return ".." not in pure.parts and "." not in pure.parts and pure.as_posix() == value


def _safe_payload_path(root: Path, logical: str) -> Path:
    if not _safe_logical(logical):
        raise MarketDataError("operations handoff delivery unsafe payload path")
    path = (root / Path(*logical.split("/"))).resolve()
    if not path.is_relative_to(root.resolve()):
        raise MarketDataError("operations handoff delivery payload path escape")
    return path


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
            raise MarketDataError("operations handoff delivery CSV schema mismatch")
        return list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise MarketDataError("operations handoff delivery CSV read failed") from exc


def _read_key_values(content: bytes) -> dict[str, str]:
    rows = _read_csv_exact(content, KEY_FIELDS)
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows):
        raise MarketDataError("operations handoff delivery duplicate key")
    return values


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    return str(value)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("operations handoff delivery json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("operations handoff delivery json shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    native = _native_path(path)
    if os.path.lexists(native):
        if os.path.islink(native) or not os.path.isfile(native) or _read_native(path) != content:
            raise MarketDataError(f"operations handoff delivery content collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(
        dir=str(path.parent), prefix=".operations-delivery-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(_native_path(temporary), native)
    finally:
        temporary.unlink(missing_ok=True)


def _native_path(path: Path) -> str:
    native = os.path.abspath(str(path))
    if os.name == "nt" and not native.startswith("\\\\?\\"):
        return "\\\\?\\" + native
    return native
