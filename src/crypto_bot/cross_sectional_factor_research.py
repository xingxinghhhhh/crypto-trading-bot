from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
import yaml

from crypto_bot.errors import MarketDataError
from crypto_bot.factors import (
    DEFAULT_FACTOR_SPECS,
    FactorSpec,
    compute_factor_frame,
    compute_forward_returns,
)
from crypto_bot.market.dataset_panel import DatasetPanelResult, build_dataset_panel
from crypto_bot.market.dataset_registry import (
    canonicalize_ohlcv_frame,
    load_dataset_registry,
    write_json_atomically,
)


CROSS_SECTIONAL_RESEARCH_SCHEMA_VERSION = 1
_IC_FIELDS = (
    "factor_name",
    "horizon_bars",
    "timestamp",
    "status",
    "n_valid_assets",
    "required_asset_count",
    "factor_unique_count",
    "forward_return_unique_count",
    "rank_ic",
)
_OBSERVATION_FIELDS = (
    "factor_name",
    "horizon_bars",
    "timestamp",
    "symbol",
    "dataset_id",
    "factor_value",
    "factor_rank",
    "forward_return",
    "forward_return_rank",
)
_STATUSES = (
    "valid",
    "insufficient_assets",
    "constant_both",
    "constant_factor",
    "constant_forward_return",
    "undefined_correlation",
)
_SUPPORTED_FACTOR_FAMILIES = frozenset(spec.family for spec in DEFAULT_FACTOR_SPECS)


@dataclass(frozen=True)
class CrossSectionalResearchConfig:
    specs: tuple[FactorSpec, ...]
    horizons: tuple[int, ...]


@dataclass(frozen=True)
class CrossSectionalResearchResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class CrossSectionalSlice:
    status: str
    n_valid_assets: int
    factor_unique_count: int
    forward_return_unique_count: int
    rank_ic: float | None
    factor_ranks: tuple[float, ...] | None
    forward_return_ranks: tuple[float, ...] | None


def load_cross_sectional_research_config(path: str | Path) -> CrossSectionalResearchConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"cross-sectional research config not found: {config_path}")
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"cross-sectional research config YAML is invalid: {config_path}") from exc
    raw = {} if loaded is None else loaded
    if not isinstance(raw, dict):
        raise ValueError("cross-sectional research config must be a mapping")
    if raw.get("schema_version") != CROSS_SECTIONAL_RESEARCH_SCHEMA_VERSION:
        raise ValueError(
            f"cross-sectional research schema_version must be {CROSS_SECTIONAL_RESEARCH_SCHEMA_VERSION}"
        )
    raw_factors = raw.get("factors")
    if not isinstance(raw_factors, list) or not raw_factors:
        raise ValueError("cross-sectional research factors must be a non-empty list")
    specs = tuple(sorted((_parse_factor_spec(item) for item in raw_factors), key=_factor_sort_key))
    names = [spec.name for spec in specs]
    if len(names) != len(set(names)):
        raise ValueError("cross-sectional research factor names must be unique")
    raw_horizons = raw.get("horizons")
    if not isinstance(raw_horizons, list) or not raw_horizons:
        raise ValueError("cross-sectional research horizons must be a non-empty list")
    if any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in raw_horizons):
        raise ValueError("cross-sectional research horizons must be positive integers")
    return CrossSectionalResearchConfig(specs=specs, horizons=tuple(sorted(set(raw_horizons))))


def run_cross_sectional_factor_research(
    registry_path: str | Path,
    panels_config_path: str | Path,
    panel_id: str,
    research_config_path: str | Path,
    output_dir: str | Path,
    *,
    promotion_context: dict[str, Any] | None = None,
) -> CrossSectionalResearchResult:
    config = load_cross_sectional_research_config(research_config_path)
    panel = build_dataset_panel(registry_path, panels_config_path, panel_id)
    registry = load_dataset_registry(registry_path)
    entries = tuple(registry.get(dataset_id) for dataset_id in panel.report["dataset_ids"])
    source_frames = {
        entry.dataset_id: canonicalize_ohlcv_frame(pd.read_csv(entry.resolved_path))
        for entry in entries
    }
    computed = {
        entry.dataset_id: _compute_source_research_frame(source_frames[entry.dataset_id], config)
        for entry in entries
    }
    panel_timestamps = pd.DatetimeIndex(pd.to_datetime(panel.frame["timestamp"], utc=True))
    aligned = {
        entry.dataset_id: computed[entry.dataset_id].set_index("timestamp").reindex(panel_timestamps)
        for entry in entries
    }
    return _write_research_artifacts(
        panel,
        entries=entries,
        aligned=aligned,
        panel_timestamps=panel_timestamps,
        config=config,
        output_dir=Path(output_dir),
        promotion_context=_normalize_promotion_context(promotion_context),
    )


def evaluate_cross_sectional_slice(
    factor_values: list[float] | tuple[float, ...] | np.ndarray,
    forward_returns: list[float] | tuple[float, ...] | np.ndarray,
    *,
    required_asset_count: int,
) -> CrossSectionalSlice:
    factors = np.asarray(factor_values, dtype=np.float64)
    returns = np.asarray(forward_returns, dtype=np.float64)
    if factors.shape != returns.shape or factors.ndim != 1:
        raise ValueError("cross-sectional values must be equal-length one-dimensional arrays")
    if required_asset_count <= 0:
        raise ValueError("required_asset_count must be positive")
    finite = np.isfinite(factors) & np.isfinite(returns)
    n_valid = int(finite.sum())
    valid_factors = factors[finite]
    valid_returns = returns[finite]
    factor_unique = int(np.unique(valid_factors).size)
    return_unique = int(np.unique(valid_returns).size)
    if n_valid != required_asset_count:
        return CrossSectionalSlice(
            "insufficient_assets",
            n_valid,
            factor_unique,
            return_unique,
            None,
            None,
            None,
        )
    if factor_unique == 1 and return_unique == 1:
        status = "constant_both"
    elif factor_unique == 1:
        status = "constant_factor"
    elif return_unique == 1:
        status = "constant_forward_return"
    else:
        status = "valid"
    if status != "valid":
        return CrossSectionalSlice(status, n_valid, factor_unique, return_unique, None, None, None)
    factor_ranks = _average_ranks(valid_factors)
    return_ranks = _average_ranks(valid_returns)
    rank_ic = _pearson(factor_ranks, return_ranks)
    if rank_ic is None:
        return CrossSectionalSlice(
            "undefined_correlation",
            n_valid,
            factor_unique,
            return_unique,
            None,
            tuple(float(value) for value in factor_ranks),
            tuple(float(value) for value in return_ranks),
        )
    return CrossSectionalSlice(
        "valid",
        n_valid,
        factor_unique,
        return_unique,
        rank_ic,
        tuple(float(value) for value in factor_ranks),
        tuple(float(value) for value in return_ranks),
    )


def format_cross_sectional_factor_research(result: CrossSectionalResearchResult) -> str:
    report = result.report
    return "\n".join(
        [
            f"research_status: {report['research_status']}",
            f"research_sha256: {report['research_sha256']}",
            f"panel_id: {report['panel']['panel_id']}",
            f"panel_sha256: {report['panel']['panel_sha256']}",
            f"factor_count: {len(report['factor_specs'])}",
            f"horizon_count: {len(report['horizons'])}",
            f"summary_count: {len(report['summaries'])}",
            "readiness_changed: false",
            "automatic_factor_approval: false",
        ]
    )


def _parse_factor_spec(raw: object) -> FactorSpec:
    if not isinstance(raw, dict):
        raise ValueError("cross-sectional research factor entries must be mappings")
    family = raw.get("family")
    window = raw.get("window")
    if not isinstance(family, str) or family not in _SUPPORTED_FACTOR_FAMILIES:
        raise ValueError(f"cross-sectional research unsupported factor family: {family}")
    if not isinstance(window, int) or isinstance(window, bool) or window <= 1:
        raise ValueError("cross-sectional research factor window must be greater than one")
    return FactorSpec(family=family, window=window)


def _factor_sort_key(spec: FactorSpec) -> tuple[str, str, int]:
    return spec.name, spec.family, spec.window


def _compute_source_research_frame(
    source: pd.DataFrame,
    config: CrossSectionalResearchConfig,
) -> pd.DataFrame:
    factors = compute_factor_frame(source, config.specs)
    forward_returns = compute_forward_returns(source, config.horizons)
    return factors.merge(forward_returns, on="timestamp", how="inner", validate="one_to_one")


def _write_research_artifacts(
    panel: DatasetPanelResult,
    *,
    entries: tuple[Any, ...],
    aligned: dict[str, pd.DataFrame],
    panel_timestamps: pd.DatetimeIndex,
    config: CrossSectionalResearchConfig,
    output_dir: Path,
    promotion_context: dict[str, Any] | None,
) -> CrossSectionalResearchResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    ic_temporary = output_dir / f".cross-sectional-ic.{uuid4().hex}.tmp"
    observations_temporary = output_dir / f".cross-sectional-observations.{uuid4().hex}.tmp"
    summaries: list[dict[str, Any]] = []
    ic_row_count = 0
    observation_row_count = 0
    try:
        with ic_temporary.open("w", encoding="utf-8", newline="") as ic_handle, observations_temporary.open(
            "w", encoding="utf-8", newline=""
        ) as observations_handle:
            ic_writer = csv.DictWriter(ic_handle, fieldnames=_IC_FIELDS, lineterminator="\n")
            observations_writer = csv.DictWriter(
                observations_handle,
                fieldnames=_OBSERVATION_FIELDS,
                lineterminator="\n",
            )
            ic_writer.writeheader()
            observations_writer.writeheader()
            for spec in config.specs:
                for horizon in config.horizons:
                    summary, written_observations = _write_factor_horizon_rows(
                        ic_writer,
                        observations_writer,
                        panel_timestamps=panel_timestamps,
                        entries=entries,
                        aligned=aligned,
                        spec=spec,
                        horizon=horizon,
                    )
                    summaries.append(summary)
                    ic_row_count += int(summary["candidate_timestamp_count"])
                    observation_row_count += written_observations
        ic_sha256 = _file_sha256(ic_temporary)
        observations_sha256 = _file_sha256(observations_temporary)
        identity = _research_identity(
            panel,
            config=config,
            ic_sha256=ic_sha256,
            observations_sha256=observations_sha256,
            promotion_context=promotion_context,
        )
        research_sha256 = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
        prefix = (
            "promoted-cross-sectional-research"
            if promotion_context is not None
            else "cross-sectional-factor-research"
        )
        stem = f"{prefix}.{research_sha256}"
        ic_path = output_dir / f"{stem}.ic.csv"
        observations_path = output_dir / f"{stem}.observations.csv"
        report_path = output_dir / f"{stem}.json"
        report = _build_report(
            panel,
            config=config,
            summaries=summaries,
            research_sha256=research_sha256,
            ic_filename=ic_path.name,
            ic_sha256=ic_sha256,
            ic_row_count=ic_row_count,
            observations_filename=observations_path.name,
            observations_sha256=observations_sha256,
            observation_row_count=observation_row_count,
            promotion_context=promotion_context,
        )
        _commit_content_addressed(ic_temporary, ic_path, ic_sha256)
        _commit_content_addressed(observations_temporary, observations_path, observations_sha256)
        _commit_report(report_path, report)
        return CrossSectionalResearchResult(
            report=report,
            export_paths={
                "report": str(report_path),
                "ic_time_series": str(ic_path),
                "valid_observations": str(observations_path),
            },
        )
    finally:
        for temporary in (ic_temporary, observations_temporary):
            if temporary.exists():
                temporary.unlink()


def _write_factor_horizon_rows(
    ic_writer: csv.DictWriter,
    observations_writer: csv.DictWriter,
    *,
    panel_timestamps: pd.DatetimeIndex,
    entries: tuple[Any, ...],
    aligned: dict[str, pd.DataFrame],
    spec: FactorSpec,
    horizon: int,
) -> tuple[dict[str, Any], int]:
    factor_values = np.column_stack(
        [aligned[entry.dataset_id][spec.name].to_numpy(dtype=np.float64) for entry in entries]
    )
    forward_column = f"forward_return_{horizon}"
    forward_returns = np.column_stack(
        [aligned[entry.dataset_id][forward_column].to_numpy(dtype=np.float64) for entry in entries]
    )
    counts = {status: 0 for status in _STATUSES}
    valid_ics: list[float] = []
    observation_count = 0
    required_asset_count = len(entries)
    for index, timestamp in enumerate(panel_timestamps):
        result = evaluate_cross_sectional_slice(
            factor_values[index],
            forward_returns[index],
            required_asset_count=required_asset_count,
        )
        counts[result.status] += 1
        timestamp_text = pd.Timestamp(timestamp).isoformat()
        ic_writer.writerow(
            {
                "factor_name": spec.name,
                "horizon_bars": horizon,
                "timestamp": timestamp_text,
                "status": result.status,
                "n_valid_assets": result.n_valid_assets,
                "required_asset_count": required_asset_count,
                "factor_unique_count": result.factor_unique_count,
                "forward_return_unique_count": result.forward_return_unique_count,
                "rank_ic": _format_optional_float(result.rank_ic),
            }
        )
        if result.status != "valid":
            continue
        assert result.rank_ic is not None
        assert result.factor_ranks is not None
        assert result.forward_return_ranks is not None
        valid_ics.append(result.rank_ic)
        for entry_index, entry in enumerate(entries):
            observations_writer.writerow(
                {
                    "factor_name": spec.name,
                    "horizon_bars": horizon,
                    "timestamp": timestamp_text,
                    "symbol": entry.symbol,
                    "dataset_id": entry.dataset_id,
                    "factor_value": _format_optional_float(float(factor_values[index, entry_index])),
                    "factor_rank": _format_optional_float(result.factor_ranks[entry_index]),
                    "forward_return": _format_optional_float(float(forward_returns[index, entry_index])),
                    "forward_return_rank": _format_optional_float(result.forward_return_ranks[entry_index]),
                }
            )
            observation_count += 1
    summary = {
        "factor_name": spec.name,
        "horizon_bars": horizon,
        "candidate_timestamp_count": len(panel_timestamps),
        "valid_ic_timestamp_count": counts["valid"],
        "insufficient_assets_count": counts["insufficient_assets"],
        "constant_both_count": counts["constant_both"],
        "constant_factor_count": counts["constant_factor"],
        "constant_forward_return_count": counts["constant_forward_return"],
        "undefined_correlation_count": counts["undefined_correlation"],
        "mean_rank_ic": _aggregate(valid_ics, "mean"),
        "median_rank_ic": _aggregate(valid_ics, "median"),
        "sample_std_rank_ic": _aggregate(valid_ics, "std"),
        "positive_ratio": (
            _round_float(float(np.mean(np.asarray(valid_ics) > 0))) if valid_ics else None
        ),
        "unique_rank_ic_value_count": len(set(valid_ics)),
    }
    if sum(counts.values()) != len(panel_timestamps):
        raise MarketDataError("cross_sectional_status_count_invariant_failed")
    return summary, observation_count


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        ranks[order[start:end]] = average_rank
        start = end
    return ranks


def _pearson(left: np.ndarray, right: np.ndarray) -> float | None:
    left_centered = left - left.mean()
    right_centered = right - right.mean()
    denominator = float(np.sqrt(np.dot(left_centered, left_centered) * np.dot(right_centered, right_centered)))
    if denominator == 0 or not np.isfinite(denominator):
        return None
    value = float(np.dot(left_centered, right_centered) / denominator)
    if not np.isfinite(value):
        return None
    return _round_float(max(-1.0, min(1.0, value)))


def _aggregate(values: list[float], operation: str) -> float | None:
    if not values:
        return None
    array = np.asarray(values, dtype=np.float64)
    if operation == "mean":
        return _round_float(float(array.mean()))
    if operation == "median":
        return _round_float(float(np.median(array)))
    if operation == "std":
        return _round_float(float(array.std(ddof=1))) if len(array) >= 2 else None
    raise ValueError(f"unsupported aggregate operation: {operation}")


def _round_float(value: float) -> float:
    return round(value, 12)


def _format_optional_float(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return ""
    return format(value, ".15g")


def _research_identity(
    panel: DatasetPanelResult,
    *,
    config: CrossSectionalResearchConfig,
    ic_sha256: str,
    observations_sha256: str,
    promotion_context: dict[str, Any] | None,
) -> dict[str, Any]:
    report = panel.report
    identity = {
        "schema_version": CROSS_SECTIONAL_RESEARCH_SCHEMA_VERSION,
        "panel_id": report["panel_id"],
        "panel_sha256": report["panel_sha256"],
        "alignment": report["alignment"],
        "timestamp_semantics_status": report["timestamp_semantics"]["status"],
        "components": [
            {
                "dataset_id": item["dataset_id"],
                "symbol": item["symbol"],
                "raw_sha256": item["raw_sha256"],
                "canonical_sha256": item["canonical_sha256"],
            }
            for item in report["datasets"]
        ],
        "factor_specs": [asdict(spec) | {"name": spec.name} for spec in config.specs],
        "horizons": list(config.horizons),
        "required_asset_count": len(report["dataset_ids"]),
        "policies": _policies(),
        "artifacts": {
            "ic_time_series_sha256": ic_sha256,
            "valid_observations_sha256": observations_sha256,
        },
    }
    if promotion_context is not None:
        identity["promotion"] = promotion_context
    return identity


def _policies() -> dict[str, str]:
    return {
        "factor_computation": "per_asset_full_source_history_then_panel_timestamp_filter",
        "forward_return": "close_shift_minus_horizon_divided_by_close_minus_one",
        "required_assets": "all_panel_constituents",
        "rank": "ascending_average_ties",
        "constant": "explicit_skip_status",
        "non_finite": "paired_finite_only_no_fill",
        "rank_ic": "pearson_correlation_of_cross_sectional_average_ranks",
    }


def _build_report(
    panel: DatasetPanelResult,
    *,
    config: CrossSectionalResearchConfig,
    summaries: list[dict[str, Any]],
    research_sha256: str,
    ic_filename: str,
    ic_sha256: str,
    ic_row_count: int,
    observations_filename: str,
    observations_sha256: str,
    observation_row_count: int,
    promotion_context: dict[str, Any] | None,
) -> dict[str, Any]:
    panel_report = panel.report
    report = {
        "schema_version": CROSS_SECTIONAL_RESEARCH_SCHEMA_VERSION,
        "research_sha256": research_sha256,
        "research_status": "cross_sectional_rank_ic_evidence_only",
        "panel": {
            "panel_id": panel_report["panel_id"],
            "panel_sha256": panel_report["panel_sha256"],
            "alignment": panel_report["alignment"],
            "timeframe": panel_report["timeframe"],
            "symbols": panel_report["symbols"],
            "dataset_ids": panel_report["dataset_ids"],
            "timestamp_semantics": panel_report["timestamp_semantics"],
            "alignment_summary": panel_report["alignment_summary"],
            "datasets": panel_report["datasets"],
        },
        "factor_specs": [asdict(spec) | {"name": spec.name} for spec in config.specs],
        "horizons": list(config.horizons),
        "required_asset_count": len(panel_report["dataset_ids"]),
        "policies": _policies(),
        "summaries": summaries,
        "artifacts": {
            "ic_time_series": {
                "filename": ic_filename,
                "sha256": ic_sha256,
                "row_count": ic_row_count,
            },
            "valid_observations": {
                "filename": observations_filename,
                "sha256": observations_sha256,
                "row_count": observation_row_count,
            },
        },
        "warnings": (
            [
                "timestamp_semantics_not_uniformly_verified",
                "six_asset_convenience_cross_section_has_limited_statistical_power",
                "historical_point_in_time_membership_not_proven",
                "survivorship_bias_not_resolved",
                "rank_ic_is_not_portfolio_pnl",
                "no_automatic_readiness_upgrade",
            ]
            if promotion_context is not None
            else [
                "timestamp_semantics_not_uniformly_verified",
                "three_asset_cross_section_has_limited_statistical_power",
                "fixed_universe_selection_and_survivorship_bias_not_corrected",
                "rank_ic_is_not_portfolio_pnl",
                "no_significance_or_multiple_testing_adjustment",
                "no_automatic_readiness_upgrade",
            ]
        ),
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    if promotion_context is not None:
        report["promotion"] = promotion_context
    return report


def _normalize_promotion_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if context is None:
        return None
    if not isinstance(context, dict) or not context:
        raise ValueError("promotion_context must be a non-empty mapping")
    return json.loads(json.dumps(context, ensure_ascii=False, sort_keys=True))


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit_content_addressed(temporary: Path, target: Path, expected_sha256: str) -> None:
    if target.exists():
        if _file_sha256(target) != expected_sha256:
            raise MarketDataError(f"content_addressed_artifact_collision:{target.name}")
        temporary.unlink()
        return
    temporary.replace(target)


def _commit_report(path: Path, report: dict[str, Any]) -> None:
    expected = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if path.exists():
        if path.read_bytes() != expected:
            raise MarketDataError(f"content_addressed_artifact_collision:{path.name}")
        return
    write_json_atomically(path, report)
