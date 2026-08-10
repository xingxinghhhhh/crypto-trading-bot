from __future__ import annotations

import json
from pathlib import Path

import pytest

import crypto_bot.prospective_economic_accounting_contract as module


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "reports/prospective-portfolio-ledger/prospective-portfolio-ledger.010881f9749218f129fab363ed9ebfc313182df2c4459ddafdbbed5465a2e65c.json"
COST = ROOT / "reports/execution-cost-evidence/execution-cost-evidence.5633858d1e4377c507953df65f3014c30b3d98d1ec4f36c72bf1fa4c5274dc21.json"
CONFIG = ROOT / "config.prospective-economic-accounting.example.yaml"


def test_interval_family_cost_and_benchmark_shapes() -> None:
    timestamps = [f"2026-08-02T{18 + index:02d}:00:00Z" for index in range(2)]
    execution = [f"2026-08-02T{20 + index:02d}:00:00Z" for index in range(2)]
    intervals = module._interval_rows(timestamps, execution)
    assert intervals[0]["economic_interval_closed"] is True
    assert intervals[1]["terminal_unscored"] is True
    ledger = {"identity": {"family": {"factors": ["low_volatility_20", "mean_reversion_20", "momentum_12", "momentum_4", "trend_quality_20", "volume_momentum_20"]}}}
    assert len(module._family_rows(ledger)) == 36
    cost = {"identity": {"fee": {"fee_policy_id": "fee", "fee_rate_bps": 100}, "spread": {"policy": "spread", "stress_bps": [0, 5, 10]}, "slippage": {"policy": "slip", "stress_bps": [0, 5, 10]}, "capacity": {"policy": "cap"}}}
    cost_rows = module._cost_rows(cost)
    assert next(row for row in cost_rows if row["component"] == "spread")["semantic_status"] == "unresolved_blocker"
    benchmark = module._benchmark_rows()
    assert len(benchmark) == 6
    assert sum(row["weight"] for row in benchmark) == pytest.approx(1.0)


def test_real_inputs_validate_and_contract_freezes(tmp_path, monkeypatch) -> None:
    config = module.load_accounting_config(CONFIG, ROOT)
    ledger = module._validate_ledger(LEDGER, config)
    cost = module._validate_cost(COST, ROOT, config)
    assert ledger["ledger_sha256"] == config["portfolio_ledger_sha256"]
    assert cost["cost_sha256"] == config["cost_evidence_sha256"]
    monkeypatch.setattr(module, "_reports_output", lambda _repo, _output: tmp_path)
    result = module.freeze_prospective_economic_accounting_contract(LEDGER, COST, CONFIG, tmp_path)
    assert result.report["closed_interval_count"] == 160
    assert result.report["terminal_unscored_count"] == 1
    assert result.report["economic_cost_application_ready"] is False
    assert result.report["pnl_rows"] == 0
    assert "spread_application_semantics_resolved: false" in module.format_prospective_economic_accounting_contract(result)
    assert json.loads(Path(result.export_paths["report"]).read_text(encoding="utf-8"))["accounting_sha256"] == result.report["accounting_sha256"]


def test_frozen_config_rejects_wrong_shape(tmp_path) -> None:
    bad = tmp_path / module.DEFAULT_CONFIG_FILENAME
    value = module.load_accounting_config(CONFIG)
    value["family_size"] = 35
    import yaml

    bad.write_text(yaml.safe_dump(value), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_accounting_config(bad)


def test_low_level_serialization_and_collision_guards(tmp_path) -> None:
    path = tmp_path / "marker.json"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")
    assert module._csv_value(True) == "true"
    assert module._csv_value(1.25) == "1.25"
    assert module._canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_contract_fail_closed_guards(tmp_path) -> None:
    with pytest.raises(module.MarketDataError):
        module._family_rows({"identity": {"family": {"factors": ["momentum_4"]}}})
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    config = module.load_accounting_config(CONFIG)
    with pytest.raises(module.MarketDataError):
        module._validate_ledger(bad, config)
    with pytest.raises(module.MarketDataError):
        module._validate_cost(bad, ROOT, config)
