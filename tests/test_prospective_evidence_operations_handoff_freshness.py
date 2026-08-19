from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as bundle_admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery as delivery_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_admission as admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_freshness as freshness_module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as manifest_module
import crypto_bot.market.prospective_evidence_operations_handoff_read_model as read_model_module


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = next((ROOT / "reports/prospective-evidence-operations-snapshot").glob("*.json"))
BUNDLE_CONFIG = ROOT / bundle_module.DEFAULT_CONFIG_FILENAME
BUNDLE_ADMISSION_CONFIG = ROOT / bundle_admission_module.DEFAULT_CONFIG_FILENAME
MANIFEST_CONFIG = ROOT / manifest_module.DEFAULT_CONFIG_FILENAME
DELIVERY_CONFIG = ROOT / delivery_module.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / admission_module.DEFAULT_CONFIG_FILENAME
ROLLOVER = next((ROOT / "reports/prospective-epoch-closeout-rollover").glob("*.json"))


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("handoff-freshness")
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


def _load(inputs: dict[str, Path]) -> read_model_module.VerifiedCurrentOperationsHandoff:
    return freshness_module.load_governance_fresh_current_operations_handoff(
        inputs["admission"], inputs["delivery"], SNAPSHOT, ROLLOVER
    )


def test_hold_returns_unchanged_read_model_without_writes(inputs: dict[str, Path]) -> None:
    before = _tree_digest(inputs["root"])
    expected = read_model_module.load_verified_current_operations_handoff(
        inputs["admission"], inputs["delivery"], SNAPSHOT
    )
    actual = _load(inputs)
    assert actual == expected
    assert actual.safe_to_consume_current_read_only is True
    assert (actual.current_samples, actual.sample_threshold, actual.remaining_samples) == (
        160,
        500,
        340,
    )
    assert _tree_digest(inputs["root"]) == before


@pytest.mark.parametrize("action", ["rollover_next_window", "transition_eligible", "unknown"])
def test_supersession_and_unknown_actions_fail_closed(
    inputs: dict[str, Path], action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = freshness_module.validate_prospective_epoch_closeout_rollover(ROLLOVER)
    rollover = dict(original)
    rollover["action"] = action
    monkeypatch.setattr(
        freshness_module,
        "validate_prospective_epoch_closeout_rollover",
        lambda _: rollover,
    )
    with pytest.raises(freshness_module.MarketDataError):
        _load(inputs)


def test_unrelated_epoch_rollover_fails_closed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = freshness_module.validate_prospective_epoch_closeout_rollover(ROLLOVER)
    rollover = dict(original)
    identity = dict(original["identity"])
    identity["assembly_sha256"] = "a" * 64
    rollover["identity"] = identity
    monkeypatch.setattr(
        freshness_module,
        "validate_prospective_epoch_closeout_rollover",
        lambda _: rollover,
    )
    with pytest.raises(freshness_module.MarketDataError, match="assembly mismatch"):
        _load(inputs)


def test_invalid_current_snapshot_stops_before_governance_check(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        freshness_module,
        "validate_prospective_evidence_operations_snapshot",
        lambda _: (_ for _ in ()).throw(freshness_module.MarketDataError("snapshot invalid")),
    )
    called = False

    def fail_if_called(_: str | Path) -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError("rollover validation must not run")

    monkeypatch.setattr(freshness_module, "validate_prospective_epoch_closeout_rollover", fail_if_called)
    with pytest.raises(freshness_module.MarketDataError, match="snapshot invalid"):
        _load(inputs)
    assert called is False


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()
