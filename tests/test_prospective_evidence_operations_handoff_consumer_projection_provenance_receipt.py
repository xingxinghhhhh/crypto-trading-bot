import hashlib
import json
import sys
from pathlib import Path

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as bundle_admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection as projection_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_materialization as materialization_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_provenance_receipt as module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_verification as verification_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery as delivery_module
import crypto_bot.market.prospective_evidence_operations_handoff_delivery_admission as admission_module
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
    root = tmp_path_factory.mktemp("cpr")
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


def _upstream_args(inputs: dict[str, Path]) -> tuple[Path, Path, Path, Path]:
    return inputs["admission"], inputs["delivery"], SNAPSHOT, ROLLOVER


def _materialized(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, name: str = "projection"
) -> tuple[Path, dict[str, projection_module.JSONScalar]]:
    repo = inputs["root"]
    monkeypatch.setattr(materialization_module, "_repo_root", lambda *_paths: repo)
    result = materialization_module.materialize_governance_fresh_current_operations_handoff_projection(
        *_upstream_args(inputs), repo / f"artifacts/{name}"
    )
    monkeypatch.setattr(module, "_repo_root", lambda *_paths: repo)
    return Path(result.projection_path), result.projection


def _receipt(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, name: str = "receipt"
) -> module.ConsumerProjectionProvenanceReceiptResult:
    projection, _ = _materialized(inputs, monkeypatch)
    return module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
        projection,
        *_upstream_args(inputs),
        inputs["root"] / f"artifacts/{name}",
    )


def test_valid_materialized_projection_produces_exact_six_key_receipt(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _receipt(inputs, monkeypatch)
    assert set(result.receipt) == {
        "receipt_version",
        "projection_sha256",
        "delivery_admission_identity",
        "handoff_delivery_identity",
        "current_operations_snapshot_identity",
        "epoch_closeout_rollover_identity",
    }
    assert all(isinstance(value, str) for value in result.receipt.values())
    assert result.receipt["receipt_version"] == module.RECEIPT_VERSION
    assert result.receipt["epoch_closeout_rollover_identity"] == ROLLOVER.stem.rsplit(".", 1)[-1]


def test_receipt_bytes_are_canonical_and_content_addressed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _receipt(inputs, monkeypatch)
    path = Path(result.receipt_path)
    expected = json.dumps(
        result.receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert path.read_bytes() == expected
    assert not expected.startswith(b"\xef\xbb\xbf")
    assert not expected.endswith(b"\n")
    assert hashlib.sha256(expected).hexdigest() == result.receipt_sha256
    assert path.name == f"{module.RECEIPT_PREFIX}.{result.receipt_sha256}.json"


def test_projection_digest_is_exact_materialized_file_digest(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _ = _materialized(inputs, monkeypatch)
    result = module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
        projection,
        *_upstream_args(inputs),
        inputs["root"] / "artifacts/receipt-digest",
    )
    assert result.receipt["projection_sha256"] == hashlib.sha256(projection.read_bytes()).hexdigest()


def test_repeated_and_cross_directory_materialization_is_idempotent(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _ = _materialized(inputs, monkeypatch)
    first = module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
        projection, *_upstream_args(inputs), inputs["root"] / "artifacts/one"
    )
    before = Path(first.receipt_path).read_bytes()
    second = module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
        projection, *_upstream_args(inputs), inputs["root"] / "artifacts/one"
    )
    third = module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
        projection, *_upstream_args(inputs), inputs["root"] / "artifacts/two"
    )
    assert second == first
    assert third.receipt_sha256 == first.receipt_sha256
    assert Path(first.receipt_path).read_bytes() == before == Path(third.receipt_path).read_bytes()


def test_same_name_different_bytes_fails_without_overwrite(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _receipt(inputs, monkeypatch)
    path = Path(result.receipt_path)
    original = path.read_bytes()
    path.write_bytes(original + b"tampered")
    projection = next((inputs["root"] / "artifacts/projection").glob("*.json"))
    with pytest.raises(MarketDataError, match="receipt collision"):
        module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
            projection, *_upstream_args(inputs), path.parent
        )
    assert path.read_bytes() == original + b"tampered"


def test_projection_tamper_prevents_receipt_commit(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _ = _materialized(inputs, monkeypatch)
    original = projection.read_bytes()
    try:
        projection.write_bytes(original + b"tampered")
        with pytest.raises(MarketDataError):
            module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
                projection, *_upstream_args(inputs), inputs["root"] / "artifacts/tamper"
            )
        assert not (inputs["root"] / "artifacts/tamper").exists()
    finally:
        projection.write_bytes(original)


def test_projection_filename_digest_tamper_is_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _ = _materialized(inputs, monkeypatch)
    wrong = projection.with_name(f"{module.PROJECTION_PREFIX}.{'0' * 64}.json")
    projection.rename(wrong)
    try:
        with pytest.raises(MarketDataError, match="materialized identity"):
            module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
                wrong, *_upstream_args(inputs), inputs["root"] / "artifacts/filename-tamper"
            )
    finally:
        wrong.rename(projection)


def test_canonical_but_non_materialized_projection_is_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    materialized, projection = _materialized(inputs, monkeypatch)
    copy = materialized.parent / "projection-copy.json"
    copy.write_bytes(projection_module.format_governance_fresh_current_operations_handoff_projection(projection).encode("utf-8"))
    with pytest.raises(MarketDataError, match="filename mismatch"):
        module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
            copy, *_upstream_args(inputs), inputs["root"] / "artifacts/non-materialized"
        )


def test_projection_terminal_newline_is_rejected_even_if_verifier_accepts(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    materialized, _ = _materialized(inputs, monkeypatch)
    newline = materialized.parent / f"{module.PROJECTION_PREFIX}.{'1' * 64}.json"
    newline.write_bytes(materialized.read_bytes() + b"\n")
    with pytest.raises(MarketDataError, match="materialized bytes mismatch|identity mismatch"):
        module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
            newline, *_upstream_args(inputs), inputs["root"] / "artifacts/newline"
        )


def test_projection_symlink_is_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    materialized, _ = _materialized(inputs, monkeypatch)
    link = materialized.parent / f"{module.PROJECTION_PREFIX}.{'2' * 64}.json"
    try:
        link.symlink_to(materialized)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(MarketDataError, match="symlink"):
        module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
            link, *_upstream_args(inputs), inputs["root"] / "artifacts/symlink"
        )


@pytest.mark.parametrize("message", ["superseded", "assembly mismatch", "parent tamper"])
def test_verifier_failures_produce_no_receipt(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    projection, _ = _materialized(inputs, monkeypatch)

    def fail_verifier(*_args: object) -> object:
        raise MarketDataError(message)

    monkeypatch.setattr(module, "verify_governance_fresh_current_operations_handoff_projection", fail_verifier)
    output = inputs["root"] / f"artifacts/{message.replace(' ', '-')}-receipt"
    with pytest.raises(MarketDataError, match=message):
        module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
            projection, *_upstream_args(inputs), output
        )
    assert not output.exists()


@pytest.mark.parametrize("bad_output", ["reports/receipt", "artifacts/../reports/receipt"])
def test_output_path_safety(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, bad_output: str
) -> None:
    projection, _ = _materialized(inputs, monkeypatch)
    with pytest.raises(MarketDataError, match="output path escape"):
        module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
            projection, *_upstream_args(inputs), inputs["root"] / bad_output
        )


def test_receipt_layer_only_replays_existing_verifier(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection_path, projection = _materialized(inputs, monkeypatch)
    calls: list[str] = []

    def fake_verifier(*_args: object) -> dict[str, projection_module.JSONScalar]:
        calls.append("verify")
        return projection

    monkeypatch.setattr(module, "verify_governance_fresh_current_operations_handoff_projection", fake_verifier)
    monkeypatch.setattr(
        verification_module,
        "project_governance_fresh_current_operations_handoff",
        lambda *_args: (_ for _ in ()).throw(AssertionError("receipt bypassed verifier")),
    )
    result = module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
        projection_path, *_upstream_args(inputs), inputs["root"] / "artifacts/layering"
    )
    assert calls == ["verify"]
    assert result.receipt["projection_sha256"] == hashlib.sha256(projection_path.read_bytes()).hexdigest()


def _cli_args(projection: Path, inputs: dict[str, Path], output: Path) -> list[str]:
    return [
        "crypto-bot",
        "materialize-verified-current-operations-handoff-projection-receipt",
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
        "--output-dir",
        str(output),
    ]


def test_cli_success_outputs_receipt_identity_and_path(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    projection, _ = _materialized(inputs, monkeypatch)
    output = inputs["root"] / "artifacts/cli"
    monkeypatch.setattr(module, "_repo_root", lambda *_paths: inputs["root"])
    monkeypatch.setattr(sys, "argv", _cli_args(projection, inputs, output))
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.err == ""
    assert "consumer_projection_receipt_sha256: " in captured.out
    assert "exported_consumer_projection_receipt: " in captured.out
    assert Path(captured.out.split("exported_consumer_projection_receipt: ", 1)[1].strip()).is_file()


def test_cli_failure_has_empty_stdout(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    projection, _ = _materialized(inputs, monkeypatch)
    output = inputs["root"] / "reports/cli-failure"
    monkeypatch.setattr(module, "_repo_root", lambda *_paths: inputs["root"])
    monkeypatch.setattr(sys, "argv", _cli_args(projection, inputs, output))
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert "provenance_receipt_failed" in captured.err


def test_cli_missing_required_argument_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["crypto-bot", "materialize-verified-current-operations-handoff-projection-receipt"],
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert "required" in captured.err
