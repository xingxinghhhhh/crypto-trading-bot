from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_capture_attempt_receipt_chain as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / (
    "reports/prospective-capture-attempt-journal/"
    "prospective-capture-attempt-journal.c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da.json"
)
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def _receipt(journal_sha: str, ticket_sha: str, number: int, outcome: str, previous: str | None = None) -> dict[str, object]:
    response = outcome != "transport_failed"
    passed = outcome == "validation_passed"
    return {
        "schema_version": 1,
        "receipt_policy_id": module.POLICY_ID,
        "journal_contract_sha256": journal_sha,
        "admission_ticket_sha256": ticket_sha,
        "epoch_ordinal": 2,
        "attempt_number": number,
        "previous_receipt_sha256": previous,
        "attempt_started_at": f"2026-08-16T10:{number:02d}:00Z",
        "outcome": outcome,
        "request_policy_sha256": "6affbc2714a723562402cae7a19dd9a7332884c29370070e1177a3ae5d304a84",
        "response_received": response,
        "response_sha256": "a" * 64 if response else None,
        "snapshot_marker_sha256": "b" * 64 if passed else None,
        "snapshot_identity": "b" * 64 if passed else None,
        "validator_status": "passed" if passed else ("failed" if response else "not_run"),
        "failure_code": None if passed else ("validation_failed" if response else "transport_timeout"),
        "accepted": passed,
        "snapshot_marker_path": "synthetic-snapshot.json" if passed else None,
    }


def test_zero_receipt_chain_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module,
        "_reports_output",
        lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)),
    )
    first = module.audit_prospective_capture_attempt_receipt_chain(JOURNAL, [], CONFIG, tmp_path / "one")
    second = module.audit_prospective_capture_attempt_receipt_chain(JOURNAL, [], CONFIG, tmp_path / "two")
    assert first.report["chain_sha256"] == second.report["chain_sha256"]
    assert first.report["receipt_count"] == 0
    assert first.report["chain_status"] == "awaiting_attempt"
    assert first.report["next_attempt_number"] == 1
    assert first.report["next_attempt_permitted"] is True
    for key in first.export_paths:
        left, right = Path(first.export_paths[key]), Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()


def test_failure_failure_pass_replays_and_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_validate_snapshot_marker", lambda *_args: None)
    monkeypatch.setattr(
        module,
        "_reports_output",
        lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)),
    )
    base = json.loads(JOURNAL.read_text(encoding="utf-8"))
    receipts: list[dict[str, object]] = []
    previous: str | None = None
    for number, outcome in enumerate(("transport_failed", "validation_failed", "validation_passed"), start=1):
        receipt = _receipt(base["journal_sha256"], base["ticket_sha256"], number, outcome, previous)
        receipts.append(receipt)
        previous = module._receipt_sha(receipt)
    paths = []
    for index, receipt in enumerate(receipts, start=1):
        path = tmp_path / f"receipt-{index}.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        paths.append(path)
    result = module.audit_prospective_capture_attempt_receipt_chain(
        JOURNAL, paths, CONFIG, tmp_path / "output"
    )
    assert result.report["receipt_count"] == 3
    assert result.report["chain_status"] == "accepted_closed"
    assert result.report["accepted_attempt_count"] == 1
    assert result.report["accepted_attempt_number"] == 3
    assert result.report["next_attempt_permitted"] is False
    assert result.report["accepted_snapshot_identity"] == "b" * 64


@pytest.mark.parametrize("mutation", ["gap", "previous", "after_pass", "reordered"])
def test_receipt_chain_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    monkeypatch.setattr(module, "_validate_snapshot_marker", lambda *_args: None)
    base = json.loads(JOURNAL.read_text(encoding="utf-8"))
    first = _receipt(base["journal_sha256"], base["ticket_sha256"], 1, "transport_failed")
    second = _receipt(base["journal_sha256"], base["ticket_sha256"], 2, "validation_failed", module._receipt_sha(first))
    third = _receipt(base["journal_sha256"], base["ticket_sha256"], 3, "validation_passed", module._receipt_sha(second))
    receipts: list[dict[str, object]] = [first, second, third]
    if mutation == "gap":
        receipts[1]["attempt_number"] = 3
    elif mutation == "previous":
        receipts[1]["previous_receipt_sha256"] = "c" * 64
    elif mutation == "after_pass":
        fourth = _receipt(base["journal_sha256"], base["ticket_sha256"], 4, "transport_failed", module._receipt_sha(third))
        receipts.append(fourth)
    elif mutation == "reordered":
        receipts = [second, first]
    paths = []
    for index, receipt in enumerate(receipts, start=1):
        path = tmp_path / f"receipt-{index}.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        paths.append(path)
    with pytest.raises(MarketDataError):
        module.audit_prospective_capture_attempt_receipt_chain(JOURNAL, paths, CONFIG, tmp_path / "output")


def test_config_drift_and_snapshot_policy_is_fail_closed(tmp_path: Path) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["receipt_order"] = "sorted"
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, MarketDataError)):
        module.load_receipt_chain_config(path)


def test_report_validator_accepts_real_zero_chain(tmp_path: Path) -> None:
    result = module.audit_prospective_capture_attempt_receipt_chain(
        JOURNAL, [], CONFIG, "reports/prospective-capture-attempt-receipt-chain"
    )
    report_path = Path(result.export_paths["report"])
    assert module.validate_receipt_chain_report(report_path)["chain_sha256"] == result.report["chain_sha256"]


def test_format_and_collision(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(MarketDataError):
        module._commit_bytes(path, b"different")
    result = module.ReceiptChainResult(
        {
            "contract_status": "status",
            "chain_sha256": "sha",
            "journal_contract_sha256": "journal",
            "window_start": "a",
            "window_end": "b",
            "receipt_count": 0,
            "chain_status": "awaiting_attempt",
            "next_attempt_number": 1,
            "accepted_attempt_count": 0,
        },
        {},
    )
    assert "chain_sha256: sha" in module.format_receipt_chain_result(result)


def _context() -> tuple[dict[str, object], dict[str, object], str]:
    journal = json.loads(JOURNAL.read_text(encoding="utf-8"))
    ticket_path = ROOT / (
        "reports/prospective-epoch-capture-admission/"
        "prospective-epoch-capture-admission.ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3.json"
    )
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    request_policy = ticket["identity"]["request_policy"]["request_policy_identity"]
    return journal, ticket, request_policy


def test_config_shape_and_semantic_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        module.load_receipt_chain_config(tmp_path / "wrong.yaml")

    shape_path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    shape_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(module.yaml, "safe_load", lambda _text: [])
    with pytest.raises(MarketDataError, match="shape mismatch"):
        module.load_receipt_chain_config(shape_path)
    monkeypatch.undo()

    good = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    with pytest.raises(MarketDataError, match="semantics mismatch"):
        module._validate_config_and_journal({**good, "sample_threshold": 1}, {})
    with pytest.raises(MarketDataError, match="zero-attempt"):
        module._validate_config_and_journal(good, {"journal_status": "closed"})


def test_report_validator_rejects_path_identity_state_and_artifact_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(MarketDataError, match="path escape"):
        module.validate_receipt_chain_report(ROOT / "README.md")

    report_path = next((ROOT / "reports/prospective-capture-attempt-receipt-chain").glob("*.json"))
    original = json.loads(report_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(module, "_repo_root", lambda _path: ROOT)
    monkeypatch.setattr(module, "validate_capture_attempt_journal", lambda _path: {})

    report = json.loads(json.dumps(original))
    report["identity"]["receipt_count"] = 99
    monkeypatch.setattr(module, "_load_json", lambda path: report if path == report_path else original)
    with pytest.raises(MarketDataError, match="identity mismatch"):
        module.validate_receipt_chain_report(report_path)

    report = json.loads(json.dumps(original))
    report["contract_status"] = "tampered"
    monkeypatch.setattr(module, "_load_json", lambda path: report if path == report_path else original)
    with pytest.raises(MarketDataError, match="state mismatch"):
        module.validate_receipt_chain_report(report_path)

    report = json.loads(json.dumps(original))
    report["artifacts"] = []
    monkeypatch.setattr(module, "_load_json", lambda path: report if path == report_path else original)
    with pytest.raises(MarketDataError, match="artifacts shape"):
        module.validate_receipt_chain_report(report_path)

    report = json.loads(json.dumps(original))
    report["artifacts"]["receipts"] = {}
    monkeypatch.setattr(module, "_load_json", lambda path: report if path == report_path else original)
    with pytest.raises(MarketDataError, match="metadata mismatch:receipts"):
        module.validate_receipt_chain_report(report_path)

    report = json.loads(json.dumps(original))
    report["artifacts"]["receipts"]["filename"] = "missing-receipts.csv"
    monkeypatch.setattr(module, "_load_json", lambda path: report if path == report_path else original)
    with pytest.raises(MarketDataError, match="artifact bytes mismatch:receipts"):
        module.validate_receipt_chain_report(report_path)


def test_report_validator_rejects_zero_table_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_path = next((ROOT / "reports/prospective-capture-attempt-receipt-chain").glob("*.json"))
    original = json.loads(report_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(module, "_repo_root", lambda _path: ROOT)
    monkeypatch.setattr(module, "validate_capture_attempt_journal", lambda _path: {})
    monkeypatch.setattr(module, "_load_json", lambda path: original if path == report_path else original)
    real_read_bytes = Path.read_bytes
    receipts_path = report_path.parent / original["artifacts"]["receipts"]["filename"]
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"bad\n" if path == receipts_path else real_read_bytes(path))
    expected_receipts_sha = original["identity"]["artifacts"]["receipts_sha256"]
    real_digest = module._digest
    monkeypatch.setattr(module, "_digest", lambda content: expected_receipts_sha if content == b"bad\n" else real_digest(content))
    with pytest.raises(MarketDataError, match="zero-receipt table"):
        module.validate_receipt_chain_report(report_path)


def test_replay_window_and_request_policy_guards() -> None:
    journal, ticket, request_policy = _context()
    receipt = _receipt(journal["journal_sha256"], ticket["ticket_sha256"], 1, "transport_failed")
    good_config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    with pytest.raises(MarketDataError, match="outside governed window"):
        module._replay_receipts(
            [{**receipt, "attempt_started_at": "2026-08-16T11:00:00Z"}],
            good_config,
            journal,
            ticket,
            request_policy,
            ROOT,
        )
    with pytest.raises(MarketDataError, match="request policy"):
        module._replay_receipts(
            [{**receipt, "request_policy_sha256": "d" * 64}],
            good_config,
            journal,
            ticket,
            request_policy,
            ROOT,
        )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "path", "attempt_bool", "accepted_bool", "response_bool"],
)
def test_receipt_shape_guards(mutation: str) -> None:
    journal, ticket, _request_policy = _context()
    receipt = _receipt(journal["journal_sha256"], ticket["ticket_sha256"], 1, "transport_failed")
    if mutation == "missing":
        receipt.pop("failure_code")
    elif mutation == "extra":
        receipt["forbidden_selection"] = "best"
    elif mutation == "path":
        receipt["snapshot_marker_path"] = 17
    elif mutation == "attempt_bool":
        receipt["attempt_number"] = True
    elif mutation == "accepted_bool":
        receipt["accepted"] = "false"
    else:
        receipt["response_received"] = "false"
    with pytest.raises(MarketDataError):
        module._validate_receipt_shape(receipt)


@pytest.mark.parametrize("outcome", ["unknown", "transport_bad", "validation_bad", "pass_bad"])
def test_outcome_semantic_guards(outcome: str, tmp_path: Path) -> None:
    journal, ticket, _request_policy = _context()
    if outcome == "unknown":
        receipt = _receipt(journal["journal_sha256"], ticket["ticket_sha256"], 1, "unknown")
    elif outcome == "transport_bad":
        receipt = {**_receipt(journal["journal_sha256"], ticket["ticket_sha256"], 1, "transport_failed"), "response_received": True}
    elif outcome == "validation_bad":
        receipt = {**_receipt(journal["journal_sha256"], ticket["ticket_sha256"], 1, "validation_failed"), "response_sha256": "bad"}
    else:
        receipt = {**_receipt(journal["journal_sha256"], ticket["ticket_sha256"], 1, "validation_passed"), "failure_code": "unexpected"}
    with pytest.raises(MarketDataError):
        module._validate_outcome(receipt, tmp_path, journal, ticket)


def test_snapshot_marker_guards_and_valid_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal, ticket, _request_policy = _context()
    base = _receipt(journal["journal_sha256"], ticket["ticket_sha256"], 1, "validation_passed")
    with pytest.raises(MarketDataError, match="path escape"):
        module._validate_snapshot_marker(tmp_path, {**base, "snapshot_marker_path": str(ROOT / "README.md")}, journal, ticket)

    marker = tmp_path / "snapshot.json"
    marker.write_text(json.dumps({"snapshot_sha256": "b" * 64, "identity": {"received_at": "2026-08-16T10:30:00Z"}}), encoding="utf-8")
    monkeypatch.setattr(module, "validate_future_universe_snapshot", lambda *_args: {})
    valid = {**base, "snapshot_marker_path": str(marker)}
    module._validate_snapshot_marker(tmp_path, valid, journal, ticket)
    module._validate_snapshot_marker(tmp_path, {**valid, "snapshot_marker_path": marker.name}, journal, ticket)

    with pytest.raises(MarketDataError, match="identity mismatch"):
        module._validate_snapshot_marker(tmp_path, {**valid, "snapshot_marker_sha256": "c" * 64}, journal, ticket)
    marker.write_text(json.dumps({"snapshot_sha256": "b" * 64, "identity": {"received_at": "2026-08-16T09:59:59Z"}}), encoding="utf-8")
    with pytest.raises(MarketDataError, match="outside governed window"):
        module._validate_snapshot_marker(tmp_path, valid, journal, ticket)
    marker.write_text(json.dumps({"snapshot_sha256": "b" * 64, "identity": {"received_at": "2026-08-16T10:30:00Z"}}), encoding="utf-8")
    with pytest.raises(MarketDataError, match="not pending"):
        module._validate_snapshot_marker(tmp_path, valid, journal, {**ticket, "ticket_status": "closed"})


def test_scalar_helpers_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(MarketDataError, match="timestamp invalid"):
        module._parse_utc("not-a-timestamp")
    with pytest.raises(MarketDataError, match="timezone-aware"):
        module._parse_utc("2026-08-16T10:00:00")
    assert module._parse_utc("2026-08-16T10:00:00+08:00").isoformat() == "2026-08-16T02:00:00+00:00"
    assert module._is_sha256("a" * 64)
    assert not module._is_sha256("g" * 64)
    assert not module._is_sha256("a")
    with pytest.raises(MarketDataError, match="repo root"):
        module._repo_root(tmp_path)
    with pytest.raises(ValueError, match="inside reports"):
        module._reports_output(ROOT, tmp_path)
    with pytest.raises(MarketDataError, match="json read"):
        module._load_json(tmp_path / "missing.json")
    shape = tmp_path / "shape.json"
    shape.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="json shape"):
        module._load_json(shape)
