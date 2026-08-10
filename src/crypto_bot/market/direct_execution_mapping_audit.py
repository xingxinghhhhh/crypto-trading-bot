from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_panel import build_dataset_panel
from crypto_bot.market.dataset_registry import canonicalize_ohlcv_frame, load_dataset_registry
from crypto_bot.market.legacy_timestamp_mapping_audit import (
    build_exact_next_open_mapping_rows,
)
from crypto_bot.market.okx_direct_six_asset_migration import (
    ALL_DATASET_IDS,
    validate_okx_direct_six_asset_1h_migration,
)


SCHEMA_VERSION = 1
AUDIT_STATUS = "verified_okx_direct_six_1h_execution_mapping_feasibility"
DEFAULT_CONFIG_FILENAME = "config.okx-direct-six-1h-execution-mapping.example.yaml"
MAPPING_FIELDS = (
    "signal_timestamp",
    "signal_completion_timestamp",
    "execution_timestamp",
    "dataset_id",
    "symbol",
    "execution_price_field",
    "structural_status",
    "mapped_source_timestamp",
    "mapped_open",
    "exact_row_exists",
    "finite_open",
    "timestamp_semantics_status",
    "semantic_authorized",
)


@dataclass(frozen=True)
class DirectExecutionMappingAuditResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def audit_okx_direct_six_1h_execution_mapping(
    migration_report_path: str | Path,
    mutability_report_path: str | Path,
    policy_path: str | Path,
    output_dir: str | Path,
) -> DirectExecutionMappingAuditResult:
    migration_path = Path(migration_report_path).resolve()
    repo = _repo_root(migration_path)
    config = load_direct_execution_mapping_config(policy_path, repo)
    migration = validate_okx_direct_six_asset_1h_migration(migration_path)
    _validate_migration_pin(migration, migration_path, config)
    mutability_path = Path(mutability_report_path).resolve()
    if _repo_root(mutability_path) != repo:
        raise MarketDataError("direct_execution_mapping_mutability_repo_mismatch")
    mutability = _validate_mutability_report(mutability_path, config, repo)

    panel = build_dataset_panel(
        migration.registry_path,
        migration.panels_config_path,
        config["panel_id"],
    )
    if panel.report.get("panel_sha256") != config["panel_sha256"]:
        raise MarketDataError("direct_execution_mapping_panel_mismatch")
    timestamps = tuple(pd.Timestamp(value) for value in panel.frame["timestamp"])
    if len(timestamps) != config["expected_signal_timestamp_count"] or len(set(timestamps)) != len(timestamps):
        raise MarketDataError("direct_execution_mapping_signal_grid_shape_mismatch")
    if any(right - left != pd.Timedelta(hours=1) for left, right in zip(timestamps, timestamps[1:])):
        raise MarketDataError("direct_execution_mapping_signal_grid_offgrid")

    registry = load_dataset_registry(migration.registry_path)
    entries = tuple(registry.get(dataset_id) for dataset_id in config["dataset_ids"])
    frames = {
        entry.dataset_id: canonicalize_ohlcv_frame(pd.read_csv(entry.resolved_path))
        for entry in entries
    }
    statuses = _timestamp_statuses(migration.report, config["dataset_ids"])
    mappings = build_exact_next_open_mapping_rows(
        entries,
        frames,
        timestamps,
        statuses,
        config,
        error_prefix="direct_execution_mapping",
    )
    counts = _mapping_counts(timestamps, mappings, config)
    lineages = migration.report["identity"]["datasets"]
    identity = {
        "schema_version": SCHEMA_VERSION,
        "mapping_policy_id": config["mapping_policy_id"],
        "config": {"filename": Path(policy_path).name, "sha256": _sha256(Path(policy_path))},
        "migration": {
            "migration_sha256": config["migration_sha256"],
            "report_sha256": config["migration_report_sha256"],
            "panel_sha256": config["panel_sha256"],
            "panel_id": config["panel_id"],
        },
        "mutability_audit": {
            "audit_sha256": config["mutability_audit_sha256"],
            "report_sha256": _sha256(mutability_path),
            "capture_replay_deterministic": mutability["identity"]["capture_replay_deterministic"],
            "network_recapture_byte_identical": mutability["identity"]["network_recapture_byte_identical"],
        },
        "captures": {
            "baseline_capture_sha256": config["baseline_capture_sha256"],
            "comparison_capture_sha256": config["comparison_capture_sha256"],
        },
        "mapping": {
            "signal_completion_delay_bars": config["signal_completion_delay_bars"],
            "execution_delay_after_completion_bars": config["execution_delay_after_completion_bars"],
            "execution_offset_bars": config["execution_offset_bars"],
            "execution_price_field": config["execution_price_field"],
            "mapping_policy": config["mapping_policy"],
            "tail_policy": "final_two_panel_timestamps_are_structural_tail_for_all_assets",
        },
        "counts": counts,
        "dataset_ids": list(ALL_DATASET_IDS),
        "lineages": lineages,
        "feasibility": {
            "structural_common_next_open_mapping_verified": True,
            "timestamp_semantics_uniformly_verified": True,
            "execution_price_mapping_feasible": True,
            "pnl_prerequisite_timestamp_mapping_satisfied": True,
            "pnl_computation_authorized": False,
        },
        "claims": config["claims"],
        "artifacts": {},
    }
    mapping_bytes = _csv_bytes(mappings)
    identity["artifacts"] = {"mappings_sha256": _digest(mapping_bytes)}
    audit_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    mapping_path = output / f"okx-direct-six-1h-execution-mapping.{audit_sha}.csv"
    report_path = output / f"okx-direct-six-1h-execution-mapping.{audit_sha}.json"
    _commit_bytes(mapping_path, mapping_bytes)
    report = {
        "schema_version": SCHEMA_VERSION,
        "audit_sha256": audit_sha,
        "audit_status": AUDIT_STATUS,
        "identity": identity,
        "counts": counts,
        "feasibility": identity["feasibility"],
        "warnings": [
            "mapping_uses_exact_t_plus_2h_open_only",
            "no_fill_resample_carry_nearest_or_substitution",
            "current_live_convenience_membership_not_point_in_time",
            "survivorship_bias_unresolved",
            "no_returns_turnover_cost_or_pnl_computation",
            "no_profitability_or_execution_claim",
            "no_automatic_readiness_upgrade",
        ],
        "profitability_evidence": False,
        "readiness_changed": False,
        "automatic_factor_approval": False,
        "artifacts": {
            "mappings": {
                "filename": mapping_path.name,
                "sha256": identity["artifacts"]["mappings_sha256"],
                "row_count": len(mappings),
            },
            "report": {"filename": report_path.name},
        },
    }
    _commit_bytes(report_path, _pretty_json_bytes(report))
    return DirectExecutionMappingAuditResult(
        report, {"mappings": str(mapping_path), "report": str(report_path)}
    )


def load_direct_execution_mapping_config(
    path: str | Path, repo: Path | None = None
) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"direct execution mapping config not found: {config_path}")
    if config_path.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("direct execution mapping config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("direct execution mapping config YAML is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("direct execution mapping config must be a mapping")
    frozen_path = Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("direct execution mapping config must equal the frozen config")
    if repo is not None and not config_path.is_relative_to(repo.resolve()):
        raise MarketDataError("direct_execution_mapping_config_path_escape")
    if value["dataset_ids"] != list(ALL_DATASET_IDS):
        raise MarketDataError("direct_execution_mapping_dataset_order_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_direct_execution_mapping_audit(
    result: DirectExecutionMappingAuditResult,
) -> str:
    report = result.report
    return "\n".join(
        [
            f"audit_status: {report['audit_status']}",
            f"audit_sha256: {report['audit_sha256']}",
            f"signal_timestamp_count: {report['counts']['signal_timestamp_count']}",
            f"mapping_row_count: {report['counts']['mapping_row_count']}",
            f"structural_valid_count: {report['counts']['structural_valid_count']}",
            f"tail_count: {report['counts']['tail_count']}",
            "execution_price_mapping_feasible: true",
            "pnl_prerequisite_timestamp_mapping_satisfied: true",
            "pnl_computation_authorized: false",
            "readiness_changed: false",
        ]
    )


def _validate_migration_pin(migration: Any, path: Path, config: dict[str, Any]) -> None:
    if (
        migration.report.get("migration_sha256") != config["migration_sha256"]
        or _sha256(path) != config["migration_report_sha256"]
        or migration.report["identity"].get("panel", {}).get("panel_sha256") != config["panel_sha256"]
        or migration.report["identity"].get("panel", {}).get("panel_id") != config["panel_id"]
    ):
        raise MarketDataError("direct_execution_mapping_migration_pin_mismatch")


def _validate_mutability_report(
    path: Path, config: dict[str, Any], repo: Path
) -> dict[str, Any]:
    expected_dir = (repo / "reports" / "okx-public-response-mutability").resolve()
    if path.parent != expected_dir or path.name != f"public-response-mutability.{config['mutability_audit_sha256']}.json":
        raise MarketDataError("direct_execution_mapping_mutability_pin_mismatch")
    report = _load_json(path)
    identity = report.get("identity")
    if not isinstance(identity, dict):
        raise MarketDataError("direct_execution_mapping_mutability_identity_mismatch")
    if (
        report.get("audit_sha256") != config["mutability_audit_sha256"]
        or _digest(_canonical_json_bytes(identity)) != config["mutability_audit_sha256"]
        or report.get("audit_status") != "verified_okx_public_response_mutability_audit"
        or identity.get("capture_replay_deterministic") is not True
        or identity.get("network_recapture_byte_identical") is not False
        or identity.get("public_historical_response_mutability_observed") is not True
    ):
        raise MarketDataError("direct_execution_mapping_mutability_identity_mismatch")
    return report


def _timestamp_statuses(report: dict[str, Any], dataset_ids: list[str]) -> dict[str, str]:
    components = report["identity"]["timestamp_semantics"]["components"]
    statuses = {str(item["dataset_id"]): str(item["status"]) for item in components}
    if list(statuses) != dataset_ids or set(statuses.values()) != {"verified_open_time"}:
        raise MarketDataError("direct_execution_mapping_timestamp_semantics_mismatch")
    return statuses


def _mapping_counts(
    timestamps: tuple[pd.Timestamp, ...], rows: list[dict[str, Any]], config: dict[str, Any]
) -> dict[str, int]:
    counts = {
        "signal_timestamp_count": len(timestamps),
        "mapping_row_count": len(rows),
        "structural_valid_count": sum(row["structural_status"] == "exact_finite_open" for row in rows),
        "tail_count": sum(row["structural_status"] == "tail_outside_common_panel" for row in rows),
        "internal_missing_count": sum(
            row["structural_status"] not in {"exact_finite_open", "tail_outside_common_panel"}
            for row in rows
        ),
    }
    expected = {
        "signal_timestamp_count": config["expected_signal_timestamp_count"],
        "mapping_row_count": config["expected_mapping_row_count"],
        "structural_valid_count": config["expected_structural_valid_count"],
        "tail_count": config["expected_tail_count"],
        "internal_missing_count": 0,
    }
    if counts != expected:
        raise MarketDataError("direct_execution_mapping_count_mismatch")
    return counts


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=MAPPING_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row[field]) for field in MAPPING_FIELDS})
    return buffer.getvalue().encode("utf-8")


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return format(value, ".15g")
    return value


def _repo_root(path: Path) -> Path:
    for candidate in (path.parent, *path.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").is_file():
            return candidate.resolve()
    raise MarketDataError("direct_execution_mapping_repo_missing")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    try:
        output.relative_to((repo / "reports").resolve())
    except ValueError as exc:
        raise ValueError("direct execution mapping output must stay inside reports") from exc
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("direct_execution_mapping_invalid_json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("direct_execution_mapping_invalid_json")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"direct_execution_mapping_content_addressed_collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".direct-execution-mapping-", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
