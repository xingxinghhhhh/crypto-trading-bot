from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_membership_epoch_closure as module
from crypto_bot.errors import MarketDataError
from crypto_bot.market.prospective_membership_epoch_materializer import MEMBERSHIP_FIELDS


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME
OPEN_REAL = next((ROOT / "reports/prospective-membership-epoch-smoke").glob("*.json"))
NEXT_REAL = next((ROOT / "reports/prospective-snapshot-transition-materializer-v2").glob("*.json"))


def _write_csv(path: Path, rows: list[dict[str, object]], fields: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def workspace(tmp_path: Path):
    path = ROOT / "reports" / f"prospective-membership-epoch-closure-tests-{tmp_path.name}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _synthetic_chain(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    transition_sha = "d" * 64
    open_sha = "c" * 64
    membership_path = workspace / f"prospective-membership-epoch.{open_sha}.membership.csv"
    _write_csv(
        membership_path,
        [{
            "epoch_id": "epoch-0002",
            "epoch_ordinal": 2,
            "inst_id": inst_id,
            "membership_state": "retained",
            "source_transition_identity": "b" * 64,
            "previous_membership_state": "retained",
            "automatic_replacement": False,
        } for inst_id in ("BTC-USDT", "ETH-USDT", "SOL-USDT", "KNC-USDT", "SWFTC-USDT", "BICO-USDT")],
        MEMBERSHIP_FIELDS,
    )
    open_report = {
        "membership_epoch_sha256": open_sha,
        "contract_status": "verified_prospective_membership_epoch_materialization",
        "membership_epoch_materialized": True,
        "status": "open_pending_future_close",
        "epoch_id": "epoch-0002",
        "current_snapshot_identity": "b" * 64,
        "current_snapshot_received_at": "2026-08-16T10:30:00Z",
        "membership_effective_at": "2026-08-16T11:00:00Z",
        "artifacts": {"membership": {"filename": membership_path.name}},
    }
    open_marker = workspace / f"prospective-membership-epoch.{open_sha}.json"
    open_marker.write_text(json.dumps(open_report, sort_keys=True), encoding="utf-8")
    transition = {
        "transition_sha256": transition_sha,
        "contract_status": module.TRANSITION_STATUS,
        "transition_materialized": True,
        "previous_snapshot_identity": "b" * 64,
        "current_snapshot_identity": "e" * 64,
        "previous_received_at": "2026-08-16T10:30:00Z",
        "current_received_at": "2026-08-23T10:30:00Z",
    }
    transition_marker = workspace / f"prospective-snapshot-transition.{transition_sha}.json"
    transition_marker.write_text(json.dumps(transition, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(module, "validate_prospective_membership_epoch_materialization", lambda _path: open_report)
    monkeypatch.setattr(module, "validate_prospective_snapshot_transition_materialization", lambda _path: transition)
    return open_marker, transition_marker


def test_real_blocked_parents_do_not_close_epoch(workspace: Path) -> None:
    result = module.close_prospective_membership_epoch(OPEN_REAL, NEXT_REAL, CONFIG, workspace / "blocked")
    assert result.report["status"] == "blocked_no_open_membership_epoch"
    assert result.report["membership_epoch_closed"] is False
    assert result.report["membership_rows"] == 0
    assert result.report["closed_interval_count"] == 0
    assert result.report["sample_credit"] == 0
    assert module.validate_prospective_membership_epoch_closure(result.export_paths["report"])["membership_rows"] == 0


def test_synthetic_open_epoch_closes_with_strict_next_snapshot_boundary(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    open_marker, next_marker = _synthetic_chain(workspace, monkeypatch)
    one = module.close_prospective_membership_epoch(open_marker, next_marker, CONFIG, workspace / "one")
    two = module.close_prospective_membership_epoch(open_marker, next_marker, CONFIG, workspace / "two")
    assert one.report["status"] == "closed_future_epoch"
    assert one.report["membership_epoch_closed"] is True
    assert one.report["first_signal_timestamp"] == "2026-08-16T11:00:00Z"
    assert one.report["last_signal_timestamp"] == "2026-08-23T08:00:00Z"
    assert one.report["last_execution_timestamp"] == "2026-08-23T10:00:00Z"
    assert one.report["epoch_end_resolved"] is True
    assert one.report["closed_interval_count"] == 166
    assert one.report["membership_rows"] == 6
    assert one.report["sample_credit"] == 0
    assert Path(one.export_paths["report"]).read_bytes() == Path(two.export_paths["report"]).read_bytes()
    assert module.validate_prospective_membership_epoch_closure(one.export_paths["report"])["closed_interval_count"] == 166


def test_lineage_and_override_guards(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    open_marker, next_marker = _synthetic_chain(workspace, monkeypatch)
    transition = json.loads(next_marker.read_text(encoding="utf-8"))
    transition["previous_snapshot_identity"] = "f" * 64
    monkeypatch.setattr(module, "validate_prospective_snapshot_transition_materialization", lambda _path: transition)
    with pytest.raises(MarketDataError, match="lineage"):
        module.close_prospective_membership_epoch(open_marker, next_marker, CONFIG, workspace / "bad-lineage")
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["sample_threshold"] = 499
    bad = workspace / CONFIG.name
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(MarketDataError, match="config mismatch"):
        module.close_prospective_membership_epoch(open_marker, next_marker, bad, workspace / "bad-config")
    transition["previous_snapshot_identity"] = "b" * 64
    transition["transition_materialized"] = False
    blocked = module.close_prospective_membership_epoch(open_marker, next_marker, CONFIG, workspace / "blocked-next")
    assert blocked.report["status"] == "blocked_next_transition_not_materialized"
    assert blocked.report["membership_rows"] == 0


def test_tamper_and_low_level_guards(workspace: Path, tmp_path: Path) -> None:
    result = module.close_prospective_membership_epoch(OPEN_REAL, NEXT_REAL, CONFIG, workspace / "tamper")
    report = Path(result.export_paths["report"])
    value = json.loads(report.read_text(encoding="utf-8"))
    value["epoch_end_resolved"] = True
    report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(MarketDataError, match="report mismatch|identity mismatch"):
        module.validate_prospective_membership_epoch_closure(report)
    with pytest.raises(ValueError, match="output"):
        module.close_prospective_membership_epoch(OPEN_REAL, NEXT_REAL, CONFIG, tmp_path / "outside")
    with pytest.raises(ValueError, match="filename"):
        module.load_membership_epoch_closure_config(tmp_path / "bad.yaml")
    malformed = tmp_path / "bad.json"
    malformed.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="JSON shape"):
        module._load_json(malformed)
    with pytest.raises(MarketDataError, match="policy semantics"):
        module._validate_policy({})
    assert module._csv_value(None) == ""
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("key,value\na,1\na,2\n", encoding="utf-8")
    with pytest.raises(MarketDataError, match="key collision"):
        module._read_key_values(duplicate)
    assert module._is_sha256("a" * 64)
    assert not module._is_sha256("bad")
    with pytest.raises(MarketDataError, match="report path"):
        module.validate_prospective_membership_epoch_closure(ROOT / "README.md")
