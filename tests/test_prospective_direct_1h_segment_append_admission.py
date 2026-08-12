from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import crypto_bot.market.prospective_direct_1h_segment_append_admission as module
import crypto_bot.cli as cli_module
from crypto_bot.errors import MarketDataError

ASSETS = list(module.INST_IDS)
START = datetime(2026, 8, 9, 11, tzinfo=timezone.utc)
END = START + timedelta(hours=335)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def csv_bytes(rows: list[dict[str, object]], fields: tuple[str, ...]) -> bytes:
    return module._csv_bytes(rows, fields)


def repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "src/crypto_bot").mkdir(parents=True)
    (root / "reports").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    cfg = root / module.DEFAULT_CONFIG_FILENAME
    cfg.write_text((Path(__file__).parents[1] / module.DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"), encoding="utf-8")
    return root, cfg


def evidence(root: Path, eligible: bool = True, candidate_offset: int = 100) -> Path:
    directory = root / "reports/prospective-direct-1h-segment-evidence"
    directory.mkdir(parents=True)
    rows = [
        {"ordinal": i, "inst_id": asset, "timeframe": "1H", "canonical_start": "2026-08-09T11:00:00Z", "canonical_end": "2026-08-23T10:00:00Z", "bar_count": 336, "raw_sha256": f"{i:064x}", "canonical_sha256": f"{(i + candidate_offset):064x}", "quality_valid": True, "request_match": True}
        for i, asset in enumerate(ASSETS, 1)
    ] if eligible else []
    asset_content = csv_bytes(rows, ("ordinal", "inst_id", "timeframe", "canonical_start", "canonical_end", "bar_count", "raw_sha256", "canonical_sha256", "quality_valid", "request_match"))
    asset_file = directory / "candidate.assets.csv"
    asset_file.write_bytes(asset_content)
    identity = {"schema_version": 1, "policy_id": "prospective_direct_1h_segment_evidence_v1", "artifacts": {"assets_sha256": digest(asset_content)}}
    sha = digest(canonical(identity))
    report = {"candidate_sha256": sha, "segment_candidate_materialized": eligible, "validated_segment_candidate": eligible, "segment_start": "2026-08-09T11:00:00Z" if eligible else None, "segment_end": "2026-08-23T10:00:00Z" if eligible else None, "segment_appended": False, "new_samples_counted": 0, "network_activity_performed": False, "readiness_changed": False, "capture_sha256": "c" * 64 if eligible else None, "identity": identity, "artifacts": {"assets": {"filename": asset_file.name, "sha256": digest(asset_content), "row_count": len(rows)}}}
    path = directory / f"candidate.{sha}.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def chain(root: Path, next_start: str = "2026-08-09T11:00:00Z") -> Path:
    directory = root / "reports/prospective-direct-1h-segment-chain"
    directory.mkdir(parents=True)
    segments = [{"segment_ordinal": 1, "extension_sha256": "a" * 64, "capture_sha256": "b" * 64, "membership_gate_sha256": "c" * 64, "canonical_start": "2026-08-02T15:00:00Z", "canonical_end": "2026-08-09T10:00:00Z", "rows_per_asset": 164, "previous_tail": "2026-08-02T14:00:00Z", "continuity_status": "exact_plus_one_hour", "immutable": True}]
    assets = [{"segment_ordinal": 1, "inst_id": asset, "baseline_canonical_sha256": f"{i:064x}", "segment_canonical_sha256": f"{(i + 20):064x}", "raw_bundle_sha256": f"{(i + 40):064x}", "canonical_start": "2026-08-02T15:00:00Z", "canonical_end": "2026-08-09T10:00:00Z", "row_count": 164, "gap_count": 0, "overlap_count": 0} for i, asset in enumerate(ASSETS, 1)]
    constraints = module._rows({"segment_count": 1, "current_chain_tail": "2026-08-09T10:00:00Z", "next_canonical_segment_start": next_start})
    identity = {"schema_version": 1, "policy_id": "prospective_direct_1h_append_only_segment_chain_v1", "segments": segments, "assets": assets, "current_chain_tail": "2026-08-09T10:00:00Z", "next_canonical_segment_start": next_start}
    sha = digest(canonical(identity))
    seg_b = csv_bytes(segments, ("segment_ordinal", "extension_sha256", "capture_sha256", "membership_gate_sha256", "canonical_start", "canonical_end", "rows_per_asset", "previous_tail", "continuity_status", "immutable"))
    asset_b = csv_bytes(assets, ("segment_ordinal", "inst_id", "baseline_canonical_sha256", "segment_canonical_sha256", "raw_bundle_sha256", "canonical_start", "canonical_end", "row_count", "gap_count", "overlap_count"))
    con_b = csv_bytes(constraints, module.KEY_VALUE_FIELDS)
    files = {"segments": (seg_b, len(segments)), "assets": (asset_b, len(assets)), "constraints": (con_b, len(constraints))}
    artifacts: dict[str, dict[str, object]] = {}
    for name, (content, count) in files.items():
        file = directory / f"chain.{sha}.{name}.csv"
        file.write_bytes(content)
        artifacts[name] = {"filename": file.name, "sha256": digest(content), "row_count": count}
    report = {"chain_sha256": sha, "audit_status": "verified_prospective_direct_1h_append_only_segment_chain", "segment_count": 1, "asset_segment_rows": 6, "current_chain_tail": "2026-08-09T10:00:00Z", "next_canonical_segment_start": next_start, "identity": identity, "artifacts": {**artifacts, "report": {"filename": f"chain.{sha}.json"}}, "economic_computation_authorized": False, "pnl_computation_authorized": False, "profitability_evidence": False, "readiness_changed": False}
    path = directory / f"prospective-direct-1h-segment-chain.{sha}.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def setup(monkeypatch: pytest.MonkeyPatch, root: Path, candidate: Path, chain_path: Path) -> None:
    monkeypatch.setattr(module, "_repo_root", lambda _path: root)
    monkeypatch.setattr(module, "validate_prospective_direct_1h_segment_evidence", lambda path: module._load_json(Path(path)))


def test_admitted_candidate_is_deterministic_and_non_mutating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root), chain(root)
    setup(monkeypatch, root, candidate, chain_path)
    one = module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/admission-one")
    two = module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/admission-two")
    assert one.report["append_admission_status"] == "admitted"
    assert one.report["expected_segment_ordinal"] == 2
    assert one.report["asset_rows"] == 6
    assert one.report["segment_appended"] is False
    assert one.report["chain_mutation_performed"] is False
    for key in one.export_paths:
        assert Path(one.export_paths[key]).read_bytes() == Path(two.export_paths[key]).read_bytes()
    checked = module.validate_prospective_direct_1h_segment_append_admission(one.export_paths["report"])
    assert checked["append_admission_status"] == "admitted"


def test_blocked_candidate_never_becomes_admitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root, eligible=False), chain(root)
    setup(monkeypatch, root, candidate, chain_path)
    result = module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/admission-blocked")
    assert result.report["append_admission_status"] == module.BLOCKED_STATUS
    assert result.report["append_admission_eligible"] is False
    assert result.report["asset_rows"] == 0
    assert result.report["expected_segment_ordinal"] == 2


def test_stale_candidate_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root), chain(root, "2026-08-23T11:00:00Z")
    setup(monkeypatch, root, candidate, chain_path)
    result = module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/admission-stale")
    assert result.report["append_admission_status"] == module.REJECTED_STATUS
    assert result.report["stale_candidate"] is True
    assert result.report["append_admission_eligible"] is False


def test_candidate_start_is_derived_not_overrideable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root), chain(root)
    setup(monkeypatch, root, candidate, chain_path)
    with pytest.raises(TypeError):
        module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/x", "2026-01-01")  # type: ignore[call-arg]


def test_chain_tamper_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root), chain(root)
    setup(monkeypatch, root, candidate, chain_path)
    report = json.loads(chain_path.read_text(encoding="utf-8"))
    report["current_chain_tail"] = "2026-08-09T09:00:00Z"
    chain_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/admission-tamper")


def test_duplicate_candidate_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root, candidate_offset=20), chain(root)
    setup(monkeypatch, root, candidate, chain_path)
    result = module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/admission-duplicate")
    assert result.report["duplicate_candidate"] is True
    assert result.report["append_admission_status"] == module.REJECTED_STATUS


def test_blocked_output_replays_through_validator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root, eligible=False), chain(root)
    setup(monkeypatch, root, candidate, chain_path)
    result = module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/a7")
    checked = module.validate_prospective_direct_1h_segment_append_admission(result.export_paths["report"])
    assert checked["append_admission_eligible"] is False


def test_report_claim_tamper_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root), chain(root)
    setup(monkeypatch, root, candidate, chain_path)
    result = module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/b7")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["segment_appended"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.validate_prospective_direct_1h_segment_append_admission(report_path)


def test_chain_artifact_tamper_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root), chain(root)
    setup(monkeypatch, root, candidate, chain_path)
    chain_report = json.loads(chain_path.read_text(encoding="utf-8"))
    asset_file = chain_path.parent / chain_report["artifacts"]["assets"]["filename"]
    asset_file.write_bytes(asset_file.read_bytes() + b"tamper")
    with pytest.raises(MarketDataError):
        module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, "reports/admission-chain-artifact-tamper")


def test_output_must_stay_in_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    candidate, chain_path = evidence(root, eligible=False), chain(root)
    setup(monkeypatch, root, candidate, chain_path)
    with pytest.raises(ValueError):
        module.freeze_prospective_direct_1h_segment_append_admission(candidate, chain_path, config, tmp_path / "outside")


def test_config_filename_is_frozen(tmp_path: Path) -> None:
    root, _config = repo(tmp_path)
    bad = root / "bad.yaml"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_segment_append_admission_config(bad, root)


def test_cli_dispatches_append_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_freeze(*args: object) -> SimpleNamespace:
        called["args"] = args
        return SimpleNamespace(report={"contract_status": "ok"}, export_paths={})

    monkeypatch.setattr(cli_module, "freeze_prospective_direct_1h_segment_append_admission", fake_freeze)
    monkeypatch.setattr(cli_module, "format_segment_append_admission_result", lambda result: "ok")
    monkeypatch.setattr(sys, "argv", ["crypto-bot", "freeze-prospective-direct-1h-segment-append-admission", "--segment-evidence", "e.json", "--segment-chain", "c.json"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert called["args"] == ("e.json", "c.json", "config.prospective-direct-1h-segment-append-admission.example.yaml", "reports/prospective-direct-1h-segment-append-admission")
