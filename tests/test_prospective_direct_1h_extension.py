from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.market.prospective_direct_1h_extension as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.okx-prospective-direct-1h-extension.example.yaml"
GATE = ROOT / "reports" / "prospective-membership-bar-gate" / (
    "prospective-membership-bar-gate.737e3da3a1e2db42b87709a23bc5f754bab2ae2a38af36210b28282cfb3d9f48.json"
)
CAPTURE = ROOT / "reports" / "prospective-direct-1h-capture" / (
    "prospective-direct-1h-capture.12e2022e6d0abb6fdb36ea9757fe9c2b32bdf5cdb93f9a0ad85981aa21b37629.json"
)


def test_config_and_real_capture_validate():
    config = module.load_prospective_direct_config(CONFIG, ROOT)
    assert config["tracked_inst_ids"] == list(module.INST_IDS)
    capture = module.validate_prospective_direct_capture(CAPTURE, config, ROOT)
    assert capture.report["identity"]["append_row_count_total"] == 984
    assert len(capture.export_paths) == 13


def test_real_audit_replays_984_and_966(monkeypatch, tmp_path):
    output = tmp_path / "reports" / "audit"
    output.mkdir(parents=True)
    monkeypatch.setattr(module, "_reports_output", lambda *_args: output)
    result = module.audit_prospective_direct_1h_extension(CAPTURE, GATE, CONFIG, output)
    assert result.report["identity"]["append_row_count_total"] == 984
    assert result.report["identity"]["gate_coverage_row_count"] == 966
    assert result.report["future_only_membership_evidence"] is True


def test_window_and_timestamp_helpers():
    assert module._ceil_hour(datetime(2026, 8, 2, 14, tzinfo=timezone.utc)).hour == 14
    assert module._ceil_hour(datetime(2026, 8, 2, 14, 1, tzinfo=timezone.utc)).hour == 15
    assert module._parse_iso("2026-08-02T15:00:00Z").hour == 15
    assert module._iso(datetime(2026, 8, 2, 15, tzinfo=timezone.utc)) == "2026-08-02T15:00:00Z"
    assert module._timestamp_ms(datetime(1970, 1, 1, tzinfo=timezone.utc)) == 0


def test_coverage_rows_and_dataset_helpers():
    one = {"inst_id": "BTC-USDT", "signal_timestamp": "2026-08-02T15:00:00Z", "completion_timestamp": "2026-08-02T16:00:00Z", "execution_timestamp": "2026-08-02T17:00:00Z", "eligible_for_closed_epoch": "true"}
    gate = SimpleNamespace(eligibility=tuple(one.copy() for _ in range(966)))
    sets = {"BTC-USDT": {"timestamps": [str(module._timestamp_ms(module._parse_iso(x))) for x in ("2026-08-02T15:00:00Z", "2026-08-02T16:00:00Z", "2026-08-02T17:00:00Z")], "opens": ["1", "2", "3"]}}
    rows = module._coverage_rows(gate, sets, ["BTC-USDT"])
    assert rows[0]["execution_open_finite"] is True
    assert module._flatten({"a": {"b": 1}}) == {"a.b": 1}
    assert module._csv_bytes([{"x": True}], ("x",)) == b"x\ntrue\n"


def test_capture_validator_rejects_tampered_marker(tmp_path):
    config = module.load_prospective_direct_config(CONFIG, ROOT)
    payload = json.loads(CAPTURE.read_text(encoding="utf-8"))
    payload["pnl_computation_authorized"] = True
    tampered = tmp_path / CAPTURE.name
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="identity_mismatch"):
        module.validate_prospective_direct_capture(tampered, config, ROOT)


def test_config_rejects_name_and_policy_drift(tmp_path):
    wrong = tmp_path / "wrong.yaml"
    wrong.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="filename"):
        module.load_prospective_direct_config(wrong)
    config = module.load_prospective_direct_config(CONFIG, ROOT)
    config["bar"] = "4H"
    import yaml

    drift = tmp_path / module.DEFAULT_CONFIG_FILENAME
    drift.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="equal"):
        module.load_prospective_direct_config(drift)


def test_safe_helpers_and_serializers(tmp_path):
    with pytest.raises(ValueError, match="inside reports"):
        module._reports_output(ROOT, tmp_path)
    with pytest.raises(MarketDataError, match="path_escape"):
        module._repo_file(ROOT, "../outside")
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="invalid_json"):
        module._load_json(bad)
    target = tmp_path / "artifact"
    target.write_bytes(b"old")
    with pytest.raises(MarketDataError, match="collision"):
        module._commit_bytes(target, b"new")
    module._commit_bytes(target, b"old")


def test_quality_rejects_invalid_csv():
    quality = module.validate_ohlcv_csv_bytes(b"timestamp,open,high,low,close,volume\nnot-a-time,1,1,1,1,1\n")
    assert quality["valid"] is False


def test_mocked_capture_freezes_all_six_assets(monkeypatch, tmp_path):
    config = module.load_prospective_direct_config(CONFIG, ROOT)
    gate = SimpleNamespace(
        report={"gate_sha256": config["membership_gate_sha256"], "identity": {"epoch": {"last_execution_timestamp": "2026-08-09T10:00:00Z"}}},
        eligibility=tuple({"inst_id": inst_id} for inst_id in config["tracked_inst_ids"] for _ in range(161)),
    )
    migration = SimpleNamespace(report={"migration_sha256": config["migration_sha256"], "identity": {"datasets": []}}, repo_root=ROOT)
    monkeypatch.setattr(module, "validate_prospective_membership_bar_gate", lambda *_args: gate)
    monkeypatch.setattr(module, "validate_okx_direct_six_asset_1h_migration", lambda *_args: migration)
    monkeypatch.setattr(module, "_validate_gate_and_migration", lambda *_args: None)
    monkeypatch.setattr(module, "_baseline_tails", lambda *_args: {inst_id: "2026-08-02T14:00:00Z" for inst_id in config["tracked_inst_ids"]})
    monkeypatch.setattr(module, "_reports_output", lambda *_args: tmp_path)
    monkeypatch.setattr(module, "validate_ohlcv_csv_bytes", lambda *_args: {"valid": True})

    def fake_download(inst_id, start_ms, end_ms, **_kwargs):
        rows = []
        for index in range(164):
            rows.append([str(start_ms + index * 3_600_000), "1", "1", "1", "1", "1", "1", "1", "1"])
        return (f"bundle-{inst_id}".encode(), [{"page_index": 0}], rows)

    monkeypatch.setattr(module, "download_okx_public_history", fake_download)
    result = module.capture_prospective_direct_1h_extension(GATE, CONFIG, tmp_path)
    assert result.report["identity"]["append_row_count_total"] == 984
    assert len(result.export_paths) == 13


def test_extension_fail_closed_guards():
    config = module.load_prospective_direct_config(CONFIG, ROOT)
    gate = SimpleNamespace(report={"gate_sha256": "wrong", "identity": {"epoch": {"last_execution_timestamp": "2026-08-09T10:00:00Z"}}}, eligibility=tuple({} for _ in range(966)))
    migration = SimpleNamespace(report={"migration_sha256": "wrong"})
    with pytest.raises(MarketDataError, match="input_identity_mismatch"):
        module._validate_gate_and_migration(config, gate, GATE, migration, ROOT / "missing.json")
    with pytest.raises(MarketDataError, match="dataset_missing"):
        module._dataset_id(SimpleNamespace(report={"identity": {"datasets": []}}), "BTC-USDT")
    with pytest.raises(MarketDataError, match="gate_asset_mismatch"):
        module._coverage_rows(SimpleNamespace(eligibility=({"inst_id": "BAD"},)), {}, config["tracked_inst_ids"])
    with pytest.raises(MarketDataError, match="timestamp_not_timezone_aware"):
        module._parse_iso("2026-08-09T10:00:00")


def test_capture_validator_missing_artifact_fails(tmp_path):
    config = module.load_prospective_direct_config(CONFIG, ROOT)
    payload = json.loads(CAPTURE.read_text(encoding="utf-8"))
    payload["identity"]["assets"][0]["raw_filename"] = "missing.raw.jsonl"
    payload["capture_sha256"] = module._digest(module._canonical_json_bytes(payload["identity"]))
    report = tmp_path / f"prospective-direct-1h-capture.{payload['capture_sha256']}.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="artifact_missing"):
        module.validate_prospective_direct_capture(report, config, ROOT)


def test_remaining_integrity_helpers(tmp_path):
    valid_csv = tmp_path / "rows.csv"
    valid_csv.write_text("a\n1\n", encoding="utf-8")
    assert module._read_csv(valid_csv) == [{"a": "1"}]
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_bytes(b"\xff")
    with pytest.raises(MarketDataError, match="invalid_csv"):
        module._read_csv(bad_csv)
    with pytest.raises(MarketDataError, match="invalid_json"):
        module._load_json(tmp_path / "missing.json")
    assert module._canonical_json_bytes({"a": 1}) == b'{"a":1}'
    assert b'"a": 1' in module._pretty_json_bytes({"a": 1})
    assert len(module._digest(b"x")) == 64
    output = module._reports_output(ROOT, "reports/prospective-direct-helper")
    assert output.is_dir()
