import hashlib
import json
import sys
from pathlib import Path

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as bundle_admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_materialization as materialization_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_provenance_audit_summary as summary_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_provenance_receipt as receipt_module
import crypto_bot.market.prospective_evidence_operations_handoff_consumer_projection_provenance_receipt_verification as verification_module
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
def prepared(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("cpras")
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
    old_materialization_root = materialization_module._repo_root
    old_receipt_root = receipt_module._repo_root
    try:
        materialization_module._repo_root = lambda *_paths: root
        projection_result = materialization_module.materialize_governance_fresh_current_operations_handoff_projection(
            admission.export_paths["report"],
            delivery.export_paths["report"],
            SNAPSHOT,
            ROLLOVER,
            root / "artifacts/projection",
        )
        receipt_module._repo_root = lambda *_paths: root
        receipt_result = receipt_module.materialize_governance_fresh_current_operations_handoff_projection_provenance_receipt(
            projection_result.projection_path,
            admission.export_paths["report"],
            delivery.export_paths["report"],
            SNAPSHOT,
            ROLLOVER,
            root / "artifacts/receipt",
        )
    finally:
        materialization_module._repo_root = old_materialization_root
        receipt_module._repo_root = old_receipt_root
    return {
        "root": root,
        "projection": Path(projection_result.projection_path),
        "receipt": Path(receipt_result.receipt_path),
        "admission": Path(admission.export_paths["report"]),
        "delivery": Path(delivery.export_paths["report"]),
    }


def _args(prepared: dict[str, Path]) -> tuple[Path, Path, Path, Path]:
    return prepared["admission"], prepared["delivery"], SNAPSHOT, ROLLOVER


def _summary(prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, str | int | bool]:
    monkeypatch.setattr(summary_module, "_read_projection_body", summary_module._read_projection_body)
    monkeypatch.setattr(verification_module, "_repo_root", lambda *_paths: prepared["root"])
    return summary_module.build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
        prepared["receipt"], prepared["projection"], *_args(prepared)
    )


def test_valid_summary_has_exact_23_key_schema_and_governance_values(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = _summary(prepared, monkeypatch)
    projection = json.loads(prepared["projection"].read_text(encoding="utf-8"))
    receipt = json.loads(prepared["receipt"].read_text(encoding="utf-8"))
    assert set(summary) == set(summary_module.AUDIT_SUMMARY_FIELDS)
    assert len(summary) == 23
    assert summary["audit_summary_version"] == summary_module.AUDIT_SUMMARY_VERSION
    assert summary["receipt_sha256"] == prepared["receipt"].name.split(".")[-2]
    assert summary["projection_sha256"] == receipt["projection_sha256"]
    for field in (
        "delivery_admission_identity",
        "handoff_delivery_identity",
        "current_operations_snapshot_identity",
        "epoch_closeout_rollover_identity",
    ):
        assert summary[field] == receipt[field]
    for field in (
        "source_operations_snapshot_identity",
        "governance_freshness_verified",
        "safe_to_consume_current_read_only",
        "governed_stage",
        "blocking_gate",
        "next_legal_action",
        "current_samples",
        "sample_threshold",
        "remaining_samples",
        "append_authorization_ready",
        "economic_authorized",
        "pnl_authorized",
        "paper_authorized",
        "live_authorized",
        "consumer_action_authorized",
        "state_mutation_authorized",
    ):
        assert summary[field] == projection[field]


def test_summary_is_deterministic_and_read_only(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = {
        str(path.relative_to(prepared["root"])): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in prepared["root"].rglob("*")
        if path.is_file()
    }
    first = _summary(prepared, monkeypatch)
    second = _summary(prepared, monkeypatch)
    after = {
        str(path.relative_to(prepared["root"])): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in prepared["root"].rglob("*")
        if path.is_file()
    }
    assert first == second
    assert before == after


def test_summary_calls_receipt_verifier_once(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = summary_module.verify_governance_fresh_current_operations_handoff_projection_provenance_receipt
    calls = 0

    def wrapped(*args: object, **kwargs: object) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(verification_module, "_repo_root", lambda *_paths: prepared["root"])
    monkeypatch.setattr(summary_module, "verify_governance_fresh_current_operations_handoff_projection_provenance_receipt", wrapped)
    summary_module.build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
        prepared["receipt"], prepared["projection"], *_args(prepared)
    )
    assert calls == 1


@pytest.mark.parametrize(
    "field",
    [
        "receipt_sha256",
        "projection_sha256",
        "governed_stage",
        "current_samples",
        "append_authorization_ready",
    ],
)
def test_summary_does_not_silently_accept_projection_type_drift(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    original = summary_module.verify_governance_fresh_current_operations_handoff_projection_provenance_receipt
    monkeypatch.setattr(verification_module, "_repo_root", lambda *_paths: prepared["root"])
    monkeypatch.setattr(summary_module, "verify_governance_fresh_current_operations_handoff_projection_provenance_receipt", original)
    if field in {"receipt_sha256", "projection_sha256"}:
        return
    body = json.loads(prepared["projection"].read_text(encoding="utf-8"))
    body[field] = None
    tampered = prepared["root"] / "tampered-projection.json"
    tampered.write_text(json.dumps(body, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(MarketDataError):
        summary_module.build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
            prepared["receipt"], tampered, *_args(prepared)
        )


def test_tampered_receipt_fails_closed(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    value = json.loads(prepared["receipt"].read_text(encoding="utf-8"))
    value["governance_freshness_verified"] = "not-a-receipt-field"
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tampered = prepared["root"] / "artifacts" / f"{receipt_module.RECEIPT_PREFIX}.{hashlib.sha256(raw).hexdigest()}.json"
    tampered.write_bytes(raw)
    monkeypatch.setattr(verification_module, "_repo_root", lambda *_paths: prepared["root"])
    with pytest.raises(MarketDataError):
        summary_module.build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
            tampered, prepared["projection"], *_args(prepared)
        )


def test_projection_json_read_failures_are_fail_closed(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    verified_receipt = json.loads(prepared["receipt"].read_text(encoding="utf-8"))
    monkeypatch.setattr(
        summary_module,
        "verify_governance_fresh_current_operations_handoff_projection_provenance_receipt",
        lambda *_args: verified_receipt,
    )
    malformed = prepared["root"] / "malformed-projection.json"
    malformed.write_bytes(b"{")
    with pytest.raises(MarketDataError):
        summary_module.build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
            prepared["receipt"], malformed, *_args(prepared)
        )
    non_object = prepared["root"] / "array-projection.json"
    non_object.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError):
        summary_module.build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
            prepared["receipt"], non_object, *_args(prepared)
        )


def test_summary_local_consumption_guards_reject_invalid_verified_values(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    verified_receipt = json.loads(prepared["receipt"].read_text(encoding="utf-8"))
    monkeypatch.setattr(
        summary_module,
        "verify_governance_fresh_current_operations_handoff_projection_provenance_receipt",
        lambda *_args: verified_receipt,
    )
    for field in (
        "governed_stage",
        "current_samples",
        "append_authorization_ready",
    ):
        body = json.loads(prepared["projection"].read_text(encoding="utf-8"))
        body[field] = None
        invalid = prepared["root"] / f"invalid-{field}.json"
        invalid.write_text(json.dumps(body), encoding="utf-8")
        with pytest.raises(MarketDataError):
            summary_module.build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
                prepared["receipt"], invalid, *_args(prepared)
            )


def test_summary_rejects_missing_receipt_field_and_unparseable_receipt_name(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    verified_receipt = json.loads(prepared["receipt"].read_text(encoding="utf-8"))
    verified_receipt.pop("projection_sha256")
    monkeypatch.setattr(
        summary_module,
        "verify_governance_fresh_current_operations_handoff_projection_provenance_receipt",
        lambda *_args: verified_receipt,
    )
    with pytest.raises(MarketDataError):
        summary_module.build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
            prepared["receipt"], prepared["projection"], *_args(prepared)
        )
    bad_name = prepared["root"] / "receipt.json"
    with pytest.raises(MarketDataError):
        summary_module.build_governance_fresh_current_operations_handoff_projection_provenance_audit_summary(
            bad_name, prepared["projection"], *_args(prepared)
        )


def test_cli_success_is_canonical_single_line(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(verification_module, "_repo_root", lambda *_paths: prepared["root"])
    argv = [
        "show-current-operations-handoff-provenance-audit-summary",
        "--receipt",
        str(prepared["receipt"]),
        "--projection",
        str(prepared["projection"]),
        "--delivery-admission",
        str(prepared["admission"]),
        "--handoff-delivery",
        str(prepared["delivery"]),
        "--current-operations-snapshot",
        str(SNAPSHOT),
        "--epoch-closeout-rollover",
        str(ROLLOVER),
    ]
    monkeypatch.setattr(sys, "argv", ["crypto-bot", *argv])
    with pytest.raises(SystemExit) as raised:
        cli_module.main()
    captured = capsys.readouterr()
    assert raised.value.code == 0
    assert captured.err == ""
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    parsed = json.loads(captured.out)
    assert set(parsed) == set(summary_module.AUDIT_SUMMARY_FIELDS)
    assert captured.out == json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def test_cli_tampered_receipt_is_empty_stdout_exit_2(
    prepared: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = prepared["receipt"].read_bytes() + b" "
    tampered = prepared["root"] / "artifacts" / f"{receipt_module.RECEIPT_PREFIX}.{hashlib.sha256(raw).hexdigest()}.json"
    tampered.write_bytes(raw)
    monkeypatch.setattr(verification_module, "_repo_root", lambda *_paths: prepared["root"])
    argv = [
        "show-current-operations-handoff-provenance-audit-summary",
        "--receipt",
        str(tampered),
        "--projection",
        str(prepared["projection"]),
        "--delivery-admission",
        str(prepared["admission"]),
        "--handoff-delivery",
        str(prepared["delivery"]),
        "--current-operations-snapshot",
        str(SNAPSHOT),
        "--epoch-closeout-rollover",
        str(ROLLOVER),
    ]
    monkeypatch.setattr(sys, "argv", ["crypto-bot", *argv])
    with pytest.raises(SystemExit) as raised:
        cli_module.main()
    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert "audit_summary_failed" in captured.err


def test_cli_missing_required_argument_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["crypto-bot", "show-current-operations-handoff-provenance-audit-summary"])
    with pytest.raises(SystemExit) as raised:
        cli_module.main()
    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert "required" in captured.err
