from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest

import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as bundle_admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery as delivery_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_admission as admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_verification as verification_module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as manifest_module
import crypto_bot.market.prospective_evidence_operations_handoff_read_model as module
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
    root = tmp_path_factory.mktemp("handoff-read-model")
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


def _load(inputs: dict[str, Path]) -> module.VerifiedCurrentOperationsHandoff:
    return module.load_verified_current_operations_handoff(
        inputs["admission"], inputs["delivery"], SNAPSHOT
    )


def test_current_handoff_is_immutable_and_path_free(inputs: dict[str, Path]) -> None:
    model = _load(inputs)
    assert model.safe_to_consume_current_read_only is True
    assert model.governed_stage == "awaiting_real_membership_epoch_progress"
    assert model.blocking_gate == "membership_epoch_progress"
    assert model.next_legal_action == "await_real_membership_epoch_progress"
    assert (model.current_samples, model.sample_threshold, model.remaining_samples) == (
        160,
        500,
        340,
    )
    assert model.append_authorization_ready is False
    assert model.economic_authorized is False
    assert model.pnl_authorized is False
    assert model.paper_authorized is False
    assert model.live_authorized is False
    assert model.consumer_action_authorized is False
    assert model.state_mutation_authorized is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        model.current_samples = 500  # type: ignore[misc]
    assert all("reports" not in str(value) and "artifacts" not in str(value) for value in dataclasses.astuple(model))


def test_same_inputs_are_deterministic_and_no_business_tree_writes(
    inputs: dict[str, Path]
) -> None:
    before = _tree_digest(inputs["root"])
    first = _load(inputs)
    second = _load(inputs)
    assert first == second
    assert _tree_digest(inputs["root"]) == before


def test_parent_or_stale_admission_fails_closed(
    inputs: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(module.MarketDataError):
        module.load_verified_current_operations_handoff(
            inputs["delivery"], inputs["delivery"], SNAPSHOT
        )
    current = snapshot_module.validate_prospective_evidence_operations_snapshot(SNAPSHOT)
    stale = dict(current)
    stale["snapshot_sha256"] = "c" * 64
    monkeypatch.setattr(admission_module, "validate_prospective_evidence_operations_snapshot", lambda _: stale)
    stale_admission = admission_module.freeze_prospective_evidence_operations_handoff_delivery_admission(
        inputs["delivery"], SNAPSHOT, ADMISSION_CONFIG, tmp_path / "stale"
    )
    monkeypatch.undo()
    with pytest.raises(module.MarketDataError):
        module.load_verified_current_operations_handoff(
            stale_admission.export_paths["report"], inputs["delivery"], SNAPSHOT
        )


def test_projection_and_authorization_escalation_fail_closed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_inspect = delivery_module.inspect_prospective_evidence_operations_handoff_delivery(
        inputs["delivery"]
    )
    projection = dict(original_inspect.source_projection)
    projection["current_samples"] = "not-an-int"
    monkeypatch.setattr(
        module,
        "inspect_prospective_evidence_operations_handoff_delivery",
        lambda _: dataclasses.replace(original_inspect, source_projection=projection),
    )
    with pytest.raises(module.MarketDataError, match="field:current_samples"):
        _load(inputs)
    monkeypatch.undo()

    original_verify = verification_module.verify_prospective_evidence_operations_handoff_delivery
    unsafe = original_verify(inputs["admission"], inputs["delivery"], SNAPSHOT)
    unsafe["state_mutation_authorized"] = True
    monkeypatch.setattr(module, "verify_prospective_evidence_operations_handoff_delivery", lambda *_: unsafe)
    with pytest.raises(module.MarketDataError, match="verification boundary"):
        _load(inputs)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()

