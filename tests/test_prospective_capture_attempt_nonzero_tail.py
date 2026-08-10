from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import crypto_bot.market.prospective_capture_attempt_evidence_adapter as adapter
import crypto_bot.market.prospective_capture_attempt_receipt_chain as chain
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / (
    "reports/prospective-capture-attempt-journal/"
    "prospective-capture-attempt-journal.c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da.json"
)
CHAIN_CONFIG = ROOT / chain.DEFAULT_CONFIG_FILENAME
ADAPTER_CONFIG = ROOT / adapter.DEFAULT_CONFIG_FILENAME
TICKET_SHA = "ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3"
JOURNAL_SHA = "c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da"
REQUEST_POLICY = "6affbc2714a723562402cae7a19dd9a7332884c29370070e1177a3ae5d304a84"


@pytest.fixture
def tail_workspaces() -> tuple[Path, Path]:
    first = ROOT / "reports/prospective-capture-attempt-receipt-chain-nonzero-tests"
    second = ROOT / "reports/prospective-capture-attempt-receipt-chain-nonzero-tests-2"
    for path in (first, second):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
    try:
        yield first, second
    finally:
        for path in (first, second):
            shutil.rmtree(path, ignore_errors=True)


def _receipt(number: int, outcome: str, previous: str | None = None) -> dict[str, object]:
    response = outcome != "transport_failed"
    passed = outcome == "validation_passed"
    return {
        "schema_version": 1,
        "receipt_policy_id": chain.POLICY_ID,
        "journal_contract_sha256": JOURNAL_SHA,
        "admission_ticket_sha256": TICKET_SHA,
        "epoch_ordinal": 2,
        "attempt_number": number,
        "previous_receipt_sha256": previous,
        "attempt_started_at": f"2026-08-16T10:{number:02d}:00Z",
        "outcome": outcome,
        "request_policy_sha256": REQUEST_POLICY,
        "response_received": response,
        "response_sha256": "a" * 64 if response else None,
        "snapshot_marker_sha256": "b" * 64 if passed else None,
        "snapshot_identity": "b" * 64 if passed else None,
        "validator_status": "passed" if passed else ("failed" if response else "not_run"),
        "failure_code": None if passed else ("validation_failed" if response else "transport_timeout"),
        "accepted": passed,
        "snapshot_marker_path": "synthetic-snapshot.json" if passed else None,
    }


def _write(path: Path, value: dict[str, object]) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def test_nonzero_retry_open_marker_replays_and_exposes_tail(tail_workspaces: tuple[Path, Path]) -> None:
    first_dir, _second_dir = tail_workspaces
    first_receipt = _receipt(1, "transport_failed")
    first_path = _write(first_dir / "attempt-1.json", first_receipt)
    result = chain.audit_prospective_capture_attempt_receipt_chain(
        JOURNAL, [first_path], CHAIN_CONFIG, first_dir
    )
    report_path = Path(result.export_paths["report"])
    report, receipts = chain.load_validated_capture_attempt_receipts(report_path)
    assert report["chain_status"] == "retry_open"
    assert report["receipt_count"] == 1
    assert report["next_attempt_number"] == 2
    assert report["next_attempt_permitted"] is True
    assert report["identity"]["receipt_order"] == [chain._receipt_sha(first_receipt)]
    assert receipts == [first_receipt]


def test_adapter_continues_nonzero_tail_and_replays_chain_two(
    tail_workspaces: tuple[Path, Path],
) -> None:
    first_dir, second_dir = tail_workspaces
    first_receipt = _receipt(1, "transport_failed")
    first_path = _write(first_dir / "attempt-1.json", first_receipt)
    first_result = chain.audit_prospective_capture_attempt_receipt_chain(
        JOURNAL, [first_path], CHAIN_CONFIG, first_dir
    )
    parent = Path(first_result.export_paths["report"])
    evidence = {
        "schema_version": 1,
        "evidence_kind": "snapshot_validation_failure",
        "attempt_started_at": "2026-08-16T10:30:00Z",
        "request_policy_sha256": REQUEST_POLICY,
        "failure_code": "fixture_validation_failure",
        "exception_class": None,
        "response_artifact_path": "reports/prospective-capture-attempt-chain-inputs/response.raw",
        "snapshot_marker_path": None,
    }
    input_dir = ROOT / "reports/prospective-capture-attempt-chain-inputs"
    if input_dir.exists():
        shutil.rmtree(input_dir)
    input_dir.mkdir(parents=True)
    try:
        (input_dir / "response.raw").write_bytes(b"retry response")
        evidence_path = _write(input_dir / "attempt-2-evidence.json", evidence)
        materialized = adapter.materialize_prospective_capture_attempt_receipt(
            parent, evidence_path, ADAPTER_CONFIG, second_dir
        )
        second_receipt = json.loads(Path(materialized.export_paths["receipt"]).read_text(encoding="utf-8"))
        assert second_receipt["attempt_number"] == 2
        assert second_receipt["previous_receipt_sha256"] == chain._receipt_sha(first_receipt)
        assert second_receipt["outcome"] == "validation_failed"

        second_path = _write(second_dir / "attempt-2.json", second_receipt)
        chain_two_dir = ROOT / "reports/prospective-capture-attempt-receipt-chain-nonzero-tests-chain2"
        if chain_two_dir.exists():
            shutil.rmtree(chain_two_dir)
        chain_two_dir.mkdir(parents=True)
        try:
            chain_two = chain.audit_prospective_capture_attempt_receipt_chain(
                JOURNAL, [first_path, second_path], CHAIN_CONFIG, chain_two_dir
            )
            validated = chain.validate_capture_attempt_receipt_chain(chain_two.export_paths["report"])
            assert validated["chain_status"] == "retry_open"
            assert validated["receipt_count"] == 2
            assert validated["next_attempt_number"] == 3
            assert validated["identity"]["receipt_order"][0] == chain._receipt_sha(first_receipt)
        finally:
            shutil.rmtree(chain_two_dir, ignore_errors=True)
    finally:
        shutil.rmtree(input_dir, ignore_errors=True)


def test_accepted_parent_is_not_retryable(
    tail_workspaces: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    first_dir, _second_dir = tail_workspaces
    monkeypatch.setattr(chain, "_validate_snapshot_marker", lambda *_args: None)
    accepted = _receipt(1, "validation_passed")
    path = _write(first_dir / "accepted.json", accepted)
    result = chain.audit_prospective_capture_attempt_receipt_chain(
        JOURNAL, [path], CHAIN_CONFIG, first_dir
    )
    report = chain.validate_capture_attempt_receipt_chain(result.export_paths["report"])
    assert report["chain_status"] == "accepted_closed"
    evidence = {
        "schema_version": 1,
        "evidence_kind": "transport_failure",
        "attempt_started_at": "2026-08-16T10:30:00Z",
        "request_policy_sha256": REQUEST_POLICY,
        "failure_code": "retry_forbidden",
        "exception_class": None,
        "response_artifact_path": None,
        "snapshot_marker_path": None,
    }
    evidence_path = _write(first_dir / "forbidden.json", evidence)
    with pytest.raises(MarketDataError, match="retry-open"):
        adapter.materialize_prospective_capture_attempt_receipt(
            result.export_paths["report"], evidence_path, ADAPTER_CONFIG, first_dir
        )
