from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import crypto_bot.market.execution_cost_evidence as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.execution-cost-evidence.example.yaml"
FEE = ROOT / "docs" / "evidence" / "okx_spot_fee_policy_assumption_v1.json"
PREREG = next((ROOT / "reports" / "cross-sectional-variant-preregistration").glob("*.json"))
MAPPING = next((ROOT / "reports" / "okx-direct-six-1h-execution-mapping").glob("*.json"))
MIGRATION = next((ROOT / "reports" / "okx-direct-six-asset-1h-migration").glob("*.json"))


def test_config_and_fee_evidence_are_frozen():
    config = module.load_execution_cost_evidence_config(CONFIG, ROOT)
    assert config["capacity_volume_field"] == "volume"
    assert config["taker_fee_bps"] == 100
    assert module._validate_fee_evidence(FEE, config)["evidence_status"] == "conservative_policy_assumption"


def test_config_rejects_wrong_name_and_bad_fee_hash(tmp_path):
    wrong_name = tmp_path / "wrong.yaml"
    wrong_name.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="filename"):
        module.load_execution_cost_evidence_config(wrong_name)
    config = module.load_execution_cost_evidence_config(CONFIG, ROOT)
    bad = tmp_path / FEE.name
    bad.write_text(FEE.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="hash_mismatch"):
        module._validate_fee_evidence(bad, config)


def test_pins_accept_frozen_markers_and_reject_wrong_paths():
    config = module.load_execution_cost_evidence_config(CONFIG, ROOT)
    assert module._validate_preregistration_pin(PREREG, config, ROOT)["family_size"] == 36
    assert module._validate_mapping_pin(MAPPING, config, ROOT)["feasibility"]["execution_price_mapping_feasible"] is True
    with pytest.raises(MarketDataError, match="pin_mismatch"):
        module._validate_mapping_pin(ROOT / "missing.json", config, ROOT)


def test_stress_and_constraints_are_uniform_and_do_not_compute_amounts():
    config = module.load_execution_cost_evidence_config(CONFIG, ROOT)
    stress = module._stress_rows(config)
    assert len(stress) == 6
    assert {row["applies_to"] for row in stress} == {"all_36_variants"}
    assert all(row["historical_observation"] is False for row in stress)
    constraints = module._constraint_rows(config)
    assert any(row["constraint_name"] == "capacity_volume_field" for row in constraints)
    assert all(row["blocks_pnl_computation"] is True for row in constraints)


def test_capacity_requires_canonical_volume(monkeypatch):
    config = module.load_execution_cost_evidence_config(CONFIG, ROOT)
    entries = tuple(SimpleNamespace(resolved_path=ROOT / "dummy.csv") for _ in module.ALL_DATASET_IDS)
    monkeypatch.setattr(module, "load_dataset_registry", lambda _: SimpleNamespace(
        get=lambda _dataset_id: entries[0]
    ))
    monkeypatch.setattr(module.pd, "read_csv", lambda *_args, **_kwargs: pd.DataFrame(columns=["timestamp", "open"]))
    with pytest.raises(MarketDataError, match="volume_field_missing"):
        module._validate_capacity_volume(SimpleNamespace(registry_path=ROOT / "registry.yaml"), config)


def test_freeze_success_is_deterministic_and_pnl_stays_blocked(monkeypatch, tmp_path):
    migration_report = json.loads(MIGRATION.read_text(encoding="utf-8"))
    fake_migration = SimpleNamespace(report=migration_report, registry_path=ROOT / "registry.yaml")
    prereg = {"family_size": 36}
    mapping = {"feasibility": {"execution_price_mapping_feasible": True}}
    monkeypatch.setattr(module, "_validate_preregistration_pin", lambda *_args: prereg)
    monkeypatch.setattr(module, "_validate_mapping_pin", lambda *_args: mapping)
    monkeypatch.setattr(module, "validate_okx_direct_six_asset_1h_migration", lambda *_args: fake_migration)
    monkeypatch.setattr(module, "_validate_migration_pin", lambda *_args: None)
    monkeypatch.setattr(module, "_validate_fee_evidence", lambda *_args: json.loads(FEE.read_text(encoding="utf-8")))
    monkeypatch.setattr(module, "_validate_capacity_volume", lambda *_args: None)
    output = tmp_path / "reports" / "cost"
    output.mkdir(parents=True)
    monkeypatch.setattr(module, "_reports_output", lambda *_args: output)
    result = module.freeze_execution_cost_evidence(PREREG, MAPPING, CONFIG, output)
    assert result.report["feasibility"]["cost_contract_frozen"] is True
    assert result.report["feasibility"]["pnl_computation_authorized"] is False
    assert "historical_spread_directly_observed: false" in module.format_execution_cost_evidence(result)
    second = module.freeze_execution_cost_evidence(PREREG, MAPPING, CONFIG, output)
    assert second.report == result.report


def test_output_path_escape_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="inside reports"):
        module._reports_output(ROOT, tmp_path)
