from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.prospective_direct_1h_segment_append_authorization as module
import crypto_bot.market.prospective_direct_1h_segment_evidence as evidence_module
from crypto_bot.errors import MarketDataError


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "src/crypto_bot").mkdir(parents=True)
    (root / "reports").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    config = root / module.DEFAULT_CONFIG_FILENAME
    config.write_text((Path(__file__).parents[1] / module.DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"), encoding="utf-8")
    return root, config


def _preflight(ready: bool = True) -> dict[str, object]:
    return {
        "preflight_sha256": "p" * 64,
        "status": "preflight_ready" if ready else "blocked_prepared_chain_not_ready",
        "commit_preflight_ready": ready,
        "candidate_identity": "c" * 64 if ready else None,
        "append_authorization_eligible": False,
    }


def _evidence(fixture_only: bool = True, candidate: str = "c" * 64) -> dict[str, object]:
    return {
        "candidate_sha256": candidate,
        "capture_sha256": "a" * 64,
        "fixture_only": fixture_only,
        "market_evidence": not fixture_only,
        "sample_evidence": False,
        "segment_appended": False,
        "current_samples": 160,
        "sample_threshold": 500,
        "remaining_samples": 340,
        "new_samples_counted": 0,
        "economic_computation_authorized": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
    }


def _patch_inputs(monkeypatch: pytest.MonkeyPatch, root: Path, preflight: dict[str, object], evidence: dict[str, object]) -> tuple[Path, Path]:
    monkeypatch.setattr(module, "_repo_root", lambda _path: root)
    monkeypatch.setattr(module, "validate_preflight", lambda _path: preflight)
    monkeypatch.setattr(module, "validate_prospective_direct_1h_segment_evidence", lambda _path: evidence)
    preflight_path = root / f"reports/prospective-direct-1h-segment-append-preflight.{preflight.get('preflight_sha256')}.json"
    evidence_path = root / f"reports/prospective-direct-1h-segment-evidence.{evidence.get('candidate_sha256')}.json"
    preflight_path.write_text("{}", encoding="utf-8")
    evidence_path.write_text("{}", encoding="utf-8")
    return preflight_path, evidence_path


def test_synthetic_preflight_ready_is_blocked_and_replayable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(), _evidence(True))
    result = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/one")
    assert result.report["status"] == module.BLOCKED_EVIDENCE
    assert result.report["authorization_materialized"] is True
    assert result.report["append_authorization_eligible"] is False
    assert result.report["authoritative_write_authorized"] is False
    assert module.validate_authorization(result.export_paths["report"])["status"] == module.BLOCKED_EVIDENCE


def test_blocked_preflight_cannot_be_completed_by_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(False), _evidence(False))
    result = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/blocked")
    assert result.report["status"] == module.BLOCKED_PREFLIGHT
    assert result.report["authorization_materialized"] is False
    assert result.report["append_authorization_eligible"] is False
    assert result.report["authoritative_write_authorized"] is False
    with Path(result.export_paths["provenance"]).open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == []


def test_non_fixture_without_validated_public_source_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(), _evidence(False))
    result = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/unknown-source")
    assert result.report["status"] == module.BLOCKED_PROVENANCE
    assert result.report["authorization_materialized"] is True
    assert result.report["validated_public_only_source"] is False
    assert result.report["append_authorization_eligible"] is False


@pytest.mark.parametrize(
    "policy_id, expected_public",
    [("prospective_direct_1h_closed_epoch_extension_v1", True), ("test_only_capture_v1", False)],
)
def test_public_provenance_requires_validated_direct_okx_capture_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy_id: str,
    expected_public: bool,
) -> None:
    root, _config = _repo(tmp_path)
    report = _evidence(False)
    report["segment_candidate_materialized"] = True
    report["identity"] = {"policy": {"policy_id": "segment-evidence"}}
    evidence_path = root / f"reports/prospective-direct-1h-segment-evidence.{report['candidate_sha256']}.json"
    evidence_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(evidence_module, "_locate_capture", lambda *_args: root / "reports/capture.json")
    monkeypatch.setattr(
        evidence_module,
        "_validate_capture_lineage",
        lambda *_args: {
            "report": {
                "capture_status": evidence_module.CAPTURE_STATUS,
                "identity": {"policy_id": policy_id},
                "future_only_membership_evidence": True,
                "profitability_evidence": False,
                "pnl_computation_authorized": False,
                "readiness_changed": False,
            }
        },
    )
    provenance = evidence_module.derive_prospective_direct_1h_segment_evidence_provenance(
        evidence_path, report
    )
    assert provenance["public_only"] is expected_public
    assert provenance["source_provider"] == ("OKX" if expected_public else "")


def test_candidate_lineage_mismatch_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(), _evidence(True, "d" * 64))
    result = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/mismatch")
    assert result.report["status"] == module.BLOCKED_LINEAGE
    assert result.report["candidate_lineage_match"] is False


def test_pure_positive_predicate_is_test_only_and_has_no_output(tmp_path: Path) -> None:
    decision = module.evaluate_authorization_gate({"preflight_ready": True, "candidate_lineage_match": True, "fixture_only": False, "market_evidence": True, "public_only": True, "test_only": True})
    assert decision == {"append_authorization_eligible": True, "authoritative_write_authorized": True, "reason": "eligible"}
    assert not list((tmp_path / "reports").glob("*") if (tmp_path / "reports").exists() else [])


def test_output_is_deterministic_across_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(), _evidence(True))
    one = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/a")
    two = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/b")
    assert one.report["authorization_sha256"] == two.report["authorization_sha256"]
    for name in one.export_paths:
        assert Path(one.export_paths[name]).name == Path(two.export_paths[name]).name
        assert Path(one.export_paths[name]).read_bytes() == Path(two.export_paths[name]).read_bytes()


def test_provenance_artifact_contains_validator_derived_negative_classification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(), _evidence(True))
    result = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/provenance")
    with Path(result.export_paths["provenance"]).open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["fixture_only"] == "true"
    assert row["market_evidence"] == "false"
    assert row["public_only"] == "false"
    assert row["source_provider"] == ""
    assert row["source_mode"] == ""


def test_forbidden_manual_provenance_is_not_an_input(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, config = _repo(tmp_path)
    evidence = _evidence(True)
    evidence["public_only"] = True
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(), evidence)
    result = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/manual")
    assert result.report["status"] == module.BLOCKED_EVIDENCE
    assert result.report["validated_public_only_source"] is False


def test_validator_rejects_tampered_authorization_and_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(), _evidence(True))
    result = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/tamper")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["segment_appended"] = True
    report_path.write_bytes(module._pretty(report))
    with pytest.raises(MarketDataError):
        module.validate_authorization(report_path)


def test_validator_rejects_policy_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(), _evidence(True))
    result = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/policy")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["identity"]["policy"]["fixture_evidence_prohibited"] = False
    report["authorization_sha256"] = module._digest(module._canon(report["identity"]))
    report_path.write_bytes(module._pretty(report))
    with pytest.raises(MarketDataError):
        module.validate_authorization(report_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract_status", "bad"),
        ("authorization_sha256", "b" * 64),
        ("append_performed", True),
        ("current_samples", 1),
        ("authoritative_write_authorized", True),
        ("status", module.READY),
    ],
)
def test_validator_rejects_top_level_tamper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object) -> None:
    root, config = _repo(tmp_path)
    preflight_path, evidence_path = _patch_inputs(monkeypatch, root, _preflight(), _evidence(True))
    result = module.authorize_prospective_direct_1h_segment_append(preflight_path, evidence_path, config, "reports/top-tamper")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report[field] = value
    report_path.write_bytes(module._pretty(report))
    with pytest.raises(MarketDataError):
        module.validate_authorization(report_path)


def test_artifact_and_io_guards(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report_path.write_text("{}", encoding="utf-8")
    digest = module._digest(b"bad")
    identity = {"authorization_sha256": digest}
    with pytest.raises(MarketDataError):
        module._artifact(report_path, {}, identity, "authorization", module.AUTH_FIELDS, 1)
    with pytest.raises(MarketDataError):
        module._artifact(report_path, {"authorization": {"sha256": digest, "filename": "../x"}}, identity, "authorization", module.AUTH_FIELDS, 1)
    artifact = tmp_path / "artifact.csv"
    artifact.write_text("wrong\n", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module._artifact(report_path, {"authorization": {"sha256": digest, "filename": artifact.name}}, identity, "authorization", module.AUTH_FIELDS, 1)
    with pytest.raises(MarketDataError):
        module._read_table(tmp_path / "missing.csv")
    collision = tmp_path / "collision"
    collision.write_bytes(b"old")
    with pytest.raises(MarketDataError):
        module._commit(collision, b"new")
    module._commit(collision, b"old")


def test_config_output_and_repo_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    with pytest.raises(ValueError):
        module.load_authorization_config(root / "bad.yaml", root)
    with pytest.raises(ValueError):
        module._output(root, tmp_path / "outside")
    bad = root / module.DEFAULT_CONFIG_FILENAME
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.load_authorization_config(bad, root)
    bad.write_text("[", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.load_authorization_config(bad, root)
    with pytest.raises(MarketDataError):
        module._repo_root(tmp_path / "missing")


def test_config_and_low_level_guards(tmp_path: Path) -> None:
    root, _config = _repo(tmp_path)
    with pytest.raises(ValueError):
        module.load_authorization_config(root / "wrong.yaml", root)
    with pytest.raises(MarketDataError):
        module._repo_root(tmp_path / "missing")
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module._load_json(bad)
    assert module._is_sha("a" * 64)
    assert not module._is_sha("z" * 64)
    assert module._value(True) == "true"


def test_cli_dispatch(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    called: dict[str, object] = {}
    fake = SimpleNamespace(report={"contract_status": "ok"}, export_paths={})

    def authorize(*args: object) -> SimpleNamespace:
        called["args"] = args
        return fake

    monkeypatch.setattr(cli_module, "authorize_prospective_direct_1h_segment_append", authorize)
    monkeypatch.setattr(cli_module, "format_authorization", lambda _result: "ok")
    monkeypatch.setattr(sys, "argv", ["crypto-bot", "freeze-prospective-direct-1h-segment-append-authorization", "--append-preflight", "p.json", "--segment-evidence", "e.json"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert called["args"] == ("p.json", "e.json", module.DEFAULT_CONFIG_FILENAME, "reports/prospective-direct-1h-segment-append-authorization")
    assert capsys.readouterr().out.strip() == "ok"
