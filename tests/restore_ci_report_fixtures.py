"""Restore the frozen report tree required by report-backed CI tests."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile


_MARKER = (
    "cross-sectional-portfolio-mechanism/"
    "cross-sectional-portfolio-mechanism."
    "dcc8e9364efd900487608090ba879194a8a84195cbeab2dc63d0266df97406d5.json"
)


def restore_report_fixtures() -> None:
    repo = Path(__file__).resolve().parents[1]
    reports = repo / "reports"
    archive = Path(__file__).resolve().parent / "fixtures" / "ci-reports.zip"
    marker = reports / _MARKER
    if marker.is_file():
        return
    if not archive.is_file():
        raise FileNotFoundError(f"CI report fixture archive is missing: {archive}")

    reports.mkdir(parents=True, exist_ok=True)
    reports_root = reports.resolve()
    with ZipFile(archive) as bundle:
        members = bundle.namelist()
        for member in members:
            target = (reports / member).resolve()
            if target != reports_root and reports_root not in target.parents:
                raise RuntimeError(f"fixture archive path escapes reports: {member}")
        bundle.extractall(reports)

    if not marker.is_file():
        raise RuntimeError(f"CI report fixture archive did not restore marker: {marker}")


if __name__ == "__main__":
    restore_report_fixtures()
    print("CI report fixtures restored")
