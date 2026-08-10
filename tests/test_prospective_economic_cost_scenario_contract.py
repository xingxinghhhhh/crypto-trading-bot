from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.prospective_economic_cost_scenario_contract as module


ROOT = Path(__file__).resolve().parents[1]
ACCOUNTING = ROOT / "reports/prospective-economic-accounting/prospective-economic-accounting.a519807c88ffee4f8e902f5c282babf0c9b8c1a97555228ac644c8d3e2de71f2.json"
COST = ROOT / "reports/execution-cost-evidence/execution-cost-evidence.5633858d1e4377c507953df65f3014c30b3d98d1ec4f36c72bf1fa4c5274dc21.json"
SPREAD = ROOT / "reports/spread-application-semantics/spread-application-semantics.6622da645ef1b665fcb49357faefecc483ac61c2d2b1faf89a9f1249f17e8380.json"
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def test_scenario_and_capacity_formulas() -> None:
    rows = module._scenario_rows()
    assert len(rows) == 9
    assert rows[0]["scenario_id"] == "fee100_spread0_slippage0"
    assert rows[-1]["primary"] is True
    assert rows[-1]["symbolic_total_friction_bps"] == 120
    assert module.symbolic_total_friction_bps(100, 5, 10) == 115
    assert module.capacity_base_quantity(100) == pytest.approx(1.0)
    with pytest.raises(module.MarketDataError):
        module.symbolic_total_friction_bps(99, 5, 10)
    with pytest.raises(module.MarketDataError):
        module.symbolic_total_friction_bps(100, 7, 10)
    with pytest.raises(module.MarketDataError):
        module.capacity_base_quantity(-1)


@pytest.mark.parametrize(
    "field,value",
    [("scenario_topology", "optional"), ("fee_multiplier", 2), ("spread_application", "full_spread"), ("capacity_role", "cost"), ("scenario_selection_prohibited", False)],
)
def test_config_rejects_policy_drift(tmp_path: Path, field: str, value: object) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config[field] = value
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, module.MarketDataError)):
        module.load_scenario_config(path)


def test_real_scenario_contract_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    monkeypatch.setattr(module, "_reports_output", lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)))
    first = module.freeze_prospective_economic_cost_scenarios(ACCOUNTING, COST, SPREAD, CONFIG, out_a)
    second = module.freeze_prospective_economic_cost_scenarios(ACCOUNTING, COST, SPREAD, CONFIG, out_b)
    assert first.report["scenario_contract_sha256"] == second.report["scenario_contract_sha256"]
    assert first.report["scenario_count"] == 9
    assert first.report["primary_scenario_count"] == 1
    assert first.report["capacity_role"] == "hard_scale_constraint_not_cost"
    assert first.report["turnover_value_rows"] == 0
    assert first.report["cost_amount_rows"] == 0
    assert first.report["capacity_pass_fail_rows"] == 0
    assert first.report["return_rows"] == 0
    assert first.report["pnl_rows"] == 0
    for key in first.export_paths:
        left = Path(first.export_paths[key])
        right = Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()
    marker = json.loads(Path(first.export_paths["report"]).read_text(encoding="utf-8"))
    assert marker["identity"]["scenarios"]["primary_scenario"] == {"fee_bps": 100, "spread_bps": 10, "slippage_bps": 10}
    assert marker["identity"]["capacity_policy"]["capacity_amount_computation_authorized"] is False


def test_input_validators_fail_closed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module._validate_accounting_marker(bad)
    with pytest.raises(module.MarketDataError):
        module._validate_spread_marker(bad)
    with pytest.raises(module.MarketDataError):
        module._validate_inputs({}, {}, {})
    with pytest.raises(ValueError):
        module._reports_output(ROOT, tmp_path)


def test_source_drift_guards() -> None:
    accounting = module._validate_accounting_marker(ACCOUNTING)
    cost = module._validate_cost(COST, ROOT, module.load_accounting_config(ROOT / "config.prospective-economic-accounting.example.yaml", ROOT))
    spread = module._validate_spread_marker(SPREAD)
    bad_cost = json.loads(json.dumps(cost))
    bad_cost["identity"]["fee"]["fee_rate_bps"] = 50
    with pytest.raises(module.MarketDataError):
        module._validate_inputs(accounting, bad_cost, spread)
    bad_spread = json.loads(json.dumps(spread))
    bad_spread["identity"]["application"]["semantics"] = "full_quoted_spread"
    with pytest.raises(module.MarketDataError):
        module._validate_inputs(accounting, cost, bad_spread)


def test_serialization_collision_and_format(tmp_path: Path) -> None:
    path = tmp_path / "x"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")
    assert module._csv_value(True) == "true"
    assert module._csv_value(1.25) == "1.25"
    result = module.ProspectiveEconomicCostScenarioResult({"contract_status": "status", "scenario_contract_sha256": "sha"}, {})
    assert "scenario_contract_sha256: sha" in module.format_prospective_economic_cost_scenarios(result)

