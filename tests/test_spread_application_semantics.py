from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.spread_application_semantics as module


ROOT = Path(__file__).resolve().parents[1]
ACCOUNTING = ROOT / "reports/prospective-economic-accounting/prospective-economic-accounting.a519807c88ffee4f8e902f5c282babf0c9b8c1a97555228ac644c8d3e2de71f2.json"
COST = ROOT / "reports/execution-cost-evidence/execution-cost-evidence.5633858d1e4377c507953df65f3014c30b3d98d1ec4f36c72bf1fa4c5274dc21.json"
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def test_config_and_formula_are_frozen() -> None:
    value = module.load_spread_application_semantics_config(CONFIG, ROOT)
    assert value["source_spread_stress_bps"] == [0, 5, 10]
    assert module.spread_cost_fraction(0.4, 5) == pytest.approx(0.4 * 5e-4)
    with pytest.raises(module.MarketDataError):
        module.spread_cost_fraction(-0.1, 5)
    with pytest.raises(module.MarketDataError):
        module.spread_cost_fraction(0.4, 7)
    with pytest.raises(module.MarketDataError):
        module.spread_cost_fraction(0.4, 5, 2)


@pytest.mark.parametrize(
    "field,value",
    [("application_semantics", "full_quoted_spread"), ("half_spread_conversion", True), ("full_spread_conversion", True), ("application_multiplier", 2), ("quoted_spread_width_claim", True)],
)
def test_config_rejects_ambiguous_or_doubled_semantics(tmp_path: Path, field: str, value: object) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config[field] = value
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, module.MarketDataError)):
        module.load_spread_application_semantics_config(path)


def test_real_contract_is_deterministic_and_fail_closed_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_a = tmp_path / "a"
    output_b = tmp_path / "b"
    monkeypatch.setattr(module, "_reports_output", lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)))
    first = module.freeze_spread_application_semantics(ACCOUNTING, COST, CONFIG, output_a)
    second = module.freeze_spread_application_semantics(ACCOUNTING, COST, CONFIG, output_b)
    assert first.report["semantics_sha256"] == second.report["semantics_sha256"]
    assert first.report["economic_cost_application_ready"] is True
    assert first.report["return_computation_authorized"] is False
    assert first.report["turnover_computation_authorized"] is False
    assert first.report["cost_amount_computation_authorized"] is False
    assert first.report["pnl_computation_authorized"] is False
    assert first.report["profitability_evidence"] is False
    assert first.report["readiness_changed"] is False
    for key in ("tiers", "constraints", "report"):
        left = Path(first.export_paths[key])
        right = Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()
    marker = json.loads(Path(first.export_paths["report"]).read_text(encoding="utf-8"))
    assert marker["identity"]["application"]["application_multiplier"] == 1
    assert marker["identity"]["application"]["half_spread_conversion"] is False


def test_source_contract_rejects_drift() -> None:
    accounting = module._validate_accounting_marker(ACCOUNTING, ROOT)
    cost = module._validate_cost(COST, ROOT, module.load_accounting_config(ROOT / "config.prospective-economic-accounting.example.yaml", ROOT))
    bad_cost = json.loads(json.dumps(cost))
    bad_cost["identity"]["spread"]["stress_bps"] = [0, 10]
    with pytest.raises(module.MarketDataError):
        module._validate_source_contract(accounting, bad_cost)
    bad_cost = json.loads(json.dumps(cost))
    bad_cost["identity"]["uniformity"]["cost_uniform_across_variants"] = False
    with pytest.raises(module.MarketDataError):
        module._validate_source_contract(accounting, bad_cost)


def test_marker_and_artifact_guards(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module._validate_accounting_marker(bad, ROOT)
    with pytest.raises(module.MarketDataError):
        module._artifact_sha(bad, {}, "missing")
    path = tmp_path / "x"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")


def test_output_and_path_guards(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        module._reports_output(ROOT, tmp_path)
    with pytest.raises(module.MarketDataError):
        module._pin_path(ROOT, ROOT / "other.json", "expected.json")
    with pytest.raises(module.MarketDataError):
        module._repo_root(tmp_path / "not-a-repo")


def test_serialization_and_format() -> None:
    assert module._csv_value(True) == "true"
    assert module._csv_value(1.25) == "1.25"
    assert module._canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    result = module.SpreadApplicationSemanticsResult(
        {"contract_status": "status", "semantics_sha256": "sha", "spread_application_semantics_resolved": True, "spread_application_multiplier": 1, "economic_cost_application_ready": True, "return_computation_authorized": False, "turnover_computation_authorized": False, "cost_amount_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False},
        {},
    )
    assert "semantics_sha256: sha" in module.format_spread_application_semantics(result)
