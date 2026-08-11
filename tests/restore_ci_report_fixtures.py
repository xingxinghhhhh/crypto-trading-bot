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
_REQUIRED_RUNTIME_FILES = (
    "data/promoted/okx_direct_six_v1/1h/okx_btc_usdt_1h_direct_frozen.37d3af3a57ca833b84573ef3f91c453fd517320f6d6e90c06199432264cc9a9b.csv",
    "data/promoted/okx_direct_six_v1/1h/okx_eth_usdt_1h_direct_frozen.90983727ba098070a6c798725790d4907ef7d9e8eb6d7f59510f202bae49a036.csv",
    "data/promoted/okx_direct_six_v1/1h/okx_sol_usdt_1h_direct_frozen.0df16980c25528c82f6e5589619c04b93992b18f30801537a959b53389f381b7.csv",
    "data/promoted/okx_convenience_v1/1h/okx_knc_usdt_1h_frozen.e8cc7e87078afeba7d372bf901c3c31b51dccd96a9cb9cfa3eefb5af5be7e27f.csv",
    "data/promoted/okx_convenience_v1/1h/okx_swftc_usdt_1h_frozen.51f4347d7b9f11d84dbdf7aa060bb39015acaa8d12f7d46d9aca7ca01bb2c78c.csv",
    "data/promoted/okx_convenience_v1/1h/okx_bico_usdt_1h_frozen.2dac269642ec23f5dbd170ada3e0a7951ad0aa1e06404a8f29b2deb6108c9bc0.csv",
    "downloads/BTCUSDT-1h-2024-2026-05-15-binance-klines.csv",
)


def _reports_are_complete(reports: Path) -> bool:
    return all(any((reports / name).glob("*.json")) for name in _REQUIRED_REPORT_DIRS)


def _runtime_inputs_are_complete(repo: Path) -> bool:
    return all((repo / path).is_file() for path in _REQUIRED_RUNTIME_FILES)


def _extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    with ZipFile(archive) as bundle:
        for info in bundle.infolist():
            member = info.filename.replace("\\", "/")
            target = (destination / member).resolve()
            if target != destination_root and destination_root not in target.parents:
                raise RuntimeError(f"fixture archive path escapes destination: {member}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as destination_file:
                copyfileobj(source, destination_file)


def restore_report_fixtures() -> None:
    repo = Path(__file__).resolve().parents[1]
    reports = repo / "reports"
    archive = Path(__file__).resolve().parent / "fixtures" / "ci-reports.zip"
    runtime_archive = Path(__file__).resolve().parent / "fixtures" / "ci-runtime-inputs.zip"
    if _reports_are_complete(reports) and _runtime_inputs_are_complete(repo):
        return
    if not archive.is_file() or not runtime_archive.is_file():
        raise FileNotFoundError(f"CI fixture archive is missing: {archive} or {runtime_archive}")

    if not _reports_are_complete(reports):
        _extract_archive(archive, reports)
    if not _runtime_inputs_are_complete(repo):
        _extract_archive(runtime_archive, repo)

    missing = [name for name in _REQUIRED_REPORT_DIRS if not any((reports / name).glob("*.json"))]
    if missing:
        sample = sorted(
            path.relative_to(reports).as_posix()
            for path in reports.rglob("*.json")
        )[:20]
        raise RuntimeError(
            f"CI report fixture archive is missing required directories: {missing}; "
            f"restored JSON sample: {sample}"
        )
    missing_runtime = [path for path in _REQUIRED_RUNTIME_FILES if not (repo / path).is_file()]
    if missing_runtime:
        raise RuntimeError(f"CI runtime fixture archive is missing files: {missing_runtime}")


if __name__ == "__main__":
    restore_report_fixtures()
    print("CI report fixtures restored")
