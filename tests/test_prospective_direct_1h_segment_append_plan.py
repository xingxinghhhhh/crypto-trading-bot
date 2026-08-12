from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.prospective_direct_1h_segment_append_plan as module
from crypto_bot.errors import MarketDataError

ASSETS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT")


def repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "src/crypto_bot").mkdir(parents=True)
    (root / "reports").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    config = root / module.DEFAULT_CONFIG_FILENAME
    config.write_text((Path(__file__).parents[1] / module.DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"), encoding="utf-8")
    return root, config


def parents(ready: bool = True) -> tuple[dict[str, object], dict[str, object]]:
    asset_rows = [{"inst_id": asset, "candidate_canonical_sha256": f"{i:064x}", "candidate_bar_count": "336"} for i, asset in enumerate(ASSETS, 1)]
    admission = {
        "admission_sha256": "a" * 64,
        "current_segment_chain_identity": "c" * 64,
        "segment_evidence_identity": "e" * 64,
        "candidate_start": "2026-08-09T11:00:00Z" if ready else None,
        "candidate_end": "2026-08-23T10:00:00Z" if ready else None,
        "current_segment_count": 1,
        "expected_segment_ordinal": 2,
        "append_admission_eligible": ready,
        "append_admission_status": "admitted" if ready else "blocked_segment_candidate_not_materialized",
        "_asset_rows": asset_rows if ready else [],
    }
    chain = {"chain_sha256": "c" * 64, "segment_count": 1, "current_chain_tail": "2026-08-09T10:00:00Z", "next_canonical_segment_start": "2026-08-09T11:00:00Z"}
    return admission, chain


def setup(monkeypatch: pytest.MonkeyPatch, root: Path, admission: dict[str, object], chain: dict[str, object]) -> None:
    monkeypatch.setattr(module, "_repo_root", lambda _path: root)
    monkeypatch.setattr(module, "validate_prospective_direct_1h_segment_append_admission", lambda _path: admission.copy())
    monkeypatch.setattr(module, "_validate_current_chain", lambda _path: chain.copy())


def test_ready_plan_derives_next_state_and_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    admission, chain = parents()
    setup(monkeypatch, root, admission, chain)
    append_path = root / "reports/admission.json"
    chain_path = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    append_path.write_text("{}", encoding="utf-8")
    chain_path.parent.mkdir()
    chain_path.write_text("{}", encoding="utf-8")
    first = module.freeze_prospective_direct_1h_segment_append_plan(append_path, chain_path, config, "reports/plan-one")
    second = module.freeze_prospective_direct_1h_segment_append_plan(append_path, chain_path, config, "reports/plan-two")
    assert first.report["status"] == module.READY_STATUS
    assert first.report["append_plan_materialized"] is True
    assert first.report["planned_segment_count"] == 2
    assert first.report["planned_chain_tail"] == "2026-08-23T10:00:00Z"
    assert first.report["planned_next_canonical_segment_start"] == "2026-08-23T11:00:00Z"
    assert first.report["chain_mutation_performed"] is False
    assert first.report["segment_appended"] is False
    for key in first.export_paths:
        assert Path(first.export_paths[key]).read_bytes() == Path(second.export_paths[key]).read_bytes()
    monkeypatch.setattr(module, "validate_prospective_direct_1h_segment_append_admission", lambda _path: admission)
    monkeypatch.setattr(module, "_validate_current_chain", lambda _path: chain)
    checked = module.validate_prospective_direct_1h_segment_append_plan(first.export_paths["report"])
    assert checked["status"] == module.READY_STATUS


def test_blocked_admission_produces_no_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    admission, chain = parents(False)
    setup(monkeypatch, root, admission, chain)
    a = root / "reports/admission.json"
    c = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    a.write_text("{}", encoding="utf-8")
    c.parent.mkdir()
    c.write_text("{}", encoding="utf-8")
    result = module.freeze_prospective_direct_1h_segment_append_plan(a, c, config, "reports/blocked")
    assert result.report["status"] == module.BLOCKED_ADMISSION_STATUS
    assert result.report["append_plan_materialized"] is False
    assert result.report["asset_rows"] == 0
    assert result.report["planned_segment_count"] is None
    monkeypatch.setattr(module, "validate_prospective_direct_1h_segment_append_admission", lambda _path: admission)
    monkeypatch.setattr(module, "_validate_current_chain", lambda _path: chain)
    assert module.validate_prospective_direct_1h_segment_append_plan(result.export_paths["report"])["status"] == module.BLOCKED_ADMISSION_STATUS


def test_current_chain_drift_blocks_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    admission, chain = parents()
    chain["chain_sha256"] = "d" * 64
    setup(monkeypatch, root, admission, chain)
    a = root / "reports/admission.json"
    c = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    a.write_text("{}", encoding="utf-8")
    c.parent.mkdir()
    c.write_text("{}", encoding="utf-8")
    result = module.freeze_prospective_direct_1h_segment_append_plan(a, c, config, "reports/drift")
    assert result.report["status"] == module.CHAIN_DRIFT_STATUS
    assert result.report["append_plan_materialized"] is False


def test_plan_validator_rejects_forbidden_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    admission, chain = parents()
    setup(monkeypatch, root, admission, chain)
    a = root / "reports/admission.json"
    c = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    a.write_text("{}", encoding="utf-8")
    c.parent.mkdir()
    c.write_text("{}", encoding="utf-8")
    result = module.freeze_prospective_direct_1h_segment_append_plan(a, c, config, "reports/forbidden")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["segment_appended"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(module, "validate_prospective_direct_1h_segment_append_admission", lambda _path: admission)
    with pytest.raises(MarketDataError):
        module.validate_prospective_direct_1h_segment_append_plan(report_path)


def test_plan_validator_rejects_admission_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    admission, chain = parents()
    setup(monkeypatch, root, admission, chain)
    a = root / "reports/admission.json"
    c = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    a.write_text("{}", encoding="utf-8")
    c.parent.mkdir()
    c.write_text("{}", encoding="utf-8")
    result = module.freeze_prospective_direct_1h_segment_append_plan(a, c, config, "reports/drift-admission")
    monkeypatch.setattr(module, "validate_prospective_direct_1h_segment_append_admission", lambda _path: {**admission, "admission_sha256": "f" * 64})
    with pytest.raises(MarketDataError):
        module.validate_prospective_direct_1h_segment_append_plan(result.export_paths["report"])


def test_cli_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_freeze(*args: object) -> SimpleNamespace:
        called["args"] = args
        return SimpleNamespace(report={"contract_status": "ok"}, export_paths={})

    monkeypatch.setattr(cli_module, "freeze_prospective_direct_1h_segment_append_plan", fake_freeze)
    monkeypatch.setattr(cli_module, "format_segment_append_plan_result", lambda _result: "ok")
    monkeypatch.setattr(sys, "argv", ["crypto-bot", "freeze-prospective-direct-1h-segment-append-plan", "--append-admission", "a.json", "--segment-chain", "c.json"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert called["args"] == ("a.json", "c.json", "config.prospective-direct-1h-segment-append-plan.example.yaml", "reports/prospective-direct-1h-segment-append-plan")


def test_config_filename_guard(tmp_path: Path) -> None:
    root, _ = repo(tmp_path)
    bad = root / "bad.yaml"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_segment_append_plan_config(bad, root)


def test_output_path_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    admission, chain = parents(False)
    setup(monkeypatch, root, admission, chain)
    a = root / "reports/admission.json"
    c = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    a.write_text("{}", encoding="utf-8")
    c.parent.mkdir()
    c.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        module.freeze_prospective_direct_1h_segment_append_plan(a, c, config, tmp_path / "outside")


def test_timestamp_and_hash_guards() -> None:
    with pytest.raises(MarketDataError):
        module._parse_iso("2026-01-01")
    assert module._is_sha256("a" * 64)
    assert not module._is_sha256("not-a-hash")


def test_chain_validator_rejects_wrong_directory(tmp_path: Path) -> None:
    path = tmp_path / "chain.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module._validate_current_chain(path)


def test_real_chain_parent_replays() -> None:
    paths = list(Path("reports/prospective-direct-1h-segment-chain").glob("prospective-direct-1h-segment-chain.*.json"))
    if not paths:
        pytest.skip("CI report fixture not restored")
    checked = module._validate_current_chain(paths[0])
    assert checked["segment_count"] == 1


def test_chain_forbidden_claim_fails_closed(tmp_path: Path) -> None:
    paths = list(Path("reports/prospective-direct-1h-segment-chain").glob("prospective-direct-1h-segment-chain.*.json"))
    if not paths:
        pytest.skip("CI report fixture not restored")
    path = paths[0]
    report = json.loads(path.read_text(encoding="utf-8"))
    report["economic_computation_authorized"] = True
    broken = tmp_path / "prospective-direct-1h-segment-chain" / path.name
    broken.parent.mkdir()
    broken.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module._validate_current_chain(broken)


def test_plan_validator_rejects_missing_contract(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.validate_prospective_direct_1h_segment_append_plan(path)


def test_json_and_parent_guards(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[1]", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module._load_json(bad)
    with pytest.raises(MarketDataError):
        module._resolve_parent(tmp_path / "report.json", "")
    with pytest.raises(MarketDataError):
        module._resolve_parent(tmp_path / "report.json", str(tmp_path / "missing.json"))


def test_asset_builder_rejects_wrong_count() -> None:
    with pytest.raises(MarketDataError):
        module._asset_rows({"_asset_rows": []}, {"candidate_start": "", "candidate_end": "", "current_chain_identity": ""})


def test_format_ready_and_blocked() -> None:
    ready = SimpleNamespace(report={"contract_status": "x", "plan_sha256": "a", "append_plan_materialized": True, "status": "append_plan_ready", "planned_segment_count": 2, "planned_chain_tail": "end", "planned_next_canonical_segment_start": "next"})
    blocked = SimpleNamespace(report={"contract_status": "x", "plan_sha256": "b", "append_plan_materialized": False, "status": "blocked_append_admission_not_eligible", "planned_segment_count": None, "planned_chain_tail": None, "planned_next_canonical_segment_start": None})
    assert "append_plan_ready" in module.format_segment_append_plan_result(ready)
    assert "blocked_append_admission_not_eligible" in module.format_segment_append_plan_result(blocked)
