import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import crypto_bot.promoted_cross_sectional_oos_evidence as evidence_module
import crypto_bot.cli as cli_module
from crypto_bot.cross_sectional_factor_research import CrossSectionalResearchResult
from crypto_bot.cross_sectional_ic_calibration import CrossSectionalICCalibrationResult
from crypto_bot.cross_sectional_oos_stability import CrossSectionalOOSStabilityResult
from crypto_bot.errors import MarketDataError
from crypto_bot.factors import DEFAULT_FACTOR_SPECS
from crypto_bot.promoted_cross_sectional_oos_evidence import (
    EXPECTED_FOLD_COUNTS,
    EXPECTED_HORIZONS,
    EXPECTED_POOLED_COUNTS,
    _load_fixed_config,
    _promotion_context,
    analyze_promoted_cross_sectional_oos_evidence,
    format_promoted_cross_sectional_oos_evidence,
)


CONFIG = Path(__file__).resolve().parents[1] / "config.promoted-cross-sectional-1h-oos.example.yaml"


def test_fixed_config_freezes_factors_mapping_and_policy(tmp_path):
    config = _load_fixed_config(CONFIG)

    assert config["horizon_mapping"] == list(evidence_module.EXPECTED_HORIZON_MAPPING)
    assert [item["target_horizon_bars"] for item in config["horizon_mapping"]] == [4, 16, 64]
    assert config["factors"] == [asdict(spec) for spec in DEFAULT_FACTOR_SPECS]

    invalid_cases = [
        config | {"extra": True},
        config | {"schema_version": 2},
        config | {"factors": config["factors"][:-1]},
        config | {"horizon_mapping": config["horizon_mapping"][:-1]},
        config | {"oos_policy_id": "changed"},
    ]
    for index, invalid in enumerate(invalid_cases):
        path = tmp_path / f"invalid-{index}.yaml"
        path.write_text(yaml.safe_dump(invalid), encoding="utf-8")
        with pytest.raises((ValueError, MarketDataError)):
            _load_fixed_config(path)

    non_mapping = tmp_path / "non-mapping.yaml"
    non_mapping.write_text("- invalid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        _load_fixed_config(non_mapping)


def test_promotion_context_binds_all_components_lineage_and_design(tmp_path, monkeypatch):
    report_path = tmp_path / "promotion.json"
    report_path.write_text("{}", encoding="utf-8")
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text("registry", encoding="utf-8")
    panels_path = tmp_path / "panels.yaml"
    panels_path.write_text("panels", encoding="utf-8")
    claims = {
        "historical_point_in_time_membership": False,
        "survivorship_bias_resolved": False,
        "profitability_evidence": False,
        "strategy_approval": False,
    }
    semantics = {
        "generic_panel_audit_status": "unverified",
        "aggregate_status": "mixed_unverified",
        "timestamp_semantics_uniform": False,
    }
    report = {
        "promotion_sha256": "promotion",
        "identity": {
            "capture_sha256": "capture",
            "capture_report_sha256": "capture-report",
            "promoted_registry": {
                "filename": "registry.yaml",
                "sha256": "registry",
                "audit_filename": "registry-audit.json",
                "audit_sha256": "registry-audit",
            },
            "promoted_panel": {
                "config_filename": "panels.yaml",
                "config_sha256": "panel-config",
                "panel_sha256": "panel",
                "audit_filename": "panel-audit.json",
                "audit_sha256": "panel-audit",
                "alignment_summary": {"intersection_bar_count": 20424},
            },
            "policy": {
                "target_panel_id": evidence_module.EXPECTED_PANEL_ID,
                "timeframe": "1h",
                "expected_panel": {"intersection_bar_count": 20424},
            },
            "timestamp_semantics": semantics,
            "claims": claims,
            "datasets": [
                {
                    "dataset_id": evidence_module.EXPECTED_DATASET_IDS[-1],
                    "lineage_sha256": "lineage",
                    "destination_repo_relative_path": "data/new.csv",
                }
            ],
        },
    }
    panel_datasets = [
        {
            "dataset_id": dataset_id,
            "symbol": dataset_id,
            "raw_sha256": character * 64,
            "canonical_sha256": character * 64,
        }
        for dataset_id, character in zip(evidence_module.EXPECTED_DATASET_IDS, "abcdef")
    ]
    panel_report = {
        "dataset_ids": list(evidence_module.EXPECTED_DATASET_IDS),
        "panel_sha256": "panel",
        "timeframe": "1h",
        "alignment_summary": {"intersection_bar_count": 20424},
        "datasets": panel_datasets,
    }
    promotion = SimpleNamespace(
        report=report,
        report_path=report_path,
        registry_path=registry_path,
        panels_config_path=panels_path,
    )
    monkeypatch.setattr(evidence_module, "EXPECTED_PROMOTION_SHA256", "promotion")
    monkeypatch.setattr(
        evidence_module,
        "EXPECTED_PROMOTION_REPORT_SHA256",
        hashlib.sha256(report_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(evidence_module, "EXPECTED_REGISTRY_SHA256", "registry")
    monkeypatch.setattr(evidence_module, "EXPECTED_PANEL_CONFIG_SHA256", "panel-config")
    monkeypatch.setattr(evidence_module, "EXPECTED_PANEL_SHA256", "panel")
    monkeypatch.setattr(
        evidence_module,
        "build_dataset_panel",
        lambda *_args: SimpleNamespace(report=panel_report),
    )

    context = _promotion_context(promotion, CONFIG, _load_fixed_config(CONFIG))

    assert context["promotion_sha256"] == "promotion"
    assert len(context["datasets"]) == 6
    assert context["datasets"][-1]["lineage_sha256"] == "lineage"
    assert context["timestamp_semantics"] == semantics
    assert context["design"]["target_horizons"] == [4, 16, 64]
    assert context["design"]["minimum_oos_valid_count"] == 500
    assert context["design"]["design_frozen_before_six_asset_1h_result_generation"] is True


def test_evidence_chain_is_deterministic_and_commits_marker_last(tmp_path, monkeypatch):
    promotion_context = {"promotion_sha256": "p" * 64, "design": {"frozen": True}}
    _install_fake_pipeline(monkeypatch, promotion_context)

    first = analyze_promoted_cross_sectional_oos_evidence(
        tmp_path / "promotion.json",
        CONFIG,
        tmp_path / "first",
    )
    second = analyze_promoted_cross_sectional_oos_evidence(
        tmp_path / "promotion.json",
        CONFIG,
        tmp_path / "second",
    )

    assert first.report == second.report
    assert first.report["evidence_status"].endswith("evidence_only")
    assert first.report["profitability_evidence"] is False
    assert first.report["oos_analysis_included"] is True
    assert len(first.export_paths) == 9
    for name in first.export_paths:
        assert Path(first.export_paths[name]).name == Path(second.export_paths[name]).name
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()
    assert Path(first.export_paths["chain"]).name.endswith(f"{first.report['chain_sha256']}.json")
    formatted = format_promoted_cross_sectional_oos_evidence(first)
    assert "oos_analysis_included: true" in formatted
    assert "profitability_evidence: false" in formatted

    failed_output = tmp_path / "failed"
    original_commit = evidence_module._commit_staged_file
    commit_count = 0

    def fail_before_marker(source, destination):
        nonlocal commit_count
        commit_count += 1
        if commit_count == 8:
            raise MarketDataError("injected_commit_failure")
        original_commit(source, destination)

    monkeypatch.setattr(evidence_module, "_commit_staged_file", fail_before_marker)
    with pytest.raises(MarketDataError, match="injected_commit_failure"):
        analyze_promoted_cross_sectional_oos_evidence(
            tmp_path / "promotion.json",
            CONFIG,
            failed_output,
        )
    assert not list(failed_output.glob("promoted-cross-sectional-1h-oos-chain.*.json"))


def test_public_loader_replays_complete_chain_and_rejects_artifact_drift(tmp_path, monkeypatch):
    promotion_context = {
        "promotion_sha256": "p" * 64,
        "design": {"config_filename": CONFIG.name, "frozen": True},
    }
    _install_fake_pipeline(monkeypatch, promotion_context)
    result = analyze_promoted_cross_sectional_oos_evidence(
        tmp_path / "promotion.json",
        CONFIG,
        tmp_path / "evidence",
    )
    promotion_marker = tmp_path / "promotion-marker.json"
    promotion_marker.write_text("promotion", encoding="utf-8")
    promotion = SimpleNamespace(
        repo_root=CONFIG.parent,
        registry_path="registry",
        panels_config_path="panels",
    )
    monkeypatch.setattr(evidence_module, "_locate_repo_dependency", lambda *_args: promotion_marker)
    monkeypatch.setattr(
        evidence_module,
        "validate_okx_frozen_universe_1h_promotion",
        lambda _path: promotion,
    )
    monkeypatch.setattr(evidence_module, "_promotion_context", lambda *_args: promotion_context)
    monkeypatch.setattr(
        evidence_module,
        "load_validated_cross_sectional_ic_evidence",
        lambda _path: SimpleNamespace(source_report=result.research.report),
    )

    validated = evidence_module.validate_promoted_cross_sectional_oos_evidence(
        result.export_paths["chain"]
    )

    assert validated.report["chain_sha256"] == result.report["chain_sha256"]
    assert len(validated.artifact_paths) == 5
    Path(result.export_paths["research_ic_time_series"]).write_text("drift", encoding="utf-8")
    with pytest.raises(MarketDataError, match="artifact_hash_mismatch"):
        evidence_module.validate_promoted_cross_sectional_oos_evidence(
            result.export_paths["chain"]
        )


def test_shape_validator_rejects_fold_and_observation_drift(monkeypatch, tmp_path):
    promotion_context = {"promotion_sha256": "p" * 64, "design": {"frozen": True}}
    fakes = _install_fake_pipeline(monkeypatch, promotion_context)
    research = fakes["research"](None, None, None, None, tmp_path, promotion_context=promotion_context)
    calibration = fakes["calibration"](None, tmp_path)
    oos = fakes["oos"](None, tmp_path)

    research.report["artifacts"]["valid_observations"]["row_count"] += 1
    with pytest.raises(MarketDataError, match="observation_count"):
        evidence_module._validate_result_shapes(
            research.report,
            calibration.report,
            oos.report,
            promotion_context,
        )
    research.report["artifacts"]["valid_observations"]["row_count"] -= 1
    oos.report["folds"][0]["oos_valid_count"] -= 1
    with pytest.raises(MarketDataError, match="fold_count"):
        evidence_module._validate_result_shapes(
            research.report,
            calibration.report,
            oos.report,
            promotion_context,
        )


def test_cli_exposes_only_marker_config_and_output(monkeypatch, tmp_path, capsys):
    result = SimpleNamespace(export_paths={"chain": str(tmp_path / "chain.json")})
    monkeypatch.setattr(cli_module, "analyze_promoted_cross_sectional_oos_evidence", lambda *_args: result)
    monkeypatch.setattr(cli_module, "format_promoted_cross_sectional_oos_evidence", lambda _result: "ok")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto-bot",
            "analyze-promoted-cross-sectional-oos-evidence",
            "--promotion-report",
            "marker.json",
            "--research-config",
            str(CONFIG),
            "--output-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as exited:
        cli_module.main()

    assert exited.value.code == 0
    assert "ok" in capsys.readouterr().out


def _install_fake_pipeline(monkeypatch, promotion_context):
    factor_specs = [asdict(spec) | {"name": spec.name} for spec in sorted(DEFAULT_FACTOR_SPECS, key=lambda x: x.name)]
    factor_names = [item["name"] for item in factor_specs]

    def fake_research(_registry, _panels, _panel_id, _config, output, *, promotion_context):
        output = Path(output)
        summaries = [
            {
                "factor_name": factor,
                "horizon_bars": horizon,
                "candidate_timestamp_count": 20424,
                "valid_ic_timestamp_count": 20340,
            }
            for factor in factor_names
            for horizon in EXPECTED_HORIZONS
        ]
        ic = _write(output / "promoted-cross-sectional-research.fake.ic.csv", b"ic\n")
        observations = _write(
            output / "promoted-cross-sectional-research.fake.observations.csv",
            b"observations\n",
        )
        report = {
            "research_sha256": "r" * 64,
            "required_asset_count": 6,
            "factor_specs": factor_specs,
            "horizons": list(EXPECTED_HORIZONS),
            "summaries": summaries,
            "policies": {"fixed": True},
            "promotion": promotion_context,
            "artifacts": {
                "ic_time_series": {
                    "filename": ic.name,
                    "sha256": hashlib.sha256(ic.read_bytes()).hexdigest(),
                    "row_count": 367632,
                },
                "valid_observations": {
                    "filename": observations.name,
                    "sha256": hashlib.sha256(observations.read_bytes()).hexdigest(),
                    "row_count": 18 * 20340 * 6,
                },
            },
        }
        report_path = _write(output / "promoted-cross-sectional-research.fake.json", _json_bytes(report))
        return CrossSectionalResearchResult(
            report,
            {
                "report": str(report_path),
                "ic_time_series": str(ic),
                "valid_observations": str(observations),
            },
        )

    def fake_calibration(_source, output):
        output = Path(output)
        hypotheses = _write(output / "promoted-cross-sectional-ic-calibration.fake.csv", b"hac\n")
        report = {
            "calibration_sha256": "c" * 64,
            "hypothesis_count": 18,
            "promotion": promotion_context,
            "policies": {"holm": True},
            "artifacts": {
                "hypotheses": {
                    "filename": hypotheses.name,
                    "sha256": hashlib.sha256(hypotheses.read_bytes()).hexdigest(),
                    "row_count": 18,
                }
            },
        }
        report_path = _write(output / "promoted-cross-sectional-ic-calibration.fake.json", _json_bytes(report))
        return CrossSectionalICCalibrationResult(
            report,
            {"report": str(report_path), "hypotheses": str(hypotheses)},
        )

    def fake_oos(_source, output):
        output = Path(output)
        fold_rows = []
        summary_rows = []
        for factor in factor_names:
            for horizon in EXPECTED_HORIZONS:
                fold_rows.extend(
                    {
                        "factor_name": factor,
                        "horizon_bars": horizon,
                        "fold_index": index,
                        "oos_valid_count": count,
                    }
                    for index, count in enumerate(EXPECTED_FOLD_COUNTS[horizon], start=1)
                )
                summary_rows.append(
                    {
                        "factor_name": factor,
                        "horizon_bars": horizon,
                        "pooled_oos_valid_count": EXPECTED_POOLED_COUNTS[horizon],
                    }
                )
        folds = _write(output / "promoted-cross-sectional-oos-stability.fake.folds.csv", b"folds\n")
        summaries = _write(
            output / "promoted-cross-sectional-oos-stability.fake.summaries.csv",
            b"summaries\n",
        )
        report = {
            "analysis_sha256": "o" * 64,
            "fold_row_count": 180,
            "summary_row_count": 18,
            "folds": fold_rows,
            "summaries": summary_rows,
            "promotion": promotion_context,
            "policies": {"folds": 10},
            "artifacts": {
                "folds": {
                    "filename": folds.name,
                    "sha256": hashlib.sha256(folds.read_bytes()).hexdigest(),
                    "row_count": 180,
                },
                "summaries": {
                    "filename": summaries.name,
                    "sha256": hashlib.sha256(summaries.read_bytes()).hexdigest(),
                    "row_count": 18,
                },
            },
        }
        report_path = _write(output / "promoted-cross-sectional-oos-stability.fake.json", _json_bytes(report))
        return CrossSectionalOOSStabilityResult(
            report,
            {"report": str(report_path), "folds": str(folds), "summaries": str(summaries)},
        )

    monkeypatch.setattr(
        evidence_module,
        "validate_okx_frozen_universe_1h_promotion",
        lambda _path: SimpleNamespace(registry_path="registry", panels_config_path="panels"),
    )
    monkeypatch.setattr(evidence_module, "_promotion_context", lambda *_args: promotion_context)
    monkeypatch.setattr(evidence_module, "run_cross_sectional_factor_research", fake_research)
    monkeypatch.setattr(evidence_module, "run_cross_sectional_ic_calibration", fake_calibration)
    monkeypatch.setattr(evidence_module, "run_cross_sectional_oos_stability", fake_oos)
    return {"research": fake_research, "calibration": fake_calibration, "oos": fake_oos}


def _write(path, content):
    path.write_bytes(content)
    return path


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, indent=2).encode("utf-8")
