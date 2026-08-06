import csv
import hashlib
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import crypto_bot.cli as cli_module
import crypto_bot.cross_sectional_portfolio_mechanism as mechanism_module
from crypto_bot.cross_sectional_portfolio_mechanism import (
    RANK_DIRECTIONS,
    audit_cross_sectional_portfolio_mechanism,
    build_long_only_target_weights,
    format_cross_sectional_portfolio_mechanism,
    load_portfolio_mechanism_config,
    validate_cross_sectional_portfolio_mechanism,
)
from crypto_bot.errors import MarketDataError
from crypto_bot.factors import DEFAULT_FACTOR_SPECS


CONFIG = Path(__file__).resolve().parents[1] / "config.cross-sectional-portfolio-mechanism.example.yaml"
SYMBOLS = ("BICO/USDT", "BTC/USDT", "ETH/USDT", "KNC/USDT", "SOL/USDT", "SWFTC/USDT")


def test_config_is_exactly_frozen(tmp_path):
    assert load_portfolio_mechanism_config(CONFIG) == mechanism_module.EXPECTED_CONFIG

    changed = mechanism_module.EXPECTED_CONFIG | {"top_k": 3}
    changed_path = tmp_path / "changed.yaml"
    changed_path.write_text(yaml.safe_dump(changed), encoding="utf-8")
    with pytest.raises(MarketDataError, match="config_not_frozen"):
        load_portfolio_mechanism_config(changed_path)

    non_mapping = tmp_path / "non-mapping.yaml"
    non_mapping.write_text("- invalid\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_portfolio_mechanism_config(non_mapping)


def test_long_only_weights_register_both_directions_without_negative_weights():
    signals = {symbol: float(index) for index, symbol in enumerate(SYMBOLS)}

    high = build_long_only_target_weights(signals, SYMBOLS, rank_direction="high_rank_selected")
    low = build_long_only_target_weights(signals, SYMBOLS, rank_direction="low_rank_selected")

    assert high.status == low.status == "invested"
    assert dict(high.weights)["SWFTC/USDT"] == 0.5
    assert dict(high.weights)["SOL/USDT"] == 0.5
    assert dict(low.weights)["BICO/USDT"] == 0.5
    assert dict(low.weights)["BTC/USDT"] == 0.5
    for result in (high, low):
        assert sum(dict(result.weights).values()) == 1.0
        assert min(dict(result.weights).values()) == 0.0
        assert result.gross_exposure == result.net_exposure == 1.0
        assert result.cash_weight == 0.0


@pytest.mark.parametrize(
    ("signals", "reason"),
    [
        ({symbol: 1.0 for symbol in SYMBOLS[:-1]}, "insufficient_assets"),
        ({symbol: (float("nan") if index == 0 else float(index)) for index, symbol in enumerate(SYMBOLS)}, "non_finite_signal"),
        (
            {
                "BICO/USDT": 6.0,
                "BTC/USDT": 5.0,
                "ETH/USDT": 5.0,
                "KNC/USDT": 3.0,
                "SOL/USDT": 2.0,
                "SWFTC/USDT": 1.0,
            },
            "ambiguous_top_k_tie",
        ),
    ],
)
def test_incomplete_non_finite_or_cutoff_tie_is_all_cash(signals, reason):
    result = build_long_only_target_weights(signals, SYMBOLS, rank_direction="high_rank_selected")

    assert result.status == "all_cash"
    assert result.reason == reason
    assert result.cash_weight == 1.0
    assert result.gross_exposure == result.net_exposure == 0.0
    assert set(dict(result.weights).values()) == {0.0}


def test_mechanism_audit_is_deterministic_blocked_and_has_36_unselected_variants(
    tmp_path,
    monkeypatch,
):
    validated = _fake_validated_chain(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mechanism_module,
        "validate_promoted_cross_sectional_oos_evidence",
        lambda _path: validated,
    )

    first = audit_cross_sectional_portfolio_mechanism("chain.json", CONFIG, tmp_path / "first")
    second = audit_cross_sectional_portfolio_mechanism("chain.json", CONFIG, tmp_path / "second")

    assert first.report == second.report
    assert first.report["variant_count"] == 36
    assert first.report["constraint_count"] == 16
    assert first.report["feasibility"] == {
        "signal_ranking_mechanism_feasible": True,
        "portfolio_weight_mechanism_feasible": True,
        "execution_price_mapping_feasible": False,
        "pnl_computation_authorized": False,
    }
    assert first.report["blockers"] == list(mechanism_module.BLOCKERS)
    assert first.report["profitability_evidence"] is False
    for name in first.export_paths:
        assert Path(first.export_paths[name]).name == Path(second.export_paths[name]).name
        assert Path(first.export_paths[name]).read_bytes() == Path(second.export_paths[name]).read_bytes()
    with Path(first.export_paths["variants"]).open(encoding="utf-8", newline="") as handle:
        variants = list(csv.DictReader(handle))
    assert len(variants) == 36
    assert {row["rank_direction"] for row in variants} == set(RANK_DIRECTIONS)
    assert {row["selection_prohibited"] for row in variants} == {"true"}
    assert {row["execution_price_mapping_feasible"] for row in variants} == {"false"}
    assert not any(key in first.report for key in ("returns", "equity", "turnover", "fees", "pnl"))
    formatted = format_cross_sectional_portfolio_mechanism(first)
    assert "execution_price_mapping_feasible: false" in formatted
    assert "pnl_computation_authorized: false" in formatted


def test_chain_drift_and_atomic_report_failure_fail_closed(tmp_path, monkeypatch):
    validated = _fake_validated_chain(tmp_path, monkeypatch)
    validated.report["oos"]["fold_row_count"] = 179
    monkeypatch.setattr(
        mechanism_module,
        "validate_promoted_cross_sectional_oos_evidence",
        lambda _path: validated,
    )
    with pytest.raises(MarketDataError, match="source_chain_mismatch"):
        audit_cross_sectional_portfolio_mechanism("chain.json", CONFIG, tmp_path / "drift")

    validated.report["oos"]["fold_row_count"] = 180
    original_commit = mechanism_module._commit_bytes
    commit_count = 0

    def fail_report(path, content):
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            raise MarketDataError("injected_report_failure")
        original_commit(path, content)

    monkeypatch.setattr(mechanism_module, "_commit_bytes", fail_report)
    output = tmp_path / "failed"
    with pytest.raises(MarketDataError, match="injected_report_failure"):
        audit_cross_sectional_portfolio_mechanism("chain.json", CONFIG, output)
    assert not list(output.glob("cross-sectional-portfolio-mechanism.*.json"))


def test_public_mechanism_validator_replays_marker_and_rejects_tampering(tmp_path, monkeypatch):
    validated = _fake_validated_chain(tmp_path, monkeypatch)
    monkeypatch.setattr(
        mechanism_module,
        "validate_promoted_cross_sectional_oos_evidence",
        lambda _path: validated,
    )
    result = audit_cross_sectional_portfolio_mechanism("chain.json", CONFIG, tmp_path / "output")
    monkeypatch.setattr(mechanism_module, "_locate_dependency", lambda *_args: validated.report_path)

    replay = validate_cross_sectional_portfolio_mechanism(result.export_paths["report"])

    assert replay.report == result.report
    assert replay.chain is validated
    Path(result.export_paths["variants"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(MarketDataError, match="artifact_hash_mismatch"):
        validate_cross_sectional_portfolio_mechanism(result.export_paths["report"])


def test_cli_has_no_mechanism_overrides(tmp_path, monkeypatch, capsys):
    result = SimpleNamespace(export_paths={"report": str(tmp_path / "report.json")})
    monkeypatch.setattr(cli_module, "audit_cross_sectional_portfolio_mechanism", lambda *_args: result)
    monkeypatch.setattr(cli_module, "format_cross_sectional_portfolio_mechanism", lambda _result: "ok")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crypto-bot",
            "audit-cross-sectional-portfolio-mechanism",
            "--evidence-chain",
            "chain.json",
            "--mechanism-config",
            str(CONFIG),
            "--output-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as exited:
        cli_module.main()

    assert exited.value.code == 0
    assert "ok" in capsys.readouterr().out


def _fake_validated_chain(tmp_path, monkeypatch):
    report_path = tmp_path / "chain.json"
    report_path.write_text("fixed-chain", encoding="utf-8")
    monkeypatch.setattr(
        mechanism_module,
        "EXPECTED_CHAIN_REPORT_SHA256",
        hashlib.sha256(report_path.read_bytes()).hexdigest(),
    )
    factors = [asdict(spec) | {"name": spec.name} for spec in sorted(DEFAULT_FACTOR_SPECS, key=lambda x: x.name)]
    datasets = [
        {
            "dataset_id": symbol.lower().replace("/", "_"),
            "symbol": symbol,
            "raw_sha256": character * 64,
            "canonical_sha256": character * 64,
        }
        for symbol, character in zip(SYMBOLS, "abcdef")
    ]
    report = {
        "chain_sha256": mechanism_module.EXPECTED_CHAIN_SHA256,
        "research": {
            "research_sha256": mechanism_module.EXPECTED_RESEARCH_SHA256,
            "report_sha256": "1" * 64,
            "factor_specs": factors,
            "horizons": [4, 16, 64],
        },
        "calibration": {
            "calibration_sha256": mechanism_module.EXPECTED_CALIBRATION_SHA256,
            "report_sha256": "2" * 64,
        },
        "oos": {
            "analysis_sha256": mechanism_module.EXPECTED_OOS_SHA256,
            "report_sha256": "3" * 64,
            "fold_row_count": 180,
            "summary_row_count": 18,
            "policies": {"minimum_oos_valid_count": 500},
        },
        "promotion": {
            "promotion_sha256": mechanism_module.EXPECTED_PROMOTION_SHA256,
            "registry": {"sha256": "4" * 64},
            "panel": {"panel_sha256": mechanism_module.EXPECTED_PANEL_SHA256},
            "datasets": datasets,
            "timestamp_semantics": {
                "generic_panel_audit_status": "unverified",
                "aggregate_status": "mixed_unverified",
                "timestamp_semantics_uniform": False,
                "components": [
                    {"dataset_id": item["dataset_id"], "status": status}
                    for item, status in zip(
                        datasets,
                        (
                            "verified_open_time",
                            "partial_unverified",
                            "unknown",
                            "verified_open_time",
                            "unknown",
                            "verified_open_time",
                        ),
                    )
                ],
            },
            "claims": {
                "historical_point_in_time_membership": False,
                "survivorship_bias_resolved": False,
            },
            "design": {
                "prior_related_results_exist": True,
                "design_frozen_before_six_asset_1h_result_generation": True,
            },
        },
    }
    return SimpleNamespace(
        report=report,
        report_path=report_path,
        promotion=SimpleNamespace(repo_root=CONFIG.parent),
    )
