from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_epoch_capture_admission as module


ROOT = Path(__file__).resolve().parents[1]
ASSEMBLY = ROOT / "reports/prospective-epoch-assembly/prospective-epoch-assembly.674116b95e03705b478f285012148f0143c2ef82befccda5627ce2ab901bcc79.json"
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def test_ticket_is_deterministic_and_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_reports_output", lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)))
    first = module.freeze_prospective_epoch_capture_admission(ASSEMBLY, CONFIG, tmp_path / "one")
    second = module.freeze_prospective_epoch_capture_admission(ASSEMBLY, CONFIG, tmp_path / "two")
    assert first.report["ticket_sha256"] == second.report["ticket_sha256"]
    assert first.report["window_start"] == "2026-08-16T10:00:00Z"
    assert first.report["window_end"] == "2026-08-16T11:00:00Z"
    assert first.report["previous_snapshot"] == module.SNAPSHOT_SHA
    assert first.report["next_segment_start"] == "2026-08-09T11:00:00Z"
    assert first.report["accepted_snapshot_count"] == 0
    assert first.report["network_activity_performed"] is False
    for key in first.export_paths:
        left, right = Path(first.export_paths[key]), Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()


@pytest.mark.parametrize("field,value", [("sample_threshold", 499), ("public_only", False), ("ticket_reuse_policy", "multi_epoch")])
def test_config_drift_rejected(tmp_path: Path, field: str, value: object) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config[field] = value
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, module.MarketDataError)):
        module.load_capture_admission_config(path)


def test_assembly_and_request_guards(tmp_path: Path) -> None:
    assembly = json.loads(ASSEMBLY.read_text(encoding="utf-8"))
    with pytest.raises(module.MarketDataError):
        module._validate_assembly(assembly, tmp_path / ASSEMBLY.name)
    assembly["expected_next_market_segment_start"] = "bad"
    with pytest.raises(module.MarketDataError):
        module._validate_assembly(assembly, ASSEMBLY)
    with pytest.raises(module.MarketDataError):
        module._marker(tmp_path / "missing.json", "prospective-epoch-assembly", ROOT)
    with pytest.raises(ValueError):
        module._reports_output(ROOT, tmp_path)


def test_snapshot_request_policy_and_helpers(tmp_path: Path) -> None:
    snapshot = module._snapshot_request_policy(ROOT)
    assert snapshot["endpoint"].endswith("/public/instruments")
    assert snapshot["request_params"] == {"instType": "SPOT"}
    assert module._repo_root(ASSEMBLY) == ROOT
    assert module._rows({"b": True, "a": 1})[0]["key"] == "a"
    assert module._csv_bytes([{"key": "a", "value": "b"}], module.KEY_VALUE_FIELDS) == b"key,value\na,b\n"


def test_collision_format_and_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")
    result = module.CaptureAdmissionResult({"contract_status": "status", "ticket_sha256": "sha", "window_start": "a", "window_end": "b", "previous_snapshot": "p", "next_segment_start": "s"}, {})
    assert "ticket_sha256: sha" in module.format_capture_admission_result(result)
    bad = tmp_path / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module._load_json(bad)
