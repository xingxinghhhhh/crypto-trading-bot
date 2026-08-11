"""Restore the frozen report tree required by report-backed CI tests."""

from __future__ import annotations

from shutil import copyfileobj
from pathlib import Path
from zipfile import ZipFile


_REQUIRED_REPORT_DIRS = (
    "cross-sectional-portfolio-mechanism",
    "cross-sectional-variant-preregistration",
    "okx-direct-six-asset-1h-migration",
    "okx-future-universe-snapshot",
    "okx-universe-capture",
    "prospective-capture-attempt-receipt-chain",
    "prospective-membership-bar-gate",
    "prospective-membership-epoch-closure-smoke",
    "prospective-membership-epoch-smoke",
)


def _reports_are_complete(reports: Path) -> bool:
    return all(any((reports / name).glob("*.json")) for name in _REQUIRED_REPORT_DIRS)


def restore_report_fixtures() -> None:
    repo = Path(__file__).resolve().parents[1]
    reports = repo / "reports"
    archive = Path(__file__).resolve().parent / "fixtures" / "ci-reports.zip"
    if _reports_are_complete(reports):
        return
    if not archive.is_file():
        raise FileNotFoundError(f"CI report fixture archive is missing: {archive}")

    reports.mkdir(parents=True, exist_ok=True)
    reports_root = reports.resolve()
    with ZipFile(archive) as bundle:
        members = bundle.infolist()
        for info in members:
            target = (reports / info.filename).resolve()
            if target != reports_root and reports_root not in target.parents:
                raise RuntimeError(f"fixture archive path escapes reports: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as destination:
                copyfileobj(source, destination)

    missing = [name for name in _REQUIRED_REPORT_DIRS if not any((reports / name).glob("*.json"))]
    if missing:
        raise RuntimeError(f"CI report fixture archive is missing required directories: {missing}")


if __name__ == "__main__":
    restore_report_fixtures()
    print("CI report fixtures restored")
