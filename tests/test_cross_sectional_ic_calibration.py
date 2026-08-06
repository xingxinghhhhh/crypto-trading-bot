import csv
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from crypto_bot.cross_sectional_ic_calibration import (
    format_cross_sectional_ic_calibration,
    holm_adjust,
    newey_west_mean_test,
    run_cross_sectional_ic_calibration,
)
from crypto_bot.errors import MarketDataError


def test_newey_west_mean_test_matches_manual_bartlett_formula():
    values = [0.12 * math.sin(index / 7) + 0.03 * math.cos(index / 19) + 0.01 for index in range(700)]

    result = newey_west_mean_test(values, horizon_bars=4)

    n = len(values)
    mean = math.fsum(values) / n
    centered = [value - mean for value in values]
    bandwidth_auto = math.floor(4 * (n / 100) ** (2 / 9))
    bandwidth = max(3, bandwidth_auto)
    lrv = math.fsum(value * value for value in centered) / n
    for lag in range(1, bandwidth + 1):
        gamma = math.fsum(centered[index] * centered[index - lag] for index in range(lag, n)) / n
        lrv += 2 * (1 - lag / (bandwidth + 1)) * gamma

    assert result.mean == pytest.approx(mean)
    assert result.bandwidth_auto == bandwidth_auto
    assert result.bandwidth_overlap_floor == 3
    assert result.bandwidth_used == bandwidth
    assert result.long_run_variance == pytest.approx(lrv)
    assert result.hac_standard_error == pytest.approx(math.sqrt(lrv / n))
    assert result.p_value_raw_two_sided == pytest.approx(
        math.erfc(abs(result.z_statistic) / math.sqrt(2))
    )
    assert result.ci95_lower < result.mean < result.ci95_upper


def test_hac_uses_overlap_floor_and_rejects_invalid_inputs():
    values = [0.1 * math.sin(index / 5) for index in range(1600)]
    result = newey_west_mean_test(values, horizon_bars=100)
    assert result.bandwidth_overlap_floor == 99
    assert result.bandwidth_used == 99

    with pytest.raises(ValueError, match="positive integer"):
        newey_west_mean_test(values, horizon_bars=0)
    with pytest.raises(ValueError, match="finite"):
        newey_west_mean_test([*values, math.nan], horizon_bars=1)
    with pytest.raises(MarketDataError, match="insufficient_sample"):
        newey_west_mean_test([0.1, 0.2], horizon_bars=1)
    with pytest.raises(MarketDataError, match="non_positive_lrv"):
        newey_west_mean_test([0.5] * 600, horizon_bars=1)


def test_holm_adjust_is_step_down_monotone_and_deterministic_for_ties():
    adjusted = holm_adjust(
        [
            (("b", 1), 0.04),
            (("a", 1), 0.01),
            (("c", 1), 0.03),
            (("a", 4), 0.03),
        ]
    )
    assert adjusted[("a", 1)] == pytest.approx(0.04)
    assert adjusted[("a", 4)] == pytest.approx(0.09)
    assert adjusted[("c", 1)] == pytest.approx(0.09)
    assert adjusted[("b", 1)] == pytest.approx(0.09)

    with pytest.raises(ValueError, match="at least one"):
        holm_adjust([])
    with pytest.raises(ValueError, match="unique"):
        holm_adjust([(("a", 1), 0.1), (("a", 1), 0.2)])
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        holm_adjust([(("a", 1), 1.1)])


def test_calibration_exports_deterministic_auditable_evidence(tmp_path):
    source = _write_source_bundle(tmp_path / "source")

    first = run_cross_sectional_ic_calibration(source, tmp_path / "first")
    second = run_cross_sectional_ic_calibration(source, tmp_path / "second")

    assert first.report["calibration_sha256"] == second.report["calibration_sha256"]
    assert first.report["statistical_status"] == "dependence_aware_mean_rank_ic_calibration_only"
    assert first.report["hypothesis_count"] == 2
    assert first.report["readiness_changed"] is False
    assert first.report["automatic_factor_approval"] is False
    assert first.report["panel"]["timestamp_semantics"]["status"] == "unverified"
    assert first.report["policies"]["randomness"] == "none"
    assert "no_profitability_or_execution_claim" in first.report["warnings"]
    for name in first.export_paths:
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()
    rows = _read_csv(first.export_paths["hypotheses"])
    assert [(row["factor_name"], row["horizon_bars"]) for row in rows] == [
        ("momentum_2", "1"),
        ("momentum_2", "4"),
    ]
    assert all(0 <= float(row["p_value_raw_two_sided"]) <= 1 for row in rows)
    assert all(float(row["p_value_holm"]) >= float(row["p_value_raw_two_sided"]) for row in rows)
    assert all(float(row["hac_standard_error"]) > 0 for row in rows)
    artifact = first.report["artifacts"]["hypotheses"]
    assert hashlib.sha256(Path(first.export_paths["hypotheses"]).read_bytes()).hexdigest() == artifact["sha256"]

    formatted = format_cross_sectional_ic_calibration(first)
    assert f"calibration_sha256: {first.report['calibration_sha256']}" in formatted
    assert "hypothesis_count: 2" in formatted
    assert "readiness_changed: false" in formatted


def test_calibration_is_idempotent_and_content_collision_fails_closed(tmp_path):
    source = _write_source_bundle(tmp_path / "source")
    output = tmp_path / "output"
    first = run_cross_sectional_ic_calibration(source, output)
    second = run_cross_sectional_ic_calibration(source, output)
    assert first.export_paths == second.export_paths

    Path(first.export_paths["hypotheses"]).write_text("corrupted", encoding="utf-8")
    with pytest.raises(MarketDataError, match="content_addressed_artifact_collision"):
        run_cross_sectional_ic_calibration(source, output)

    clean_output = tmp_path / "report-collision"
    clean = run_cross_sectional_ic_calibration(source, clean_output)
    Path(clean.export_paths["report"]).write_text("corrupted", encoding="utf-8")
    with pytest.raises(MarketDataError, match="content_addressed_artifact_collision"):
        run_cross_sectional_ic_calibration(source, clean_output)


def test_calibration_fails_closed_on_internal_invalid_timestamp(tmp_path):
    source = _write_source_bundle(tmp_path / "source", internal_invalid=True)

    with pytest.raises(MarketDataError, match="internal_invalid_timestamp"):
        run_cross_sectional_ic_calibration(source, tmp_path / "output")
    assert not list((tmp_path / "output").glob("*.json")) if (tmp_path / "output").exists() else True


def test_source_report_basic_shape_and_file_failures(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        run_cross_sectional_ic_calibration(tmp_path / "missing.json", tmp_path / "output")

    malformed = tmp_path / "bad.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(MarketDataError, match="invalid_source_json"):
        run_cross_sectional_ic_calibration(malformed, tmp_path / "output")

    not_mapping = tmp_path / "list.json"
    not_mapping.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="source_must_be_mapping"):
        run_cross_sectional_ic_calibration(not_mapping, tmp_path / "output")


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("unsafe_readiness", "unsafe_source_readiness"),
        ("wrong_status", "invalid_source_status"),
        ("wrong_schema", "unsupported_source_schema"),
        ("wrong_research_hash", "source_filename_mismatch"),
        ("wrong_timestamp_semantics", "unexpected_timestamp_semantics"),
        ("artifact_path_escape", "artifact_filename_mismatch"),
        ("wrong_artifact_hash", "source_ic_hash_mismatch"),
        ("wrong_observations_hash", "source_observations_hash_mismatch"),
        ("wrong_artifact_rows", "source_ic_row_count_mismatch"),
        ("wrong_observations_rows", "source_observations_row_count_mismatch"),
        ("wrong_identity", "source_research_hash_mismatch"),
    ],
)
def test_source_bundle_validation_failures(tmp_path, mutation, expected):
    source = _write_source_bundle(tmp_path / "source")
    report = json.loads(source.read_text(encoding="utf-8"))
    if mutation == "unsafe_readiness":
        report["readiness_changed"] = True
    elif mutation == "wrong_status":
        report["research_status"] = "wrong"
    elif mutation == "wrong_schema":
        report["schema_version"] = 2
    elif mutation == "wrong_research_hash":
        report["research_sha256"] = "f" * 64
    elif mutation == "wrong_timestamp_semantics":
        report["panel"]["timestamp_semantics"]["status"] = "verified"
    elif mutation == "artifact_path_escape":
        report["artifacts"]["ic_time_series"]["filename"] = "../escaped.csv"
    elif mutation == "wrong_artifact_hash":
        report["artifacts"]["ic_time_series"]["sha256"] = "f" * 64
    elif mutation == "wrong_observations_hash":
        report["artifacts"]["valid_observations"]["sha256"] = "f" * 64
    elif mutation == "wrong_artifact_rows":
        report["artifacts"]["ic_time_series"]["row_count"] += 1
    elif mutation == "wrong_observations_rows":
        report["artifacts"]["valid_observations"]["row_count"] += 1
    elif mutation == "wrong_identity":
        report["policies"]["rank"] = "wrong"
    source.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    with pytest.raises(MarketDataError, match=expected):
        run_cross_sectional_ic_calibration(source, tmp_path / "output")


def test_source_artifact_missing_and_empty_fail_closed(tmp_path):
    source = _write_source_bundle(tmp_path / "missing-source")
    report = json.loads(source.read_text(encoding="utf-8"))
    observations = source.parent / report["artifacts"]["valid_observations"]["filename"]
    observations.unlink()
    with pytest.raises(MarketDataError, match="missing_or_escaped"):
        run_cross_sectional_ic_calibration(source, tmp_path / "output")

    source = _write_source_bundle(tmp_path / "empty-source")
    report = json.loads(source.read_text(encoding="utf-8"))
    observations = source.parent / report["artifacts"]["valid_observations"]["filename"]
    observations.write_bytes(b"")
    with pytest.raises(MarketDataError, match="empty_source_csv"):
        run_cross_sectional_ic_calibration(source, tmp_path / "output")


def test_cli_runs_independent_calibration_command(tmp_path):
    source = _write_source_bundle(tmp_path / "source")
    output = tmp_path / "cli-output"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "calibrate-cross-sectional-rank-ic",
            "--source-report",
            str(source),
            "--output-dir",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0
    assert "statistical_status: dependence_aware_mean_rank_ic_calibration_only" in completed.stdout
    assert "hypothesis_count: 2" in completed.stdout
    assert len(list(output.glob("cross-sectional-ic-calibration.*"))) == 2


def _write_source_bundle(directory: Path, *, internal_invalid: bool = False) -> Path:
    directory.mkdir(parents=True)
    factors = [{"family": "momentum", "name": "momentum_2", "window": 2}]
    horizons = [1, 4]
    required_assets = 3
    rows: list[dict[str, object]] = []
    summaries = []
    total_valid = 0
    candidate_count = 626
    for horizon in horizons:
        statuses = (
            ["insufficient_assets"] * 2
            + ["valid"] * (candidate_count - 2 - horizon)
            + ["insufficient_assets"] * horizon
        )
        if internal_invalid:
            statuses[200] = "insufficient_assets"
        values = []
        status_counts = {status: 0 for status in (
            "valid",
            "insufficient_assets",
            "constant_both",
            "constant_factor",
            "constant_forward_return",
            "undefined_correlation",
        )}
        for index, status in enumerate(statuses):
            value = 0.14 * math.sin(index / 9) + 0.04 * math.cos(index / 23) + horizon / 1000
            timestamp = f"2026-01-{1 + index // 24:02d}T{index % 24:02d}:00:00+00:00"
            if status == "valid":
                rank_ic: object = format(value, ".15g")
                n_valid = required_assets
                unique_count = required_assets
                values.append(value)
            else:
                rank_ic = ""
                n_valid = 0
                unique_count = 0
            status_counts[status] += 1
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
        summaries.append(
            {
                "factor_name": "momentum_2",
                "horizon_bars": horizon,
                "candidate_timestamp_count": len(statuses),
                "valid_ic_timestamp_count": len(values),
                "insufficient_assets_count": status_counts["insufficient_assets"],
                "constant_both_count": 0,
                "constant_factor_count": 0,
                "constant_forward_return_count": 0,
                "undefined_correlation_count": 0,
                "mean_rank_ic": round(math.fsum(values) / len(values), 12),
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
    observation_buffer = ["symbol\n"] + ["BTC/USDT\n"] * (total_valid * required_assets)
    observations_bytes = "".join(observation_buffer).encode("utf-8")
    ic_sha = hashlib.sha256(ic_bytes).hexdigest()
    observations_sha = hashlib.sha256(observations_bytes).hexdigest()
    datasets = [
        {
            "dataset_id": f"{symbol.lower()}_v1",
            "symbol": f"{symbol}/USDT",
            "raw_sha256": character * 64,
            "canonical_sha256": character.upper().lower() * 64,
        }
        for symbol, character in (("BTC", "a"), ("ETH", "b"), ("SOL", "c"))
    ]
    policies = {
        "factor_computation": "per_asset_full_source_history_then_panel_timestamp_filter",
        "forward_return": "close_shift_minus_horizon_divided_by_close_minus_one",
        "required_assets": "all_panel_constituents",
        "rank": "ascending_average_ties",
        "constant": "explicit_skip_status",
        "non_finite": "paired_finite_only_no_fill",
        "rank_ic": "pearson_correlation_of_cross_sectional_average_ranks",
    }
    identity = {
        "schema_version": 1,
        "panel_id": "three_1h_v1",
        "panel_sha256": "d" * 64,
        "alignment": "inner_exact",
        "timestamp_semantics_status": "unverified",
        "components": datasets,
        "factor_specs": factors,
        "horizons": horizons,
        "required_asset_count": required_assets,
        "policies": policies,
        "artifacts": {
            "ic_time_series_sha256": ic_sha,
            "valid_observations_sha256": observations_sha,
        },
    }
    research_sha = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    stem = f"cross-sectional-factor-research.{research_sha}"
    ic_name = f"{stem}.ic.csv"
    observations_name = f"{stem}.observations.csv"
    (directory / ic_name).write_bytes(ic_bytes)
    (directory / observations_name).write_bytes(observations_bytes)
    report = {
        "schema_version": 1,
        "research_sha256": research_sha,
        "research_status": "cross_sectional_rank_ic_evidence_only",
        "panel": {
            "panel_id": "three_1h_v1",
            "panel_sha256": "d" * 64,
            "alignment": "inner_exact",
            "timeframe": "1h",
            "symbols": [item["symbol"] for item in datasets],
            "dataset_ids": [item["dataset_id"] for item in datasets],
            "timestamp_semantics": {"status": "unverified", "note": "fixture"},
            "alignment_summary": {},
            "datasets": datasets,
        },
        "factor_specs": factors,
        "horizons": horizons,
        "required_asset_count": required_assets,
        "policies": policies,
        "summaries": summaries,
        "artifacts": {
            "ic_time_series": {"filename": ic_name, "sha256": ic_sha, "row_count": len(rows)},
            "valid_observations": {
                "filename": observations_name,
                "sha256": observations_sha,
                "row_count": total_valid * required_assets,
            },
        },
        "warnings": [],
        "readiness_changed": False,
        "automatic_factor_approval": False,
    }
    report_path = directory / f"{stem}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return report_path


def _csv_bytes(rows: list[dict[str, object]], fields: tuple[str, ...]) -> bytes:
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _read_csv(path: str) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
