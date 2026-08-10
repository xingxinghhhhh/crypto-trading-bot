from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.prospective_economic_readiness_gate as module


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "reports/prospective-portfolio-ledger/prospective-portfolio-ledger.010881f9749218f129fab363ed9ebfc313182df2c4459ddafdbbed5465a2e65c.json"
ACCOUNTING = ROOT / "reports/prospective-economic-accounting/prospective-economic-accounting.a519807c88ffee4f8e902f5c282babf0c9b8c1a97555228ac644c8d3e2de71f2.json"
SCENARIOS = ROOT / "reports/prospective-economic-cost-scenarios/prospective-economic-cost-scenarios.5f4ec5239cac34821d3a2d0de3f1635bef027b034b50a0c3872be033ec4d3c88.json"
EXTENSION = ROOT / "reports/prospective-direct-1h-extension/prospective-direct-1h-extension.b0cbcb119a24ee786a81a6aed7adf4ddb8b11cf93891c19ca1fb21c4d3848146.json"
MEMBERSHIP = ROOT / "reports/prospective-membership-bar-gate/prospective-membership-bar-gate.737e3da3a1e2db42b87709a23bc5f754bab2ae2a38af36210b28282cfb3d9f48.json"
MAPPING = ROOT / "reports/okx-direct-six-1h-execution-mapping/okx-direct-six-1h-execution-mapping.48df5c8d1d8f2ee76e45f85920ded6ce37dc25422a14e1b54626668f8aec9ddb.json"
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def test_real_gate_is_deterministic_and_structural_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_reports_output", lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)))
    first = module.audit_prospective_economic_readiness(LEDGER, ACCOUNTING, SCENARIOS, EXTENSION, MEMBERSHIP, MAPPING, CONFIG, tmp_path / "a")
    second = module.audit_prospective_economic_readiness(LEDGER, ACCOUNTING, SCENARIOS, EXTENSION, MEMBERSHIP, MAPPING, CONFIG, tmp_path / "b")
    assert first.report["readiness_sha256"] == second.report["readiness_sha256"]
    assert first.report["strategy_matrix_rows"] == 5760
    assert first.report["benchmark_matrix_rows"] == 160
    assert first.report["terminal_scored_rows"] == 0
    assert first.report["prospective_economic_inputs_structurally_ready"] is True
    assert first.report["economic_value_computation_authorized"] is False
    assert first.report["trading_readiness_changed"] is False
    for key in first.export_paths:
        left, right = Path(first.export_paths[key]), Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()
    header = Path(first.export_paths["strategy_matrix"]).read_text(encoding="utf-8").splitlines()[0]
    assert "open" in header
    assert "price" not in header
    assert "weight" not in header


@pytest.mark.parametrize("field,value", [("required_family_size", 35), ("terminal_scoring_prohibited", False), ("economic_value_computation_authorized", True)])
def test_config_rejects_gate_drift(tmp_path: Path, field: str, value: object) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config[field] = value
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, module.MarketDataError)):
        module.load_readiness_config(path)


def test_matrix_helpers_and_low_level_guards() -> None:
    accounting = module._validate_accounting(ACCOUNTING)
    ledger = module._validate_ledger(LEDGER)
    intervals = module._closed_intervals(accounting)
    families = module._families(ledger)
    assert len(intervals) == 160
    assert len(families) == 36
    with pytest.raises(module.MarketDataError):
        module._families({"identity": {"family": {"factors": ["x"]}}})
    assert module._slot_flags("missing", "missing", {inst: {} for inst in module.INST_IDS}) == (False, False)
    assert module._csv_value(True) == "true"
    assert module._csv_value(1.25) == "1.25"
    assert module._canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_validators_fail_closed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    for validator in (module._validate_ledger, module._validate_accounting, module._validate_scenarios, module._validate_extension, module._validate_membership, module._validate_mapping):
        with pytest.raises(module.MarketDataError):
            validator(bad)
    with pytest.raises(module.MarketDataError):
        module._pin_inputs(ROOT, bad, bad, bad, bad, bad, bad)
    with pytest.raises(ValueError):
        module._reports_output(ROOT, tmp_path)


def test_source_mutation_and_collision_guards(tmp_path: Path) -> None:
    scenarios = module._validate_scenarios(SCENARIOS)
    bad = json.loads(json.dumps(scenarios))
    bad["primary_spread_bps"] = 0
    bad_path = tmp_path / SCENARIOS.name
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module._validate_scenarios(bad_path)
    path = tmp_path / "x"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")


def test_format_and_csv_read_failure(tmp_path: Path) -> None:
    result = module.ProspectiveEconomicReadinessGateResult({"contract_status": "status", "readiness_sha256": "sha"}, {})
    assert "readiness_sha256: sha" in module.format_prospective_economic_readiness(result)
    with pytest.raises(module.MarketDataError):
        module._read_csv(tmp_path / "missing.csv")
    with pytest.raises(module.MarketDataError):
        module._load_json(tmp_path / "missing.json")
