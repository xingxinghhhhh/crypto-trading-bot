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
from typing import Any, Iterable, Mapping, Sequence
from unittest.mock import patch

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_snapshot import (
    validate_prospective_evidence_operations_snapshot,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_evidence_operations_bundle_v1"
CONTRACT_STATUS = "verified_prospective_evidence_operations_bundle"
DEFAULT_CONFIG_FILENAME = "config.prospective-evidence-operations-bundle.example.yaml"
BUNDLE_PREFIX = "prospective-evidence-operations-bundle"
REPLAY_PYPROJECT = "[tool.crypto_bot_replay]\nportable = true\n"
INVENTORY_FIELDS = (
    "ordinal",
    "artifact_role",
    "logical_name",
    "source_identity",
    "sha256",
    "byte_count",
)
DEPENDENCY_FIELDS = (
    "ordinal",
    "from_logical_name",
    "to_logical_name",
    "dependency_kind",
)
KEY_FIELDS = ("key", "value")
SOURCE_STATE_FIELDS = (
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
    "state_changed",
    "network_activity_performed",
    "new_samples_counted",
)
FALSE_BUNDLE_FLAGS = {
    "bundle_authorizes_capture": False,
    "bundle_authorizes_append": False,
    "bundle_authorizes_sample_credit": False,
    "bundle_authorizes_economic": False,
    "bundle_authorizes_pnl": False,
    "bundle_authorizes_paper": False,
    "bundle_authorizes_live": False,
    "state_mutation_authorized": False,
    "network_activity_performed": False,
    "state_changed": False,
    "new_samples_counted": 0,
}
POLICY_FIELDS = {
    "schema_version": SCHEMA_VERSION,
    "policy_id": POLICY_ID,
    "minimal_transitive_closure_required": True,
    "offline_replay_required": True,
    "content_addressed_payload_required": True,
    "original_artifact_bytes_required": True,
    "absolute_paths_prohibited": True,
    "undeclared_payload_files_prohibited": True,
    "external_replay_dependency_prohibited": True,
    "read_only": True,
    "authorization_escalation_prohibited": True,
    "state_mutation_prohibited": True,
    "network_activity_prohibited": True,
}
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class OperationsBundleResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def load_operations_bundle_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("operations bundle config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME
        frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("operations bundle config read failed") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("operations bundle config policy mismatch")
    if not isinstance(value, dict) or value != POLICY_FIELDS:
        raise MarketDataError("operations bundle config policy mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def build_prospective_evidence_operations_bundle(
    operations_snapshot: str | Path,
    config: str | Path,
    output_dir: str | Path,
) -> OperationsBundleResult:
    snapshot_path = Path(operations_snapshot).resolve()
    repo = _repo_root(snapshot_path)
    policy = load_operations_bundle_config(config, repo)
    snapshot, consumed = _validated_consumed_files(snapshot_path)
    entries = _build_entries(repo, consumed, snapshot)
    inventory_bytes = _csv_bytes(
        [entry["inventory"] for entry in entries], INVENTORY_FIELDS
    )
    snapshot_logical = _logical_name(repo, snapshot_path)
    dependency_rows = [
        {
            "ordinal": index,
            "from_logical_name": snapshot_logical,
            "to_logical_name": entry["inventory"]["logical_name"],
            "dependency_kind": "offline_replay_input",
        }
        for index, entry in enumerate(entries, start=1)
    ]
    dependency_bytes = _csv_bytes(dependency_rows, DEPENDENCY_FIELDS)
    constraints_bytes = _csv_bytes(
        [{"key": key, "value": value} for key, value in sorted(policy.items())],
        KEY_FIELDS,
    )
    source_state = {field: snapshot.get(field) for field in SOURCE_STATE_FIELDS}
    identity = {
        "bundle_schema_version": SCHEMA_VERSION,
        "bundle_role": "operations_handoff",
        "portable": True,
        "read_only": True,
        "policy_id": POLICY_ID,
        "policy": policy,
        "source_operations_snapshot_identity": snapshot["snapshot_sha256"],
        "source_snapshot_replay_valid": True,
        "source_state": source_state,
        **FALSE_BUNDLE_FLAGS,
        "payload_file_count": len(entries),
        "inventory_sha256": _digest(inventory_bytes),
        "dependencies_sha256": _digest(dependency_bytes),
        "constraints_sha256": _digest(constraints_bytes),
    }
    bundle_sha = _digest(_canonical(identity))
    output = Path(output_dir).resolve()
    if output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("operations bundle output must be outside source reports")
    output.mkdir(parents=True, exist_ok=True)
    stem = f"{BUNDLE_PREFIX}.{bundle_sha}"
    # Keep the physical payload directory short so the original content-addressed
    # report names remain portable on Windows (the logical name is frozen in the
    # inventory and is independent of this physical directory name).
    payload = output / "payload"
    paths = {
        "inventory": output / f"{stem}.inventory.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
        "payload": payload,
    }
    _materialize_payload(payload, entries)
    _commit_bytes(paths["inventory"], inventory_bytes)
    _commit_bytes(paths["dependencies"], dependency_bytes)
    _commit_bytes(paths["constraints"], constraints_bytes)
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract_status": CONTRACT_STATUS,
        "bundle_sha256": bundle_sha,
        "bundle_status": "portable_handoff_ready",
        "identity": identity,
        **source_state,
        "source_operations_snapshot_identity": snapshot["snapshot_sha256"],
        "source_snapshot_replay_valid": True,
        **FALSE_BUNDLE_FLAGS,
        "artifacts": {
            "inventory": {
                "filename": paths["inventory"].name,
                "sha256": _digest(inventory_bytes),
                "row_count": len(entries),
            },
            "dependencies": {
                "filename": paths["dependencies"].name,
                "sha256": _digest(dependency_bytes),
                "row_count": len(dependency_rows),
            },
            "constraints": {
                "filename": paths["constraints"].name,
                "sha256": _digest(constraints_bytes),
                "row_count": len(policy),
            },
            "payload": {"directory": paths["payload"].name},
        },
    }
    _commit_bytes(paths["report"], _pretty(report))
    validate_prospective_evidence_operations_bundle(paths["report"])
    return OperationsBundleResult(
        report,
        {key: str(value) for key, value in paths.items()},
    )


def validate_prospective_evidence_operations_bundle(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    report = _load_json(report_path)
    bundle_sha = report.get("bundle_sha256")
    identity = report.get("identity")
    if (
        not isinstance(bundle_sha, str)
        or not _SHA_RE.fullmatch(bundle_sha)
        or report_path.name != f"{BUNDLE_PREFIX}.{bundle_sha}.json"
        or not isinstance(identity, dict)
        or _digest(_canonical(identity)) != bundle_sha
        or report.get("schema_version") != SCHEMA_VERSION
        or report.get("contract_status") != CONTRACT_STATUS
        or report.get("bundle_status") != "portable_handoff_ready"
    ):
        raise MarketDataError("operations bundle identity mismatch")
    _validate_portable_identity(identity)
    source_state = identity.get("source_state")
    if not isinstance(source_state, dict) or any(report.get(key) != source_state.get(key) for key in SOURCE_STATE_FIELDS):
        raise MarketDataError("operations bundle source state mismatch")
    for key, expected in FALSE_BUNDLE_FLAGS.items():
        if report.get(key) != expected or identity.get(key) != expected:
            raise MarketDataError(f"operations bundle forbidden flag:{key}")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MarketDataError("operations bundle artifacts missing")
    inventory_path, dependencies_path, constraints_path, payload_path = _artifact_paths(
        report_path, bundle_sha, artifacts
    )
    inventory_bytes = _validated_sidecar(inventory_path, artifacts, "inventory")
    dependencies_bytes = _validated_sidecar(dependencies_path, artifacts, "dependencies")
    constraints_bytes = _validated_sidecar(constraints_path, artifacts, "constraints")
    inventory = _read_csv_exact(inventory_bytes, INVENTORY_FIELDS)
    dependencies = _read_csv_exact(dependencies_bytes, DEPENDENCY_FIELDS)
    constraints = _read_csv_exact(constraints_bytes, KEY_FIELDS)
    _validate_inventory(inventory, payload_path)
    _validate_dependencies(dependencies, inventory, identity["source_operations_snapshot_identity"])
    if constraints != [{"key": key, "value": _csv_value(value)} for key, value in sorted(POLICY_FIELDS.items())]:
        raise MarketDataError("operations bundle constraints mismatch")
    _validate_payload_bytes(inventory, payload_path)
    snapshot_logical = _snapshot_logical_name(inventory, identity["source_operations_snapshot_identity"])
    # Keep the staging root short on Windows: the governed artifact names are
    # intentionally content-addressed and can approach the MAX_PATH boundary.
    replay_parent = Path(__file__).resolve().parents[3] / "tmp"
    replay_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="r-", dir=str(replay_parent), ignore_cleanup_errors=True
    ) as replay_dir:
        replay_root = Path(replay_dir)
        _copy_payload_to_short_root(inventory, payload_path, replay_root)
        replayed = validate_prospective_evidence_operations_snapshot(
            _payload_path(replay_root, snapshot_logical), replay_root=replay_root
        )
    if replayed.get("snapshot_sha256") != identity["source_operations_snapshot_identity"]:
        raise MarketDataError("operations bundle source snapshot identity mismatch")
    if any(replayed.get(key) != source_state.get(key) for key in SOURCE_STATE_FIELDS):
        raise MarketDataError("operations bundle replay projection mismatch")
    return report


def format_operations_bundle(result: OperationsBundleResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"bundle_sha256: {report['bundle_sha256']}",
            f"bundle_status: {report['bundle_status']}",
            f"source_operations_snapshot_identity: {report['source_operations_snapshot_identity']}",
            "source_snapshot_replay_valid: true",
            f"payload_file_count: {report['identity']['payload_file_count']}",
            f"governed_stage: {report['governed_stage']}",
            f"next_legal_action: {report['next_legal_action']}",
            f"current_samples: {report['current_samples']}",
            f"remaining_samples: {report['remaining_samples']}",
            "state_changed: false",
            "network_activity_performed: false",
        )
    )


def _validated_consumed_files(path: Path) -> tuple[dict[str, Any], list[Path]]:
    repo = _repo_root(path)
    seen: list[Path] = []
    seen_set: set[Path] = set()

    def remember(candidate: Path) -> None:
        resolved = candidate.resolve()
        if resolved not in seen_set:
            seen_set.add(resolved)
            seen.append(resolved)

    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    original_open = Path.open

    def read_bytes(candidate: Path, *args: Any, **kwargs: Any) -> bytes:
        remember(candidate)
        return original_read_bytes(candidate, *args, **kwargs)

    def read_text(candidate: Path, *args: Any, **kwargs: Any) -> str:
        remember(candidate)
        return original_read_text(candidate, *args, **kwargs)

    def open_file(candidate: Path, *args: Any, **kwargs: Any) -> Any:
        remember(candidate)
        return original_open(candidate, *args, **kwargs)

    with patch.object(Path, "read_bytes", read_bytes), patch.object(Path, "read_text", read_text), patch.object(Path, "open", open_file):
        report = validate_prospective_evidence_operations_snapshot(path)
    allowed: list[Path] = []
    for candidate in seen:
        if not candidate.is_file() or not candidate.is_relative_to(repo):
            continue
        relative = candidate.relative_to(repo)
        if relative == Path("pyproject.toml") or relative.parts[:1] == ("reports",) or _is_root_config(relative):
            allowed.append(candidate)
        else:
            raise MarketDataError(f"operations bundle external dependency:{relative.as_posix()}")
    return report, sorted(set(allowed))


def _build_entries(repo: Path, consumed: Iterable[Path], snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    files: dict[str, bytes] = {}
    source_paths: dict[str, Path | None] = {}
    for source in consumed:
        relative = source.relative_to(repo)
        logical = relative.as_posix()
        if relative == Path("pyproject.toml"):
            files[logical] = REPLAY_PYPROJECT.encode("utf-8")
            source_paths[logical] = None
        else:
            files[logical] = source.read_bytes()
            source_paths[logical] = source
    # The explicit replay root requires these deterministic local markers even
    # when the source validators did not happen to read them directly.
    files["pyproject.toml"] = REPLAY_PYPROJECT.encode("utf-8")
    source_paths["pyproject.toml"] = None
    files["src/crypto_bot/.replay-root"] = b"portable replay root\n"
    source_paths["src/crypto_bot/.replay-root"] = None
    marker_ids = _marker_ids(repo, source_paths.values())
    entries: list[dict[str, Any]] = []
    for logical in sorted(files):
        role = _artifact_role(logical, snapshot)
        source_path: Path | None = source_paths[logical]
        source_identity = marker_ids.get(source_path.parent if source_path is not None else Path(""), "")
        if source_path is not None and source_path.suffix.lower() == ".json":
            source_identity = _json_identity(source_path.read_bytes()) or source_identity
        row = {
            "ordinal": 0,
            "artifact_role": role,
            "logical_name": logical,
            "source_identity": source_identity,
            "sha256": _digest(files[logical]),
            "byte_count": len(files[logical]),
        }
        entries.append({"inventory": row, "content": files[logical]})
    for ordinal, entry in enumerate(entries, start=1):
        entry["inventory"]["ordinal"] = ordinal
    return entries


def _marker_ids(repo: Path, sources: Iterable[Path | None]) -> dict[Path, str]:
    result: dict[Path, str] = {}
    for source in sources:
        if source is None or source.suffix.lower() != ".json":
            continue
        try:
            identity = _json_identity(source.read_bytes())
        except OSError:
            continue
        if identity:
            result[source.parent] = identity
    return result


def _json_identity(content: bytes) -> str:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(value, dict):
        return ""
    filename = ""
    for key in value:
        item = value.get(key)
        if key.endswith("_sha256") and isinstance(item, str) and _SHA_RE.fullmatch(item):
            filename = item
            break
    return filename


def _materialize_payload(payload: Path, entries: Sequence[Mapping[str, Any]]) -> None:
    payload.mkdir(parents=True, exist_ok=True)
    expected: set[Path] = set()
    for entry in entries:
        logical = str(entry["inventory"]["logical_name"])
        destination = _payload_path(payload, logical)
        expected.add(_absolute_path(destination))
        _commit_bytes(destination, bytes(entry["content"]))
    existing = {
        _absolute_path(_payload_path(payload, logical))
        for logical in _payload_file_logicals(payload)
    }
    if existing != expected:
        raise MarketDataError("operations bundle undeclared payload file")


def _artifact_paths(report_path: Path, bundle_sha: str, artifacts: Mapping[str, Any]) -> tuple[Path, Path, Path, Path]:
    stem = f"{BUNDLE_PREFIX}.{bundle_sha}"
    expected = {
        "inventory": report_path.parent / f"{stem}.inventory.csv",
        "dependencies": report_path.parent / f"{stem}.dependencies.csv",
        "constraints": report_path.parent / f"{stem}.constraints.csv",
        "payload": report_path.parent / "payload",
    }
    for key, value in expected.items():
        info = artifacts.get(key)
        if key == "payload":
            if not isinstance(info, dict) or info.get("directory") != value.name:
                raise MarketDataError("operations bundle payload metadata mismatch")
        elif not isinstance(info, dict) or info.get("filename") != value.name:
            raise MarketDataError(f"operations bundle artifact filename:{key}")
    return expected["inventory"], expected["dependencies"], expected["constraints"], expected["payload"]


def _validated_sidecar(path: Path, artifacts: Mapping[str, Any], name: str) -> bytes:
    info = artifacts.get(name)
    if not isinstance(info, dict) or not path.is_file():
        raise MarketDataError(f"operations bundle artifact missing:{name}")
    content = path.read_bytes()
    if info.get("sha256") != _digest(content):
        raise MarketDataError(f"operations bundle artifact hash:{name}")
    return content


def _validate_inventory(rows: list[dict[str, str]], payload: Path) -> None:
    if not rows or [int(row["ordinal"]) for row in rows] != list(range(1, len(rows) + 1)):
        raise MarketDataError("operations bundle inventory ordinal mismatch")
    logicals: set[str] = set()
    for row in rows:
        logical = row["logical_name"]
        if logical in logicals or not _safe_logical_name(logical):
            raise MarketDataError("operations bundle inventory logical path mismatch")
        logicals.add(logical)
        if not _SHA_RE.fullmatch(row["sha256"]) or int(row["byte_count"]) < 0:
            raise MarketDataError("operations bundle inventory metadata mismatch")
    if not payload.is_dir():
        raise MarketDataError("operations bundle payload missing")


def _validate_dependencies(rows: list[dict[str, str]], inventory: list[dict[str, str]], snapshot_identity: str) -> None:
    logicals = {row["logical_name"] for row in inventory}
    expected = [
        {
            "ordinal": str(index),
            "from_logical_name": next(row["logical_name"] for row in inventory if row["source_identity"] == snapshot_identity and row["logical_name"].endswith(".json")),
            "to_logical_name": row["logical_name"],
            "dependency_kind": "offline_replay_input",
        }
        for index, row in enumerate(inventory, start=1)
    ]
    if rows != expected or any(row["to_logical_name"] not in logicals for row in rows):
        raise MarketDataError("operations bundle dependency closure mismatch")


def _validate_payload_bytes(rows: list[dict[str, str]], payload: Path) -> None:
    actual = set(_payload_file_logicals(payload))
    expected = {row["logical_name"] for row in rows}
    if actual != expected:
        raise MarketDataError("operations bundle undeclared payload file")
    for row in rows:
        content_path = _payload_path(payload, row["logical_name"])
        content = _read_bytes(content_path)
        if _digest(content) != row["sha256"] or len(content) != int(row["byte_count"]):
            raise MarketDataError("operations bundle payload hash mismatch")


def _copy_payload_to_short_root(
    rows: Sequence[Mapping[str, str]], payload: Path, destination: Path
) -> None:
    for row in rows:
        target = _payload_path(destination, str(row["logical_name"]))
        _commit_bytes(target, _read_bytes(_payload_path(payload, str(row["logical_name"]))))


def _payload_file_logicals(payload: Path) -> list[str]:
    root = _native_path(payload)
    if not os.path.isdir(root):
        raise MarketDataError("operations bundle payload missing")
    logicals: list[str] = []
    for current, _directories, files in os.walk(root):
        for filename in files:
            absolute = os.path.join(current, filename)
            relative = os.path.relpath(absolute, root).replace(os.sep, "/")
            logicals.append(relative)
    return sorted(logicals)


def _snapshot_logical_name(rows: list[dict[str, str]], identity: str) -> str:
    matches = [row["logical_name"] for row in rows if row["source_identity"] == identity and "prospective-evidence-operations-snapshot" in row["logical_name"] and row["logical_name"].endswith(".json")]
    if len(matches) != 1:
        raise MarketDataError("operations bundle source snapshot missing")
    return matches[0]


def _payload_path(payload: Path, logical: str) -> Path:
    if not _safe_logical_name(logical):
        raise MarketDataError("operations bundle unsafe logical path")
    root = _absolute_path(payload)
    path = _absolute_path(root / Path(*logical.split("/")))
    if not path.is_relative_to(root):
        raise MarketDataError("operations bundle payload path escape")
    return path


def _absolute_path(path: Path) -> Path:
    """Normalize a path without pathlib.resolve's Windows long-path prefix."""
    return Path(os.path.abspath(os.fspath(path)))


def _safe_logical_name(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return False
    pure = PurePosixPath(value)
    return ".." not in pure.parts and "." not in pure.parts and pure.as_posix() == value


def _validate_portable_identity(identity: Mapping[str, Any]) -> None:
    expected = {
        "bundle_schema_version": SCHEMA_VERSION,
        "bundle_role": "operations_handoff",
        "portable": True,
        "read_only": True,
        "policy_id": POLICY_ID,
        "source_snapshot_replay_valid": True,
    }
    if any(identity.get(key) != value for key, value in expected.items()) or identity.get("policy") != POLICY_FIELDS:
        raise MarketDataError("operations bundle policy mismatch")
    for key, value in FALSE_BUNDLE_FLAGS.items():
        if identity.get(key) != value:
            raise MarketDataError(f"operations bundle forbidden identity flag:{key}")
    _reject_absolute_strings(identity)


def _reject_absolute_strings(value: Any) -> None:
    if isinstance(value, str) and (value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value)):
        raise MarketDataError("operations bundle absolute path")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_absolute_strings(item)
    elif isinstance(value, list):
        for item in value:
            _reject_absolute_strings(item)


def _artifact_role(logical: str, snapshot: Mapping[str, Any]) -> str:
    if logical == "pyproject.toml" or logical == "src/crypto_bot/.replay-root":
        return "replay_root_marker"
    if logical.startswith("config."):
        return "policy_config"
    if "prospective-evidence-operations-snapshot" in logical:
        return "operations_snapshot_report" if logical.endswith(".json") else "operations_snapshot_artifact"
    return "transitive_report" if logical.endswith(".json") else "transitive_artifact"


def _logical_name(repo: Path, path: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def _is_root_config(path: Path) -> bool:
    return len(path.parts) == 1 and path.name.startswith("config.") and path.name.endswith(".example.yaml")


def _repo_root(path: Path) -> Path:
    for parent in (path.resolve(), *path.resolve().parents):
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "crypto_bot").is_dir():
            return parent
    raise MarketDataError("operations bundle repo root missing")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("operations bundle json read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("operations bundle json shape mismatch")
    return value


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
            raise MarketDataError("operations bundle CSV schema mismatch")
        return list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise MarketDataError("operations bundle CSV read failed") from exc


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    return str(value)


def _commit_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    native = _native_path(path)
    if os.path.lexists(native):
        if path.is_symlink() or _read_bytes(path) != content:
            raise MarketDataError(f"operations bundle content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=_native_path(path.parent), prefix=".operations-bundle-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(_native_path(temporary), native)
    finally:
        try:
            os.unlink(_native_path(temporary))
        except FileNotFoundError:
            pass


def _read_bytes(path: Path) -> bytes:
    with open(_native_path(path), "rb") as handle:
        return handle.read()


def _native_path(path: Path) -> str:
    value = os.path.abspath(str(path))
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return "\\\\?\\" + value
    return value


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
