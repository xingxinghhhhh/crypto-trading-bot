"""Test-session bootstrap for deterministic report-backed integration fixtures.

The generated ``reports/`` tree is intentionally ignored because it is a
runtime artifact.  A clean CI checkout therefore needs the small, frozen
fixture bundle restored before modules that resolve report paths at import
time are collected.
"""

from __future__ import annotations

def _restore_report_fixtures() -> None:
    from restore_ci_report_fixtures import restore_report_fixtures

    restore_report_fixtures()


_restore_report_fixtures()
