from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from crypto_bot.cross_sectional_ic_calibration import (
    CrossSectionalICSeries,
    ValidatedCrossSectionalICEvidence,
    load_validated_cross_sectional_ic_evidence,
)
from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_registry import write_json_atomically


OOS_STABILITY_SCHEMA_VERSION = 1
OOS_STABILITY_STATUS = "post_hoc_chronological_oos_stability_only"
PROMOTED_OOS_STABILITY_STATUS = "promotion_aware_chronological_oos_stability_only"
FOLD_COUNT = 10
MINIMUM_TRAIN_VALID_COUNT = 1000
MINIMUM_OOS_VALID_COUNT = 500

_FOLD_FIELDS = (
    "factor_name",
    "horizon_bars",
    "fold_index",
    "train_raw_start_timestamp",
    "train_raw_end_timestamp",
    "train_effective_end_timestamp",
    "oos_raw_start_timestamp",
    "oos_raw_end_timestamp",
    "oos_effective_end_timestamp",
    "train_raw_candidate_count",
    "train_purged_candidate_count",
    "train_candidate_count",
    "train_prefix_invalid_count",
    "train_valid_count",
    "oos_raw_candidate_count",
    "oos_purged_candidate_count",
    "oos_valid_count",
    "train_mean_rank_ic",
    "oos_mean_rank_ic",
    "mean_rank_ic_difference",
    "train_median_rank_ic",
    "oos_median_rank_ic",
    "median_rank_ic_difference",
    "train_sample_std_rank_ic",
    "oos_sample_std_rank_ic",
    "sample_std_rank_ic_difference",
    "train_positive_ratio",
    "oos_positive_ratio",
    "positive_ratio_difference",
)
_SUMMARY_FIELDS = (
    "factor_name",
    "horizon_bars",
    "fold_count",
    "pooled_oos_valid_count",
    "pooled_oos_mean_rank_ic",
    "pooled_oos_median_rank_ic",
    "pooled_oos_sample_std_rank_ic",
    "pooled_oos_positive_ratio",
    "fold_mean_mean_rank_ic",
    "fold_mean_median_rank_ic",
    "fold_mean_sample_std_rank_ic",
    "positive_fold_ratio",
    "best_fold_mean_rank_ic",
    "worst_fold_mean_rank_ic",
    "adjacent_strict_sign_switch_count",
)


@dataclass(frozen=True)
class ExpandingOOSWindow:
    fold_index: int
    train_raw_start: int
    train_raw_end_exclusive: int
    train_effective_end_exclusive: int
    oos_raw_start: int
    oos_raw_end_exclusive: int
    oos_effective_end_exclusive: int


@dataclass(frozen=True)
class DistributionMetrics:
    count: int
    mean: float
    median: float
    sample_std: float
    positive_ratio: float


@dataclass(frozen=True)
class CrossSectionalOOSStabilityResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


def build_purged_expanding_windows(
    candidate_count: int,
    *,
    horizon_bars: int,
) -> tuple[ExpandingOOSWindow, ...]:
    if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) or candidate_count <= 0:
        raise ValueError("candidate_count must be a positive integer")
    if not isinstance(horizon_bars, int) or isinstance(horizon_bars, bool) or horizon_bars <= 0:
        raise ValueError("horizon_bars must be a positive integer")
    initial_history = candidate_count // 2
    oos_count = candidate_count - initial_history
    base_size, remainder = divmod(oos_count, FOLD_COUNT)
    if base_size <= horizon_bars:
        raise MarketDataError("cross_sectional_oos_stability_fold_too_short_for_purge")
    sizes = [base_size + (1 if index < remainder else 0) for index in range(FOLD_COUNT)]
    windows: list[ExpandingOOSWindow] = []
    oos_start = initial_history
    for index, size in enumerate(sizes, start=1):
        oos_end = oos_start + size
        train_effective_end = oos_start - horizon_bars
        if train_effective_end <= 0:
            raise MarketDataError("cross_sectional_oos_stability_empty_train_after_purge")
        windows.append(
            ExpandingOOSWindow(
                fold_index=index,
                train_raw_start=0,
                train_raw_end_exclusive=oos_start,
                train_effective_end_exclusive=train_effective_end,
                oos_raw_start=oos_start,
                oos_raw_end_exclusive=oos_end,
                oos_effective_end_exclusive=oos_end - horizon_bars,
            )
        )
        oos_start = oos_end
    if oos_start != candidate_count:
        raise AssertionError("OOS fold construction did not consume the candidate timeline")
    return tuple(windows)


def distribution_metrics(values: Sequence[float]) -> DistributionMetrics:
    observations = [float(value) for value in values]
    if len(observations) < 2:
        raise MarketDataError("cross_sectional_oos_stability_insufficient_metric_sample")
    if any(not math.isfinite(value) for value in observations):
        raise MarketDataError("cross_sectional_oos_stability_non_finite_metric_input")
    return DistributionMetrics(
        count=len(observations),
        mean=_normalized_float(math.fsum(observations) / len(observations)),
        median=_normalized_float(float(statistics.median(observations))),
        sample_std=_normalized_float(float(statistics.stdev(observations))),
        positive_ratio=_normalized_float(
            math.fsum(1.0 for value in observations if value > 0) / len(observations)
        ),
    )


def run_cross_sectional_oos_stability(
    source_report_path: str | Path,
    output_dir: str | Path,
) -> CrossSectionalOOSStabilityResult:
    evidence = load_validated_cross_sectional_ic_evidence(source_report_path)
    fold_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    for key, series in evidence.series_by_key.items():
        windows = build_purged_expanding_windows(
            len(evidence.candidate_timestamps), horizon_bars=key[1]
        )
        series_folds, summary = _analyze_series(series, evidence.candidate_timestamps, windows)
        fold_rows.extend(series_folds)
        summary_rows.append(summary)

    folds_bytes = _serialize_rows(fold_rows, _FOLD_FIELDS)
    summaries_bytes = _serialize_rows(summary_rows, _SUMMARY_FIELDS)
    folds_sha256 = hashlib.sha256(folds_bytes).hexdigest()
    summaries_sha256 = hashlib.sha256(summaries_bytes).hexdigest()
    identity = _oos_identity(
        evidence,
        folds_sha256=folds_sha256,
        summaries_sha256=summaries_sha256,
    )
    analysis_sha256 = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    prefix = (
        "promoted-cross-sectional-oos-stability"
        if evidence.source_report.get("promotion") is not None
        else "cross-sectional-oos-stability"
    )
    stem = f"{prefix}.{analysis_sha256}"
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    folds_path = destination / f"{stem}.folds.csv"
    summaries_path = destination / f"{stem}.summaries.csv"
    report_path = destination / f"{stem}.json"
    report = _build_report(
        evidence,
        fold_rows=fold_rows,
        summary_rows=summary_rows,
        folds_filename=folds_path.name,
        folds_sha256=folds_sha256,
        summaries_filename=summaries_path.name,
        summaries_sha256=summaries_sha256,
        analysis_sha256=analysis_sha256,
    )
    _commit_bytes(folds_path, folds_bytes, folds_sha256)
    _commit_bytes(summaries_path, summaries_bytes, summaries_sha256)
    _commit_report(report_path, report)
    return CrossSectionalOOSStabilityResult(
        report=report,
        export_paths={
            "report": str(report_path),
            "folds": str(folds_path),
            "summaries": str(summaries_path),
        },
    )


def format_cross_sectional_oos_stability(result: CrossSectionalOOSStabilityResult) -> str:
    report = result.report
    return "\n".join(
        [
            f"research_status: {report['research_status']}",
            f"analysis_sha256: {report['analysis_sha256']}",
            f"source_research_sha256: {report['source']['research_sha256']}",
            f"panel_id: {report['panel']['panel_id']}",
            f"fold_row_count: {report['fold_row_count']}",
            f"summary_row_count: {report['summary_row_count']}",
            "readiness_changed: false",
            "automatic_factor_approval: false",
        ]
    )


def _analyze_series(
    series: CrossSectionalICSeries,
    candidate_timestamps: tuple[str, ...],
    windows: tuple[ExpandingOOSWindow, ...],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fold_rows: list[dict[str, Any]] = []
    pooled_oos: list[float] = []
    fold_means: list[float] = []
    for window in windows:
        train_statuses = series.statuses[: window.train_effective_end_exclusive]
        prefix_invalid_count = _validate_train_statuses(train_statuses)
        train_values = [
            value
            for value in series.candidate_values[: window.train_effective_end_exclusive]
            if value is not None
        ]
        oos_statuses = series.statuses[window.oos_raw_start : window.oos_effective_end_exclusive]
        if not oos_statuses or any(status != "valid" for status in oos_statuses):
            raise MarketDataError("cross_sectional_oos_stability_invalid_oos_timestamp")
        oos_candidates = series.candidate_values[
            window.oos_raw_start : window.oos_effective_end_exclusive
        ]
        if any(value is None for value in oos_candidates):
            raise MarketDataError("cross_sectional_oos_stability_missing_oos_rank_ic")
        oos_values = [float(value) for value in oos_candidates if value is not None]
        if len(train_values) < MINIMUM_TRAIN_VALID_COUNT:
            raise MarketDataError(
                "cross_sectional_oos_stability_insufficient_train_sample:"
                f"factor={series.factor_name}:horizon={series.horizon_bars}:"
                f"fold={window.fold_index}:n={len(train_values)}"
            )
        if len(oos_values) < MINIMUM_OOS_VALID_COUNT:
            raise MarketDataError(
                "cross_sectional_oos_stability_insufficient_oos_sample:"
                f"factor={series.factor_name}:horizon={series.horizon_bars}:"
                f"fold={window.fold_index}:n={len(oos_values)}"
            )
        train = distribution_metrics(train_values)
        oos = distribution_metrics(oos_values)
        pooled_oos.extend(oos_values)
        fold_means.append(oos.mean)
        fold_rows.append(
            {
                "factor_name": series.factor_name,
                "horizon_bars": series.horizon_bars,
                "fold_index": window.fold_index,
                "train_raw_start_timestamp": candidate_timestamps[window.train_raw_start],
                "train_raw_end_timestamp": candidate_timestamps[window.train_raw_end_exclusive - 1],
                "train_effective_end_timestamp": candidate_timestamps[
                    window.train_effective_end_exclusive - 1
                ],
                "oos_raw_start_timestamp": candidate_timestamps[window.oos_raw_start],
                "oos_raw_end_timestamp": candidate_timestamps[window.oos_raw_end_exclusive - 1],
                "oos_effective_end_timestamp": candidate_timestamps[
                    window.oos_effective_end_exclusive - 1
                ],
                "train_raw_candidate_count": window.train_raw_end_exclusive,
                "train_purged_candidate_count": series.horizon_bars,
                "train_candidate_count": window.train_effective_end_exclusive,
                "train_prefix_invalid_count": prefix_invalid_count,
                "train_valid_count": train.count,
                "oos_raw_candidate_count": window.oos_raw_end_exclusive - window.oos_raw_start,
                "oos_purged_candidate_count": series.horizon_bars,
                "oos_valid_count": oos.count,
                "train_mean_rank_ic": train.mean,
                "oos_mean_rank_ic": oos.mean,
                "mean_rank_ic_difference": _normalized_float(oos.mean - train.mean),
                "train_median_rank_ic": train.median,
                "oos_median_rank_ic": oos.median,
                "median_rank_ic_difference": _normalized_float(oos.median - train.median),
                "train_sample_std_rank_ic": train.sample_std,
                "oos_sample_std_rank_ic": oos.sample_std,
                "sample_std_rank_ic_difference": _normalized_float(oos.sample_std - train.sample_std),
                "train_positive_ratio": train.positive_ratio,
                "oos_positive_ratio": oos.positive_ratio,
                "positive_ratio_difference": _normalized_float(
                    oos.positive_ratio - train.positive_ratio
                ),
            }
        )
    pooled = distribution_metrics(pooled_oos)
    fold_distribution = distribution_metrics(fold_means)
    sign_switches = sum(
        1
        for left, right in zip(fold_means, fold_means[1:])
        if (left < 0 < right) or (right < 0 < left)
    )
    return fold_rows, {
        "factor_name": series.factor_name,
        "horizon_bars": series.horizon_bars,
        "fold_count": len(windows),
        "pooled_oos_valid_count": pooled.count,
        "pooled_oos_mean_rank_ic": pooled.mean,
        "pooled_oos_median_rank_ic": pooled.median,
        "pooled_oos_sample_std_rank_ic": pooled.sample_std,
        "pooled_oos_positive_ratio": pooled.positive_ratio,
        "fold_mean_mean_rank_ic": fold_distribution.mean,
        "fold_mean_median_rank_ic": fold_distribution.median,
        "fold_mean_sample_std_rank_ic": fold_distribution.sample_std,
        "positive_fold_ratio": _normalized_float(
            math.fsum(1.0 for value in fold_means if value > 0) / len(fold_means)
        ),
        "best_fold_mean_rank_ic": max(fold_means),
        "worst_fold_mean_rank_ic": min(fold_means),
        "adjacent_strict_sign_switch_count": sign_switches,
    }


def _validate_train_statuses(statuses: tuple[str, ...]) -> int:
    first_valid = next((index for index, status in enumerate(statuses) if status == "valid"), None)
    if first_valid is None:
        raise MarketDataError("cross_sectional_oos_stability_train_has_no_valid_timestamp")
    if any(status == "valid" for status in statuses[:first_valid]):
        raise AssertionError("first valid status calculation is inconsistent")
    if any(status != "valid" for status in statuses[first_valid:]):
        raise MarketDataError("cross_sectional_oos_stability_internal_train_invalid_timestamp")
    return first_valid


def _policies() -> dict[str, Any]:
    return {
        "analysis_scope": "all_source_factor_horizon_pairs_no_selection",
        "candidate_timeline": "all_common_candidate_timestamps_no_compression_no_fill",
        "initial_history": "floor(candidate_timestamp_count/2)",
        "oos_fold_count": FOLD_COUNT,
        "oos_fold_partition": "contiguous_non_overlapping_remainder_to_earliest_folds",
        "train_window": "expanding_from_panel_start",
        "train_purge": "last_horizon_bars_candidate_timestamps",
        "oos_purge": "last_horizon_bars_candidate_timestamps_per_fold",
        "train_invalid_policy": "one_contiguous_structural_prefix_only",
        "oos_invalid_policy": "none_after_per_fold_horizon_purge",
        "minimum_train_valid_count": MINIMUM_TRAIN_VALID_COUNT,
        "minimum_oos_valid_count": MINIMUM_OOS_VALID_COUNT,
        "metrics": "mean_median_sample_std_positive_ratio",
        "metric_difference": "oos_minus_train",
        "fold_sign": "strictly_positive_or_strictly_negative_zero_is_not_a_switch",
        "statistical_inference": "none",
        "randomness": "none",
    }


def _oos_identity(
    evidence: ValidatedCrossSectionalICEvidence,
    *,
    folds_sha256: str,
    summaries_sha256: str,
) -> dict[str, Any]:
    source = evidence.source_report
    panel = source["panel"]
    identity = {
        "schema_version": OOS_STABILITY_SCHEMA_VERSION,
        "source_research_sha256": source["research_sha256"],
        "source_report_sha256": evidence.source_report_sha256,
        "source_ic_sha256": source["artifacts"]["ic_time_series"]["sha256"],
        "source_observations_sha256": source["artifacts"]["valid_observations"]["sha256"],
        "panel": {
            "panel_id": panel["panel_id"],
            "panel_sha256": panel["panel_sha256"],
            "components": [
                {
                    "dataset_id": item["dataset_id"],
                    "symbol": item["symbol"],
                    "raw_sha256": item["raw_sha256"],
                    "canonical_sha256": item["canonical_sha256"],
                }
                for item in panel["datasets"]
            ],
        },
        "factor_specs": source["factor_specs"],
        "horizons": source["horizons"],
        "candidate_timestamp_count": len(evidence.candidate_timestamps),
        "policies": _policies(),
        "artifacts": {
            "folds_sha256": folds_sha256,
            "summaries_sha256": summaries_sha256,
        },
    }
    promotion = source.get("promotion")
    if promotion is not None:
        identity["promotion"] = promotion
    return identity


def _build_report(
    evidence: ValidatedCrossSectionalICEvidence,
    *,
    fold_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    folds_filename: str,
    folds_sha256: str,
    summaries_filename: str,
    summaries_sha256: str,
    analysis_sha256: str,
) -> dict[str, Any]:
    source = evidence.source_report
    panel = source["panel"]
    report = {
        "schema_version": OOS_STABILITY_SCHEMA_VERSION,
        "analysis_sha256": analysis_sha256,
        "research_status": (
            PROMOTED_OOS_STABILITY_STATUS
            if source.get("promotion") is not None
            else OOS_STABILITY_STATUS
        ),
        "source": {
            "research_sha256": source["research_sha256"],
            "report_filename": evidence.source_report_path.name,
            "report_sha256": evidence.source_report_sha256,
            "ic_filename": evidence.ic_path.name,
            "ic_sha256": source["artifacts"]["ic_time_series"]["sha256"],
            "observations_filename": evidence.observations_path.name,
            "observations_sha256": source["artifacts"]["valid_observations"]["sha256"],
        },
        "panel": {
            "panel_id": panel["panel_id"],
            "panel_sha256": panel["panel_sha256"],
            "timeframe": panel["timeframe"],
            "timestamp_semantics": panel["timestamp_semantics"],
            "datasets": panel["datasets"],
        },
        "factor_specs": source["factor_specs"],
        "horizons": source["horizons"],
        "candidate_timestamp_count": len(evidence.candidate_timestamps),
        "policies": _policies(),
        "fold_row_count": len(fold_rows),
        "summary_row_count": len(summary_rows),
        "folds": fold_rows,
        "summaries": summary_rows,
        "artifacts": {
            "folds": {
                "filename": folds_filename,
                "sha256": folds_sha256,
                "row_count": len(fold_rows),
            },
            "summaries": {
                "filename": summaries_filename,
                "sha256": summaries_sha256,
                "row_count": len(summary_rows),
            },
        },
        "warnings": (
            [
                "promotion_aware_stability_evidence_only",
                "timestamp_semantics_not_uniformly_verified",
                "six_asset_convenience_cross_section_has_limited_statistical_power",
                "historical_point_in_time_membership_not_proven",
                "survivorship_bias_not_resolved",
                "prior_related_results_exist",
                "fold_metrics_are_descriptive_without_null_calibration",
                "per_fold_horizon_purge_discards_boundary_timestamps",
                "statistical_evidence_is_not_portfolio_pnl",
                "no_profitability_or_execution_claim",
                "no_automatic_readiness_upgrade",
            ]
            if source.get("promotion") is not None
            else [
                "post_hoc_stability_evidence_only",
                "timestamp_semantics_not_uniformly_verified",
                "three_asset_cross_section_has_limited_statistical_power",
                "fold_metrics_are_descriptive_without_null_calibration",
                "per_fold_horizon_purge_discards_boundary_timestamps",
                "statistical_evidence_is_not_portfolio_pnl",
                "no_profitability_or_execution_claim",
                "no_automatic_readiness_upgrade",
            ]
        ),
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    promotion = source.get("promotion")
    if promotion is not None:
        report["promotion"] = promotion
    return report


def _serialize_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _format_csv_value(row[field]) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _commit_bytes(path: Path, payload: bytes, expected_sha256: str) -> None:
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise MarketDataError(f"content_addressed_artifact_collision:{path.name}")
        return
    temporary = path.parent / f".{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _commit_report(path: Path, report: dict[str, Any]) -> None:
    expected = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if path.exists():
        if path.read_bytes() != expected:
            raise MarketDataError(f"content_addressed_artifact_collision:{path.name}")
        return
    write_json_atomically(path, report)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _normalized_float(value: float) -> float:
    return float(format(value, ".15g"))


def _format_csv_value(value: Any) -> Any:
    if isinstance(value, float):
        return format(value, ".15g")
    return value
