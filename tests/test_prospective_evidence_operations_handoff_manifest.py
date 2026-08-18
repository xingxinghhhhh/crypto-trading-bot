from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as admission_module
import crypto_bot.market.prospective_evidence_operations_handoff_manifest as module


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = next(
    (ROOT / "reports/prospective-evidence-operations-snapshot").glob("*.json")
)
BUNDLE_CONFIG = ROOT / bundle_module.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / admission_module.DEFAULT_CONFIG_FILENAME
MANIFEST_CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


@pytest.fixture(scope="module")
def parents(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("handoff-parents")
    bundle = bundle_module.build_prospective_evidence_operations_bundle(
        SNAPSHOT, BUNDLE_CONFIG, root / "bundle"
    )
    admission = admission_module.freeze_prospective_evidence_operations_bundle_admission(
        bundle.export_paths["report"],
        SNAPSHOT,
        ADMISSION_CONFIG,
        root / "admission",
    )
    return {
        "root": root,
        "bundle": Path(bundle.export_paths["report"]),
        "admission": Path(admission.export_paths["report"]),
    }


def _build(parents: dict[str, Path], output: Path) -> module.OperationsHandoffManifestResult:
    return module.build_prospective_evidence_operations_handoff_manifest(
        parents["admission"],
        parents["bundle"],
        MANIFEST_CONFIG,
        output,
    )


def _copy_manifest(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.parent.iterdir():
        if item.is_file():
            shutil.copy2(item, destination / item.name)
    return destination / source.name


def _stale_snapshot() -> Path:
    source = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    identity = dict(source["identity"])
    identity["handoff_manifest_stale_test_nonce"] = "stale-but-valid-test-fixture"
    snapshot_sha = hashlib.sha256(
        json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    report = dict(source)
    report["identity"] = identity
    report["snapshot_sha256"] = snapshot_sha
    path = SNAPSHOT.parent / f"prospective-evidence-operations-snapshot.{snapshot_sha}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def test_current_admitted_handoff_projects_validated_state(parents: dict[str, Path], tmp_path: Path) -> None:
    result = _build(parents, tmp_path / "manifest")
    report = result.report
    assert report["status"] == "handoff_manifest_ready"
    assert report["handoff_manifest_materialized"] is True
    assert report["consumer_readable"] is True
    assert report["consumer_action_authorized"] is False
    assert report["state_mutation_authorized"] is False
    assert report["current_samples"] == 160
    assert report["sample_threshold"] == 500
    assert report["remaining_samples"] == 340
    assert report["governed_stage"] == "awaiting_real_membership_epoch_progress"
    assert report["blocking_gate"] == "membership_epoch_progress"
    assert report["next_legal_action"] == "await_real_membership_epoch_progress"
    validated = module.validate_prospective_evidence_operations_handoff_manifest(
        result.export_paths["report"], parents["admission"], parents["bundle"]
    )
    assert validated["manifest_sha256"] == report["manifest_sha256"]


def test_stale_admission_is_blocked_and_has_no_status_row(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    stale = _stale_snapshot()
    try:
        admission = admission_module.freeze_prospective_evidence_operations_bundle_admission(
            parents["bundle"], stale, ADMISSION_CONFIG, tmp_path / "stale-admission"
        )
        result = module.build_prospective_evidence_operations_handoff_manifest(
            admission.export_paths["report"],
            parents["bundle"],
            MANIFEST_CONFIG,
            tmp_path / "stale-manifest",
        )
        assert result.report["status"] == "blocked_bundle_not_current"
        assert result.report["handoff_manifest_materialized"] is False
        assert result.report["consumer_readable"] is False
        assert result.report["manifest_state"] is None
        assert result.report["artifacts"]["status"]["row_count"] == 0
        module.validate_prospective_evidence_operations_handoff_manifest(
            result.export_paths["report"], admission.export_paths["report"], parents["bundle"]
        )
    finally:
        stale.unlink(missing_ok=True)


def test_admission_tamper_fails_closed(parents: dict[str, Path], tmp_path: Path) -> None:
    result = _build(parents, tmp_path / "manifest")
    copied = _copy_manifest(Path(result.export_paths["report"]), tmp_path / "tampered-admission")
    admission = json.loads(parents["admission"].read_text(encoding="utf-8"))
    admission["status"] = "blocked_stale_bundle"
    admission_path = tmp_path / "tampered-admission.json"
    admission_path.write_text(json.dumps(admission), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_handoff_manifest(
            copied, admission_path, parents["bundle"]
        )


def test_bundle_tamper_fails_closed(parents: dict[str, Path], tmp_path: Path) -> None:
    result = _build(parents, tmp_path / "manifest")
    copied = _copy_manifest(Path(result.export_paths["report"]), tmp_path / "tampered-bundle")
    bundle = json.loads(parents["bundle"].read_text(encoding="utf-8"))
    bundle["bundle_status"] = "tampered"
    bundle_path = tmp_path / "tampered-bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(bundle_module.MarketDataError):
        module.validate_prospective_evidence_operations_handoff_manifest(
            copied, parents["admission"], bundle_path
        )


def test_projection_tamper_fails_closed(parents: dict[str, Path], tmp_path: Path) -> None:
    result = _build(parents, tmp_path / "manifest")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["current_samples"] = 500
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_handoff_manifest(
            report_path, parents["admission"], parents["bundle"]
        )


def test_authorization_escalation_fails_closed(parents: dict[str, Path], tmp_path: Path) -> None:
    result = _build(parents, tmp_path / "manifest")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["consumer_action_authorized"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_handoff_manifest(
            report_path, parents["admission"], parents["bundle"]
        )


def test_parent_identity_mismatch_fails_closed(parents: dict[str, Path], tmp_path: Path) -> None:
    stale = _stale_snapshot()
    try:
        stale_bundle = bundle_module.build_prospective_evidence_operations_bundle(
            stale, BUNDLE_CONFIG, tmp_path / "stale-bundle"
        )
        result = _build(parents, tmp_path / "manifest")
        with pytest.raises(module.MarketDataError, match="bundle identity"):
            module.validate_prospective_evidence_operations_handoff_manifest(
                result.export_paths["report"],
                parents["admission"],
                stale_bundle.export_paths["report"],
            )
    finally:
        stale.unlink(missing_ok=True)


def test_two_output_directories_are_byte_identical(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    first = _build(parents, tmp_path / "one")
    second = _build(parents, tmp_path / "two")
    assert Path(first.export_paths["report"]).name == Path(second.export_paths["report"]).name
    for key in ("status", "dependencies", "constraints", "report"):
        assert Path(first.export_paths[key]).read_bytes() == Path(second.export_paths[key]).read_bytes()


def test_content_addressed_collision_fails_closed(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "manifest")
    status_path = Path(result.export_paths["status"])
    status_path.write_bytes(status_path.read_bytes() + b"tamper\n")
    with pytest.raises(module.MarketDataError, match="collision"):
        _build(parents, tmp_path / "manifest")


def test_output_must_stay_outside_reports() -> None:
    with pytest.raises(ValueError, match="outside source reports"):
        module._output_dir(ROOT, ROOT / "reports" / "handoff")


def test_config_and_parent_inputs_fail_closed(parents: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="filename"):
        module.load_operations_handoff_manifest_config(tmp_path / "wrong.yaml")
    drifted = tmp_path / module.DEFAULT_CONFIG_FILENAME
    drifted.write_text("schema_version: 1\n", encoding="utf-8")
    with pytest.raises(module.MarketDataError, match="policy mismatch"):
        module.load_operations_handoff_manifest_config(drifted)
    result = _build(parents, tmp_path / "manifest")
    with pytest.raises(ValueError, match="inputs are required"):
        module.validate_prospective_evidence_operations_handoff_manifest(
            result.export_paths["report"]
        )
    wrong_name = tmp_path / "wrong-name.json"
    wrong_name.write_bytes(Path(result.export_paths["report"]).read_bytes())
    with pytest.raises(module.MarketDataError, match="identity mismatch"):
        module.validate_prospective_evidence_operations_handoff_manifest(
            wrong_name, parents["admission"], parents["bundle"]
        )


def test_sidecar_integrity_and_low_level_helpers_fail_closed(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    result = _build(parents, tmp_path / "manifest")
    copied = _copy_manifest(Path(result.export_paths["report"]), tmp_path / "sidecar-bytes")
    status_path = next(copied.parent.glob("*.status.csv"))
    status_path.write_bytes(status_path.read_bytes() + b"tamper\n")
    with pytest.raises(module.MarketDataError, match="artifact bytes"):
        module.validate_prospective_evidence_operations_handoff_manifest(
            copied, parents["admission"], parents["bundle"]
        )

    copied_missing = _copy_manifest(
        Path(result.export_paths["report"]), tmp_path / "sidecar-missing"
    )
    next(copied_missing.parent.glob("*.constraints.csv")).unlink()
    with pytest.raises(module.MarketDataError, match="artifact missing"):
        module.validate_prospective_evidence_operations_handoff_manifest(
            copied_missing, parents["admission"], parents["bundle"]
        )

    copied_metadata = _copy_manifest(
        Path(result.export_paths["report"]), tmp_path / "sidecar-metadata"
    )
    metadata = json.loads(copied_metadata.read_text(encoding="utf-8"))
    metadata["artifacts"]["status"]["sha256"] = "0" * 64
    copied_metadata.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(module.MarketDataError, match="artifact metadata"):
        module.validate_prospective_evidence_operations_handoff_manifest(
            copied_metadata, parents["admission"], parents["bundle"]
        )

    copied_row_count = _copy_manifest(
        Path(result.export_paths["report"]), tmp_path / "sidecar-row-count"
    )
    row_count = json.loads(copied_row_count.read_text(encoding="utf-8"))
    row_count["artifacts"]["status"]["row_count"] = 0
    copied_row_count.write_text(json.dumps(row_count), encoding="utf-8")
    with pytest.raises(module.MarketDataError, match="artifact metadata"):
        module.validate_prospective_evidence_operations_handoff_manifest(
            copied_row_count, parents["admission"], parents["bundle"]
        )

    with pytest.raises(module.MarketDataError, match="json read failed"):
        module._load_json(tmp_path / "missing.json")
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("[]", encoding="utf-8")
    with pytest.raises(module.MarketDataError, match="json shape"):
        module._load_json(invalid_json)
    with pytest.raises(module.MarketDataError, match="CSV schema"):
        module._read_csv_exact(b"wrong\n", module.STATUS_FIELDS)
    with pytest.raises(module.MarketDataError, match="duplicate key"):
        module._read_key_values(b"key,value\nx,1\nx,2\n")
    assert module._csv_value(None) == ""
    same_path = tmp_path / "same-bytes"
    module._commit_bytes(same_path, b"same")
    module._commit_bytes(same_path, b"same")


def test_projection_reducer_rejects_inconsistent_parents(
    parents: dict[str, Path], tmp_path: Path
) -> None:
    admission = json.loads(parents["admission"].read_text(encoding="utf-8"))
    bundle = json.loads(parents["bundle"].read_text(encoding="utf-8"))
    changed = dict(admission)
    changed["bundle_sha256"] = "0" * 64
    with pytest.raises(module.MarketDataError, match="bundle identity"):
        module._derive_manifest(changed, bundle)
    changed = dict(admission)
    changed["source_operations_snapshot_identity"] = "0" * 64
    with pytest.raises(module.MarketDataError, match="source identity"):
        module._derive_manifest(changed, bundle)
    changed = dict(admission)
    changed["source_projection"] = None
    with pytest.raises(module.MarketDataError, match="projection missing"):
        module._derive_manifest(changed, bundle)
    changed = dict(admission)
    changed["source_projection"] = dict(admission["source_projection"])
    changed["source_projection"]["current_samples"] = 500
    with pytest.raises(module.MarketDataError, match="projection mismatch"):
        module._derive_manifest(changed, bundle)
    changed = dict(admission)
    changed["status"] = "unexpected"
    with pytest.raises(module.MarketDataError, match="status invalid"):
        module._derive_manifest(changed, bundle)
    with pytest.raises(module.MarketDataError, match="repo root missing"):
        module._repo_root(tmp_path / "missing-repo")
    with pytest.raises(module.MarketDataError, match="policy mismatch"):
        module._validate_policy({"policy_id": "wrong", "policy": {}})
    with pytest.raises(module.MarketDataError, match="authorization claims"):
        module._validate_policy({"policy_id": module.POLICY_ID, "policy": module.POLICY_FIELDS, "claims": {}})
    with pytest.raises(module.MarketDataError, match="mutation claims"):
        module._validate_policy(
            {
                "policy_id": module.POLICY_ID,
                "policy": module.POLICY_FIELDS,
                "claims": {"consumer_action_authorized": False, "state_mutation_authorized": True},
            }
        )
