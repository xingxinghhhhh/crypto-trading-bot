from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.prospective_direct_1h_segment_append_prepared as module
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
    assets = [{"segment_ordinal": 2, "inst_id": asset, "candidate_canonical_sha256": f"{i:064x}", "candidate_bar_count": "336"} for i, asset in enumerate(ASSETS, 1)]
    plan = {"plan_sha256": "a" * 64, "current_chain_identity": "c" * 64, "candidate_identity": "e" * 64, "candidate_start": "2026-08-09T11:00:00Z" if ready else None, "candidate_end": "2026-08-23T10:00:00Z" if ready else None, "current_segment_count": 1, "expected_segment_ordinal": 2, "status": "append_plan_ready" if ready else "blocked_append_admission_not_eligible", "append_plan_materialized": ready, "_asset_rows": assets if ready else []}
    chain = {"chain_sha256": "c" * 64, "segment_count": 1, "current_chain_tail": "2026-08-09T10:00:00Z", "next_canonical_segment_start": "2026-08-09T11:00:00Z", "identity": {"segments": [{"segment_ordinal": 1, "extension_sha256": "x" * 64, "capture_sha256": "y" * 64, "canonical_start": "2026-08-02T15:00:00Z", "canonical_end": "2026-08-09T10:00:00Z"}]}}
    return plan, chain


def setup(monkeypatch: pytest.MonkeyPatch, root: Path, plan: dict[str, object], chain: dict[str, object]) -> None:
    monkeypatch.setattr(module, "_repo_root", lambda _path: root)
    monkeypatch.setattr(module, "validate_prospective_direct_1h_segment_append_plan", lambda _path: plan.copy())
    monkeypatch.setattr(module, "_validate_current_chain", lambda _path: chain.copy())


def plan_file(root: Path, plan: dict[str, object]) -> Path:
    directory = root / "reports/plan"
    directory.mkdir(parents=True)
    rows = plan.get("_asset_rows", [])
    assets_file = directory / "assets.csv"
    assets_file.write_text("ordinal,inst_id,candidate_canonical_sha256,candidate_bar_count,candidate_start,candidate_end,predecessor_chain_identity,request_match,quality_valid\n" + "\n".join(f"2,{r['inst_id']},{r['candidate_canonical_sha256']},{r['candidate_bar_count']},2026-08-09T11:00:00Z,2026-08-23T10:00:00Z,c,true,true" for r in rows) + "\n", encoding="utf-8")
    # The builder reads the artifact, while the mocked plan validator supplies parent semantics.
    plan["artifacts"] = {"assets": {"filename": assets_file.name, "row_count": len(rows)}}
    path = directory / "plan.json"
    path.write_text("{}", encoding="utf-8")
    return path


def test_ready_shadow_is_deterministic_and_validates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    plan, chain = parents()
    path = plan_file(root, plan)
    chain_path = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    chain_path.parent.mkdir()
    chain_path.write_text("{}", encoding="utf-8")
    setup(monkeypatch, root, plan, chain)
    first = module.prepare_prospective_direct_1h_segment_append(path, chain_path, config, "reports/one")
    second = module.prepare_prospective_direct_1h_segment_append(path, chain_path, config, "reports/two")
    assert first.report["status"] == module.READY_RESULT
    assert first.report["prepared_chain_materialized"] is True
    assert first.report["proposed_segment_count"] == 2
    assert first.report["proposed_chain_tail"] == "2026-08-23T10:00:00Z"
    assert first.report["authoritative"] is False
    assert first.report["promotion_authorized"] is False
    for key in first.export_paths:
        assert Path(first.export_paths[key]).read_bytes() == Path(second.export_paths[key]).read_bytes()
    assert module.validate_prepared_segment(first.export_paths["report"])["status"] == module.READY_RESULT


def test_blocked_plan_has_no_shadow_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    plan, chain = parents(False)
    path = plan_file(root, plan)
    chain_path = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    chain_path.parent.mkdir()
    chain_path.write_text("{}", encoding="utf-8")
    setup(monkeypatch, root, plan, chain)
    result = module.prepare_prospective_direct_1h_segment_append(path, chain_path, config, "reports/blocked")
    assert result.report["status"] == module.BLOCKED_PLAN_RESULT
    assert result.report["prepared_chain_materialized"] is False
    assert result.report["proposed_segment_count"] is None
    assert module.validate_prepared_segment(result.export_paths["report"])["prepared_chain_materialized"] is False


def test_chain_drift_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    plan, chain = parents()
    chain["chain_sha256"] = "d" * 64
    path = plan_file(root, plan)
    chain_path = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    chain_path.parent.mkdir()
    chain_path.write_text("{}", encoding="utf-8")
    setup(monkeypatch, root, plan, chain)
    result = module.prepare_prospective_direct_1h_segment_append(path, chain_path, config, "reports/drift")
    assert result.report["status"] == module.CHAIN_DRIFT_RESULT
    assert result.report["prepared_chain_materialized"] is False


def test_forbidden_marker_claim_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    plan, chain = parents(False)
    path = plan_file(root, plan)
    chain_path = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    chain_path.parent.mkdir()
    chain_path.write_text("{}", encoding="utf-8")
    setup(monkeypatch, root, plan, chain)
    result = module.prepare_prospective_direct_1h_segment_append(path, chain_path, config, "reports/forbidden")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["segment_appended"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.validate_prepared_segment(report_path)


def test_config_and_output_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = repo(tmp_path)
    bad = root / "bad.yaml"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_prepared_config(bad, root)
    plan, chain = parents(False)
    path = plan_file(root, plan)
    chain_path = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    chain_path.parent.mkdir()
    chain_path.write_text("{}", encoding="utf-8")
    setup(monkeypatch, root, plan, chain)
    with pytest.raises(ValueError):
        module.prepare_prospective_direct_1h_segment_append(path, chain_path, config, tmp_path / "outside")


def test_config_policy_and_shape_guards(tmp_path: Path) -> None:
    root, _ = repo(tmp_path)
    config = root / module.DEFAULT_CONFIG_FILENAME
    config.write_text("{}", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.load_prepared_config(config, root)
    config.write_text("[", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.load_prepared_config(config, root)
    config.write_text("{}", encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module.yaml, "safe_load", lambda _text: [])
    with pytest.raises(MarketDataError):
        module.load_prepared_config(config, root)
    monkeypatch.undo()
    config.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.load_prepared_config(config, root)


def test_prepared_validator_identity_and_artifact_guards(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"contract_status": module.CONTRACT_STATUS, "schema_version": 1, "prepared_sha256": "a" * 64, "identity": {}}), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.validate_prepared_segment(path)
    path.write_text(json.dumps({"contract_status": "wrong", "schema_version": 1}), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.validate_prepared_segment(path)
    path.write_text(json.dumps({"contract_status": module.CONTRACT_STATUS, "schema_version": 1, "prepared_sha256": module._digest(module._canonical({"x": 1})), "identity": {"x": 1}}), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.validate_prepared_segment(path)


def test_internal_parent_and_timestamp_guards(tmp_path: Path) -> None:
    with pytest.raises(MarketDataError):
        module._resolve_parent(123)
    with pytest.raises(MarketDataError):
        module._resolve_parent(str(tmp_path / "missing"))
    with pytest.raises(MarketDataError):
        module._parse_iso("not-a-time")
    with pytest.raises(MarketDataError):
        module._parse_iso("2026-01-01")
    assert module._is_sha256("a" * 64)
    assert not module._is_sha256("g" * 64)
    assert not module._is_sha256(123)


def test_load_json_and_repo_guards(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[1]", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module._load_json(bad)
    with pytest.raises(MarketDataError):
        module._repo_root(tmp_path)


def test_candidate_asset_and_existing_segment_guards(tmp_path: Path) -> None:
    root, config = repo(tmp_path)
    plan, chain = parents()
    path = plan_file(root, plan)
    chain_path = root / "reports/prospective-direct-1h-segment-chain/chain.json"
    chain_path.parent.mkdir()
    chain_path.write_text("{}", encoding="utf-8")
    setup(monkeypatch := pytest.MonkeyPatch(), root, plan, chain)
    try:
        broken_chain = {**chain, "segment_count": 2}
        with pytest.raises(MarketDataError):
            module._ready_state(plan, broken_chain, path)
        broken_plan = {**plan, "expected_segment_ordinal": 3}
        with pytest.raises(MarketDataError):
            module._ready_state(broken_plan, chain, path)
    finally:
        monkeypatch.undo()


def test_ready_state_asset_count_guard(tmp_path: Path) -> None:
    root, _ = repo(tmp_path)
    plan, chain = parents()
    path = plan_file(root, plan)
    assets_path = path.parent / "assets.csv"
    lines = assets_path.read_text(encoding="utf-8").splitlines()
    assets_path.write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")
    plan["artifacts"]["assets"]["row_count"] = 4  # type: ignore[index]
    with pytest.raises(MarketDataError):
        module._ready_state(plan, chain, path)


def test_prepared_validator_claim_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report = {"contract_status": module.CONTRACT_STATUS, "schema_version": 1, "prepared_sha256": "a" * 64, "identity": {}}
    monkeypatch.setattr(module, "_load_json", lambda _path: report)
    monkeypatch.setattr(module, "_validate_artifact", lambda *args: Path(tmp_path / "x"))
    monkeypatch.setattr(module, "_digest", lambda _value: "a" * 64)
    monkeypatch.setattr(module, "_canonical", lambda _value: b"x")
    report.update({"artifacts": {"chain_image": {"row_count": 0}, "dependencies": {"row_count": 0}, "constraints": {"row_count": 0}}, "proposed_segment_count": None, "prepared_chain_materialized": False, "authoritative_chain_changed": False, "chain_mutation_performed": False, "segment_appended": False, "promotion_authorized": False, "new_samples_counted": 0, "current_samples": 160, "sample_threshold": 500, "remaining_samples": 340, "network_activity_performed": False, "economic_computation_authorized": False, "pnl_computation_authorized": False, "readiness_changed": False})
    report["network_activity_performed"] = True
    with pytest.raises(MarketDataError):
        module.validate_prepared_segment(tmp_path / "report.json")


def test_low_level_artifact_and_io_guards(tmp_path: Path) -> None:
    report = tmp_path / "report"
    artifact = tmp_path / "artifact.csv"
    artifact.write_text("wrong\n", encoding="utf-8")
    identity = {"artifacts": {"x_sha256": module._digest(artifact.read_bytes())}}
    info = {"x": {"filename": artifact.name, "sha256": identity["artifacts"]["x_sha256"]}}
    with pytest.raises(MarketDataError):
        module._validate_artifact(report, info, identity, "x", ("key",), 1)
    artifact.write_text("key\nvalue\n", encoding="utf-8")
    info["x"]["sha256"] = identity["artifacts"]["x_sha256"]
    with pytest.raises(MarketDataError):
        module._validate_artifact(report, info, identity, "x", ("key",), 1)
    info["x"]["sha256"] = "a" * 64
    with pytest.raises(MarketDataError):
        module._validate_artifact(report, info, identity, "x", ("key",), 1)
    with pytest.raises(MarketDataError):
        module._read_csv(tmp_path / "missing.csv")
    with pytest.raises(MarketDataError):
        module._load_json(tmp_path / "missing.json")
    target = tmp_path / "collision"
    target.write_bytes(b"old")
    with pytest.raises(MarketDataError):
        module._commit(target, b"new")
    fresh = tmp_path / "fresh"
    module._commit(fresh, b"new")
    assert fresh.read_bytes() == b"new"


def test_format_prepared_result_and_repo_root(tmp_path: Path) -> None:
    result = SimpleNamespace(report={"contract_status": "x", "prepared_sha256": "a", "status": "prepared_non_authoritative", "prepared_chain_materialized": True, "proposed_segment_count": 2, "proposed_chain_tail": "end"})
    assert "prepared_non_authoritative" in module.format_prepared_result(result)
    root = tmp_path / "root"
    (root / "src/crypto_bot").mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    assert module._repo_root(root / "src/crypto_bot") == root


def test_plan_asset_reader_guards(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module._read_plan_assets(path, {})
    with pytest.raises(MarketDataError):
        module._read_plan_assets(path, {"artifacts": {"assets": {"filename": "missing.csv", "row_count": 0}}})


def test_cli_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_prepare(*args: object) -> SimpleNamespace:
        called["args"] = args
        return SimpleNamespace(report={"contract_status": "ok"}, export_paths={})

    monkeypatch.setattr(cli_module, "prepare_prospective_direct_1h_segment_append", fake_prepare)
    monkeypatch.setattr(cli_module, "format_prepared_result", lambda _result: "ok")
    monkeypatch.setattr(sys, "argv", ["crypto-bot", "prepare-prospective-direct-1h-segment-append", "--append-plan", "p.json", "--segment-chain", "c.json"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert called["args"] == ("p.json", "c.json", "config.prospective-direct-1h-segment-append-prepared.example.yaml", "reports/prospective-direct-1h-segment-append-prepared")
