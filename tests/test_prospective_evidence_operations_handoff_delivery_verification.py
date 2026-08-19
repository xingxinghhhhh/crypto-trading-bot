from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as bundle_admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery as delivery_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_admission as admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as manifest_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_verification as module
import crypto_bot.market.prospective_evidence_operations_snapshot as snapshot_module


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = next((ROOT / "reports/prospective-evidence-operations-snapshot").glob("*.json"))
BUNDLE_CONFIG = ROOT / bundle_module.DEFAULT_CONFIG_FILENAME
BUNDLE_ADMISSION_CONFIG = ROOT / bundle_admission_module.DEFAULT_CONFIG_FILENAME
MANIFEST_CONFIG = ROOT / manifest_module.DEFAULT_CONFIG_FILENAME
DELIVERY_CONFIG = ROOT / delivery_module.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / admission_module.DEFAULT_CONFIG_FILENAME


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("handoff-delivery-verification")
    bundle = bundle_module.build_prospective_evidence_operations_bundle(
        SNAPSHOT, BUNDLE_CONFIG, root / "bundle"
    )
    bundle_admission = bundle_admission_module.freeze_prospective_evidence_operations_bundle_admission(
        bundle.export_paths["report"], SNAPSHOT, BUNDLE_ADMISSION_CONFIG, root / "bundle-admission"
    )
    manifest = manifest_module.build_prospective_evidence_operations_handoff_manifest(
        bundle_admission.export_paths["report"],
        bundle.export_paths["report"],
        MANIFEST_CONFIG,
        root / "manifest",
    )
    delivery = delivery_module.build_prospective_evidence_operations_handoff_delivery(
        manifest.export_paths["report"],
        bundle_admission.export_paths["report"],
        bundle.export_paths["report"],
        DELIVERY_CONFIG,
        root / "delivery",
    )
    admission = admission_module.freeze_prospective_evidence_operations_handoff_delivery_admission(
        delivery.export_paths["report"], SNAPSHOT, ADMISSION_CONFIG, root / "delivery-admission"
    )
    return {
        "root": root,
        "delivery": Path(delivery.export_paths["report"]),
        "admission": Path(admission.export_paths["report"]),
    }


def test_current_triple_is_a_canonical_read_barrier(inputs: dict[str, Path]) -> None:
    result = module.verify_prospective_evidence_operations_handoff_delivery(
        inputs["admission"], inputs["delivery"], SNAPSHOT
    )
    assert result["verification_status"] == module.VERIFICATION_STATUS
    assert result["safe_to_consume_current_read_only"] is True
    assert result["delivery_currentness_admitted"] is True
    assert result["delivery_stale"] is False
    assert result["delivery_action_authorized"] is False
    assert result["state_mutation_authorized"] is False
    assert result["network_activity_performed"] is False
    assert result["new_samples_counted"] == 0
    encoded = module.format_prospective_evidence_operations_handoff_delivery_verification(result)
    assert json.loads(encoded) == result
    assert encoded == module.format_prospective_evidence_operations_handoff_delivery_verification(result)


def test_stale_admission_and_current_snapshot_tamper_fail_closed(
    inputs: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = snapshot_module.validate_prospective_evidence_operations_snapshot(SNAPSHOT)
    stale = dict(current)
    stale["snapshot_sha256"] = "b" * 64
    monkeypatch.setattr(admission_module, "validate_prospective_evidence_operations_snapshot", lambda _: stale)
    stale_admission = admission_module.freeze_prospective_evidence_operations_handoff_delivery_admission(
        inputs["delivery"], SNAPSHOT, ADMISSION_CONFIG, tmp_path / "stale-admission"
    )
    monkeypatch.undo()
    with pytest.raises(module.MarketDataError, match="currentness|rejected"):
        module.verify_prospective_evidence_operations_handoff_delivery(
            stale_admission.export_paths["report"], inputs["delivery"], SNAPSHOT
        )

    tampered_snapshot = tmp_path / "snapshot-tampered.json"
    tampered_snapshot.write_bytes(SNAPSHOT.read_bytes() + b"tamper\n")
    with pytest.raises(module.MarketDataError):
        module.verify_prospective_evidence_operations_handoff_delivery(
            inputs["admission"], inputs["delivery"], tampered_snapshot
        )


def test_delivery_tamper_and_bound_mismatch_fail_closed(
    inputs: dict[str, Path], tmp_path: Path
) -> None:
    payload = next((inputs["delivery"].parent / "payload").rglob("*.json"))
    original = payload.read_bytes()
    try:
        payload.write_bytes(original + b"tamper\n")
        with pytest.raises(module.MarketDataError):
            module.verify_prospective_evidence_operations_handoff_delivery(
                inputs["admission"], inputs["delivery"], SNAPSHOT
            )
    finally:
        payload.write_bytes(original)


def test_cli_modes_and_no_business_tree_writes(inputs: dict[str, Path]) -> None:
    before = _tree_digest(inputs["root"])
    command = [
        sys.executable,
        "-m",
        "crypto_bot.cli",
        "verify-prospective-evidence-operations-handoff-delivery",
        "--delivery-admission",
        str(inputs["admission"]),
        "--handoff-delivery",
        str(inputs["delivery"]),
        "--current-operations-snapshot",
        str(SNAPSHOT),
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["safe_to_consume_current_read_only"] is True
    assert _tree_digest(inputs["root"]) == before

    legacy = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "verify-prospective-evidence-operations-handoff-delivery",
            "--delivery-package",
            str(inputs["delivery"]),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert legacy.returncode == 0
    assert json.loads(legacy.stdout)["safe_to_consume_read_only"] is True

    missing = subprocess.run(
        command[:-2], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert missing.returncode == 2
    assert "current read-barrier" in missing.stderr
    assert missing.stdout == ""


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()
