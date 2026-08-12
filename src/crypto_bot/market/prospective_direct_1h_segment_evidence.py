from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.data_quality import validate_ohlcv_csv
from crypto_bot.market.okx_universe_intake import (
    okx_history_rows_to_csv_bytes,
    replay_okx_public_history,
)
from crypto_bot.market.prospective_direct_1h_extension import (
    AUDIT_STATUS as EXTENSION_AUDIT_STATUS,
    validate_prospective_direct_capture,
)
from crypto_bot.market.prospective_direct_1h_segment_admission import (
    validate_prospective_direct_1h_segment_admission,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_direct_1h_segment_evidence_v1"
CONTRACT_STATUS = "verified_prospective_direct_1h_segment_evidence"
MATERIALIZED_STATUS = "materialized_segment_candidate"
BLOCKED_STATUS = "blocked_segment_admission_not_eligible"
DEFAULT_CONFIG_FILENAME = "config.prospective-direct-1h-segment-evidence.example.yaml"
CAPTURE_STATUS = "complete_prospective_direct_okx_1h_extension_capture"
SEGMENT_CAPTURE_STATUS = "complete_prospective_direct_okx_1h_segment_capture"
CAPTURE_STATUSES = {CAPTURE_STATUS, SEGMENT_CAPTURE_STATUS}
INST_IDS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT")
REQUEST_FIELDS = (
    "epoch_id",
    "closure_identity",
    "segment_chain_identity",
    "timeframe",
    "segment_start",
    "segment_end",
    "expected_bars_per_asset",
    "required_asset_count",
    "expected_total_canonical_rows",
    "admission_eligible",
    "capture_performed",
)
ADMISSION_ASSET_FIELDS = (
    "inst_id",
    "timeframe",
    "segment_start",
    "segment_end",
    "expected_bar_count",
    "previous_chain_tail",
    "continuity_required",
)
ASSET_FIELDS = (
    "inst_id",
    "ordinal",
    "timeframe",
    "canonical_start",
    "canonical_end",
    "bar_count",
    "raw_sha256",
    "canonical_sha256",
    "quality_valid",
    "request_match",
)
ARTIFACT_FIELDS = (
    "inst_id",
    "artifact_role",
    "artifact_path",
    "artifact_sha256",
    "source_report_identity",
    "row_count",
)
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectiveDirect1hSegmentEvidenceResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def materialize_prospective_direct_1h_segment_evidence(
    segment_admission: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
    capture_report: str | Path | None = None,
) -> ProspectiveDirect1hSegmentEvidenceResult:
    admission_path = Path(segment_admission).resolve()
    repo = _repo_root(admission_path)
    config = load_segment_evidence_config(config_path, repo)
    admission = validate_prospective_direct_1h_segment_admission(admission_path)
    state = _admission_state(admission_path, admission, repo)
    if not state["admission_eligible"]:
        return _write_result(repo, output_dir, config, state, None)
    if capture_report is None:
        raise MarketDataError("eligible_segment_admission_requires_capture_report")
    capture_path = Path(capture_report).resolve()
    capture = _validate_capture_lineage(capture_path, repo, config)
    rows = _exact_match_rows(state, capture, repo)
    return _write_result(repo, output_dir, config, state, capture, rows)


def validate_prospective_direct_1h_segment_evidence(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if not report_path.is_relative_to(repo / "reports") or not report_path.name.startswith(
        "prospective-direct-1h-segment-evidence."
    ):
        raise MarketDataError("segment evidence report path escape")
    report = _load_json(report_path)
    sha = report.get("candidate_sha256")
    identity = report.get("identity")
    if (
        not isinstance(sha, str)
        or report_path.name != f"prospective-direct-1h-segment-evidence.{sha}.json"
        or not isinstance(identity, dict)
        or _digest(_canonical_json_bytes(identity)) != sha
    ):
        raise MarketDataError("segment evidence identity mismatch")
    if report.get("contract_status") != CONTRACT_STATUS:
        raise MarketDataError("segment evidence contract status mismatch")
    config = identity.get("policy")
    if not isinstance(config, dict) or config != load_segment_evidence_config(
        repo / DEFAULT_CONFIG_FILENAME, repo
    ):
        raise MarketDataError("segment evidence policy mismatch")
    admission_path = _locate_report(repo, "prospective-direct-1h-segment-admission", str(identity.get("admission_sha256")))
    admission = validate_prospective_direct_1h_segment_admission(admission_path)
    state = _admission_state(admission_path, admission, repo)
    expected = _expected_report_fields(state, identity.get("capture_sha256"))
    for key, value in expected.items():
        if report.get(key) != value:
            raise MarketDataError(f"segment evidence report mismatch:{key}")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("segment evidence artifacts shape mismatch")
    asset_file = _validate_output_artifact(
        report_path, artifacts, identity_artifacts, "assets", ASSET_FIELDS, identity.get("asset_rows", 0)
    )
    artifact_file = _validate_output_artifact(
        report_path, artifacts, identity_artifacts, "artifacts", ARTIFACT_FIELDS, identity.get("artifact_rows", 0)
    )
    for name in ("dependencies", "constraints"):
        info = artifacts.get(name)
        expected_sha = identity_artifacts.get(f"{name}_sha256")
        if not isinstance(info, dict) or info.get("sha256") != expected_sha:
            raise MarketDataError(f"segment evidence artifact metadata mismatch:{name}")
        artifact = report_path.parent / str(info.get("filename", ""))
        if not artifact.is_file() or _digest(artifact.read_bytes()) != expected_sha:
            raise MarketDataError(f"segment evidence artifact bytes mismatch:{name}")
    capture_sha = identity.get("capture_sha256")
    if state["admission_eligible"]:
        if not isinstance(capture_sha, str):
            raise MarketDataError("segment evidence capture reference missing")
        capture_path = _locate_capture(repo, capture_sha)
        capture = _validate_capture_lineage(capture_path, repo, config)
        rows = _exact_match_rows(state, capture, repo)
        if report.get("asset_rows") != len(rows) or report.get("artifact_rows") != len(rows) * 2:
            raise MarketDataError("segment evidence row count mismatch")
        expected_assets = _csv_bytes(rows, ASSET_FIELDS)
        if asset_file.read_bytes() != expected_assets:
            raise MarketDataError("segment evidence assets replay mismatch")
        expected_artifacts = _csv_bytes(_artifact_rows(rows), ARTIFACT_FIELDS)
        if artifact_file.read_bytes() != expected_artifacts:
            raise MarketDataError("segment evidence artifacts replay mismatch")
        expected_fixture = bool(capture.get("fixture_only"))
        if report.get("fixture_only") is not expected_fixture or report.get("market_evidence") is not (not expected_fixture):
            raise MarketDataError("segment evidence lineage classification mismatch")
    elif report.get("fixture_only") is not False or report.get("market_evidence") is not False:
        raise MarketDataError("blocked segment evidence lineage classification mismatch")
    elif capture_sha is not None or report.get("capture_consumed") is not False:
        raise MarketDataError("blocked segment evidence consumed capture")
    return report


def load_segment_evidence_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("segment evidence config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen = yaml.safe_load(
            (Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("segment evidence config is invalid") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("segment evidence config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("segment evidence config shape mismatch")
    _validate_policy(value)
    return json.loads(json.dumps(value, sort_keys=True))


def format_segment_evidence_result(result: ProspectiveDirect1hSegmentEvidenceResult) -> str:
    report = result.report
    return "\n".join(
        (
            f"contract_status: {report['contract_status']}",
            f"candidate_sha256: {report['candidate_sha256']}",
            f"status: {report['status']}",
            f"segment_candidate_materialized: {str(report['segment_candidate_materialized']).lower()}",
            f"capture_consumed: {str(report['capture_consumed']).lower()}",
            f"asset_rows: {report['asset_rows']}",
            f"artifact_rows: {report['artifact_rows']}",
            "segment_appended: false",
            "new_samples_counted: 0",
            f"current_samples: {report['current_samples']}",
            f"remaining_samples: {report['remaining_samples']}",
            "network_activity_performed: false",
            "readiness_changed: false",
        )
    )


def _admission_state(path: Path, admission: Mapping[str, Any], repo: Path) -> dict[str, Any]:
    if admission.get("contract_status") != "verified_prospective_direct_1h_segment_admission":
        raise MarketDataError("segment evidence admission contract mismatch")
    eligible = admission.get("admission_eligible") is True
    state: dict[str, Any] = {
        "admission_path": path,
        "admission_sha256": admission.get("admission_sha256"),
        "admission_eligible": eligible,
        "status": MATERIALIZED_STATUS if eligible else BLOCKED_STATUS,
        "segment_start": admission.get("segment_start"),
        "segment_end": admission.get("segment_end"),
        "expected_bars_per_asset": admission.get("expected_bars_per_asset", 0),
        "expected_total_canonical_rows": admission.get("expected_total_canonical_rows", 0),
        "assets": [],
        "epoch_id": admission.get("epoch_id"),
    }
    if not isinstance(state["admission_sha256"], str) or not _is_sha256(state["admission_sha256"]):
        raise MarketDataError("segment evidence admission identity missing")
    if not eligible:
        return state
    if admission.get("required_asset_count") != 6 or admission.get("request_rows") != 1 or admission.get("asset_rows") != 6:
        raise MarketDataError("segment evidence admission shape mismatch")
    assets_info = admission.get("artifacts", {}).get("assets") if isinstance(admission.get("artifacts"), dict) else None
    identity = admission.get("identity")
    identity_artifacts = identity.get("artifacts") if isinstance(identity, dict) else None
    if not isinstance(assets_info, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("segment evidence admission assets missing")
    assets_path = path.parent / str(assets_info.get("filename", ""))
    if assets_info.get("sha256") != identity_artifacts.get("assets_sha256") or not assets_path.is_file() or _digest(assets_path.read_bytes()) != assets_info.get("sha256"):
        raise MarketDataError("segment evidence admission assets hash mismatch")
    rows = _read_csv(assets_path, ADMISSION_ASSET_FIELDS)
    if len(rows) != 6:
        raise MarketDataError("segment evidence admission asset row count mismatch")
    state["assets"] = rows
    if [row["inst_id"] for row in rows] != list(INST_IDS):
        raise MarketDataError("segment evidence admission asset order mismatch")
    if any(row["timeframe"] != "1H" for row in rows):
        raise MarketDataError("segment evidence admission timeframe mismatch")
    return state


def _validate_capture_lineage(path: Path, repo: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    if not path.is_relative_to(repo / "reports") or not path.is_file():
        raise MarketDataError("segment evidence capture path escape")
    report = _load_json(path)
    if report.get("audit_status") == EXTENSION_AUDIT_STATUS:
        _validate_extension_marker(path, report)
        capture_sha = report.get("identity", {}).get("capture_sha256")
        if not isinstance(capture_sha, str):
            raise MarketDataError("segment evidence extension capture reference missing")
        capture_path = _locate_capture(repo, capture_sha)
        capture = _validate_capture_marker(capture_path, repo, config)
        capture["source_report_path"] = str(path)
        capture["source_report_identity"] = str(report.get("extension_sha256"))
        return capture
    return _validate_capture_marker(path, repo, config)


def _validate_capture_marker(path: Path, repo: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    report = _load_json(path)
    identity = report.get("identity")
    capture_sha = report.get("capture_sha256")
    if (
        not isinstance(identity, dict)
        or not isinstance(capture_sha, str)
        or not _is_sha256(capture_sha)
        or path.name != f"prospective-direct-1h-capture.{capture_sha}.json"
        or _digest(_canonical_json_bytes(identity)) != capture_sha
        or report.get("capture_status") not in CAPTURE_STATUSES
    ):
        raise MarketDataError("segment evidence capture identity mismatch")
    if report.get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False or report.get("baseline_replacement_prohibited") is not True:
        raise MarketDataError("segment evidence capture safety policy mismatch")
    assets = identity.get("assets")
    if not isinstance(assets, list) or len(assets) != 6 or [item.get("inst_id") for item in assets if isinstance(item, dict)] != list(INST_IDS):
        raise MarketDataError("segment evidence capture asset order mismatch")
    try:
        config_path = repo / "config.okx-prospective-direct-1h-extension.example.yaml"
        if path.parent.name == "prospective-direct-1h-capture" and config_path.is_file() and identity.get("append_row_count_per_asset") == 164:
            validate_prospective_direct_capture(path, _load_yaml(config_path), repo)
    except (FileNotFoundError, OSError, ValueError, MarketDataError):
        if identity.get("append_row_count_per_asset") == 164:
            raise
    source_identity = capture_sha
    return {
        "report": report,
        "report_path": path,
        "capture_sha256": capture_sha,
        "source_report_path": str(path),
        "source_report_identity": source_identity,
        "assets": assets,
        "fixture_only": _fixture_only(report),
    }


def _validate_extension_marker(path: Path, report: Mapping[str, Any]) -> None:
    sha = report.get("extension_sha256")
    identity = report.get("identity")
    if not isinstance(sha, str) or not _is_sha256(sha) or path.name != f"prospective-direct-1h-extension.{sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != sha:
        raise MarketDataError("segment evidence extension identity mismatch")
    artifacts = report.get("artifacts")
    identity_artifacts = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(identity_artifacts, dict):
        raise MarketDataError("segment evidence extension artifacts missing")
    for name in ("datasets", "coverage", "constraints"):
        info = artifacts.get(name)
        expected = identity_artifacts.get(f"{name}_sha256")
        artifact = path.parent / str(info.get("filename", "")) if isinstance(info, dict) else path
        if not isinstance(info, dict) or info.get("sha256") != expected or not artifact.is_file() or _digest(artifact.read_bytes()) != expected:
            raise MarketDataError(f"segment evidence extension artifact mismatch:{name}")


def _exact_match_rows(state: Mapping[str, Any], capture: Mapping[str, Any], repo: Path) -> list[dict[str, Any]]:
    expected_assets = [str(row["inst_id"]) for row in state["assets"]]
    capture_assets = capture.get("assets")
    if (
        not isinstance(capture_assets, list)
        or any(not isinstance(item, dict) for item in capture_assets)
        or [item.get("inst_id") for item in capture_assets] != expected_assets
    ):
        raise MarketDataError("segment evidence capture assets do not match request")
    expected_start = _parse_iso(str(state["segment_start"]))
    expected_end = _parse_iso(str(state["segment_end"]))
    expected_count = int(state["expected_bars_per_asset"])
    rows: list[dict[str, Any]] = []
    for ordinal, item in enumerate(capture_assets, start=1):
        if not isinstance(item, dict):
            raise MarketDataError("segment evidence capture asset shape mismatch")
        inst_id = str(item.get("inst_id"))
        start = _parse_iso(str(item.get("append_start")))
        end = _parse_iso(str(item.get("append_end")))
        if start != expected_start or end != expected_end or item.get("append_row_count") != expected_count:
            raise MarketDataError(f"segment evidence request mismatch:{inst_id}")
        raw_path = _sibling(capture["report_path"], item.get("raw_filename"))
        canonical_path = _sibling(capture["report_path"], item.get("append_filename"))
        raw_bytes = raw_path.read_bytes()
        canonical_bytes = canonical_path.read_bytes()
        raw_sha = _digest(raw_bytes)
        canonical_sha = _digest(canonical_bytes)
        if raw_sha != item.get("raw_sha256") or canonical_sha != item.get("canonical_sha256"):
            raise MarketDataError(f"segment evidence artifact hash mismatch:{inst_id}")
        replayed, _pages = replay_okx_public_history(
            raw_bytes,
            inst_id,
            _timestamp_ms(expected_start),
            _timestamp_ms(expected_end),
            okx_bar="1H",
            bar_duration_ms=3_600_000,
        )
        rebuilt = okx_history_rows_to_csv_bytes(replayed)
        if rebuilt != canonical_bytes or len(replayed) != expected_count:
            raise MarketDataError(f"segment evidence canonical replay mismatch:{inst_id}")
        quality = _validate_quality(canonical_bytes)
        if not quality["valid"]:
            raise MarketDataError(f"segment evidence quality mismatch:{inst_id}")
        rows.append(
            {
                "inst_id": inst_id,
                "ordinal": ordinal,
                "timeframe": "1H",
                "canonical_start": _iso(expected_start),
                "canonical_end": _iso(expected_end),
                "bar_count": expected_count,
                "raw_sha256": raw_sha,
                "canonical_sha256": canonical_sha,
                "quality_valid": True,
                "request_match": True,
                "raw_path": _relative(repo, raw_path),
                "canonical_path": _relative(repo, canonical_path),
                "source_report_identity": str(capture["source_report_identity"]),
                "fixture_only": bool(capture["fixture_only"]),
            }
        )
    if len(rows) != 6 or sum(int(row["bar_count"]) for row in rows) != int(state["expected_total_canonical_rows"]):
        raise MarketDataError("segment evidence total row mismatch")
    return rows


def _write_result(
    repo: Path,
    output_dir: str | Path,
    config: Mapping[str, Any],
    state: Mapping[str, Any],
    capture: Mapping[str, Any] | None,
    rows: list[dict[str, Any]] | None = None,
) -> ProspectiveDirect1hSegmentEvidenceResult:
    output = _reports_output(repo, output_dir)
    materialized = bool(state["admission_eligible"] and capture is not None and rows is not None)
    asset_rows = rows or []
    artifact_rows = _artifact_rows(asset_rows) if materialized and capture is not None else []
    assets_bytes = _csv_bytes(asset_rows, ASSET_FIELDS)
    artifacts_bytes = _csv_bytes(artifact_rows, ARTIFACT_FIELDS)
    dependencies = _dependencies(state, capture)
    constraints = _constraints(state, materialized, capture)
    dependencies_bytes = _csv_bytes(_rows(dependencies), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraints), KEY_VALUE_FIELDS)
    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "policy": dict(config),
        "admission_sha256": state["admission_sha256"],
        "capture_sha256": capture.get("capture_sha256") if capture else None,
        "segment_start": state["segment_start"],
        "segment_end": state["segment_end"],
        "expected_bars_per_asset": state["expected_bars_per_asset"],
        "expected_total_canonical_rows": state["expected_total_canonical_rows"],
        "asset_rows": len(asset_rows),
        "artifact_rows": len(artifact_rows),
        "artifacts": {
            "assets_sha256": _digest(assets_bytes),
            "artifacts_sha256": _digest(artifacts_bytes),
            "dependencies_sha256": _digest(dependencies_bytes),
            "constraints_sha256": _digest(constraints_bytes),
        },
        "claims": {
            "future_only": True,
            "fixture_only": bool(capture.get("fixture_only")) if capture else False,
            "market_evidence": False if capture and capture.get("fixture_only") else bool(capture),
            "sample_evidence": False,
            "profitability_evidence": False,
            "segment_appended": False,
        },
    }
    candidate_sha = _digest(_canonical_json_bytes(identity))
    stem = f"prospective-direct-1h-segment-evidence.{candidate_sha}"
    paths = {
        "assets": output / f"{stem}.assets.csv",
        "artifacts": output / f"{stem}.artifacts.csv",
        "dependencies": output / f"{stem}.dependencies.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "candidate_sha256": candidate_sha,
        "contract_status": CONTRACT_STATUS,
        "status": MATERIALIZED_STATUS if materialized else BLOCKED_STATUS,
        "segment_candidate_materialized": materialized,
        "validated_segment_candidate": materialized,
        "capture_consumed": materialized,
        "admission_eligible": bool(state["admission_eligible"]),
        "admission_sha256": state["admission_sha256"],
        "capture_sha256": capture.get("capture_sha256") if capture else None,
        "segment_start": state["segment_start"],
        "segment_end": state["segment_end"],
        "expected_bars_per_asset": state["expected_bars_per_asset"],
        "expected_total_canonical_rows": state["expected_total_canonical_rows"],
        "asset_rows": len(asset_rows),
        "artifact_rows": len(artifact_rows),
        "segment_appended": False,
        "current_samples": 160,
        "sample_threshold": 500,
        "remaining_samples": 340,
        "new_samples_counted": 0,
        "network_activity_performed": False,
        "network_capture_authorized": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "future_only": True,
        "historical_recapture_prohibited": True,
        "baseline_replacement_prohibited": True,
        "accepted_segment_replacement_prohibited": True,
        "market_data_segment_is_not_sample": True,
        "sample_credit_authorized": False,
        "fixture_only": bool(capture.get("fixture_only")) if capture else False,
        "market_evidence": False if capture and capture.get("fixture_only") else bool(capture),
        "sample_evidence": False,
        "profitability_evidence": False,
        "identity": identity,
        "artifacts": {
            "assets": {"filename": paths["assets"].name, "sha256": identity["artifacts"]["assets_sha256"], "row_count": len(asset_rows)},
            "artifacts": {"filename": paths["artifacts"].name, "sha256": identity["artifacts"]["artifacts_sha256"], "row_count": len(artifact_rows)},
            "dependencies": {"filename": paths["dependencies"].name, "sha256": identity["artifacts"]["dependencies_sha256"], "row_count": len(dependencies)},
            "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)},
            "report": {"filename": paths["report"].name},
        },
    }
    for key, content in (("assets", assets_bytes), ("artifacts", artifacts_bytes), ("dependencies", dependencies_bytes), ("constraints", constraints_bytes)):
        _commit_bytes(paths[key], content)
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveDirect1hSegmentEvidenceResult(report, {key: str(path) for key, path in paths.items()})


def _expected_report_fields(state: Mapping[str, Any], capture_sha: Any) -> dict[str, Any]:
    eligible = bool(state["admission_eligible"])
    return {
        "status": MATERIALIZED_STATUS if eligible else BLOCKED_STATUS,
        "segment_candidate_materialized": eligible,
        "validated_segment_candidate": eligible,
        "capture_consumed": eligible,
        "admission_eligible": eligible,
        "admission_sha256": state["admission_sha256"],
        "capture_sha256": capture_sha if eligible else None,
        "segment_start": state["segment_start"],
        "segment_end": state["segment_end"],
        "expected_bars_per_asset": state["expected_bars_per_asset"],
        "expected_total_canonical_rows": state["expected_total_canonical_rows"],
        "segment_appended": False,
        "current_samples": 160,
        "sample_threshold": 500,
        "remaining_samples": 340,
        "new_samples_counted": 0,
        "network_activity_performed": False,
        "network_capture_authorized": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "future_only": True,
        "historical_recapture_prohibited": True,
        "baseline_replacement_prohibited": True,
        "accepted_segment_replacement_prohibited": True,
        "market_data_segment_is_not_sample": True,
        "sample_credit_authorized": False,
        "sample_evidence": False,
        "profitability_evidence": False,
    }


def _dependencies(state: Mapping[str, Any], capture: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "admission_sha256": state["admission_sha256"],
        "capture_sha256": capture.get("capture_sha256") if capture else "",
        "capture_report_identity": capture.get("source_report_identity") if capture else "",
        "epoch_id": state.get("epoch_id") or "",
        "segment_start": state.get("segment_start") or "",
        "segment_end": state.get("segment_end") or "",
    }


def _artifact_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for row in rows:
        for role, path_key, sha_key, count in (
            ("raw_bundle", "raw_path", "raw_sha256", ""),
            ("canonical_csv", "canonical_path", "canonical_sha256", row["bar_count"]),
        ):
            artifacts.append(
                {
                    "inst_id": row["inst_id"],
                    "artifact_role": role,
                    "artifact_path": row[path_key],
                    "artifact_sha256": row[sha_key],
                    "source_report_identity": row["source_report_identity"],
                    "row_count": count,
                }
            )
    return artifacts


def _constraints(state: Mapping[str, Any], materialized: bool, capture: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "admission_eligible": bool(state["admission_eligible"]),
        "segment_candidate_materialized": materialized,
        "capture_consumed": materialized,
        "segment_appended": False,
        "current_samples": 160,
        "sample_threshold": 500,
        "remaining_samples": 340,
        "new_samples_counted": 0,
        "market_data_segment_is_not_sample": True,
        "network_activity_performed": False,
        "network_capture_authorized": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "fixture_only": bool(capture.get("fixture_only")) if capture else False,
    }


def _fixture_only(report: Mapping[str, Any]) -> bool:
    claims = report.get("identity", {}).get("claims", {}) if isinstance(report.get("identity"), dict) else {}
    return bool(report.get("fixture_only") or claims.get("fixture_only"))


def _validate_output_artifact(report_path: Path, artifacts: Mapping[str, Any], identity: Mapping[str, Any], name: str, fields: tuple[str, ...], expected_rows: Any) -> Path:
    info = artifacts.get(name)
    expected_sha = identity.get(f"{name}_sha256")
    if not isinstance(info, dict) or info.get("sha256") != expected_sha:
        raise MarketDataError(f"segment evidence artifact metadata mismatch:{name}")
    artifact = report_path.parent / str(info.get("filename", ""))
    if not artifact.is_file() or _digest(artifact.read_bytes()) != expected_sha:
        raise MarketDataError(f"segment evidence artifact bytes mismatch:{name}")
    rows = _read_csv(artifact, fields)
    if len(rows) != int(expected_rows):
        raise MarketDataError(f"segment evidence artifact row count mismatch:{name}")
    return artifact


def _validate_quality(content: bytes) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_bytes(content)
        return validate_ohlcv_csv(temporary, "1h").to_dict()
    finally:
        temporary.unlink(missing_ok=True)


def _validate_policy(config: Mapping[str, Any]) -> None:
    expected = {
        "schema_version": 1,
        "policy_id": POLICY_ID,
        "required_timeframe": "1H",
        "required_asset_count": 6,
        "request_match_policy": "exact",
        "canonical_window_match": "exact",
        "canonical_bar_count_match": "exact",
        "asset_order_match": "exact",
        "raw_evidence_required": True,
        "canonical_evidence_required": True,
        "ohlcv_quality_validation_required": True,
        "offline_replay_required": True,
        "historical_recapture_prohibited": True,
        "baseline_replacement_prohibited": True,
        "accepted_segment_replacement_prohibited": True,
        "segment_candidate_only": True,
        "segment_chain_append_authorized": False,
        "market_data_segment_is_not_sample": True,
        "new_samples_counted": 0,
        "sample_threshold": 500,
        "network_activity_performed": False,
        "network_capture_authorized": False,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }
    if dict(config) != expected:
        raise MarketDataError("segment evidence policy semantics mismatch")


def _locate_capture(repo: Path, sha: str) -> Path:
    if not _is_sha256(sha):
        raise MarketDataError("segment evidence capture reference mismatch")
    matches = [p for p in (repo / "reports").rglob(f"prospective-direct-1h-capture.{sha}.json") if p.is_file()]
    if len(matches) != 1:
        raise MarketDataError("segment evidence capture reference ambiguous")
    return matches[0]


def _locate_report(repo: Path, prefix: str, sha: str) -> Path:
    if not _is_sha256(sha):
        raise MarketDataError("segment evidence parent reference mismatch")
    matches = [p for p in (repo / "reports").rglob(f"{prefix}.{sha}.json") if p.is_file()]
    if len(matches) != 1:
        raise MarketDataError("segment evidence parent reference ambiguous")
    return matches[0]


def _sibling(marker: Path, filename: Any) -> Path:
    if not isinstance(filename, str) or not filename:
        raise MarketDataError("segment evidence artifact filename escape")
    path = (marker.parent / filename).resolve()
    if path.parent != marker.parent.resolve():
        raise MarketDataError("segment evidence artifact path escape")
    if Path(filename).name != filename:
        raise MarketDataError("segment evidence artifact filename escape")
    if not path.is_file():
        raise MarketDataError("segment evidence artifact missing")
    return path


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    candidate = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not candidate.is_relative_to((repo / "reports").resolve()):
        raise ValueError("segment evidence output must stay inside reports")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _repo_root(path: Path) -> Path:
    resolved = path.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "src/crypto_bot").is_dir():
            return candidate
    raise MarketDataError("segment evidence repo root not found")


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fields):
                raise MarketDataError("segment evidence CSV schema mismatch")
            return list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("segment evidence CSV read failed") from exc


def _csv_bytes(rows: Sequence[Mapping[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row.get(field)) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _rows(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": values[key]} for key in sorted(values)]


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("segment evidence JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("segment evidence JSON shape mismatch")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MarketDataError("segment evidence YAML read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("segment evidence YAML shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError("segment evidence artifact collision")
        return
    path.write_bytes(content)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("segment evidence timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise MarketDataError("segment evidence timestamp not timezone aware")
    return parsed.astimezone(timezone.utc)


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _relative(repo: Path, path: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()
