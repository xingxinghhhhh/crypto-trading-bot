from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as manifest_module
import crypto_bot.market.prospective_evidence_operations_handoff_verification as module


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = next((ROOT / "reports/prospective-evidence-operations-snapshot").glob("*.json"))
BUNDLE_CONFIG = ROOT / bundle_module.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / admission_module.DEFAULT_CONFIG_FILENAME
MANIFEST_CONFIG = ROOT / manifest_module.DEFAULT_CONFIG_FILENAME


@pytest.fixture(scope="module")
def parents(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("handoff-verification-parents")
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


def _copy_manifest(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.parent.iterdir():
        if item.is_file():
            shutil.copy2(item, destination / item.name)
    return destination / source.name


def test_current_manifest_is_read_only_safe(parents: dict[str, Path]) -> None:
    result = module.verify_prospective_evidence_operations_handoff_manifest(
        parents["manifest"], parents["admission"], parents["bundle"]
    )
    assert result["verification_status"] == "consumer_handoff_verified"
    assert result["safe_to_consume_read_only"] is True
    assert result["current_samples"] == 160
    assert result["sample_threshold"] == 500
    assert result["remaining_samples"] == 340
    assert result["consumer_action_authorized"] is False
    assert result["state_mutation_authorized"] is False
    assert result["live_authorized"] is False


def test_verification_output_is_canonical_and_minimal(parents: dict[str, Path]) -> None:
    result = module.verify_prospective_evidence_operations_handoff_manifest(
        parents["manifest"], parents["admission"], parents["bundle"]
    )
    output = module.format_handoff_manifest_verification(result)
    decoded = json.loads(output)
    assert tuple(decoded) == tuple(sorted(module.VERIFICATION_FIELDS))
    assert "manifest_state" not in decoded
    assert str(ROOT) not in output
    assert output == module.format_handoff_manifest_verification(decoded)


def test_manifest_projection_tamper_is_rejected(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    copied = _copy_manifest(parents["manifest"], tmp_path / "projection-tamper")
    report = json.loads(copied.read_text(encoding="utf-8"))
    report["current_samples"] = 500
    copied.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.verify_prospective_evidence_operations_handoff_manifest(
            copied, parents["admission"], parents["bundle"]
        )


@pytest.mark.parametrize("field", ["consumer_action_authorized", "state_mutation_authorized"])
def test_authorization_escalation_is_rejected(
    parents: dict[str, Path], tmp_path: Path, field: str
) -> None:
    copied = _copy_manifest(parents["manifest"], tmp_path / field)
    report = json.loads(copied.read_text(encoding="utf-8"))
    report[field] = True
    copied.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.verify_prospective_evidence_operations_handoff_manifest(
            copied, parents["admission"], parents["bundle"]
        )


def test_stale_lineage_is_rejected(parents: dict[str, Path], tmp_path: Path) -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    identity = dict(snapshot["identity"])
    identity["verification_stale_test_nonce"] = "stale"
    snapshot_sha = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    snapshot["identity"] = identity
    snapshot["snapshot_sha256"] = snapshot_sha
    repo_fixture = ROOT / "reports" / "prospective-evidence-operations-snapshot"
    stale_snapshot = repo_fixture / f"prospective-evidence-operations-snapshot.{snapshot_sha}.json"
    stale_snapshot.write_text(json.dumps(snapshot), encoding="utf-8")
    try:
        admission = admission_module.freeze_prospective_evidence_operations_bundle_admission(
            parents["bundle"], stale_snapshot, ADMISSION_CONFIG, tmp_path / "stale-admission"
        )
        manifest = manifest_module.build_prospective_evidence_operations_handoff_manifest(
            admission.export_paths["report"],
            parents["bundle"],
            MANIFEST_CONFIG,
            tmp_path / "stale-manifest",
        )
        assert manifest.report["status"] == "blocked_bundle_not_current"
        with pytest.raises(module.MarketDataError, match="not current"):
            module.verify_prospective_evidence_operations_handoff_manifest(
                manifest.export_paths["report"], admission.export_paths["report"], parents["bundle"]
            )
    finally:
        stale_snapshot.unlink(missing_ok=True)


def test_parent_identity_mismatch_is_rejected(parents: dict[str, Path], tmp_path: Path) -> None:
    bundle = json.loads(parents["bundle"].read_text(encoding="utf-8"))
    bundle["bundle_status"] = "tampered"
    tampered = tmp_path / "tampered-bundle.json"
    tampered.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.verify_prospective_evidence_operations_handoff_manifest(
            parents["manifest"], parents["admission"], tampered
        )


def test_missing_parent_is_fail_closed(parents: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises((FileNotFoundError, OSError, module.MarketDataError)):
        module.verify_prospective_evidence_operations_handoff_manifest(
            parents["manifest"], tmp_path / "missing-admission.json", parents["bundle"]
        )


def test_cli_returns_json_and_exit_zero(parents: dict[str, Path]) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "verify-prospective-evidence-operations-handoff-manifest",
            "--handoff-manifest",
            str(parents["manifest"]),
            "--bundle-admission",
            str(parents["admission"]),
            "--operations-bundle",
            str(parents["bundle"]),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    output = json.loads(completed.stdout)
    assert output["verification_status"] == "consumer_handoff_verified"
    assert output["safe_to_consume_read_only"] is True
    assert completed.stderr == ""


def test_result_shape_rejects_authority() -> None:
    invalid = {field: None for field in module.VERIFICATION_FIELDS}
    invalid["verification_status"] = "consumer_handoff_verified"
    invalid["safe_to_consume_read_only"] = True
    with pytest.raises(module.MarketDataError):
        module.format_handoff_manifest_verification(invalid)
