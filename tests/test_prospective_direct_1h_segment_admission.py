from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_direct_1h_segment_admission as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME
CLOSURE_REAL = next((ROOT / "reports/prospective-membership-epoch-closure-smoke").glob("*.json"))
CHAIN = next((ROOT / "reports/prospective-direct-1h-segment-chain").glob("*.json"))


@pytest.fixture
def workspace(tmp_path: Path):
    path = ROOT / "reports" / f"prospective-direct-1h-segment-admission-tests-{tmp_path.name}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _synthetic_closure(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    report = {"closure_sha256": "c" * 64, "contract_status": "verified_prospective_membership_epoch_closure", "status": "closed_future_epoch", "membership_epoch_closed": True, "epoch_end_resolved": True, "epoch_id": "epoch-0002", "last_execution_timestamp": "2026-08-23T10:00:00Z"}
    marker = workspace / f"prospective-membership-epoch-closure.{report['closure_sha256']}.json"
    marker.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(module, "validate_prospective_membership_epoch_closure", lambda _path: report)
    return marker


def test_real_blocked_closure_produces_no_request(workspace: Path) -> None:
    result = module.freeze_prospective_direct_1h_segment_admission(CLOSURE_REAL, CHAIN, CONFIG, workspace / "blocked")
    assert result.report["status"] == "blocked_membership_epoch_not_closed"
    assert result.report["admission_eligible"] is False
    assert result.report["request_rows"] == 0
    assert result.report["asset_rows"] == 0
    assert result.report["expected_total_canonical_rows"] == 0
    assert result.report["capture_performed"] is False
    assert module.validate_prospective_direct_1h_segment_admission(result.export_paths["report"])["request_rows"] == 0


def test_synthetic_closed_epoch_derives_segment_window_and_is_deterministic(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closure = _synthetic_closure(workspace, monkeypatch)
    one = module.freeze_prospective_direct_1h_segment_admission(closure, CHAIN, CONFIG, workspace / "one")
    two = module.freeze_prospective_direct_1h_segment_admission(closure, CHAIN, CONFIG, workspace / "two")
    assert one.report["status"] == "admitted_future_segment_request"
    assert one.report["admission_eligible"] is True
    assert one.report["segment_start"] == "2026-08-09T11:00:00Z"
    assert one.report["segment_end"] == "2026-08-23T10:00:00Z"
    assert one.report["expected_bars_per_asset"] == 336
    assert one.report["expected_total_canonical_rows"] == 2016
    assert one.report["request_rows"] == 1
    assert one.report["asset_rows"] == 6
    assert one.report["new_samples_counted"] == 0
    assert Path(one.export_paths["report"]).read_bytes() == Path(two.export_paths["report"]).read_bytes()
    assert module.validate_prospective_direct_1h_segment_admission(one.export_paths["report"])["expected_bars_per_asset"] == 336


def test_config_and_tamper_guards(workspace: Path, tmp_path: Path) -> None:
    result = module.freeze_prospective_direct_1h_segment_admission(CLOSURE_REAL, CHAIN, CONFIG, workspace / "tamper")
    report = Path(result.export_paths["report"])
    value = json.loads(report.read_text(encoding="utf-8"))
    value["capture_performed"] = True
    report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(MarketDataError, match="identity mismatch|report mismatch"):
        module.validate_prospective_direct_1h_segment_admission(report)
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["sample_threshold"] = 499
    bad = workspace / CONFIG.name
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(MarketDataError, match="config mismatch"):
        module.freeze_prospective_direct_1h_segment_admission(CLOSURE_REAL, CHAIN, bad, workspace / "bad-config")
    with pytest.raises(ValueError, match="output"):
        module.freeze_prospective_direct_1h_segment_admission(CLOSURE_REAL, CHAIN, CONFIG, tmp_path / "outside")


def test_low_level_and_lineage_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    closure = _synthetic_closure(workspace, monkeypatch)
    report = json.loads(closure.read_text(encoding="utf-8"))
    report["last_execution_timestamp"] = "2026-08-09T10:00:00Z"
    monkeypatch.setattr(module, "validate_prospective_membership_epoch_closure", lambda _path: report)
    with pytest.raises(MarketDataError, match="window ordering|empty"):
        module.freeze_prospective_direct_1h_segment_admission(closure, CHAIN, CONFIG, workspace / "bad-window")
    with pytest.raises(ValueError, match="filename"):
        module.load_segment_admission_config(tmp_path / "bad.yaml")
    malformed = tmp_path / "bad.json"
    malformed.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="JSON shape"):
        module._load_json(malformed)
    with pytest.raises(MarketDataError, match="policy semantics"):
        module._validate_policy({})
    assert module._csv_value(False) == "false"
    assert module._is_sha256("a" * 64)
    assert not module._is_sha256("bad")


def test_serialization_and_path_guards(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inside reports"):
        module._reports_output(ROOT, tmp_path)
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json", encoding="utf-8")
    with pytest.raises(MarketDataError, match="JSON read"):
        module._load_json(malformed)
    missing = tmp_path / "missing.json"
    with pytest.raises(MarketDataError, match="JSON read"):
        module._load_json(missing)
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("wrong\nvalue\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="CSV schema"):
        module._read_csv(bad_csv, ("key", "value"))
    with pytest.raises(MarketDataError, match="CSV read"):
        module._read_csv(tmp_path / "missing.csv", ("key", "value"))
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("key,value\na,1\na,2\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="key collision"):
        module._read_key_values(duplicate)
    with pytest.raises(MarketDataError, match="timezone"):
        module._parse_iso("2026-08-09T11:00:00")
    assert module._iso(module._parse_iso("2026-08-09T11:00:00+08:00")) == "2026-08-09T03:00:00Z"
    target = tmp_path / "addressed.bin"
    module._commit_bytes(target, b"one")
    module._commit_bytes(target, b"one")
    with pytest.raises(MarketDataError, match="collision"):
        module._commit_bytes(target, b"two")
    assert module._csv_value(None) == ""
    assert module._csv_value(3) == "3"
    assert module._rows({"b": 2, "a": True}) == [{"key": "a", "value": "true"}, {"key": "b", "value": "2"}]
    state = {"closure_sha256": "c" * 64, "segment_chain_sha256": "d" * 64, "epoch_id": "epoch-0002", "segment_start": None, "segment_end": None, "admission_eligible": False, "status": "blocked_membership_epoch_not_closed", "expected_bars_per_asset": 0, "expected_total_canonical_rows": 0}
    assert module._dependencies(state)["segment_start"] == ""
    assert module._constraints(state)["capture_performed"] is False
    assert module._claims()["request_contract_only"] is True
    assert module._canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert len(module._pretty_json_bytes({"ok": True})) > 0
    assert len(module._digest(b"fixture")) == 64
    assert module._repo_root(CONFIG) == ROOT
    with pytest.raises(MarketDataError, match="repo root"):
        module._repo_root(tmp_path / "no-project")
    with pytest.raises(MarketDataError, match="report path escape"):
        module.validate_prospective_direct_1h_segment_admission(ROOT / "tmp" / "outside.json")
    bad_report = ROOT / "reports" / "prospective-direct-1h-segment-admission.bad.json"
    bad_report.write_text(json.dumps({"admission_sha256": "a" * 64}), encoding="utf-8")
    try:
        with pytest.raises(MarketDataError, match="identity mismatch"):
            module.validate_prospective_direct_1h_segment_admission(bad_report)
    finally:
        bad_report.unlink()
