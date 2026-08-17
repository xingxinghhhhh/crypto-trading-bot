"""Integrity and hermeticity checks for the report-backed CI fixture closure."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from restore_ci_report_fixtures import (
    _REQUIRED_OPERATIONS_REPORTS,
    _extract_archive,
    _validate_archive,
    _validate_report_tree,
)
from crypto_bot.market.prospective_evidence_operations_snapshot import (
    validate_prospective_evidence_operations_snapshot,
)
from crypto_bot.prospective_economic_readiness_gate import (
    validate_prospective_economic_readiness,
)


ROOT = Path(__file__).resolve().parents[1]
OPERATIONS_ARCHIVE = ROOT / "tests/fixtures/ci-operations-reports.zip"


def _copy_operations_archive(destination: Path, *, omit: str | None = None, tamper: str | None = None) -> Path:
    with ZipFile(OPERATIONS_ARCHIVE) as source, ZipFile(destination, "w", compression=ZIP_DEFLATED) as target:
        for info in source.infolist():
            if info.filename == omit:
                continue
            data = source.read(info)
            if info.filename == tamper:
                data += b"tampered"
            target.writestr(info.filename, data)
    return destination


def test_operations_archive_is_a_minimal_content_addressed_closure() -> None:
    _validate_archive(OPERATIONS_ARCHIVE)
    with ZipFile(OPERATIONS_ARCHIVE) as archive:
        names = archive.namelist()
    assert all("\\" not in name and not name.startswith("/") for name in names)
    assert all(name in names for name in (
        f"{directory}/{filename}" for directory, filename in _REQUIRED_OPERATIONS_REPORTS.items()
    ))
    assert not any(".f13cccb549f1ed908df2445772ba30fc17d6ac63c4b0260182777a14a8a83c7c" in name for name in names)
    assert not any(".91f4d7e85a0a0042bd4adfe45e7e1f457eadf593f9407da897f83071321c13cf" in name for name in names)


def test_clean_destination_restores_and_validates_the_parent_tree() -> None:
    with TemporaryDirectory(prefix="ci-fixture-", dir=ROOT) as temporary:
        reports = Path(temporary) / "reports"
        _extract_archive(OPERATIONS_ARCHIVE, reports)
        _validate_report_tree(reports)


def test_clean_destination_replays_operations_snapshot_fixture() -> None:
    with TemporaryDirectory(prefix="ci-snapshot-replay-", dir=ROOT) as temporary:
        replay_root = Path(temporary)
        (replay_root / "src" / "crypto_bot").mkdir(parents=True)
        (replay_root / "src" / "crypto_bot" / "__init__.py").write_text("", encoding="utf-8")
        (replay_root / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        for source in ROOT.glob("config*.yaml"):
            copyfile(source, replay_root / source.name)
        reports = replay_root / "reports"
        _extract_archive(OPERATIONS_ARCHIVE, reports)
        snapshot_path = reports / "prospective-evidence-operations-snapshot" / (
            _REQUIRED_OPERATIONS_REPORTS["prospective-evidence-operations-snapshot"]
        )
        report = validate_prospective_evidence_operations_snapshot(
            snapshot_path, replay_root=replay_root
        )
        assert report["snapshot_sha256"] == "b424c38d8cb2347a567ce7c4608e31b2b1b1b0b65d61419a0db843b7374cc1d1"
        assert report["current_samples"] == 160
        assert report["sample_threshold"] == 500
        assert report["remaining_samples"] == 340
        assert report["governed_stage"] == "awaiting_real_membership_epoch_progress"
        assert report["blocking_gate"] == "membership_epoch_progress"
        assert report["next_legal_action"] == "await_real_membership_epoch_progress"
        for key in ("append_authorization_ready", "economic_authorized", "pnl_authorized", "paper_authorized", "live_authorized"):
            assert report[key] is False
        assert report["state_changed"] is False
        assert report["network_activity_performed"] is False
        assert report["new_samples_counted"] == 0
        readiness_path = reports / "prospective-economic-readiness" / (
            "prospective-economic-readiness."
            "0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b.json"
        )
        readiness = validate_prospective_economic_readiness(readiness_path)
        assert readiness["readiness_sha256"] == "0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b"
        assert readiness["closed_interval_count"] == 160
        assert readiness["prospective_economic_inputs_structurally_ready"] is True
        assert readiness["economic_value_computation_authorized"] is False
        assert readiness["pnl_computation_authorized"] is False


def test_restore_is_idempotent_but_refuses_different_existing_bytes() -> None:
    with TemporaryDirectory(prefix="ci-fixture-", dir=ROOT) as temporary:
        reports = Path(temporary) / "reports"
        _extract_archive(OPERATIONS_ARCHIVE, reports)
        _extract_archive(OPERATIONS_ARCHIVE, reports)
        report = reports / next(iter(_REQUIRED_OPERATIONS_REPORTS)) / next(
            iter(_REQUIRED_OPERATIONS_REPORTS.values())
        )
        report.write_bytes(report.read_bytes() + b"drift")
        with pytest.raises(RuntimeError, match="different bytes"):
            _extract_archive(OPERATIONS_ARCHIVE, reports)


def test_missing_parent_fails_before_replay(tmp_path: Path) -> None:
    missing = next(iter(_REQUIRED_OPERATIONS_REPORTS.items()))
    archive = _copy_operations_archive(
        tmp_path / "missing.zip",
        omit=f"{missing[0]}/{missing[1]}",
    )
    with pytest.raises(RuntimeError, match="missing required CI report fixture"):
        _validate_archive(archive)


def test_tampered_transitive_artifact_fails_before_replay(tmp_path: Path) -> None:
    with ZipFile(OPERATIONS_ARCHIVE) as source:
        target = next(
            name
            for name in source.namelist()
            if name.endswith(".constraints.csv")
            and name.startswith("prospective-direct-1h-segment-append-authorization-smoke/")
        )
    archive = _copy_operations_archive(tmp_path / "tampered.zip", tamper=target)
    with pytest.raises(RuntimeError, match="artifact hash mismatch"):
        _validate_archive(archive)
