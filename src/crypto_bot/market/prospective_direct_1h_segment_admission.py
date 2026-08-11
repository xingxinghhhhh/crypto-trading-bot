from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_membership_epoch_closure import (
    validate_prospective_membership_epoch_closure,
)
from crypto_bot.market.prospective_direct_1h_segment_chain import (
    AUDIT_STATUS as SEGMENT_CHAIN_STATUS,
)


SCHEMA_VERSION = 1
POLICY_ID = "prospective_direct_1h_segment_admission_v1"
CONTRACT_STATUS = "verified_prospective_direct_1h_segment_admission"
DEFAULT_CONFIG_FILENAME = "config.prospective-direct-1h-segment-admission.example.yaml"
INST_IDS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT")
REQUEST_FIELDS = ("epoch_id", "closure_identity", "segment_chain_identity", "timeframe", "segment_start", "segment_end", "expected_bars_per_asset", "required_asset_count", "expected_total_canonical_rows", "admission_eligible", "capture_performed")
ASSET_FIELDS = ("inst_id", "timeframe", "segment_start", "segment_end", "expected_bar_count", "previous_chain_tail", "continuity_required")
KEY_VALUE_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectiveDirect1hSegmentAdmissionResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def freeze_prospective_direct_1h_segment_admission(
    membership_epoch_closure: str | Path,
    segment_chain: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectiveDirect1hSegmentAdmissionResult:
    closure_path = Path(membership_epoch_closure).resolve()
    repo = _repo_root(closure_path)
    config = load_segment_admission_config(config_path, repo)
    state = _derive_state(closure_path, Path(segment_chain), config, repo)
    request_bytes = _csv_bytes(state["request_rows"], REQUEST_FIELDS)
    asset_bytes = _csv_bytes(state["asset_rows"], ASSET_FIELDS)
    dependencies = _dependencies(state)
    constraints = _constraints(state)
    dependencies_bytes = _csv_bytes(_rows(dependencies), KEY_VALUE_FIELDS)
    constraints_bytes = _csv_bytes(_rows(constraints), KEY_VALUE_FIELDS)
    identity: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID, "closure_sha256": state["closure_sha256"], "segment_chain_sha256": state["segment_chain_sha256"], "status": state["status"], "admission_eligible": state["admission_eligible"], "segment_start": state["segment_start"], "segment_end": state["segment_end"], "expected_bars_per_asset": state["expected_bars_per_asset"], "required_asset_count": 6, "expected_total_canonical_rows": state["expected_total_canonical_rows"], "policy": config, "claims": _claims(), "artifacts": {"request_sha256": _digest(request_bytes), "assets_sha256": _digest(asset_bytes), "dependencies_sha256": _digest(dependencies_bytes), "constraints_sha256": _digest(constraints_bytes)}}
    admission_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-direct-1h-segment-admission.{admission_sha}"
    paths = {"request": output / f"{stem}.request.csv", "assets": output / f"{stem}.assets.csv", "dependencies": output / f"{stem}.dependencies.csv", "constraints": output / f"{stem}.constraints.csv", "report": output / f"{stem}.json"}
    report: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "admission_sha256": admission_sha, "contract_status": CONTRACT_STATUS, "status": state["status"], "admission_eligible": state["admission_eligible"], "closure_sha256": state["closure_sha256"], "segment_chain_sha256": state["segment_chain_sha256"], "epoch_id": state["epoch_id"], "segment_start": state["segment_start"], "segment_end": state["segment_end"], "expected_bars_per_asset": state["expected_bars_per_asset"], "required_asset_count": 6, "expected_total_canonical_rows": state["expected_total_canonical_rows"], "request_rows": len(state["request_rows"]), "asset_rows": len(state["asset_rows"]), "capture_performed": False, "market_data_segment_is_not_sample": True, "current_samples": 160, "new_samples_counted": 0, "remaining_samples": 340, "network_activity_performed": False, "network_capture_authorized": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False, "future_only": True, "historical_recapture_prohibited": True, "canonical_overlap_prohibited": True, "canonical_gap_prohibited": True, "sample_credit_authorized": False, "identity": identity, "artifacts": {"request": {"filename": paths["request"].name, "sha256": identity["artifacts"]["request_sha256"], "row_count": len(state["request_rows"])}, "assets": {"filename": paths["assets"].name, "sha256": identity["artifacts"]["assets_sha256"], "row_count": len(state["asset_rows"])}, "dependencies": {"filename": paths["dependencies"].name, "sha256": identity["artifacts"]["dependencies_sha256"], "row_count": len(dependencies)}, "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)}, "report": {"filename": paths["report"].name}}}
    for key, content in (("request", request_bytes), ("assets", asset_bytes), ("dependencies", dependencies_bytes), ("constraints", constraints_bytes)):
        _commit_bytes(paths[key], content)
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectiveDirect1hSegmentAdmissionResult(report, {key: str(value) for key, value in paths.items()})


def validate_prospective_direct_1h_segment_admission(path: str | Path) -> dict[str, Any]:
    report_path = Path(path).resolve()
    repo = _repo_root(report_path)
    if not report_path.is_relative_to(repo / "reports") or not report_path.name.startswith("prospective-direct-1h-segment-admission."):
        raise MarketDataError("segment admission report path escape")
    report = _load_json(report_path)
    sha = report.get("admission_sha256")
    identity = report.get("identity")
    if not isinstance(sha, str) or report_path.name != f"prospective-direct-1h-segment-admission.{sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != sha:
        raise MarketDataError("segment admission identity mismatch")
    if report.get("contract_status") != CONTRACT_STATUS:
        raise MarketDataError("segment admission contract status mismatch")
    config = identity.get("policy")
    if not isinstance(config, dict) or config != load_segment_admission_config(repo / DEFAULT_CONFIG_FILENAME, repo):
        raise MarketDataError("segment admission policy mismatch")
    state = _derive_state(_locate_closure(repo, str(identity.get("closure_sha256"))), _locate_chain(repo, str(identity.get("segment_chain_sha256"))), config, repo)
    expected = {"status": state["status"], "admission_eligible": state["admission_eligible"], "closure_sha256": state["closure_sha256"], "segment_chain_sha256": state["segment_chain_sha256"], "epoch_id": state["epoch_id"], "segment_start": state["segment_start"], "segment_end": state["segment_end"], "expected_bars_per_asset": state["expected_bars_per_asset"], "expected_total_canonical_rows": state["expected_total_canonical_rows"], "request_rows": len(state["request_rows"]), "asset_rows": len(state["asset_rows"]), "capture_performed": False, "market_data_segment_is_not_sample": True, "current_samples": 160, "new_samples_counted": 0, "remaining_samples": 340, "network_activity_performed": False, "network_capture_authorized": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False, "future_only": True, "historical_recapture_prohibited": True, "canonical_overlap_prohibited": True, "canonical_gap_prohibited": True, "sample_credit_authorized": False}
    for key, value in expected.items():
        if report.get(key) != value:
            raise MarketDataError(f"segment admission report mismatch:{key}")
    artifacts = report.get("artifacts")
    ia = identity.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(ia, dict):
        raise MarketDataError("segment admission artifacts shape mismatch")
    for key, fields, rows in (("request", REQUEST_FIELDS, state["request_rows"]), ("assets", ASSET_FIELDS, state["asset_rows"])):
        info = artifacts.get(key)
        if not isinstance(info, dict) or info.get("sha256") != ia.get(f"{key}_sha256"):
            raise MarketDataError(f"segment admission artifact metadata mismatch:{key}")
        artifact = report_path.parent / str(info.get("filename", ""))
        if not artifact.is_file() or _digest(artifact.read_bytes()) != info.get("sha256"):
            raise MarketDataError(f"segment admission artifact bytes mismatch:{key}")
        if _read_csv(artifact, fields) != [{field: _csv_value(row.get(field)) for field in fields} for row in rows]:
            raise MarketDataError(f"segment admission {key} artifact mismatch")
    for key, expected_values in (("dependencies", _dependencies(state)), ("constraints", _constraints(state))):
        info = artifacts.get(key)
        artifact = report_path.parent / str(info.get("filename", "")) if isinstance(info, dict) else report_path
        if not isinstance(info, dict) or info.get("sha256") != ia.get(f"{key}_sha256") or not artifact.is_file() or _digest(artifact.read_bytes()) != info.get("sha256") or _read_key_values(artifact) != {k: _csv_value(v) for k, v in expected_values.items()}:
            raise MarketDataError(f"segment admission {key} artifact mismatch")
    return report


def load_segment_admission_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_path = Path(path).resolve()
    if config_path.name != DEFAULT_CONFIG_FILENAME or not config_path.is_file():
        raise ValueError("segment admission config filename is not frozen")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        frozen = yaml.safe_load((Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("segment admission config is invalid") from exc
    if value != frozen or (repo is not None and not config_path.is_relative_to(repo.resolve())):
        raise MarketDataError("segment admission config mismatch")
    if not isinstance(value, dict):
        raise MarketDataError("segment admission config shape mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_segment_admission_result(result: ProspectiveDirect1hSegmentAdmissionResult) -> str:
    r = result.report
    return "\n".join((f"contract_status: {r['contract_status']}", f"admission_sha256: {r['admission_sha256']}", f"status: {r['status']}", f"admission_eligible: {str(r['admission_eligible']).lower()}", f"segment_start: {r['segment_start']}", f"segment_end: {r['segment_end']}", f"expected_bars_per_asset: {r['expected_bars_per_asset']}", f"expected_total_canonical_rows: {r['expected_total_canonical_rows']}", "capture_performed: false", "current_samples: 160", "new_samples_counted: 0", "remaining_samples: 340", "network_activity_performed: false", "network_capture_authorized: false", "readiness_changed: false"))


def _derive_state(closure_path: Path, chain_path: Path, config: Mapping[str, Any], repo: Path) -> dict[str, Any]:
    _validate_policy(config)
    closure = validate_prospective_membership_epoch_closure(closure_path)
    chain = _validated_chain(chain_path, repo)
    closure_sha = str(closure["closure_sha256"])
    chain_sha = str(chain["chain_sha256"])
    if closure.get("membership_epoch_closed") is not True or closure.get("epoch_end_resolved") is not True or closure.get("status") != "closed_future_epoch":
        return _state_base(closure, chain, closure_sha, chain_sha, False, "blocked_membership_epoch_not_closed", None, None, 0, [], [])
    start = _parse_iso(str(chain["next_canonical_segment_start"]))
    end = _parse_iso(str(closure["last_execution_timestamp"]))
    if start.minute or start.second or start.microsecond or end.minute or end.second or end.microsecond or end < start:
        raise MarketDataError("segment admission window ordering mismatch")
    count = int((end - start).total_seconds() // 3600) + 1
    if count <= 0:
        raise MarketDataError("segment admission empty window")
    assets = chain.get("identity", {}).get("assets", [])
    if not isinstance(assets, list) or [str(item.get("inst_id")) for item in assets] != list(INST_IDS):
        raise MarketDataError("segment admission asset order mismatch")
    request = [{"epoch_id": str(closure["epoch_id"]), "closure_identity": closure_sha, "segment_chain_identity": chain_sha, "timeframe": "1H", "segment_start": _iso(start), "segment_end": _iso(end), "expected_bars_per_asset": count, "required_asset_count": 6, "expected_total_canonical_rows": count * 6, "admission_eligible": True, "capture_performed": False}]
    previous_tail = str(chain["current_chain_tail"])
    asset_rows = [{"inst_id": inst_id, "timeframe": "1H", "segment_start": _iso(start), "segment_end": _iso(end), "expected_bar_count": count, "previous_chain_tail": previous_tail, "continuity_required": True} for inst_id in INST_IDS]
    return _state_base(closure, chain, closure_sha, chain_sha, True, "admitted_future_segment_request", _iso(start), _iso(end), count, request, asset_rows)


def _state_base(closure: Mapping[str, Any], chain: Mapping[str, Any], closure_sha: str, chain_sha: str, eligible: bool, status: str, start: str | None, end: str | None, count: int, request: list[dict[str, Any]], assets: list[dict[str, Any]]) -> dict[str, Any]:
    return {"closure_sha256": closure_sha, "segment_chain_sha256": chain_sha, "epoch_id": str(closure.get("epoch_id", "epoch-0002")), "admission_eligible": eligible, "status": status, "segment_start": start, "segment_end": end, "expected_bars_per_asset": count, "expected_total_canonical_rows": count * 6 if eligible else 0, "request_rows": request, "asset_rows": assets}


def _validated_chain(path: Path, repo: Path) -> dict[str, Any]:
    if path.parent != (repo / "reports/prospective-direct-1h-segment-chain").resolve():
        raise MarketDataError("segment admission segment chain path mismatch")
    report = _load_json(path)
    sha = report.get("chain_sha256")
    identity = report.get("identity")
    if not isinstance(sha, str) or path.name != f"prospective-direct-1h-segment-chain.{sha}.json" or not isinstance(identity, dict) or _digest(_canonical_json_bytes(identity)) != sha or report.get("audit_status") != SEGMENT_CHAIN_STATUS:
        raise MarketDataError("segment admission segment chain identity mismatch")
    if report.get("next_segment_end_resolved") is not False or report.get("unique_closed_execution_intervals") != 160 or report.get("minimum") != 500 or report.get("remaining") != 340 or report.get("canonical_gap_count") != 0 or report.get("canonical_overlap_count") != 0 or report.get("economic_computation_authorized") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("segment admission segment chain gate mismatch")
    return report


def _validate_policy(config: Mapping[str, Any]) -> None:
    expected = {"schema_version": 1, "policy_id": POLICY_ID, "timeframe": "1H", "bar_duration_ms": 3600000, "required_asset_count": 6, "segment_start_source": "validated_segment_chain_next_canonical_start", "segment_end_source": "validated_closed_membership_epoch_last_execution", "segment_start_override_prohibited": True, "segment_end_override_prohibited": True, "asset_override_prohibited": True, "canonical_continuity_required": True, "canonical_overlap_prohibited": True, "canonical_gap_prohibited": True, "historical_recapture_prohibited": True, "accepted_segment_replacement_prohibited": True, "baseline_replacement_prohibited": True, "network_activity_performed": False, "network_capture_authorized": False, "market_data_segment_is_not_sample": True, "new_samples_counted": 0, "sample_threshold": 500, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False}
    if dict(config) != expected:
        raise MarketDataError("segment admission policy semantics mismatch")


def _dependencies(state: Mapping[str, Any]) -> dict[str, str]:
    return {"closure_sha256": str(state["closure_sha256"]), "segment_chain_sha256": str(state["segment_chain_sha256"]), "epoch_id": str(state["epoch_id"]), "segment_start": str(state["segment_start"] or ""), "segment_end": str(state["segment_end"] or "")}


def _constraints(state: Mapping[str, Any]) -> dict[str, Any]:
    return {"admission_eligible": state["admission_eligible"], "status": state["status"], "expected_bars_per_asset": state["expected_bars_per_asset"], "expected_total_canonical_rows": state["expected_total_canonical_rows"], "capture_performed": False, "market_data_segment_is_not_sample": True, "market_data_captured": False, "current_samples": 160, "new_samples_counted": 0, "remaining_samples": 340, "sample_threshold": 500, "network_activity_performed": False, "network_capture_authorized": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False, "future_only": True, "historical_recapture_prohibited": True, "canonical_overlap_prohibited": True, "canonical_gap_prohibited": True, "sample_credit_authorized": False}


def _claims() -> dict[str, Any]:
    return {"future_only": True, "request_contract_only": True, "capture_performed": False, "market_data_captured": False, "market_data_segment_is_not_sample": True, "historical_recapture_prohibited": True, "accepted_segment_replacement_prohibited": True, "baseline_replacement_prohibited": True, "sample_credit_authorized": False, "profitability_evidence": False, "economic_computation_authorized": False, "readiness_changed": False}


def _locate_closure(repo: Path, sha: str) -> Path:
    if not _is_sha256(sha):
        raise MarketDataError("segment admission closure reference mismatch")
    matches = [p for p in (repo / "reports").rglob(f"prospective-membership-epoch-closure.{sha}.json") if p.is_file()]
    if len(matches) != 1:
        raise MarketDataError("segment admission closure reference ambiguous")
    return matches[0]


def _locate_chain(repo: Path, sha: str) -> Path:
    if not _is_sha256(sha):
        raise MarketDataError("segment admission chain reference mismatch")
    path = repo / "reports/prospective-direct-1h-segment-chain" / f"prospective-direct-1h-segment-chain.{sha}.json"
    if not path.is_file():
        raise MarketDataError("segment admission chain reference missing")
    return path


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("segment admission output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(fields):
                raise MarketDataError("segment admission CSV schema mismatch")
            return list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MarketDataError("segment admission CSV read failed") from exc


def _read_key_values(path: Path) -> dict[str, str]:
    rows = _read_csv(path, KEY_VALUE_FIELDS)
    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(rows):
        raise MarketDataError("segment admission key collision")
    return values


def _rows(values: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True) if not isinstance(values[key], str) else values[key]} for key in sorted(values)]


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows({field: _csv_value(row.get(field)) for field in fields} for row in rows)
    return buffer.getvalue().encode("utf-8")


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("segment admission JSON read failed") from exc
    if not isinstance(value, dict):
        raise MarketDataError("segment admission JSON shape mismatch")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"segment admission content-addressed collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".segment-admission-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _parse_iso(value: str):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise MarketDataError("segment admission timestamp missing timezone")
    return parsed


def _iso(value: Any) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("segment admission repo root not found")
