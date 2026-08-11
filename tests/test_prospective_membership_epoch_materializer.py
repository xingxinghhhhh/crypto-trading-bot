from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest
import yaml

import crypto_bot.market.prospective_membership_epoch_materializer as module
from crypto_bot.errors import MarketDataError
from crypto_bot.market.okx_future_universe_archive import TRACKED_FIELDS


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / module.DEFAULT_CONFIG_FILENAME
CHAIN = next((ROOT / "reports/prospective-direct-1h-segment-chain").glob("*.json"))
_CHAIN_REPORT = json.loads(CHAIN.read_text(encoding="utf-8"))
_CHAIN_GATE_SHA = _CHAIN_REPORT["identity"]["segments"][0]["membership_gate_sha256"]
GATE = ROOT / "reports/prospective-membership-bar-gate" / f"prospective-membership-bar-gate.{_CHAIN_GATE_SHA}.json"
TRANSITION = next((ROOT / "reports/prospective-snapshot-transition-materializer-v2").glob("*.json"), None)
if TRANSITION is None:
    TRANSITION = next((ROOT / "reports/prospective-snapshot-transition").glob("*.json"))


def _write_csv(path: Path, rows: list[dict[str, object]], fields: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _synthetic_transition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, object]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    gate = json.loads(GATE.read_text(encoding="utf-8"))
    transition_sha = "a" * 64
    tracked_path = tmp_path / f"prospective-snapshot-transition.{transition_sha}.tracked-assets.csv"
    _write_csv(
        tracked_path,
        [{
            "inst_id": inst_id,
            "snapshot_received_at": "2026-08-16T10:30:00+00:00",
            "present": "true",
            "state": "live",
            "policy_eligible": "true",
            "tracked_membership_status": "retained",
            "exclusion_reasons": "",
            "effective_continuous_start": "2021-01-01T00:00:00+00:00",
        } for inst_id in module.EXPECTED_INST_IDS],
        TRACKED_FIELDS,
    )
    transition = {
        "transition_sha256": transition_sha,
        "contract_status": module.TRANSITION_STATUS,
        "transition_materialized": True,
        "previous_snapshot_identity": gate["identity"]["current_snapshot_sha256"],
        "current_snapshot_identity": "b" * 64,
        "previous_received_at": "2026-08-09T10:22:11.263565Z",
        "current_received_at": "2026-08-16T10:30:00Z",
        "artifacts": {"tracked": {"filename": tracked_path.name}},
    }
    marker = tmp_path / f"prospective-snapshot-transition.{transition_sha}.json"
    marker.write_text(json.dumps(transition, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(module, "validate_prospective_snapshot_transition_materialization", lambda _path: transition)
    return marker, transition


@pytest.fixture
def workspace(tmp_path: Path):
    path = ROOT / "reports" / f"prospective-membership-epoch-materializer-tests-{tmp_path.name}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_real_pending_transition_is_blocked_and_replayed(workspace: Path) -> None:
    result = module.materialize_prospective_membership_epoch(TRANSITION, GATE, CHAIN, CONFIG, workspace / "blocked")
    assert result.report["status"] == "blocked_transition_not_materialized"
    assert result.report["membership_epoch_materialized"] is False
    assert result.report["membership_rows"] == 0
    assert result.report["new_samples_counted"] == 0
    assert module.validate_prospective_membership_epoch_materialization(result.export_paths["report"])["membership_rows"] == 0


def test_synthetic_accepted_transition_opens_future_epoch_and_is_deterministic(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker, _ = _synthetic_transition(workspace / "transition", monkeypatch)
    one = module.materialize_prospective_membership_epoch(marker, GATE, CHAIN, CONFIG, workspace / "one")
    two = module.materialize_prospective_membership_epoch(marker, GATE, CHAIN, CONFIG, workspace / "two")
    assert one.report["status"] == "open_pending_future_close"
    assert one.report["membership_epoch_materialized"] is True
    assert one.report["membership_effective_at"] == "2026-08-16T11:00:00Z"
    assert one.report["epoch_end_resolved"] is False
    assert one.report["membership_rows"] == 6
    assert one.report["new_samples_counted"] == 0
    assert Path(one.export_paths["report"]).read_bytes() == Path(two.export_paths["report"]).read_bytes()
    assert module.validate_prospective_membership_epoch_materialization(one.export_paths["report"])["status"] == "open_pending_future_close"


def test_transition_lineage_and_manual_config_guards(workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker, transition = _synthetic_transition(workspace / "transition", monkeypatch)
    transition["previous_snapshot_identity"] = "c" * 64
    with pytest.raises(MarketDataError, match="lineage"):
        module.materialize_prospective_membership_epoch(marker, GATE, CHAIN, CONFIG, workspace / "bad-lineage")
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["sample_threshold"] = 499
    bad_config = tmp_path / CONFIG.name
    bad_config.write_text(yaml.safe_dump(config), encoding="utf-8")
    transition["previous_snapshot_identity"] = json.loads(GATE.read_text(encoding="utf-8"))["identity"]["current_snapshot_sha256"]
    with pytest.raises(MarketDataError, match="config mismatch"):
        module.materialize_prospective_membership_epoch(marker, GATE, CHAIN, bad_config, workspace / "bad-config")


def test_artifact_tamper_and_path_guards(workspace: Path, tmp_path: Path) -> None:
    result = module.materialize_prospective_membership_epoch(TRANSITION, GATE, CHAIN, CONFIG, workspace / "report")
    report = Path(result.export_paths["report"])
    value = json.loads(report.read_text(encoding="utf-8"))
    value["epoch_end_resolved"] = True
    report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(MarketDataError, match="report mismatch|identity mismatch"):
        module.validate_prospective_membership_epoch_materialization(report)
    with pytest.raises(ValueError, match="output"):
        module.materialize_prospective_membership_epoch(TRANSITION, GATE, CHAIN, CONFIG, ROOT / "tmp-membership-epoch-outside")


def test_low_level_guards(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="filename"):
        module.load_membership_epoch_materializer_config(tmp_path / "wrong.yaml")
    malformed = tmp_path / "bad.json"
    malformed.write_text("[]", encoding="utf-8")
    with pytest.raises(MarketDataError, match="JSON shape"):
        module._load_json(malformed)
    with pytest.raises(MarketDataError, match="policy semantics"):
        module._validate_policy({})
    with pytest.raises(MarketDataError, match="report path"):
        module.validate_prospective_membership_epoch_materialization(ROOT / "README.md")
    assert module._csv_value(False) == "false"
