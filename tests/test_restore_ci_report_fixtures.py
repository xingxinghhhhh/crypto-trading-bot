"""Integrity and hermeticity checks for the report-backed CI fixture closure."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from restore_ci_report_fixtures import (
    _REQUIRED_OPERATIONS_REPORTS,
    _extract_archive,
    _validate_archive,
    _validate_report_tree,
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
