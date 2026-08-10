from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import crypto_bot.prospective_portfolio_ledger as ledger_module
from crypto_bot.cross_sectional_portfolio_mechanism import RANK_DIRECTIONS
from crypto_bot.factors import DEFAULT_FACTOR_SPECS
from crypto_bot.prospective_portfolio_ledger import (
    FACTORS,
    INST_IDS,
    _decision_rows,
    _factor_score_rows,
)


def _bars(*, future_delta: float = 0.0) -> pd.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(32):
        timestamp = start + timedelta(hours=index)
        close = 100.0 + index + (future_delta if index >= 25 else 0.0)
        rows.append(
            {
                "timestamp": timestamp,
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 1000 + index,
            }
        )
    return pd.DataFrame(rows)


def test_future_rows_do_not_change_signal_scores() -> None:
    signal = "2026-01-02T00:00:00Z"
    first = {symbol: _bars() for symbol in INST_IDS}
    changed = {symbol: _bars(future_delta=500.0) for symbol in INST_IDS}
    before = _factor_score_rows(first, [signal], {})
    after = _factor_score_rows(changed, [signal], {})
    assert before == after


def test_ledger_emits_all_registered_families_and_all_cash_tie_rows() -> None:
    timestamp = "2026-01-02T00:00:00Z"
    scores = {
        (timestamp, symbol, factor): 1.0
        for symbol in INST_IDS
        for factor in FACTORS
    }
    decisions, weights = _decision_rows(scores, [timestamp], {})
    assert len(decisions) == 36
    assert len(weights) == 216
    assert {row["rank_direction"] for row in decisions} == set(RANK_DIRECTIONS)
    assert all(row["decision_status"] == "all_cash" for row in decisions)
    assert all(row["decision_reason"] == "ambiguous_top_k_tie" for row in decisions)
    assert all(row["weight"] == 0.0 for row in weights)
    assert all(row["selected"] is False for row in weights)


def test_ledger_top_two_weights_and_horizon_identity() -> None:
    timestamp = "2026-01-02T00:00:00Z"
    scores = {
        (timestamp, symbol, factor): float(index + 1)
        for index, symbol in enumerate(INST_IDS)
        for factor in FACTORS
    }
    decisions, weights = _decision_rows(scores, [timestamp], {})
    assert all(row["decision_status"] == "invested" for row in decisions)
    assert all(row["cash_weight"] == 0.0 and row["gross_exposure"] == 1.0 and row["net_exposure"] == 1.0 for row in decisions)
    for factor in FACTORS:
        for direction in RANK_DIRECTIONS:
            variants = [
                row for row in decisions
                if row["factor"] == factor and row["rank_direction"] == direction
            ]
            assert {row["selected_asset_1"] for row in variants} == {"BICO-USDT" if direction == "high_rank_selected" else "BTC-USDT"}
            family_weights = [
                row for row in weights
                if row["family_member_id"].startswith(f"{factor}.h")
                and row["family_member_id"].endswith(direction)
            ]
            assert all(sum(row["weight"] for row in family_weights if row["family_member_id"] == member) == 1.0 for member in {row["family_member_id"] for row in family_weights})


def test_factor_spec_order_is_the_frozen_six() -> None:
    assert tuple(sorted(spec.name for spec in DEFAULT_FACTOR_SPECS)) == FACTORS


def test_build_commits_marker_last_and_is_output_dir_independent(tmp_path, monkeypatch) -> None:
    timestamps = [
        (datetime(2026, 8, 2, 16, tzinfo=timezone.utc) + timedelta(hours=index)).isoformat().replace("+00:00", "Z")
        for index in range(161)
    ]
    config = {
        "policy_id": "test_policy",
        "preregistration_report": "prereg.json",
        "membership_gate_report": "gate.json",
        "market_data_extension_report": "extension.json",
        "execution_mapping_report": "mapping.json",
        "preregistration_sha256": "p",
        "mechanism_sha256": "m",
        "membership_gate_sha256": "g",
        "market_data_extension_sha256": "e",
        "execution_mapping_sha256": "x",
        "family_reporting_order": "factor_then_horizon_then_direction",
        "rank_policy": "existing_cross_sectional_average_rank",
        "top_k": 2,
        "weighting": "equal_weight",
        "insufficient_assets_policy": "all_cash",
        "cutoff_tie_policy": "all_cash",
        "signal_data_cutoff": "current_bar_only",
        "future_data_usage_prohibited": True,
        "factor_policy": "existing_fixed_six_factor_specs",
        "membership_policy": "future_only_piecewise_constant_closed_epoch",
        "signal_start": timestamps[0],
        "signal_end": timestamps[-1],
        "claims": {"pnl_computation_authorized": False, "readiness_changed": False},
    }
    for name in ("prereg.json", "gate.json", "extension.json", "mapping.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    monkeypatch.setattr(ledger_module, "_repo_root", lambda _path: tmp_path)
    monkeypatch.setattr(ledger_module, "load_ledger_config", lambda *_args: config)
    monkeypatch.setattr(ledger_module, "_pin_path", lambda *_args: None)
    monkeypatch.setattr(ledger_module, "_validate_preregistration", lambda *_args: {"preregistration_sha256": "p"})
    monkeypatch.setattr(ledger_module, "validate_prospective_membership_bar_gate", lambda *_args: SimpleNamespace(report={"gate_sha256": "g"}, eligibility=()))
    monkeypatch.setattr(ledger_module, "_validate_extension", lambda *_args: {"extension_sha256": "e"})
    monkeypatch.setattr(ledger_module, "_validate_mapping", lambda *_args: {"audit_sha256": "x"})
    monkeypatch.setattr(ledger_module, "_signal_timestamps", lambda *_args: timestamps)
    monkeypatch.setattr(ledger_module, "_load_concatenated_bars", lambda *_args: ({}, {symbol: {"baseline_sha256": "b", "append_sha256": "a"} for symbol in INST_IDS}))
    monkeypatch.setattr(ledger_module, "_factor_score_rows", lambda *_args: [{"signal_timestamp": timestamps[0], "symbol": INST_IDS[0], "factor": FACTORS[0], "score": 1.0, "finite": True, "data_cutoff_timestamp": timestamps[0]}] * 5796)
    monkeypatch.setattr(ledger_module, "_decision_rows", lambda *_args: ([{"family_member_id": "f", "factor": FACTORS[0], "horizon": 4, "rank_direction": RANK_DIRECTIONS[0], "signal_timestamp": timestamps[0], "completion_timestamp": timestamps[1], "execution_timestamp": timestamps[2], "decision_status": "invested", "decision_reason": "complete_unique_top_k", "selected_asset_1": INST_IDS[0], "selected_asset_2": INST_IDS[1], "cash_weight": 0.0, "gross_exposure": 1.0, "net_exposure": 1.0}] * 5796, [{"family_member_id": "f", "signal_timestamp": timestamps[0], "symbol": INST_IDS[0], "rank": 1.0, "selected": True, "weight": 0.5}] * 34776))
    monkeypatch.setattr(ledger_module, "_constraint_rows", lambda *_args: [{"key": "x", "value": "y"}])
    first = ledger_module.build_prospective_portfolio_ledger(*(tmp_path / name for name in ("prereg.json", "gate.json", "extension.json", "mapping.json")), tmp_path / "config.yaml", tmp_path / "reports" / "one")
    second = ledger_module.build_prospective_portfolio_ledger(*(tmp_path / name for name in ("prereg.json", "gate.json", "extension.json", "mapping.json")), tmp_path / "config.yaml", tmp_path / "reports" / "two")
    assert first.report["ledger_sha256"] == second.report["ledger_sha256"]
    assert Path(first.export_paths["report"]).read_bytes() == Path(second.export_paths["report"]).read_bytes()


def test_frozen_input_validators_and_config_are_replayed() -> None:
    repo = Path.cwd()
    config = ledger_module.load_ledger_config(repo / "config.prospective-portfolio-ledger.example.yaml", repo)
    prereg_path = repo / config["preregistration_report"]
    gate_path = repo / config["membership_gate_report"]
    extension_path = repo / config["market_data_extension_report"]
    mapping_path = repo / config["execution_mapping_report"]
    prereg = ledger_module._validate_preregistration(prereg_path, repo, config)
    gate = ledger_module.validate_prospective_membership_bar_gate(
        gate_path, repo / "config.prospective-membership-bar-gate.example.yaml"
    )
    extension = ledger_module._validate_extension(extension_path, repo, config)
    mapping = ledger_module._validate_mapping(mapping_path, config)
    assert prereg["family_size"] == 36
    assert len(ledger_module._signal_timestamps(list(gate.eligibility), config)) == 161
    constraints = ledger_module._constraint_rows(config, prereg, gate, extension, mapping)
    assert {row["key"] for row in constraints} >= {"policy_id", "pnl_computation_authorized"}
    assert "ledger_sha256:" in ledger_module.format_prospective_portfolio_ledger(
        SimpleNamespace(report={"ledger_status": "x", "ledger_sha256": "y", "signal_timestamp_count": 1, "family_count": 1, "factor_score_row_count": 1, "decision_row_count": 1, "weight_row_count": 1, "all_cash_decision_count": 0})
    )


def test_dataset_concat_and_low_level_guards(tmp_path, monkeypatch) -> None:
    repo = Path.cwd()
    frame = _bars().iloc[:1].copy()
    frame = pd.concat([frame] * 40355, ignore_index=True)
    frame["timestamp"] = pd.date_range("2026-01-01", periods=40355, freq="h", tz="UTC")
    migration = {"identity": {"datasets": [{"inst_id": symbol, "destination_repo_relative_path": f"{symbol}.csv"} for symbol in INST_IDS]}}
    monkeypatch.setattr(ledger_module, "_load_json", lambda _path: migration)
    calls = iter([frame.iloc[:40191], frame.iloc[40191:]] * 6)
    monkeypatch.setattr(ledger_module, "load_ohlcv_csv", lambda _path: next(calls))
    monkeypatch.setattr(ledger_module, "_sha256", lambda _path: "hash")
    capture = SimpleNamespace(export_paths={f"{symbol}_append_filename": str(tmp_path / f"{symbol}.append.csv") for symbol in INST_IDS})
    bars, hashes = ledger_module._load_concatenated_bars({"capture": capture}, tmp_path, {})
    assert set(bars) == set(INST_IDS)
    assert hashes["BTC-USDT"] == {"baseline_sha256": "hash", "append_sha256": "hash"}
    assert ledger_module._shift_iso("2026-08-02T16:00:00Z", 2) == "2026-08-02T18:00:00Z"
    ledger_module._pin_path(tmp_path, tmp_path / "x", "x")
    with pytest.raises(ledger_module.MarketDataError):
        ledger_module._pin_path(tmp_path, tmp_path / "y", "x")
    assert ledger_module._repo_root(repo / "src") == repo
    with pytest.raises(ValueError):
        ledger_module._reports_output(repo, tmp_path)
