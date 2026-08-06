import csv
import hashlib
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from crypto_bot.cross_sectional_factor_research import (
    evaluate_cross_sectional_slice,
    format_cross_sectional_factor_research,
    load_cross_sectional_research_config,
    run_cross_sectional_factor_research,
)
from crypto_bot.errors import MarketDataError


def test_cross_sectional_slice_known_correlations_and_average_ties():
    positive = evaluate_cross_sectional_slice([1, 2, 3], [10, 20, 30], required_asset_count=3)
    negative = evaluate_cross_sectional_slice([1, 2, 3], [30, 20, 10], required_asset_count=3)
    tied = evaluate_cross_sectional_slice([1, 1, 3], [10, 20, 30], required_asset_count=3)

    assert positive.status == negative.status == tied.status == "valid"
    assert positive.rank_ic == 1.0
    assert negative.rank_ic == -1.0
    assert tied.factor_ranks == (1.5, 1.5, 3.0)
    assert tied.rank_ic == pytest.approx(0.866025403784)


@pytest.mark.parametrize(
    ("factors", "returns", "status"),
    [
        ([1, np.nan, 3], [1, 2, 3], "insufficient_assets"),
        ([1, 1, 1], [1, 2, 3], "constant_factor"),
        ([1, 2, 3], [1, 1, 1], "constant_forward_return"),
        ([1, 1, 1], [2, 2, 2], "constant_both"),
    ],
)
def test_cross_sectional_slice_explicit_skip_statuses(factors, returns, status):
    result = evaluate_cross_sectional_slice(factors, returns, required_asset_count=3)

    assert result.status == status
    assert result.rank_ic is None


def test_cross_sectional_slice_rejects_mismatched_shapes():
    with pytest.raises(ValueError, match="equal-length"):
        evaluate_cross_sectional_slice([1, 2], [1], required_asset_count=2)


def test_research_config_is_canonical_and_deduplicates_horizons(tmp_path):
    path = _write_research_config(
        tmp_path / "research.yaml",
        factors=[
            {"family": "mean_reversion", "window": 3},
            {"family": "momentum", "window": 2},
        ],
        horizons=[4, 1, 4],
    )

    config = load_cross_sectional_research_config(path)

    assert [spec.name for spec in config.specs] == ["mean_reversion_3", "momentum_2"]
    assert config.horizons == (1, 4)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([], "must be a mapping"),
        ({"schema_version": 2, "factors": [], "horizons": []}, "schema_version"),
        ({"schema_version": 1, "factors": [], "horizons": [1]}, "factors"),
        (
            {"schema_version": 1, "factors": [{"family": "unknown", "window": 2}], "horizons": [1]},
            "unsupported factor family",
        ),
        (
            {"schema_version": 1, "factors": [{"family": "momentum", "window": 1}], "horizons": [1]},
            "window",
        ),
        (
            {
                "schema_version": 1,
                "factors": [
                    {"family": "momentum", "window": 2},
                    {"family": "momentum", "window": 2},
                ],
                "horizons": [1],
            },
            "must be unique",
        ),
        (
            {"schema_version": 1, "factors": [{"family": "momentum", "window": 2}], "horizons": [0]},
            "positive integers",
        ),
    ],
)
def test_research_config_rejects_invalid_payloads(tmp_path, payload, expected):
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        load_cross_sectional_research_config(path)


def test_research_config_rejects_missing_and_invalid_yaml(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        load_cross_sectional_research_config(tmp_path / "missing.yaml")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("factors: [", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML is invalid"):
        load_cross_sectional_research_config(invalid)


def test_cross_sectional_research_exports_recomputable_deterministic_evidence(tmp_path):
    registry, panels = _write_project_inputs(tmp_path, periods=30)
    research = _write_research_config(
        tmp_path / "research.yaml",
        factors=[{"family": "momentum", "window": 2}],
        horizons=[1],
    )

    first = run_cross_sectional_factor_research(registry, panels, "three_1h_v1", research, tmp_path / "first")
    second = run_cross_sectional_factor_research(registry, panels, "three_1h_v1", research, tmp_path / "second")

    assert first.report["research_sha256"] == second.report["research_sha256"]
    assert first.report["research_status"] == "cross_sectional_rank_ic_evidence_only"
    assert first.report["readiness_changed"] is False
    assert first.report["automatic_factor_approval"] is False
    assert first.report["panel"]["timestamp_semantics"]["status"] == "unverified"
    assert first.report["warnings"][1] == "three_asset_cross_section_has_limited_statistical_power"
    assert {Path(path).name for path in first.export_paths.values()} == {
        Path(path).name for path in second.export_paths.values()
    }
    for name in first.export_paths:
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()

    summary = first.report["summaries"][0]
    assert summary["candidate_timestamp_count"] == 30
    assert summary["valid_ic_timestamp_count"] > 0
    assert summary["insufficient_assets_count"] >= 3
    assert sum(
        summary[key]
        for key in (
            "valid_ic_timestamp_count",
            "insufficient_assets_count",
            "constant_both_count",
            "constant_factor_count",
            "constant_forward_return_count",
            "undefined_correlation_count",
        )
    ) == summary["candidate_timestamp_count"]
    assert -1 <= summary["mean_rank_ic"] <= 1

    ic_rows = _read_csv(first.export_paths["ic_time_series"])
    observation_rows = _read_csv(first.export_paths["valid_observations"])
    valid_ic_rows = [row for row in ic_rows if row["status"] == "valid"]
    assert len(valid_ic_rows) == summary["valid_ic_timestamp_count"]
    assert len(observation_rows) == 3 * len(valid_ic_rows)
    sample = valid_ic_rows[0]
    sample_observations = [
        row
        for row in observation_rows
        if row["factor_name"] == sample["factor_name"]
        and row["horizon_bars"] == sample["horizon_bars"]
        and row["timestamp"] == sample["timestamp"]
    ]
    recomputed = np.corrcoef(
        [float(row["factor_rank"]) for row in sample_observations],
        [float(row["forward_return_rank"]) for row in sample_observations],
    )[0, 1]
    assert float(sample["rank_ic"]) == pytest.approx(recomputed)

    for artifact_name, artifact in first.report["artifacts"].items():
        path = Path(first.export_paths[artifact_name])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]


def test_factor_and_horizon_order_do_not_change_research_identity(tmp_path):
    registry, panels = _write_project_inputs(tmp_path, periods=20)
    first_config = _write_research_config(
        tmp_path / "first.yaml",
        factors=[
            {"family": "momentum", "window": 2},
            {"family": "mean_reversion", "window": 3},
        ],
        horizons=[2, 1],
    )
    second_config = _write_research_config(
        tmp_path / "second.yaml",
        factors=[
            {"family": "mean_reversion", "window": 3},
            {"family": "momentum", "window": 2},
        ],
        horizons=[1, 2],
    )

    first = run_cross_sectional_factor_research(registry, panels, "three_1h_v1", first_config, tmp_path / "a")
    second = run_cross_sectional_factor_research(registry, panels, "three_1h_v1", second_config, tmp_path / "b")

    assert first.report["research_sha256"] == second.report["research_sha256"]
    for name in first.export_paths:
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()


def test_content_addressed_collision_fails_closed(tmp_path):
    registry, panels = _write_project_inputs(tmp_path, periods=12)
    research = _write_research_config(
        tmp_path / "research.yaml",
        factors=[{"family": "momentum", "window": 2}],
        horizons=[1],
    )
    output = tmp_path / "output"
    first = run_cross_sectional_factor_research(registry, panels, "three_1h_v1", research, output)
    Path(first.export_paths["ic_time_series"]).write_text("corrupted", encoding="utf-8")

    with pytest.raises(MarketDataError, match="content_addressed_artifact_collision"):
        run_cross_sectional_factor_research(registry, panels, "three_1h_v1", research, output)


def test_cli_runs_independent_cross_sectional_command(tmp_path):
    registry, panels = _write_project_inputs(tmp_path, periods=15)
    research = _write_research_config(
        tmp_path / "research.yaml",
        factors=[{"family": "momentum", "window": 2}],
        horizons=[1],
    )
    output = tmp_path / "cli-output"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "analyze-cross-sectional-factors",
            "--registry",
            str(registry),
            "--panels-config",
            str(panels),
            "--panel-id",
            "three_1h_v1",
            "--research-config",
            str(research),
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
    assert "research_status: cross_sectional_rank_ic_evidence_only" in completed.stdout
    assert "readiness_changed: false" in completed.stdout
    assert len(list(output.glob("cross-sectional-factor-research.*"))) == 3


def test_runner_propagates_panel_failure(tmp_path):
    registry, panels = _write_project_inputs(tmp_path, periods=8)
    research = _write_research_config(
        tmp_path / "research.yaml",
        factors=[{"family": "momentum", "window": 2}],
        horizons=[1],
    )

    with pytest.raises(ValueError, match="panel_id not found"):
        run_cross_sectional_factor_research(registry, panels, "missing", research, tmp_path / "output")


def test_format_cross_sectional_report_contains_identity(tmp_path):
    registry, panels = _write_project_inputs(tmp_path, periods=8)
    research = _write_research_config(
        tmp_path / "research.yaml",
        factors=[{"family": "momentum", "window": 2}],
        horizons=[1],
    )
    result = run_cross_sectional_factor_research(registry, panels, "three_1h_v1", research, tmp_path / "output")

    formatted = format_cross_sectional_factor_research(result)

    assert f"research_sha256: {result.report['research_sha256']}" in formatted
    assert "panel_id: three_1h_v1" in formatted
    assert "summary_count: 1" in formatted


def _write_project_inputs(tmp_path: Path, *, periods: int) -> tuple[Path, Path]:
    timestamps = pd.date_range("2026-01-01", periods=periods, freq="1h", tz="UTC")
    definitions = [
        ("btc_v1", "BTC/USDT", "btc.csv", 100.0, 2.0, 3),
        ("eth_v1", "ETH/USDT", "eth.csv", 200.0, 1.1, 4),
        ("sol_v1", "SOL/USDT", "sol.csv", 300.0, 0.6, 5),
    ]
    entries = []
    for dataset_id, symbol, filename, base, slope, cycle in definitions:
        index = np.arange(periods, dtype=float)
        close = base + slope * index + (index % cycle) * 0.17
        frame = pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": base * 10 + index * 3 + (index % cycle),
            }
        )
        frame.to_csv(tmp_path / filename, index=False)
        entries.append(
            {
                "dataset_id": dataset_id,
                "symbol": symbol,
                "timeframe": "1h",
                "path": filename,
                "source": {"status": "unknown", "evidence": []},
                "quality": {"validator": "validate_ohlcv_csv"},
            }
        )
    registry = tmp_path / "datasets.yaml"
    registry.write_text(
        yaml.safe_dump({"schema_version": 1, "datasets": entries}, sort_keys=False),
        encoding="utf-8",
    )
    panels = tmp_path / "panels.yaml"
    panels.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "panels": [
                    {
                        "panel_id": "three_1h_v1",
                        "dataset_ids": ["sol_v1", "btc_v1", "eth_v1"],
                        "alignment": "inner_exact",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return registry, panels


def _write_research_config(path: Path, *, factors: list[dict], horizons: list[int]) -> Path:
    path.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "factors": factors, "horizons": horizons},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _read_csv(path: str) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
