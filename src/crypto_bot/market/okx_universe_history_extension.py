from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.data_quality import validate_ohlcv_csv
from crypto_bot.market.dataset_panel import build_dataset_panel, load_dataset_panel_config
from crypto_bot.market.dataset_promotion import (
    ValidatedDatasetPromotion,
    validate_dataset_promotion_report,
)
from crypto_bot.market.dataset_registry import (
    audit_dataset_registry,
    canonical_ohlcv_sha256,
    load_dataset_registry,
    normalize_dataset_registry_audit_paths,
)
from crypto_bot.market.okx_universe_intake import (
    download_okx_public_history,
    okx_history_rows_to_csv_bytes,
    replay_okx_public_history,
    validate_okx_universe_capture,
)


EXTENSION_SCHEMA_VERSION = 1
CAPTURE_SCHEMA_VERSION = 1
PROMOTION_SCHEMA_VERSION = 1
DEFAULT_CONTRACT = "docs/evidence/okx_public_frozen_history_contract_v1.json"
EXPECTED_SELECTED = ("KNC-USDT", "SWFTC-USDT", "BICO-USDT")
EXPECTED_BASE_DATASETS = ("btc_usdt_1h_v1", "eth_usdt_1h_v1", "sol_usdt_1h_v1")
DATASET_IDS = {
    "KNC-USDT": "okx_knc_usdt_1h_frozen",
    "SWFTC-USDT": "okx_swftc_usdt_1h_frozen",
    "BICO-USDT": "okx_bico_usdt_1h_frozen",
}


@dataclass(frozen=True)
class FrozenUniverse1hCaptureResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class ValidatedFrozenUniverse1hCapture:
    report_path: Path
    report: dict[str, Any]
    source_promotion: ValidatedDatasetPromotion
    histories: tuple[dict[str, Any], ...]
    artifact_paths: dict[str, Path]


@dataclass(frozen=True)
class FrozenUniverse1hPromotionResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class ValidatedFrozenUniverse1hPromotion:
    report_path: Path
    report: dict[str, Any]
    repo_root: Path
    registry_path: Path
    panels_config_path: Path
    capture: ValidatedFrozenUniverse1hCapture


def capture_okx_frozen_universe_1h_history(
    source_promotion_report: str | Path,
    policy_path: str | Path,
    output_dir: str | Path,
    *,
    contract_path: str | Path = DEFAULT_CONTRACT,
    fetcher: Callable[[str], bytes] | None = None,
    request_interval_seconds: float = 0.12,
    sleeper: Callable[[float], None] = time.sleep,
) -> FrozenUniverse1hCaptureResult:
    policy = _load_policy(policy_path)
    promotion = validate_dataset_promotion_report(source_promotion_report)
    source_capture = _validate_source_promotion_selection(promotion, policy)
    contract = _load_contract(promotion.repo_root, contract_path, policy)
    start_ms = _timestamp_ms(policy["history_start"])
    end_ms = _timestamp_ms(policy["end_open"])
    output = _reports_output(promotion.repo_root, output_dir)
    prepared: list[dict[str, Any]] = []
    for inst_id in policy["selected_inst_ids"]:
        bundle, pages, rows = download_okx_public_history(
            inst_id,
            start_ms,
            end_ms,
            okx_bar=policy["okx_bar"],
            bar_duration_ms=policy["bar_duration_ms"],
            fetcher=fetcher,
            request_interval_seconds=request_interval_seconds,
            sleeper=sleeper,
        )
        if len(rows) != policy["expected_bar_count"] or len(pages) != policy["expected_page_count"]:
            raise MarketDataError(f"okx_1h_extension_history_shape_mismatch:{inst_id}")
        csv_bytes = okx_history_rows_to_csv_bytes(rows)
        quality, canonical = _validate_csv_content(csv_bytes, output, "1h")
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
                    "dataset_id": DATASET_IDS[inst_id],
                    "symbol": inst_id.replace("-", "/"),
                    "timeframe": "1h",
                    "okx_bar": "1H",
                    "first_timestamp": policy["history_start"],
                    "last_timestamp": policy["end_open"],
                    "bar_count": len(rows),
                    "page_count": len(pages),
                    "pages": pages,
                    "history_bundle": {
                        "filename": f"okx-frozen-1h-history.{slug}.{bundle_sha}.jsonl",
                        "sha256": bundle_sha,
                    },
                    "csv": {
                        "filename": f"okx-frozen-1h-history.{slug}.{raw_sha}.csv",
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
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "source_promotion": _source_promotion_identity(promotion),
        "source_snapshot": {
            "capture_sha256": source_capture.capture["capture_sha256"],
            "report_sha256": _sha256(source_capture.capture_path),
            "received_at": source_capture.capture["identity"]["snapshot"]["received_at"],
            "selected_inst_ids": list(source_capture.selected_inst_ids),
        },
        "policy": policy,
        "contract": contract,
        "histories": [item["identity"] for item in prepared],
        "claims": policy["claims"],
        "safety": _safety(),
    }
    capture_sha = _digest(_canonical_json_bytes(identity))
    report = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "capture_sha256": capture_sha,
        "capture_status": "complete_frozen_convenience_universe_1h_history",
        "identity": identity,
        "warnings": _warnings(),
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    exports: dict[str, str] = {}
    for item in prepared:
        history = item["identity"]
        bundle_path = output / history["history_bundle"]["filename"]
        _commit_bytes(bundle_path, item["bundle_bytes"])
        exports[f"{item['inst_id']}_history"] = str(bundle_path)
    for item in prepared:
        history = item["identity"]
        csv_path = output / history["csv"]["filename"]
        _commit_bytes(csv_path, item["csv_bytes"])
        exports[f"{item['inst_id']}_csv"] = str(csv_path)
    report_path = output / f"okx-frozen-universe-1h-capture.{capture_sha}.json"
    _commit_json(report_path, report)
    exports["report"] = str(report_path)
    return FrozenUniverse1hCaptureResult(report=report, export_paths=exports)


def validate_okx_frozen_universe_1h_capture(
    report_path: str | Path,
) -> ValidatedFrozenUniverse1hCapture:
    path = Path(report_path).resolve()
    report = _load_json(path, "okx_1h_extension_invalid_capture")
    identity = _mapping(report.get("identity"), "capture_identity")
    capture_sha = _sha_value(report.get("capture_sha256"), "capture_sha256")
    if (
        path.name != f"okx-frozen-universe-1h-capture.{capture_sha}.json"
        or _digest(_canonical_json_bytes(identity)) != capture_sha
        or report.get("capture_status") != "complete_frozen_convenience_universe_1h_history"
        or report.get("readiness_changed") is not False
    ):
        raise MarketDataError("okx_1h_extension_capture_identity_mismatch")
    policy = _validate_policy(identity.get("policy"))
    promotion_info = _mapping(identity.get("source_promotion"), "source_promotion")
    promotion_path = _locate_repo_dependency(
        path,
        Path("reports/okx-universe-promotion") / str(promotion_info["report_filename"]),
        str(promotion_info["report_sha256"]),
    )
    promotion = validate_dataset_promotion_report(promotion_path)
    source_capture = _validate_source_promotion_selection(promotion, policy)
    source_snapshot = _mapping(identity.get("source_snapshot"), "source_snapshot")
    if source_snapshot != {
        "capture_sha256": source_capture.capture["capture_sha256"],
        "report_sha256": _sha256(source_capture.capture_path),
        "received_at": source_capture.capture["identity"]["snapshot"]["received_at"],
        "selected_inst_ids": list(source_capture.selected_inst_ids),
    }:
        raise MarketDataError("okx_1h_extension_source_snapshot_mismatch")
    _validate_contract_identity(promotion.repo_root, identity.get("contract"), policy)
    start_ms = _timestamp_ms(policy["history_start"])
    end_ms = _timestamp_ms(policy["end_open"])
    histories = identity.get("histories")
    if not isinstance(histories, list) or [item.get("inst_id") for item in histories] != list(EXPECTED_SELECTED):
        raise MarketDataError("okx_1h_extension_history_order_mismatch")
    artifacts: dict[str, Path] = {}
    for history in histories:
        inst_id = history["inst_id"]
        bundle_info = _mapping(history.get("history_bundle"), "history_bundle")
        csv_info = _mapping(history.get("csv"), "history_csv")
        bundle_path = _sibling(path, bundle_info["filename"])
        csv_path = _sibling(path, csv_info["filename"])
        if _sha256(bundle_path) != bundle_info["sha256"]:
            raise MarketDataError(f"okx_1h_extension_bundle_hash_mismatch:{inst_id}")
        rows, pages = replay_okx_public_history(
            bundle_path.read_bytes(),
            inst_id,
            start_ms,
            end_ms,
            okx_bar="1H",
            bar_duration_ms=3_600_000,
        )
        rebuilt = okx_history_rows_to_csv_bytes(rows)
        if csv_path.read_bytes() != rebuilt or _sha256(csv_path) != csv_info["raw_sha256"]:
            raise MarketDataError(f"okx_1h_extension_csv_replay_mismatch:{inst_id}")
        quality = validate_ohlcv_csv(csv_path, "1h")
        canonical = canonical_ohlcv_sha256(csv_path)
        if (
            not quality.valid
            or canonical != csv_info["canonical_sha256"]
            or pages != history.get("pages")
            or len(rows) != policy["expected_bar_count"]
            or len(pages) != policy["expected_page_count"]
        ):
            raise MarketDataError(f"okx_1h_extension_history_validation_mismatch:{inst_id}")
        artifacts[f"{inst_id}_history"] = bundle_path
        artifacts[f"{inst_id}_csv"] = csv_path
    if identity.get("claims") != policy["claims"] or identity.get("safety") != _safety():
        raise MarketDataError("okx_1h_extension_capture_safety_mismatch")
    return ValidatedFrozenUniverse1hCapture(path, report, promotion, tuple(histories), artifacts)


def promote_okx_frozen_universe_1h_panel(
    capture_report: str | Path,
    base_registry_path: str | Path,
    base_panels_config_path: str | Path,
    promotion_config_path: str | Path,
    output_dir: str | Path,
) -> FrozenUniverse1hPromotionResult:
    validated = validate_okx_frozen_universe_1h_capture(capture_report)
    promotion = validated.source_promotion
    policy = _load_policy(promotion_config_path)
    if policy != validated.report["identity"]["policy"]:
        raise MarketDataError("okx_1h_extension_promotion_policy_mismatch")
    repo = promotion.repo_root
    base_registry = _repo_file(repo, base_registry_path)
    base_panels = _repo_file(repo, base_panels_config_path)
    source_identity = _mapping(promotion.report["identity"].get("inputs"), "promotion_inputs")
    if (
        _sha256(base_registry) != source_identity["base_registry"]["sha256"]
        or _sha256(base_panels) != source_identity["base_panels_config"]["sha256"]
    ):
        raise MarketDataError("okx_1h_extension_base_identity_mismatch")
    registry = load_dataset_registry(base_registry)
    panels = load_dataset_panel_config(base_panels)
    base_panel = panels.get(policy["base_panel_id"])
    if base_panel.dataset_ids != EXPECTED_BASE_DATASETS:
        raise MarketDataError("okx_1h_extension_base_panel_mismatch")
    output = _reports_output(repo, output_dir)
    stable_root = (repo / policy["stable_data_root"]).resolve()
    if not stable_root.is_relative_to(repo) or stable_root.is_relative_to((repo / "reports").resolve()):
        raise MarketDataError("okx_1h_extension_stable_root_invalid")
    stable_root.mkdir(parents=True, exist_ok=True)
    exports: dict[str, str] = {}
    promoted_datasets: list[dict[str, Any]] = []
    for history in validated.histories:
        inst_id = history["inst_id"]
        source_csv = validated.artifact_paths[f"{inst_id}_csv"]
        raw_sha = history["csv"]["raw_sha256"]
        destination_relative = Path(policy["stable_data_root"]) / f"{history['dataset_id']}.{raw_sha}.csv"
        destination = (repo / destination_relative).resolve()
        _commit_bytes(destination, source_csv.read_bytes())
        if not validate_ohlcv_csv(destination, "1h").valid:
            raise MarketDataError(f"okx_1h_extension_promoted_quality_failed:{inst_id}")
        lineage_identity = {
            "schema_version": 1,
            "dataset_id": history["dataset_id"],
            "inst_id": inst_id,
            "source_capture_sha256": validated.report["capture_sha256"],
            "source_report_sha256": _sha256(validated.report_path),
            "source_history_bundle_sha256": history["history_bundle"]["sha256"],
            "source_csv_sha256": raw_sha,
            "destination_repo_relative_path": destination_relative.as_posix(),
            "destination_raw_sha256": _sha256(destination),
            "destination_canonical_sha256": canonical_ohlcv_sha256(destination),
            "timestamp_semantics": "verified_open_time",
            "claims": policy["claims"],
        }
        lineage_sha = _digest(_canonical_json_bytes(lineage_identity))
        lineage_relative = Path(policy["stable_data_root"]) / f"lineage.{history['dataset_id']}.{lineage_sha}.json"
        lineage_path = repo / lineage_relative
        _commit_json(lineage_path, lineage_identity | {"lineage_sha256": lineage_sha})
        promoted_datasets.append(
            lineage_identity
            | {
                "lineage_sha256": lineage_sha,
                "lineage_repo_relative_path": lineage_relative.as_posix(),
            }
        )
        exports[f"{inst_id}_csv"] = str(destination)
        exports[f"{inst_id}_lineage"] = str(lineage_path)
    registry_payload = {
        "schema_version": 1,
        "datasets": [
            {"dataset_id": dataset_id, **registry.get(dataset_id).declared_metadata()}
            for dataset_id in EXPECTED_BASE_DATASETS
        ]
        + [
            _registry_entry(item, next(h for h in validated.histories if h["dataset_id"] == item["dataset_id"]))
            for item in promoted_datasets
        ],
    }
    registry_bytes = yaml.safe_dump(registry_payload, sort_keys=False, allow_unicode=True).encode("utf-8")
    registry_sha = _digest(registry_bytes)
    promoted_registry = repo / f"promoted-registry-1h.{registry_sha}.yaml"
    _commit_bytes(promoted_registry, registry_bytes)
    registry_audit = audit_dataset_registry(promoted_registry)
    if not registry_audit.get("valid") or registry_audit.get("dataset_count") != 6:
        raise MarketDataError("okx_1h_extension_registry_audit_failed")
    registry_audit_sha = _digest(_pretty_json_bytes(registry_audit))
    registry_audit_path = output / f"promoted-registry-1h-audit.{registry_audit_sha}.json"
    _commit_json(registry_audit_path, registry_audit)
    panel_payload = {
        "schema_version": 1,
        "panels": [
            {
                "panel_id": policy["target_panel_id"],
                "dataset_ids": [*EXPECTED_BASE_DATASETS, *(DATASET_IDS[item] for item in EXPECTED_SELECTED)],
                "alignment": "inner_exact",
            }
        ],
    }
    panel_bytes = yaml.safe_dump(panel_payload, sort_keys=False, allow_unicode=True).encode("utf-8")
    panel_config_sha = _digest(panel_bytes)
    promoted_panels = repo / f"promoted-panels-1h.{panel_config_sha}.yaml"
    _commit_bytes(promoted_panels, panel_bytes)
    panel = build_dataset_panel(promoted_registry, promoted_panels, policy["target_panel_id"])
    _validate_panel(panel.report, policy)
    panel_audit_path = output / f"promoted-panel-1h.{panel.report['panel_sha256']}.json"
    _commit_json(panel_audit_path, panel.report)
    semantics = {
        "generic_panel_audit_status": "unverified",
        "aggregate_status": "mixed_unverified",
        "timestamp_semantics_uniform": False,
        "components": [
            {"dataset_id": "btc_usdt_1h_v1", "status": "partial_unverified"},
            {"dataset_id": "eth_usdt_1h_v1", "status": "unknown"},
            {"dataset_id": "sol_usdt_1h_v1", "status": "unknown"},
            *({"dataset_id": DATASET_IDS[item], "status": "verified_open_time"} for item in EXPECTED_SELECTED),
        ],
    }
    identity = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "capture_sha256": validated.report["capture_sha256"],
        "capture_report_sha256": _sha256(validated.report_path),
        "source_promotion": _source_promotion_identity(promotion),
        "policy": policy,
        "base_registry_sha256": _sha256(base_registry),
        "base_panels_config_sha256": _sha256(base_panels),
        "datasets": promoted_datasets,
        "promoted_registry": {
            "filename": promoted_registry.name,
            "sha256": registry_sha,
            "audit_filename": registry_audit_path.name,
            "audit_sha256": _sha256(registry_audit_path),
        },
        "promoted_panel": {
            "config_filename": promoted_panels.name,
            "config_sha256": panel_config_sha,
            "panel_sha256": panel.report["panel_sha256"],
            "audit_filename": panel_audit_path.name,
            "audit_sha256": _sha256(panel_audit_path),
            "alignment_summary": panel.report["alignment_summary"],
        },
        "timestamp_semantics": semantics,
        "claims": policy["claims"],
    }
    promotion_sha = _digest(_canonical_json_bytes(identity))
    report = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "promotion_sha256": promotion_sha,
        "promotion_status": "verified_frozen_convenience_universe_1h_promotion",
        "identity": identity,
        "warnings": _warnings(),
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    report_path = output / f"okx-universe-1h-promotion.{promotion_sha}.json"
    _commit_json(report_path, report)
    exports |= {
        "promoted_registry": str(promoted_registry),
        "registry_audit": str(registry_audit_path),
        "promoted_panels": str(promoted_panels),
        "panel_audit": str(panel_audit_path),
        "report": str(report_path),
    }
    return FrozenUniverse1hPromotionResult(report, exports)


def validate_okx_frozen_universe_1h_promotion(
    report_path: str | Path,
) -> ValidatedFrozenUniverse1hPromotion:
    path = Path(report_path).resolve()
    report = _load_json(path, "okx_1h_extension_invalid_promotion")
    identity = _mapping(report.get("identity"), "promotion_identity")
    promotion_sha = _sha_value(report.get("promotion_sha256"), "promotion_sha256")
    if (
        path.name != f"okx-universe-1h-promotion.{promotion_sha}.json"
        or _digest(_canonical_json_bytes(identity)) != promotion_sha
        or report.get("promotion_status") != "verified_frozen_convenience_universe_1h_promotion"
        or report.get("readiness_changed") is not False
    ):
        raise MarketDataError("okx_1h_extension_promotion_identity_mismatch")
    source_promotion = _mapping(identity.get("source_promotion"), "source_promotion")
    original_marker = _locate_repo_dependency(
        path,
        Path("reports/okx-universe-promotion") / str(source_promotion["report_filename"]),
        str(source_promotion["report_sha256"]),
    )
    original = validate_dataset_promotion_report(original_marker)
    repo = original.repo_root
    capture_path = _locate_repo_dependency(
        path,
        Path("reports/okx-frozen-universe-1h-capture")
        / f"okx-frozen-universe-1h-capture.{identity['capture_sha256']}.json",
        str(identity["capture_report_sha256"]),
    )
    capture = validate_okx_frozen_universe_1h_capture(capture_path)
    policy = _validate_policy(identity.get("policy"))
    registry_info = _mapping(identity.get("promoted_registry"), "promoted_registry")
    panel_info = _mapping(identity.get("promoted_panel"), "promoted_panel")
    registry_path = _repo_file(repo, registry_info["filename"])
    panels_path = _repo_file(repo, panel_info["config_filename"])
    if _sha256(registry_path) != registry_info["sha256"] or _sha256(panels_path) != panel_info["config_sha256"]:
        raise MarketDataError("okx_1h_extension_promoted_config_hash_mismatch")
    registry_audit_path = _sibling(path, registry_info["audit_filename"])
    panel_audit_path = _sibling(path, panel_info["audit_filename"])
    frozen_registry_audit = normalize_dataset_registry_audit_paths(
        _load_json(registry_audit_path, "okx_1h_extension_invalid_registry_audit"), repo
    )
    observed_registry_audit = normalize_dataset_registry_audit_paths(
        audit_dataset_registry(registry_path), repo
    )
    if (
        _sha256(registry_audit_path) != registry_info["audit_sha256"]
        or _sha256(panel_audit_path) != panel_info["audit_sha256"]
        or frozen_registry_audit != observed_registry_audit
    ):
        raise MarketDataError("okx_1h_extension_promoted_audit_mismatch")
    panel = build_dataset_panel(registry_path, panels_path, policy["target_panel_id"])
    if _load_json(panel_audit_path, "okx_1h_extension_invalid_panel_audit") != panel.report:
        raise MarketDataError("okx_1h_extension_promoted_panel_replay_mismatch")
    _validate_panel(panel.report, policy)
    datasets = identity.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != 3:
        raise MarketDataError("okx_1h_extension_promoted_dataset_count_mismatch")
    for item in datasets:
        lineage_path = _repo_file(repo, item["lineage_repo_relative_path"])
        destination = _repo_file(repo, item["destination_repo_relative_path"])
        lineage = _load_json(lineage_path, "okx_1h_extension_invalid_lineage")
        if (
            _sha256(destination) != item["destination_raw_sha256"]
            or canonical_ohlcv_sha256(destination) != item["destination_canonical_sha256"]
            or lineage.get("lineage_sha256") != item["lineage_sha256"]
            or _digest(_canonical_json_bytes({k: v for k, v in lineage.items() if k != "lineage_sha256"}))
            != item["lineage_sha256"]
        ):
            raise MarketDataError("okx_1h_extension_promoted_lineage_mismatch")
    semantics = _mapping(identity.get("timestamp_semantics"), "timestamp_semantics")
    if (
        semantics.get("aggregate_status") != "mixed_unverified"
        or semantics.get("timestamp_semantics_uniform") is not False
        or identity.get("claims") != policy["claims"]
    ):
        raise MarketDataError("okx_1h_extension_promoted_safety_mismatch")
    return ValidatedFrozenUniverse1hPromotion(path, report, repo, registry_path, panels_path, capture)


def format_frozen_universe_1h_capture(result: FrozenUniverse1hCaptureResult) -> str:
    return "\n".join(
        [
            f"capture_status: {result.report['capture_status']}",
            f"capture_sha256: {result.report['capture_sha256']}",
            f"dataset_count: {len(result.report['identity']['histories'])}",
            f"bar_count_per_dataset: {result.report['identity']['policy']['expected_bar_count']}",
            "private_api_used: false",
            "readiness_changed: false",
        ]
    )


def format_frozen_universe_1h_promotion(result: FrozenUniverse1hPromotionResult) -> str:
    identity = result.report["identity"]
    return "\n".join(
        [
            f"promotion_status: {result.report['promotion_status']}",
            f"promotion_sha256: {result.report['promotion_sha256']}",
            f"panel_sha256: {identity['promoted_panel']['panel_sha256']}",
            f"intersection_bar_count: {identity['promoted_panel']['alignment_summary']['intersection_bar_count']}",
            "readiness_changed: false",
        ]
    )


def _load_policy(path: str | Path) -> dict[str, Any]:
    policy_path = Path(path)
    if not policy_path.is_file():
        raise FileNotFoundError(f"OKX 1h extension policy not found: {policy_path}")
    return _validate_policy(yaml.safe_load(policy_path.read_text(encoding="utf-8")))


def _validate_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("OKX 1h extension policy must be a mapping")
    frozen_path = Path(__file__).resolve().parents[3] / "config.okx-universe-1h-extension.example.yaml"
    frozen = yaml.safe_load(frozen_path.read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("OKX 1h extension policy must equal the frozen policy")
    return json.loads(json.dumps(value, sort_keys=True))


def _validate_source_promotion_selection(promotion: ValidatedDatasetPromotion, policy: dict[str, Any]):
    if promotion.report["promotion_sha256"] != policy["source_promotion_sha256"]:
        raise MarketDataError("okx_1h_extension_source_promotion_mismatch")
    capture_info = promotion.report["identity"]["inputs"]["capture"]
    capture_path = promotion.repo_root / "reports" / "okx-universe-capture" / (
        f"okx-universe-capture.{capture_info['capture_sha256']}.json"
    )
    if _sha256(capture_path) != capture_info["report_sha256"]:
        raise MarketDataError("okx_1h_extension_source_capture_hash_mismatch")
    capture = validate_okx_universe_capture(capture_path)
    if (
        capture.selected_inst_ids != EXPECTED_SELECTED
        or list(capture.selected_inst_ids) != policy["selected_inst_ids"]
        or capture.capture["identity"]["snapshot"]["received_at"] != policy["source_snapshot_received_at"]
        or _compute_end_open(policy["source_snapshot_received_at"]) != policy["end_open"]
    ):
        raise MarketDataError("okx_1h_extension_frozen_selection_mismatch")
    return capture


def _load_contract(repo: Path, path: str | Path, policy: dict[str, Any]) -> dict[str, Any]:
    contract_path = _repo_file(repo, path)
    payload = _load_json(contract_path, "okx_1h_extension_invalid_contract")
    history = _mapping(payload.get("history_candles"), "contract_history")
    if (
        payload.get("provider") != "okx"
        or payload.get("scope") != "public_market_data_only"
        or payload.get("private_api_required") is not False
        or payload.get("trading_api_required") is not False
        or history.get("endpoint") != "https://www.okx.com/api/v5/market/history-candles"
        or history.get("allowed_bars") != ["1H", "4H"]
        or history.get("maximum_limit") != 300
        or history.get("after_semantics") != "records_strictly_earlier_than_ts"
        or history.get("timestamp_meaning") != "bar_open_time"
        or policy["okx_bar"] not in history["allowed_bars"]
    ):
        raise MarketDataError("okx_1h_extension_contract_mismatch")
    return {"filename": contract_path.name, "sha256": _sha256(contract_path), "official_url": payload["official_url"]}


def _validate_contract_identity(repo: Path, value: Any, policy: dict[str, Any]) -> None:
    observed = _load_contract(repo, DEFAULT_CONTRACT, policy)
    if value != observed:
        raise MarketDataError("okx_1h_extension_contract_identity_mismatch")


def _source_promotion_identity(promotion: ValidatedDatasetPromotion) -> dict[str, Any]:
    return {
        "promotion_sha256": promotion.report["promotion_sha256"],
        "report_filename": promotion.report_path.name,
        "report_sha256": _sha256(promotion.report_path),
        "promoted_registry_sha256": promotion.report["identity"]["promoted_registry"]["sha256"],
        "promoted_panel_sha256": promotion.report["identity"]["promoted_panel"]["panel_sha256"],
    }


def _registry_entry(lineage: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
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


def _validate_panel(report: dict[str, Any], policy: dict[str, Any]) -> None:
    expected = policy["expected_panel"]
    summary = report["alignment_summary"]
    for key in ("common_first_timestamp", "common_last_timestamp", "intersection_bar_count", "union_bar_count"):
        if summary.get(key) != expected[key]:
            raise MarketDataError(f"okx_1h_extension_panel_{key}_mismatch")
    if summary["coverage_rate"] != round(expected["coverage_rate"], 12):
        raise MarketDataError("okx_1h_extension_panel_coverage_mismatch")
    components = {item["dataset_id"]: item for item in report["datasets"]}
    for dataset_id, (before, after) in expected["boundary_drops"].items():
        item = components.get(dataset_id)
        if item is None or (
            item["dropped_before_common_start"], item["dropped_after_common_end"]
        ) != (before, after) or item["missing_inside_common_window"] != 0:
            raise MarketDataError(f"okx_1h_extension_panel_boundary_mismatch:{dataset_id}")


def _compute_end_open(received_at: str) -> str:
    parsed = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    floored = parsed.replace(minute=0, second=0, microsecond=0)
    return (floored - timedelta(hours=1)).isoformat()


def _validate_csv_content(content: bytes, output: Path, timeframe: str) -> tuple[dict[str, Any], str]:
    temporary = output / f".validate-1h.{uuid4().hex}.csv"
    try:
        temporary.write_bytes(content)
        quality = validate_ohlcv_csv(temporary, timeframe)
        canonical = canonical_ohlcv_sha256(temporary)
        if not quality.valid or canonical is None:
            raise MarketDataError("okx_1h_extension_csv_quality_failed")
        payload = quality.to_dict()
        payload.pop("csv_path", None)
        return payload, canonical
    finally:
        temporary.unlink(missing_ok=True)


def _reports_output(repo: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = (repo / path).resolve() if not path.is_absolute() else path.resolve()
    reports = (repo / "reports").resolve()
    if not resolved.is_relative_to(reports):
        raise ValueError("OKX 1h extension output must stay inside reports")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _repo_file(repo: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = (repo / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_relative_to(repo) or not resolved.is_file():
        raise FileNotFoundError(f"repository file not found: {value}")
    return resolved


def _locate_repo_dependency(report: Path, relative: Path, expected_sha: str) -> Path:
    for parent in report.parents:
        candidate = (parent / relative).resolve()
        if candidate.is_file() and _sha256(candidate) == expected_sha:
            return candidate
    raise MarketDataError("okx_1h_extension_repo_dependency_not_found")


def _sibling(report: Path, filename: Any) -> Path:
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise MarketDataError("okx_1h_extension_artifact_path_escape")
    path = (report.parent / filename).resolve()
    if path.parent != report.parent or not path.is_file():
        raise MarketDataError("okx_1h_extension_artifact_missing")
    return path


def _timestamp_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must be UTC")
    return int(parsed.timestamp() * 1000)


def _safety() -> dict[str, bool]:
    return {
        "public_api_only": True,
        "private_api_used": False,
        "trading_api_used": False,
        "instrument_snapshot_refetched": False,
        "candidate_reselection": False,
        "readiness_changed": False,
    }


def _warnings() -> list[str]:
    return [
        "frozen_current_live_convenience_sample_only",
        "historical_point_in_time_membership_not_proven",
        "survivorship_bias_not_resolved",
        "mixed_timestamp_semantics_not_uniform",
        "no_research_oos_pnl_or_readiness_claim",
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


def _pretty_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256(path: Path) -> str:
    return _digest(path.read_bytes())


def _sha_value(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise MarketDataError(f"okx_1h_extension_invalid_{name}")
    return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MarketDataError(f"okx_1h_extension_invalid_{name}")
    return value


def _load_json(path: Path, error: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError(error) from exc
    if not isinstance(value, dict):
        raise MarketDataError(error)
    return value
