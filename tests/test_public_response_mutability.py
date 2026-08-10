import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import crypto_bot.market.public_response_mutability as module
import crypto_bot.cli as cli_module
from crypto_bot.errors import MarketDataError
from crypto_bot.market.okx_direct_six_asset_migration import ANCHOR_DATASET_IDS


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config.public-response-mutability.example.yaml"


def test_policy_is_frozen_and_contract_is_exact(tmp_path):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    policy_path = repo / POLICY.name
    shutil.copy2(POLICY, policy_path)
    policy = module.load_public_response_mutability_policy(policy_path, repo)
    assert policy["inst_ids"] == list(module.ANCHOR_INST_IDS)
    assert policy["fields"] == list(module.FIELDS)
    with pytest.raises(FileNotFoundError):
        module.load_public_response_mutability_policy(repo / "missing.yaml", repo)
    wrong_name = repo / "policy.yaml"
    shutil.copy2(POLICY, wrong_name)
    with pytest.raises(ValueError, match="filename"):
        module.load_public_response_mutability_policy(wrong_name, repo)
    wrong = repo / POLICY.name
    wrong.write_text("schema_version: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen config"):
        module.load_public_response_mutability_policy(wrong, repo)


def test_compare_page_classifies_all_field_changes_and_row_delta():
    baseline = {
        "page_index": 3,
        "code": "0",
        "msg": "",
        "data": [["1", "2", "3", "4", "5", "6", "7", "8", "1"]],
    }
    comparison = {
        "page_index": 3,
        "code": "1",
        "msg": "changed",
        "data": [["9", "20", "30", "40", "50", "60", "70", "80", "0"], ["2"]],
    }
    policy = module.load_public_response_mutability_policy(POLICY)
    diffs = module._compare_page("BTC-USDT", baseline, comparison, policy)
    categories = {item["category"] for item in diffs}
    assert categories == {
        "response_envelope_changed",
        "timestamp_changed",
        "ohlc_changed",
        "base_volume_changed",
        "quote_volume_changed",
        "confirm_changed",
        "row_added_or_removed",
    }
    assert sum(item["category"] == "quote_volume_changed" for item in diffs) == 2


def test_bundle_pages_rejects_bad_hash_and_invalid_json(tmp_path):
    bad_hash = tmp_path / "bad.jsonl"
    response = json.dumps({"code": "0", "msg": "", "data": []}, separators=(",", ":"))
    bad_hash.write_text(
        json.dumps(
            {
                "page_index": 0,
                "request_params": {},
                "response_body": response,
                "response_sha256": "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(MarketDataError, match="response_hash_mismatch"):
        module._bundle_pages(bad_hash, "BTC-USDT")
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="invalid_bundle"):
        module._bundle_pages(invalid, "BTC-USDT")


def test_audit_is_deterministic_and_reports_realistic_field_diffs(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    reports = repo / "reports"
    reports.mkdir(parents=True)
    policy_path = repo / POLICY.name
    shutil.copy2(POLICY, policy_path)
    baseline = _fake_capture(repo, "baseline", volume_delta=False)
    comparison = _fake_capture(repo, "comparison", volume_delta=True)
    monkeypatch.setattr(module, "validate_okx_direct_anchor_1h_capture", _fake_validator(baseline, comparison))
    monkeypatch.setattr(module, "_migration_pin", lambda _policy, _repo: {"baseline_remains_pinned": True})
    first = module.audit_okx_public_response_mutability(
        baseline.report_path,
        comparison.report_path,
        policy_path,
        reports / "audit-a",
    )
    second = module.audit_okx_public_response_mutability(
        baseline.report_path,
        comparison.report_path,
        policy_path,
        reports / "audit-b",
    )
    assert first.report == second.report
    assert first.report["identity"]["differences"]["total"] == 3
    assert first.report["identity"]["differences"]["categories"]["quote_volume_changed"] == 2
    assert first.report["identity"]["capture_replay_deterministic"] is True
    assert first.report["identity"]["network_recapture_byte_identical"] is False
    difference_csv = Path(first.export_paths["differences"])
    assert "volCcyQuote" in difference_csv.read_text(encoding="utf-8")
    assert {Path(value).name for value in first.export_paths.values()} == {
        Path(value).name for value in second.export_paths.values()
    }


def test_audit_rejects_same_identity_request_mismatch_and_output_escape(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "reports").mkdir(parents=True)
    policy_path = repo / POLICY.name
    shutil.copy2(POLICY, policy_path)
    baseline = _fake_capture(repo, "baseline", volume_delta=False)
    same = _fake_capture(repo, "same", volume_delta=False)
    same.report["capture_sha256"] = baseline.report["capture_sha256"]
    monkeypatch.setattr(module, "validate_okx_direct_anchor_1h_capture", _fake_validator(baseline, same))
    monkeypatch.setattr(module, "_migration_pin", lambda _policy, _repo: {"baseline_remains_pinned": True})
    with pytest.raises(MarketDataError, match="identity_not_distinct"):
        module.audit_okx_public_response_mutability(
            baseline.report_path, same.report_path, policy_path, repo / "reports" / "audit"
        )
    different = _fake_capture(repo, "different", volume_delta=False)
    different.report["identity"]["policy"]["end_open"] = "different"
    monkeypatch.setattr(module, "validate_okx_direct_anchor_1h_capture", _fake_validator(baseline, different))
    with pytest.raises(MarketDataError, match="policy_mismatch"):
        module.audit_okx_public_response_mutability(
            baseline.report_path, different.report_path, policy_path, repo / "reports" / "audit"
        )
    with pytest.raises(ValueError, match="inside reports"):
        module._reports_output(repo, repo / "outside")


def test_commit_collision_and_policy_flattening(tmp_path):
    path = tmp_path / "artifact"
    module._commit_bytes(path, b"stable")
    module._commit_bytes(path, b"stable")
    with pytest.raises(MarketDataError, match="collision"):
        module._commit_bytes(path, b"changed")
    flattened = module._flatten_policy({"b": {"z": 1}, "a": True})
    assert flattened == {"a": True, "b.z": 1}
    assert module._difference_counts([])["quote_volume_changed"] == 0
    assert module._csv_value(["a", 1]) == '["a",1]'


def test_cli_mutability_command_has_no_policy_overrides(monkeypatch, capsys):
    result = SimpleNamespace(
        report={
            "audit_status": module.AUDIT_STATUS,
            "audit_sha256": "a" * 64,
            "identity": {
                "differences": {"total": 3},
                "public_historical_response_mutability_observed": True,
            },
        },
        export_paths={"report": "report.json"},
    )
    monkeypatch.setattr(cli_module, "audit_okx_public_response_mutability", lambda *_args: result)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto-bot",
            "audit-okx-public-response-mutability",
            "--baseline-capture",
            "baseline.json",
            "--comparison-capture",
            "comparison.json",
        ],
    )
    with pytest.raises(SystemExit) as exited:
        cli_module.main()
    assert exited.value.code == 0
    assert "difference_count: 3" in capsys.readouterr().out


def _fake_validator(baseline, comparison):
    def validate(path):
        return baseline if "baseline" in Path(path).name else comparison

    return validate


def _fake_capture(repo, role, *, volume_delta):
    capture_dir = repo / "reports" / role
    capture_dir.mkdir(parents=True, exist_ok=True)
    inst_ids = list(module.ANCHOR_INST_IDS)
    histories = []
    artifacts = {}
    for inst_id in inst_ids:
        changed = volume_delta and inst_id == "ETH-USDT"
        rows = [["1785222000000", "1888.51", "1889.02", "1880.66", "1883.97", "3719.417509" if changed else "3719.417652", "7010896.30557013" if changed else "7010896.57502503", "7010896.30557013" if changed else "7010896.57502503", "1"]]
        response = json.dumps({"code": "0", "msg": "", "data": rows}, separators=(",", ":"))
        record = {
            "page_index": 0,
            "request_params": {"instId": inst_id, "bar": "1H"},
            "response_body": response,
            "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
        }
        bundle = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        bundle_path = capture_dir / f"{inst_id}.jsonl"
        bundle_path.write_bytes(bundle)
        csv_path = capture_dir / f"{inst_id}.csv"
        csv_path.write_text("timestamp,open,high,low,close,volume\n2026-01-01T00:00:00+00:00,1,1,1,1,1\n", encoding="utf-8")
        artifacts[f"{inst_id}_history"] = bundle_path
        artifacts[f"{inst_id}_csv"] = csv_path
        histories.append(
            {
                "inst_id": inst_id,
                    "dataset_id": ANCHOR_DATASET_IDS[inst_id],
                "bar_count": 40191,
                "pages": [{"page_index": 0, "request_params": record["request_params"]}],
                "history_bundle": {"sha256": hashlib.sha256(bundle).hexdigest()},
                "csv": {"raw_sha256": "r" * 64, "canonical_sha256": "c" * 64},
            }
        )
    report_path = capture_dir / f"{role}.json"
    report_path.write_text(role, encoding="utf-8")
    report = {
        "capture_sha256": "b" * 64 if role == "baseline" else "c" * 64,
        "identity": {
            "policy": {
                "anchor_inst_ids": inst_ids,
                "history_start": "2022-01-01T00:00:00+00:00",
                "end_open": "2026-08-02T14:00:00+00:00",
                "okx_bar": "1H",
                "timeframe": "1h",
                "expected_bar_count": 40191,
                "expected_page_count": 134,
            },
            "contract": {"official_url": "https://www.okx.com/docs-v5/en/"},
        },
    }
    return SimpleNamespace(report_path=report_path, report=report, histories=tuple(histories), artifact_paths=artifacts)
