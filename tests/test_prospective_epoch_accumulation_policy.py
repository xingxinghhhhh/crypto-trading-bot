from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.prospective_epoch_accumulation_policy as module


ROOT = Path(__file__).resolve().parents[1]
MATURITY = ROOT / "reports/prospective-economic-sample-maturity/prospective-economic-sample-maturity.32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178.json"
READINESS = ROOT / "reports/prospective-economic-readiness/prospective-economic-readiness.0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b.json"
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def test_freeze_is_deterministic_and_uses_next_sunday(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_reports_output", lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)))
    first = module.freeze_prospective_epoch_accumulation_policy(MATURITY, READINESS, CONFIG, tmp_path / "one")
    second = module.freeze_prospective_epoch_accumulation_policy(MATURITY, READINESS, CONFIG, tmp_path / "two")
    assert first.report["policy_sha256"] == second.report["policy_sha256"]
    assert first.report["first_governed_window_start"] == "2026-08-16T10:00:00Z"
    assert first.report["first_governed_window_end"] == "2026-08-16T11:00:00Z"
    assert first.report["current_unique_closed_intervals"] == 160
    assert first.report["remaining_closed_intervals"] == 340
    assert first.report["accumulation_should_continue"] is True
    for key in first.export_paths:
        left, right = Path(first.export_paths[key]), Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()


@pytest.mark.parametrize("field,value", [("sample_maturity_threshold", 499), ("cadence", "daily"), ("historical_backfill_prohibited", False)])
def test_config_drift_rejected(tmp_path: Path, field: str, value: object) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config[field] = value
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, module.MarketDataError)):
        module.load_accumulation_config(path)


def test_config_path_and_output_guards(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        module.load_accumulation_config(tmp_path / "wrong.yaml")
    with pytest.raises(ValueError):
        module._reports_output(ROOT, tmp_path)


def test_window_boundaries_and_utc_guards() -> None:
    config = module.load_accumulation_config(CONFIG)
    before = module._next_window(module._parse_datetime("2026-08-09T09:59:59Z"), config)
    assert before[0].isoformat() == "2026-08-09T10:00:00+00:00"
    at_end = module._next_window(module._parse_datetime("2026-08-09T11:00:00Z"), config)
    assert at_end[0].isoformat() == "2026-08-16T10:00:00+00:00"
    with pytest.raises(module.MarketDataError):
        module._parse_datetime("2026-08-09T10:00:00")
    with pytest.raises(module.MarketDataError):
        module._parse_datetime("2026-08-09T10:00:00+08:00")


def test_input_fail_closed_guards(tmp_path: Path) -> None:
    maturity = json.loads(MATURITY.read_text(encoding="utf-8"))
    readiness = json.loads(READINESS.read_text(encoding="utf-8"))
    with pytest.raises(module.MarketDataError):
        module._validate_inputs(maturity, readiness, tmp_path / MATURITY.name, READINESS)
    maturity["sample_maturity_met"] = True
    with pytest.raises(module.MarketDataError):
        module._validate_inputs(maturity, readiness, MATURITY, READINESS)
    maturity = json.loads(MATURITY.read_text(encoding="utf-8"))
    maturity["identity"]["readiness_reports"][0]["readiness_sha256"] = "old"
    with pytest.raises(module.MarketDataError):
        module._validate_inputs(maturity, readiness, MATURITY, READINESS)


def test_marker_and_content_addressed_guards(tmp_path: Path) -> None:
    with pytest.raises(module.MarketDataError):
        module._load_marker(tmp_path / "missing.json", "prospective-economic-readiness", ROOT)
    path = tmp_path / "artifact"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")
    assert module._canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_format_and_invalid_snapshot_lookup(tmp_path: Path) -> None:
    result = module.ProspectiveEpochAccumulationPolicyResult({"contract_status": "status", "policy_sha256": "sha", "current_unique_closed_intervals": 160, "minimum_required": 500, "remaining_closed_intervals": 340, "cadence": "weekly", "capture_window_utc": "[10,11)", "first_governed_window_start": "a", "first_governed_window_end": "b", "sample_maturity_met": False, "accumulation_should_continue": True}, {})
    assert "policy_sha256: sha" in module.format_prospective_epoch_accumulation_policy(result)
    with pytest.raises(module.MarketDataError):
        module._latest_snapshot(tmp_path, {}, {})
