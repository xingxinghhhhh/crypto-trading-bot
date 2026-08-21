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
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_provenance_receipt as receipt_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_provenance_receipt_verification as module
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
    root = tmp_path_factory.mktemp("cprv")
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
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, projection_module.JSONScalar]]:
    repo = inputs["root"]
    monkeypatch.setattr(materialization_module, "_repo_root", lambda *_paths: repo)
    result = materialization_module.materialize_governance_fresh_current_operations_handoff_projection(
        *_upstream_args(inputs), repo / "artifacts/projection"
    )
    return Path(result.projection_path), result.projection


def _receipt(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, projection_module.JSONScalar], Path]:
    projection, projected = _materialized(inputs, monkeypatch)
    monkeypatch.setattr(receipt_module, "_repo_root", lambda *_paths: inputs["root"])
    result = receipt_module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
        projection,
        *_upstream_args(inputs),
        inputs["root"] / "artifacts/receipt",
    )
    return projection, projected, Path(result.receipt_path)


def _verify(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, receipt: Path, projection: Path
) -> dict[str, str]:
    monkeypatch.setattr(module, "_repo_root", lambda *_paths: inputs["root"])
    return module.verify_governance_fresh_current_operations_handoff_projection_provenance_receipt(
        receipt, projection, *_upstream_args(inputs)
    )


def _canonical(value: dict[str, str]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _write_receipt_variant(
    receipt: Path, value: dict[str, str], suffix: str = "variant"
) -> Path:
    raw = _canonical(value)
    target = receipt.parent / f"{receipt_module.RECEIPT_PREFIX}.{hashlib.sha256(raw).hexdigest()}.json"
    target.write_bytes(raw)
    return target


def _write_raw_receipt(receipt: Path, raw: bytes) -> Path:
    target = receipt.parent / f"{receipt_module.RECEIPT_PREFIX}.{hashlib.sha256(raw).hexdigest()}.json"
    target.write_bytes(raw)
    return target


def _artifact_tree(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_valid_receipt_returns_exact_persisted_six_key_dict(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    before = _artifact_tree(inputs["root"] / "artifacts")
    result = _verify(inputs, monkeypatch, receipt, projection)
    after = _artifact_tree(inputs["root"] / "artifacts")
    assert result == json.loads(receipt.read_text(encoding="utf-8"))
    assert set(result) == module.RECEIPT_FIELDS
    assert all(isinstance(value, str) for value in result.values())
    assert before == after


def test_receipt_file_is_canonical_and_content_addressed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    _verify(inputs, monkeypatch, receipt, projection)
    raw = receipt.read_bytes()
    assert raw == _canonical(json.loads(raw.decode("utf-8")))
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in raw and b"\r" not in raw
    assert hashlib.sha256(raw).hexdigest() == receipt.name.split(".")[-2]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("receipt_version", "wrong"),
        ("projection_sha256", "0" * 64),
        ("delivery_admission_identity", "tampered-admission"),
        ("handoff_delivery_identity", "tampered-delivery"),
        ("current_operations_snapshot_identity", "tampered-snapshot"),
        ("epoch_closeout_rollover_identity", "0" * 64),
    ],
)
def test_receipt_value_tamper_fails_closed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    tampered = json.loads(receipt.read_text(encoding="utf-8"))
    tampered[field] = value
    variant = _write_receipt_variant(receipt, tampered, field)
    with pytest.raises(MarketDataError):
        _verify(inputs, monkeypatch, variant, projection)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.pop("receipt_version"),
        lambda value: value.update({"extra": "nope"}),
        lambda value: value.update({"projection_sha256": 1}),
    ],
)
def test_receipt_schema_tamper_fails_closed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, mutator: object
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    tampered = json.loads(receipt.read_text(encoding="utf-8"))
    mutator(tampered)
    raw = json.dumps(tampered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    variant = receipt.parent / f"{receipt_module.RECEIPT_PREFIX}.{hashlib.sha256(raw).hexdigest()}.schema.json"
    variant.write_bytes(raw)
    with pytest.raises(MarketDataError):
        _verify(inputs, monkeypatch, variant, projection)


@pytest.mark.parametrize(
    "raw_builder",
    [
        lambda raw: b" " + raw,
        lambda raw: raw + b"\n",
        lambda raw: b"\xef\xbb\xbf" + raw,
        lambda raw: raw.replace(b":", b": ", 1),
    ],
)
def test_noncanonical_receipt_bytes_fail_closed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, raw_builder: object
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    raw = raw_builder(receipt.read_bytes())
    variant = receipt.parent / f"{receipt_module.RECEIPT_PREFIX}.{hashlib.sha256(raw).hexdigest()}.bytes.json"
    variant.write_bytes(raw)
    with pytest.raises(MarketDataError):
        _verify(inputs, monkeypatch, variant, projection)


def test_receipt_filename_digest_and_arbitrary_name_fail_closed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    wrong_digest = receipt.with_name(f"{receipt_module.RECEIPT_PREFIX}.{'0' * 64}.json")
    wrong_digest.write_bytes(receipt.read_bytes())
    arbitrary = receipt.with_name("canonical-receipt.json")
    arbitrary.write_bytes(receipt.read_bytes())
    for path in (wrong_digest, arbitrary):
        with pytest.raises(MarketDataError):
            _verify(inputs, monkeypatch, path, projection)


@pytest.mark.parametrize("raw", [b"{", b"[]", b"\xff"])
def test_receipt_encoding_and_json_fail_closed(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    variant = _write_raw_receipt(receipt, raw)
    with pytest.raises(MarketDataError):
        _verify(inputs, monkeypatch, variant, projection)


def test_receipt_canonical_bytes_mismatch_after_valid_parse_is_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["delivery_admission_identity"] = "非 ASCII"
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    variant = _write_raw_receipt(receipt, raw)
    with pytest.raises(MarketDataError, match="canonical bytes"):
        _verify(inputs, monkeypatch, variant, projection)


def test_receipt_path_must_be_under_artifacts_and_regular(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    missing = receipt.parent / "missing.json"
    with pytest.raises(MarketDataError):
        _verify(inputs, monkeypatch, missing, projection)
    outside = inputs["root"] / "reports" / receipt.name
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(receipt.read_bytes())
    with pytest.raises(MarketDataError):
        _verify(inputs, monkeypatch, outside, projection)


def test_receipt_symlink_is_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    link = receipt.parent / f"{receipt_module.RECEIPT_PREFIX}.{'1' * 64}.json"
    try:
        link.symlink_to(receipt)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(MarketDataError, match="symlink"):
        _verify(inputs, monkeypatch, link, projection)


def test_projection_bytes_tamper_is_rejected_without_writes(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    original = projection.read_bytes()
    try:
        projection.write_bytes(original + b"tamper")
        before = _artifact_tree(inputs["root"] / "artifacts")
        with pytest.raises(MarketDataError):
            _verify(inputs, monkeypatch, receipt, projection)
        assert _artifact_tree(inputs["root"] / "artifacts") == before
    finally:
        projection.write_bytes(original)


def test_projection_filename_digest_mismatch_is_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    wrong = projection.with_name(f"{module.PROJECTION_PREFIX}.{'0' * 64}.json")
    projection.rename(wrong)
    try:
        with pytest.raises(MarketDataError):
            _verify(inputs, monkeypatch, receipt, wrong)
    finally:
        wrong.rename(projection)


def test_canonical_non_materialized_projection_is_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, projected, receipt = _receipt(inputs, monkeypatch)
    copy = projection.parent / "canonical-copy.json"
    copy.write_bytes(projection_module.format_governance_fresh_current_operations_handoff_projection(projected).encode())
    with pytest.raises(MarketDataError):
        _verify(inputs, monkeypatch, receipt, copy)


def test_projection_terminal_newline_is_rejected_even_if_projection_verifier_accepts(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    raw = projection.read_bytes() + b"\n"
    newline = projection.with_name(f"{module.PROJECTION_PREFIX}.{hashlib.sha256(raw).hexdigest()}.json")
    newline.write_bytes(raw)
    with pytest.raises(MarketDataError):
        _verify(inputs, monkeypatch, receipt, newline)


@pytest.mark.parametrize("rollover_name", ["rollover.json", f"prospective-epoch-closeout-rollover.{'x' * 64}.json"])
def test_rollover_filename_identity_is_strict_after_projection_verifier(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, rollover_name: str
) -> None:
    projection, projected, receipt = _receipt(inputs, monkeypatch)
    rollover = inputs["root"] / "artifacts" / rollover_name
    rollover.write_bytes(ROLLOVER.read_bytes())
    monkeypatch.setattr(module, "_repo_root", lambda *_paths: inputs["root"])
    monkeypatch.setattr(module, "verify_governance_fresh_current_operations_handoff_projection", lambda *_args: projected)
    with pytest.raises(MarketDataError, match="rollover identity"):
        module.verify_governance_fresh_current_operations_handoff_projection_provenance_receipt(
            receipt,
            projection,
            inputs["admission"],
            inputs["delivery"],
            SNAPSHOT,
            rollover,
        )


def test_projection_identity_field_type_is_rejected_after_existing_verifier(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, projected, receipt = _receipt(inputs, monkeypatch)
    invalid = dict(projected)
    invalid["delivery_admission_identity"] = 1
    monkeypatch.setattr(module, "verify_governance_fresh_current_operations_handoff_projection", lambda *_args: invalid)
    with pytest.raises(MarketDataError, match="field type"):
        _verify(inputs, monkeypatch, receipt, projection)


@pytest.mark.parametrize("message", ["superseded rollover", "assembly mismatch", "upstream parent tamper"])
def test_stale_or_tampered_chain_fails_without_writes(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    before = _artifact_tree(inputs["root"] / "artifacts")

    def fail_verifier(*_args: object) -> object:
        raise MarketDataError(message)

    monkeypatch.setattr(module, "verify_governance_fresh_current_operations_handoff_projection", fail_verifier)
    with pytest.raises(MarketDataError, match=message):
        _verify(inputs, monkeypatch, receipt, projection)
    assert _artifact_tree(inputs["root"] / "artifacts") == before


def test_layering_calls_existing_projection_verifier_once_and_never_writer(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, projected, receipt = _receipt(inputs, monkeypatch)
    calls: list[str] = []

    def fake_verifier(*_args: object) -> dict[str, projection_module.JSONScalar]:
        calls.append("verify")
        return projected

    monkeypatch.setattr(module, "verify_governance_fresh_current_operations_handoff_projection", fake_verifier)
    monkeypatch.setattr(
        receipt_module,
        "materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt",
        lambda *_args: (_ for _ in ()).throw(AssertionError("verification called writer")),
    )
    result = _verify(inputs, monkeypatch, receipt, projection)
    assert calls == ["verify"]
    assert result["projection_sha256"] == hashlib.sha256(projection.read_bytes()).hexdigest()


def _cli_args(receipt: Path, projection: Path, inputs: dict[str, Path]) -> list[str]:
    return [
        "crypto-bot",
        "verify-current-operations-handoff-projection-receipt",
        "--receipt",
        str(receipt),
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


def test_cli_success_prints_one_canonical_receipt_line_and_writes_nothing(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    before = _artifact_tree(inputs["root"] / "artifacts")
    monkeypatch.setattr(module, "_repo_root", lambda *_paths: inputs["root"])
    monkeypatch.setattr(sys, "argv", _cli_args(receipt, projection, inputs))
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    expected = receipt.read_text(encoding="utf-8") + "\n"
    assert exc_info.value.code == 0
    assert captured.err == ""
    assert captured.out == expected
    assert _artifact_tree(inputs["root"] / "artifacts") == before


def test_cli_failure_has_empty_stdout_and_no_writes(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    original = receipt.read_bytes()
    receipt.write_bytes(original + b"\n")
    before = _artifact_tree(inputs["root"] / "artifacts")
    monkeypatch.setattr(module, "_repo_root", lambda *_paths: inputs["root"])
    monkeypatch.setattr(sys, "argv", _cli_args(receipt, projection, inputs))
    try:
        with pytest.raises(SystemExit) as exc_info:
            cli_module.main()
        captured = capsys.readouterr()
        assert exc_info.value.code == 2
        assert captured.out == ""
        assert "provenance_receipt_verification_failed" in captured.err
        assert _artifact_tree(inputs["root"] / "artifacts") == before
    finally:
        receipt.write_bytes(original)


def test_cli_missing_required_argument_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["crypto-bot", "verify-current-operations-handoff-projection-receipt"]
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert "required" in captured.err


def test_repeated_cli_verification_is_byte_deterministic(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    monkeypatch.setattr(module, "_repo_root", lambda *_paths: inputs["root"])
    outputs: list[str] = []
    for _ in range(2):
        monkeypatch.setattr(sys, "argv", _cli_args(receipt, projection, inputs))
        with pytest.raises(SystemExit) as exc_info:
            cli_module.main()
        captured = capsys.readouterr()
        assert exc_info.value.code == 0
        assert captured.err == ""
        outputs.append(captured.out)
    assert outputs[0] == outputs[1]


def test_receipt_outside_artifacts_is_rejected(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    outside = inputs["root"] / "reports" / receipt.name
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(receipt.read_bytes())
    with pytest.raises(MarketDataError):
        _verify(inputs, monkeypatch, outside, projection)


def test_receipt_verification_does_not_touch_mtime(
    inputs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projection, _, receipt = _receipt(inputs, monkeypatch)
    paths = [receipt, projection, inputs["admission"], inputs["delivery"], SNAPSHOT, ROLLOVER]
    before = {path: path.stat().st_mtime_ns for path in paths}
    _verify(inputs, monkeypatch, receipt, projection)
    assert {path: path.stat().st_mtime_ns for path in paths} == before
