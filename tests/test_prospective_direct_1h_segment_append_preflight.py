from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.cli as cli_module
import crypto_bot.market.prospective_direct_1h_segment_append_preflight as module
from crypto_bot.errors import MarketDataError


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "src/crypto_bot").mkdir(parents=True)
    (root / "reports").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    config = root / module.DEFAULT_CONFIG_FILENAME
    config.write_text((Path(__file__).parents[1] / module.DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"), encoding="utf-8")
    return root, config


def _chain(identity: str = "c" * 64) -> dict[str, object]:
    return {
        "chain_sha256": identity,
        "segment_count": 1,
        "current_chain_tail": "2026-08-09T10:00:00Z",
        "next_canonical_segment_start": "2026-08-09T11:00:00Z",
        "identity": {"segments": [{"segment_ordinal": 1, "extension_sha256": "x" * 64, "capture_sha256": "y" * 64, "canonical_start": "2026-08-02T15:00:00Z", "canonical_end": "2026-08-09T10:00:00Z"}]},
    }


def _prepared(ready: bool = True, parent: str = "c" * 64) -> dict[str, object]:
    segments: list[dict[str, object]] = [
        {"ordinal": 1, "source_type": "existing_immutable", "source_identity": "s" * 64, "start": "2026-08-02T15:00:00Z", "end": "2026-08-09T10:00:00Z", "asset_count": 6, "segment_sha256_reference": "s" * 64}
    ]
    candidate = "e" * 64
    post_count: int | None = 2 if ready else None
    post_tail: str | None = "2026-08-23T10:00:00Z" if ready else None
    post_next: str | None = "2026-08-23T11:00:00Z" if ready else None
    if ready:
        segments.append({"ordinal": 2, "source_type": "validated_candidate", "source_identity": candidate, "start": "2026-08-09T11:00:00Z", "end": post_tail, "asset_count": 6, "segment_sha256_reference": candidate})
    expected = module.recompute_proposed_post_identity(parent, candidate, segments, post_count or 0, post_tail or "", post_next or "") if ready else None
    return {
        "prepared_sha256": "a" * 64,
        "status": "prepared_non_authoritative" if ready else "blocked_append_plan_not_ready",
        "prepared_chain_materialized": ready,
        "authoritative_pre_chain_identity": parent,
        "current_chain_identity": parent,
        "candidate_identity": candidate if ready else None,
        "proposed_segment_count": post_count,
        "proposed_chain_tail": post_tail,
        "proposed_next_start": post_next,
        "expected_post_append_chain_identity": expected,
        "segments": segments if ready else [],
    }


def _patch_inputs(monkeypatch: pytest.MonkeyPatch, root: Path, prepared: dict[str, object], chain: dict[str, object]) -> tuple[Path, Path]:
    monkeypatch.setattr(module, "_repo_root", lambda _path: root)
    monkeypatch.setattr(module, "validate_prepared_segment", lambda _path: prepared)
    monkeypatch.setattr(module, "_validate_current_chain", lambda _path: chain)
    prepared_path = root / "reports/prepared.json"
    chain_path = root / "reports/chain.json"
    prepared_path.write_text("{}", encoding="utf-8")
    chain_path.write_text("{}", encoding="utf-8")
    return prepared_path, chain_path


def test_ready_preflight_is_deterministic_and_replayable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(), _chain())
    first = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/one")
    second = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/two")
    assert first.report["status"] == module.READY
    assert first.report["commit_preflight_ready"] is True
    assert first.report["commit_authorized"] is False
    assert first.report["expected_post_segment_count"] == 2
    assert first.report["expected_segment_ordinal"] == 2
    assert module.validate_preflight(first.export_paths["report"])["status"] == module.READY
    for name in first.export_paths:
        assert Path(first.export_paths[name]).name == Path(second.export_paths[name]).name
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()


def test_ready_preflight_write_set_has_only_logical_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(), _chain())
    result = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/ready")
    with Path(result.export_paths["write_set"]).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["operation"] for row in rows] == ["preserve", "append_exactly_once", "materialize_post_chain_marker_last"]
    assert all("path" not in value.lower() for row in rows for value in row.values())


def test_blocked_prepared_has_no_post_state_or_write_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(False), _chain())
    result = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/blocked")
    assert result.report["status"] == module.BLOCKED_PREPARED
    assert result.report["preflight_materialized"] is False
    for key in ("candidate_identity", "expected_segment_ordinal", "expected_parent_segment_count", "observed_current_segment_count", "expected_parent_tail", "observed_current_tail", "expected_post_chain_identity", "expected_post_segment_count", "expected_post_tail", "expected_post_next_start"):
        assert result.report[key] is None
    assert module.validate_preflight(result.export_paths["report"])["status"] == module.BLOCKED_PREPARED
    with Path(result.export_paths["write_set"]).open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == []


def test_parent_drift_is_fail_closed_without_post_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(), _chain("d" * 64))
    result = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/drift")
    assert result.report["status"] == module.DRIFT
    assert result.report["parent_identity_match"] is False
    assert result.report["expected_post_chain_identity"] is None
    assert module.validate_preflight(result.export_paths["report"])["status"] == module.DRIFT


def test_post_identity_tamper_fails_before_materialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared = _prepared()
    prepared["expected_post_append_chain_identity"] = "f" * 64
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, prepared, _chain())
    with pytest.raises(MarketDataError, match="post identity"):
        module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/tampered")


def test_identity_excludes_paths_and_output_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(), _chain())
    one = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/a")
    two = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/b")
    assert one.report["identity"] == two.report["identity"]
    assert "prepared_path" not in json.dumps(one.report["identity"])
    assert "chain_path" not in json.dumps(one.report["identity"])
    assert "output_dir" not in json.dumps(one.report["identity"])


def test_preflight_validator_rejects_cas_and_write_tamper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(), _chain())
    result = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/tamper")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cas_path = report_path.parent / report["artifacts"]["cas"]["filename"]
    cas_path.write_text(cas_path.read_text(encoding="utf-8").replace("true", "false", 1), encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.validate_preflight(report_path)


def test_preflight_validator_rejects_policy_and_forbidden_authorization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(), _chain())
    result = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/policy")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["identity"]["policy"]["commit_authorized"] = True
    report["preflight_sha256"] = module._digest(module._canon(report["identity"]))
    report_path.write_bytes(module._pretty(report))
    with pytest.raises(MarketDataError):
        module.validate_preflight(report_path)


def test_same_name_different_bytes_collision_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(), _chain())
    result = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/collision")
    cas_path = Path(result.export_paths["cas"])
    cas_path.write_bytes(cas_path.read_bytes() + b"tamper")
    with pytest.raises(MarketDataError):
        module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/collision")


def test_config_and_output_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    with pytest.raises(ValueError):
        module.load_preflight_config(root / "wrong.yaml", root)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(False), _chain())
    with pytest.raises(ValueError):
        module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, tmp_path / "outside")
    bad = root / module.DEFAULT_CONFIG_FILENAME
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module.load_preflight_config(bad, root)


def test_artifact_and_json_guards(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError):
        module._load_json(bad)
    with pytest.raises(MarketDataError):
        module._read(tmp_path / "missing.csv")
    assert module._is_sha("a" * 64)
    assert not module._is_sha("z" * 64)
    assert not module._is_sha(1)
    assert module._value(True) == "true"
    assert module._value(None) == ""


def test_cli_dispatch(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    called: dict[str, object] = {}
    fake_report = {"contract_status": "ok", "preflight_sha256": "a", "status": module.BLOCKED_PREPARED, "preflight_materialized": False, "commit_preflight_ready": False, "commit_authorized": False, "authoritative_write_authorized": False, "chain_mutation_performed": False, "segment_appended": False}

    def fake_preflight(*args: object) -> SimpleNamespace:
        called["args"] = args
        return SimpleNamespace(report=fake_report, export_paths={})

    monkeypatch.setattr(cli_module, "preflight_prospective_direct_1h_segment_append", fake_preflight)
    monkeypatch.setattr(cli_module, "format_preflight", lambda _result: "ok")
    monkeypatch.setattr(sys, "argv", ["crypto-bot", "preflight-prospective-direct-1h-segment-append", "--prepared", "p.json", "--segment-chain", "c.json"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert called["args"] == ("p.json", "c.json", module.DEFAULT_CONFIG_FILENAME, "reports/prospective-direct-1h-segment-append-preflight")
    assert capsys.readouterr().out.strip() == "ok"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda report: report.update({"contract_status": "bad"}), "contract"),
        (lambda report: report.update({"preflight_sha256": "b" * 64}), "identity"),
        (lambda report: report.update({"status": "unknown"}), "status"),
        (lambda report: report.update({"preflight_materialized": False}), "materialization"),
        (lambda report: report.update({"segment_appended": True}), "forbidden"),
        (lambda report: report.update({"current_samples": 1}), "sample"),
        (lambda report: report.update({"parent_identity_match": False}), "parent/post"),
    ],
)
def test_validator_rejects_top_level_tamper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: object, message: str) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(), _chain())
    result = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/validation")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if message == "identity":
        report["identity"]["status"] = "tampered"
        report["preflight_sha256"] = module._digest(module._canon(report["identity"]))
    mutate(report)  # type: ignore[operator]
    report_path.write_bytes(module._pretty(report))
    with pytest.raises(MarketDataError):
        module.validate_preflight(report_path)


def test_validator_rejects_forbidden_path_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    prepared_path, chain_path = _patch_inputs(monkeypatch, root, _prepared(), _chain())
    result = module.preflight_prospective_direct_1h_segment_append(prepared_path, chain_path, config, "reports/path")
    report_path = Path(result.export_paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["identity"]["temporary_path"] = "x"
    report["preflight_sha256"] = module._digest(module._canon(report["identity"]))
    report_path.write_bytes(module._pretty(report))
    with pytest.raises(MarketDataError):
        module.validate_preflight(report_path)


def test_low_level_post_identity_and_ordinal_guards() -> None:
    with pytest.raises(MarketDataError):
        module._verify_post_identity({"segments": []})
    with pytest.raises(MarketDataError):
        module._verify_post_identity({"segments": [{}]})
    with pytest.raises(MarketDataError):
        module._candidate_ordinal({"segments": []})
    with pytest.raises(MarketDataError):
        module._candidate_ordinal({"segments": [{"ordinal": "x"}], "proposed_segment_count": 1})
    with pytest.raises(MarketDataError):
        module._candidate_ordinal({"segments": [{"ordinal": 1}], "proposed_segment_count": 2})


def test_low_level_write_set_artifact_and_collision_guards(tmp_path: Path) -> None:
    report = _prepared()
    report.update(module._state(_prepared(), _chain(), module.READY))
    with pytest.raises(MarketDataError):
        module._validate_write_set([{"bad": "row"}], False, report)
    with pytest.raises(MarketDataError):
        module._validate_write_set([], True, report)
    normalized = [{key: module._value(row.get(key)) for key in module.WRITE_FIELDS} for row in module._write_set(report)]
    assert module._validate_write_set(normalized, True, report) is None
    report_path = tmp_path / "report.json"
    report_path.write_text("{}", encoding="utf-8")
    artifact = tmp_path / "artifact.csv"
    artifact.write_text("wrong\n", encoding="utf-8")
    digest = module._digest(artifact.read_bytes())
    identity = {"artifacts": {"cas_sha256": digest}}
    with pytest.raises(MarketDataError):
        module._artifact(report_path, {}, identity, "cas", module.CAS_FIELDS, 1)
    with pytest.raises(MarketDataError):
        module._artifact(report_path, {"cas": {"sha256": digest, "filename": "../artifact.csv"}}, identity, "cas", module.CAS_FIELDS, 1)
    with pytest.raises(MarketDataError):
        module._artifact(report_path, {"cas": {"sha256": digest, "filename": "artifact.csv"}}, identity, "cas", module.CAS_FIELDS, 1)
    collision = tmp_path / "collision"
    collision.write_bytes(b"old")
    with pytest.raises(MarketDataError):
        module._commit(collision, b"new")
    module._commit(collision, b"old")


def test_config_read_and_repo_error_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, config = _repo(tmp_path)
    monkeypatch.setattr(module.Path, "read_text", lambda *_args, **_kwargs: "[")
    with pytest.raises(MarketDataError):
        module.load_preflight_config(config, root)
    monkeypatch.undo()
    with pytest.raises(MarketDataError):
        module._repo_root(tmp_path / "missing")
    with pytest.raises(MarketDataError):
        module._load_json(tmp_path / "missing.json")
    assert module._contains_forbidden_path_key({"x": [{"path": "x"}]}) is True
    assert module._contains_forbidden_path_key({"x": [1, 2]}) is False
