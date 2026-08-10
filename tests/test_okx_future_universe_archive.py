from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.market.okx_future_universe_archive as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.okx-future-universe-archive.example.yaml"
BASELINE = ROOT / "reports" / "okx-universe-capture" / (
    "okx-universe-capture.b96aa6011796c8e2f1e0d7826c0f95b9f5be15fbdf2cf38862493f3fc75da451.json"
)
CURRENT = next(
    path for path in (ROOT / "reports" / "okx-future-universe-snapshot").glob("*.json")
    if not path.name.endswith(".raw.json")
)


def test_config_and_real_snapshot_validate():
    config = module.load_future_universe_archive_config(CONFIG, ROOT)
    assert config["tracked_inst_ids"] == ["BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT"]
    snapshot = module.validate_future_universe_snapshot(CURRENT, CONFIG)
    assert snapshot.report["future_only_evidence"] is True
    assert len(snapshot.tracked) == 6


def test_config_rejects_wrong_name_and_snapshot_tamper(tmp_path):
    wrong = tmp_path / "wrong.yaml"
    wrong.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="filename"):
        module.load_future_universe_archive_config(wrong)
    config = module.load_future_universe_archive_config(CONFIG, ROOT)
    tampered = tmp_path / CURRENT.name
    payload = json.loads(CURRENT.read_text(encoding="utf-8"))
    payload["identity"]["automatic_replacement"] = True
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.validate_future_universe_snapshot(tampered, CONFIG)
    assert config["automatic_replacement"] is False


def test_tracked_status_ignores_selection_only_exclusion():
    decision = {
        "state": "live",
        "exclusion_reasons": "existing_base_exclusion",
    }
    assert module._tracked_status(decision) == "retained"
    decision["exclusion_reasons"] = "continuous_start_after_history_start"
    assert module._tracked_status(decision) == "became_ineligible"
    decision["state"] = "suspended"
    assert module._tracked_status(decision) == "no_longer_live"


def test_changes_normalize_legacy_booleans_and_strings():
    before = ({"inst_id": "BTC-USDT", "eligible": False, "exclusion_reasons": "", "state": "live"},)
    after = ({"inst_id": "BTC-USDT", "eligible": "false", "exclusion_reasons": "", "state": "live"},)
    assert module._changes(before, after) == []
    after = ({"inst_id": "BTC-USDT", "eligible": "true", "exclusion_reasons": "", "state": "live"},)
    assert module._changes(before, after)[0]["change_type"] == "eligibility_changed"


def test_capture_writes_future_snapshot_with_fixed_time(monkeypatch, tmp_path):
    config = module.load_future_universe_archive_config(CONFIG, ROOT)
    decisions = [{
        "inst_id": "BTC-USDT", "base_ccy": "BTC", "quote_ccy": "USDT", "inst_type": "SPOT",
        "state": "live", "rule_type": "normal", "inst_category": "1", "list_time": "1",
        "cont_td_sw_time": "", "effective_continuous_start": "2020-01-01T00:00:00+00:00",
        "eligible": True, "exclusion_reasons": "existing_base_exclusion", "selection_sha256": "x", "selected_rank": "",
    }]
    fake_baseline = SimpleNamespace(capture={"capture_sha256": config["baseline_capture_sha256"], "identity": {"snapshot": {"received_at": config["baseline_snapshot_received_at"]}}})
    monkeypatch.setattr(module, "validate_okx_universe_capture", lambda *_args: fake_baseline)
    monkeypatch.setattr(module, "_validate_baseline", lambda *_args: None)
    monkeypatch.setattr(module, "_load_policy", lambda *_args: {})
    monkeypatch.setattr(module, "fetch_okx_public_instruments_snapshot", lambda *_args: b"raw")
    monkeypatch.setattr(module, "evaluate_okx_public_instrument_snapshot", lambda *_args: (decisions, []))
    output = tmp_path / "reports" / "snapshot"
    output.mkdir(parents=True)
    monkeypatch.setattr(module, "_reports_output", lambda *_args: output)
    result = module.capture_okx_future_universe_snapshot(
        BASELINE, CONFIG, output, received_at=datetime(2026, 8, 9, tzinfo=timezone.utc)
    )
    assert result.report["future_only_evidence"] is True
    assert Path(result.export_paths["report"]).is_file()


def test_transition_replays_baseline_and_current(monkeypatch, tmp_path):
    output = tmp_path / "reports" / "transition"
    output.mkdir(parents=True)
    monkeypatch.setattr(module, "_reports_output", lambda *_args: output)
    result = module.audit_okx_future_universe_transition(BASELINE, CURRENT, CONFIG, output)
    assert result.report["future_only_evidence"] is True
    assert result.report["automatic_replacement"] is False
    assert Path(result.export_paths["report"]).is_file()


def test_future_snapshot_received_at_must_follow_baseline():
    with pytest.raises(ValueError, match="timezone-aware"):
        module._iso(datetime(2026, 8, 9))


def test_output_path_escape_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="inside reports"):
        module._reports_output(ROOT, tmp_path)


def test_tracked_rows_handles_disappeared_and_transition_statuses():
    rows = module._tracked_rows([], ["BTC-USDT"], "2026-08-09T00:00:00+00:00")
    assert rows[0]["tracked_membership_status"] == "disappeared_from_snapshot"
    prior = ({"inst_id": "BTC-USDT", "tracked_membership_status": "retained"},)
    current = tuple(rows)
    assert module._transition_tracked(prior, current) == list(current)


def test_changes_reports_added_and_removed_rows():
    before = ({"inst_id": "BTC-USDT", "eligible": True, "exclusion_reasons": ""},)
    after = ({"inst_id": "ETH-USDT", "eligible": False, "exclusion_reasons": "x"},)
    changes = module._changes(before, after)
    assert [row["change_type"] for row in changes] == ["removed", "added"]
    assert changes[0]["previous_present"] is True
    assert changes[1]["current_present"] is True


def test_snapshot_helpers_fail_closed_on_bad_artifacts(tmp_path):
    with pytest.raises(MarketDataError, match="invalid_artifact_path"):
        module._sibling(tmp_path / "report.json", "../escape.csv")
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="invalid_json"):
        module._load_json(bad_json)
    missing = tmp_path / "missing.csv"
    with pytest.raises(MarketDataError, match="artifact_missing"):
        module._sibling(tmp_path / "report.json", missing.name)


def test_policy_and_baseline_guards_reject_drift(tmp_path):
    config = module.load_future_universe_archive_config(CONFIG, ROOT)
    with pytest.raises(MarketDataError, match="policy_identity"):
        module._validate_snapshot_identity({"baseline_capture_sha256": "wrong"}, config)
    escaped = tmp_path / "../outside"
    with pytest.raises(MarketDataError, match="path_escape"):
        module._repo_file(ROOT, str(escaped))


def test_csv_and_formatter_cover_boolean_and_transition_paths(tmp_path):
    payload = module._csv_bytes([{"present": True}], ("present",))
    assert payload == b"present\ntrue\n"
    capture = module.FutureUniverseResult(
        {"capture_status": "x", "snapshot_sha256": "s", "identity": {"received_at": "t", "instrument_count": 1}}, {}
    )
    assert "snapshot_sha256: s" in module.format_future_universe_result(capture)
    transition = module.FutureUniverseResult(
        {"audit_status": "x", "transition_sha256": "t", "identity": {"counts": {"change_count": 0}}}, {}
    )
    assert "change_count: 0" in module.format_future_universe_result(transition)


def test_commit_collision_is_rejected(tmp_path):
    target = tmp_path / "artifact"
    target.write_bytes(b"old")
    with pytest.raises(MarketDataError, match="content_addressed_collision"):
        module._commit_bytes(target, b"new")


def test_misc_helpers_cover_nested_values_and_safe_writes(tmp_path):
    assert module._flatten({"b": {"a": 1}, "c": True}) == {"b.a": 1, "c": True}
    assert module._csv_text(None) == ""
    valid_csv = tmp_path / "rows.csv"
    valid_csv.write_text("a\n1\n", encoding="utf-8")
    assert module._read_csv(valid_csv) == [{"a": "1"}]
    invalid_csv = tmp_path / "invalid.csv"
    invalid_csv.write_bytes(b"\xff")
    with pytest.raises(MarketDataError, match="invalid_csv"):
        module._read_csv(invalid_csv)
    with pytest.raises(MarketDataError, match="artifact_missing"):
        module._repo_file(ROOT, "does-not-exist")
    output = module._reports_output(ROOT, "reports/test-okx-future-helper")
    assert output.is_dir()


def test_remaining_integrity_guards_and_serializers(tmp_path):
    config = module.load_future_universe_archive_config(CONFIG, ROOT)
    fake = SimpleNamespace(capture={"capture_sha256": "wrong", "identity": {"snapshot": {"received_at": "old"}}})
    with pytest.raises(MarketDataError, match="baseline_mismatch"):
        module._validate_baseline(fake, BASELINE, config)
    with pytest.raises(MarketDataError, match="policy_hash_mismatch"):
        module._load_policy(ROOT, {"eligibility_policy_file": "config.okx-universe-intake.example.yaml", "eligibility_policy_file_sha256": "wrong"})
    assert module._iso(datetime(2026, 8, 9, tzinfo=timezone.utc)).endswith("+00:00")
    assert len(module._digest(b"x")) == 64
    assert module._canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    assert b'"a": 2' in module._pretty_json_bytes({"a": 2})


def test_content_addressed_commit_accepts_identical_existing_bytes(tmp_path):
    target = tmp_path / "artifact"
    target.write_bytes(b"same")
    module._commit_bytes(target, b"same")
    assert target.read_bytes() == b"same"
