from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as bundle_admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection as module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery as delivery_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_admission as admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as manifest_module
from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_evidence_operations_handoff_read_model import (
    VerifiedCurrentOperationsHandoff,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = next((ROOT / "reports/prospective-evidence-operations-snapshot").glob("*.json"))
ROLLOVER = next((ROOT / "reports/prospective-epoch-closeout-rollover").glob("*.json"))
BUNDLE_CONFIG = ROOT / bundle_module.DEFAULT_CONFIG_FILENAME
BUNDLE_ADMISSION_CONFIG = ROOT / bundle_admission_module.DEFAULT_CONFIG_FILENAME
MANIFEST_CONFIG = ROOT / manifest_module.DEFAULT_CONFIG_FILENAME
DELIVERY_CONFIG = ROOT / delivery_module.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / admission_module.DEFAULT_CONFIG_FILENAME


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("consumer-projection")
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


def _project(inputs: dict[str, Path]) -> dict[str, module.JSONScalar]:
    return module.project_governance_fresh_current_operations_handoff(
        inputs["admission"], inputs["delivery"], SNAPSHOT, ROLLOVER
    )


def _cli_args(inputs: dict[str, Path]) -> list[str]:
    return [
        "crypto-bot",
        "show-verified-current-operations-handoff",
        "--delivery-admission",
        str(inputs["admission"]),
        "--handoff-delivery",
        str(inputs["delivery"]),
        "--current-operations-snapshot",
        str(SNAPSHOT),
        "--epoch-closeout-rollover",
        str(ROLLOVER),
    ]


def test_projection_has_exact_schema_and_current_smoke_values(
    inputs: dict[str, Path]
) -> None:
    projection = _project(inputs)
    assert set(projection) == set(module.PROJECTION_FIELDS)
    assert projection["projection_version"] == module.PROJECTION_VERSION
    assert projection["governance_freshness_verified"] is True
    assert projection["safe_to_consume_current_read_only"] is True
    assert (projection["current_samples"], projection["sample_threshold"], projection["remaining_samples"]) == (
        160,
        500,
        340,
    )
    assert projection["governed_stage"] == "awaiting_real_membership_epoch_progress"
    assert projection["blocking_gate"] == "membership_epoch_progress"
    assert projection["next_legal_action"] == "await_real_membership_epoch_progress"
    for field in (
        "append_authorization_ready",
        "economic_authorized",
        "pnl_authorized",
        "paper_authorized",
        "live_authorized",
        "consumer_action_authorized",
        "state_mutation_authorized",
    ):
        assert projection[field] is False


def test_projection_is_serialization_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _model()
    calls: list[tuple[object, ...]] = []

    def fake_loader(*args: object) -> VerifiedCurrentOperationsHandoff:
        calls.append(args)
        return model

    monkeypatch.setattr(module, "load_governance_fresh_current_operations_handoff", fake_loader)
    projection = module.project_governance_fresh_current_operations_handoff(
        "admission.json", "delivery.json", "snapshot.json", "rollover.json"
    )
    assert calls == [("admission.json", "delivery.json", "snapshot.json", "rollover.json")]
    assert projection["delivery_admission_identity"] == "delivery-admission"
    assert projection["governance_freshness_verified"] is True


def test_canonical_json_is_deterministic_and_metadata_free(
    inputs: dict[str, Path],
) -> None:
    first = module.format_governance_fresh_current_operations_handoff_projection(_project(inputs))
    second = module.format_governance_fresh_current_operations_handoff_projection(_project(inputs))
    assert first == second
    assert first == json.dumps(json.loads(first), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert "\n" not in first
    assert all(value not in first for value in ("reports", "artifacts", "\\", "/"))


@pytest.mark.parametrize(
    "changed",
    [
        lambda value: {key: item for key, item in value.items() if key != "blocking_gate"},
        lambda value: {**value, "unexpected": False},
        lambda value: {**value, "current_samples": True},
    ],
)
def test_projection_formatter_rejects_schema_or_type_drift(
    inputs: dict[str, Path], changed: object
) -> None:
    projection = _project(inputs)
    altered = changed(projection)  # type: ignore[operator]
    with pytest.raises(MarketDataError, match="consumer projection"):
        module.format_governance_fresh_current_operations_handoff_projection(altered)


def test_cli_success_is_one_canonical_json_line(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", _cli_args(inputs))
    expected = module.format_governance_fresh_current_operations_handoff_projection(_project(inputs))
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.out == expected + "\n"
    assert captured.err == ""
    assert json.loads(captured.out) == json.loads(expected)


def test_cli_fail_closed_has_empty_stdout_and_exit_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_projection(*args: object) -> dict[str, module.JSONScalar]:
        del args
        raise MarketDataError("superseded")

    monkeypatch.setattr(
        cli_module,
        "project_governance_fresh_current_operations_handoff",
        fail_projection,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto-bot",
            "show-verified-current-operations-handoff",
            "--delivery-admission",
            "a",
            "--handoff-delivery",
            "b",
            "--current-operations-snapshot",
            "c",
            "--epoch-closeout-rollover",
            "d",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert captured.err == "prospective_evidence_operations_handoff_consumer_projection_failed: superseded\n"


def test_cli_missing_required_argument_is_argparse_exit_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["crypto-bot", "show-verified-current-operations-handoff"])
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert "required" in captured.err


def test_projection_does_not_write_business_tree(inputs: dict[str, Path]) -> None:
    before = _tree_digest(inputs["root"])
    _project(inputs)
    assert _tree_digest(inputs["root"]) == before


def _model() -> VerifiedCurrentOperationsHandoff:
    return VerifiedCurrentOperationsHandoff(
        delivery_admission_identity="delivery-admission",
        handoff_delivery_identity="handoff-delivery",
        source_operations_snapshot_identity="source-snapshot",
        current_operations_snapshot_identity="current-snapshot",
        safe_to_consume_current_read_only=True,
        governed_stage="stage",
        blocking_gate="gate",
        next_legal_action="action",
        current_samples=160,
        sample_threshold=500,
        remaining_samples=340,
        append_authorization_ready=False,
        economic_authorized=False,
        pnl_authorized=False,
        paper_authorized=False,
        live_authorized=False,
        consumer_action_authorized=False,
        state_mutation_authorized=False,
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()
