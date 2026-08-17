from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import crypto_bot.market.prospective_evidence_operations_bundle as bundle_module
import crypto_bot.market.prospective_evidence_operations_bundle_admission as module


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = (
    ROOT
    / "reports/prospective-evidence-operations-snapshot/"
    "prospective-evidence-operations-snapshot.b424c38d8cb2347a567ce7c4608e31b2b1b1b0b65d61419a0db843b7374cc1d1.json"
)
BUNDLE_CONFIG = ROOT / bundle_module.DEFAULT_CONFIG_FILENAME
ADMISSION_CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def _build_bundle(output: Path) -> Path:
    result = bundle_module.build_prospective_evidence_operations_bundle(
        SNAPSHOT, BUNDLE_CONFIG, output
    )
    return Path(result.export_paths["report"])


def _build_admission(bundle_report: Path, output: Path) -> module.OperationsBundleAdmissionResult:
    return module.freeze_prospective_evidence_operations_bundle_admission(
        bundle_report, SNAPSHOT, ADMISSION_CONFIG, output
    )


def _stale_snapshot() -> Path:
    source = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    identity = dict(source["identity"])
    identity["currentness_test_nonce"] = "stale-but-valid-test-fixture"
    snapshot_sha = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    report = dict(source)
    report["identity"] = identity
    report["snapshot_sha256"] = snapshot_sha
    path = SNAPSHOT.parent / f"prospective-evidence-operations-snapshot.{snapshot_sha}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def test_current_bundle_is_admitted_and_replay_validated(tmp_path: Path) -> None:
    bundle_report = _build_bundle(tmp_path / "bundle")
    result = _build_admission(bundle_report, tmp_path / "admission")
    assert result.report["status"] == "current_bundle_admitted"
    assert result.report["bundle_currentness_admitted"] is True
    assert result.report["bundle_stale"] is False
    assert result.report["state_changed"] is False
    assert result.report["bundle_mutated"] is False
    assert result.report["authoritative_action_authorized"] is False
    assert result.report["network_activity_performed"] is False
    assert result.report["new_samples_counted"] == 0
    validated = module.validate_prospective_evidence_operations_bundle_admission(
        result.export_paths["report"], bundle_report, SNAPSHOT
    )
    assert validated["status"] == "current_bundle_admitted"


def test_admission_formatter_exposes_currentness_without_authority(tmp_path: Path) -> None:
    bundle_report = _build_bundle(tmp_path / "bundle")
    result = _build_admission(bundle_report, tmp_path / "admission")
    text = module.format_operations_bundle_admission(result)
    assert "status: current_bundle_admitted" in text
    assert "bundle_currentness_admitted: true" in text
    assert "authoritative_action_authorized: false" in text


def test_valid_but_old_bundle_is_blocked_as_stale(tmp_path: Path) -> None:
    bundle_report = _build_bundle(tmp_path / "bundle")
    stale = _stale_snapshot()
    try:
        result = module.freeze_prospective_evidence_operations_bundle_admission(
            bundle_report, stale, ADMISSION_CONFIG, tmp_path / "admission"
        )
        assert result.report["status"] == "blocked_stale_bundle"
        assert result.report["bundle_currentness_admitted"] is False
        assert result.report["bundle_stale"] is True
        assert result.report["source_operations_snapshot_identity"] != result.report[
            "current_operations_snapshot_identity"
        ]
        assert module.validate_prospective_evidence_operations_bundle_admission(
            result.export_paths["report"], bundle_report, stale
        )["status"] == "blocked_stale_bundle"
    finally:
        stale.unlink(missing_ok=True)


def test_identity_match_with_projection_mismatch_fails_closed() -> None:
    bundle = {
        "bundle_sha256": "b" * 64,
        "source_operations_snapshot_identity": "s" * 64,
        "source_snapshot_replay_valid": True,
        **bundle_module.FALSE_BUNDLE_FLAGS,
        "source_state": {field: None for field in bundle_module.SOURCE_STATE_FIELDS},
    }
    current = {"snapshot_sha256": "s" * 64, "current_samples": 500}
    with pytest.raises(module.MarketDataError, match="projection mismatch"):
        module._derive_admission(bundle, current, module.POLICY_FIELDS)


def test_admission_marker_tamper_fails_closed(tmp_path: Path) -> None:
    bundle_report = _build_bundle(tmp_path / "bundle")
    result = _build_admission(bundle_report, tmp_path / "admission")
    report_path = Path(result.export_paths["report"])
    value = json.loads(report_path.read_text(encoding="utf-8"))
    value["bundle_currentness_admitted"] = False
    report_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.validate_prospective_evidence_operations_bundle_admission(
            report_path, bundle_report, SNAPSHOT
        )


def test_admission_requires_both_replay_inputs(tmp_path: Path) -> None:
    bundle_report = _build_bundle(tmp_path / "bundle")
    result = _build_admission(bundle_report, tmp_path / "admission")
    with pytest.raises(ValueError, match="both currentness validation inputs"):
        module.validate_prospective_evidence_operations_bundle_admission(
            result.export_paths["report"], bundle_report
        )


def test_admission_rejects_config_drift(tmp_path: Path) -> None:
    config = json.loads(json.dumps(module.POLICY_FIELDS))
    config["manual_currentness_override_prohibited"] = False
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    import yaml

    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(module.MarketDataError):
        module.load_operations_bundle_admission_config(path)


def test_admission_output_must_stay_outside_reports() -> None:
    with pytest.raises(ValueError, match="outside source reports"):
        module._output_dir(ROOT, ROOT / "reports" / "prospective-evidence-operations-bundle-admission")


def test_admission_low_level_reads_and_collisions_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(module.MarketDataError):
        module._load_json(tmp_path / "missing.json")
    path = tmp_path / "content-addressed"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError, match="collision"):
        module._commit_bytes(path, b"different")
