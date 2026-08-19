from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery as module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as manifest_module


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = next((ROOT / "reports/prospective-evidence-operations-snapshot").glob("*.json"))
BUNDLE_CONFIG = ROOT / bundle_module.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / admission_module.DEFAULT_CONFIG_FILENAME
MANIFEST_CONFIG = ROOT / manifest_module.DEFAULT_CONFIG_FILENAME
DELIVERY_CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


@pytest.fixture(scope="module")
def parents(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("handoff-delivery-parents")
    bundle = bundle_module.build_prospective_evidence_operations_bundle(
        SNAPSHOT, BUNDLE_CONFIG, root / "bundle"
    )
    admission = admission_module.freeze_prospective_evidence_operations_bundle_admission(
        bundle.export_paths["report"], SNAPSHOT, ADMISSION_CONFIG, root / "admission"
    )
    manifest = manifest_module.build_prospective_evidence_operations_handoff_manifest(
        admission.export_paths["report"],
        bundle.export_paths["report"],
        MANIFEST_CONFIG,
        root / "manifest",
    )
    return {
        "root": root,
        "bundle": Path(bundle.export_paths["report"]),
        "admission": Path(admission.export_paths["report"]),
        "manifest": Path(manifest.export_paths["report"]),
    }


def _build(parents: dict[str, Path], output: Path) -> module.OperationsHandoffDeliveryResult:
    return module.build_prospective_evidence_operations_handoff_delivery(
        parents["manifest"],
        parents["admission"],
        parents["bundle"],
        DELIVERY_CONFIG,
        output,
    )


def test_current_delivery_is_ready_and_self_contained(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "delivery")
    report = result.report
    assert report["delivery_status"] == "handoff_delivery_ready"
    assert report["safe_to_consume_read_only"] is True
    assert report["delivery_action_authorized"] is False
    assert report["state_mutation_authorized"] is False
    assert report["network_activity_performed"] is False
    assert module.validate_prospective_evidence_operations_handoff_delivery(
        result.export_paths["report"]
    )["delivery_sha256"] == report["delivery_sha256"]


def test_package_only_replay_works_without_source_trees(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "delivery")
    package_report = Path(result.export_paths["report"])
    assert module.validate_prospective_evidence_operations_handoff_delivery(package_report)[
        "safe_to_consume_read_only"
    ] is True
    package_bytes = b"\n".join(
        path.read_bytes() for path in package_report.parent.rglob("*") if path.is_file()
    )
    assert str(parents["root"]).encode() not in package_bytes


def test_delivery_is_deterministic(parents: dict[str, Path], tmp_path: Path) -> None:
    first = _build(parents, tmp_path / "one")
    second = _build(parents, tmp_path / "two")
    assert Path(first.export_paths["report"]).name == Path(second.export_paths["report"]).name
    first_files = sorted(path.relative_to(Path(first.export_paths["package"])) for path in Path(first.export_paths["package"]).rglob("*"))
    second_files = sorted(path.relative_to(Path(second.export_paths["package"])) for path in Path(second.export_paths["package"]).rglob("*"))
    assert first_files == second_files
    for relative in first_files:
        first_path = Path(first.export_paths["package"]) / relative
        second_path = Path(second.export_paths["package"]) / relative
        if first_path.is_file():
            assert first_path.read_bytes() == second_path.read_bytes()


@pytest.mark.parametrize("tamper", ["manifest", "admission", "bundle"])
def test_parent_tamper_fails_closed(
    parents: dict[str, Path], tmp_path: Path, tamper: str
) -> None:
    result = _build(parents, tmp_path / "delivery")
    report_path = Path(result.export_paths["report"])
    role_dir = report_path.parent / "payload" / tamper
    target = next(path for path in role_dir.rglob("*.json") if path.is_file())
    content = json.loads(target.read_text(encoding="utf-8"))
    content["delivery_tamper"] = True
    target.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_handoff_delivery(report_path)


def test_missing_and_extra_payload_files_fail_closed(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "delivery")
    report_path = Path(result.export_paths["report"])
    target = next((report_path.parent / "payload").rglob("*.csv"))
    target.unlink()
    with pytest.raises(module.MarketDataError, match="undeclared|hash|payload"):
        module.validate_prospective_evidence_operations_handoff_delivery(report_path)

    second = _build(parents, tmp_path / "extra")
    extra = Path(second.export_paths["report"]).parent / "payload" / "extra.txt"
    extra.write_text("undeclared", encoding="utf-8")
    with pytest.raises(module.MarketDataError, match="undeclared"):
        module.validate_prospective_evidence_operations_handoff_delivery(
            second.export_paths["report"]
        )


def test_path_traversal_inventory_fails_closed(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "delivery")
    inventory_path = Path(result.export_paths["inventory"])
    text = inventory_path.read_text(encoding="utf-8").replace("payload/", "../")
    inventory_path.write_text(text, encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_handoff_delivery(
            result.export_paths["report"]
        )


def test_delivery_authority_tamper_fails_closed(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "delivery")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["delivery_action_authorized"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_handoff_delivery(report_path)


def test_same_bytes_idempotent_and_collision_rejected(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    output = tmp_path / "delivery"
    first = _build(parents, output)
    second = _build(parents, output)
    assert first.report == second.report
    sidecar = Path(first.export_paths["constraints"])
    sidecar.write_bytes(sidecar.read_bytes() + b"tamper\n")
    with pytest.raises(module.MarketDataError, match="collision"):
        _build(parents, output)


def test_cli_build_and_verify(parents: dict[str, Path], tmp_path: Path) -> None:
    output = tmp_path / "cli-delivery"
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "build-prospective-evidence-operations-handoff-delivery",
            "--handoff-manifest",
            str(parents["manifest"]),
            "--bundle-admission",
            str(parents["admission"]),
            "--operations-bundle",
            str(parents["bundle"]),
            "--config",
            str(DELIVERY_CONFIG),
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0
    report_path = next(output.rglob(f"{module.PREFIX}.*.json"))
    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "verify-prospective-evidence-operations-handoff-delivery",
            "--delivery-package",
            str(report_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0
    assert json.loads(verified.stdout)["safe_to_consume_read_only"] is True
    assert verified.stderr == ""


def test_config_and_output_boundaries(parents: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="filename"):
        module.load_operations_handoff_delivery_config(tmp_path / "wrong.yaml")
    drifted = tmp_path / module.DEFAULT_CONFIG_FILENAME
    drifted.write_text("schema_version: 1\n", encoding="utf-8")
    with pytest.raises(module.MarketDataError, match="policy mismatch"):
        module.load_operations_handoff_delivery_config(drifted)
    result = _build(parents, tmp_path / "delivery")
    assert Path(result.export_paths["package"]) == tmp_path / "delivery"
    with pytest.raises(ValueError, match="outside source reports"):
        module._output_root(ROOT, ROOT / "reports" / "delivery")
