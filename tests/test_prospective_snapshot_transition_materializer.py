from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_capture_attempt_receipt_chain as chain
import crypto_bot.market.prospective_capture_window_closeout as closeout
import crypto_bot.market.prospective_snapshot_transition_admission as admission
import crypto_bot.market.prospective_snapshot_transition_materializer as materializer
import crypto_bot.prospective_epoch_closeout_rollover as rollover
from crypto_bot.errors import MarketDataError
from crypto_bot.market.okx_future_universe_archive import (
    FutureUniverseSnapshot,
    load_future_universe_archive_config,
    validate_future_universe_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
ZERO_ADMISSION = ROOT / "reports/prospective-snapshot-transition-admission/prospective-snapshot-transition-admission.f7f25cdd1d320d365c17139e3b2b3b58dc59c420cb1f0d0fd5cbec3ed35a4753.json"
ZERO_ROLLOVER = ROOT / "reports/prospective-epoch-closeout-rollover/prospective-epoch-closeout-rollover.154743d15832f92b63f9b9731c08c1ad01ea574c45aa5a9b30cbff0b53d23125.json"
ZERO_CLOSEOUT = ROOT / "reports/prospective-capture-window-closeout/prospective-capture-window-closeout.11b9d558226dc62d589fd546ab80bfadab1598a99511e0c8795bdc2058f9e06f.json"
TICKET = ROOT / "reports/prospective-epoch-capture-admission/prospective-epoch-capture-admission.ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3.json"
ASSEMBLY = ROOT / "reports/prospective-epoch-assembly/prospective-epoch-assembly.674116b95e03705b478f285012148f0143c2ef82befccda5627ce2ab901bcc79.json"
POLICY = ROOT / "reports/prospective-epoch-accumulation-policy/prospective-epoch-accumulation-policy.8f4a90632bcf73df04c7e6f44924924db682cdc8373ed04c1f726cb6ce353092.json"
MATURITY = ROOT / "reports/prospective-economic-sample-maturity/prospective-economic-sample-maturity.32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178.json"
JOURNAL = ROOT / "reports/prospective-capture-attempt-journal/prospective-capture-attempt-journal.c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da.json"
CHAIN_CONFIG = ROOT / chain.DEFAULT_CONFIG_FILENAME
CLOSEOUT_CONFIG = ROOT / closeout.DEFAULT_CONFIG_FILENAME
ROLLOVER_CONFIG = ROOT / rollover.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / admission.DEFAULT_CONFIG_FILENAME
MATERIALIZER_CONFIG = ROOT / materializer.DEFAULT_CONFIG_FILENAME
JOURNAL_SHA = "c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da"
TICKET_SHA = "ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3"
REQUEST_POLICY = "6affbc2714a723562402cae7a19dd9a7332884c29370070e1177a3ae5d304a84"


@pytest.fixture
def workspaces() -> list[Path]:
    names = (
        "prospective-snapshot-transition-materializer-tests",
        "prospective-snapshot-transition-materializer-tests-2",
        "prospective-capture-window-closeout-materializer-parent",
        "prospective-capture-attempt-receipt-chain-materializer-tests",
        "prospective-epoch-closeout-rollover-materializer-tests",
        "prospective-snapshot-transition-admission-materializer-tests",
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


def _receipt() -> dict[str, object]:
    return {
        "schema_version": 1,
        "receipt_policy_id": chain.POLICY_ID,
        "journal_contract_sha256": JOURNAL_SHA,
        "admission_ticket_sha256": TICKET_SHA,
        "epoch_ordinal": 2,
        "attempt_number": 1,
        "previous_receipt_sha256": None,
        "attempt_started_at": "2026-08-16T10:01:00Z",
        "outcome": "validation_passed",
        "request_policy_sha256": REQUEST_POLICY,
        "response_received": True,
        "response_sha256": "a" * 64,
        "snapshot_marker_sha256": "b" * 64,
        "snapshot_identity": "b" * 64,
        "validator_status": "passed",
        "failure_code": None,
        "accepted": True,
        "snapshot_marker_path": "reports/prospective-snapshot-transition-admission-materializer-tests/synthetic-snapshot.json",
    }


def _accepted_admission(workspaces: list[Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    previous = validate_future_universe_snapshot(ROOT / "reports/okx-future-universe-snapshot/okx-universe-snapshot.d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e.json", ROOT / "config.okx-future-universe-archive.example.yaml")
    current_report = copy.deepcopy(previous.report)
    current_report["snapshot_sha256"] = "b" * 64
    current_report["identity"]["received_at"] = "2026-08-16T10:30:00Z"
    current = FutureUniverseSnapshot(ROOT / "reports/prospective-snapshot-transition-admission-materializer-tests/synthetic-snapshot.json", current_report, previous.eligibility, previous.tracked, previous.raw_path)
    def fake_snapshot(path: Path, _config: Path) -> FutureUniverseSnapshot:
        return current if path.name == "synthetic-snapshot.json" else previous
    monkeypatch.setattr(chain, "_validate_snapshot_marker", lambda *_args: None)
    monkeypatch.setattr(admission, "validate_future_universe_snapshot", fake_snapshot)
    (ROOT / "reports/prospective-snapshot-transition-admission-materializer-tests/synthetic-snapshot.json").write_text("{}", encoding="utf-8")
    receipt = _write(workspaces[3] / "attempt-1.json", _receipt())
    chain_path = Path(chain.audit_prospective_capture_attempt_receipt_chain(JOURNAL, [receipt], CHAIN_CONFIG, workspaces[3]).export_paths["report"])
    evidence = _write(workspaces[2] / "closeout-evidence.json", {"schema_version": 1, "observed_at": "2026-08-16T11:00:01Z"})
    closeout_path = Path(closeout.audit_prospective_capture_window_closeout(chain_path, TICKET, evidence, CLOSEOUT_CONFIG, workspaces[2]).export_paths["report"])
    rollover_path = Path(rollover.audit_prospective_epoch_closeout_rollover(closeout_path, ASSEMBLY, POLICY, MATURITY, ROLLOVER_CONFIG, workspaces[4]).export_paths["report"])
    return Path(admission.freeze_prospective_snapshot_transition_admission(rollover_path, closeout_path, ADMISSION_CONFIG, workspaces[5]).export_paths["report"])


def test_real_pending_materialization_is_blocked_without_transition() -> None:
    result = materializer.materialize_prospective_snapshot_transition(ZERO_ADMISSION, MATERIALIZER_CONFIG, ROOT / "reports/prospective-snapshot-transition-materializer-tests")
    assert result.report["transition_materialized"] is False
    assert result.report["change_rows"] == 0
    assert result.report["transition_admission_status"] == "blocked_pending_closeout"
    assert materializer.validate_prospective_snapshot_transition_materialization(result.export_paths["report"])["transition_materialized"] is False


def test_synthetic_accepted_materializes_and_replays(
    workspaces: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    admission_path = _accepted_admission(workspaces, monkeypatch)
    monkeypatch.setattr(materializer, "validate_future_universe_snapshot", admission.validate_future_universe_snapshot)
    monkeypatch.setattr(materializer, "_validated_pair", lambda *_args: (
        validate_future_universe_snapshot(ROOT / "reports/okx-future-universe-snapshot/okx-universe-snapshot.d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e.json", ROOT / "config.okx-future-universe-archive.example.yaml"),
        FutureUniverseSnapshot(ROOT / "reports/prospective-snapshot-transition-admission-materializer-tests/synthetic-snapshot.json", {"snapshot_sha256": "b" * 64, "identity": {"received_at": "2026-08-16T10:30:00Z", "baseline_capture_sha256": "b96aa6011796c8e2f1e0d7826c0f95b9f5be15fbdf2cf38862493f3fc75da451", "policy_file_sha256": "6affbc2714a723562402cae7a19dd9a7332884c29370070e1177a3ae5d304a84"}}, validate_future_universe_snapshot(ROOT / "reports/okx-future-universe-snapshot/okx-universe-snapshot.d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e.json", ROOT / "config.okx-future-universe-archive.example.yaml").eligibility, validate_future_universe_snapshot(ROOT / "reports/okx-future-universe-snapshot/okx-universe-snapshot.d7e5fd717d2ed1f90f24d0176ea14ad283611f37c7246e075133d97caf58627e.json", ROOT / "config.okx-future-universe-archive.example.yaml").tracked, Path("")),
    ))
    result = materializer.materialize_prospective_snapshot_transition(admission_path, MATERIALIZER_CONFIG, workspaces[0])
    assert result.report["transition_materialized"] is True
    assert result.report["transition_admission_status"] == "admitted"
    assert result.report["current_snapshot_identity"] == "b" * 64
    validated = materializer.validate_prospective_snapshot_transition_materialization(result.export_paths["report"])
    assert validated["change_rows"] == result.report["change_rows"]


def test_pending_cli_round_trip_and_config_guard(workspaces: list[Path]) -> None:
    completed = subprocess.run([sys.executable, "-m", "crypto_bot.cli", "materialize-prospective-snapshot-transition", "--transition-admission", str(ZERO_ADMISSION), "--config", str(MATERIALIZER_CONFIG), "--output-dir", str(workspaces[0])], cwd=ROOT, check=True, capture_output=True, text=True)
    assert "transition_materialized: false" in completed.stdout
    config = yaml.safe_load(MATERIALIZER_CONFIG.read_text(encoding="utf-8"))
    config["sample_threshold"] = 499
    bad = workspaces[0] / MATERIALIZER_CONFIG.name
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(MarketDataError, match="config mismatch"):
        materializer.materialize_prospective_snapshot_transition(ZERO_ADMISSION, bad, workspaces[0])


def test_materialization_tamper_and_path_guards(workspaces: list[Path], tmp_path: Path) -> None:
    result = materializer.materialize_prospective_snapshot_transition(ZERO_ADMISSION, MATERIALIZER_CONFIG, workspaces[0])
    report_path = Path(result.export_paths["report"])
    value = json.loads(report_path.read_text(encoding="utf-8"))
    value["transition_materialized"] = True
    report_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(MarketDataError, match="identity mismatch"):
        materializer.validate_prospective_snapshot_transition_materialization(report_path)
    with pytest.raises(ValueError, match="output"):
        materializer.materialize_prospective_snapshot_transition(ZERO_ADMISSION, MATERIALIZER_CONFIG, tmp_path / "outside")


def test_materializer_low_level_guards(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="filename"):
        materializer.load_snapshot_transition_materializer_config(tmp_path / "wrong.yaml")
    with pytest.raises(MarketDataError, match="repo root"):
        materializer._repo_root(tmp_path)
    with pytest.raises(MarketDataError, match="JSON read"):
        materializer._load_json(tmp_path / "missing.json")
    malformed = tmp_path / "malformed.json"
    malformed.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="JSON shape"):
        materializer._load_json(malformed)
    with pytest.raises(MarketDataError, match="policy semantics"):
        materializer._validate_policy({})
    assert materializer._csv_value(None) == ""
    assert materializer._csv_value(False) == "false"


def test_materializer_validation_and_helper_guards(tmp_path: Path) -> None:
    invalid = tmp_path / MATERIALIZER_CONFIG.name
    invalid.write_text("[", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        materializer.load_snapshot_transition_materializer_config(invalid)
    with pytest.raises(MarketDataError, match="report path"):
        materializer.validate_prospective_snapshot_transition_materialization(ROOT / "README.md")
    with pytest.raises(MarketDataError, match="admission reference"):
        materializer._locate_admission_report(ROOT, "bad", "bad.json")
    with pytest.raises(MarketDataError, match="timestamp"):
        materializer._snapshot_received_at(FutureUniverseSnapshot(Path(""), {"identity": {"received_at": "bad"}}, (), (), Path("")))
    with pytest.raises(MarketDataError, match="timezone"):
        materializer._snapshot_received_at(FutureUniverseSnapshot(Path(""), {"identity": {"received_at": "2026-08-16T10:00:00"}}, (), (), Path("")))
    with pytest.raises(MarketDataError, match="gate mismatch"):
        materializer._derive_materialization({"transition_admission_status": "unknown", "transition_admission_eligible": False}, ROOT, load_future_universe_archive_config(ROOT / "config.okx-future-universe-archive.example.yaml", ROOT), materializer.load_snapshot_transition_materializer_config(MATERIALIZER_CONFIG))
    with pytest.raises(MarketDataError, match="closeout reference"):
        materializer._validated_pair({"identity": {}}, ROOT, rollover.SNAPSHOT_SHA, "b" * 64)
    csv_file = tmp_path / "bad.csv"
    with pytest.raises(MarketDataError, match="CSV read"):
        materializer._read_csv(csv_file, materializer.TRANSITION_FIELDS)
    csv_file.write_text("wrong\nvalue\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="CSV schema"):
        materializer._read_csv(csv_file, materializer.TRANSITION_FIELDS)
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("key,value\na,1\na,2\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="key collision"):
        materializer._read_key_values(duplicate)
    collision = tmp_path / "collision.bin"
    collision.write_bytes(b"old")
    with pytest.raises(MarketDataError, match="collision"):
        materializer._commit_bytes(collision, b"new")


def test_materializer_artifact_tamper_guards(workspaces: list[Path]) -> None:
    result = materializer.materialize_prospective_snapshot_transition(ZERO_ADMISSION, MATERIALIZER_CONFIG, workspaces[0])
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifact_path = report_path.parent / report["artifacts"]["changes"]["filename"]
    artifact_path.write_text("bad\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="artifact bytes"):
        materializer.validate_prospective_snapshot_transition_materialization(report_path)
