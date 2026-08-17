from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_evidence_operations_snapshot as module


ROOT = Path(__file__).resolve().parents[1]
ASSEMBLY = ROOT / "reports/prospective-epoch-assembly/prospective-epoch-assembly.674116b95e03705b478f285012148f0143c2ef82befccda5627ce2ab901bcc79.json"
MATURITY = ROOT / "reports/prospective-economic-sample-maturity/prospective-economic-sample-maturity.32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178.json"
AUTHORIZATION = ROOT / "reports/prospective-direct-1h-segment-append-authorization-smoke/prospective-direct-1h-segment-append-authorization.272c9ac3b5da943372af574d050b4ccd15cb6817c537fb6630bf896f2463e688.json"
READINESS = ROOT / "reports/prospective-economic-readiness/prospective-economic-readiness.0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b.json"
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def _build(output: Path) -> module.OperationsSnapshotResult:
    return module.build_prospective_evidence_operations_snapshot(
        ASSEMBLY, MATURITY, AUTHORIZATION, READINESS, CONFIG, output
    )


def test_real_baseline_is_blocked_and_replayable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module,
        "_reports_output",
        lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)),
    )
    result = _build(tmp_path / "one")
    report = result.report
    assert report["governed_stage"] == "awaiting_real_membership_epoch_progress"
    assert report["blocking_gate"] == "membership_epoch_progress"
    assert report["next_legal_action"] == "await_real_membership_epoch_progress"
    assert report["capture_evidence_available"] is False
    assert report["segment_candidate_available"] is False
    assert report["append_authorization_ready"] is False
    assert report["current_samples"] == 160
    assert report["sample_threshold"] == 500
    assert report["remaining_samples"] == 340
    assert report["sample_maturity_met"] is False
    assert report["economic_authorized"] is False
    assert report["pnl_authorized"] is False
    assert report["paper_authorized"] is False
    assert report["live_authorized"] is False
    assert report["state_changed"] is False
    assert report["network_activity_performed"] is False
    assert report["new_samples_counted"] == 0


def test_public_validator_replays_content_addressed_report() -> None:
    result = module.build_prospective_evidence_operations_snapshot(
        ASSEMBLY,
        MATURITY,
        AUTHORIZATION,
        READINESS,
        CONFIG,
        ROOT / "reports/prospective-evidence-operations-snapshot",
    )
    replayed = module.validate_prospective_evidence_operations_snapshot(result.export_paths["report"])
    assert replayed["snapshot_sha256"] == result.report["snapshot_sha256"]


def test_reducer_does_not_skip_sample_gate() -> None:
    state = {
        "epoch_state": "segment_chain_appended",
        "next_epoch_ordinal": 2,
        "capture_evidence_available": True,
        "membership_epoch_closed": True,
        "segment_candidate_available": True,
        "append_authorization_ready": True,
        "authoritative_write_authorized": True,
        "current_samples": 160,
        "sample_threshold": 500,
        "remaining_samples": 340,
        "sample_maturity_met": False,
        "economic_inputs_structurally_ready": True,
        "economic_authorized": False,
        "pnl_authorized": False,
        "readiness_ready": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    reduced = module.reduce_operations_state(state)
    assert reduced == {
        "governed_stage": "awaiting_sample_maturity",
        "blocking_gate": "sample_maturity",
        "blocking_reason": "closed interval sample threshold is unmet",
        "next_legal_action": "await_sample_maturity",
    }


def test_maturity_is_not_profitability_or_readiness() -> None:
    state = {
        "epoch_state": "segment_chain_appended",
        "membership_epoch_closed": True,
        "capture_evidence_available": True,
        "segment_candidate_available": True,
        "append_authorization_ready": True,
        "authoritative_write_authorized": True,
        "sample_maturity_met": True,
        "economic_inputs_structurally_ready": True,
        "economic_authorized": False,
        "pnl_authorized": False,
        "readiness_ready": False,
        "paper_authorized": False,
        "live_authorized": False,
    }
    reduced = module.reduce_operations_state(state)
    assert reduced["governed_stage"] == "awaiting_economic_authorization"
    assert reduced["next_legal_action"] == "await_economic_authorization"


def test_reducer_reports_terminal_projection() -> None:
    state = {
        "epoch_state": "maturity_counted",
        "membership_epoch_closed": True,
        "capture_evidence_available": True,
        "segment_candidate_available": True,
        "append_authorization_ready": True,
        "authoritative_write_authorized": True,
        "sample_maturity_met": True,
        "economic_inputs_structurally_ready": True,
        "economic_authorized": True,
        "pnl_authorized": True,
        "readiness_ready": True,
        "paper_authorized": True,
        "live_authorized": True,
    }
    assert module.reduce_operations_state(state)["next_legal_action"] == "no_legal_action"


def test_normalization_rejects_parent_sample_drift() -> None:
    with pytest.raises(module.MarketDataError):
        module.normalize_operations_state(
            {"current_state": "awaiting_capture_window", "market_segment_end_resolved": False},
            {"unique_closed_interval_count": 159, "remaining_closed_interval_count": 341},
            {"sample_threshold": 500, "current_samples": 160, "remaining_samples": 340},
            {},
            {"sample_threshold": 500},
        )


def test_snapshot_tamper_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module,
        "_reports_output",
        lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)),
    )
    result = _build(tmp_path / "one")
    report_path = Path(result.export_paths["report"])
    tampered = json.loads(report_path.read_text(encoding="utf-8"))
    tampered["next_legal_action"] = "append_authorized_segment"
    report_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_snapshot(report_path)


def test_two_output_directories_are_byte_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module,
        "_reports_output",
        lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)),
    )
    first = _build(tmp_path / "one")
    second = _build(tmp_path / "two")
    assert first.report["snapshot_sha256"] == second.report["snapshot_sha256"]
    for key in first.export_paths:
        left = Path(first.export_paths[key])
        right = Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()


@pytest.mark.parametrize("field,value", [("sample_threshold", 499), ("manual_stage_override_prohibited", False)])
def test_config_drift_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config[field] = value
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, module.MarketDataError)):
        module.load_operations_snapshot_config(path)


def test_output_escape_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        module._reports_output(ROOT, tmp_path)


def test_low_level_helpers_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(module.MarketDataError):
        module._load_json(tmp_path / "missing.json")
    with pytest.raises(module.MarketDataError):
        module._resolve_parent(ROOT, {"filename": "x.json", "sha256": "x"}, "epoch_assembly")
    assert module._csv_value({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    path = tmp_path / "collision"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")
