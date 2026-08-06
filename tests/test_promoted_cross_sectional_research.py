import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.promoted_cross_sectional_research as module
from crypto_bot.cross_sectional_factor_research import CrossSectionalResearchResult
from crypto_bot.cross_sectional_ic_calibration import CrossSectionalICCalibrationResult
from crypto_bot.errors import MarketDataError
from crypto_bot.market.dataset_promotion import ValidatedDatasetPromotion
from crypto_bot.promoted_cross_sectional_research import (
    analyze_promoted_cross_sectional_evidence,
    format_promoted_cross_sectional_evidence,
)


def test_promoted_chain_is_marker_driven_deterministic_and_commits_chain_last(tmp_path, monkeypatch):
    promotion = _promotion(tmp_path)
    monkeypatch.setattr(module, "validate_dataset_promotion_report", lambda path: promotion)
    monkeypatch.setattr(module, "run_cross_sectional_factor_research", _fake_research)
    monkeypatch.setattr(module, "run_cross_sectional_ic_calibration", _fake_calibration)
    config = Path(__file__).parents[1] / "config.cross-sectional-factor-research.example.yaml"

    first = analyze_promoted_cross_sectional_evidence("marker.json", config, tmp_path / "first")
    second = analyze_promoted_cross_sectional_evidence("marker.json", config, tmp_path / "second")

    assert first.report == second.report
    assert first.report["research"]["candidate_timestamp_count"] == 9486
    assert first.report["calibration"]["hypothesis_count"] == 18
    assert first.report["oos_analysis_included"] is False
    assert first.report["readiness_changed"] is False
    assert first.report["promotion"]["timestamp_semantics"]["aggregate_status"] == "mixed_unverified"
    assert len(first.report["promotion"]["datasets"]) == 6
    assert all(Path(path).is_file() for path in first.export_paths.values())
    assert {Path(path).name for path in first.export_paths.values()} == {
        Path(path).name for path in second.export_paths.values()
    }
    assert "oos_analysis_included: false" in format_promoted_cross_sectional_evidence(first)

    replay = analyze_promoted_cross_sectional_evidence("marker.json", config, tmp_path / "first")
    assert replay.report == first.report


def test_promoted_chain_rejects_non_fixed_research_config(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "validate_dataset_promotion_report", lambda path: _promotion(tmp_path))
    config = tmp_path / "research.yaml"
    config.write_text(
        "schema_version: 1\nfactors:\n  - family: momentum\n    window: 4\nhorizons: [1, 4, 16]\n",
        encoding="utf-8",
    )

    with pytest.raises(MarketDataError, match="config_not_fixed"):
        analyze_promoted_cross_sectional_evidence("marker.json", config, tmp_path / "output")


def test_promoted_chain_does_not_publish_marker_when_calibration_fails(tmp_path, monkeypatch):
    promotion = _promotion(tmp_path)
    monkeypatch.setattr(module, "validate_dataset_promotion_report", lambda path: promotion)
    monkeypatch.setattr(module, "run_cross_sectional_factor_research", _fake_research)

    def fail(*args):
        raise MarketDataError("calibration_failed")

    monkeypatch.setattr(module, "run_cross_sectional_ic_calibration", fail)
    config = Path(__file__).parents[1] / "config.cross-sectional-factor-research.example.yaml"
    output = tmp_path / "output"

    with pytest.raises(MarketDataError, match="calibration_failed"):
        analyze_promoted_cross_sectional_evidence("marker.json", config, output)

    assert not list(output.glob("promoted-cross-sectional-evidence.*.json"))
    assert not list(output.glob("promoted-cross-sectional-research.*"))


def test_promoted_chain_cli_exposes_only_marker_config_and_output(monkeypatch, capsys):
    fake = SimpleNamespace(
        report={
            "evidence_status": "promotion_aware_cross_sectional_rank_ic_hac_holm_evidence_only",
            "chain_sha256": "a" * 64,
            "promotion": {"promotion_sha256": "b" * 64},
            "research": {"research_sha256": "c" * 64, "candidate_timestamp_count": 9486},
            "calibration": {"calibration_sha256": "d" * 64, "hypothesis_count": 18},
        },
        export_paths={"chain": "chain.json"},
    )
    monkeypatch.setattr(cli_module, "analyze_promoted_cross_sectional_evidence", lambda *args: fake)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto-bot",
            "analyze-promoted-cross-sectional-evidence",
            "--promotion-report",
            "marker.json",
        ],
    )

    with pytest.raises(SystemExit, match="0"):
        cli_module.main()

    output = capsys.readouterr().out
    assert "candidate_timestamp_count: 9486" in output
    assert "exported_chain: chain.json" in output


def test_promoted_chain_fail_closed_shape_type_and_collision_guards(tmp_path):
    research = _fake_research(None, None, None, None, tmp_path / "research", promotion_context={"x": 1})
    calibration = _fake_calibration(None, tmp_path / "calibration")
    research_path = Path(research.export_paths["report"])
    calibration_path = Path(calibration.export_paths["report"])

    bad_shape = CrossSectionalResearchResult(
        report=deepcopy(research.report) | {"summaries": research.report["summaries"][:-1]},
        export_paths=research.export_paths,
    )
    with pytest.raises(MarketDataError, match="research_shape"):
        module._chain_identity({}, bad_shape, research_path, calibration, calibration_path)

    bad_assets = CrossSectionalResearchResult(
        report=deepcopy(research.report) | {"required_asset_count": 5},
        export_paths=research.export_paths,
    )
    with pytest.raises(MarketDataError, match="required_asset_count"):
        module._chain_identity({}, bad_assets, research_path, calibration, calibration_path)

    bad_calibration = CrossSectionalICCalibrationResult(
        report=deepcopy(calibration.report) | {"hypothesis_count": 17},
        export_paths=calibration.export_paths,
    )
    with pytest.raises(MarketDataError, match="calibration_shape"):
        module._chain_identity({}, research, research_path, bad_calibration, calibration_path)

    with pytest.raises(MarketDataError, match="invalid_mapping"):
        module._mapping([], "mapping")
    with pytest.raises(MarketDataError, match="invalid_items"):
        module._list_of_mappings([{} , "bad"], "items")

    source = _write(tmp_path / "source", b"source")
    destination = _write(tmp_path / "destination", b"different")
    with pytest.raises(MarketDataError, match="content_addressed_collision"):
        module._commit_staged_file(source, destination)


def _promotion(tmp_path: Path) -> ValidatedDatasetPromotion:
    marker = _write(tmp_path / "okx-universe-promotion.marker.json", b"marker")
    registry = _write(tmp_path / "registry.yaml", b"registry")
    panels = _write(tmp_path / "panels.yaml", b"panels")
    registry_audit = _write(tmp_path / "registry-audit.json", b"registry-audit")
    panel_audit = _write(tmp_path / "panel-audit.json", b"panel-audit")
    candidate_ids = ["knc", "swftc", "bico"]
    components = [
        {
            "dataset_id": dataset_id,
            "raw_sha256": _digest(f"raw-{dataset_id}".encode()),
            "canonical_sha256": _digest(f"canonical-{dataset_id}".encode()),
        }
        for dataset_id in ["btc", "eth", "sol", *candidate_ids]
    ]
    datasets = [
        {
            "dataset_id": dataset_id,
            "lineage_sha256": _digest(f"lineage-{dataset_id}".encode()),
            "destination_raw_sha256": _digest(f"raw-{dataset_id}".encode()),
            "destination_canonical_sha256": _digest(f"canonical-{dataset_id}".encode()),
        }
        for dataset_id in candidate_ids
    ]
    report = {
        "promotion_sha256": "1" * 64,
        "identity": {
            "promotion_policy_version": "policy-v1",
            "promotion_id": "promotion-v1",
            "promoted_registry": {
                "repo_relative_path": "registry.yaml",
                "sha256": _sha(registry),
            },
            "promoted_panel": {
                "config_repo_relative_path": "panels.yaml",
                "config_sha256": _sha(panels),
                "panel_sha256": "2" * 64,
                "components": components,
            },
            "datasets": datasets,
            "timestamp_semantics": {
                "aggregate_status": "mixed_unverified",
                "generic_panel_audit_status": "unverified",
                "timestamp_semantics_uniform": False,
                "old_datasets_upgraded": False,
            },
            "claims": {
                "profitability_evidence": False,
                "strategy_approval": False,
                "historical_point_in_time_membership": False,
                "survivorship_bias_resolved": False,
            },
        },
    }
    return ValidatedDatasetPromotion(
        report_path=marker,
        report=report,
        repo_root=tmp_path,
        registry_path=registry,
        panels_config_path=panels,
        registry_audit_path=registry_audit,
        panel_audit_path=panel_audit,
    )


def _fake_research(*args, **kwargs) -> CrossSectionalResearchResult:
    output = Path(args[4])
    promotion = kwargs["promotion_context"]
    research_sha = "3" * 64
    stem = f"promoted-cross-sectional-research.{research_sha}"
    ic = _write(output / f"{stem}.ic.csv", b"ic")
    observations = _write(output / f"{stem}.observations.csv", b"observations")
    summaries = [
        {"factor_name": f"factor-{index // 3}", "horizon_bars": (1, 4, 16)[index % 3], "candidate_timestamp_count": 9486}
        for index in range(18)
    ]
    report = {
        "research_sha256": research_sha,
        "required_asset_count": 6,
        "promotion": promotion,
        "factor_specs": [{"name": f"factor-{index}"} for index in range(6)],
        "horizons": [1, 4, 16],
        "policies": {"rank": "ascending_average_ties"},
        "summaries": summaries,
        "artifacts": {
            "ic_time_series": {"filename": ic.name, "sha256": _sha(ic), "row_count": 170748},
            "valid_observations": {
                "filename": observations.name,
                "sha256": _sha(observations),
                "row_count": 100,
            },
        },
    }
    report_path = output / f"{stem}.json"
    report_path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
    return CrossSectionalResearchResult(
        report=report,
        export_paths={
            "report": str(report_path),
            "ic_time_series": str(ic),
            "valid_observations": str(observations),
        },
    )


def _fake_calibration(source, output) -> CrossSectionalICCalibrationResult:
    output = Path(output)
    calibration_sha = "4" * 64
    stem = f"promoted-cross-sectional-ic-calibration.{calibration_sha}"
    hypotheses = _write(output / f"{stem}.hypotheses.csv", b"hypotheses")
    report = {
        "calibration_sha256": calibration_sha,
        "hypothesis_count": 18,
        "policies": {"multiple_testing": "holm_step_down_v1"},
        "artifacts": {
            "hypotheses": {
                "filename": hypotheses.name,
                "sha256": _sha(hypotheses),
                "row_count": 18,
            }
        },
    }
    report_path = output / f"{stem}.json"
    report_path.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
    return CrossSectionalICCalibrationResult(
        report=report,
        export_paths={"report": str(report_path), "hypotheses": str(hypotheses)},
    )


def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
