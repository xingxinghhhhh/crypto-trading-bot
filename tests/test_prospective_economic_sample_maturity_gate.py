from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import crypto_bot.prospective_economic_sample_maturity_gate as module


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "reports/prospective-economic-readiness/prospective-economic-readiness.0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b.json"
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def test_current_sample_is_structurally_valid_but_not_mature(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_reports_output", lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)))
    first = module.audit_prospective_economic_sample_maturity([READINESS], CONFIG, tmp_path / "a")
    second = module.audit_prospective_economic_sample_maturity([READINESS], CONFIG, tmp_path / "b")
    assert first.report["maturity_sha256"] == second.report["maturity_sha256"]
    assert first.report["unique_closed_interval_count"] == 160
    assert first.report["minimum_closed_interval_count"] == 500
    assert first.report["remaining_closed_interval_count"] == 340
    assert first.report["strategy_slot_count_observed"] == 5760
    assert first.report["strategy_slots_count_as_samples"] is False
    assert first.report["sample_maturity_met"] is False
    assert first.report["prospective_economic_evaluation_authorizable"] is False
    for key in first.export_paths:
        left, right = Path(first.export_paths[key]), Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()


@pytest.mark.parametrize("field,value", [("minimum_closed_interval_count", 499), ("sample_unit", "slot"), ("threshold_reduction_prohibited", False)])
def test_config_rejects_threshold_drift(tmp_path: Path, field: str, value: object) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config[field] = value
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, module.MarketDataError)):
        module.load_maturity_config(path)


def test_epoch_overlap_gap_and_duplicate_guards() -> None:
    base = {"ordinal": 1, "readiness_sha256": "a", "marker_sha256": "m", "artifact_hashes": {}, "protocol_fingerprint": "p", "strategy_matrix_rows": 5760, "first_execution_start": "2026-01-01T00:00:00Z", "last_execution_end": "2026-01-01T02:00:00Z", "closed_interval_count": 2, "family_size": 36, "intervals": [], "sources": {"membership_gate": {"identity": "same"}}, "preceding_gap_class": "none", "readiness_identity": "a"}
    overlap = dict(base)
    overlap["ordinal"] = 2
    overlap["first_execution_start"] = "2026-01-01T01:00:00Z"
    with pytest.raises(module.MarketDataError):
        module._validate_epoch_order([base, overlap])
    gap = dict(base)
    gap["ordinal"] = 2
    gap["first_execution_start"] = "2026-01-01T04:00:00Z"
    gap["sources"] = {"membership_gate": {"identity": "new"}}
    module._validate_epoch_order([base, gap])
    duplicate = {"intervals": [{"interval_id": "0", "execution_start": "a", "execution_end": "b"}]}
    with pytest.raises(module.MarketDataError):
        module._merge_intervals([duplicate, duplicate])


def test_matrix_and_low_level_guards(tmp_path: Path) -> None:
    report = module._validate_readiness(READINESS, ROOT)
    assert len(report["intervals"]) == 160
    with pytest.raises(module.MarketDataError):
        module._validate_matrix([], [])
    with pytest.raises(module.MarketDataError):
        module._artifact(tmp_path / "bad.json", {}, "missing")
    with pytest.raises(ValueError):
        module._reports_output(ROOT, tmp_path)
    assert module._csv_value(True) == "true"
    assert module._csv_value(1.25) == "1.25"
    assert module._canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_invalid_readiness_and_collision_guards(tmp_path: Path) -> None:
    bad = tmp_path / READINESS.name
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module._validate_readiness(bad, ROOT)
    with pytest.raises(ValueError):
        module.audit_prospective_economic_sample_maturity([], CONFIG, tmp_path)
    path = tmp_path / "x"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")


def test_format_and_csv_json_failures(tmp_path: Path) -> None:
    result = module.ProspectiveEconomicSampleMaturityResult({"contract_status": "status", "maturity_sha256": "sha", "readiness_epoch_count": 1, "unique_closed_interval_count": 160, "remaining_closed_interval_count": 340, "sample_maturity_met": False, "prospective_economic_evaluation_authorizable": False}, {})
    assert "maturity_sha256: sha" in module.format_prospective_economic_sample_maturity(result)
    with pytest.raises(module.MarketDataError):
        module._read_csv(tmp_path / "missing.csv")
    with pytest.raises(module.MarketDataError):
        module._load_json(tmp_path / "missing.json")
