from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from crypto_bot.config import AppConfig
from crypto_bot.errors import MarketDataError
from crypto_bot.factors import (
    DEFAULT_FACTOR_SPECS,
    FactorSpec,
    compute_factor_frame,
    compute_forward_returns,
)
from crypto_bot.market.archive_replay import build_replay_dataset


def run_factor_research(
    config: AppConfig,
    input_dir: str | Path,
    *,
    target_timeframe: str,
    output_dir: str | Path | None = None,
    horizons: tuple[int, ...] = (1, 4, 16),
    cost_bps: tuple[float, ...] = (0.0, 5.0, 10.0),
    quantiles: int = 5,
    rank_lookback: int = 40,
    rolling_train_bars: int = 60,
    rolling_test_bars: int = 20,
    rolling_step_bars: int = 20,
    min_observations: int = 20,
    specs: tuple[FactorSpec, ...] = DEFAULT_FACTOR_SPECS,
) -> dict[str, Any]:
    _validate_settings(
        horizons=horizons,
        cost_bps=cost_bps,
        quantiles=quantiles,
        rank_lookback=rank_lookback,
        rolling_train_bars=rolling_train_bars,
        rolling_test_bars=rolling_test_bars,
        rolling_step_bars=rolling_step_bars,
        min_observations=min_observations,
    )
    dataset = build_replay_dataset(
        input_dir,
        exchange=config.market_data.exchange,
        symbol=config.market_data.symbols[0],
        source_timeframe=config.market_data.timeframe,
        target_timeframe=target_timeframe,
    )
    factors = compute_factor_frame(dataset.bars, specs)
    forward_returns = compute_forward_returns(dataset.bars, horizons)
    research_frame = factors.merge(
        forward_returns,
        on="timestamp",
        how="inner",
        validate="one_to_one",
    )
    factor_names = [spec.name for spec in specs]
    metrics = _factor_metrics(
        research_frame,
        factor_names=factor_names,
        horizons=horizons,
        cost_bps=cost_bps,
        quantiles=quantiles,
        rank_lookback=rank_lookback,
        min_observations=min_observations,
    )
    decay = _summarize_decay(metrics, horizons)
    correlations = _factor_correlations(research_frame, factor_names, min_observations)
    rolling_windows = _rolling_stability_windows(
        research_frame,
        factor_names=factor_names,
        horizons=horizons,
        train_bars=rolling_train_bars,
        test_bars=rolling_test_bars,
        step_bars=rolling_step_bars,
        min_observations=min_observations,
    )
    stability = _summarize_stability(rolling_windows)
    warnings = _research_warnings(
        replay_bar_count=len(dataset.bars),
        rolling_window_count=len(
            {
                row["window_index"]
                for row in rolling_windows
            }
        ),
    )
    has_usable_metrics = any(
        row["pearson_ic"] is not None or row["rank_ic"] is not None
        for row in metrics
    )
    has_usable_rolling_evidence = any(
        row["test_ic"] is not None or row["test_rank_ic"] is not None
        for row in rolling_windows
    )
    evidence: dict[str, Any] = {
        "research_status": (
            "research_evidence_only"
            if has_usable_metrics and has_usable_rolling_evidence
            else "insufficient_data"
        ),
        "readiness_changed": False,
        "automatic_factor_approval": False,
        "dataset": dataset.report.to_dict(),
        "settings": {
            "horizons": list(horizons),
            "cost_bps": list(cost_bps),
            "quantiles": quantiles,
            "rank_lookback": rank_lookback,
            "rolling_train_bars": rolling_train_bars,
            "rolling_test_bars": rolling_test_bars,
            "rolling_step_bars": rolling_step_bars,
            "min_observations": min_observations,
        },
        "factor_specs": [asdict(spec) | {"name": spec.name} for spec in specs],
        "factor_metrics": metrics,
        "factor_decay": decay,
        "factor_correlations": correlations,
        "rolling_windows": rolling_windows,
        "stability": stability,
        "warnings": warnings,
    }
    digest_payload: dict[str, Any] = dict(evidence)
    digest_dataset = dict(evidence["dataset"])
    digest_dataset.pop("output_csv", None)
    digest_dataset = {
        key: digest_dataset[key]
        for key in (
            "exchange",
            "symbol",
            "source_timeframe",
            "target_timeframe",
            "replay_bar_count",
            "dataset_sha256",
        )
    }
    digest_payload["dataset"] = digest_dataset
    research_sha256 = hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result = {
        "research_sha256": research_sha256,
        **evidence,
        "export_paths": {},
    }
    if output_dir is not None:
        result["export_paths"] = _export_factor_research(
            result,
            research_frame,
            Path(output_dir),
            target_timeframe=target_timeframe,
            dataset_sha256=dataset.report.dataset_sha256,
        )
    return result


def format_factor_research_report(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"research_status: {report['research_status']}",
            f"research_sha256: {report['research_sha256']}",
            f"dataset_sha256: {report['dataset']['dataset_sha256']}",
            f"replay_bar_count: {report['dataset']['replay_bar_count']}",
            f"factor_count: {len(report['factor_specs'])}",
            f"metric_count: {len(report['factor_metrics'])}",
            f"decay_metric_count: {len(report['factor_decay'])}",
            f"rolling_window_metric_count: {len(report['rolling_windows'])}",
            "readiness_changed: false",
            "automatic_factor_approval: false",
            f"warnings: {','.join(report['warnings'])}",
        ]
    )


def _factor_metrics(
    frame: pd.DataFrame,
    *,
    factor_names: list[str],
    horizons: tuple[int, ...],
    cost_bps: tuple[float, ...],
    quantiles: int,
    rank_lookback: int,
    min_observations: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rank_min_periods = min(rank_lookback, max(5, min_observations))
    for factor_name in factor_names:
        factor = frame[factor_name]
        percentile = factor.rolling(
            rank_lookback,
            min_periods=rank_min_periods,
        ).rank(pct=True)
        position = pd.Series(0.0, index=frame.index)
        position.loc[percentile <= 1.0 / quantiles] = -1.0
        position.loc[percentile > 1.0 - 1.0 / quantiles] = 1.0
        turnover_series = position.diff().abs().fillna(position.abs()) / 2.0
        turnover = _round_optional(float(turnover_series.mean()))
        for horizon in horizons:
            forward_column = f"forward_return_{horizon}"
            paired = pd.DataFrame(
                {
                    "factor": factor,
                    "forward_return": frame[forward_column],
                    "position": position,
                    "turnover": turnover_series,
                }
            ).dropna(subset=["factor", "forward_return"])
            top = paired.loc[paired["position"] == 1.0, "forward_return"]
            bottom = paired.loc[paired["position"] == -1.0, "forward_return"]
            gross_position_return = paired["position"] * paired["forward_return"]
            net_by_cost = {
                _cost_key(cost): _round_optional(
                    float(
                        (
                            gross_position_return
                            - paired["turnover"] * cost / 10_000.0
                        ).mean()
                    )
                )
                for cost in cost_bps
            }
            rows.append(
                {
                    "factor": factor_name,
                    "horizon": horizon,
                    "observation_count": len(paired),
                    "pearson_ic": _correlation(
                        paired["factor"],
                        paired["forward_return"],
                        min_observations,
                    ),
                    "rank_ic": _rank_correlation(
                        paired["factor"],
                        paired["forward_return"],
                        min_observations,
                    ),
                    "top_quantile_count": len(top),
                    "bottom_quantile_count": len(bottom),
                    "top_quantile_mean_forward_return": _series_mean(top),
                    "bottom_quantile_mean_forward_return": _series_mean(bottom),
                    "gross_quantile_spread": _round_optional(
                        float(top.mean() - bottom.mean())
                        if not top.empty and not bottom.empty
                        else None
                    ),
                    "mean_turnover": turnover,
                    "mean_long_short_return": _series_mean(gross_position_return),
                    "mean_net_return_by_cost_bps": net_by_cost,
                }
            )
    return rows


def _summarize_decay(
    metrics: list[dict[str, Any]],
    horizons: tuple[int, ...],
) -> list[dict[str, Any]]:
    if not metrics:
        return []
    base_horizon = min(horizons)
    by_factor_horizon = {
        (str(row["factor"]), int(row["horizon"])): row
        for row in metrics
    }
    rows: list[dict[str, Any]] = []
    factor_names = sorted({str(row["factor"]) for row in metrics})
    for factor_name in factor_names:
        base = by_factor_horizon[(factor_name, base_horizon)]
        for horizon in sorted(set(horizons)):
            current = by_factor_horizon[(factor_name, horizon)]
            pearson_base = base["pearson_ic"]
            pearson_current = current["pearson_ic"]
            rank_base = base["rank_ic"]
            rank_current = current["rank_ic"]
            rows.append(
                {
                    "factor": factor_name,
                    "base_horizon": base_horizon,
                    "horizon": horizon,
                    "pearson_ic": pearson_current,
                    "pearson_ic_retention_ratio": _retention_ratio(
                        pearson_current,
                        pearson_base,
                    ),
                    "absolute_pearson_ic_retention_ratio": _absolute_retention_ratio(
                        pearson_current,
                        pearson_base,
                    ),
                    "pearson_ic_sign_preserved": _sign_preserved(
                        pearson_current,
                        pearson_base,
                    ),
                    "rank_ic": rank_current,
                    "rank_ic_retention_ratio": _retention_ratio(
                        rank_current,
                        rank_base,
                    ),
                    "absolute_rank_ic_retention_ratio": _absolute_retention_ratio(
                        rank_current,
                        rank_base,
                    ),
                    "rank_ic_sign_preserved": _sign_preserved(
                        rank_current,
                        rank_base,
                    ),
                }
            )
    return rows


def _factor_correlations(
    frame: pd.DataFrame,
    factor_names: list[str],
    min_observations: int,
) -> list[dict[str, Any]]:
    rows = []
    for left in factor_names:
        for right in factor_names:
            paired = pd.concat(
                [frame[left].rename("left"), frame[right].rename("right")],
                axis=1,
            ).dropna()
            rows.append(
                {
                    "factor_left": left,
                    "factor_right": right,
                    "observation_count": len(paired),
                    "correlation": _correlation(
                        paired["left"],
                        paired["right"],
                        min_observations,
                    ),
                }
            )
    return rows


def _rolling_stability_windows(
    frame: pd.DataFrame,
    *,
    factor_names: list[str],
    horizons: tuple[int, ...],
    train_bars: int,
    test_bars: int,
    step_bars: int,
    min_observations: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    window_index = 0
    start = 0
    while start + train_bars + test_bars <= len(frame):
        train_end = start + train_bars
        test_end = train_end + test_bars
        for factor_name in factor_names:
            for horizon in horizons:
                forward_column = f"forward_return_{horizon}"
                train_stop = max(start, train_end - horizon)
                test_stop = max(train_end, test_end - horizon)
                train = frame.iloc[start:train_stop]
                test = frame.iloc[train_end:test_stop]
                train_pair = train[[factor_name, forward_column]].dropna()
                test_pair = test[[factor_name, forward_column]].dropna()
                train_ic = _correlation(
                    train_pair[factor_name],
                    train_pair[forward_column],
                    min_observations,
                )
                test_ic = _correlation(
                    test_pair[factor_name],
                    test_pair[forward_column],
                    min_observations,
                )
                rows.append(
                    {
                        "window_index": window_index,
                        "factor": factor_name,
                        "horizon": horizon,
                        "train_start": _timestamp_at(frame, start),
                        "train_end": _timestamp_at(frame, train_end - 1),
                        "test_start": _timestamp_at(frame, train_end),
                        "test_end": _timestamp_at(frame, test_end - 1),
                        "train_observation_count": len(train_pair),
                        "test_observation_count": len(test_pair),
                        "train_ic": train_ic,
                        "test_ic": test_ic,
                        "train_rank_ic": _rank_correlation(
                            train_pair[factor_name],
                            train_pair[forward_column],
                            min_observations,
                        ),
                        "test_rank_ic": _rank_correlation(
                            test_pair[factor_name],
                            test_pair[forward_column],
                            min_observations,
                        ),
                        "ic_sign_agreement": (
                            None
                            if train_ic is None or test_ic is None
                            else bool(np.sign(train_ic) == np.sign(test_ic))
                        ),
                    }
                )
        window_index += 1
        start += step_bars
    return rows


def _summarize_stability(
    rolling_windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rolling_windows:
        return []
    frame = pd.DataFrame(rolling_windows)
    summaries = []
    for (factor, horizon), group in frame.groupby(["factor", "horizon"], sort=True):
        numeric_ic = pd.to_numeric(group["test_ic"], errors="coerce").dropna()
        numeric_rank_ic = pd.to_numeric(
            group["test_rank_ic"],
            errors="coerce",
        ).dropna()
        sign_agreement = group["ic_sign_agreement"].dropna()
        summaries.append(
            {
                "factor": factor,
                "horizon": int(horizon),
                "window_count": len(group),
                "numeric_test_ic_count": len(numeric_ic),
                "mean_test_ic": _series_mean(numeric_ic),
                "median_test_ic": _series_median(numeric_ic),
                "worst_test_ic": _series_min(numeric_ic),
                "positive_test_ic_ratio": _round_optional(
                    float((numeric_ic > 0).mean()) if not numeric_ic.empty else None
                ),
                "mean_test_rank_ic": _series_mean(numeric_rank_ic),
                "ic_sign_agreement_ratio": _round_optional(
                    float(sign_agreement.astype(bool).mean())
                    if not sign_agreement.empty
                    else None
                ),
            }
        )
    return summaries


def _research_warnings(
    *,
    replay_bar_count: int,
    rolling_window_count: int,
) -> list[str]:
    warnings = [
        "time_series_ic_not_cross_sectional_ic",
        "factor_returns_are_diagnostic_not_executable_pnl",
        "no_automatic_readiness_upgrade",
    ]
    if replay_bar_count < 1_000:
        warnings.append("short_history_under_1000_bars")
    if rolling_window_count < 3:
        warnings.append("fewer_than_3_rolling_windows")
    return warnings


def _validate_settings(
    *,
    horizons: tuple[int, ...],
    cost_bps: tuple[float, ...],
    quantiles: int,
    rank_lookback: int,
    rolling_train_bars: int,
    rolling_test_bars: int,
    rolling_step_bars: int,
    min_observations: int,
) -> None:
    if not horizons or any(horizon <= 0 for horizon in horizons):
        raise MarketDataError("factor_horizons_must_be_positive")
    if not cost_bps or any(cost < 0 for cost in cost_bps):
        raise MarketDataError("factor_cost_bps_must_be_non_negative")
    if quantiles < 2:
        raise MarketDataError("factor_quantiles_must_be_at_least_two")
    if rank_lookback < 2:
        raise MarketDataError("factor_rank_lookback_must_be_at_least_two")
    if min(
        rolling_train_bars,
        rolling_test_bars,
        rolling_step_bars,
        min_observations,
    ) <= 0:
        raise MarketDataError("factor_rolling_settings_must_be_positive")


def _correlation(
    left: pd.Series,
    right: pd.Series,
    min_observations: int,
) -> float | None:
    paired = pd.concat([left, right], axis=1).dropna()
    if len(paired) < min_observations:
        return None
    if paired.iloc[:, 0].nunique() < 2 or paired.iloc[:, 1].nunique() < 2:
        return None
    return _round_optional(float(paired.iloc[:, 0].corr(paired.iloc[:, 1])))


def _rank_correlation(
    left: pd.Series,
    right: pd.Series,
    min_observations: int,
) -> float | None:
    paired = pd.concat([left, right], axis=1).dropna()
    if len(paired) < min_observations:
        return None
    return _correlation(
        paired.iloc[:, 0].rank(method="average"),
        paired.iloc[:, 1].rank(method="average"),
        min_observations,
    )


def _series_mean(series: pd.Series) -> float | None:
    return _round_optional(float(series.mean()) if not series.empty else None)


def _series_median(series: pd.Series) -> float | None:
    return _round_optional(float(series.median()) if not series.empty else None)


def _series_min(series: pd.Series) -> float | None:
    return _round_optional(float(series.min()) if not series.empty else None)


def _round_optional(value: float | None) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(value, 12)


def _cost_key(cost: float) -> str:
    return f"{cost:g}"


def _retention_ratio(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base == 0:
        return None
    return _round_optional(current / base)


def _absolute_retention_ratio(
    current: float | None,
    base: float | None,
) -> float | None:
    if current is None or base is None or base == 0:
        return None
    return _round_optional(abs(current) / abs(base))


def _sign_preserved(current: float | None, base: float | None) -> bool | None:
    if current is None or base is None:
        return None
    return bool(np.sign(current) == np.sign(base))


def _timestamp_at(frame: pd.DataFrame, index: int) -> str:
    return pd.Timestamp(frame.iloc[index]["timestamp"]).isoformat()


def _export_factor_research(
    report: dict[str, Any],
    research_frame: pd.DataFrame,
    output_dir: Path,
    *,
    target_timeframe: str,
    dataset_sha256: str,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"factor_research_{target_timeframe}_{dataset_sha256[:12]}"
    paths = {
        "summary": output_dir / f"{prefix}.json",
        "metrics": output_dir / f"{prefix}_metrics.csv",
        "decay": output_dir / f"{prefix}_decay.csv",
        "correlations": output_dir / f"{prefix}_correlations.csv",
        "rolling_windows": output_dir / f"{prefix}_rolling_windows.csv",
        "stability": output_dir / f"{prefix}_stability.csv",
        "factor_values": output_dir / f"{prefix}_factor_values.csv",
    }
    export_report = dict(report)
    export_report["export_paths"] = {
        name: str(path)
        for name, path in paths.items()
    }
    _write_json_atomically(paths["summary"], export_report)
    _write_dict_rows(paths["metrics"], report["factor_metrics"])
    _write_dict_rows(paths["decay"], report["factor_decay"])
    _write_dict_rows(paths["correlations"], report["factor_correlations"])
    _write_dict_rows(paths["rolling_windows"], report["rolling_windows"])
    _write_dict_rows(paths["stability"], report["stability"])
    factor_values = research_frame.copy()
    factor_values["timestamp"] = pd.to_datetime(
        factor_values["timestamp"],
        utc=True,
    ).map(lambda value: value.isoformat())
    _write_text_atomically(
        paths["factor_values"],
        factor_values.to_csv(index=False, lineterminator="\n"),
    )
    return {name: str(path) for name, path in paths.items()}


def _write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        _write_text_atomically(path, "")
        return
    fieldnames = list(rows[0])
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomically(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
    )


def _write_text_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
