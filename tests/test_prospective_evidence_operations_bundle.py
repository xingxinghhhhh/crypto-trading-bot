from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import crypto_bot.market.prospective_evidence_operations_bundle as module
from crypto_bot.market.prospective_evidence_operations_snapshot import (
    validate_prospective_evidence_operations_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = (
    ROOT
    / "reports/prospective-evidence-operations-snapshot/"
    "prospective-evidence-operations-snapshot.b424c38d8cb2347a567ce7c4608e31b2b1b1b0b65d61419a0db843b7374cc1d1.json"
)
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def _build(output: Path) -> module.OperationsBundleResult:
    return module.build_prospective_evidence_operations_bundle(SNAPSHOT, CONFIG, output)


def _report_path(bundle_dir: Path) -> Path:
    return next(bundle_dir.glob("prospective-evidence-operations-bundle.*.json"))


def _inventory_path(bundle_dir: Path) -> Path:
    return next(bundle_dir.glob("prospective-evidence-operations-bundle.*.inventory.csv"))


def _validate_with_source_reports_hidden(report_path: Path) -> dict[str, object]:
    """Prove bundle replay does not fall back to the source report tree."""
    source_reports = ROOT / "reports"
    hidden_reports = ROOT / "reports-bundle-isolation-hidden"
    assert not hidden_reports.exists()
    source_reports.rename(hidden_reports)
    try:
        return module.validate_prospective_evidence_operations_bundle(report_path)
    finally:
        hidden_reports.rename(source_reports)


def test_real_bundle_replays_blocked_state_and_is_source_isolated(tmp_path: Path) -> None:
    result = _build(tmp_path / "bundle")
    report = result.report
    assert report["source_snapshot_replay_valid"] is True
    assert report["governed_stage"] == "awaiting_real_membership_epoch_progress"
    assert report["blocking_gate"] == "membership_epoch_progress"
    assert report["next_legal_action"] == "await_real_membership_epoch_progress"
    assert report["current_samples"] == 160
    assert report["sample_threshold"] == 500
    assert report["remaining_samples"] == 340
    assert report["append_authorization_ready"] is False
    assert report["economic_authorized"] is False
    assert report["pnl_authorized"] is False
    assert report["paper_authorized"] is False
    assert report["live_authorized"] is False
    assert report["state_changed"] is False
    assert report["network_activity_performed"] is False
    assert report["new_samples_counted"] == 0

    isolated = _validate_with_source_reports_hidden(_report_path(tmp_path / "bundle"))
    assert isolated["bundle_sha256"] == report["bundle_sha256"]


def test_replay_root_is_backward_compatible() -> None:
    default = validate_prospective_evidence_operations_snapshot(SNAPSHOT)
    explicit = validate_prospective_evidence_operations_snapshot(SNAPSHOT, replay_root=ROOT)
    assert explicit == default


def test_bundle_is_deterministic_across_output_directories(tmp_path: Path) -> None:
    first = _build(tmp_path / "one")
    second = _build(tmp_path / "two")
    assert first.report["bundle_sha256"] == second.report["bundle_sha256"]
    for suffix in ("inventory.csv", "dependencies.csv", "constraints.csv"):
        left = next((tmp_path / "one").glob(f"*.{suffix}"))
        right = next((tmp_path / "two").glob(f"*.{suffix}"))
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()

    left_files = sorted(
        p.relative_to(tmp_path / "one" / "payload").as_posix()
        for p in (tmp_path / "one" / "payload").rglob("*")
        if p.is_file()
    )
    right_files = sorted(
        p.relative_to(tmp_path / "two" / "payload").as_posix()
        for p in (tmp_path / "two" / "payload").rglob("*")
        if p.is_file()
    )
    assert left_files == right_files
    for logical in left_files:
        assert (tmp_path / "one" / "payload" / logical).read_bytes() == (
            tmp_path / "two" / "payload" / logical
        ).read_bytes()


@pytest.mark.parametrize("mutation", ["missing", "tamper", "extra", "inventory", "path", "authorization"])
def test_bundle_tamper_and_closure_fail_closed(tmp_path: Path, mutation: str) -> None:
    target = tmp_path / mutation
    _build(target)
    report_path = _report_path(target)

    if mutation == "missing":
        (target / "payload" / "pyproject.toml").unlink()
    elif mutation == "tamper":
        (target / "payload" / "pyproject.toml").write_bytes(b"tampered\n")
    elif mutation == "extra":
        (target / "payload" / "undeclared.txt").write_bytes(b"x")
    elif mutation == "inventory":
        inventory_path = _inventory_path(target)
        rows = list(csv.DictReader(inventory_path.read_text(encoding="utf-8").splitlines()))
        rows[0]["byte_count"] = str(int(rows[0]["byte_count"]) + 1)
        with inventory_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=module.INVENTORY_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    elif mutation == "path":
        inventory_path = _inventory_path(target)
        rows = list(csv.DictReader(inventory_path.read_text(encoding="utf-8").splitlines()))
        rows[0]["logical_name"] = "../escape"
        with inventory_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=module.INVENTORY_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    else:
        value = json.loads(report_path.read_text(encoding="utf-8"))
        value["bundle_authorizes_append"] = True
        report_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_bundle(report_path)


def test_bundle_rejects_output_inside_source_reports(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside source reports"):
        _build(ROOT / "reports" / "prospective-evidence-operations-bundle-test")


def test_bundle_marker_projection_tamper_fails_closed(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    _build(output)
    report_path = _report_path(output)
    value = json.loads(report_path.read_text(encoding="utf-8"))
    value["current_samples"] = 500
    report_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_bundle(report_path)


def test_bundle_content_addressed_collision_is_idempotent_and_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "same-name"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError, match="collision"):
        module._commit_bytes(path, b"different")
