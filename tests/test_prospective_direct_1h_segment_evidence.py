from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import crypto_bot.market.prospective_direct_1h_segment_evidence as module
import crypto_bot.cli as cli_module
from crypto_bot.errors import MarketDataError


ASSETS = list(module.INST_IDS)
START = datetime(2026, 8, 9, 11, tzinfo=timezone.utc)
COUNT = 336
END = START + timedelta(hours=COUNT - 1)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "src/crypto_bot").mkdir(parents=True)
    (repo / "reports").mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    config = repo / module.DEFAULT_CONFIG_FILENAME
    config.write_text(
        (Path(__file__).resolve().parents[1] / module.DEFAULT_CONFIG_FILENAME).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return repo, config


def _admission(repo: Path, eligible: bool = True) -> tuple[Path, dict[str, object]]:
    directory = repo / "reports/prospective-direct-1h-segment-admission"
    directory.mkdir(parents=True, exist_ok=True)
    admission_sha = "a" * 64
    rows = [
        {
            "inst_id": inst_id,
            "timeframe": "1H",
            "segment_start": START.isoformat().replace("+00:00", "Z"),
            "segment_end": END.isoformat().replace("+00:00", "Z"),
            "expected_bar_count": COUNT,
            "previous_chain_tail": "2026-08-09T10:00:00Z",
            "continuity_required": True,
        }
        for inst_id in ASSETS
    ]
    asset_bytes = module._csv_bytes(rows, module.ADMISSION_ASSET_FIELDS)
    asset_path = directory / f"admission.{admission_sha}.assets.csv"
    asset_path.write_bytes(asset_bytes)
    report = {
        "schema_version": 1,
        "admission_sha256": admission_sha,
        "contract_status": "verified_prospective_direct_1h_segment_admission",
        "status": "admitted_future_segment_request" if eligible else "blocked_membership_epoch_not_closed",
        "admission_eligible": eligible,
        "closure_sha256": "b" * 64,
        "segment_chain_sha256": "c" * 64,
        "epoch_id": "epoch-0002",
        "segment_start": START.isoformat().replace("+00:00", "Z") if eligible else None,
        "segment_end": END.isoformat().replace("+00:00", "Z") if eligible else None,
        "expected_bars_per_asset": COUNT if eligible else 0,
        "required_asset_count": 6,
        "expected_total_canonical_rows": COUNT * 6 if eligible else 0,
        "request_rows": 1 if eligible else 0,
        "asset_rows": 6 if eligible else 0,
        "capture_performed": False,
        "identity": {
            "schema_version": 1,
            "policy_id": "prospective_direct_1h_segment_admission_v1",
            "policy": {},
            "artifacts": {"assets_sha256": _digest(asset_bytes)},
        },
        "artifacts": {
            "assets": {
                "filename": asset_path.name,
                "sha256": _digest(asset_bytes),
                "row_count": 6,
            }
        },
    }
    path = directory / f"prospective-direct-1h-segment-admission.{admission_sha}.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path, report


def _raw_bundle(inst_id: str) -> tuple[bytes, bytes, int]:
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    rows: list[list[str]] = []
    for index in range(COUNT):
        timestamp = start_ms + index * 3_600_000
        base = 100 + index / 100
        rows.append(
            [
                str(timestamp),
                f"{base:.8f}",
                f"{base + 1:.8f}",
                f"{base - 1:.8f}",
                f"{base + 0.5:.8f}",
                "10",
                "10",
                "10",
                "1",
            ]
        )
    pages = [rows[-300:][::-1], rows[:-300][::-1]]
    records: list[dict[str, object]] = []
    cursor = end_ms + 3_600_000
    for page_index, page_rows in enumerate(pages):
        body = json.dumps({"code": "0", "msg": "", "data": page_rows}, separators=(",", ":"))
        records.append(
            {
                "endpoint": "https://www.okx.com/api/v5/market/history-candles",
                "page_index": page_index,
                "request_params": {
                    "after": str(cursor),
                    "bar": "1H",
                    "instId": inst_id,
                    "limit": "300",
                },
                "response_body": body,
                "response_sha256": _digest(body.encode()),
            }
        )
        cursor = int(page_rows[-1][0])
    raw = b"".join(_canonical(record) + b"\n" for record in records)
    canonical = module.okx_history_rows_to_csv_bytes(rows)
    return raw, canonical, len(rows)


def _capture(repo: Path, fixture_only: bool = True) -> tuple[Path, dict[str, object]]:
    directory = repo / "reports/prospective-direct-1h-capture"
    directory.mkdir(parents=True)
    asset_identity: list[dict[str, object]] = []
    files: list[tuple[Path, bytes]] = []
    for inst_id in ASSETS:
        raw, canonical, count = _raw_bundle(inst_id)
        raw_sha, canonical_sha = _digest(raw), _digest(canonical)
        slug = inst_id.lower().replace("-", "_")
        raw_name = f"{slug}.{raw_sha}.raw.jsonl"
        csv_name = f"{slug}.{raw_sha}.append.csv"
        files.extend(((directory / raw_name, raw), (directory / csv_name, canonical)))
        asset_identity.append(
            {
                "inst_id": inst_id,
                "append_start": START.isoformat().replace("+00:00", "Z"),
                "append_end": END.isoformat().replace("+00:00", "Z"),
                "append_row_count": count,
                "raw_sha256": raw_sha,
                "canonical_sha256": canonical_sha,
                "raw_filename": raw_name,
                "append_filename": csv_name,
            }
        )
    identity = {
        "schema_version": 1,
        "policy_id": "prospective_direct_1h_segment_capture_v1",
        "tracked_inst_ids": ASSETS,
        "append_start": START.isoformat().replace("+00:00", "Z"),
        "append_end": END.isoformat().replace("+00:00", "Z"),
        "append_row_count_per_asset": COUNT,
        "append_row_count_total": COUNT * 6,
        "assets": asset_identity,
        "claims": {
            "fixture_only": fixture_only,
            "market_evidence": not fixture_only,
            "sample_evidence": False,
        },
    }
    capture_sha = _digest(_canonical(identity))
    report = {
        "schema_version": 1,
        "capture_sha256": capture_sha,
        "capture_status": module.SEGMENT_CAPTURE_STATUS,
        "identity": identity,
        "future_only_membership_evidence": True,
        "fixture_only": fixture_only,
        "market_evidence": not fixture_only,
        "sample_evidence": False,
        "profitability_evidence": False,
        "pnl_computation_authorized": False,
        "readiness_changed": False,
        "baseline_replacement_prohibited": True,
    }
    path = directory / f"prospective-direct-1h-capture.{capture_sha}.json"
    for file_path, content in files:
        file_path.write_bytes(content)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path, report


def _run(monkeypatch: pytest.MonkeyPatch, repo: Path, config: Path, admission: Path, report: dict[str, object], output: str, capture: Path | None = None):
    monkeypatch.setattr(module, "_repo_root", lambda _path: repo)
    monkeypatch.setattr(module, "validate_prospective_direct_1h_segment_admission", lambda _path: report)
    return module.materialize_prospective_direct_1h_segment_evidence(admission, config, output, capture)


def test_blocked_real_path_omits_capture_and_replays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, config = _repo(tmp_path)
    admission, report = _admission(repo, eligible=False)
    result = _run(monkeypatch, repo, config, admission, report, "reports/evidence-blocked", repo / "missing-capture.json")
    assert result.report["status"] == module.BLOCKED_STATUS
    assert result.report["capture_consumed"] is False
    assert result.report["asset_rows"] == result.report["artifact_rows"] == 0
    assert module.validate_prospective_direct_1h_segment_evidence(result.export_paths["report"])["candidate_sha256"] == result.report["candidate_sha256"]


def test_synthetic_exact_match_is_deterministic_and_replayable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)
    capture, _capture_report = _capture(repo)
    first = _run(monkeypatch, repo, config, admission, admission_report, "reports/evidence-one", capture)
    second = _run(monkeypatch, repo, config, admission, admission_report, "reports/evidence-two", capture)
    assert first.report["validated_segment_candidate"] is True
    assert first.report["asset_rows"] == 6
    assert first.report["artifact_rows"] == 12
    assert first.report["new_samples_counted"] == 0
    assert first.report["segment_appended"] is False
    assert first.report["fixture_only"] is True
    assert first.report["market_evidence"] is False
    for key in first.export_paths:
        assert Path(first.export_paths[key]).read_bytes() == Path(second.export_paths[key]).read_bytes()
    replayed = module.validate_prospective_direct_1h_segment_evidence(first.export_paths["report"])
    assert replayed["validated_segment_candidate"] is True


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("start", "request mismatch"),
        ("count", "request mismatch"),
        ("raw", "artifact hash mismatch"),
        ("canonical", "artifact hash mismatch"),
    ],
)
def test_capture_exact_match_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, match: str) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)
    capture, capture_report = _capture(repo)
    assets = capture_report["identity"]["assets"]  # type: ignore[index]
    item = assets[0]  # type: ignore[index]
    if mutation == "start":
        item["append_start"] = "2026-08-09T10:00:00Z"  # type: ignore[index]
    elif mutation == "count":
        item["append_row_count"] = COUNT - 1  # type: ignore[index]
    elif mutation == "raw":
        item["raw_sha256"] = "d" * 64  # type: ignore[index]
    else:
        item["canonical_sha256"] = "e" * 64  # type: ignore[index]
    identity = capture_report["identity"]  # type: ignore[index]
    new_sha = _digest(_canonical(identity))
    capture_report["capture_sha256"] = new_sha
    bad = capture.parent / f"prospective-direct-1h-capture.{new_sha}.json"
    bad.write_text(json.dumps(capture_report), encoding="utf-8")
    with pytest.raises(MarketDataError, match=match):
        _run(monkeypatch, repo, config, admission, admission_report, "reports/evidence-bad", bad)


def test_policy_paths_and_low_level_guards(tmp_path: Path) -> None:
    repo, config = _repo(tmp_path)
    assert module.load_segment_evidence_config(config, repo)["policy_id"] == module.POLICY_ID
    with pytest.raises(ValueError, match="filename"):
        module.load_segment_evidence_config(tmp_path / "wrong.yaml")
    with pytest.raises(ValueError, match="inside reports"):
        module._reports_output(repo, tmp_path / "outside")
    assert module._csv_value(False) == "false"
    assert module._csv_value(None) == ""
    assert module._is_sha256("a" * 64)
    assert not module._is_sha256("bad")
    with pytest.raises(MarketDataError, match="timestamp"):
        module._parse_iso("2026-08-09T10:00:00")
    with pytest.raises(MarketDataError, match="escape"):
        module._sibling(repo / "marker.json", "../bad")


def test_tampered_candidate_and_json_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)
    capture, _ = _capture(repo)
    result = _run(monkeypatch, repo, config, admission, admission_report, "reports/evidence", capture)
    report_path = Path(result.export_paths["report"])
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["segment_appended"] = True
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="segment_appended"):
        module.validate_prospective_direct_1h_segment_evidence(report_path)
    malformed = repo / "reports/malformed.json"
    malformed.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="shape"):
        module._load_json(malformed)
    missing = repo / "reports/missing.json"
    with pytest.raises(MarketDataError, match="read"):
        module._load_json(missing)


def test_validator_and_parent_guard_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)
    capture, _ = _capture(repo)
    result = _run(monkeypatch, repo, config, admission, admission_report, "reports/evidence", capture)
    report_path = Path(result.export_paths["report"])

    with pytest.raises(MarketDataError, match="path escape"):
        module.validate_prospective_direct_1h_segment_evidence(tmp_path / "outside.json")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["contract_status"] = "wrong"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="contract status"):
        module.validate_prospective_direct_1h_segment_evidence(report_path)
    payload["contract_status"] = module.CONTRACT_STATUS
    payload["identity"]["policy"] = {"wrong": True}
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="identity mismatch"):
        module.validate_prospective_direct_1h_segment_evidence(report_path)

    invalid = repo / "reports/evidence/prospective-direct-1h-segment-evidence.bad.json"
    invalid.write_text(json.dumps({"candidate_sha256": "a" * 64, "identity": {}}), encoding="utf-8")
    with pytest.raises(MarketDataError, match="identity mismatch"):
        module.validate_prospective_direct_1h_segment_evidence(invalid)

    wrong_admission = dict(admission_report)
    wrong_admission["contract_status"] = "wrong"
    with pytest.raises(MarketDataError, match="admission contract"):
        module._admission_state(admission, wrong_admission, repo)
    wrong_admission = dict(admission_report)
    wrong_admission["admission_sha256"] = "bad"
    with pytest.raises(MarketDataError, match="identity missing"):
        module._admission_state(admission, wrong_admission, repo)
    wrong_admission = dict(admission_report)
    wrong_admission["required_asset_count"] = 5
    with pytest.raises(MarketDataError, match="shape"):
        module._admission_state(admission, wrong_admission, repo)


def test_blocked_and_output_artifact_guard_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)
    capture, _ = _capture(repo)
    result = _run(monkeypatch, repo, config, admission, admission_report, "reports/evidence", capture)
    report_path = Path(result.export_paths["report"])
    original_report = report_path.read_bytes()

    def validate_with(mutator, expected: str) -> None:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        mutator(payload)
        report_path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(MarketDataError, match=expected):
            module.validate_prospective_direct_1h_segment_evidence(report_path)
        report_path.write_bytes(original_report)

    validate_with(lambda p: p["artifacts"].pop("dependencies"), "metadata mismatch:dependencies")
    dep = Path(result.export_paths["dependencies"])
    original = dep.read_bytes()
    dep.write_bytes(original + b"tamper")
    with pytest.raises(MarketDataError, match="bytes mismatch:dependencies"):
        module.validate_prospective_direct_1h_segment_evidence(report_path)
    dep.write_bytes(original)
    validate_with(lambda p: p.__setitem__("asset_rows", 5), "row count mismatch")

    original_csv = Path(result.export_paths["assets"]).read_bytes()
    monkeypatch.setattr(module, "_csv_bytes", lambda _rows, _fields: b"not-the-replay")
    with pytest.raises(MarketDataError, match="assets replay mismatch"):
        module.validate_prospective_direct_1h_segment_evidence(report_path)
    monkeypatch.undo()
    assert Path(result.export_paths["assets"]).read_bytes() == original_csv

    blocked_admission, blocked_report = _admission(repo, eligible=False)
    blocked = _run(monkeypatch, repo, config, blocked_admission, blocked_report, "reports/evidence-blocked")
    blocked_path = Path(blocked.export_paths["report"])
    blocked_payload = json.loads(blocked_path.read_text(encoding="utf-8"))
    blocked_payload["fixture_only"] = True
    blocked_path.write_text(json.dumps(blocked_payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="classification"):
        module.validate_prospective_direct_1h_segment_evidence(blocked_path)
    blocked_payload["fixture_only"] = False
    blocked_payload["capture_consumed"] = True
    blocked_path.write_text(json.dumps(blocked_payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="capture_consumed"):
        module.validate_prospective_direct_1h_segment_evidence(blocked_path)


def test_capture_and_extension_lineage_guard_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)
    capture, capture_report = _capture(repo)
    with pytest.raises(MarketDataError, match="path escape"):
        module._validate_capture_lineage(tmp_path / "missing.json", repo, {})
    unsafe = dict(capture_report)
    unsafe["pnl_computation_authorized"] = True
    unsafe_dir = repo / "reports/unsafe-capture"
    unsafe_dir.mkdir()
    unsafe_path = unsafe_dir / capture.name
    unsafe_path.write_text(json.dumps(unsafe), encoding="utf-8")
    with pytest.raises(MarketDataError, match="safety policy"):
        module._validate_capture_marker(unsafe_path, repo, {})
    unsafe_path.unlink()

    extension_dir = repo / "reports/prospective-direct-1h-extension"
    extension_dir.mkdir()
    extension_identity = {
        "schema_version": 1,
        "capture_sha256": capture.name.split(".")[1],
        "capture_report_sha256": _digest(capture.read_bytes()),
        "artifacts": {},
    }
    extension_artifacts: dict[str, dict[str, object]] = {}
    for name in ("datasets", "coverage", "constraints"):
        content = f"{name}\n".encode()
        file_path = extension_dir / f"extension.{name}.csv"
        file_path.write_bytes(content)
        extension_artifacts[name] = {"filename": file_path.name, "sha256": _digest(content)}
        extension_identity["artifacts"][f"{name}_sha256"] = _digest(content)  # type: ignore[index]
    extension_sha = _digest(_canonical(extension_identity))
    extension = extension_dir / f"prospective-direct-1h-extension.{extension_sha}.json"
    extension.write_text(
        json.dumps(
            {
                "extension_sha256": extension_sha,
                "audit_status": module.EXTENSION_AUDIT_STATUS,
                "identity": extension_identity,
                "artifacts": extension_artifacts,
            }
        ),
        encoding="utf-8",
    )
    validated = module._validate_capture_lineage(extension, repo, {})
    assert validated["source_report_identity"] == extension_sha

    missing_ref_identity = dict(extension_identity)
    missing_ref_identity.pop("capture_sha256")
    missing_ref_sha = _digest(_canonical(missing_ref_identity))
    missing_ref = extension_dir / f"prospective-direct-1h-extension.{missing_ref_sha}.json"
    missing_ref.write_text(
        json.dumps({"extension_sha256": missing_ref_sha, "audit_status": module.EXTENSION_AUDIT_STATUS, "identity": missing_ref_identity, "artifacts": extension_artifacts}),
        encoding="utf-8",
    )
    with pytest.raises(MarketDataError, match="capture reference"):
        module._validate_capture_lineage(missing_ref, repo, {})


def test_low_level_serialization_and_replay_guard_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)
    capture, capture_report = _capture(repo)
    state = module._admission_state(admission, admission_report, repo)
    captured = module._validate_capture_lineage(capture, repo, {})
    bad_capture = dict(captured)
    bad_capture["assets"] = []
    with pytest.raises(MarketDataError, match="do not match"):
        module._exact_match_rows(state, bad_capture, repo)
    bad_capture["assets"] = ["not-a-mapping"] + captured["assets"][1:]
    with pytest.raises(MarketDataError, match="do not match"):
        module._exact_match_rows(state, bad_capture, repo)
    bad_item = dict(captured["assets"][0])
    bad_item["append_start"] = "2026-08-09T10:00:00Z"
    bad_capture["assets"] = [bad_item] + captured["assets"][1:]
    with pytest.raises(MarketDataError, match="request mismatch"):
        module._exact_match_rows(state, bad_capture, repo)

    with pytest.raises(MarketDataError, match="policy semantics"):
        module._validate_policy({})
    with pytest.raises(MarketDataError, match="capture reference"):
        module._locate_capture(repo, "bad")
    with pytest.raises(MarketDataError, match="parent reference"):
        module._locate_report(repo, "missing", "bad")
    malformed = repo / "reports/bad.csv"
    malformed.write_text("wrong\nvalue\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="schema"):
        module._read_csv(malformed, ("key", "value"))
    invalid_utf = repo / "reports/invalid.csv"
    invalid_utf.write_bytes(b"\xff")
    with pytest.raises(MarketDataError, match="read failed"):
        module._read_csv(invalid_utf, ("key", "value"))
    with pytest.raises(MarketDataError, match="repo root"):
        module._repo_root(tmp_path / "no-project")
    assert module.format_segment_evidence_result(_run(monkeypatch, repo, config, admission, admission_report, "reports/formatted", capture)).startswith("contract_status:")


def test_additional_fail_closed_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)
    capture, capture_report = _capture(repo)
    with pytest.raises(MarketDataError, match="requires_capture_report"):
        _run(monkeypatch, repo, config, admission, admission_report, "reports/missing-capture")

    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="filename"):
        module.load_segment_evidence_config(bad_config)
    invalid_config = repo / "invalid-config" / module.DEFAULT_CONFIG_FILENAME
    invalid_config.parent.mkdir()
    invalid_config.write_text("[", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        module.load_segment_evidence_config(invalid_config, repo)
    drift_config = repo / "drift-config" / module.DEFAULT_CONFIG_FILENAME
    drift_config.parent.mkdir()
    drift_config.write_text(config.read_text(encoding="utf-8").replace("required_asset_count: 6", "required_asset_count: 5"), encoding="utf-8")
    with pytest.raises(MarketDataError, match="config mismatch"):
        module.load_segment_evidence_config(drift_config, repo)
    shape_config = repo / "shape-config" / module.DEFAULT_CONFIG_FILENAME
    shape_config.parent.mkdir()
    shape_config.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="config mismatch"):
        module.load_segment_evidence_config(shape_config, repo)

    result = _run(monkeypatch, repo, config, admission, admission_report, "reports/evidence", capture)
    report_path = Path(result.export_paths["report"])
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["identity"]["policy"] = {"bad": True}
    payload["candidate_sha256"] = _digest(_canonical(payload["identity"]))
    policy_path = report_path.parent / f"prospective-direct-1h-segment-evidence.{payload['candidate_sha256']}.json"
    report_path.unlink()
    policy_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="policy mismatch"):
        module.validate_prospective_direct_1h_segment_evidence(policy_path)

    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload["identity"]["policy"] = module.load_segment_evidence_config(config, repo)
    payload["identity"]["capture_sha256"] = None
    payload["capture_sha256"] = None
    payload["candidate_sha256"] = _digest(_canonical(payload["identity"]))
    missing_capture_path = policy_path.parent / f"prospective-direct-1h-segment-evidence.{payload['candidate_sha256']}.json"
    policy_path.unlink()
    missing_capture_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="capture reference missing"):
        module.validate_prospective_direct_1h_segment_evidence(missing_capture_path)

    valid = _run(monkeypatch, repo, config, admission, admission_report, "reports/evidence-valid", capture)
    valid_path = Path(valid.export_paths["report"])
    valid_payload = json.loads(valid_path.read_text(encoding="utf-8"))
    valid_payload["fixture_only"] = False
    valid_path.write_text(json.dumps(valid_payload), encoding="utf-8")
    with pytest.raises(MarketDataError, match="classification"):
        module.validate_prospective_direct_1h_segment_evidence(valid_path)

    valid_path.write_bytes(Path(valid.export_paths["report"]).read_bytes())
    original_csv_bytes = module._csv_bytes
    monkeypatch.setattr(module, "_csv_bytes", lambda rows, fields: original_csv_bytes(rows, fields) if fields == module.ASSET_FIELDS else b"bad-artifacts")
    with pytest.raises(MarketDataError, match="artifacts replay mismatch"):
        module.validate_prospective_direct_1h_segment_evidence(valid_path)
    monkeypatch.undo()


def test_admission_capture_and_artifact_guards(tmp_path: Path) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)

    missing = dict(admission_report)
    missing["artifacts"] = {}
    with pytest.raises(MarketDataError, match="assets missing"):
        module._admission_state(admission, missing, repo)
    hashed = dict(admission_report)
    hashed["artifacts"] = {"assets": dict(admission_report["artifacts"]["assets"])}  # type: ignore[index]
    hashed["artifacts"]["assets"]["sha256"] = "f" * 64  # type: ignore[index]
    with pytest.raises(MarketDataError, match="assets hash"):
        module._admission_state(admission, hashed, repo)
    admission_assets_path = repo / "reports/prospective-direct-1h-segment-admission" / ("admission." + "a" * 64 + ".assets.csv")
    rows = [dict(row) for row in module._read_csv(admission_assets_path, module.ADMISSION_ASSET_FIELDS)]
    short_bytes = module._csv_bytes(rows[:-1], module.ADMISSION_ASSET_FIELDS)
    short_path = admission.parent / "short.csv"
    short_path.write_bytes(short_bytes)
    short_report = dict(admission_report)
    short_report["artifacts"] = {"assets": {"filename": short_path.name, "sha256": _digest(short_bytes), "row_count": 5}}
    short_report["identity"] = {"artifacts": {"assets_sha256": _digest(short_bytes)}}
    with pytest.raises(MarketDataError, match="asset row count"):
        module._admission_state(admission, short_report, repo)
    for field, match in (("inst_id", "asset order"), ("timeframe", "timeframe")):
        changed = [dict(row) for row in rows]
        changed[0][field] = "BAD" if field == "inst_id" else "4H"
        changed_bytes = module._csv_bytes(changed, module.ADMISSION_ASSET_FIELDS)
        changed_path = admission.parent / f"changed-{field}.csv"
        changed_path.write_bytes(changed_bytes)
        changed_report = dict(admission_report)
        changed_report["artifacts"] = {"assets": {"filename": changed_path.name, "sha256": _digest(changed_bytes), "row_count": 6}}
        changed_report["identity"] = {"artifacts": {"assets_sha256": _digest(changed_bytes)}}
        with pytest.raises(MarketDataError, match=match):
            module._admission_state(admission, changed_report, repo)

    capture, capture_report = _capture(repo)
    bad_dir = repo / "reports/bad-capture"
    bad_dir.mkdir()
    bad = dict(capture_report)
    bad["capture_status"] = "wrong"
    bad_path = bad_dir / capture.name
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(MarketDataError, match="capture identity"):
        module._validate_capture_marker(bad_path, repo, {})
    bad_identity = json.loads(capture.read_text(encoding="utf-8"))["identity"]
    bad_identity["assets"][0]["inst_id"] = "BAD"
    bad_sha = _digest(_canonical(bad_identity))
    bad_asset_path = bad_dir / f"prospective-direct-1h-capture.{bad_sha}.json"
    bad_asset_path.write_text(json.dumps({**capture_report, "capture_sha256": bad_sha, "identity": bad_identity}), encoding="utf-8")
    with pytest.raises(MarketDataError, match="asset order"):
        module._validate_capture_marker(bad_asset_path, repo, {})

    extension_dir = repo / "reports/prospective-direct-1h-extension"
    extension_dir.mkdir(exist_ok=True)
    wrong_extension = extension_dir / "prospective-direct-1h-extension.bad.json"
    wrong_extension.write_text("{}", encoding="utf-8")
    with pytest.raises(MarketDataError, match="extension identity"):
        module._validate_extension_marker(wrong_extension, {})
    empty_extension_identity = {"artifacts": None}
    empty_extension_sha = _digest(_canonical(empty_extension_identity))
    empty_extension = extension_dir / f"prospective-direct-1h-extension.{empty_extension_sha}.json"
    with pytest.raises(MarketDataError, match="extension artifacts"):
        module._validate_extension_marker(
            empty_extension,
            {"extension_sha256": empty_extension_sha, "identity": empty_extension_identity, "artifacts": {}},
        )
    empty_extension.write_text("{}", encoding="utf-8")
    with pytest.raises(MarketDataError, match="extension artifacts"):
        module._validate_extension_marker(
            empty_extension,
            {"extension_sha256": empty_extension_sha, "identity": empty_extension_identity, "artifacts": {}},
        )

    marker = repo / "reports/marker.json"
    marker.write_text("x", encoding="utf-8")
    with pytest.raises(MarketDataError, match="missing"):
        module._sibling(marker, "missing.csv")
    with pytest.raises(MarketDataError, match="path escape"):
        module._sibling(marker, "sub/file.csv")


def test_replay_quality_and_utility_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, config = _repo(tmp_path)
    admission, admission_report = _admission(repo)
    capture, _ = _capture(repo)
    state = module._admission_state(admission, admission_report, repo)
    captured = module._validate_capture_lineage(capture, repo, {})
    monkeypatch.setattr(module, "okx_history_rows_to_csv_bytes", lambda _rows: b"wrong")
    with pytest.raises(MarketDataError, match="canonical replay"):
        module._exact_match_rows(state, captured, repo)
    monkeypatch.undo()
    monkeypatch.setattr(module, "_validate_quality", lambda _content: {"valid": False})
    with pytest.raises(MarketDataError, match="quality"):
        module._exact_match_rows(state, captured, repo)
    monkeypatch.undo()
    bad_state = dict(state)
    bad_state["expected_total_canonical_rows"] = COUNT * 6 + 1
    with pytest.raises(MarketDataError, match="total row"):
        module._exact_match_rows(bad_state, captured, repo)

    duplicate_dir = repo / "reports/duplicate"
    duplicate_dir.mkdir()
    duplicate = duplicate_dir / capture.name
    duplicate.write_bytes(capture.read_bytes())
    with pytest.raises(MarketDataError, match="ambiguous"):
        module._locate_capture(repo, str(capture.name.split(".")[1]))
    assert module._repo_root(repo / "reports") == repo
    yaml_bad = repo / "bad-yaml.yaml"
    yaml_bad.write_text("[", encoding="utf-8")
    with pytest.raises(MarketDataError, match="YAML read"):
        module._load_yaml(yaml_bad)
    yaml_shape = repo / "shape.yaml"
    yaml_shape.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="YAML shape"):
        module._load_yaml(yaml_shape)
    target = repo / "reports/collision"
    target.write_bytes(b"old")
    with pytest.raises(MarketDataError, match="collision"):
        module._commit_bytes(target, b"new")
    module._commit_bytes(target, b"old")
    with pytest.raises(MarketDataError, match="timestamp invalid"):
        module._parse_iso("not-a-date")
    assert not module._is_sha256("g" * 64)


def test_cli_dispatches_segment_evidence_command(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = module.ProspectiveDirect1hSegmentEvidenceResult(
        {"contract_status": "x", "candidate_sha256": "a" * 64, "status": "blocked", "segment_candidate_materialized": False, "capture_consumed": False, "asset_rows": 0, "artifact_rows": 0, "current_samples": 160, "remaining_samples": 340},
        {"report": "reports/fake.json"},
    )
    monkeypatch.setattr(cli_module, "materialize_prospective_direct_1h_segment_evidence", lambda *_args: fake)
    monkeypatch.setattr(sys, "argv", ["crypto-bot", "materialize-prospective-direct-1h-segment-evidence", "--segment-admission", "admission.json"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
