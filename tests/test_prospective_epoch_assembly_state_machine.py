from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.prospective_epoch_assembly_state_machine as module


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "reports/prospective-epoch-accumulation-policy/prospective-epoch-accumulation-policy.8f4a90632bcf73df04c7e6f44924924db682cdc8373ed04c1f726cb6ce353092.json"
MATURITY = ROOT / "reports/prospective-economic-sample-maturity/prospective-economic-sample-maturity.32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178.json"
READINESS = ROOT / "reports/prospective-economic-readiness/prospective-economic-readiness.0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b.json"
CHAIN = ROOT / "reports/prospective-direct-1h-segment-chain/prospective-direct-1h-segment-chain.c6e3ccad96735923760931afc3b81e16068e880d64a97ec4d34d84908a681f05.json"
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def test_pending_epoch_assembly_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_reports_output", lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)))
    first = module.freeze_prospective_epoch_assembly_state_machine(POLICY, MATURITY, READINESS, CHAIN, CONFIG, tmp_path / "one")
    second = module.freeze_prospective_epoch_assembly_state_machine(POLICY, MATURITY, READINESS, CHAIN, CONFIG, tmp_path / "two")
    assert first.report["assembly_sha256"] == second.report["assembly_sha256"]
    assert first.report["current_state"] == "awaiting_capture_window"
    assert first.report["next_epoch_ordinal"] == 2
    assert first.report["next_capture_window_start"] == "2026-08-16T10:00:00Z"
    assert first.report["next_capture_window_end"] == "2026-08-16T11:00:00Z"
    assert first.report["expected_next_market_segment_start"] == "2026-08-09T11:00:00Z"
    assert first.report["new_samples_counted"] == 0
    for key in first.export_paths:
        left, right = Path(first.export_paths[key]), Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()


@pytest.mark.parametrize("field,value", [("sample_threshold", 499), ("state_order", "loose"), ("historical_backfill_prohibited", False)])
def test_config_drift_rejected(tmp_path: Path, field: str, value: object) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config[field] = value
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, module.MarketDataError)):
        module.load_assembly_config(path)


def test_state_graph_and_low_level_guards(tmp_path: Path) -> None:
    rows = module._state_rows()
    assert len(rows) == 10
    assert rows[0]["state"] == "awaiting_capture_window"
    assert rows[-1]["next_state"] == "terminal"
    assert module._rows({"b": True, "a": 1})[0]["key"] == "a"
    assert module._csv_bytes([{"key": "a", "value": "b"}], module.KEY_VALUE_FIELDS) == b"key,value\na,b\n"
    assert module._repo_root(POLICY) == ROOT
    assert module._iso(module._parse_iso("2026-08-16T10:00:00Z")) == "2026-08-16T10:00:00Z"
    with pytest.raises(module.MarketDataError):
        module._parse_iso("2026-08-16T10:00:00")
    with pytest.raises(ValueError):
        module._reports_output(ROOT, tmp_path)


def test_fail_closed_inputs(tmp_path: Path) -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    maturity = json.loads(MATURITY.read_text(encoding="utf-8"))
    readiness = json.loads(READINESS.read_text(encoding="utf-8"))
    chain = json.loads(CHAIN.read_text(encoding="utf-8"))
    with pytest.raises(module.MarketDataError):
        module._validate_inputs(policy, maturity, readiness, chain, tmp_path / "bad.json", MATURITY, READINESS, CHAIN)
    chain["next_canonical_segment_start"] = "2026-08-09T12:00:00Z"
    with pytest.raises(module.MarketDataError):
        module._validate_inputs(policy, maturity, readiness, chain, POLICY, MATURITY, READINESS, CHAIN)
    readiness = json.loads(READINESS.read_text(encoding="utf-8"))
    maturity["identity"]["readiness_reports"][0]["readiness_sha256"] = "old"
    with pytest.raises(module.MarketDataError):
        module._validate_inputs(policy, maturity, readiness, json.loads(CHAIN.read_text(encoding="utf-8")), POLICY, MATURITY, READINESS, CHAIN)
    with pytest.raises(module.MarketDataError):
        module._marker(tmp_path / "missing.json", "prospective-economic-readiness", ROOT)
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("not-json", encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module._load_json(bad_json)


def test_content_addressed_collision_and_format(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")
    result = module.EpochAssemblyResult({"contract_status": "status", "assembly_sha256": "sha", "current_state": "awaiting_capture_window", "next_epoch_ordinal": 2, "next_capture_window_start": "a", "next_capture_window_end": "b", "expected_previous_snapshot": "p", "expected_next_market_segment_start": "s"}, {})
    assert "assembly_sha256: sha" in module.format_epoch_assembly_result(result)
    with pytest.raises(module.MarketDataError):
        module._load_json(tmp_path / "missing.json")
