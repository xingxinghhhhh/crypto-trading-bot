from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_capture_attempt_receipt_chain as chain
import crypto_bot.market.prospective_capture_window_closeout as closeout
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
JOURNAL_SHA = "c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da"
TICKET_SHA = "ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3"
REQUEST_POLICY = "6affbc2714a723562402cae7a19dd9a7332884c29370070e1177a3ae5d304a84"


@pytest.fixture
def workspaces() -> list[Path]:
    names = (
        "prospective-epoch-closeout-rollover-tests",
        "prospective-epoch-closeout-rollover-tests-2",
        "prospective-capture-window-closeout-rollover-parent",
        "prospective-capture-attempt-receipt-chain-rollover-tests",
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


def _evidence(path: Path, observed_at: str) -> Path:
    return _write(path, {"schema_version": 1, "observed_at": observed_at})


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
        "snapshot_marker_path": "synthetic-snapshot.json" if passed else None,
    }


def _closeout_report(workspaces: list[Path], observed_at: str, outcome: str | None = None) -> Path:
    parent = workspaces[2]
    if outcome is None:
        chain_path = ZERO_CHAIN
    else:
        receipt_path = _write(parent / "attempt-1.json", _receipt(outcome))
        chain_result = chain.audit_prospective_capture_attempt_receipt_chain(
            JOURNAL,
            [receipt_path],
            CHAIN_CONFIG,
            workspaces[3],
        )
        chain_path = Path(chain_result.export_paths["report"])
    evidence = _evidence(parent / f"evidence-{observed_at.replace(':', '')}.json", observed_at)
    result = closeout.audit_prospective_capture_window_closeout(
        chain_path, TICKET, evidence, CLOSEOUT_CONFIG, parent
    )
    return Path(result.export_paths["report"])


def test_pending_closeout_holds_and_is_replayable(workspaces: list[Path]) -> None:
    closeout_report = _closeout_report(workspaces, "2026-08-10T12:00:00Z")
    result = rollover.audit_prospective_epoch_closeout_rollover(
        closeout_report, ASSEMBLY, POLICY, MATURITY, ROLLOVER_CONFIG, workspaces[0]
    )
    assert result.report["action"] == "hold"
    assert result.report["next_epoch_ordinal"] == 2
    assert result.report["next_window_start"] is None
    assert result.report["epoch_2_closed_without_sample"] is False
    validated = rollover.validate_prospective_epoch_closeout_rollover(result.export_paths["report"])
    assert validated["action"] == "hold"


def test_missed_closeout_rolls_zero_sample_to_next_fixed_window(workspaces: list[Path]) -> None:
    closeout_report = _closeout_report(workspaces, "2026-08-16T11:00:00Z")
    first = rollover.audit_prospective_epoch_closeout_rollover(
        closeout_report, ASSEMBLY, POLICY, MATURITY, ROLLOVER_CONFIG, workspaces[0]
    )
    second = rollover.audit_prospective_epoch_closeout_rollover(
        closeout_report, ASSEMBLY, POLICY, MATURITY, ROLLOVER_CONFIG, workspaces[1]
    )
    assert first.report["action"] == "rollover_next_window"
    assert first.report["derived_final_state"] == "missed_no_backfill"
    assert first.report["epoch_2_closed_without_sample"] is True
    assert first.report["sample_credit"] == 0
    assert first.report["new_samples_counted"] == 0
    assert first.report["next_epoch_ordinal"] == 3
    assert first.report["next_window_start"] == "2026-08-23T10:00:00Z"
    assert first.report["next_window_end"] == "2026-08-23T11:00:00Z"
    assert first.report["latest_accepted_snapshot_identity"] == rollover.SNAPSHOT_SHA
    assert first.report["next_segment_start"] == rollover.SEGMENT_START
    assert first.report["rollover_sha256"] == second.report["rollover_sha256"]
    rollover.validate_prospective_epoch_closeout_rollover(first.export_paths["report"])


def test_accepted_closeout_is_transition_only(
    workspaces: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chain, "_validate_snapshot_marker", lambda *_args: None)
    closeout_report = _closeout_report(workspaces, "2026-08-16T11:00:01Z", "validation_passed")
    result = rollover.audit_prospective_epoch_closeout_rollover(
        closeout_report, ASSEMBLY, POLICY, MATURITY, ROLLOVER_CONFIG, workspaces[0]
    )
    assert result.report["action"] == "transition_eligible"
    assert result.report["next_epoch_ordinal"] == 2
    assert result.report["epoch_2_closed_without_sample"] is False
    assert result.report["latest_accepted_snapshot_identity"] == "b" * 64
    with pytest.raises(MarketDataError, match="identity mismatch"):
        tampered = Path(result.export_paths["report"])
        value = json.loads(tampered.read_text(encoding="utf-8"))
        value["action"] = "rollover_next_window"
        tampered.write_text(json.dumps(value), encoding="utf-8")
        rollover.validate_prospective_epoch_closeout_rollover(tampered)


def test_cli_round_trip_and_manual_config_override_rejected(workspaces: list[Path]) -> None:
    closeout_report = _closeout_report(workspaces, "2026-08-16T11:00:00Z")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "audit-prospective-epoch-closeout-rollover",
            "--closeout-report",
            str(closeout_report),
            "--assembly-report",
            str(ASSEMBLY),
            "--accumulation-policy",
            str(POLICY),
            "--sample-maturity",
            str(MATURITY),
            "--config",
            str(ROLLOVER_CONFIG),
            "--output-dir",
            str(workspaces[0]),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "action: rollover_next_window" in completed.stdout
    config_path = workspaces[0] / ROLLOVER_CONFIG.name
    config = yaml.safe_load(ROLLOVER_CONFIG.read_text(encoding="utf-8"))
    config["missed_epoch_sample_credit"] = 1
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(MarketDataError, match="config mismatch"):
        rollover.audit_prospective_epoch_closeout_rollover(
            closeout_report, ASSEMBLY, POLICY, MATURITY, config_path, workspaces[0]
        )


def test_parent_and_path_guards(workspaces: list[Path], tmp_path: Path) -> None:
    closeout_report = _closeout_report(workspaces, "2026-08-16T11:00:00Z")
    with pytest.raises(ValueError, match="output"):
        rollover.audit_prospective_epoch_closeout_rollover(
            closeout_report, ASSEMBLY, POLICY, MATURITY, ROLLOVER_CONFIG, tmp_path / "outside"
        )
    with pytest.raises(ValueError, match="filename"):
        rollover.load_closeout_rollover_config(workspaces[0] / "wrong.yaml")
    with pytest.raises(MarketDataError, match="timestamp invalid"):
        rollover._parse_utc("bad")
    with pytest.raises(MarketDataError, match="repo root"):
        rollover._repo_root(tmp_path)
