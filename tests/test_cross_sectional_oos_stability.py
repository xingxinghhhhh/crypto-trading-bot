import csv
import hashlib
import io
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crypto_bot.cross_sectional_ic_calibration import _source_research_identity
from crypto_bot.cross_sectional_oos_stability import (
    build_purged_expanding_windows,
    distribution_metrics,
    format_cross_sectional_oos_stability,
    run_cross_sectional_oos_stability,
)
from crypto_bot.errors import MarketDataError


def test_purged_expanding_windows_use_fixed_half_and_remainder_policy():
    windows = build_purged_expanding_windows(12533, horizon_bars=4)

    assert len(windows) == 10
    assert windows[0].train_raw_end_exclusive == 6266
    assert windows[0].train_effective_end_exclusive == 6262
    assert [window.oos_raw_end_exclusive - window.oos_raw_start for window in windows] == [
        627,
        627,
        627,
        627,
        627,
        627,
        627,
        626,
        626,
        626,
    ]
    assert windows[-1].oos_raw_end_exclusive == 12533
    assert windows[-1].oos_effective_end_exclusive == 12529


@pytest.mark.parametrize("candidate_count", [0, -1, True])
def test_purged_expanding_windows_reject_invalid_candidate_count(candidate_count):
    with pytest.raises(ValueError, match="candidate_count"):
        build_purged_expanding_windows(candidate_count, horizon_bars=1)


def test_purged_expanding_windows_fail_when_fold_cannot_be_purged():
    with pytest.raises(MarketDataError, match="fold_too_short"):
        build_purged_expanding_windows(40, horizon_bars=2)


def test_distribution_metrics_are_deterministic_and_use_sample_std():
    metrics = distribution_metrics([-2.0, 0.0, 2.0, 4.0])

    assert metrics.count == 4
    assert metrics.mean == 1.0
    assert metrics.median == 1.0
    assert metrics.sample_std == pytest.approx(math.sqrt(20 / 3))
    assert metrics.positive_ratio == 0.5


def test_distribution_metrics_reject_non_finite_and_singleton():
    with pytest.raises(MarketDataError, match="insufficient_metric"):
        distribution_metrics([1.0])
    with pytest.raises(MarketDataError, match="non_finite"):
        distribution_metrics([1.0, math.nan])


def test_oos_stability_exports_deterministic_content_addressed_evidence(tmp_path):
    source = _write_source_bundle(tmp_path / "source", candidate_count=10240)

    first = run_cross_sectional_oos_stability(source, tmp_path / "first")
    second = run_cross_sectional_oos_stability(source, tmp_path / "second")

    assert first.report == second.report
    assert first.report["research_status"] == "post_hoc_chronological_oos_stability_only"
    assert first.report["fold_row_count"] == 20
    assert first.report["summary_row_count"] == 2
    assert first.report["readiness_changed"] is False
    assert first.report["automatic_factor_approval"] is False
    assert first.report["policies"]["statistical_inference"] == "none"
    assert first.report["policies"]["randomness"] == "none"
    assert first.report["summaries"][0]["pooled_oos_valid_count"] == 5110
    assert first.report["summaries"][1]["pooled_oos_valid_count"] == 5080
    first_fold = first.report["folds"][0]
    assert first_fold["train_raw_candidate_count"] == 5120
    assert first_fold["train_purged_candidate_count"] == 1
    assert first_fold["train_candidate_count"] == 5119
    assert first_fold["train_prefix_invalid_count"] == 2
    assert first_fold["train_valid_count"] == 5117
    assert first_fold["oos_raw_candidate_count"] == 512
    assert first_fold["oos_valid_count"] == 511
    assert first_fold["mean_rank_ic_difference"] == pytest.approx(
        first_fold["oos_mean_rank_ic"] - first_fold["train_mean_rank_ic"]
    )
    for name in ("report", "folds", "summaries"):
        first_bytes = Path(first.export_paths[name]).read_bytes()
        second_bytes = Path(second.export_paths[name]).read_bytes()
        assert first_bytes == second_bytes
    report_name = Path(first.export_paths["report"]).name
    assert first.report["analysis_sha256"] in report_name
    for artifact_name in ("folds", "summaries"):
        artifact = first.report["artifacts"][artifact_name]
        artifact_path = Path(first.export_paths[artifact_name])
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == artifact["sha256"]


def test_oos_stability_binds_promotion_without_changing_generic_path(tmp_path):
    promotion = {"promotion_sha256": "f" * 64, "design": {"frozen": True}}
    source = _write_source_bundle(
        tmp_path / "source",
        candidate_count=10240,
        promotion=promotion,
    )

    result = run_cross_sectional_oos_stability(source, tmp_path / "out")

    assert result.report["research_status"] == "promotion_aware_chronological_oos_stability_only"
    assert result.report["promotion"] == promotion
    assert Path(result.export_paths["report"]).name.startswith(
        "promoted-cross-sectional-oos-stability."
    )
    assert "prior_related_results_exist" in result.report["warnings"]


def test_oos_stability_rejects_internal_source_gap_via_shared_validator(tmp_path):
    source = _write_source_bundle(
        tmp_path / "source",
        candidate_count=10240,
        internal_invalid=True,
    )

    with pytest.raises(MarketDataError, match="internal_invalid"):
        run_cross_sectional_oos_stability(source, tmp_path / "out")


def test_oos_stability_fails_closed_on_per_fold_minimum(tmp_path):
    source = _write_source_bundle(tmp_path / "source", candidate_count=10000)

    with pytest.raises(MarketDataError, match="insufficient_oos_sample"):
        run_cross_sectional_oos_stability(source, tmp_path / "out")


def test_oos_stability_format_and_cli(tmp_path):
    source = _write_source_bundle(tmp_path / "source", candidate_count=10240)
    output = tmp_path / "output"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "analyze-cross-sectional-oos-stability",
            "--source-report",
            str(source),
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "fold_row_count: 20" in completed.stdout
    assert "summary_row_count: 2" in completed.stdout
    assert len(list(output.glob("cross-sectional-oos-stability.*"))) == 3
    result = run_cross_sectional_oos_stability(source, tmp_path / "direct")
    formatted = format_cross_sectional_oos_stability(result)
    assert "readiness_changed: false" in formatted
    assert "automatic_factor_approval: false" in formatted


def _write_source_bundle(
    directory: Path,
    *,
    candidate_count: int,
    internal_invalid: bool = False,
    promotion: dict[str, object] | None = None,
) -> Path:
    directory.mkdir(parents=True)
    factors = [{"family": "momentum", "name": "momentum_2", "window": 2}]
    horizons = [1, 4]
    required_assets = 3
    rows: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    total_valid = 0
    for horizon in horizons:
        statuses = (
            ["insufficient_assets"] * 2
            + ["valid"] * (candidate_count - 2 - horizon)
            + ["insufficient_assets"] * horizon
        )
        if internal_invalid:
            statuses[candidate_count // 3] = "insufficient_assets"
        values: list[float] = []
        for index, status in enumerate(statuses):
            value = 0.08 * math.sin(index / 37) + 0.03 * math.cos(index / 91) + horizon / 1000
            timestamp = (
                datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
            ).isoformat()
            if status == "valid":
                rank_ic: object = format(value, ".15g")
                n_valid = required_assets
                unique_count = required_assets
                values.append(float(rank_ic))
            else:
                rank_ic = ""
                n_valid = 0
                unique_count = 0
            rows.append(
                {
                    "factor_name": "momentum_2",
                    "horizon_bars": horizon,
                    "timestamp": timestamp,
                    "status": status,
                    "n_valid_assets": n_valid,
                    "required_asset_count": required_assets,
                    "factor_unique_count": unique_count,
                    "forward_return_unique_count": unique_count,
                    "rank_ic": rank_ic,
                }
            )
        total_valid += len(values)
        invalid_count = candidate_count - len(values)
        summaries.append(
            {
                "factor_name": "momentum_2",
                "horizon_bars": horizon,
                "candidate_timestamp_count": candidate_count,
                "valid_ic_timestamp_count": len(values),
                "insufficient_assets_count": invalid_count,
                "constant_both_count": 0,
                "constant_factor_count": 0,
                "constant_forward_return_count": 0,
                "undefined_correlation_count": 0,
                "mean_rank_ic": math.fsum(values) / len(values),
                "median_rank_ic": 0.0,
                "sample_std_rank_ic": 0.1,
                "positive_ratio": 0.5,
                "unique_rank_ic_value_count": len(set(values)),
            }
        )
    ic_bytes = _csv_bytes(
        rows,
        (
            "factor_name",
            "horizon_bars",
            "timestamp",
            "status",
            "n_valid_assets",
            "required_asset_count",
            "factor_unique_count",
            "forward_return_unique_count",
            "rank_ic",
        ),
    )
    observations_bytes = b"dummy\n" + b"x\n" * (total_valid * required_assets)
    ic_hash = hashlib.sha256(ic_bytes).hexdigest()
    observations_hash = hashlib.sha256(observations_bytes).hexdigest()
    panel_hash = "a" * 64
    source: dict[str, object] = {
        "schema_version": 1,
        "research_status": "cross_sectional_rank_ic_evidence_only",
        "panel": {
            "panel_id": "synthetic_1h",
            "panel_sha256": panel_hash,
            "alignment": "inner_exact",
            "timeframe": "1h",
            "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            "dataset_ids": ["btc", "eth", "sol"],
            "timestamp_semantics": {"status": "unverified"},
            "alignment_summary": {},
            "datasets": [
                {
                    "dataset_id": name,
                    "symbol": f"{name.upper()}/USDT",
                    "raw_sha256": character * 64,
                    "canonical_sha256": character * 64,
                }
                for name, character in (("btc", "b"), ("eth", "c"), ("sol", "d"))
            ],
        },
        "factor_specs": factors,
        "horizons": horizons,
        "required_asset_count": required_assets,
        "policies": {"fixture": "fixed"},
        "summaries": summaries,
        "artifacts": {
            "ic_time_series": {"filename": "pending", "sha256": ic_hash, "row_count": len(rows)},
            "valid_observations": {
                "filename": "pending",
                "sha256": observations_hash,
                "row_count": total_valid * required_assets,
            },
        },
        "warnings": [],
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    if promotion is not None:
        source["promotion"] = promotion
    identity = _source_research_identity(source)  # type: ignore[arg-type]
    research_hash = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prefix = "promoted-cross-sectional-research" if promotion is not None else "cross-sectional-factor-research"
    stem = f"{prefix}.{research_hash}"
    source["research_sha256"] = research_hash
    artifacts = source["artifacts"]
    assert isinstance(artifacts, dict)
    artifacts["ic_time_series"]["filename"] = f"{stem}.ic.csv"  # type: ignore[index]
    artifacts["valid_observations"]["filename"] = f"{stem}.observations.csv"  # type: ignore[index]
    (directory / f"{stem}.ic.csv").write_bytes(ic_bytes)
    (directory / f"{stem}.observations.csv").write_bytes(observations_bytes)
    report_path = directory / f"{stem}.json"
    report_path.write_text(
        json.dumps(source, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    return report_path


def _csv_bytes(rows: list[dict[str, object]], fields: tuple[str, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()
