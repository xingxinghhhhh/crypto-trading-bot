import hashlib
import json
import sys
from pathlib import Path

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as bundle_admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection as projection_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_verification as module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery as delivery_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_admission as admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_freshness as freshness_module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as manifest_module
from crypto_bot.errors import MarketDataError


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
    root = tmp_path_factory.mktemp("consumer-projection-verification")
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


def _project(inputs: dict[str, Path]) -> dict[str, projection_module.JSONScalar]:
    return projection_module.project_governance_fresh_current_operations_handoff(
        inputs["admission"], inputs["delivery"], SNAPSHOT, ROLLOVER
    )


def _persist(
    inputs: dict[str, Path],
    projection: dict[str, projection_module.JSONScalar],
    *,
    suffix: str = "projection.json",
    trailing_newline: bool = False,
) -> Path:
    path = inputs["root"] / suffix
    encoded = projection_module.format_governance_fresh_current_operations_handoff_projection(
        projection
    )
    path.write_text(encoded + ("\n" if trailing_newline else ""), encoding="utf-8")
    return path


def _verify_args(projection: Path, inputs: dict[str, Path]) -> tuple[Path, Path, Path, Path, Path]:
    return (
        projection,
        inputs["admission"],
        inputs["delivery"],
        SNAPSHOT,
        ROLLOVER,
    )


def _cli_args(projection: Path, inputs: dict[str, Path]) -> list[str]:
    return [
        "crypto-bot",
        "verify-current-operations-handoff-projection",
        "--projection",
        str(projection),
        "--delivery-admission",
        str(inputs["admission"]),
        "--handoff-delivery",
        str(inputs["delivery"]),
        "--current-operations-snapshot",
        str(SNAPSHOT),
        "--epoch-closeout-rollover",
        str(ROLLOVER),
    ]


def test_persisted_canonical_projection_replays_exactly(inputs: dict[str, Path]) -> None:
    projection = _project(inputs)
    persisted_path = _persist(inputs, projection)
    assert module.verify_governance_fresh_current_operations_handoff_projection(
        *_verify_args(persisted_path, inputs)
    ) == projection


def test_one_terminal_newline_is_accepted(inputs: dict[str, Path]) -> None:
    projection = _project(inputs)
    persisted_path = _persist(inputs, projection, suffix="projection-newline.json", trailing_newline=True)
    assert module.verify_governance_fresh_current_operations_handoff_projection(
        *_verify_args(persisted_path, inputs)
    ) == projection


@pytest.mark.parametrize("field,value", [("current_samples", 161), ("delivery_admission_identity", "changed")])
def test_projection_value_or_identity_tamper_fails_closed(
    inputs: dict[str, Path], field: str, value: object
) -> None:
    projection = _project(inputs)
    altered = dict(projection)
    altered[field] = value  # type: ignore[assignment]
    persisted_path = _persist(inputs, altered, suffix=f"tampered-{field}.json")
    with pytest.raises(MarketDataError, match="provenance mismatch"):
        module.verify_governance_fresh_current_operations_handoff_projection(
            *_verify_args(persisted_path, inputs)
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: {key: item for key, item in value.items() if key != "blocking_gate"},
        lambda value: {**value, "unexpected": False},
        lambda value: {**value, "current_samples": True},
    ],
)
def test_schema_and_type_drift_fails_closed(
    inputs: dict[str, Path], mutate: object
) -> None:
    projection = _project(inputs)
    altered = mutate(projection)  # type: ignore[operator]
    path = inputs["root"] / "schema-drift.json"
    path.write_text(json.dumps(altered, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(MarketDataError, match="consumer projection"):
        module.verify_governance_fresh_current_operations_handoff_projection(
            *_verify_args(path, inputs)
        )


@pytest.mark.parametrize(
    "encoded",
    [
        lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        lambda value: projection_module.format_governance_fresh_current_operations_handoff_projection(value) + "\n\n",
        lambda value: " " + projection_module.format_governance_fresh_current_operations_handoff_projection(value),
        lambda value: projection_module.format_governance_fresh_current_operations_handoff_projection(value) + " ",
        lambda value: "\ufeff" + projection_module.format_governance_fresh_current_operations_handoff_projection(value),
    ],
)
def test_noncanonical_projection_text_fails_closed(
    inputs: dict[str, Path], encoded: object
) -> None:
    projection = _project(inputs)
    path = inputs["root"] / "noncanonical.json"
    path.write_text(encoded(projection), encoding="utf-8")  # type: ignore[operator]
    with pytest.raises(MarketDataError, match="canonical|newline"):
        module.verify_governance_fresh_current_operations_handoff_projection(
            *_verify_args(path, inputs)
        )


@pytest.mark.parametrize("message", ["superseded", "assembly mismatch"])
def test_upstream_freshness_failures_propagate_fail_closed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    projection = _project(inputs)
    persisted_path = _persist(inputs, projection, suffix=f"{message.replace(' ', '-')}.json")

    def fail_loader(*args: object) -> object:
        del args
        raise MarketDataError(message)

    monkeypatch.setattr(projection_module, "load_governance_fresh_current_operations_handoff", fail_loader)
    with pytest.raises(MarketDataError, match=message):
        module.verify_governance_fresh_current_operations_handoff_projection(
            *_verify_args(persisted_path, inputs)
        )


def test_tampered_upstream_parent_cannot_be_bypassed(inputs: dict[str, Path]) -> None:
    projection = _project(inputs)
    persisted_path = _persist(inputs, projection, suffix="parent-tamper.json")
    payload = next((inputs["delivery"].parent / "payload").rglob("*.json"))
    original = payload.read_bytes()
    try:
        payload.write_bytes(original + b"tampered\n")
        with pytest.raises(MarketDataError):
            module.verify_governance_fresh_current_operations_handoff_projection(
                *_verify_args(persisted_path, inputs)
            )
    finally:
        payload.write_bytes(original)


def test_verifier_only_replays_existing_projector(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = _project(inputs)
    persisted_path = _persist(inputs, projection, suffix="layering.json")

    def fail_if_directly_called(*args: object) -> object:
        del args
        raise AssertionError("verifier must not call freshness internals directly")

    monkeypatch.setattr(module, "project_governance_fresh_current_operations_handoff", lambda *args: projection)
    monkeypatch.setattr(freshness_module, "load_governance_fresh_current_operations_handoff", fail_if_directly_called)
    assert module.verify_governance_fresh_current_operations_handoff_projection(
        *_verify_args(persisted_path, inputs)
    ) == projection


def test_verifier_has_no_business_tree_side_effects(inputs: dict[str, Path]) -> None:
    projection = _project(inputs)
    persisted_path = _persist(inputs, projection, suffix="side-effects.json")
    before = _tree_digest(inputs["root"])
    module.verify_governance_fresh_current_operations_handoff_projection(
        *_verify_args(persisted_path, inputs)
    )
    assert _tree_digest(inputs["root"]) == before


def test_cli_success_is_existing_canonical_projection(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    projection = _project(inputs)
    persisted_path = _persist(inputs, projection, suffix="cli-success.json", trailing_newline=True)
    monkeypatch.setattr(sys, "argv", _cli_args(persisted_path, inputs))
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    expected = projection_module.format_governance_fresh_current_operations_handoff_projection(projection)
    assert exc_info.value.code == 0
    assert captured.out == expected + "\n"
    assert captured.err == ""


def test_cli_tampered_projection_fails_closed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    projection = _project(inputs)
    altered = dict(projection)
    altered["current_samples"] = 161
    persisted_path = _persist(inputs, altered, suffix="cli-tampered.json")
    monkeypatch.setattr(sys, "argv", _cli_args(persisted_path, inputs))
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert "consumer_projection_verification_failed" in captured.err


def test_cli_missing_required_argument_is_argparse_exit_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["crypto-bot", "verify-current-operations-handoff-projection"])
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert "required" in captured.err


def test_replay_is_deterministic(inputs: dict[str, Path]) -> None:
    projection = _project(inputs)
    persisted_path = _persist(inputs, projection, suffix="deterministic.json")
    first = module.verify_governance_fresh_current_operations_handoff_projection(
        *_verify_args(persisted_path, inputs)
    )
    second = module.verify_governance_fresh_current_operations_handoff_projection(
        *_verify_args(persisted_path, inputs)
    )
    assert first == second
    assert projection_module.format_governance_fresh_current_operations_handoff_projection(first) == (
        projection_module.format_governance_fresh_current_operations_handoff_projection(second)
    )


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()
