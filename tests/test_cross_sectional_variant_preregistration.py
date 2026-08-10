from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.cross_sectional_variant_preregistration as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.cross-sectional-variant-preregistration.example.yaml"
MECHANISM = next((ROOT / "reports" / "cross-sectional-portfolio-mechanism").glob("*.json"))
MECHANISM_VARIANTS = next((ROOT / "reports" / "cross-sectional-portfolio-mechanism").glob("*.variants.csv"))
MAPPING = next((ROOT / "reports" / "okx-direct-six-1h-execution-mapping").glob("*.json"))


def test_config_is_frozen_and_loads_expected_family():
    config = module.load_variant_preregistration_config(CONFIG, ROOT)
    assert config["family_size"] == 36
    assert config["future_multiple_testing_method"] == "holm"
    assert config["factor_names"] == [
        "low_volatility_20", "mean_reversion_20", "momentum_12",
        "momentum_4", "trend_quality_20", "volume_momentum_20",
    ]


def test_config_rejects_wrong_name_and_content(tmp_path):
    wrong_name = tmp_path / "wrong.yaml"
    wrong_name.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="filename"):
        module.load_variant_preregistration_config(wrong_name)
    wrong = tmp_path / CONFIG.name
    wrong.write_text("schema_version: 9\n", encoding="utf-8")
    with pytest.raises(ValueError, match="equal"):
        module.load_variant_preregistration_config(wrong)


def test_variant_rows_replay_exact_36_family_and_reject_tamper():
    config = module.load_variant_preregistration_config(CONFIG, ROOT)
    report = json.loads(MECHANISM.read_text(encoding="utf-8"))
    fake = SimpleNamespace(artifact_paths={"variants": MECHANISM_VARIANTS}, report=report)
    rows = module._variant_rows(fake, config)
    assert len(rows) == 36
    assert {row["rank_direction"] for row in rows} == set(config["rank_directions"])
    assert {row["selection_prohibited"] for row in rows} == {True}
    assert [row["reporting_order"] for row in rows] == list(range(1, 37))
    tampered = MECHANISM_VARIANTS.with_suffix(".tampered.csv")
    tampered.write_text(MECHANISM_VARIANTS.read_text(encoding="utf-8").replace(",true\n", ",false\n", 1), encoding="utf-8")
    try:
        with pytest.raises(MarketDataError, match="family_mismatch"):
            module._variant_rows(SimpleNamespace(artifact_paths={"variants": tampered}), config)
    finally:
        tampered.unlink()


def test_mapping_pin_is_content_addressed_and_rejects_wrong_path():
    config = module.load_variant_preregistration_config(CONFIG, ROOT)
    report = module._validate_mapping_pin(MAPPING, config, ROOT)
    assert report["feasibility"]["pnl_computation_authorized"] is False
    with pytest.raises(MarketDataError, match="mapping_pin"):
        module._validate_mapping_pin(ROOT / "missing.json", config, ROOT)


def test_freeze_success_is_deterministic_and_has_no_pnl_authorization(monkeypatch, tmp_path):
    mechanism_report = json.loads(MECHANISM.read_text(encoding="utf-8"))
    mapping_report = json.loads(MAPPING.read_text(encoding="utf-8"))
    fake_mechanism = SimpleNamespace(
        report=mechanism_report,
        artifact_paths={"variants": MECHANISM_VARIANTS},
    )
    monkeypatch.setattr(module, "validate_cross_sectional_portfolio_mechanism", lambda _: fake_mechanism)
    monkeypatch.setattr(module, "_validate_mechanism_pin", lambda *_args: None)
    monkeypatch.setattr(module, "_validate_mapping_pin", lambda *_args: mapping_report)
    output = tmp_path / "reports" / "prereg"
    output.mkdir(parents=True)
    monkeypatch.setattr(module, "_reports_output", lambda *_args: output)
    result = module.freeze_cross_sectional_variant_preregistration(
        MECHANISM, MAPPING, CONFIG, output
    )
    assert result.report["family_size"] == 36
    assert result.report["selection_prohibited"] is True
    assert result.report["feasibility"]["pnl_computation_authorized"] is False
    assert "future_multiple_testing_method: holm" in module.format_variant_preregistration(result)
    second = module.freeze_cross_sectional_variant_preregistration(
        MECHANISM, MAPPING, CONFIG, output
    )
    assert second.report == result.report


def test_output_path_must_stay_inside_reports(tmp_path):
    with pytest.raises(ValueError, match="inside reports"):
        module._reports_output(ROOT, tmp_path)
