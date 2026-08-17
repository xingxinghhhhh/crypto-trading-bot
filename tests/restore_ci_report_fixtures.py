"""Restore and validate the frozen report tree required by report-backed CI tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from shutil import copyfileobj
from pathlib import Path
from pathlib import PurePosixPath
from zipfile import ZipFile, ZipInfo


_REQUIRED_REPORT_DIRS = (
    "cross-sectional-portfolio-mechanism",
    "cross-sectional-variant-preregistration",
    "okx-direct-six-asset-1h-migration",
    "okx-future-universe-snapshot",
    "okx-universe-capture",
    "prospective-capture-attempt-receipt-chain",
    "prospective-direct-1h-segment-append-authorization-smoke",
    "prospective-direct-1h-segment-append-preflight-smoke",
    "prospective-direct-1h-segment-evidence-smoke",
    "prospective-direct-1h-segment-admission-smoke",
    "prospective-economic-readiness",
    "prospective-economic-sample-maturity",
    "prospective-epoch-assembly",
    "prospective-membership-bar-gate",
    "prospective-membership-epoch-closure-smoke",
    "prospective-membership-epoch-smoke",
)
_REQUIRED_OPERATIONS_REPORTS = {
    "prospective-epoch-assembly": "prospective-epoch-assembly.674116b95e03705b478f285012148f0143c2ef82befccda5627ce2ab901bcc79.json",
    "prospective-economic-sample-maturity": "prospective-economic-sample-maturity.32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178.json",
    "prospective-economic-readiness": "prospective-economic-readiness.0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b.json",
    "prospective-direct-1h-segment-append-preflight-smoke": "prospective-direct-1h-segment-append-preflight.b70f686e3cd65bb2b617325f3fd9f623ac573e81df66f3ff1c8a42f49d9ab328.json",
    "prospective-direct-1h-segment-evidence-smoke": "prospective-direct-1h-segment-evidence.64be9005ed29a64a93d8ae5a53f3a975a9376596245f960d877584f8093c423e.json",
    "prospective-direct-1h-segment-admission-smoke": "prospective-direct-1h-segment-admission.05780d162e6b630e367e7da632e9b9e448ded25c7576cc33bfef1a442b1a125a.json",
    "prospective-direct-1h-segment-append-authorization-smoke": "prospective-direct-1h-segment-append-authorization.272c9ac3b5da943372af574d050b4ccd15cb6817c537fb6630bf896f2463e688.json",
}
_REQUIRED_RUNTIME_FILES = (
    "promoted-panels-1h.9e5a03f60327ce77a417471f513c256b740bf440e2bfa950d953a937ca068ff0.yaml",
    "promoted-panels.c18a99849be5f3eb92dd329f9073ea9a5260d16bddb7aac4b635b5f30d1dc1ae.yaml",
    "promoted-registry-1h.51ebbf3f97658fa8f36e5cd8f37742047727887957b347ba0399459b9de22975.yaml",
    "promoted-registry.cf3ecbc7cb7c84b3e127568acb65b26597c07fb79e45c6a350ea67395587a42f.yaml",
    "data/promoted/okx_direct_six_v1/1h/okx_btc_usdt_1h_direct_frozen.37d3af3a57ca833b84573ef3f91c453fd517320f6d6e90c06199432264cc9a9b.csv",
    "data/promoted/okx_direct_six_v1/1h/okx_eth_usdt_1h_direct_frozen.90983727ba098070a6c798725790d4907ef7d9e8eb6d7f59510f202bae49a036.csv",
    "data/promoted/okx_direct_six_v1/1h/okx_sol_usdt_1h_direct_frozen.0df16980c25528c82f6e5589619c04b93992b18f30801537a959b53389f381b7.csv",
    "data/promoted/okx_convenience_v1/1h/okx_knc_usdt_1h_frozen.e8cc7e87078afeba7d372bf901c3c31b51dccd96a9cb9cfa3eefb5af5be7e27f.csv",
    "data/promoted/okx_convenience_v1/1h/okx_swftc_usdt_1h_frozen.51f4347d7b9f11d84dbdf7aa060bb39015acaa8d12f7d46d9aca7ca01bb2c78c.csv",
    "data/promoted/okx_convenience_v1/1h/okx_bico_usdt_1h_frozen.2dac269642ec23f5dbd170ada3e0a7951ad0aa1e06404a8f29b2deb6108c9bc0.csv",
    "downloads/BTCUSDT-1h-2024-2026-05-15-binance-klines.csv",
)


def _reports_are_complete(reports: Path) -> bool:
    return (
        all(any((reports / name).glob("*.json")) for name in _REQUIRED_REPORT_DIRS)
        and all(
            (reports / directory / filename).is_file()
            for directory, filename in _REQUIRED_OPERATIONS_REPORTS.items()
        )
    )


def _runtime_inputs_are_complete(repo: Path) -> bool:
    return all((repo / path).is_file() for path in _REQUIRED_RUNTIME_FILES)


def _normalise_member(name: str) -> str:
    member = name.replace("\\", "/")
    path = PurePosixPath(member)
    if path.is_absolute() or ".." in path.parts or member != path.as_posix():
        raise RuntimeError(f"fixture archive path is unsafe: {name}")
    return member


def _artifact_references(value: object) -> Iterator[tuple[str, str]]:
    if isinstance(value, Mapping):
        filename = value.get("filename")
        digest = value.get("sha256")
        if isinstance(filename, str) and isinstance(digest, str):
            yield filename, digest
        for child in value.values():
            yield from _artifact_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _artifact_references(child)


def _validate_report_payload(
    report_name: str,
    report_bytes: bytes,
    read_artifact: Callable[[str], bytes],
) -> None:
    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"CI report fixture JSON is invalid: {report_name}") from exc
    if not isinstance(report, dict):
        raise RuntimeError(f"CI report fixture JSON is not an object: {report_name}")
    report_digest = PurePosixPath(report_name).stem.rsplit(".", 1)[-1]
    identity_digests = {
        value
        for key, value in report.items()
        if key.endswith("_sha256") and isinstance(value, str)
    }
    if report_digest not in identity_digests:
        raise RuntimeError(f"CI report fixture identity mismatch: {report_name}")
    report_directory = str(PurePosixPath(report_name).parent)
    seen: set[tuple[str, str]] = set()
    for filename, expected_digest in _artifact_references(report):
        reference = (filename, expected_digest)
        if reference in seen:
            continue
        seen.add(reference)
        safe_filename = _normalise_member(filename)
        if "/" in safe_filename:
            raise RuntimeError(f"CI report fixture artifact escapes family: {report_name}:{filename}")
        artifact_name = f"{report_directory}/{safe_filename}"
        try:
            artifact_bytes = read_artifact(artifact_name)
        except KeyError as exc:
            raise RuntimeError(f"CI report fixture artifact is missing: {artifact_name}") from exc
        actual_digest = hashlib.sha256(artifact_bytes).hexdigest()
        if actual_digest != expected_digest:
            raise RuntimeError(f"CI report fixture artifact hash mismatch: {artifact_name}")


def _validate_archive(archive: Path) -> None:
    with ZipFile(archive) as bundle:
        member_infos: dict[str, ZipInfo] = {}
        for info in bundle.infolist():
            member = _normalise_member(info.filename)
            if member in member_infos:
                raise RuntimeError(f"duplicate CI report fixture member: {member}")
            member_infos[member] = info
        for directory, filename in _REQUIRED_OPERATIONS_REPORTS.items():
            report_name = f"{directory}/{filename}"
            if report_name not in member_infos:
                raise RuntimeError(f"missing required CI report fixture: {report_name}")
            _validate_report_payload(
                report_name,
                bundle.read(member_infos[report_name]),
                lambda name: bundle.read(member_infos[name]),
            )


def _validate_report_tree(reports: Path) -> None:
    for directory, filename in _REQUIRED_OPERATIONS_REPORTS.items():
        report_path = reports / directory / filename
        if not report_path.is_file():
            raise RuntimeError(f"missing restored CI report fixture: {report_path}")
        _validate_report_payload(
            f"{directory}/{filename}",
            report_path.read_bytes(),
            lambda name: (reports / name).read_bytes(),
        )


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
            with bundle.open(info) as source:
                if target.is_file():
                    existing = target.read_bytes()
                    incoming = source.read()
                    if existing != incoming:
                        raise RuntimeError(f"fixture restore would overwrite different bytes: {member}")
                else:
                    with target.open("wb") as destination_file:
                        copyfileobj(source, destination_file)


def restore_report_fixtures() -> None:
    repo = Path(__file__).resolve().parents[1]
    reports = repo / "reports"
    archive = Path(__file__).resolve().parent / "fixtures" / "ci-reports.zip"
    operations_archive = Path(__file__).resolve().parent / "fixtures" / "ci-operations-reports.zip"
    runtime_archive = Path(__file__).resolve().parent / "fixtures" / "ci-runtime-inputs.zip"
    if not archive.is_file() or not operations_archive.is_file() or not runtime_archive.is_file():
        raise FileNotFoundError(
            f"CI fixture archive is missing: {archive}, {operations_archive}, or {runtime_archive}"
        )
    _validate_archive(operations_archive)
    if _reports_are_complete(reports) and _runtime_inputs_are_complete(repo):
        _validate_report_tree(reports)
        return

    if not _reports_are_complete(reports):
        _extract_archive(archive, reports)
        _extract_archive(operations_archive, reports)
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
    _validate_report_tree(reports)


if __name__ == "__main__":
    restore_report_fixtures()
    print("restore_status=complete missing_required_families=0")
