from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_capture_attempt_journal as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
TICKET = ROOT / (
    "reports/prospective-epoch-capture-admission/"
    "prospective-epoch-capture-admission.ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3.json"
)
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def test_empty_journal_is_deterministic_and_ticket_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module,
        "_reports_output",
        lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)),
    )
    first = module.freeze_prospective_capture_attempt_journal(TICKET, CONFIG, tmp_path / "one")
    second = module.freeze_prospective_capture_attempt_journal(TICKET, CONFIG, tmp_path / "two")
    assert first.report["journal_sha256"] == second.report["journal_sha256"]
    assert first.report["ticket_sha256"] == "ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3"
    assert first.report["journal_status"] == "awaiting_future_attempt"
    assert first.report["attempt_count"] == 0
    assert first.report["accepted_attempt_count"] == 0
    assert first.report["accepted_snapshot_identity"] is None
    assert first.report["network_activity_performed"] is False
    for key in first.export_paths:
        left, right = Path(first.export_paths[key]), Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()


def test_config_drift_is_rejected(tmp_path: Path) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["accepted_attempt_count_max"] = 2
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, MarketDataError)):
        module.load_capture_attempt_journal_config(path)


def test_sequence_accepts_failures_then_first_valid() -> None:
    attempts = [
        {"attempt_number": 1, "received_at": "2026-08-16T10:00:00Z", "status": "transport_failed"},
        {"attempt_number": 2, "received_at": "2026-08-16T10:12:00Z", "status": "validation_failed"},
        {
            "attempt_number": 3,
            "received_at": "2026-08-16T10:24:00Z",
            "status": "validation_passed",
            "accepted": True,
            "snapshot_identity": "snapshot-3",
        },
    ]
    assert module.validate_capture_attempt_sequence(
        attempts, "2026-08-16T10:00:00Z", "2026-08-16T11:00:00Z"
    ) == {"attempt_count": 3, "accepted_attempt_count": 1, "accepted_snapshot_identity": "snapshot-3"}


@pytest.mark.parametrize(
    "attempts",
    [
        [{"attempt_number": 2, "received_at": "2026-08-16T10:00:00Z", "status": "transport_failed"}],
        [
            {"attempt_number": 1, "received_at": "2026-08-16T10:00:00Z", "status": "validation_passed", "accepted": True, "snapshot_identity": "a"},
            {"attempt_number": 2, "received_at": "2026-08-16T10:01:00Z", "status": "transport_failed"},
        ],
        [{"attempt_number": 1, "received_at": "2026-08-16T11:00:00Z", "status": "transport_failed"}],
        [{"attempt_number": 1, "received_at": "2026-08-16T10:00:00Z", "status": "validation_failed", "accepted": True}],
        [{"attempt_number": 1, "received_at": "2026-08-16T10:00:00Z", "status": "validation_failed", "selected_response": "best"}],
        [{"attempt_number": 1, "received_at": "2026-08-16T10:00:00Z", "status": "validation_passed", "accepted": True}],
    ],
)
def test_sequence_fail_closed(attempts: list[dict[str, object]]) -> None:
    with pytest.raises(MarketDataError):
        module.validate_capture_attempt_sequence(
            attempts, "2026-08-16T10:00:00Z", "2026-08-16T11:00:00Z"
        )


def test_empty_sequence_is_zero_attempt_contract() -> None:
    assert module.validate_capture_attempt_sequence(
        [], "2026-08-16T10:00:00Z", "2026-08-16T11:00:00Z"
    ) == {"attempt_count": 0, "accepted_attempt_count": 0, "accepted_snapshot_identity": None}


def test_ticket_drift_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ticket = json.loads(TICKET.read_text(encoding="utf-8"))
    ticket["ticket_status"] = "closed"
    drifted = tmp_path / TICKET.name
    drifted.write_text(json.dumps(ticket), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.freeze_prospective_capture_attempt_journal(drifted, CONFIG, tmp_path / "output")


def test_commit_collision_and_format(tmp_path: Path) -> None:
    path = tmp_path / "artifact"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(MarketDataError):
        module._commit_bytes(path, b"different")
    result = module.CaptureAttemptJournalResult(
        {
            "contract_status": "status",
            "journal_sha256": "sha",
            "ticket_sha256": "ticket",
            "window_start": "a",
            "window_end": "b",
        },
        {},
    )
    formatted = module.format_capture_attempt_journal_result(result)
    assert "journal_sha256: sha" in formatted
    assert "attempt_count: 0" in formatted
