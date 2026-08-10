from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_direct_1h_segment_chain as module


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "reports/prospective-epoch-accumulation-policy/prospective-epoch-accumulation-policy.8f4a90632bcf73df04c7e6f44924924db682cdc8373ed04c1f726cb6ce353092.json"
MATURITY = ROOT / "reports/prospective-economic-sample-maturity/prospective-economic-sample-maturity.32cf37484013bf7ee5739016cf5ef54e935ee2ac4dfcb429266f24adc885a178.json"
READINESS = ROOT / "reports/prospective-economic-readiness/prospective-economic-readiness.0f160960684cf5a4ed5e7636f59e863652a3c0f3087eadb3ed61091bb026f27b.json"
EXTENSION = ROOT / "reports/prospective-direct-1h-extension/prospective-direct-1h-extension.b0cbcb119a24ee786a81a6aed7adf4ddb8b11cf93891c19ca1fb21c4d3848146.json"
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME


def test_current_chain_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "_reports_output", lambda _repo, output: (Path(output).mkdir(parents=True, exist_ok=True) or Path(output)))
    first = module.freeze_prospective_direct_1h_segment_chain(POLICY, MATURITY, READINESS, [EXTENSION], CONFIG, tmp_path / "one")
    second = module.freeze_prospective_direct_1h_segment_chain(POLICY, MATURITY, READINESS, [EXTENSION], CONFIG, tmp_path / "two")
    assert first.report["chain_sha256"] == second.report["chain_sha256"]
    assert first.report["segment_count"] == 1
    assert first.report["asset_segment_rows"] == 6
    assert first.report["segment_1_start"] == "2026-08-02T15:00:00Z"
    assert first.report["segment_1_end"] == "2026-08-09T10:00:00Z"
    assert first.report["current_chain_tail"] == "2026-08-09T10:00:00Z"
    assert first.report["next_canonical_segment_start"] == "2026-08-09T11:00:00Z"
    assert first.report["next_segment_end_resolved"] is False
    assert first.report["unique_closed_execution_intervals"] == 160
    for key in first.export_paths:
        left, right = Path(first.export_paths[key]), Path(second.export_paths[key])
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes()


@pytest.mark.parametrize("field,value", [("timeframe", "4H"), ("canonical_gap_policy", "allow"), ("required_asset_count", 5)])
def test_config_drift_rejected(tmp_path: Path, field: str, value: object) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config[field] = value
    path = tmp_path / module.DEFAULT_CONFIG_FILENAME
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises((ValueError, module.MarketDataError)):
        module.load_segment_chain_config(path)


def test_guards_and_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        module.load_segment_chain_config(tmp_path / "wrong.yaml")
    with pytest.raises(ValueError):
        module._reports_output(ROOT, tmp_path)
    with pytest.raises(ValueError):
        module.freeze_prospective_direct_1h_segment_chain(POLICY, MATURITY, READINESS, [], CONFIG, tmp_path)
    result = module.SegmentChainResult({"audit_status": "status", "chain_sha256": "sha", "segment_count": 1, "asset_segment_rows": 6, "current_chain_tail": "a", "next_canonical_segment_start": "b"}, {})
    assert "chain_sha256: sha" in module.format_segment_chain_result(result)


def test_policy_and_dependency_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    maturity = json.loads(MATURITY.read_text(encoding="utf-8"))
    readiness = json.loads(READINESS.read_text(encoding="utf-8"))
    with pytest.raises(module.MarketDataError):
        module._validate_policy_inputs(policy, maturity, readiness, POLICY.parent / "bad.json", MATURITY, READINESS)
    policy["policy_sha256"] = "bad"
    with pytest.raises(module.MarketDataError):
        module._validate_policy_inputs(policy, maturity, readiness, POLICY, MATURITY, READINESS)
    extension = json.loads(EXTENSION.read_text(encoding="utf-8"))
    extension["extension_sha256"] = "bad"
    with pytest.raises(module.MarketDataError):
        module._validate_extension(extension, EXTENSION, ROOT, 1)
    valid = json.loads(EXTENSION.read_text(encoding="utf-8"))
    valid["identity"]["migration_sha256"] = "future-migration"
    valid["extension_sha256"] = module._digest(module._canonical_json_bytes(valid["identity"]))
    with pytest.raises(module.MarketDataError):
        module._validate_extension(valid, EXTENSION, ROOT, 2)
    monkeypatch.setattr(module, "_load_json", lambda _path: {"migration_sha256": "bad", "identity": {}})
    with pytest.raises(module.MarketDataError):
        module._validate_migration(ROOT)
    with pytest.raises(module.MarketDataError):
        module._marker(tmp_path / "missing.json", "prospective-economic-readiness", ROOT)
    with pytest.raises(module.MarketDataError):
        module._sibling(EXTENSION, "missing.csv")


def test_low_level_guards(tmp_path: Path) -> None:
    with pytest.raises(module.MarketDataError):
        module._parse_iso("2026-08-09T10:00:00")
    with pytest.raises(module.MarketDataError):
        module._read_csv(tmp_path / "missing.csv")
    with pytest.raises(module.MarketDataError):
        module._load_json(tmp_path / "missing.json")
    path = tmp_path / "artifact"
    module._commit_bytes(path, b"same")
    module._commit_bytes(path, b"same")
    with pytest.raises(module.MarketDataError):
        module._commit_bytes(path, b"different")
    assert module._canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_validated_dependencies_and_serializers() -> None:
    baseline = module._validate_migration(ROOT)
    assert tuple(baseline) == module.INST_IDS
    capture, capture_path = module._validate_extension(json.loads(EXTENSION.read_text(encoding="utf-8")), EXTENSION, ROOT, 2)
    assert capture["capture_sha256"] == module.CAPTURE_SHA
    assert capture_path.parent.name == "prospective-direct-1h-capture"
    assert module.load_segment_chain_config(CONFIG, ROOT)["timeframe"] == "1H"
    assert module._iso(module._parse_iso("2026-08-09T10:00:00Z")) == "2026-08-09T10:00:00Z"
    rows = module._rows({"b": True, "a": 1})
    assert rows[0]["key"] == "a"
    assert module._sha256(EXTENSION) == module._digest(EXTENSION.read_bytes())
    assert module._repo_root(EXTENSION) == ROOT
    marker = module._marker(POLICY, "prospective-epoch-accumulation-policy", ROOT)
    assert marker["policy_sha256"] == module._load_json(POLICY)["policy_sha256"]
    sibling = module._sibling(EXTENSION, module._load_json(EXTENSION)["artifacts"]["datasets"]["filename"])
    assert sibling.is_file()
    assert module._csv_bytes([{"key": "a", "value": "b"}], module.CONSTRAINT_FIELDS) == b"key,value\na,b\n"


def test_reordered_extension_is_rejected(tmp_path: Path) -> None:
    extension = json.loads(EXTENSION.read_text(encoding="utf-8"))
    extension["identity"]["append_start"] = "2026-08-09T11:00:00Z"
    with pytest.raises(module.MarketDataError):
        module._validate_extension(extension, EXTENSION, ROOT, 1)
