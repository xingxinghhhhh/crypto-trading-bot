from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.data_quality import validate_ohlcv_csv
from crypto_bot.market.dataset_panel import build_dataset_panel
from crypto_bot.market.dataset_registry import (
    audit_dataset_registry,
    canonical_ohlcv_sha256,
    load_dataset_registry,
    normalize_dataset_registry_audit_paths,
)
from crypto_bot.market.okx_universe_history_extension import (
    DEFAULT_CONTRACT,
    ValidatedFrozenUniverse1hPromotion,
    validate_okx_frozen_universe_1h_promotion,
)
from crypto_bot.market.okx_universe_intake import (
    download_okx_public_history,
    okx_history_rows_to_csv_bytes,
    replay_okx_public_history,
)


SCHEMA_VERSION = 1
CAPTURE_STATUS = "complete_direct_okx_anchor_1h_history"
MIGRATION_STATUS = "verified_direct_okx_six_asset_1h_migration"
EXPECTED_SOURCE_PROMOTION_SHA256 = "36739b6fcff8ffa8150b3fdd268150cc7fc0c997d90d9fb028f3f3c096d0c24e"
EXPECTED_SOURCE_PROMOTION_REPORT_SHA256 = "68664799613953ee7dd279c0e9c2292178bef9306d1f1858fa2249deaa29e849"
EXPECTED_SOURCE_REGISTRY_SHA256 = "51ebbf3f97658fa8f36e5cd8f37742047727887957b347ba0399459b9de22975"
EXPECTED_SOURCE_PANEL_SHA256 = "b7cdebac94f728a3588cb090391fb6024cb6783ec480b1fedc830e3bd4a32054"
ANCHOR_INST_IDS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
ANCHOR_DATASET_IDS = {
    "BTC-USDT": "okx_btc_usdt_1h_direct_frozen",
    "ETH-USDT": "okx_eth_usdt_1h_direct_frozen",
    "SOL-USDT": "okx_sol_usdt_1h_direct_frozen",
}
EXISTING_DATASET_IDS = (
    "okx_knc_usdt_1h_frozen",
    "okx_swftc_usdt_1h_frozen",
    "okx_bico_usdt_1h_frozen",
)
ALL_DATASET_IDS = (*ANCHOR_DATASET_IDS.values(), *EXISTING_DATASET_IDS)


@dataclass(frozen=True)
class OkxDirectAnchor1hCaptureResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class ValidatedOkxDirectAnchor1hCapture:
    report_path: Path
    report: dict[str, Any]
    source_promotion: ValidatedFrozenUniverse1hPromotion
    histories: tuple[dict[str, Any], ...]
    artifact_paths: dict[str, Path]


@dataclass(frozen=True)
class OkxDirectSixAsset1hMigrationResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class ValidatedOkxDirectSixAsset1hMigration:
    report_path: Path
    report: dict[str, Any]
    repo_root: Path
    registry_path: Path
    panels_config_path: Path
    capture: ValidatedOkxDirectAnchor1hCapture


def capture_okx_direct_anchor_1h_history(
    source_promotion_report: str | Path,
    migration_config_path: str | Path,
    output_dir: str | Path,
    *,
    fetcher: Callable[[str], bytes] | None = None,
    request_interval_seconds: float = 0.12,
    sleeper: Callable[[float], None] = time.sleep,
) -> OkxDirectAnchor1hCaptureResult:
    promotion = validate_okx_frozen_universe_1h_promotion(source_promotion_report)
    config = load_okx_direct_six_asset_migration_config(migration_config_path)
    _validate_source_promotion(promotion, config)
    contract = _load_contract(promotion.repo_root, config)
    start_ms = _timestamp_ms(config["history_start"])
    end_ms = _timestamp_ms(config["end_open"])
    output = _reports_output(promotion.repo_root, output_dir)
    prepared: list[dict[str, Any]] = []
    for inst_id in ANCHOR_INST_IDS:
        bundle, pages, rows = download_okx_public_history(
            inst_id,
            start_ms,
            end_ms,
            okx_bar=config["okx_bar"],
            bar_duration_ms=config["bar_duration_ms"],
            fetcher=fetcher,
            request_interval_seconds=request_interval_seconds,
            sleeper=sleeper,
        )
        if len(rows) != config["expected_bar_count"] or len(pages) != config["expected_page_count"]:
            raise MarketDataError(f"okx_direct_six_history_shape_mismatch:{inst_id}")
        csv_bytes = okx_history_rows_to_csv_bytes(rows)
        quality, canonical = _validate_csv_bytes(csv_bytes, output)
        bundle_sha = _digest(bundle)
        raw_sha = _digest(csv_bytes)
        slug = inst_id.lower().replace("-", "_")
        prepared.append(
            {
                "inst_id": inst_id,
                "bundle_bytes": bundle,
                "csv_bytes": csv_bytes,
                "identity": {
                    "inst_id": inst_id,
                    "dataset_id": ANCHOR_DATASET_IDS[inst_id],
                    "symbol": inst_id.replace("-", "/"),
                    "timeframe": "1h",
                    "okx_bar": "1H",
                    "first_timestamp": config["history_start"],
                    "last_timestamp": config["end_open"],
                    "bar_count": len(rows),
                    "page_count": len(pages),
                    "pages": pages,
                    "history_bundle": {
                        "filename": f"okx-direct-anchor-1h-history.{slug}.{bundle_sha}.jsonl",
                        "sha256": bundle_sha,
                    },
                    "csv": {
                        "filename": f"okx-direct-anchor-1h-history.{slug}.{raw_sha}.csv",
                        "raw_sha256": raw_sha,
                        "canonical_sha256": canonical,
                    },
                    "quality": quality,
                    "timestamp_semantics": "verified_open_time",
                    "lineage_status": "complete_direct_okx_public",
                },
            }
        )
    identity = {
        "schema_version": SCHEMA_VERSION,
        "source_promotion": _source_promotion_identity(promotion),
        "source_snapshot": _source_snapshot_identity(promotion),
        "policy": config,
        "contract": contract,
        "histories": [item["identity"] for item in prepared],
        "claims": config["claims"],
        "safety": _safety(),
    }
    capture_sha = _digest(_canonical_json_bytes(identity))
    report = {
        "schema_version": SCHEMA_VERSION,
        "capture_sha256": capture_sha,
        "capture_status": CAPTURE_STATUS,
        "identity": identity,
        "warnings": _warnings(),
        "profitability_evidence": False,
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    exports: dict[str, str] = {}
    for item in prepared:
        history = item["identity"]
        path = output / history["history_bundle"]["filename"]
        _commit_bytes(path, item["bundle_bytes"])
        exports[f"{item['inst_id']}_history"] = str(path)
    for item in prepared:
        history = item["identity"]
        path = output / history["csv"]["filename"]
        _commit_bytes(path, item["csv_bytes"])
        exports[f"{item['inst_id']}_csv"] = str(path)
    report_path = output / f"okx-direct-anchor-1h-capture.{capture_sha}.json"
    _commit_json(report_path, report)
    exports["report"] = str(report_path)
    return OkxDirectAnchor1hCaptureResult(report, exports)


def validate_okx_direct_anchor_1h_capture(
    report_path: str | Path,
) -> ValidatedOkxDirectAnchor1hCapture:
    path = Path(report_path).resolve()
    report = _load_json(path, "okx_direct_six_invalid_capture")
    identity = _mapping(report.get("identity"), "capture_identity")
    capture_sha = _sha_value(report.get("capture_sha256"), "capture_sha256")
    if (
        path.name != f"okx-direct-anchor-1h-capture.{capture_sha}.json"
        or _digest(_canonical_json_bytes(identity)) != capture_sha
        or report.get("capture_status") != CAPTURE_STATUS
        or report.get("profitability_evidence") is not False
        or report.get("readiness_changed") is not False
    ):
        raise MarketDataError("okx_direct_six_capture_identity_mismatch")
    config = _validate_config(identity.get("policy"))
    promotion_info = _mapping(identity.get("source_promotion"), "source_promotion")
    promotion_path = _locate_dependency(
        path,
        Path("reports/okx-frozen-universe-1h-promotion")
        / str(promotion_info.get("report_filename")),
        str(promotion_info.get("report_sha256")),
    )
    promotion = validate_okx_frozen_universe_1h_promotion(promotion_path)
    _validate_source_promotion(promotion, config)
    if identity.get("source_snapshot") != _source_snapshot_identity(promotion):
        raise MarketDataError("okx_direct_six_source_snapshot_mismatch")
    if identity.get("contract") != _load_contract(promotion.repo_root, config):
        raise MarketDataError("okx_direct_six_contract_identity_mismatch")
    histories = identity.get("histories")
    if not isinstance(histories, list) or [item.get("inst_id") for item in histories] != list(ANCHOR_INST_IDS):
        raise MarketDataError("okx_direct_six_history_order_mismatch")
    start_ms = _timestamp_ms(config["history_start"])
    end_ms = _timestamp_ms(config["end_open"])
    artifacts: dict[str, Path] = {}
    for history in histories:
        inst_id = history["inst_id"]
        bundle_info = _mapping(history.get("history_bundle"), "history_bundle")
        csv_info = _mapping(history.get("csv"), "history_csv")
        bundle_path = _sibling(path, bundle_info.get("filename"))
        csv_path = _sibling(path, csv_info.get("filename"))
        if _sha256(bundle_path) != bundle_info.get("sha256"):
            raise MarketDataError(f"okx_direct_six_bundle_hash_mismatch:{inst_id}")
        rows, pages = replay_okx_public_history(
            bundle_path.read_bytes(),
            inst_id,
            start_ms,
            end_ms,
            okx_bar="1H",
            bar_duration_ms=3_600_000,
        )
        rebuilt = okx_history_rows_to_csv_bytes(rows)
        quality = validate_ohlcv_csv(csv_path, "1h")
        if (
            csv_path.read_bytes() != rebuilt
            or _sha256(csv_path) != csv_info.get("raw_sha256")
            or canonical_ohlcv_sha256(csv_path) != csv_info.get("canonical_sha256")
            or not quality.valid
            or pages != history.get("pages")
            or len(rows) != config["expected_bar_count"]
            or len(pages) != config["expected_page_count"]
            or history.get("timestamp_semantics") != "verified_open_time"
        ):
            raise MarketDataError(f"okx_direct_six_history_replay_mismatch:{inst_id}")
        artifacts[f"{inst_id}_history"] = bundle_path
        artifacts[f"{inst_id}_csv"] = csv_path
    if identity.get("claims") != config["claims"] or identity.get("safety") != _safety():
        raise MarketDataError("okx_direct_six_capture_safety_mismatch")
    return ValidatedOkxDirectAnchor1hCapture(path, report, promotion, tuple(histories), artifacts)


def freeze_okx_direct_six_asset_1h_panel(
    capture_report: str | Path,
    source_promotion_report: str | Path,
    migration_config_path: str | Path,
    output_dir: str | Path,
) -> OkxDirectSixAsset1hMigrationResult:
    capture = validate_okx_direct_anchor_1h_capture(capture_report)
    promotion = validate_okx_frozen_universe_1h_promotion(source_promotion_report)
    config = load_okx_direct_six_asset_migration_config(migration_config_path)
    _validate_source_promotion(promotion, config)
    if capture.source_promotion.report_path != promotion.report_path or capture.report["identity"]["policy"] != config:
        raise MarketDataError("okx_direct_six_capture_source_mismatch")
    repo = promotion.repo_root
    output = _reports_output(repo, output_dir)
    stable_root = (repo / config["stable_data_root"]).resolve()
    if not stable_root.is_relative_to(repo) or stable_root.is_relative_to((repo / "reports").resolve()):
        raise MarketDataError("okx_direct_six_stable_root_invalid")
    stable_root.mkdir(parents=True, exist_ok=True)
    exports: dict[str, str] = {}
    new_lineages: list[dict[str, Any]] = []
    for history in capture.histories:
        inst_id = history["inst_id"]
        source_csv = capture.artifact_paths[f"{inst_id}_csv"]
        raw_sha = history["csv"]["raw_sha256"]
        destination_relative = Path(config["stable_data_root"]) / f"{history['dataset_id']}.{raw_sha}.csv"
        destination = repo / destination_relative
        _commit_bytes(destination, source_csv.read_bytes())
        if not validate_ohlcv_csv(destination, "1h").valid:
            raise MarketDataError(f"okx_direct_six_promoted_quality_failed:{inst_id}")
        lineage_identity = {
            "schema_version": 1,
            "dataset_id": history["dataset_id"],
            "inst_id": inst_id,
            "source_capture_sha256": capture.report["capture_sha256"],
            "source_report_sha256": _sha256(capture.report_path),
            "source_history_bundle_sha256": history["history_bundle"]["sha256"],
            "source_csv_sha256": raw_sha,
            "destination_repo_relative_path": destination_relative.as_posix(),
            "destination_raw_sha256": _sha256(destination),
            "destination_canonical_sha256": canonical_ohlcv_sha256(destination),
            "timestamp_semantics": "verified_open_time",
            "lineage_status": "complete_direct_okx_public",
            "claims": config["claims"],
        }
        lineage_sha = _digest(_canonical_json_bytes(lineage_identity))
        lineage_relative = Path(config["stable_data_root"]) / (
            f"lineage.{history['dataset_id']}.{lineage_sha}.json"
        )
        lineage_path = repo / lineage_relative
        lineage = lineage_identity | {
            "lineage_sha256": lineage_sha,
            "lineage_repo_relative_path": lineage_relative.as_posix(),
            "source_kind": "new_anchor_capture",
        }
        _commit_json(lineage_path, lineage_identity | {"lineage_sha256": lineage_sha})
        new_lineages.append(lineage)
        exports[f"{inst_id}_csv"] = str(destination)
        exports[f"{inst_id}_lineage"] = str(lineage_path)
    existing_lineages = _validated_existing_lineages(promotion, config)
    all_lineages = [*new_lineages, *existing_lineages]
    source_registry = load_dataset_registry(promotion.registry_path)
    history_by_dataset = {item["dataset_id"]: item for item in capture.histories}
    registry_entries: list[dict[str, Any]] = [
        _new_registry_entry(item, history_by_dataset[item["dataset_id"]])
        for item in new_lineages
    ]
    registry_entries += [
        {"dataset_id": dataset_id, **source_registry.get(dataset_id).declared_metadata()}
        for dataset_id in EXISTING_DATASET_IDS
    ]
    registry_payload = {
        "schema_version": 1,
        "datasets": registry_entries,
    }
    if [item["dataset_id"] for item in registry_entries] != list(ALL_DATASET_IDS):
        raise MarketDataError("okx_direct_six_registry_dataset_order_mismatch")
    registry_bytes = yaml.safe_dump(registry_payload, sort_keys=False, allow_unicode=True).encode("utf-8")
    registry_sha = _digest(registry_bytes)
    registry_path = repo / f"okx-direct-six-registry.{registry_sha}.yaml"
    _commit_bytes(registry_path, registry_bytes)
    registry_audit = audit_dataset_registry(registry_path)
    if registry_audit.get("valid") is not True or registry_audit.get("dataset_count") != 6:
        raise MarketDataError("okx_direct_six_registry_audit_failed")
    registry_audit_path = output / f"okx-direct-six-registry-audit.{_digest(_pretty_json_bytes(registry_audit))}.json"
    _commit_json(registry_audit_path, registry_audit)
    panel_payload = {
        "schema_version": 1,
        "panels": [
            {
                "panel_id": config["target_panel_id"],
                "dataset_ids": list(ALL_DATASET_IDS),
                "alignment": "inner_exact",
            }
        ],
    }
    panel_bytes = yaml.safe_dump(panel_payload, sort_keys=False, allow_unicode=True).encode("utf-8")
    panel_config_sha = _digest(panel_bytes)
    panels_path = repo / f"okx-direct-six-panels.{panel_config_sha}.yaml"
    _commit_bytes(panels_path, panel_bytes)
    panel = build_dataset_panel(registry_path, panels_path, config["target_panel_id"])
    _validate_direct_panel(panel.report, config)
    panel_audit_path = output / f"okx-direct-six-panel.{panel.report['panel_sha256']}.json"
    _commit_json(panel_audit_path, panel.report)
    semantics = {
        "generic_panel_audit_status": "unverified",
        "aggregate_status": "verified_open_time",
        "timestamp_semantics_uniform": True,
        "components": [
            {"dataset_id": dataset_id, "status": "verified_open_time"}
            for dataset_id in ALL_DATASET_IDS
        ],
    }
    identity = {
        "schema_version": SCHEMA_VERSION,
        "source_promotion": _source_promotion_identity(promotion),
        "capture_sha256": capture.report["capture_sha256"],
        "capture_report_sha256": _sha256(capture.report_path),
        "policy": config,
        "contract": capture.report["identity"]["contract"],
        "datasets": all_lineages,
        "registry": {
            "filename": registry_path.name,
            "sha256": registry_sha,
            "audit_filename": registry_audit_path.name,
            "audit_sha256": _sha256(registry_audit_path),
        },
        "panel": {
            "config_filename": panels_path.name,
            "config_sha256": panel_config_sha,
            "panel_id": config["target_panel_id"],
            "panel_sha256": panel.report["panel_sha256"],
            "audit_filename": panel_audit_path.name,
            "audit_sha256": _sha256(panel_audit_path),
            "alignment_summary": panel.report["alignment_summary"],
        },
        "timestamp_semantics": semantics,
        "claims": config["claims"],
        "safety": _safety(),
    }
    migration_sha = _digest(_canonical_json_bytes(identity))
    report = {
        "schema_version": SCHEMA_VERSION,
        "migration_sha256": migration_sha,
        "migration_status": MIGRATION_STATUS,
        "identity": identity,
        "warnings": _warnings(),
        "profitability_evidence": False,
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    marker = output / f"okx-direct-six-migration.{migration_sha}.json"
    _commit_json(marker, report)
    exports |= {
        "registry": str(registry_path),
        "registry_audit": str(registry_audit_path),
        "panels": str(panels_path),
        "panel_audit": str(panel_audit_path),
        "report": str(marker),
    }
    return OkxDirectSixAsset1hMigrationResult(report, exports)


def validate_okx_direct_six_asset_1h_migration(
    report_path: str | Path,
) -> ValidatedOkxDirectSixAsset1hMigration:
    path = Path(report_path).resolve()
    report = _load_json(path, "okx_direct_six_invalid_migration")
    identity = _mapping(report.get("identity"), "migration_identity")
    migration_sha = _sha_value(report.get("migration_sha256"), "migration_sha256")
    if (
        path.name != f"okx-direct-six-migration.{migration_sha}.json"
        or _digest(_canonical_json_bytes(identity)) != migration_sha
        or report.get("migration_status") != MIGRATION_STATUS
        or report.get("profitability_evidence") is not False
        or report.get("readiness_changed") is not False
    ):
        raise MarketDataError("okx_direct_six_migration_identity_mismatch")
    source = _mapping(identity.get("source_promotion"), "source_promotion")
    source_path = _locate_dependency(
        path,
        Path("reports/okx-frozen-universe-1h-promotion") / str(source.get("report_filename")),
        str(source.get("report_sha256")),
    )
    promotion = validate_okx_frozen_universe_1h_promotion(source_path)
    repo = promotion.repo_root
    config = _validate_config(identity.get("policy"))
    _validate_source_promotion(promotion, config)
    capture_path = _locate_dependency(
        path,
        Path("reports/okx-direct-anchor-1h-capture")
        / f"okx-direct-anchor-1h-capture.{identity.get('capture_sha256')}.json",
        str(identity.get("capture_report_sha256")),
    )
    capture = validate_okx_direct_anchor_1h_capture(capture_path)
    registry_info = _mapping(identity.get("registry"), "registry")
    panel_info = _mapping(identity.get("panel"), "panel")
    registry_path = _repo_file(repo, str(registry_info.get("filename")))
    panels_path = _repo_file(repo, str(panel_info.get("config_filename")))
    if _sha256(registry_path) != registry_info.get("sha256") or _sha256(panels_path) != panel_info.get("config_sha256"):
        raise MarketDataError("okx_direct_six_config_hash_mismatch")
    registry_audit_path = _sibling(path, registry_info.get("audit_filename"))
    panel_audit_path = _sibling(path, panel_info.get("audit_filename"))
    frozen_registry_audit = normalize_dataset_registry_audit_paths(
        _load_json(registry_audit_path, "okx_direct_six_invalid_registry_audit"), repo
    )
    observed_registry_audit = normalize_dataset_registry_audit_paths(
        audit_dataset_registry(registry_path), repo
    )
    if (
        _sha256(registry_audit_path) != registry_info.get("audit_sha256")
        or frozen_registry_audit != observed_registry_audit
    ):
        raise MarketDataError("okx_direct_six_registry_replay_mismatch")
    panel = build_dataset_panel(registry_path, panels_path, config["target_panel_id"])
    if (
        _sha256(panel_audit_path) != panel_info.get("audit_sha256")
        or _load_json(panel_audit_path, "okx_direct_six_invalid_panel_audit") != panel.report
    ):
        raise MarketDataError("okx_direct_six_panel_replay_mismatch")
    _validate_direct_panel(panel.report, config)
    datasets = identity.get("datasets")
    if not isinstance(datasets, list) or [item.get("dataset_id") for item in datasets] != list(ALL_DATASET_IDS):
        raise MarketDataError("okx_direct_six_dataset_identity_mismatch")
    for item in datasets:
        lineage_path = _repo_file(repo, str(item.get("lineage_repo_relative_path")))
        destination = _repo_file(repo, str(item.get("destination_repo_relative_path")))
        lineage = _load_json(lineage_path, "okx_direct_six_invalid_lineage")
        lineage_identity = {key: value for key, value in lineage.items() if key != "lineage_sha256"}
        if (
            _sha256(destination) != item.get("destination_raw_sha256")
            or canonical_ohlcv_sha256(destination) != item.get("destination_canonical_sha256")
            or lineage.get("lineage_sha256") != item.get("lineage_sha256")
            or _digest(_canonical_json_bytes(lineage_identity)) != item.get("lineage_sha256")
            or item.get("timestamp_semantics") != "verified_open_time"
        ):
            raise MarketDataError("okx_direct_six_lineage_mismatch")
    semantics = _mapping(identity.get("timestamp_semantics"), "timestamp_semantics")
    if (
        semantics.get("aggregate_status") != "verified_open_time"
        or semantics.get("timestamp_semantics_uniform") is not True
        or identity.get("claims") != config["claims"]
        or identity.get("safety") != _safety()
    ):
        raise MarketDataError("okx_direct_six_safety_mismatch")
    return ValidatedOkxDirectSixAsset1hMigration(
        path, report, repo, registry_path, panels_path, capture
    )


def load_okx_direct_six_asset_migration_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"OKX direct-six migration config not found: {config_path}")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"OKX direct-six migration config YAML is invalid: {config_path}") from exc
    return _validate_config(value)


def format_okx_direct_anchor_1h_capture(result: OkxDirectAnchor1hCaptureResult) -> str:
    return "\n".join(
        [
            f"capture_status: {result.report['capture_status']}",
            f"capture_sha256: {result.report['capture_sha256']}",
            "dataset_count: 3",
            f"bar_count_per_dataset: {result.report['identity']['policy']['expected_bar_count']}",
            "private_api_used: false",
            "readiness_changed: false",
        ]
    )


def format_okx_direct_six_asset_1h_migration(
    result: OkxDirectSixAsset1hMigrationResult,
) -> str:
    identity = result.report["identity"]
    return "\n".join(
        [
            f"migration_status: {result.report['migration_status']}",
            f"migration_sha256: {result.report['migration_sha256']}",
            f"panel_sha256: {identity['panel']['panel_sha256']}",
            f"intersection_bar_count: {identity['panel']['alignment_summary']['intersection_bar_count']}",
            "timestamp_semantics_uniform: true",
            "profitability_evidence: false",
            "readiness_changed: false",
        ]
    )


def _validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("OKX direct-six migration config must be a mapping")
    frozen_path = Path(__file__).resolve().parents[3] / "config.okx-direct-six-1h-migration.example.yaml"
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("OKX direct-six migration config must equal the frozen config")
    return json.loads(json.dumps(value, sort_keys=True))


def _validate_source_promotion(
    promotion: ValidatedFrozenUniverse1hPromotion,
    config: dict[str, Any],
) -> None:
    if (
        promotion.report.get("promotion_sha256") != EXPECTED_SOURCE_PROMOTION_SHA256
        or promotion.report.get("promotion_sha256") != config["source_promotion_sha256"]
        or _sha256(promotion.report_path) != EXPECTED_SOURCE_PROMOTION_REPORT_SHA256
        or promotion.report["identity"]["policy"].get("source_snapshot_received_at")
        != config["source_snapshot_received_at"]
        or promotion.report["identity"]["promoted_registry"].get("sha256")
        != EXPECTED_SOURCE_REGISTRY_SHA256
        or promotion.report["identity"]["promoted_panel"].get("panel_sha256")
        != EXPECTED_SOURCE_PANEL_SHA256
    ):
        raise MarketDataError("okx_direct_six_source_promotion_mismatch")


def _source_promotion_identity(promotion: ValidatedFrozenUniverse1hPromotion) -> dict[str, Any]:
    return {
        "promotion_sha256": promotion.report["promotion_sha256"],
        "report_filename": promotion.report_path.name,
        "report_sha256": _sha256(promotion.report_path),
        "registry_sha256": promotion.report["identity"]["promoted_registry"]["sha256"],
        "panel_sha256": promotion.report["identity"]["promoted_panel"]["panel_sha256"],
    }


def _source_snapshot_identity(promotion: ValidatedFrozenUniverse1hPromotion) -> dict[str, Any]:
    policy = promotion.report["identity"]["policy"]
    return {
        "received_at": policy["source_snapshot_received_at"],
        "membership_source": "2026-08-02_current_live_snapshot",
        "selected_direct_assets": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT"],
    }


def _load_contract(repo: Path, config: dict[str, Any]) -> dict[str, Any]:
    path = _repo_file(repo, DEFAULT_CONTRACT)
    payload = _load_json(path, "okx_direct_six_invalid_contract")
    history = _mapping(payload.get("history_candles"), "contract_history")
    if (
        payload.get("provider") != "okx"
        or payload.get("scope") != "public_market_data_only"
        or payload.get("private_api_required") is not False
        or payload.get("trading_api_required") is not False
        or history.get("endpoint") != "https://www.okx.com/api/v5/market/history-candles"
        or history.get("allowed_bars") != ["1H", "4H"]
        or history.get("maximum_limit") != config["limit"]
        or history.get("after_semantics") != "records_strictly_earlier_than_ts"
        or history.get("response_order") != "newest_first"
        or history.get("response_shape")
        != ["ts", "o", "h", "l", "c", "vol", "volCcy", "volCcyQuote", "confirm"]
        or history.get("timestamp_meaning") != "bar_open_time"
        or history.get("confirmed_value") != "1"
    ):
        raise MarketDataError("okx_direct_six_contract_mismatch")
    return {
        "filename": path.name,
        "sha256": _sha256(path),
        "official_url": payload["official_url"],
    }


def _validated_existing_lineages(
    promotion: ValidatedFrozenUniverse1hPromotion,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    repo = promotion.repo_root
    by_id = {item["dataset_id"]: item for item in promotion.report["identity"]["datasets"]}
    result = []
    for dataset_id in EXISTING_DATASET_IDS:
        item = by_id.get(dataset_id)
        if item is None:
            raise MarketDataError("okx_direct_six_existing_lineage_missing")
        lineage_path = _repo_file(repo, item["lineage_repo_relative_path"])
        destination = _repo_file(repo, item["destination_repo_relative_path"])
        lineage = _load_json(lineage_path, "okx_direct_six_invalid_existing_lineage")
        lineage_identity = {key: value for key, value in lineage.items() if key != "lineage_sha256"}
        if (
            lineage.get("lineage_sha256") != item["lineage_sha256"]
            or _digest(_canonical_json_bytes(lineage_identity)) != item["lineage_sha256"]
            or _sha256(destination) != item["destination_raw_sha256"]
            or canonical_ohlcv_sha256(destination) != item["destination_canonical_sha256"]
            or item.get("timestamp_semantics") != "verified_open_time"
        ):
            raise MarketDataError("okx_direct_six_existing_lineage_mismatch")
        result.append(
            item
            | {
                "lineage_status": "complete_direct_okx_public",
                "source_kind": "reused_existing_direct_capture",
                "claims": config["claims"],
            }
        )
    return result


def _new_registry_entry(lineage: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_id": lineage["dataset_id"],
        "symbol": history["symbol"],
        "timeframe": "1h",
        "path": lineage["destination_repo_relative_path"],
        "source": {
            "status": "verified",
            "provider": "okx_public_history_candles",
            "evidence": [lineage["lineage_repo_relative_path"]],
            "timestamp_semantics": "verified_open_time",
        },
        "quality": {"validator": "validate_ohlcv_csv"},
        "expected": {
            "bar_count": history["bar_count"],
            "first_timestamp": history["first_timestamp"],
            "last_timestamp": history["last_timestamp"],
            "raw_sha256": lineage["destination_raw_sha256"],
            "canonical_sha256": lineage["destination_canonical_sha256"],
        },
    }


def _validate_direct_panel(report: dict[str, Any], config: dict[str, Any]) -> None:
    expected = config["expected_panel"]
    summary = report["alignment_summary"]
    for key in (
        "common_first_timestamp",
        "common_last_timestamp",
        "intersection_bar_count",
        "union_bar_count",
    ):
        if summary.get(key) != expected[key]:
            raise MarketDataError(f"okx_direct_six_panel_{key}_mismatch")
    if summary.get("coverage_rate") != 1.0:
        raise MarketDataError("okx_direct_six_panel_coverage_mismatch")
    components = {item["dataset_id"]: item for item in report["datasets"]}
    if set(components) != set(ALL_DATASET_IDS):
        raise MarketDataError("okx_direct_six_panel_membership_mismatch")
    for dataset_id, item in components.items():
        if (
            item["dropped_before_common_start"] != 0
            or item["dropped_after_common_end"] != 0
            or item["missing_inside_common_window"] != 0
        ):
            raise MarketDataError(f"okx_direct_six_panel_boundary_mismatch:{dataset_id}")


def _validate_csv_bytes(content: bytes, output: Path) -> tuple[dict[str, Any], str]:
    temporary = output / f".validate-direct-1h.{uuid4().hex}.csv"
    try:
        temporary.write_bytes(content)
        quality = validate_ohlcv_csv(temporary, "1h")
        canonical = canonical_ohlcv_sha256(temporary)
        if not quality.valid or canonical is None:
            raise MarketDataError("okx_direct_six_csv_quality_failed")
        payload = quality.to_dict()
        payload.pop("csv_path", None)
        return payload, canonical
    finally:
        temporary.unlink(missing_ok=True)


def _reports_output(repo: Path, value: str | Path) -> Path:
    raw = Path(value)
    path = (repo / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if not path.is_relative_to((repo / "reports").resolve()):
        raise ValueError("OKX direct-six output must stay inside reports")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _repo_file(repo: Path, value: str | Path) -> Path:
    raw = Path(value)
    path = (repo / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if not path.is_relative_to(repo.resolve()) or not path.is_file():
        raise FileNotFoundError(f"repository file not found: {value}")
    return path


def _locate_dependency(report: Path, relative: Path, expected_sha256: str) -> Path:
    for parent in report.parents:
        candidate = (parent / relative).resolve()
        if candidate.is_file() and _sha256(candidate) == expected_sha256:
            return candidate
    raise MarketDataError("okx_direct_six_dependency_missing")


def _sibling(report: Path, filename: Any) -> Path:
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise MarketDataError("okx_direct_six_artifact_path_escape")
    path = (report.parent / filename).resolve()
    if path.parent != report.parent or not path.is_file():
        raise MarketDataError("okx_direct_six_artifact_missing")
    return path


def _timestamp_ms(value: str) -> int:
    import pandas as pd

    parsed = pd.Timestamp(value)
    if parsed.tz is None or str(parsed.tz) not in {"UTC", "UTC+00:00"}:
        raise ValueError("timestamp must be UTC")
    return int(parsed.timestamp() * 1000)


def _safety() -> dict[str, bool]:
    return {
        "public_api_only": True,
        "private_api_used": False,
        "trading_api_used": False,
        "instrument_snapshot_refetched": False,
        "candidate_reselection": False,
        "legacy_datasets_modified": False,
        "pnl_computed": False,
        "readiness_changed": False,
    }


def _warnings() -> list[str]:
    return [
        "current_live_convenience_snapshot_membership_only",
        "historical_point_in_time_membership_not_proven",
        "survivorship_bias_not_resolved",
        "delisted_assets_not_recovered",
        "legacy_datasets_and_semantics_unchanged",
        "no_research_oos_cost_pnl_or_readiness_claim",
    ]


def _commit_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"content_addressed_collision:{path.name}")
        return
    temporary = path.with_name(f".{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _commit_json(path: Path, payload: dict[str, Any]) -> None:
    _commit_bytes(path, _pretty_json_bytes(payload))


def _load_json(path: Path, error: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError(error) from exc
    if not isinstance(value, dict):
        raise MarketDataError(error)
    return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MarketDataError(f"okx_direct_six_invalid_{name}")
    return value


def _sha_value(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise MarketDataError(f"okx_direct_six_invalid_{name}")
    return value


def _sha256(path: Path) -> str:
    return _digest(path.read_bytes())


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
