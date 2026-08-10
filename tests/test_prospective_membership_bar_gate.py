from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.market.prospective_membership_bar_gate as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.prospective-membership-bar-gate.example.yaml"
TRANSITION = ROOT / "reports" / "okx-future-universe-transition-v2" / (
    "okx-universe-transition.706ba3e03bfc199ba332da1fd23cec7554ad5279c44f895b8eb302d3bfe244ef.json"
)
MAPPING = ROOT / "reports" / "okx-direct-six-1h-execution-mapping" / (
    "okx-direct-six-1h-execution-mapping.48df5c8d1d8f2ee76e45f85920ded6ce37dc25422a14e1b54626668f8aec9ddb.json"
)


def test_config_and_real_gate_inputs_validate():
    config = module.load_membership_bar_gate_config(CONFIG, ROOT)
    assert config["tracked_inst_ids"] == module.EXPECTED_INST_IDS
    validated = module.validate_okx_future_universe_transition(
        TRANSITION, ROOT / "config.okx-future-universe-archive.example.yaml"
    )
    assert len(validated.tracked) == 6


def test_real_gate_replays_to_expected_161_by_966(monkeypatch, tmp_path):
    output = tmp_path / "reports" / "gate"
    output.mkdir(parents=True)
    monkeypatch.setattr(module, "_reports_output", lambda *_args: output)
    result = module.freeze_prospective_membership_bar_gate(TRANSITION, MAPPING, CONFIG, output)
    assert result.report["identity"]["epoch"]["signal_count"] == 161
    assert result.report["identity"]["epoch"]["row_count"] == 966
    assert result.report["identity"]["epoch"]["first_signal_timestamp"] == "2026-08-02T16:00:00Z"
    assert result.report["identity"]["epoch"]["last_execution_timestamp"] == "2026-08-09T10:00:00Z"


def test_grid_boundary_helpers_are_strict():
    assert module._ceil_hour(datetime(2026, 8, 2, 15, 49, tzinfo=timezone.utc)).hour == 16
    assert module._ceil_hour(datetime(2026, 8, 2, 16, tzinfo=timezone.utc)).hour == 16
    assert module._floor_strict_hour(datetime(2026, 8, 9, 10, 22, tzinfo=timezone.utc)).hour == 10
    assert module._floor_strict_hour(datetime(2026, 8, 9, 10, tzinfo=timezone.utc)).hour == 9
    assert len(module._signal_grid(
        datetime(2026, 8, 2, 15, 49, tzinfo=timezone.utc),
        datetime(2026, 8, 9, 10, 22, tzinfo=timezone.utc),
    )) == 161


def test_signal_grid_empty_epoch_fails():
    with pytest.raises(MarketDataError, match="empty_epoch"):
        module._signal_grid(
            datetime(2026, 8, 9, 10, tzinfo=timezone.utc),
            datetime(2026, 8, 9, 10, 30, tzinfo=timezone.utc),
        )


def test_eligibility_rows_preserve_previous_state_and_no_retroactive_change():
    rows = module._eligibility_rows(
        [datetime(2026, 8, 2, 16, tzinfo=timezone.utc)],
        {"tracked_inst_ids": ["BTC-USDT"], "dataset_ids": ["dataset"]},
        {"BTC-USDT": "retained"},
        {"BTC-USDT": "became_ineligible"},
    )
    assert rows[0]["eligible_for_closed_epoch"] is True
    assert rows[0]["retroactive_change_applied"] is False
    assert rows[0]["current_snapshot_transition_state"] == "became_ineligible"


def test_transition_validator_rejects_tampering(monkeypatch, tmp_path):
    payload = json.loads(TRANSITION.read_text(encoding="utf-8"))
    payload["automatic_replacement"] = True
    tampered = tmp_path / TRANSITION.name
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "_repo_root", lambda *_args: ROOT)
    with pytest.raises(MarketDataError, match="claims_mismatch"):
        module.validate_okx_future_universe_transition(
            tampered, ROOT / "config.okx-future-universe-archive.example.yaml"
        )


def test_config_rejects_wrong_name_and_asset_order(tmp_path):
    wrong = tmp_path / "wrong.yaml"
    wrong.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="filename"):
        module.load_membership_bar_gate_config(wrong)
    config = module.load_membership_bar_gate_config(CONFIG, ROOT)
    config["tracked_inst_ids"] = list(reversed(config["tracked_inst_ids"]))
    monkey = tmp_path / module.DEFAULT_CONFIG_FILENAME
    import yaml

    monkey.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="equal"):
        module.load_membership_bar_gate_config(monkey)


def test_baseline_existing_base_exclusion_is_retained():
    baseline = SimpleNamespace(capture={"identity": {"snapshot": {"eligibility_decisions": [
        {"inst_id": "BTC-USDT", "eligible": False, "state": "live", "exclusion_reasons": "existing_base_excluded"},
        {"inst_id": "ETH-USDT", "eligible": True, "state": "live", "exclusion_reasons": ""},
    ]}}})
    assert module._baseline_membership(baseline, ["BTC-USDT", "ETH-USDT"]) == {"BTC-USDT": "retained", "ETH-USDT": "retained"}


def test_baseline_ineligible_asset_fails():
    baseline = SimpleNamespace(capture={"identity": {"snapshot": {"eligibility_decisions": [
        {"inst_id": "BTC-USDT", "eligible": False, "state": "live", "exclusion_reasons": "continuous_start_after_history_start"},
    ]}}})
    with pytest.raises(MarketDataError, match="baseline_asset_not_eligible"):
        module._baseline_membership(baseline, ["BTC-USDT"])


def test_current_transition_set_must_match():
    with pytest.raises(MarketDataError, match="tracked_set_mismatch"):
        module._current_transition_state(({"inst_id": "BTC-USDT", "tracked_membership_status": "retained"},), ["ETH-USDT"])


def test_format_and_serializers(tmp_path):
    result = module.MembershipBarGateResult({"gate_status": "x", "gate_sha256": "g", "identity": {"epoch": {"signal_count": 1, "row_count": 2}}}, {})
    text = module.format_membership_bar_gate_result(result)
    assert "gate_sha256: g" in text and "eligibility_row_count: 2" in text
    assert module._csv_bytes([{"value": True}], ("value",)) == b"value\ntrue\n"
    assert module._flatten({"b": {"a": 1}}) == {"b.a": 1}
    assert module._iso(datetime(2026, 8, 2, tzinfo=timezone.utc)) == "2026-08-02T00:00:00Z"


def test_safe_helpers_fail_closed(tmp_path):
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


def test_config_and_path_guards(tmp_path):
    with pytest.raises(FileNotFoundError):
        module.load_membership_bar_gate_config(tmp_path / module.DEFAULT_CONFIG_FILENAME)
    invalid = tmp_path / module.DEFAULT_CONFIG_FILENAME
    invalid.write_text("[", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        module.load_membership_bar_gate_config(invalid)
    nonmapping = tmp_path / module.DEFAULT_CONFIG_FILENAME
    nonmapping.write_text("- one\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        module.load_membership_bar_gate_config(nonmapping)


def test_timestamp_and_csv_error_guards(tmp_path):
    with pytest.raises(MarketDataError, match="timezone_aware"):
        module._parse_iso("2026-08-09T10:00:00")
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_bytes(b"\xff")
    with pytest.raises(MarketDataError, match="invalid_csv"):
        module._read_csv(bad_csv)


def test_freeze_rejects_noncanonical_input_paths():
    with pytest.raises(MarketDataError, match="transition_path_mismatch"):
        module.freeze_prospective_membership_bar_gate(MAPPING, MAPPING, CONFIG, "reports/x")
    with pytest.raises(MarketDataError, match="mapping_path_mismatch"):
        module.freeze_prospective_membership_bar_gate(TRANSITION, TRANSITION, CONFIG, "reports/x")


def test_transition_validator_rejects_wrong_filename_and_archive_path(monkeypatch, tmp_path):
    payload = json.loads(TRANSITION.read_text(encoding="utf-8"))
    payload["transition_sha256"] = "0" * 64
    altered = tmp_path / ("okx-universe-transition." + "0" * 64 + ".json")
    altered.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "_repo_root", lambda *_args: ROOT)
    with pytest.raises(MarketDataError, match="identity_mismatch"):
        module.validate_okx_future_universe_transition(altered, ROOT / "config.okx-future-universe-archive.example.yaml")
    with pytest.raises(MarketDataError, match="archive_config_path_mismatch"):
        module.validate_okx_future_universe_transition(TRANSITION, tmp_path / "missing.yaml")
