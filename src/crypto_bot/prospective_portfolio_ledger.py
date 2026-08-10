from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from crypto_bot.cross_sectional_portfolio_mechanism import (
    RANK_DIRECTIONS,
    build_long_only_target_weights,
    validate_cross_sectional_portfolio_mechanism,
)
from crypto_bot.errors import MarketDataError
from crypto_bot.factors import DEFAULT_FACTOR_SPECS, compute_factor_frame
from crypto_bot.market.csv_data import load_ohlcv_csv
from crypto_bot.market.prospective_direct_1h_extension import (
    validate_prospective_direct_capture,
    load_prospective_direct_config,
)
from crypto_bot.market.prospective_membership_bar_gate import (
    validate_prospective_membership_bar_gate,
)


SCHEMA_VERSION = 1
LEDGER_STATUS = "verified_prospective_closed_epoch_portfolio_ledger"
DEFAULT_CONFIG_FILENAME = "config.prospective-portfolio-ledger.example.yaml"
INST_IDS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT")
FACTORS = tuple(sorted(spec.name for spec in DEFAULT_FACTOR_SPECS))
HORIZONS = (4, 16, 64)
SIGNAL_COUNT = 161
FAMILY_COUNT = 36
FACTOR_SCORE_FIELDS = (
    "signal_timestamp", "symbol", "factor", "score", "finite", "data_cutoff_timestamp",
)
DECISION_FIELDS = (
    "family_member_id", "factor", "horizon", "rank_direction", "signal_timestamp",
    "completion_timestamp", "execution_timestamp", "decision_status", "decision_reason",
    "selected_asset_1", "selected_asset_2", "cash_weight", "gross_exposure", "net_exposure",
)
WEIGHT_FIELDS = (
    "family_member_id", "signal_timestamp", "symbol", "rank", "selected", "weight",
)
CONSTRAINT_FIELDS = ("key", "value")


@dataclass(frozen=True)
class ProspectivePortfolioLedgerResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def build_prospective_portfolio_ledger(
    preregistration_report: str | Path,
    membership_gate_report: str | Path,
    market_data_extension_report: str | Path,
    execution_mapping_report: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> ProspectivePortfolioLedgerResult:
    prereg_path = Path(preregistration_report).resolve()
    repo = _repo_root(prereg_path)
    config = load_ledger_config(config_path, repo)
    gate_path = Path(membership_gate_report).resolve()
    extension_path = Path(market_data_extension_report).resolve()
    mapping_path = Path(execution_mapping_report).resolve()
    _pin_path(repo, prereg_path, config["preregistration_report"])
    _pin_path(repo, gate_path, config["membership_gate_report"])
    _pin_path(repo, extension_path, config["market_data_extension_report"])
    _pin_path(repo, mapping_path, config["execution_mapping_report"])
    prereg = _validate_preregistration(prereg_path, repo, config)
    gate = validate_prospective_membership_bar_gate(
        gate_path, repo / "config.prospective-membership-bar-gate.example.yaml"
    )
    extension = _validate_extension(extension_path, repo, config)
    mapping = _validate_mapping(mapping_path, config)
    if gate.report["gate_sha256"] != config["membership_gate_sha256"]:
        raise MarketDataError("prospective_ledger_membership_gate_identity_mismatch")
    if extension["extension_sha256"] != config["market_data_extension_sha256"]:
        raise MarketDataError("prospective_ledger_market_data_extension_identity_mismatch")
    if mapping["audit_sha256"] != config["execution_mapping_sha256"]:
        raise MarketDataError("prospective_ledger_execution_mapping_identity_mismatch")

    eligibility = list(gate.eligibility)
    timestamps = _signal_timestamps(eligibility, config)
    bars, dataset_hashes = _load_concatenated_bars(extension, repo, config)
    score_rows = _factor_score_rows(bars, timestamps, config)
    score_map = {
        (row["signal_timestamp"], row["symbol"], row["factor"]): float(row["score"])
        for row in score_rows
    }
    decisions, weights = _decision_rows(score_map, timestamps, config)
    if len(score_rows) != 5796 or len(decisions) != 5796 or len(weights) != 34776:
        raise MarketDataError("prospective_ledger_output_shape_mismatch")
    constraints = _constraint_rows(config, prereg, gate, extension, mapping)
    score_bytes = _csv_bytes(score_rows, FACTOR_SCORE_FIELDS)
    decision_bytes = _csv_bytes(decisions, DECISION_FIELDS)
    weight_bytes = _csv_bytes(weights, WEIGHT_FIELDS)
    constraint_bytes = _csv_bytes(constraints, CONSTRAINT_FIELDS)
    identity = {
        "schema_version": SCHEMA_VERSION,
        "policy_id": config["policy_id"],
        "sources": {
            "preregistration_sha256": prereg["preregistration_sha256"],
            "preregistration_report_sha256": _sha256(prereg_path),
            "mechanism_sha256": config["mechanism_sha256"],
            "membership_gate_sha256": gate.report["gate_sha256"],
            "membership_gate_report_sha256": _sha256(gate_path),
            "market_data_extension_sha256": extension["extension_sha256"],
            "market_data_extension_report_sha256": _sha256(extension_path),
            "execution_mapping_sha256": mapping["audit_sha256"],
            "execution_mapping_report_sha256": _sha256(mapping_path),
        },
        "tracked_inst_ids": list(INST_IDS),
        "dataset_hashes": dataset_hashes,
        "signal_timestamps": timestamps,
        "factor_specs": [
            {"family": spec.family, "name": spec.name, "window": spec.window}
            for spec in sorted(DEFAULT_FACTOR_SPECS, key=lambda item: item.name)
        ],
        "family": {
            "size": FAMILY_COUNT,
            "factors": list(FACTORS),
            "horizons": list(HORIZONS),
            "rank_directions": list(RANK_DIRECTIONS),
            "reporting_order": config["family_reporting_order"],
        },
        "policies": {
            "rank_policy": config["rank_policy"],
            "top_k": config["top_k"],
            "weighting": config["weighting"],
            "insufficient_assets_policy": config["insufficient_assets_policy"],
            "cutoff_tie_policy": config["cutoff_tie_policy"],
            "signal_data_cutoff": config["signal_data_cutoff"],
            "future_data_usage_prohibited": config["future_data_usage_prohibited"],
        },
        "artifacts": {
            "factor_scores_sha256": _digest(score_bytes),
            "decisions_sha256": _digest(decision_bytes),
            "weights_sha256": _digest(weight_bytes),
            "constraints_sha256": _digest(constraint_bytes),
        },
        "claims": config["claims"],
    }
    ledger_sha = _digest(_canonical_json_bytes(identity))
    output = _reports_output(repo, output_dir)
    stem = f"prospective-portfolio-ledger.{ledger_sha}"
    paths = {
        "factor_scores": output / f"{stem}.factor-scores.csv",
        "decisions": output / f"{stem}.decisions.csv",
        "weights": output / f"{stem}.weights.csv",
        "constraints": output / f"{stem}.constraints.csv",
        "report": output / f"{stem}.json",
    }
    _commit_bytes(paths["factor_scores"], score_bytes)
    _commit_bytes(paths["decisions"], decision_bytes)
    _commit_bytes(paths["weights"], weight_bytes)
    _commit_bytes(paths["constraints"], constraint_bytes)
    report = {
        "schema_version": SCHEMA_VERSION,
        "ledger_sha256": ledger_sha,
        "ledger_status": LEDGER_STATUS,
        "identity": identity,
        "factor_score_row_count": len(score_rows),
        "decision_row_count": len(decisions),
        "weight_row_count": len(weights),
        "signal_timestamp_count": len(timestamps),
        "family_count": FAMILY_COUNT,
        "all_cash_decision_count": sum(row["decision_status"] == "all_cash" for row in decisions),
        "artifacts": {
            "factor_scores": {"filename": paths["factor_scores"].name, "sha256": identity["artifacts"]["factor_scores_sha256"], "row_count": len(score_rows)},
            "decisions": {"filename": paths["decisions"].name, "sha256": identity["artifacts"]["decisions_sha256"], "row_count": len(decisions)},
            "weights": {"filename": paths["weights"].name, "sha256": identity["artifacts"]["weights_sha256"], "row_count": len(weights)},
            "constraints": {"filename": paths["constraints"].name, "sha256": identity["artifacts"]["constraints_sha256"], "row_count": len(constraints)},
            "report": {"filename": paths["report"].name},
        },
        "future_only_membership_evidence": True,
        "membership_piecewise_constant_between_snapshots": True,
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "selection_prohibited": True,
        "return_computation_authorized": False,
        "turnover_computation_authorized": False,
        "cost_application_authorized": False,
        "pnl_computation_authorized": False,
        "profitability_evidence": False,
        "readiness_changed": False,
    }
    _commit_bytes(paths["report"], _pretty_json_bytes(report))
    return ProspectivePortfolioLedgerResult(report, {key: str(value) for key, value in paths.items()})


def load_ledger_config(path: str | Path, repo: Path | None = None) -> dict[str, Any]:
    config_file = Path(path).resolve()
    if not config_file.is_file() or config_file.name != DEFAULT_CONFIG_FILENAME:
        raise ValueError("prospective portfolio ledger config filename is not frozen")
    value = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    frozen = yaml.safe_load((Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    if value != frozen:
        raise ValueError("prospective portfolio ledger config must equal the frozen config")
    if repo is not None and not config_file.is_relative_to(repo.resolve()):
        raise MarketDataError("prospective_ledger_config_path_escape")
    if value["tracked_inst_ids"] != list(INST_IDS) or value["required_signal_count"] != SIGNAL_COUNT or value["family_size"] != FAMILY_COUNT:
        raise MarketDataError("prospective_ledger_config_shape_mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def format_prospective_portfolio_ledger(result: ProspectivePortfolioLedgerResult) -> str:
    report = result.report
    return "\n".join([
        f"ledger_status: {report['ledger_status']}",
        f"ledger_sha256: {report['ledger_sha256']}",
        f"signal_timestamp_count: {report['signal_timestamp_count']}",
        f"family_count: {report['family_count']}",
        f"factor_score_row_count: {report['factor_score_row_count']}",
        f"decision_row_count: {report['decision_row_count']}",
        f"weight_row_count: {report['weight_row_count']}",
        f"all_cash_decision_count: {report['all_cash_decision_count']}",
        "return_computation_authorized: false",
        "pnl_computation_authorized: false",
        "profitability_evidence: false",
        "readiness_changed: false",
    ])


def _validate_preregistration(path: Path, repo: Path, config: dict[str, Any]) -> dict[str, Any]:
    if _sha256(path) != config["preregistration_report_sha256"]:
        raise MarketDataError("prospective_ledger_preregistration_report_hash_mismatch")
    report = _load_json(path)
    if report.get("preregistration_sha256") != config["preregistration_sha256"] or report.get("audit_status") != "verified_cross_sectional_variant_family_preregistration" or report.get("family_size") != FAMILY_COUNT or report.get("selection_prohibited") is not True or report.get("feasibility", {}).get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("prospective_ledger_preregistration_claims_mismatch")
    identity = report.get("identity", {})
    if identity.get("mechanism", {}).get("mechanism_sha256") != config["mechanism_sha256"] or identity.get("family", {}).get("size") != FAMILY_COUNT or identity.get("family", {}).get("factor_names") != list(FACTORS):
        raise MarketDataError("prospective_ledger_preregistration_policy_mismatch")
    mechanism_report = repo / "reports/cross-sectional-portfolio-mechanism" / f"cross-sectional-portfolio-mechanism.{config['mechanism_sha256']}.json"
    validate_cross_sectional_portfolio_mechanism(mechanism_report)
    return report


def _validate_mapping(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    report = _load_json(path)
    if path.name != f"okx-direct-six-1h-execution-mapping.{config['execution_mapping_sha256']}.json" or _sha256(path) != config["execution_mapping_report_sha256"] or report.get("audit_sha256") != config["execution_mapping_sha256"] or report.get("audit_status") != "verified_okx_direct_six_1h_execution_mapping_feasibility" or report.get("feasibility", {}).get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("prospective_ledger_execution_mapping_claims_mismatch")
    if report.get("feasibility", {}).get("execution_price_mapping_feasible") is not True:
        raise MarketDataError("prospective_ledger_execution_mapping_not_feasible")
    return report


def _validate_extension(path: Path, repo: Path, config: dict[str, Any]) -> dict[str, Any]:
    report = _load_json(path)
    if path.name != f"prospective-direct-1h-extension.{config['market_data_extension_sha256']}.json" or _sha256(path) != config["market_data_extension_report_sha256"] or report.get("extension_sha256") != config["market_data_extension_sha256"] or report.get("audit_status") != "verified_prospective_direct_okx_1h_extension" or report.get("pnl_computation_authorized") is not False or report.get("readiness_changed") is not False:
        raise MarketDataError("prospective_ledger_extension_claims_mismatch")
    capture_sha = report.get("identity", {}).get("capture_sha256")
    capture_path = repo / "reports/prospective-direct-1h-capture" / f"prospective-direct-1h-capture.{capture_sha}.json"
    capture_config = load_prospective_direct_config(repo / "config.okx-prospective-direct-1h-extension.example.yaml", repo)
    capture = validate_prospective_direct_capture(capture_path, capture_config, repo)
    return {"extension_sha256": report["extension_sha256"], "report": report, "capture": capture}


def _load_concatenated_bars(extension: dict[str, Any], repo: Path, config: dict[str, Any]) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, str]]]:
    migration = _load_json(repo / "reports/okx-direct-six-asset-1h-migration/okx-direct-six-migration.67fb338366f01f6ac03e0280f374fb8c6c5578029d7756286d5315ac1cc3b698.json")
    datasets = {item["inst_id"]: item for item in migration["identity"]["datasets"]}
    result: dict[str, pd.DataFrame] = {}
    hashes: dict[str, dict[str, str]] = {}
    capture = extension["capture"]
    for inst_id in INST_IDS:
        baseline_path = repo / datasets[inst_id]["destination_repo_relative_path"]
        append_path = Path(capture.export_paths[f"{inst_id}_append_filename"])
        baseline = load_ohlcv_csv(baseline_path)
        append = load_ohlcv_csv(append_path)
        combined = pd.concat([baseline, append], ignore_index=True)
        if combined["timestamp"].duplicated().any() or not combined["timestamp"].is_monotonic_increasing or len(combined) != 40355:
            raise MarketDataError(f"prospective_ledger_dataset_continuity_mismatch:{inst_id}")
        result[inst_id] = combined
        hashes[inst_id] = {
            "baseline_sha256": _sha256(baseline_path),
            "append_sha256": _sha256(append_path),
        }
    return result, hashes


def _signal_timestamps(eligibility: list[dict[str, Any]], config: dict[str, Any]) -> list[str]:
    rows = [row for row in eligibility if row.get("inst_id") == INST_IDS[0]]
    values = [str(row["signal_timestamp"]).replace("+00:00", "Z") for row in rows]
    if len(values) != SIGNAL_COUNT or values[0] != config["signal_start"] or values[-1] != config["signal_end"] or len(set(values)) != SIGNAL_COUNT:
        raise MarketDataError("prospective_ledger_signal_grid_mismatch")
    if any(row.get("eligible_for_closed_epoch") != "true" for row in rows):
        raise MarketDataError("prospective_ledger_membership_not_complete")
    return values


def _factor_score_rows(bars: dict[str, pd.DataFrame], timestamps: list[str], config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for timestamp in timestamps:
        cutoff = pd.Timestamp(timestamp)
        for symbol in INST_IDS:
            frame = bars[symbol]
            truncated = frame.loc[frame["timestamp"] <= cutoff].reset_index(drop=True)
            factors = compute_factor_frame(truncated, DEFAULT_FACTOR_SPECS)
            last = factors.iloc[-1]
            for factor in FACTORS:
                score = float(last[factor]) if pd.notna(last[factor]) else math.nan
                rows.append({"signal_timestamp": timestamp, "symbol": symbol, "factor": factor, "score": score, "finite": math.isfinite(score), "data_cutoff_timestamp": timestamp})
    return rows


def _decision_rows(score_map: dict[tuple[str, str, str], float], timestamps: list[str], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    for timestamp in timestamps:
        completion = _shift_iso(timestamp, 1)
        execution = _shift_iso(timestamp, 2)
        for factor in FACTORS:
            for horizon in HORIZONS:
                for direction in RANK_DIRECTIONS:
                    family_id = f"{factor}.h{horizon}.{direction}"
                    signals = {symbol: score_map[(timestamp, symbol, factor)] for symbol in INST_IDS}
                    target = build_long_only_target_weights(signals, INST_IDS, rank_direction=direction)
                    finite_values = {symbol: value for symbol, value in signals.items() if math.isfinite(value)}
                    if len(finite_values) == len(INST_IDS):
                        rank_series = pd.Series(signals, dtype=float).rank(method="average", ascending=True)
                        rank_map = {symbol: float(rank_series[symbol]) for symbol in INST_IDS}
                    else:
                        rank_map = {symbol: math.nan for symbol in INST_IDS}
                    selected = [symbol for symbol, weight in target.weights if weight > 0]
                    decisions.append({"family_member_id": family_id, "factor": factor, "horizon": horizon, "rank_direction": direction, "signal_timestamp": timestamp, "completion_timestamp": completion, "execution_timestamp": execution, "decision_status": target.status, "decision_reason": target.reason, "selected_asset_1": selected[0] if len(selected) > 0 else "", "selected_asset_2": selected[1] if len(selected) > 1 else "", "cash_weight": target.cash_weight, "gross_exposure": target.gross_exposure, "net_exposure": target.net_exposure})
                    for symbol in INST_IDS:
                        weight = dict(target.weights)[symbol]
                        weights.append({"family_member_id": family_id, "signal_timestamp": timestamp, "symbol": symbol, "rank": rank_map[symbol], "selected": weight > 0, "weight": weight})
    return decisions, weights


def _constraint_rows(config: dict[str, Any], prereg: dict[str, Any], gate: Any, extension: dict[str, Any], mapping: dict[str, Any]) -> list[dict[str, Any]]:
    values: dict[str, Any] = {
        "policy_id": config["policy_id"],
        "preregistration_sha256": prereg["preregistration_sha256"],
        "mechanism_sha256": config["mechanism_sha256"],
        "membership_gate_sha256": gate.report["gate_sha256"],
        "market_data_extension_sha256": extension["extension_sha256"],
        "execution_mapping_sha256": mapping["audit_sha256"],
        "tracked_inst_ids": list(INST_IDS),
        "signal_count": SIGNAL_COUNT,
        "family_count": FAMILY_COUNT,
        "factor_policy": config["factor_policy"],
        "signal_data_cutoff": config["signal_data_cutoff"],
        "future_data_usage_prohibited": config["future_data_usage_prohibited"],
        "membership_policy": config["membership_policy"],
        "return_computation_authorized": False,
        "turnover_computation_authorized": False,
        "cost_application_authorized": False,
        "pnl_computation_authorized": False,
        "profitability_evidence": False,
        "readiness_changed": False,
    }
    return [{"key": key, "value": json.dumps(values[key], ensure_ascii=False, sort_keys=True)} for key in sorted(values)]


def _shift_iso(value: str, hours: int) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(hours=hours)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pin_path(repo: Path, actual: Path, relative: str) -> None:
    if actual != (repo / relative).resolve():
        raise MarketDataError("prospective_ledger_input_path_mismatch")


def _repo_root(path: Path) -> Path:
    for parent in (path, *path.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    raise MarketDataError("prospective_ledger_repo_root_not_found")


def _reports_output(repo: Path, output_dir: str | Path) -> Path:
    output = (repo / output_dir).resolve() if not Path(output_dir).is_absolute() else Path(output_dir).resolve()
    if not output.is_relative_to((repo / "reports").resolve()):
        raise ValueError("prospective portfolio ledger output must stay inside reports")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("prospective_ledger_invalid_json") from exc
    if not isinstance(value, dict):
        raise MarketDataError("prospective_ledger_invalid_json")
    return value


def _csv_bytes(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row[field]) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return "" if math.isnan(value) else format(value, ".15g")
    return value


def _commit_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise MarketDataError(f"prospective_ledger_content_addressed_collision:{path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".prospective-ledger-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
