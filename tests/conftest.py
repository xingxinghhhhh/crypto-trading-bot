"""Test-session bootstrap for deterministic report-backed integration fixtures.

The generated ``reports/`` tree is intentionally ignored because it is a
runtime artifact.  A clean CI checkout therefore needs the small, frozen
fixture bundle restored before modules that resolve report paths at import
time are collected.
"""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile


def _restore_report_fixtures() -> None:
    repo = Path(__file__).resolve().parents[1]
    reports = repo / "reports"
    archive = Path(__file__).resolve().parent / "fixtures" / "ci-reports.zip"
    # Git does not preserve empty directories, but a runner or a plugin may
    # create the reports directory before pytest imports this module.  Use a
    # concrete archive member as the readiness check so a partially-created
    # directory can never suppress fixture restoration.
    sentinel = reports / "cross-sectional-portfolio-mechanism" / (
        "cross-sectional-portfolio-mechanism."
        "dcc8e9364efd900487608090ba879194a8a84195cbeab2dc63d0266df97406d5.json"
    )
    if sentinel.is_file() or not archive.is_file():
        return

    reports.mkdir(parents=True, exist_ok=True)
    reports_root = reports.resolve()
    with ZipFile(archive) as bundle:
        members = bundle.namelist()
        for member in members:
            target = (reports / member).resolve()
            if target != reports_root and reports_root not in target.parents:
                raise RuntimeError(f"fixture archive path escapes reports: {member}")
        bundle.extractall(reports)


_restore_report_fixtures()
