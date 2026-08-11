"""Restore the frozen report tree required by report-backed CI tests."""

from __future__ import annotations

from shutil import copyfileobj
from pathlib import Path
from zipfile import ZipFile


def restore_report_fixtures() -> None:
    repo = Path(__file__).resolve().parents[1]
    reports = repo / "reports"
    archive = Path(__file__).resolve().parent / "fixtures" / "ci-reports.zip"
    if any(reports.rglob("*.json")):
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

    restored_json = list(reports.rglob("*.json"))
    if not restored_json:
        raise RuntimeError(f"CI report fixture archive restored no JSON files: {reports}")


if __name__ == "__main__":
    restore_report_fixtures()
    print("CI report fixtures restored")
