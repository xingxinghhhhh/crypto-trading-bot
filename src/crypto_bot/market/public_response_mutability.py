from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.okx_direct_six_asset_migration import (
    ANCHOR_INST_IDS,
    ValidatedOkxDirectAnchor1hCapture,
    validate_okx_direct_six_asset_1h_migration,
    validate_okx_direct_anchor_1h_capture,
)


SCHEMA_VERSION = 1
AUDIT_STATUS = "verified_okx_public_response_mutability_audit"
DEFAULT_POLICY_FILENAME = "config.public-response-mutability.example.yaml"
FIELDS = ("ts", "o", "h", "l", "c", "vol", "volCcy", "volCcyQuote", "confirm")
DIFF_CATEGORIES = (
    "timestamp_changed",
    "ohlc_changed",
    "base_volume_changed",
    "quote_volume_changed",
    "confirm_changed",
    "row_added_or_removed",
    "response_envelope_changed",
)


@dataclass(frozen=True)
class PublicResponseMutabilityAuditResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def audit_okx_public_response_mutability(
    baseline_capture: str | Path,
    comparison_capture: str | Path,
    policy_path: str | Path,
    output_dir: str | Path,
) -> PublicResponseMutabilityAuditResult:
    baseline_path = Path(baseline_capture).resolve()
    comparison_path = Path(comparison_capture).resolve()
    repo = _repo_root(baseline_path)
    if _repo_root(comparison_path) != repo:
        raise MarketDataError("public_mutability_capture_repo_mismatch")
    policy = load_public_response_mutability_policy(policy_path, repo)
    baseline = validate_okx_direct_anchor_1h_capture(baseline_path)
    comparison = validate_okx_direct_anchor_1h_capture(comparison_path)
    _validate_capture_policy(baseline, policy)
    _validate_capture_policy(comparison, policy)
    if baseline.report["capture_sha256"] == comparison.report["capture_sha256"]:
        raise MarketDataError("public_mutability_capture_identity_not_distinct")
    if _capture_request_identity(baseline, policy) != _capture_request_identity(comparison, policy):
        raise MarketDataError("public_mutability_request_identity_mismatch")
    migration_pin = _migration_pin(policy, repo)
    captures_rows = _capture_rows(baseline, comparison)
    differences = _compare_captures(baseline, comparison, policy)
    counts = _difference_counts(differences)
    output = _reports_output(repo, output_dir)
    captures_bytes = _csv_bytes(
        [
            "capture_role",
            "capture_sha256",
            "marker_sha256",
            "inst_id",
            "dataset_id",
            "bar_count",
            "page_count",
            "history_bundle_sha256",
            "csv_raw_sha256",
            "csv_canonical_sha256",
        ],
        captures_rows,
    )
    difference_bytes = _csv_bytes(
        [
            "inst_id",
            "page_index",
            "row_index",
            "timestamp",
            "field_index",
            "field_name",
            "category",
            "baseline_value",
            "comparison_value",
        ],
        differences,
    )
    policy_rows = [
        {"key": key, "value": json.dumps(value, ensure_ascii=False, sort_keys=True)}
        for key, value in _flatten_policy(policy).items()
    ]
    policy_bytes = _csv_bytes(["key", "value"], policy_rows)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy": policy,
        "baseline_capture": _capture_identity(baseline),
        "comparison_capture": _capture_identity(comparison),
        "migration_pin": migration_pin,
        "request_identity": _capture_request_identity(baseline, policy),
        "differences": {"total": len(differences), "categories": counts},
        "claims": policy["claims"],
        "capture_replay_deterministic": True,
        "network_recapture_byte_identical": False,
        "public_historical_response_mutability_observed": bool(differences),
        "artifacts": {},
    }
    captures_sha = _digest(captures_bytes)
    differences_sha = _digest(difference_bytes)
    policy_sha = _digest(policy_bytes)
    identity["artifacts"] = {
        "captures_sha256": captures_sha,
        "differences_sha256": differences_sha,
        "policy_sha256": policy_sha,
    }
    audit_sha = _digest(_json_bytes(identity))
    captures_path = output / f"public-response-mutability.{audit_sha}.captures.csv"
    differences_path = output / f"public-response-mutability.{audit_sha}.differences.csv"
    policy_path_out = output / f"public-response-mutability.{audit_sha}.policy.csv"
    _commit_bytes(captures_path, captures_bytes)
    _commit_bytes(differences_path, difference_bytes)
    _commit_bytes(policy_path_out, policy_bytes)
    marker_identity = identity
    audit_sha = _digest(_json_bytes(marker_identity))
    if audit_sha != Path(captures_path).stem.split(".")[1]:
        raise MarketDataError("public_mutability_audit_identity_filename_mismatch")
    report = {
        "schema_version": SCHEMA_VERSION,
        "audit_sha256": audit_sha,
        "audit_status": AUDIT_STATUS,
        "identity": marker_identity,
        "profitability_evidence": False,
        "readiness_changed": False,
    }
    marker_path = output / f"public-response-mutability.{audit_sha}.json"
    _commit_json(marker_path, report)
    return PublicResponseMutabilityAuditResult(
        report,
        {
            "captures": str(captures_path),
            "differences": str(differences_path),
            "policy": str(policy_path_out),
            "report": str(marker_path),
        },
    )


def format_public_response_mutability(result: PublicResponseMutabilityAuditResult) -> str:
    identity = result.report["identity"]
    return "\n".join(
        [
            f"audit_status: {result.report['audit_status']}",
            f"audit_sha256: {result.report['audit_sha256']}",
            f"difference_count: {identity['differences']['total']}",
            "capture_replay_deterministic: true",
            "network_recapture_byte_identical: false",
            f"public_historical_response_mutability_observed: {str(identity['public_historical_response_mutability_observed']).lower()}",
            "baseline_migration_remains_pinned: true",
        ]
    )


def load_public_response_mutability_policy(
    path: str | Path, repo: Path | None = None
) -> dict[str, Any]:
    policy_path = Path(path).resolve()
    if not policy_path.is_file():
        raise FileNotFoundError(f"public response mutability policy not found: {policy_path}")
    if policy_path.name != DEFAULT_POLICY_FILENAME:
        raise ValueError("public response mutability policy filename is not frozen")
    try:
        value = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("public response mutability policy YAML is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("public response mutability policy must be a mapping")
    frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_POLICY_FILENAME
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("public response mutability policy must equal the frozen config")
    if repo is not None and not policy_path.is_relative_to(repo.resolve()):
        raise MarketDataError("public_mutability_policy_path_escape")
    if value["inst_ids"] != list(ANCHOR_INST_IDS) or value["fields"] != list(FIELDS):
        raise MarketDataError("public_mutability_policy_contract_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def _validate_capture_policy(
    capture: ValidatedOkxDirectAnchor1hCapture, policy: dict[str, Any]
) -> None:
    identity = capture.report["identity"]
    actual = identity["policy"]
    if (
        actual["anchor_inst_ids"] != policy["inst_ids"]
        or actual["history_start"] != policy["history_start"]
        or actual["end_open"] != policy["end_open"]
        or actual["timeframe"] != policy["timeframe"]
        or actual["okx_bar"] != policy["okx_bar"]
        or actual["expected_bar_count"] != policy["expected_bar_count"]
        or actual["expected_page_count"] != policy["expected_page_count"]
    ):
        raise MarketDataError("public_mutability_capture_policy_mismatch")


def _capture_request_identity(
    capture: ValidatedOkxDirectAnchor1hCapture, policy: dict[str, Any]
) -> dict[str, Any]:
    capture_policy = capture.report["identity"]["policy"]
    return {
        "endpoint": policy["endpoint"],
        "inst_ids": list(ANCHOR_INST_IDS),
        "timeframe": capture_policy["timeframe"],
        "okx_bar": policy["okx_bar"],
        "history_start": policy["history_start"],
        "end_open": policy["end_open"],
        "expected_bar_count": policy["expected_bar_count"],
        "expected_page_count": policy["expected_page_count"],
    }


def _capture_identity(capture: ValidatedOkxDirectAnchor1hCapture) -> dict[str, Any]:
    return {
        "capture_sha256": capture.report["capture_sha256"],
        "marker_filename": capture.report_path.name,
        "marker_sha256": _sha256(capture.report_path),
        "histories": [
            {
                "inst_id": history["inst_id"],
                "dataset_id": history["dataset_id"],
                "bar_count": history["bar_count"],
                "page_count": len(history["pages"]),
                "history_bundle_sha256": history["history_bundle"]["sha256"],
                "csv_raw_sha256": history["csv"]["raw_sha256"],
                "csv_canonical_sha256": history["csv"]["canonical_sha256"],
            }
            for history in capture.histories
        ],
    }


def _capture_rows(
    baseline: ValidatedOkxDirectAnchor1hCapture,
    comparison: ValidatedOkxDirectAnchor1hCapture,
) -> list[dict[str, Any]]:
    rows = []
    for role, capture in (("baseline", baseline), ("comparison", comparison)):
        for history in capture.histories:
            rows.append(
                {
                    "capture_role": role,
                    "capture_sha256": capture.report["capture_sha256"],
                    "marker_sha256": _sha256(capture.report_path),
                    "inst_id": history["inst_id"],
                    "dataset_id": history["dataset_id"],
                    "bar_count": history["bar_count"],
                    "page_count": len(history["pages"]),
                    "history_bundle_sha256": history["history_bundle"]["sha256"],
                    "csv_raw_sha256": history["csv"]["raw_sha256"],
                    "csv_canonical_sha256": history["csv"]["canonical_sha256"],
                }
            )
    return rows


def _compare_captures(
    baseline: ValidatedOkxDirectAnchor1hCapture,
    comparison: ValidatedOkxDirectAnchor1hCapture,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_by_id = {item["inst_id"]: item for item in baseline.histories}
    comparison_by_id = {item["inst_id"]: item for item in comparison.histories}
    if list(baseline_by_id) != list(ANCHOR_INST_IDS) or list(comparison_by_id) != list(ANCHOR_INST_IDS):
        raise MarketDataError("public_mutability_capture_asset_order_mismatch")
    differences: list[dict[str, Any]] = []
    for inst_id in ANCHOR_INST_IDS:
        left_pages = _bundle_pages(baseline.artifact_paths[f"{inst_id}_history"], inst_id)
        right_pages = _bundle_pages(comparison.artifact_paths[f"{inst_id}_history"], inst_id)
        if list(left_pages) == [] or list(right_pages) == []:
            raise MarketDataError(f"public_mutability_empty_history:{inst_id}")
        left_by_page = {page["page_index"]: page for page in left_pages}
        right_by_page = {page["page_index"]: page for page in right_pages}
        if set(left_by_page) != set(right_by_page):
            raise MarketDataError(f"public_mutability_page_identity_mismatch:{inst_id}")
        for page_index in sorted(left_by_page):
            a = left_by_page[page_index]
            b = right_by_page[page_index]
            if a["request_params"] != b["request_params"]:
                raise MarketDataError(f"public_mutability_request_params_mismatch:{inst_id}:{page_index}")
            differences.extend(_compare_page(inst_id, a, b, policy))
    return differences


def _bundle_pages(path: Path, inst_id: str) -> list[dict[str, Any]]:
    pages = []
    for line in path.read_bytes().splitlines():
        try:
            record = json.loads(line)
            response = json.loads(record["response_body"])
            if record["response_sha256"] != _digest(record["response_body"].encode()):
                raise MarketDataError(f"public_mutability_response_hash_mismatch:{inst_id}")
            pages.append(
                {
                    "page_index": record["page_index"],
                    "request_params": record["request_params"],
                    "code": response.get("code"),
                    "msg": response.get("msg"),
                    "data": response.get("data"),
                }
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MarketDataError(f"public_mutability_invalid_bundle:{inst_id}") from exc
    return pages


def _compare_page(
    inst_id: str, baseline: dict[str, Any], comparison: dict[str, Any], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    if baseline["code"] != comparison["code"]:
        differences.append(_difference(inst_id, baseline, -1, "code", "response_envelope_changed"))
    if baseline["msg"] != comparison["msg"]:
        differences.append(_difference(inst_id, baseline, -1, "msg", "response_envelope_changed"))
    left_rows = baseline["data"]
    right_rows = comparison["data"]
    if not isinstance(left_rows, list) or not isinstance(right_rows, list):
        raise MarketDataError(f"public_mutability_invalid_data:{inst_id}")
    for row_index in range(max(len(left_rows), len(right_rows))):
        if row_index >= len(left_rows) or row_index >= len(right_rows):
            differences.append(
                _difference(
                    inst_id,
                    baseline,
                    row_index,
                    "",
                    "row_added_or_removed",
                    baseline_value=_value(left_rows, row_index),
                    comparison_value=_value(right_rows, row_index),
                )
            )
            continue
        left = left_rows[row_index]
        right = right_rows[row_index]
        if not isinstance(left, list) or not isinstance(right, list):
            raise MarketDataError(f"public_mutability_invalid_row:{inst_id}")
        for field_index, field_name in enumerate(policy["fields"]):
            left_value = _value(left, field_index)
            right_value = _value(right, field_index)
            if left_value != right_value:
                differences.append(
                    _difference(
                        inst_id,
                        baseline,
                        row_index,
                        field_name,
                        _category(field_index),
                        baseline_value=left_value,
                        comparison_value=right_value,
                        field_index=field_index,
                        timestamp=_value(left, 0),
                    )
                )
    return differences


def _difference(
    inst_id: str,
    page: dict[str, Any],
    row_index: int,
    field_name: str,
    category: str,
    *,
    baseline_value: Any = "",
    comparison_value: Any = "",
    field_index: int = -1,
    timestamp: Any = "",
) -> dict[str, Any]:
    return {
        "inst_id": inst_id,
        "page_index": page["page_index"],
        "row_index": row_index,
        "timestamp": timestamp,
        "field_index": field_index,
        "field_name": field_name,
        "category": category,
        "baseline_value": _csv_value(baseline_value),
        "comparison_value": _csv_value(comparison_value),
    }


def _category(field_index: int) -> str:
    if field_index == 0:
        return "timestamp_changed"
    if 1 <= field_index <= 4:
        return "ohlc_changed"
    if field_index == 5:
        return "base_volume_changed"
    if field_index in (6, 7):
        return "quote_volume_changed"
    return "confirm_changed"


def _difference_counts(differences: list[dict[str, Any]]) -> dict[str, int]:
    return {category: sum(item["category"] == category for item in differences) for category in DIFF_CATEGORIES}


def _flatten_policy(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key in sorted(value):
        name = f"{prefix}.{key}" if prefix else key
        child = value[key]
        if isinstance(child, dict):
            flattened.update(_flatten_policy(child, name))
        else:
            flattened[name] = child
    return flattened


def _migration_pin(policy: dict[str, Any], repo: Path) -> dict[str, Any]:
    marker = (
        repo
        / "reports"
        / "okx-direct-six-asset-1h-migration"
        / f"okx-direct-six-migration.{policy['baseline_migration_sha256']}.json"
    )
    if not marker.is_file():
        raise MarketDataError("public_mutability_baseline_migration_missing")
    migration = validate_okx_direct_six_asset_1h_migration(marker)
    panel_sha = migration.report["identity"]["panel"]["panel_sha256"]
    if (
        migration.report["migration_sha256"] != policy["baseline_migration_sha256"]
        or panel_sha != policy["baseline_panel_sha256"]
    ):
        raise MarketDataError("public_mutability_baseline_migration_mismatch")
    return {
        "migration_sha256": policy["baseline_migration_sha256"],
        "panel_sha256": policy["baseline_panel_sha256"],
        "marker_filename": marker.name,
        "marker_sha256": _sha256(marker),
        "baseline_remains_pinned": True,
    }


def _value(values: Any, index: int) -> Any:
    return values[index] if isinstance(values, list) and index < len(values) else ""


def _csv_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return "" if value is None else str(value)


def _csv_bytes(fields: list[str], rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="raise", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _repo_root(path: Path) -> Path:
    for parent in (path.parent, *path.parents):
        if parent.name == "reports":
            return parent.parent.resolve()
    raise MarketDataError("public_mutability_report_repo_missing")


def _reports_output(repo: Path, value: str | Path) -> Path:
    raw = Path(value)
    path = (repo / raw).resolve() if not raw.is_absolute() else raw.resolve()
    reports = (repo / "reports").resolve()
    if not path.is_relative_to(reports):
        raise ValueError("public response mutability output must stay inside reports")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"public_mutability_content_addressed_collision:{path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".public-mutability-", suffix=".tmp", delete=False
        ) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _commit_json(path: Path, value: dict[str, Any]) -> None:
    _commit_bytes(path, _json_bytes(value, pretty=True))


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256(path: Path) -> str:
    return _digest(path.read_bytes())
