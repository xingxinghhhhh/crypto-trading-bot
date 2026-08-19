from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as bundle_admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery as delivery_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_admission as module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as manifest_module
import crypto_bot.market.prospective_evidence_operations_snapshot as snapshot_module


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = next((ROOT / "reports/prospective-evidence-operations-snapshot").glob("*.json"))
SNAPSHOT_CONFIG = ROOT / snapshot_module.DEFAULT_CONFIG_FILENAME
BUNDLE_CONFIG = ROOT / bundle_module.DEFAULT_CONFIG_FILENAME
BUNDLE_ADMISSION_CONFIG = ROOT / bundle_admission_module.DEFAULT_CONFIG_FILENAME
MANIFEST_CONFIG = ROOT / manifest_module.DEFAULT_CONFIG_FILENAME
DELIVERY_CONFIG = ROOT / delivery_module.DEFAULT_CONFIG_FILENAME
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


@pytest.fixture(scope="module")
def parents(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("handoff-delivery-admission-parents")
    bundle = bundle_module.build_prospective_evidence_operations_bundle(
        SNAPSHOT, BUNDLE_CONFIG, root / "bundle"
    )
    admission = bundle_admission_module.freeze_prospective_evidence_operations_bundle_admission(
        bundle.export_paths["report"],
        SNAPSHOT,
        BUNDLE_ADMISSION_CONFIG,
        root / "admission",
    )
    manifest = manifest_module.build_prospective_evidence_operations_handoff_manifest(
        admission.export_paths["report"],
        bundle.export_paths["report"],
        MANIFEST_CONFIG,
        root / "manifest",
    )
    delivery = delivery_module.build_prospective_evidence_operations_handoff_delivery(
        manifest.export_paths["report"],
        admission.export_paths["report"],
        bundle.export_paths["report"],
        DELIVERY_CONFIG,
        root / "delivery",
    )
    return {"root": root, "delivery": Path(delivery.export_paths["report"])}


def _build(parents: dict[str, Path], output: Path) -> module.OperationsHandoffDeliveryAdmissionResult:
    return module.freeze_prospective_evidence_operations_handoff_delivery_admission(
        parents["delivery"], SNAPSHOT, CONFIG, output
    )


def test_current_delivery_is_admitted_and_legacy_validator_is_unchanged(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "admission")
    report = result.report
    assert report["status"] == "current_delivery_admitted"
    assert report["delivery_currentness_admitted"] is True
    assert report["delivery_stale"] is False
    assert report["safe_to_consume_current_read_only"] is True
    assert delivery_module.validate_prospective_evidence_operations_handoff_delivery(
        parents["delivery"]
    )["delivery_sha256"] == report["delivery_identity"]
    inspection = delivery_module.inspect_prospective_evidence_operations_handoff_delivery(
        parents["delivery"]
    )
    assert inspection.delivery_identity == report["delivery_identity"]
    assert inspection.source_operations_snapshot_identity == report[
        "delivery_source_snapshot_identity"
    ]
    assert set(inspection.source_projection) == set(bundle_module.SOURCE_STATE_FIELDS)


def test_config_and_format_are_frozen(parents: dict[str, Path], tmp_path: Path) -> None:
    assert module.load_operations_handoff_delivery_admission_config(CONFIG, ROOT) == module.POLICY_FIELDS
    result = _build(parents, tmp_path / "format")
    formatted = json.loads(module.format_operations_handoff_delivery_admission(result))
    assert formatted["status"] == "current_delivery_admitted"
    assert formatted["admission_identity"] == result.report["admission_sha256"]


def test_validation_requires_both_currentness_inputs(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "inputs")
    with pytest.raises(ValueError, match="both currentness"):
        module.validate_prospective_evidence_operations_handoff_delivery_admission(
            result.export_paths["report"], parents["delivery"], None
        )
    with pytest.raises(ValueError, match="delivery and current"):
        module.validate_prospective_evidence_operations_handoff_delivery_admission(
            result.export_paths["report"]
        )


def test_output_boundaries_and_invalid_snapshot_identity(
    parents: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="outside reports"):
        _build(parents, ROOT / "reports" / "delivery-admission")
    with pytest.raises(ValueError, match="delivery package"):
        _build(parents, parents["delivery"].parent / "nested")
    monkeypatch.setattr(module, "validate_prospective_evidence_operations_snapshot", lambda _: {})
    with pytest.raises(module.MarketDataError, match="identity missing"):
        _build(parents, tmp_path / "missing-identity")


def test_internal_read_boundaries_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(module.MarketDataError, match="policy mismatch"):
        module._validate_policy({})
    with pytest.raises(module.MarketDataError, match="repo root missing"):
        module._repo_root(tmp_path / "not-a-repository")
    with pytest.raises(module.MarketDataError, match="CSV schema"):
        module._read_csv_exact(b"wrong\n", module.ADMISSION_FIELDS)
    with pytest.raises(module.MarketDataError, match="duplicate key"):
        module._read_key_values(b"key,value\nx,1\nx,2\n")
    assert module._csv_value(None) == ""
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json", encoding="utf-8")
    with pytest.raises(module.MarketDataError, match="json read failed"):
        module._load_json(malformed)
    non_mapping = tmp_path / "array.json"
    non_mapping.write_text("[]", encoding="utf-8")
    with pytest.raises(module.MarketDataError, match="shape mismatch"):
        module._load_json(non_mapping)


def test_stale_delivery_is_blocked_without_refresh(
    parents: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = snapshot_module.validate_prospective_evidence_operations_snapshot(SNAPSHOT)
    stale = dict(current)
    stale["snapshot_sha256"] = "a" * 64
    monkeypatch.setattr(module, "validate_prospective_evidence_operations_snapshot", lambda _: stale)
    result = _build(parents, tmp_path / "stale")
    assert result.report["status"] == "blocked_stale_delivery"
    assert result.report["delivery_stale"] is True
    assert result.report["delivery_currentness_admitted"] is False
    assert result.report["safe_to_consume_current_read_only"] is False


def test_identity_match_with_projection_mismatch_fails_closed(
    parents: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = snapshot_module.validate_prospective_evidence_operations_snapshot(SNAPSHOT)
    mismatched = dict(current)
    mismatched["current_samples"] = int(current["current_samples"]) + 1
    monkeypatch.setattr(module, "validate_prospective_evidence_operations_snapshot", lambda _: mismatched)
    with pytest.raises(module.MarketDataError, match="projection mismatch"):
        _build(parents, tmp_path / "projection-mismatch")


def test_admission_report_tamper_and_sidecar_tamper_fail_closed(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "tamper")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["safe_to_consume_current_read_only"] = False
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_handoff_delivery_admission(
            report_path, parents["delivery"], SNAPSHOT
        )

    second = _build(parents, tmp_path / "sidecar")
    sidecar = Path(second.export_paths["dependencies"])
    sidecar.write_bytes(sidecar.read_bytes() + b"tamper\n")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_handoff_delivery_admission(
            second.export_paths["report"], parents["delivery"], SNAPSHOT
        )


def test_deterministic_and_collision_rejected(parents: dict[str, Path], tmp_path: Path) -> None:
    output = tmp_path / "same"
    first = _build(parents, output)
    second = _build(parents, output)
    assert first.report == second.report
    constraints = Path(first.export_paths["constraints"])
    constraints.write_bytes(constraints.read_bytes() + b"tamper\n")
    with pytest.raises(module.MarketDataError, match="collision"):
        _build(parents, output)


def test_cli_admission(parents: dict[str, Path], tmp_path: Path) -> None:
    output = tmp_path / "cli-admission"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "freeze-prospective-evidence-operations-handoff-delivery-admission",
            "--handoff-delivery",
            str(parents["delivery"]),
            "--current-operations-snapshot",
            str(SNAPSHOT),
            "--config",
            str(CONFIG),
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.splitlines()[0])["status"] == "current_delivery_admitted"
    assert completed.stderr == ""
