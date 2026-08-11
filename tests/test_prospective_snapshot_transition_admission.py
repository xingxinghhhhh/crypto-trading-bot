from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import crypto_bot.market.prospective_capture_attempt_receipt_chain as chain
import crypto_bot.market.prospective_capture_window_closeout as closeout
import crypto_bot.market.prospective_snapshot_transition_admission as admission
import crypto_bot.prospective_epoch_closeout_rollover as rollover
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
ZERO_CHAIN = ROOT / (
    "reports/prospective-capture-attempt-receipt-chain/"
    "prospective-capture-attempt-receipt-chain.3fef210901ee2ba1db387ff8468da069809f4d0c0a6914f87bedaa699b89b538.json"
)
TICKET = ROOT / (
    "reports/prospective-epoch-capture-admission/"
    "prospective-epoch-capture-admission.ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3.json"
)
ASSEMBLY = ROOT / (
    "reports/prospective-epoch-assembly/"
    "prospective-epoch-assembly.674116b95e03705b478f285012148f0143c2ef82befccda5627ce2ab901bcc79.json"
)
POLICY = ROOT / (
    "reports/prospective-epoch-accumulation-policy/"
    "prospective-epoch-accumulation-policy.8f4a90632bcf73df04c7e6f44924924db682cdc8373ed04c1f726cb6ce353092.json"
)
MATURITY = ROOT / (
    "reports/prospective-economic-sample-maturity/"
    "prospective-economic-sample-maturity.32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178.json"
)
JOURNAL = ROOT / (
    "reports/prospective-capture-attempt-journal/"
    "prospective-capture-attempt-journal.c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da.json"
)
CHAIN_CONFIG = ROOT / chain.DEFAULT_CONFIG_FILENAME
CLOSEOUT_CONFIG = ROOT / closeout.DEFAULT_CONFIG_FILENAME
ROLLOVER_CONFIG = ROOT / rollover.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / admission.DEFAULT_CONFIG_FILENAME
JOURNAL_SHA = "c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da"
TICKET_SHA = "ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3"
REQUEST_POLICY = "6affbc2714a723562402cae7a19dd9a7332884c29370070e1177a3ae5d304a84"


@pytest.fixture
def workspaces() -> list[Path]:
    names = (
        "prospective-snapshot-transition-admission-tests",
        "prospective-snapshot-transition-admission-tests-2",
        "prospective-capture-window-closeout-admission-parent",
        "prospective-capture-attempt-receipt-chain-admission-tests",
        "prospective-epoch-closeout-rollover-admission-tests",
    )
    paths = [ROOT / "reports" / name for name in names]
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True)
    try:
        yield paths
    finally:
        for path in paths:
            shutil.rmtree(path, ignore_errors=True)


def _write(path: Path, value: dict[str, object]) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _receipt(outcome: str) -> dict[str, object]:
    passed = outcome == "validation_passed"
    return {
        "schema_version": 1,
        "receipt_policy_id": chain.POLICY_ID,
        "journal_contract_sha256": JOURNAL_SHA,
        "admission_ticket_sha256": TICKET_SHA,
        "epoch_ordinal": 2,
        "attempt_number": 1,
        "previous_receipt_sha256": None,
        "attempt_started_at": "2026-08-16T10:01:00Z",
        "outcome": outcome,
        "request_policy_sha256": REQUEST_POLICY,
        "response_received": passed,
        "response_sha256": "a" * 64 if passed else None,
        "snapshot_marker_sha256": "b" * 64 if passed else None,
        "snapshot_identity": "b" * 64 if passed else None,
        "validator_status": "passed" if passed else "not_run",
        "failure_code": None if passed else "transport_timeout",
        "accepted": passed,
        "snapshot_marker_path": "reports/prospective-snapshot-transition-admission-tests/synthetic-snapshot.json" if passed else None,
    }


def _closeout_report(workspaces: list[Path], observed_at: str, outcome: str | None = None) -> Path:
    parent = workspaces[2]
    if outcome is None:
        chain_path = ZERO_CHAIN
    else:
        receipt_path = _write(workspaces[3] / "attempt-1.json", _receipt(outcome))
        chain_path = Path(chain.audit_prospective_capture_attempt_receipt_chain(JOURNAL, [receipt_path], CHAIN_CONFIG, workspaces[3]).export_paths["report"])
    evidence = _write(parent / "closeout-evidence.json", {"schema_version": 1, "observed_at": observed_at})
    return Path(closeout.audit_prospective_capture_window_closeout(chain_path, TICKET, evidence, CLOSEOUT_CONFIG, parent).export_paths["report"])


def _rollover(workspaces: list[Path], observed_at: str, outcome: str | None = None) -> tuple[Path, Path]:
    closeout_report = _closeout_report(workspaces, observed_at, outcome)
    result = rollover.audit_prospective_epoch_closeout_rollover(closeout_report, ASSEMBLY, POLICY, MATURITY, ROLLOVER_CONFIG, workspaces[4])
    return closeout_report, Path(result.export_paths["report"])


def _fake_snapshot_validator(path: Path, _config: Path) -> SimpleNamespace:
    if path.name == "synthetic-snapshot.json":
        return SimpleNamespace(report={"snapshot_sha256": "b" * 64, "identity": {"received_at": "2026-08-16T10:30:00Z"}})
    return SimpleNamespace(report={"snapshot_sha256": rollover.SNAPSHOT_SHA, "identity": {"received_at": "2026-08-09T10:22:11.263565Z"}})


def test_real_pending_is_blocked_and_replayable() -> None:
    rollover_report = ROOT / "reports/prospective-epoch-closeout-rollover/prospective-epoch-closeout-rollover.154743d15832f92b63f9b9731c08c1ad01ea574c45aa5a9b30cbff0b53d23125.json"
    closeout_report = ROOT / "reports/prospective-capture-window-closeout/prospective-capture-window-closeout.11b9d558226dc62d589fd546ab80bfadab1598a99511e0c8795bdc2058f9e06f.json"
    result = admission.freeze_prospective_snapshot_transition_admission(rollover_report, closeout_report, ADMISSION_CONFIG, ROOT / "reports/prospective-snapshot-transition-admission-tests")
    assert result.report["transition_admission_eligible"] is False
    assert result.report["transition_admission_status"] == "blocked_pending_closeout"
    assert result.report["previous_snapshot_identity"] == rollover.SNAPSHOT_SHA
    assert result.report["current_snapshot_identity"] is None
    assert result.report["transition_created"] is False
    assert admission.validate_prospective_snapshot_transition_admission(result.export_paths["report"])["transition_admission_status"] == "blocked_pending_closeout"


def test_synthetic_missed_is_blocked(workspaces: list[Path]) -> None:
    closeout_report, rollover_report = _rollover(workspaces, "2026-08-16T11:00:00Z")
    result = admission.freeze_prospective_snapshot_transition_admission(rollover_report, closeout_report, ADMISSION_CONFIG, workspaces[1])
    assert result.report["closeout_state"] == "missed_no_backfill"
    assert result.report["rollover_action"] == "rollover_next_window"
    assert result.report["transition_admission_eligible"] is False
    assert result.report["transition_admission_status"] == "blocked_missed_epoch"
    assert result.report["current_snapshot_identity"] is None


def test_synthetic_accepted_admits_only_first_snapshot(
    workspaces: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chain, "_validate_snapshot_marker", lambda *_args: None)
    monkeypatch.setattr(admission, "validate_future_universe_snapshot", _fake_snapshot_validator)
    (ROOT / "reports/prospective-snapshot-transition-admission-tests/synthetic-snapshot.json").write_text("{}", encoding="utf-8")
    closeout_report, rollover_report = _rollover(workspaces, "2026-08-16T11:00:01Z", "validation_passed")
    result = admission.freeze_prospective_snapshot_transition_admission(rollover_report, closeout_report, ADMISSION_CONFIG, workspaces[0])
    assert result.report["transition_admission_eligible"] is True
    assert result.report["transition_admission_status"] == "admitted"
    assert result.report["previous_snapshot_identity"] == rollover.SNAPSHOT_SHA
    assert result.report["current_snapshot_identity"] == "b" * 64
    assert result.report["previous_received_at"] < result.report["current_received_at"]
    assert result.report["transition_created"] is False
    assert admission.validate_prospective_snapshot_transition_admission(result.export_paths["report"])["current_snapshot_identity"] == "b" * 64


def test_accepted_snapshot_identity_and_ordering_fail_closed(
    workspaces: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chain, "_validate_snapshot_marker", lambda *_args: None)
    monkeypatch.setattr(admission, "validate_future_universe_snapshot", _fake_snapshot_validator)
    monkeypatch.setattr(
        admission,
        "_validated_accepted_snapshot",
        lambda *_args: SimpleNamespace(report={"snapshot_sha256": "b" * 64, "identity": {"received_at": "2026-08-09T10:00:00Z"}}),
    )
    (ROOT / "reports/prospective-snapshot-transition-admission-tests/synthetic-snapshot.json").write_text("{}", encoding="utf-8")
    closeout_report, rollover_report = _rollover(workspaces, "2026-08-16T11:00:01Z", "validation_passed")
    with pytest.raises(MarketDataError, match="ordering mismatch"):
        admission.freeze_prospective_snapshot_transition_admission(rollover_report, closeout_report, ADMISSION_CONFIG, workspaces[0])


def test_manual_config_override_cli_and_path_guards(workspaces: list[Path], tmp_path: Path) -> None:
    closeout_report, rollover_report = _rollover(workspaces, "2026-08-16T11:00:00Z")
    config = yaml.safe_load(ADMISSION_CONFIG.read_text(encoding="utf-8"))
    config["sample_threshold"] = 499
    bad_config = workspaces[0] / ADMISSION_CONFIG.name
    bad_config.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(MarketDataError, match="config mismatch"):
        admission.freeze_prospective_snapshot_transition_admission(rollover_report, closeout_report, bad_config, workspaces[0])
    with pytest.raises(ValueError, match="output"):
        admission.freeze_prospective_snapshot_transition_admission(rollover_report, closeout_report, ADMISSION_CONFIG, tmp_path / "outside")
    completed = subprocess.run(
        [sys.executable, "-m", "crypto_bot.cli", "freeze-prospective-snapshot-transition-admission", "--rollover-report", str(rollover_report), "--closeout-report", str(closeout_report), "--config", str(ADMISSION_CONFIG), "--output-dir", str(workspaces[1])],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "transition_admission_status: blocked_missed_epoch" in completed.stdout


def test_report_tamper_and_collision_fail_closed(workspaces: list[Path]) -> None:
    closeout_report, rollover_report = _rollover(workspaces, "2026-08-16T11:00:00Z")
    result = admission.freeze_prospective_snapshot_transition_admission(rollover_report, closeout_report, ADMISSION_CONFIG, workspaces[0])
    report_path = Path(result.export_paths["report"])
    value = json.loads(report_path.read_text(encoding="utf-8"))
    value["transition_admission_status"] = "admitted"
    report_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(MarketDataError, match="identity mismatch"):
        admission.validate_prospective_snapshot_transition_admission(report_path)


def test_low_level_guards_and_deterministic_helpers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="filename"):
        admission.load_snapshot_transition_admission_config(tmp_path / "wrong.yaml")
    invalid = tmp_path / admission.DEFAULT_CONFIG_FILENAME
    invalid.write_text("[", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        admission.load_snapshot_transition_admission_config(invalid)
    valid_copy = tmp_path / admission.DEFAULT_CONFIG_FILENAME
    valid_copy.write_text(ADMISSION_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(MarketDataError, match="config mismatch"):
        admission.load_snapshot_transition_admission_config(valid_copy, ROOT)
    with pytest.raises(MarketDataError, match="repo root"):
        admission._repo_root(tmp_path)
    with pytest.raises(MarketDataError, match="timestamp invalid"):
        admission._parse_utc("bad")
    with pytest.raises(MarketDataError, match="timezone"):
        admission._parse_utc("2026-08-16T10:00:00")
    assert admission._csv_value(None) == ""
    assert admission._csv_value(True) == "true"
    with pytest.raises(MarketDataError, match="JSON read"):
        admission._load_json(tmp_path / "missing.json")
    malformed = tmp_path / "malformed.json"
    malformed.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="JSON shape"):
        admission._load_json(malformed)
    with pytest.raises(MarketDataError, match="CSV read"):
        admission._read_csv(tmp_path / "missing.csv", admission.ADMISSION_FIELDS)
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("wrong\nvalue\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="CSV schema"):
        admission._read_csv(bad_csv, admission.ADMISSION_FIELDS)
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("key,value\na,1\na,2\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="key collision"):
        admission._read_key_values(duplicate)
    collision = tmp_path / "collision.bin"
    collision.write_bytes(b"old")
    with pytest.raises(MarketDataError, match="collision"):
        admission._commit_bytes(collision, b"new")
    with pytest.raises(MarketDataError, match="path escape"):
        admission._repo_relative_path(ROOT, "../outside.json")
    assert admission._report_dir_allowed(ROOT / "reports" / "other" / "x.json", ROOT) is False


def test_parent_and_reference_guards(tmp_path: Path) -> None:
    with pytest.raises(MarketDataError, match="rollover reference"):
        admission._locate_rollover_report(ROOT, "bad", "bad.json")
    with pytest.raises(MarketDataError, match="closeout reference"):
        admission._locate_closeout_report(ROOT, "bad", "bad.json")
    with pytest.raises(MarketDataError, match="receipt-chain reference"):
        admission._locate_chain_report(ROOT, "bad", "bad.json")
    with pytest.raises(MarketDataError, match="assembly reference"):
        admission._validated_assembly_for_rollover({}, ROOT)
    with pytest.raises(MarketDataError, match="previous snapshot"):
        admission._validate_snapshot_by_identity(ROOT, "c" * 64)
    with pytest.raises(MarketDataError, match="snapshot report"):
        admission._snapshot_received_at(SimpleNamespace(report=[]))
    assert admission._repo_relative_path(ROOT, "README.md").is_file()


def test_derive_state_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    config = admission.load_snapshot_transition_admission_config(ADMISSION_CONFIG)
    assembly = {"expected_previous_snapshot": rollover.SNAPSHOT_SHA}
    pending = {"action": "hold"}
    closeout_state = {"derived_final_state": "unexpected"}
    with pytest.raises(MarketDataError, match="closeout/action"):
        admission._derive_admission(pending, closeout_state, assembly, ROOT, config)
    with pytest.raises(MarketDataError, match="accepted identity"):
        admission._derive_admission({"action": "transition_eligible", "latest_accepted_snapshot_identity": "b" * 64}, {"derived_final_state": "accepted_closed", "accepted_snapshot_identity": None}, assembly, ROOT, config)
    with pytest.raises(MarketDataError, match="policy semantics"):
        admission._derive_admission({"action": "hold"}, {"derived_final_state": "pending_window_end"}, assembly, ROOT, {**config, "sample_threshold": 499})
    monkeypatch.setattr(admission, "validate_future_universe_snapshot", _fake_snapshot_validator)
    monkeypatch.setattr(
        admission,
        "_validated_accepted_snapshot",
        lambda *_args: SimpleNamespace(report={"snapshot_sha256": "b" * 64, "identity": {"received_at": "2026-08-09T10:00:00Z"}}),
    )
    with pytest.raises(MarketDataError, match="ordering mismatch"):
        admission._derive_admission({"action": "transition_eligible", "latest_accepted_snapshot_identity": "b" * 64}, {"derived_final_state": "accepted_closed", "accepted_snapshot_identity": "b" * 64, "identity": {}}, assembly, ROOT, config)
