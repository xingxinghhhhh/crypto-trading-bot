from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from crypto_bot.cross_sectional_factor_research import CROSS_SECTIONAL_RESEARCH_SCHEMA_VERSION
from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_registry import write_json_atomically


CALIBRATION_SCHEMA_VERSION = 1
_SOURCE_RESEARCH_STATUS = "cross_sectional_rank_ic_evidence_only"
_CALIBRATION_STATUS = "dependence_aware_mean_rank_ic_calibration_only"
_MEAN_TOLERANCE = 5e-12
_MINIMUM_SAMPLE_COUNT = 500
_MINIMUM_SAMPLE_MULTIPLIER = 10
_NORMAL_95_CRITICAL_VALUE = 1.959963984540054
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
_HYPOTHESIS_FIELDS = (
    "factor_name",
    "horizon_bars",
    "candidate_timestamp_count",
    "valid_ic_timestamp_count",
    "first_valid_timestamp",
    "last_valid_timestamp",
    "mean_rank_ic",
    "bandwidth_auto",
    "bandwidth_overlap_floor",
    "bandwidth_used",
    "long_run_variance",
    "hac_standard_error",
    "z_statistic",
    "p_value_raw_two_sided",
    "p_value_holm",
    "ci95_lower",
    "ci95_upper",
    "naive_standard_error",
    "hac_to_naive_se_ratio",
)
_SOURCE_STATUSES = (
    "valid",
    "insufficient_assets",
    "constant_both",
    "constant_factor",
    "constant_forward_return",
    "undefined_correlation",
)


@dataclass(frozen=True)
class HacMeanTestResult:
    sample_count: int
    mean: float
    bandwidth_auto: int
    bandwidth_overlap_floor: int
    bandwidth_used: int
    long_run_variance: float
    hac_standard_error: float
    z_statistic: float
    p_value_raw_two_sided: float
    ci95_lower: float
    ci95_upper: float
    naive_standard_error: float
    hac_to_naive_se_ratio: float


@dataclass(frozen=True)
class CrossSectionalICCalibrationResult:
    report: dict[str, Any]
    export_paths: dict[str, str]


@dataclass(frozen=True)
class CrossSectionalICSeries:
    factor_name: str
    horizon_bars: int
    candidate_timestamps: tuple[str, ...]
    statuses: tuple[str, ...]
    candidate_values: tuple[float | None, ...]
    timestamps: tuple[str, ...]
    values: tuple[float, ...]
    status_counts: dict[str, int]


@dataclass(frozen=True)
class ValidatedCrossSectionalICEvidence:
    source_report: dict[str, Any]
    source_report_sha256: str
    source_report_path: Path
    ic_path: Path
    observations_path: Path
    candidate_timestamps: tuple[str, ...]
    series_by_key: dict[tuple[str, int], CrossSectionalICSeries]


@dataclass
class _ICSeries:
    factor_name: str
    horizon_bars: int
    timestamps: list[str]
    values: list[float]
    status_counts: dict[str, int]
    candidate_timestamps: list[str]
    statuses: list[str]
    candidate_values: list[float | None]
    candidate_count: int = 0
    seen_valid: bool = False
    ended_valid_region: bool = False
    last_timestamp: datetime | None = None


def newey_west_mean_test(values: list[float] | tuple[float, ...], *, horizon_bars: int) -> HacMeanTestResult:
    if not isinstance(horizon_bars, int) or isinstance(horizon_bars, bool) or horizon_bars <= 0:
        raise ValueError("horizon_bars must be a positive integer")
    observations = [float(value) for value in values]
    if any(not math.isfinite(value) for value in observations):
        raise ValueError("HAC observations must all be finite")
    n = len(observations)
    bandwidth_auto = math.floor(4 * (n / 100) ** (2 / 9)) if n else 0
    bandwidth_overlap_floor = horizon_bars - 1
    bandwidth = max(bandwidth_auto, bandwidth_overlap_floor)
    minimum_required = max(_MINIMUM_SAMPLE_COUNT, _MINIMUM_SAMPLE_MULTIPLIER * (bandwidth + 1))
    if bandwidth >= n or n < minimum_required:
        raise MarketDataError(
            "cross_sectional_ic_calibration_insufficient_sample:"
            f"n={n}:bandwidth={bandwidth}:minimum={minimum_required}"
        )

    mean = math.fsum(observations) / n
    centered = [value - mean for value in observations]
    gamma_zero = math.fsum(value * value for value in centered) / n
    long_run_variance = gamma_zero
    for lag in range(1, bandwidth + 1):
        autocovariance = math.fsum(
            centered[index] * centered[index - lag] for index in range(lag, n)
        ) / n
        weight = 1.0 - lag / (bandwidth + 1)
        long_run_variance += 2.0 * weight * autocovariance
    if not math.isfinite(long_run_variance) or long_run_variance <= 0:
        raise MarketDataError("cross_sectional_ic_calibration_non_positive_lrv")
    hac_standard_error = math.sqrt(long_run_variance / n)
    if not math.isfinite(hac_standard_error) or hac_standard_error <= 0:
        raise MarketDataError("cross_sectional_ic_calibration_invalid_hac_standard_error")
    z_statistic = mean / hac_standard_error
    p_value = math.erfc(abs(z_statistic) / math.sqrt(2.0))
    sample_variance = math.fsum(value * value for value in centered) / (n - 1)
    naive_standard_error = math.sqrt(sample_variance / n)
    hac_ratio = hac_standard_error / naive_standard_error if naive_standard_error > 0 else math.inf
    lower = mean - _NORMAL_95_CRITICAL_VALUE * hac_standard_error
    upper = mean + _NORMAL_95_CRITICAL_VALUE * hac_standard_error
    outputs = (
        mean,
        long_run_variance,
        hac_standard_error,
        z_statistic,
        p_value,
        lower,
        upper,
        naive_standard_error,
        hac_ratio,
    )
    if any(not math.isfinite(value) for value in outputs):
        raise MarketDataError("cross_sectional_ic_calibration_non_finite_statistic")
    if not 0 <= p_value <= 1:
        raise MarketDataError("cross_sectional_ic_calibration_invalid_p_value")
    return HacMeanTestResult(
        sample_count=n,
        mean=_normalized_float(mean),
        bandwidth_auto=bandwidth_auto,
        bandwidth_overlap_floor=bandwidth_overlap_floor,
        bandwidth_used=bandwidth,
        long_run_variance=_normalized_float(long_run_variance),
        hac_standard_error=_normalized_float(hac_standard_error),
        z_statistic=_normalized_float(z_statistic),
        p_value_raw_two_sided=_normalized_float(p_value),
        ci95_lower=_normalized_float(lower),
        ci95_upper=_normalized_float(upper),
        naive_standard_error=_normalized_float(naive_standard_error),
        hac_to_naive_se_ratio=_normalized_float(hac_ratio),
    )


def holm_adjust(
    hypotheses: list[tuple[tuple[str, int], float]],
) -> dict[tuple[str, int], float]:
    if not hypotheses:
        raise ValueError("Holm adjustment requires at least one hypothesis")
    keys = [key for key, _ in hypotheses]
    if len(keys) != len(set(keys)):
        raise ValueError("Holm hypothesis keys must be unique")
    if any(not math.isfinite(value) or not 0 <= value <= 1 for _, value in hypotheses):
        raise ValueError("Holm p-values must be finite values in [0, 1]")
    ordered = sorted(hypotheses, key=lambda item: (item[1], item[0][0], item[0][1]))
    adjusted: dict[tuple[str, int], float] = {}
    running = 0.0
    total = len(ordered)
    for index, (key, value) in enumerate(ordered):
        running = max(running, min(1.0, (total - index) * value))
        adjusted[key] = _normalized_float(running)
    return adjusted


def run_cross_sectional_ic_calibration(
    source_report_path: str | Path,
    output_dir: str | Path,
) -> CrossSectionalICCalibrationResult:
    source_path = Path(source_report_path)
    evidence = load_validated_cross_sectional_ic_evidence(source_path)
    source = evidence.source_report
    source_hash = evidence.source_report_sha256
    ic_path = evidence.ic_path
    observations_path = evidence.observations_path
    series_by_key = evidence.series_by_key
    hypothesis_rows: list[dict[str, Any]] = []
    raw_p_values: list[tuple[tuple[str, int], float]] = []
    for key in _expected_hypothesis_keys(source):
        series = series_by_key[key]
        hac = newey_west_mean_test(series.values, horizon_bars=key[1])
        row = {
            "factor_name": key[0],
            "horizon_bars": key[1],
            "candidate_timestamp_count": len(series.candidate_timestamps),
            "valid_ic_timestamp_count": len(series.values),
            "first_valid_timestamp": series.timestamps[0],
            "last_valid_timestamp": series.timestamps[-1],
            "mean_rank_ic": hac.mean,
            "bandwidth_auto": hac.bandwidth_auto,
            "bandwidth_overlap_floor": hac.bandwidth_overlap_floor,
            "bandwidth_used": hac.bandwidth_used,
            "long_run_variance": hac.long_run_variance,
            "hac_standard_error": hac.hac_standard_error,
            "z_statistic": hac.z_statistic,
            "p_value_raw_two_sided": hac.p_value_raw_two_sided,
            "p_value_holm": None,
            "ci95_lower": hac.ci95_lower,
            "ci95_upper": hac.ci95_upper,
            "naive_standard_error": hac.naive_standard_error,
            "hac_to_naive_se_ratio": hac.hac_to_naive_se_ratio,
        }
        hypothesis_rows.append(row)
        raw_p_values.append((key, hac.p_value_raw_two_sided))
    adjusted = holm_adjust(raw_p_values)
    for row in hypothesis_rows:
        key = (
            str(row["factor_name"]),
            _require_positive_int(row["horizon_bars"], "hypothesis_horizon"),
        )
        row["p_value_holm"] = adjusted[key]

    csv_bytes = _serialize_hypotheses(hypothesis_rows)
    hypotheses_sha256 = hashlib.sha256(csv_bytes).hexdigest()
    source_ic = source["artifacts"]["ic_time_series"]
    source_observations = source["artifacts"]["valid_observations"]
    identity = _calibration_identity(
        source,
        source_report_sha256=source_hash,
        source_ic_sha256=source_ic["sha256"],
        source_observations_sha256=source_observations["sha256"],
        hypotheses_sha256=hypotheses_sha256,
    )
    calibration_sha256 = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    prefix = (
        "promoted-cross-sectional-ic-calibration"
        if source.get("promotion") is not None
        else "cross-sectional-ic-calibration"
    )
    stem = f"{prefix}.{calibration_sha256}"
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    hypotheses_path = destination / f"{stem}.hypotheses.csv"
    report_path = destination / f"{stem}.json"
    report = _build_calibration_report(
        source,
        source_report_sha256=source_hash,
        source_report_filename=source_path.name,
        source_ic_filename=ic_path.name,
        source_observations_filename=observations_path.name,
        hypotheses=hypothesis_rows,
        hypotheses_filename=hypotheses_path.name,
        hypotheses_sha256=hypotheses_sha256,
        calibration_sha256=calibration_sha256,
    )
    _commit_bytes(hypotheses_path, csv_bytes, hypotheses_sha256)
    _commit_report(report_path, report)
    return CrossSectionalICCalibrationResult(
        report=report,
        export_paths={"report": str(report_path), "hypotheses": str(hypotheses_path)},
    )


def load_validated_cross_sectional_ic_evidence(
    source_report_path: str | Path,
) -> ValidatedCrossSectionalICEvidence:
    source_path = Path(source_report_path)
    source, source_hash, ic_path, observations_path = _load_and_validate_source_report(source_path)
    series_by_key = _load_and_validate_ic_series(source, ic_path)
    first = next(iter(series_by_key.values()))
    candidate_timestamps = first.candidate_timestamps
    if any(series.candidate_timestamps != candidate_timestamps for series in series_by_key.values()):
        raise MarketDataError("cross_sectional_ic_evidence_candidate_timestamps_mismatch")
    return ValidatedCrossSectionalICEvidence(
        source_report=source,
        source_report_sha256=source_hash,
        source_report_path=source_path.resolve(),
        ic_path=ic_path,
        observations_path=observations_path,
        candidate_timestamps=candidate_timestamps,
        series_by_key=series_by_key,
    )


def format_cross_sectional_ic_calibration(result: CrossSectionalICCalibrationResult) -> str:
    report = result.report
    return "\n".join(
        [
            f"statistical_status: {report['statistical_status']}",
            f"calibration_sha256: {report['calibration_sha256']}",
            f"source_research_sha256: {report['source']['research_sha256']}",
            f"panel_id: {report['panel']['panel_id']}",
            f"hypothesis_count: {report['hypothesis_count']}",
            "readiness_changed: false",
            "automatic_factor_approval: false",
        ]
    )


def _load_and_validate_source_report(
    source_path: Path,
) -> tuple[dict[str, Any], str, Path, Path]:
    if not source_path.is_file():
        raise FileNotFoundError(f"cross-sectional research report not found: {source_path}")
    source_bytes = source_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    try:
        source = json.loads(source_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError("cross_sectional_ic_calibration_invalid_source_json") from exc
    if not isinstance(source, dict):
        raise MarketDataError("cross_sectional_ic_calibration_source_must_be_mapping")
    if source.get("schema_version") != CROSS_SECTIONAL_RESEARCH_SCHEMA_VERSION:
        raise MarketDataError("cross_sectional_ic_calibration_unsupported_source_schema")
    if source.get("research_status") != _SOURCE_RESEARCH_STATUS:
        raise MarketDataError("cross_sectional_ic_calibration_invalid_source_status")
    if source.get("readiness_changed") is not False or source.get("automatic_factor_approval") is not False:
        raise MarketDataError("cross_sectional_ic_calibration_unsafe_source_readiness")
    research_sha256 = _require_sha256(source.get("research_sha256"), "source_research_sha256")
    source_prefix = (
        "promoted-cross-sectional-research"
        if source.get("promotion") is not None
        else "cross-sectional-factor-research"
    )
    expected_source_name = f"{source_prefix}.{research_sha256}.json"
    if source_path.name != expected_source_name:
        raise MarketDataError("cross_sectional_ic_calibration_source_filename_mismatch")
    panel = _require_mapping(source.get("panel"), "source_panel")
    _require_sha256(panel.get("panel_sha256"), "source_panel_sha256")
    timestamp_semantics = _require_mapping(panel.get("timestamp_semantics"), "timestamp_semantics")
    if timestamp_semantics.get("status") != "unverified":
        raise MarketDataError("cross_sectional_ic_calibration_unexpected_timestamp_semantics")
    artifacts = _require_mapping(source.get("artifacts"), "source_artifacts")
    ic_artifact = _require_mapping(artifacts.get("ic_time_series"), "source_ic_artifact")
    observations_artifact = _require_mapping(
        artifacts.get("valid_observations"), "source_observations_artifact"
    )
    ic_path = _validated_artifact_path(source_path, ic_artifact, f"{expected_source_name[:-5]}.ic.csv")
    observations_path = _validated_artifact_path(
        source_path,
        observations_artifact,
        f"{expected_source_name[:-5]}.observations.csv",
    )
    ic_sha256, ic_rows = _hash_and_csv_row_count(ic_path)
    observations_sha256, observations_rows = _hash_and_csv_row_count(observations_path)
    if ic_sha256 != _require_sha256(ic_artifact.get("sha256"), "source_ic_sha256"):
        raise MarketDataError("cross_sectional_ic_calibration_source_ic_hash_mismatch")
    if observations_sha256 != _require_sha256(
        observations_artifact.get("sha256"), "source_observations_sha256"
    ):
        raise MarketDataError("cross_sectional_ic_calibration_source_observations_hash_mismatch")
    if ic_rows != _require_non_negative_int(ic_artifact.get("row_count"), "source_ic_row_count"):
        raise MarketDataError("cross_sectional_ic_calibration_source_ic_row_count_mismatch")
    if observations_rows != _require_non_negative_int(
        observations_artifact.get("row_count"), "source_observations_row_count"
    ):
        raise MarketDataError("cross_sectional_ic_calibration_source_observations_row_count_mismatch")
    expected_identity = _source_research_identity(source)
    recomputed = hashlib.sha256(_canonical_json_bytes(expected_identity)).hexdigest()
    if recomputed != research_sha256:
        raise MarketDataError("cross_sectional_ic_calibration_source_research_hash_mismatch")
    return source, source_hash, ic_path, observations_path


def _source_research_identity(source: dict[str, Any]) -> dict[str, Any]:
    panel = _require_mapping(source.get("panel"), "source_panel")
    datasets = _require_list(panel.get("datasets"), "source_datasets")
    components = []
    for item in datasets:
        dataset = _require_mapping(item, "source_dataset")
        components.append(
            {
                "dataset_id": dataset.get("dataset_id"),
                "symbol": dataset.get("symbol"),
                "raw_sha256": dataset.get("raw_sha256"),
                "canonical_sha256": dataset.get("canonical_sha256"),
            }
        )
    artifacts = _require_mapping(source.get("artifacts"), "source_artifacts")
    ic_artifact = _require_mapping(artifacts.get("ic_time_series"), "source_ic_artifact")
    observations_artifact = _require_mapping(
        artifacts.get("valid_observations"), "source_observations_artifact"
    )
    timestamp_semantics = _require_mapping(panel.get("timestamp_semantics"), "timestamp_semantics")
    identity = {
        "schema_version": source["schema_version"],
        "panel_id": panel.get("panel_id"),
        "panel_sha256": panel.get("panel_sha256"),
        "alignment": panel.get("alignment"),
        "timestamp_semantics_status": timestamp_semantics.get("status"),
        "components": components,
        "factor_specs": source.get("factor_specs"),
        "horizons": source.get("horizons"),
        "required_asset_count": source.get("required_asset_count"),
        "policies": source.get("policies"),
        "artifacts": {
            "ic_time_series_sha256": ic_artifact.get("sha256"),
            "valid_observations_sha256": observations_artifact.get("sha256"),
        },
    }
    promotion = source.get("promotion")
    if promotion is not None:
        identity["promotion"] = _require_mapping(promotion, "source_promotion")
    return identity


def _load_and_validate_ic_series(
    source: dict[str, Any],
    ic_path: Path,
) -> dict[tuple[str, int], CrossSectionalICSeries]:
    keys = _expected_hypothesis_keys(source)
    summaries = _source_summaries(source, keys)
    series_by_key = {
        key: _ICSeries(
            key[0],
            key[1],
            [],
            [],
            {status: 0 for status in _SOURCE_STATUSES},
            [],
            [],
            [],
        )
        for key in keys
    }
    expected_index = {key: index for index, key in enumerate(keys)}
    current_index = 0
    required_asset_count = _require_positive_int(source.get("required_asset_count"), "required_asset_count")
    with ic_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _IC_FIELDS:
            raise MarketDataError("cross_sectional_ic_calibration_source_ic_header_mismatch")
        for row in reader:
            factor_name = row["factor_name"]
            horizon = _parse_positive_int(row["horizon_bars"], "horizon_bars")
            key = (factor_name, horizon)
            if key not in series_by_key:
                raise MarketDataError("cross_sectional_ic_calibration_unexpected_hypothesis")
            row_index = expected_index[key]
            if row_index < current_index or row_index > current_index + 1:
                raise MarketDataError("cross_sectional_ic_calibration_source_ic_order_mismatch")
            current_index = row_index
            series = series_by_key[key]
            timestamp = _parse_utc_timestamp(row["timestamp"])
            if series.last_timestamp is not None and timestamp <= series.last_timestamp:
                raise MarketDataError("cross_sectional_ic_calibration_duplicate_or_reversed_timestamp")
            series.last_timestamp = timestamp
            series.candidate_count += 1
            status = row["status"]
            if status not in series.status_counts:
                raise MarketDataError("cross_sectional_ic_calibration_unknown_source_status")
            series.status_counts[status] += 1
            series.candidate_timestamps.append(row["timestamp"])
            series.statuses.append(status)
            if _parse_positive_int(row["required_asset_count"], "row_required_asset_count") != required_asset_count:
                raise MarketDataError("cross_sectional_ic_calibration_required_asset_count_mismatch")
            if status == "valid":
                if series.ended_valid_region:
                    raise MarketDataError("cross_sectional_ic_calibration_internal_invalid_timestamp")
                value = _parse_finite_float(row["rank_ic"], "rank_ic")
                if not -1 <= value <= 1:
                    raise MarketDataError("cross_sectional_ic_calibration_rank_ic_out_of_range")
                if _parse_positive_int(row["n_valid_assets"], "n_valid_assets") != required_asset_count:
                    raise MarketDataError("cross_sectional_ic_calibration_valid_asset_count_mismatch")
                if _parse_positive_int(row["factor_unique_count"], "factor_unique_count") < 2:
                    raise MarketDataError("cross_sectional_ic_calibration_invalid_factor_unique_count")
                if _parse_positive_int(
                    row["forward_return_unique_count"], "forward_return_unique_count"
                ) < 2:
                    raise MarketDataError("cross_sectional_ic_calibration_invalid_return_unique_count")
                series.seen_valid = True
                series.timestamps.append(row["timestamp"])
                series.values.append(value)
                series.candidate_values.append(value)
            else:
                if row["rank_ic"] != "":
                    raise MarketDataError("cross_sectional_ic_calibration_invalid_row_has_rank_ic")
                if series.seen_valid:
                    series.ended_valid_region = True
                series.candidate_values.append(None)
    if current_index != len(keys) - 1:
        raise MarketDataError("cross_sectional_ic_calibration_missing_hypothesis_rows")
    total_rows = 0
    total_valid = 0
    for key in keys:
        series = series_by_key[key]
        summary = summaries[key]
        if not series.values:
            raise MarketDataError("cross_sectional_ic_calibration_empty_valid_series")
        if series.candidate_count != _require_non_negative_int(
            summary.get("candidate_timestamp_count"), "candidate_timestamp_count"
        ):
            raise MarketDataError("cross_sectional_ic_calibration_candidate_count_mismatch")
        if len(series.values) != _require_non_negative_int(
            summary.get("valid_ic_timestamp_count"), "valid_ic_timestamp_count"
        ):
            raise MarketDataError("cross_sectional_ic_calibration_valid_count_mismatch")
        for status in _SOURCE_STATUSES[1:]:
            summary_key = f"{status}_count"
            if series.status_counts[status] != _require_non_negative_int(summary.get(summary_key), summary_key):
                raise MarketDataError("cross_sectional_ic_calibration_status_count_mismatch")
        source_mean = _parse_finite_number(summary.get("mean_rank_ic"), "source_mean_rank_ic")
        recomputed_mean = math.fsum(series.values) / len(series.values)
        if not math.isclose(source_mean, recomputed_mean, rel_tol=0.0, abs_tol=_MEAN_TOLERANCE):
            raise MarketDataError("cross_sectional_ic_calibration_source_mean_mismatch")
        total_rows += series.candidate_count
        total_valid += len(series.values)
    artifacts = _require_mapping(source.get("artifacts"), "source_artifacts")
    ic_artifact = _require_mapping(artifacts.get("ic_time_series"), "source_ic_artifact")
    observations_artifact = _require_mapping(
        artifacts.get("valid_observations"), "source_observations_artifact"
    )
    if total_rows != _require_non_negative_int(ic_artifact.get("row_count"), "source_ic_row_count"):
        raise MarketDataError("cross_sectional_ic_calibration_total_ic_count_mismatch")
    expected_observations = total_valid * required_asset_count
    if expected_observations != _require_non_negative_int(
        observations_artifact.get("row_count"), "source_observations_row_count"
    ):
        raise MarketDataError("cross_sectional_ic_calibration_observation_count_invariant_failed")
    return {
        key: CrossSectionalICSeries(
            factor_name=series.factor_name,
            horizon_bars=series.horizon_bars,
            candidate_timestamps=tuple(series.candidate_timestamps),
            statuses=tuple(series.statuses),
            candidate_values=tuple(series.candidate_values),
            timestamps=tuple(series.timestamps),
            values=tuple(series.values),
            status_counts=dict(series.status_counts),
        )
        for key, series in series_by_key.items()
    }


def _expected_hypothesis_keys(source: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    raw_specs = _require_list(source.get("factor_specs"), "factor_specs")
    names: list[str] = []
    for item in raw_specs:
        spec = _require_mapping(item, "factor_spec")
        name = spec.get("name")
        if not isinstance(name, str) or not name:
            raise MarketDataError("cross_sectional_ic_calibration_invalid_factor_name")
        names.append(name)
    if not names or names != sorted(names) or len(names) != len(set(names)):
        raise MarketDataError("cross_sectional_ic_calibration_noncanonical_factor_specs")
    raw_horizons = _require_list(source.get("horizons"), "horizons")
    horizons = [_require_positive_int(item, "horizon") for item in raw_horizons]
    if not horizons or horizons != sorted(set(horizons)):
        raise MarketDataError("cross_sectional_ic_calibration_noncanonical_horizons")
    return tuple((name, horizon) for name in names for horizon in horizons)


def _source_summaries(
    source: dict[str, Any], keys: tuple[tuple[str, int], ...]
) -> dict[tuple[str, int], dict[str, Any]]:
    raw_summaries = _require_list(source.get("summaries"), "summaries")
    summaries: dict[tuple[str, int], dict[str, Any]] = {}
    for item in raw_summaries:
        summary = _require_mapping(item, "summary")
        factor_name = summary.get("factor_name")
        horizon = _require_positive_int(summary.get("horizon_bars"), "summary_horizon")
        if not isinstance(factor_name, str):
            raise MarketDataError("cross_sectional_ic_calibration_invalid_summary_factor")
        key = (factor_name, horizon)
        if key in summaries:
            raise MarketDataError("cross_sectional_ic_calibration_duplicate_summary")
        summaries[key] = summary
    if tuple(summaries) != keys:
        raise MarketDataError("cross_sectional_ic_calibration_summary_order_or_shape_mismatch")
    return summaries


def _calibration_identity(
    source: dict[str, Any],
    *,
    source_report_sha256: str,
    source_ic_sha256: str,
    source_observations_sha256: str,
    hypotheses_sha256: str,
) -> dict[str, Any]:
    panel = _require_mapping(source.get("panel"), "source_panel")
    timestamp_semantics = _require_mapping(panel.get("timestamp_semantics"), "timestamp_semantics")
    identity = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "source_research_sha256": source["research_sha256"],
        "source_report_sha256": source_report_sha256,
        "source_ic_sha256": source_ic_sha256,
        "source_observations_sha256": source_observations_sha256,
        "panel_sha256": panel["panel_sha256"],
        "timestamp_semantics_status": timestamp_semantics["status"],
        "policies": _calibration_policies(),
        "hypotheses_sha256": hypotheses_sha256,
    }
    promotion = source.get("promotion")
    if promotion is not None:
        identity["promotion"] = _require_mapping(promotion, "source_promotion")
    return identity


def _calibration_policies() -> dict[str, Any]:
    return {
        "null_hypothesis": "mean_rank_ic_equals_zero",
        "alternative": "two_sided",
        "hac": "newey_west_bartlett_v1",
        "autocovariance_denominator": "n",
        "bandwidth_auto": "floor(4*(n/100)^(2/9))",
        "bandwidth_overlap_floor": "horizon_bars_minus_one",
        "bandwidth_used": "max(auto,overlap_floor)",
        "minimum_sample": "n>=max(500,10*(bandwidth+1))",
        "confidence_interval": "asymptotic_normal_95_percent",
        "multiple_testing": "holm_step_down_v1",
        "multiple_testing_scope": "all_factor_horizon_pairs_within_source_research",
        "missing_values": "prefix_and_suffix_only_no_fill_no_compression",
        "randomness": "none",
        "source_mean_absolute_tolerance": _MEAN_TOLERANCE,
    }


def _build_calibration_report(
    source: dict[str, Any],
    *,
    source_report_sha256: str,
    source_report_filename: str,
    source_ic_filename: str,
    source_observations_filename: str,
    hypotheses: list[dict[str, Any]],
    hypotheses_filename: str,
    hypotheses_sha256: str,
    calibration_sha256: str,
) -> dict[str, Any]:
    panel = _require_mapping(source.get("panel"), "source_panel")
    report = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "calibration_sha256": calibration_sha256,
        "statistical_status": _CALIBRATION_STATUS,
        "source": {
            "research_sha256": source["research_sha256"],
            "report_filename": source_report_filename,
            "report_sha256": source_report_sha256,
            "ic_filename": source_ic_filename,
            "ic_sha256": source["artifacts"]["ic_time_series"]["sha256"],
            "observations_filename": source_observations_filename,
            "observations_sha256": source["artifacts"]["valid_observations"]["sha256"],
        },
        "panel": {
            "panel_id": panel["panel_id"],
            "panel_sha256": panel["panel_sha256"],
            "timeframe": panel["timeframe"],
            "timestamp_semantics": panel["timestamp_semantics"],
            "datasets": [
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
        "policies": _calibration_policies(),
        "hypothesis_count": len(hypotheses),
        "hypotheses": hypotheses,
        "artifacts": {
            "hypotheses": {
                "filename": hypotheses_filename,
                "sha256": hypotheses_sha256,
                "row_count": len(hypotheses),
            }
        },
        "warnings": (
            [
                "timestamp_semantics_not_uniformly_verified",
                "six_asset_convenience_cross_section_has_limited_statistical_power",
                "historical_point_in_time_membership_not_proven",
                "survivorship_bias_not_resolved",
                "rank_ic_time_series_is_serially_dependent",
                "hac_results_are_asymptotic",
                "multiple_testing_scope_excludes_other_panels_and_prior_experiments",
                "statistical_evidence_is_not_portfolio_pnl",
                "no_profitability_or_execution_claim",
                "no_automatic_readiness_upgrade",
            ]
            if source.get("promotion") is not None
            else [
                "timestamp_semantics_not_uniformly_verified",
                "three_asset_cross_section_has_limited_statistical_power",
                "rank_ic_time_series_is_serially_dependent",
                "hac_results_are_asymptotic",
                "multiple_testing_scope_excludes_other_panels_and_prior_experiments",
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
        report["promotion"] = _require_mapping(promotion, "source_promotion")
    return report


def _serialize_hypotheses(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=_HYPOTHESIS_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                field: _format_csv_value(row[field])
                for field in _HYPOTHESIS_FIELDS
            }
        )
    return buffer.getvalue().encode("utf-8")


def _validated_artifact_path(source_path: Path, artifact: dict[str, Any], expected_name: str) -> Path:
    filename = artifact.get("filename")
    if not isinstance(filename, str) or filename != expected_name:
        raise MarketDataError("cross_sectional_ic_calibration_source_artifact_filename_mismatch")
    candidate = Path(filename)
    if candidate.is_absolute() or candidate.name != filename or "/" in filename or "\\" in filename:
        raise MarketDataError("cross_sectional_ic_calibration_source_artifact_path_escape")
    parent = source_path.resolve().parent
    resolved = (parent / candidate).resolve()
    if resolved.parent != parent or not resolved.is_file():
        raise MarketDataError("cross_sectional_ic_calibration_source_artifact_missing_or_escaped")
    return resolved


def _hash_and_csv_row_count(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            line_count += chunk.count(b"\n")
    if line_count < 1:
        raise MarketDataError("cross_sectional_ic_calibration_empty_source_csv")
    return digest.hexdigest(), line_count - 1


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
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MarketDataError("cross_sectional_ic_calibration_invalid_timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MarketDataError("cross_sectional_ic_calibration_timestamp_not_utc")
    return parsed


def _parse_positive_int(value: object, name: str) -> int:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}") from exc
    if isinstance(value, bool) or parsed <= 0 or str(parsed) != str(value):
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    return parsed


def _require_positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    return value


def _require_non_negative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    return value


def _parse_finite_float(value: object, name: str) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}") from exc
    if not math.isfinite(parsed):
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    return parsed


def _parse_finite_number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    return parsed


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    return value


def _require_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    return value


def _require_list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise MarketDataError(f"cross_sectional_ic_calibration_invalid_{name}")
    return value


def _normalized_float(value: float) -> float:
    return float(format(value, ".15g"))


def _format_csv_value(value: Any) -> Any:
    if isinstance(value, float):
        return format(value, ".15g")
    return value
