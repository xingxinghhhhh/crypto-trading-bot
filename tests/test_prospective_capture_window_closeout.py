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
CONFIG = ROOT / closeout.DEFAULT_CONFIG_FILENAME
CHAIN_CONFIG = ROOT / chain.DEFAULT_CONFIG_FILENAME
JOURNAL = ROOT / (
    "reports/prospective-capture-attempt-journal/"
    "prospective-capture-attempt-journal.c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da.json"
)
JOURNAL_SHA = "c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da"
TICKET_SHA = "ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3"
REQUEST_POLICY = "6affbc2714a723562402cae7a19dd9a7332884c29370070e1177a3ae5d304a84"


@pytest.fixture
def workspaces() -> list[Path]:
    paths = [
        ROOT / "reports/prospective-capture-window-closeout-tests",
        ROOT / "reports/prospective-capture-window-closeout-tests-2",
        ROOT / "reports/prospective-capture-attempt-receipt-chain-closeout-tests",
    ]
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


def _evidence(observed_at: str) -> dict[str, object]:
    return {"schema_version": 1, "observed_at": observed_at}


def _receipt(outcome: str = "transport_failed") -> dict[str, object]:
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
        "validator_status": "passed" if passed else ("failed" if outcome == "validation_failed" else "not_run"),
        "failure_code": None if passed else ("validation_failed" if outcome == "validation_failed" else "transport_timeout"),
        "accepted": passed,
        "snapshot_marker_path": "synthetic-snapshot.json" if passed else None,
    }


def _chain_report(workspace: Path, outcome: str = "transport_failed") -> Path:
    receipt_path = _write(workspace / "attempt-1.json", _receipt(outcome))
    if outcome == "validation_passed":
        # The marker is intentionally validated through the existing chain seam
        # in the accepted-tail test, where the validator is monkeypatched.
        pass
    result = chain.audit_prospective_capture_attempt_receipt_chain(
        JOURNAL, [receipt_path], CHAIN_CONFIG, workspace
    )
    return Path(result.export_paths["report"])


def test_zero_pre_window_is_pending_and_validated(workspaces: list[Path]) -> None:
    output = workspaces[0]
    evidence = _write(output / "pre-window.json", _evidence("2026-08-10T12:00:00Z"))
    result = closeout.audit_prospective_capture_window_closeout(
        ZERO_CHAIN, TICKET, evidence, CONFIG, output
    )
    assert result.report["closeout_eligible"] is False
    assert result.report["derived_final_state"] == "pending_window_end"
    assert result.report["retry_permitted"] is True
    validated = closeout.validate_capture_window_closeout(result.export_paths["report"])
    assert validated["receipt_count"] == 0
    assert validated["derived_final_state"] == "pending_window_end"
    assert validated["network_activity_performed"] is False


def test_zero_at_window_end_is_missed_without_backfill(workspaces: list[Path]) -> None:
    evidence = _write(workspaces[0] / "at-end.json", _evidence("2026-08-16T11:00:00Z"))
    result = closeout.audit_prospective_capture_window_closeout(
        ZERO_CHAIN, TICKET, evidence, CONFIG, workspaces[0]
    )
    assert result.report["derived_final_state"] == "missed_no_backfill"
    assert result.report["closeout_eligible"] is True
    assert result.report["retry_permitted"] is False
    assert result.report["accepted_snapshot_identity"] is None
    assert closeout.validate_capture_window_closeout(result.export_paths["report"])["retry_permitted"] is False


def test_retry_open_post_window_is_missed(workspaces: list[Path]) -> None:
    chain_report = _chain_report(workspaces[2])
    evidence = _write(workspaces[0] / "retry-post-window.json", _evidence("2026-08-16T11:00:01Z"))
    result = closeout.audit_prospective_capture_window_closeout(
        chain_report, TICKET, evidence, CONFIG, workspaces[0]
    )
    assert result.report["replayed_chain_status"] == "retry_open"
    assert result.report["derived_final_state"] == "missed_no_backfill"
    assert result.report["accepted_attempt_count"] == 0
    closeout.validate_capture_window_closeout(result.export_paths["report"])


def test_accepted_chain_remains_accepted_closed(
    workspaces: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chain, "_validate_snapshot_marker", lambda *_args: None)
    chain_report = _chain_report(workspaces[2], "validation_passed")
    evidence = _write(workspaces[0] / "accepted-post-window.json", _evidence("2026-08-16T11:00:01Z"))
    result = closeout.audit_prospective_capture_window_closeout(
        chain_report, TICKET, evidence, CONFIG, workspaces[0]
    )
    assert result.report["replayed_chain_status"] == "accepted_closed"
    assert result.report["derived_final_state"] == "accepted_closed"
    assert result.report["retry_permitted"] is False
    assert result.report["accepted_snapshot_identity"] == "b" * 64
    closeout.validate_capture_window_closeout(result.export_paths["report"])


def test_deterministic_outputs_and_cli_round_trip(workspaces: list[Path]) -> None:
    evidence = _write(workspaces[0] / "cli-evidence.json", _evidence("2026-08-16T11:00:00Z"))
    first = closeout.audit_prospective_capture_window_closeout(
        ZERO_CHAIN, TICKET, evidence, CONFIG, workspaces[0]
    )
    second = closeout.audit_prospective_capture_window_closeout(
        ZERO_CHAIN, TICKET, evidence, CONFIG, workspaces[1]
    )
    assert first.report["closeout_sha256"] == second.report["closeout_sha256"]
    for key in ("state", "dependencies", "constraints", "report"):
        assert Path(first.export_paths[key]).read_bytes() == Path(second.export_paths[key]).read_bytes()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "audit-prospective-capture-window-closeout",
            "--receipt-chain",
            str(ZERO_CHAIN),
            "--admission-ticket",
            str(TICKET),
            "--closeout-evidence",
            str(evidence),
            "--config",
            str(CONFIG),
            "--output-dir",
            str(workspaces[1]),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "derived_final_state: missed_no_backfill" in completed.stdout


def test_fail_closed_for_manual_override_and_drift(
    workspaces: list[Path], tmp_path: Path
) -> None:
    override = workspaces[0] / "override.json"
    override.write_text(
        json.dumps({"schema_version": 1, "observed_at": "2026-08-16T11:00:00Z", "final_state": "missed_no_backfill"}),
        encoding="utf-8",
    )
    with pytest.raises(MarketDataError, match="evidence schema"):
        closeout.audit_prospective_capture_window_closeout(
            ZERO_CHAIN, TICKET, override, CONFIG, workspaces[0]
        )
    drift = workspaces[0] / CONFIG.name
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["sample_threshold"] = 499
    drift.write_text(yaml.safe_dump(config), encoding="utf-8")
    evidence = _write(workspaces[0] / "drift-evidence.json", _evidence("2026-08-16T11:00:00Z"))
    with pytest.raises(MarketDataError, match="config mismatch"):
        closeout.audit_prospective_capture_window_closeout(
            ZERO_CHAIN, TICKET, evidence, drift, workspaces[0]
        )
    with pytest.raises(ValueError, match="output"):
        closeout.audit_prospective_capture_window_closeout(
            ZERO_CHAIN, TICKET, evidence, CONFIG, tmp_path / "outside"
        )


def test_scalar_and_path_guards(workspaces: list[Path], tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="filename"):
        closeout.load_capture_window_closeout_config(workspaces[0] / "wrong.yaml")
    with pytest.raises(MarketDataError, match="timestamp invalid"):
        closeout._parse_utc("invalid")
    with pytest.raises(MarketDataError, match="timezone-aware"):
        closeout._parse_utc("2026-08-16T11:00:00")
    with pytest.raises(MarketDataError, match="repo root"):
        closeout._repo_root(tmp_path)
