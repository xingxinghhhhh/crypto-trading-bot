from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import crypto_bot.market.prospective_capture_attempt_evidence_adapter as module
from crypto_bot.errors import MarketDataError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME
PARENT = ROOT / (
    "reports/prospective-capture-attempt-receipt-chain/"
    "prospective-capture-attempt-receipt-chain.3fef210901ee2ba1db387ff8468da069809f4d0c0a6914f87bedaa699b89b538.json"
)
CURRENT_SNAPSHOT = next(
    path
    for path in (ROOT / "reports/okx-future-universe-snapshot").glob("*.json")
    if not path.name.endswith(".raw.json")
)
REQUEST_POLICY = "6affbc2714a723562402cae7a19dd9a7332884c29370070e1177a3ae5d304a84"


@pytest.fixture
def evidence_workspace() -> Path:
    path = ROOT / "reports/prospective-capture-attempt-evidence-adapter-tests"
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _evidence(kind: str, response: str | None = None, snapshot: str | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "evidence_kind": kind,
        "attempt_started_at": "2026-08-16T10:30:00Z",
        "request_policy_sha256": REQUEST_POLICY,
        "failure_code": "fixture_failure" if kind != "snapshot_validation_pass" else None,
        "exception_class": "FixtureError" if kind == "transport_failure" else None,
        "response_artifact_path": response,
        "snapshot_marker_path": snapshot,
    }


def _write_evidence(workspace: Path, payload: dict[str, object], name: str = "evidence.json") -> Path:
    path = workspace / name
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _snapshot_fixture(workspace: Path) -> Path:
    for source in CURRENT_SNAPSHOT.parent.iterdir():
        if source.is_file():
            (workspace / source.name).write_bytes(source.read_bytes())
    report = json.loads(CURRENT_SNAPSHOT.read_text(encoding="utf-8"))
    report["identity"]["received_at"] = "2026-08-16T10:30:00+00:00"
    snapshot_sha = module._digest(module._canonical_json_bytes(report["identity"]))
    report["snapshot_sha256"] = snapshot_sha
    report["artifacts"]["report"]["filename"] = f"okx-universe-snapshot.{snapshot_sha}.json"
    marker = workspace / report["artifacts"]["report"]["filename"]
    marker.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return marker


def _receipt(result: module.ReceiptMaterializationResult) -> dict[str, object]:
    return json.loads(Path(result.export_paths["receipt"]).read_text(encoding="utf-8"))


def test_transport_materialization_is_replayed_and_safe(evidence_workspace: Path) -> None:
    evidence = _write_evidence(evidence_workspace, _evidence("transport_failure"))
    result = module.materialize_prospective_capture_attempt_receipt(
        PARENT, evidence, CONFIG, evidence_workspace
    )
    receipt = _receipt(result)
    assert receipt["outcome"] == "transport_failed"
    assert receipt["attempt_number"] == 1
    assert receipt["previous_receipt_sha256"] is None
    assert receipt["response_sha256"] is None
    assert receipt["accepted"] is False
    assert result.report["fixture_only"] is True
    assert result.report["network_activity_performed"] is False
    assert module.validate_receipt_materialization(result.export_paths["report"])["materialization_sha256"] == result.report["materialization_sha256"]


def test_validation_failure_hashes_raw_local_response(evidence_workspace: Path) -> None:
    response = evidence_workspace / "response.raw"
    response.write_bytes(b"raw response fixture")
    evidence = _write_evidence(
        evidence_workspace,
        _evidence("snapshot_validation_failure", response.relative_to(ROOT).as_posix()),
    )
    result = module.materialize_prospective_capture_attempt_receipt(
        PARENT, evidence, CONFIG, evidence_workspace
    )
    receipt = _receipt(result)
    assert receipt["outcome"] == "validation_failed"
    assert receipt["response_sha256"] == module._digest(response.read_bytes())
    assert receipt["snapshot_marker_sha256"] is None
    assert receipt["accepted"] is False


def test_validation_pass_uses_public_snapshot_validator(evidence_workspace: Path) -> None:
    response = evidence_workspace / "response.raw"
    response.write_bytes(b"canonical response fixture")
    marker = _snapshot_fixture(evidence_workspace)
    evidence = _write_evidence(
        evidence_workspace,
        _evidence(
            "snapshot_validation_pass",
            response.relative_to(ROOT).as_posix(),
            marker.relative_to(ROOT).as_posix(),
        ),
    )
    result = module.materialize_prospective_capture_attempt_receipt(
        PARENT, evidence, CONFIG, evidence_workspace
    )
    receipt = _receipt(result)
    assert receipt["outcome"] == "validation_passed"
    assert receipt["accepted"] is True
    assert receipt["validator_status"] == "passed"
    assert receipt["snapshot_marker_sha256"] == receipt["snapshot_identity"]
    assert result.report["next_attempt_number"] == 1


def test_same_evidence_is_deterministic_across_output_dirs(evidence_workspace: Path) -> None:
    evidence = _write_evidence(evidence_workspace, _evidence("transport_failure"))
    first_dir = ROOT / "reports/prospective-capture-attempt-evidence-adapter-one"
    second_dir = ROOT / "reports/prospective-capture-attempt-evidence-adapter-two"
    for path in (first_dir, second_dir):
        if path.exists():
            shutil.rmtree(path)
    try:
        first = module.materialize_prospective_capture_attempt_receipt(PARENT, evidence, CONFIG, first_dir)
        second = module.materialize_prospective_capture_attempt_receipt(PARENT, evidence, CONFIG, second_dir)
        assert first.report["materialization_sha256"] == second.report["materialization_sha256"]
        for key in first.export_paths:
            assert Path(first.export_paths[key]).name == Path(second.export_paths[key]).name
            assert Path(first.export_paths[key]).read_bytes() == Path(second.export_paths[key]).read_bytes()
    finally:
        for path in (first_dir, second_dir):
            shutil.rmtree(path, ignore_errors=True)


@pytest.mark.parametrize(
    "mutation",
    ["extra", "manual_outcome", "manual_acceptance", "manual_attempt", "manual_previous", "wrong_policy", "outside_window"],
)
def test_evidence_fail_closed(mutation: str, evidence_workspace: Path) -> None:
    payload = _evidence("transport_failure")
    if mutation == "extra":
        payload["outcome"] = "transport_failed"
    elif mutation == "manual_outcome":
        payload["outcome"] = "transport_failed"
    elif mutation == "manual_acceptance":
        payload["accepted"] = True
    elif mutation == "manual_attempt":
        payload["attempt_number"] = 1
    elif mutation == "manual_previous":
        payload["previous_receipt_sha256"] = None
    elif mutation == "wrong_policy":
        payload["request_policy_sha256"] = "0" * 64
    else:
        payload["attempt_started_at"] = "2026-08-16T11:00:00Z"
    evidence = _write_evidence(evidence_workspace, payload)
    with pytest.raises(MarketDataError):
        module.materialize_prospective_capture_attempt_receipt(PARENT, evidence, CONFIG, evidence_workspace)


def test_config_and_parent_guards(evidence_workspace: Path) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["network_access_prohibited"] = False
    drift = evidence_workspace / CONFIG.name
    drift.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.load_evidence_adapter_config(drift)
    evidence = _write_evidence(evidence_workspace, _evidence("transport_failure"))
    with pytest.raises(MarketDataError, match="parent chain"):
        module._validate_parent_chain({"contract_status": "x", "receipt_count": 1})
    with pytest.raises(ValueError):
        module.materialize_prospective_capture_attempt_receipt(PARENT, evidence, CONFIG, ROOT.parent / "outside")


def test_collision_and_cli_round_trip(evidence_workspace: Path) -> None:
    target = evidence_workspace / "collision"
    module._commit_bytes(target, b"same")
    module._commit_bytes(target, b"same")
    with pytest.raises(MarketDataError):
        module._commit_bytes(target, b"different")
    evidence = _write_evidence(evidence_workspace, _evidence("transport_failure"), "cli-evidence.json")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "materialize-prospective-capture-attempt-receipt",
            "--receipt-chain",
            str(PARENT),
            "--attempt-evidence",
            str(evidence),
            "--config",
            str(CONFIG),
            "--output-dir",
            str(evidence_workspace),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "real_attempt_materialized: false" in completed.stdout


def test_config_shape_and_scalar_guards(
    evidence_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError):
        module.load_evidence_adapter_config(evidence_workspace / "wrong.yaml")
    shape = evidence_workspace / CONFIG.name
    shape.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(module.yaml, "safe_load", lambda _text: [])
    with pytest.raises(MarketDataError, match="shape mismatch"):
        module.load_evidence_adapter_config(shape)
    monkeypatch.undo()
    with pytest.raises(MarketDataError, match="timestamp invalid"):
        module._parse_utc("bad")
    with pytest.raises(MarketDataError, match="timezone-aware"):
        module._parse_utc("2026-08-16T10:00:00")
    assert module._is_sha256("a" * 64)
    assert not module._is_sha256("g" * 64)
    assert not module._is_sha256("short")
    with pytest.raises(MarketDataError, match="repo root"):
        module._repo_root(tmp_path)
    with pytest.raises(MarketDataError, match="json read"):
        module._load_json(evidence_workspace / "missing.json")
    bad_shape = evidence_workspace / "list.json"
    bad_shape.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="json shape"):
        module._load_json(bad_shape)


@pytest.mark.parametrize("mutation", ["identity", "artifacts", "metadata", "bytes", "receipt", "safety"])
def test_materialization_validator_fail_closed(
    mutation: str, evidence_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = _write_evidence(evidence_workspace, _evidence("transport_failure"))
    result = module.materialize_prospective_capture_attempt_receipt(PARENT, evidence, CONFIG, evidence_workspace)
    report_path = Path(result.export_paths["report"])
    original_report = json.loads(report_path.read_text(encoding="utf-8"))
    report = json.loads(json.dumps(original_report))
    original_load = module._load_json
    if mutation == "identity":
        report["identity"]["evidence_kind"] = "tampered"
    elif mutation == "artifacts":
        report["artifacts"] = []
    elif mutation == "metadata":
        report["artifacts"]["receipt"] = {}
    elif mutation == "bytes":
        report["artifacts"]["receipt"]["filename"] = "missing.receipt.json"
    elif mutation == "receipt":
        receipt_path = report_path.parent / report["artifacts"]["receipt"]["filename"]
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["failure_code"] = "tampered"
        monkeypatch.setattr(module, "_load_json", lambda path: receipt if path == receipt_path else (report if path == report_path else original_load(path)))
    else:
        report["fixture_only"] = False
    if mutation != "receipt":
        monkeypatch.setattr(module, "_load_json", lambda path: report if path == report_path else original_load(path))
    with pytest.raises(MarketDataError):
        module.validate_receipt_materialization(report_path)


def test_materialization_parent_tail_and_replay_guards(
    evidence_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(MarketDataError, match="retry-open"):
        module._validate_parent_chain(
            {
                "contract_status": "verified_prospective_capture_attempt_receipt_chain",
                "receipt_count": 0,
                "chain_status": "awaiting_attempt",
                "next_attempt_number": 1,
                "next_attempt_permitted": True,
                "accepted_attempt_count": 0,
                "accepted_snapshot_identity": None,
                "identity": {"receipt_order": ["a"]},
            }
        )
    evidence = _write_evidence(evidence_workspace, _evidence("transport_failure"))
    monkeypatch.setattr(
        module,
        "audit_prospective_capture_attempt_receipt_chain",
        lambda *_args: SimpleNamespace(report={"receipt_count": 0, "chain_status": "awaiting_attempt"}),
    )
    with pytest.raises(MarketDataError, match="replay state"):
        module.materialize_prospective_capture_attempt_receipt(PARENT, evidence, CONFIG, evidence_workspace)


@pytest.mark.parametrize("mutation", ["version", "kind", "timestamp", "policy", "field", "transport", "validation", "pass"])
def test_evidence_shape_semantics_fail_closed(mutation: str) -> None:
    payload = _evidence("transport_failure")
    if mutation == "version":
        payload["schema_version"] = 2
    elif mutation == "kind":
        payload["evidence_kind"] = "other"
    elif mutation == "timestamp":
        payload["attempt_started_at"] = None
    elif mutation == "policy":
        payload["request_policy_sha256"] = "bad"
    elif mutation == "field":
        payload["exception_class"] = 1
    elif mutation == "transport":
        payload["response_artifact_path"] = "reports/response.raw"
    elif mutation == "validation":
        payload = _evidence("snapshot_validation_failure")
        payload["response_artifact_path"] = None
    else:
        payload = _evidence("snapshot_validation_pass")
        payload["snapshot_marker_path"] = None
    with pytest.raises(MarketDataError):
        module._validate_evidence_shape(payload)


def test_repo_path_escape_and_config_path_escape(evidence_workspace: Path) -> None:
    with pytest.raises(MarketDataError, match="path escape"):
        module.validate_receipt_materialization(ROOT / "README.md")
    with pytest.raises(MarketDataError, match="escape"):
        module._repo_path(ROOT, "../outside")
    missing = evidence_workspace / "missing"
    with pytest.raises(MarketDataError, match="escape or missing"):
        module._repo_path(ROOT, str(missing))
