from __future__ import annotations

import argparse
import sys
from datetime import date

from crypto_bot.backtest.engine import BacktestEngine
from crypto_bot.backtest.export import export_backtest_reports
from crypto_bot.backtest.report import format_backtest_report
from crypto_bot.benchmark_decision import create_benchmark_decision_report, format_benchmark_decision_report
from crypto_bot.config import AppConfig, load_config
from crypto_bot.cross_sectional_ic_calibration import (
    format_cross_sectional_ic_calibration,
    run_cross_sectional_ic_calibration,
)
from crypto_bot.cross_sectional_oos_stability import (
    format_cross_sectional_oos_stability,
    run_cross_sectional_oos_stability,
)
from crypto_bot.cross_sectional_portfolio_mechanism import (
    audit_cross_sectional_portfolio_mechanism,
    format_cross_sectional_portfolio_mechanism,
)
from crypto_bot.cross_sectional_factor_research import (
    format_cross_sectional_factor_research,
    run_cross_sectional_factor_research,
)
from crypto_bot.errors import MarketDataError
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.factor_research import (
    format_factor_research_report,
    run_factor_research,
)
from crypto_bot.logging import setup_logging
from crypto_bot.market.csv_data import load_ohlcv_csv
from crypto_bot.market.csv_normalizer import normalize_ohlcv_csv
from crypto_bot.market.data_quality import format_data_quality_report, validate_ohlcv_csv
from crypto_bot.market.dataset_promotion import (
    format_dataset_promotion,
    promote_okx_universe_candidates,
)
from crypto_bot.market.dataset_panel import build_dataset_panel, format_dataset_panel_audit
from crypto_bot.market.dataset_registry import audit_dataset_registry, format_dataset_registry_audit
from crypto_bot.market.history import fetch_history_to_csv
from crypto_bot.market.legacy_timestamp_mapping_audit import (
    audit_legacy_1h_next_open_mapping,
    format_legacy_timestamp_mapping_audit,
)
from crypto_bot.market.okx_universe_intake import (
    audit_okx_universe_intake,
    capture_okx_universe_intake,
    format_okx_universe_audit,
    format_okx_universe_capture,
)
from crypto_bot.market.okx_direct_six_asset_migration import (
    capture_okx_direct_anchor_1h_history,
    format_okx_direct_anchor_1h_capture,
    format_okx_direct_six_asset_1h_migration,
    freeze_okx_direct_six_asset_1h_panel,
)
from crypto_bot.market.public_response_mutability import (
    audit_okx_public_response_mutability,
    format_public_response_mutability,
)
from crypto_bot.market.execution_cost_evidence import (
    freeze_execution_cost_evidence,
    format_execution_cost_evidence,
)
from crypto_bot.market.okx_future_universe_archive import (
    audit_okx_future_universe_transition,
    capture_okx_future_universe_snapshot,
    format_future_universe_result,
)
from crypto_bot.market.prospective_membership_bar_gate import (
    format_membership_bar_gate_result,
    freeze_prospective_membership_bar_gate,
)
from crypto_bot.market.prospective_direct_1h_extension import (
    audit_prospective_direct_1h_extension,
    capture_prospective_direct_1h_extension,
    format_prospective_direct_result,
)
from crypto_bot.market.prospective_direct_1h_segment_chain import (
    format_segment_chain_result,
    freeze_prospective_direct_1h_segment_chain,
)
from crypto_bot.market.prospective_epoch_capture_admission import (
    format_capture_admission_result,
    freeze_prospective_epoch_capture_admission,
)
from crypto_bot.market.prospective_capture_attempt_journal import (
    format_capture_attempt_journal_result,
    freeze_prospective_capture_attempt_journal,
)
from crypto_bot.market.prospective_capture_attempt_receipt_chain import (
    audit_prospective_capture_attempt_receipt_chain,
    format_receipt_chain_result,
)
from crypto_bot.market.prospective_capture_attempt_evidence_adapter import (
    format_receipt_materialization_result,
    materialize_prospective_capture_attempt_receipt,
)
from crypto_bot.market.prospective_capture_window_closeout import (
    audit_prospective_capture_window_closeout,
    format_capture_window_closeout_result,
)
from crypto_bot.prospective_epoch_closeout_rollover import (
    audit_prospective_epoch_closeout_rollover,
    format_prospective_epoch_closeout_rollover,
)
from crypto_bot.market.prospective_snapshot_transition_admission import (
    freeze_prospective_snapshot_transition_admission,
    format_prospective_snapshot_transition_admission,
)
from crypto_bot.market.prospective_snapshot_transition_materializer import (
    format_prospective_snapshot_transition_materialization,
    materialize_prospective_snapshot_transition,
)
from crypto_bot.market.prospective_membership_epoch_materializer import (
    format_prospective_membership_epoch_materialization,
    materialize_prospective_membership_epoch,
)
from crypto_bot.market.prospective_membership_epoch_closure import (
    close_prospective_membership_epoch,
    format_prospective_membership_epoch_closure,
)
from crypto_bot.market.direct_execution_mapping_audit import (
    audit_okx_direct_six_1h_execution_mapping,
    format_direct_execution_mapping_audit,
)
from crypto_bot.cross_sectional_variant_preregistration import (
    freeze_cross_sectional_variant_preregistration,
    format_variant_preregistration,
)
from crypto_bot.prospective_portfolio_ledger import (
    build_prospective_portfolio_ledger,
    format_prospective_portfolio_ledger,
)
from crypto_bot.prospective_economic_accounting_contract import (
    format_prospective_economic_accounting_contract,
    freeze_prospective_economic_accounting_contract,
)
from crypto_bot.market.spread_application_semantics import (
    format_spread_application_semantics,
    freeze_spread_application_semantics,
)
from crypto_bot.prospective_economic_cost_scenario_contract import (
    format_prospective_economic_cost_scenarios,
    freeze_prospective_economic_cost_scenarios,
)
from crypto_bot.prospective_economic_readiness_gate import (
    audit_prospective_economic_readiness,
    format_prospective_economic_readiness,
)
from crypto_bot.prospective_economic_sample_maturity_gate import (
    audit_prospective_economic_sample_maturity,
    format_prospective_economic_sample_maturity,
)
from crypto_bot.prospective_epoch_accumulation_policy import (
    format_prospective_epoch_accumulation_policy,
    freeze_prospective_epoch_accumulation_policy,
)
from crypto_bot.prospective_epoch_assembly_state_machine import (
    format_epoch_assembly_result,
    freeze_prospective_epoch_assembly_state_machine,
)
from crypto_bot.market.okx_universe_history_extension import (
    capture_okx_frozen_universe_1h_history,
    format_frozen_universe_1h_capture,
    format_frozen_universe_1h_promotion,
    promote_okx_frozen_universe_1h_panel,
)
from crypto_bot.market.recorder import run_market_recorder
from crypto_bot.market.recorder_summary import summarize_market_recordings
from crypto_bot.market.timestamp_semantics import (
    audit_timestamp_semantics,
    capture_okx_timestamp_probe,
    format_okx_timestamp_probe,
    format_timestamp_semantics_audit,
)
from crypto_bot.promoted_cross_sectional_research import (
    analyze_promoted_cross_sectional_evidence,
    format_promoted_cross_sectional_evidence,
)
from crypto_bot.promoted_cross_sectional_oos_evidence import (
    analyze_promoted_cross_sectional_oos_evidence,
    format_promoted_cross_sectional_oos_evidence,
)
from crypto_bot.paper.runner import run_paper_session
from crypto_bot.paper.shadow import run_shadow_session
from crypto_bot.paper.summary import create_daily_summary
from crypto_bot.portfolio.account import Account
from crypto_bot.regime_filter import RegimeFilter, RegimeFilterSettings
from crypto_bot.research_freeze import create_research_freeze_report, format_research_freeze_report
from crypto_bot.replay import format_market_replay_result, run_market_replay
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.storage.repositories import SQLiteStorage
from crypto_bot.strategy_readiness import (
    evaluate_latest_strategy_readiness,
    evaluate_strategy_readiness,
    format_strategy_readiness_report,
)
from crypto_bot.strategy.factory import create_strategy
from crypto_bot.optimization.engine import run_optimization, run_walk_forward
from crypto_bot.optimization.export import export_optimization_reports
from crypto_bot.filter_comparison import compare_filters, format_filter_comparison
from crypto_bot.regime_analysis import analyze_regimes, format_regime_analysis_report
from crypto_bot.strategy_benchmark import format_benchmark_result, run_strategy_benchmark
from crypto_bot.walk_forward_diagnostics import diagnose_walk_forward, format_walk_forward_diagnosis
from crypto_bot.watchlist_diagnosis import create_watchlist_diagnosis, format_watchlist_diagnosis


def main() -> None:
    parser = argparse.ArgumentParser(prog="crypto-bot")
    parser.add_argument("--log-level", default="INFO")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backtest_parser = subparsers.add_parser("backtest")
    backtest_parser.add_argument("--config", default="config.example.yaml")
    backtest_parser.add_argument("--data", default=None)
    backtest_parser.add_argument("--export-dir", default=None)

    paper_parser = subparsers.add_parser("paper")
    paper_parser.add_argument("--config", default="config.example.yaml")
    paper_parser.add_argument("--data", default=None)
    paper_parser.add_argument("--once", action="store_true")
    paper_parser.add_argument("--interval-seconds", type=float, default=None)
    paper_parser.add_argument("--max-iterations", type=int, default=None)

    shadow_parser = subparsers.add_parser("shadow")
    shadow_parser.add_argument("--config", default="config.shadow.okx.example.yaml")
    shadow_parser.add_argument("--max-iterations", type=int, default=1)
    shadow_parser.add_argument("--interval-seconds", type=float, default=0)

    summary_parser = subparsers.add_parser("daily-summary")
    summary_parser.add_argument("--config", default="config.example.yaml")
    summary_parser.add_argument("--date", default=None)

    health_parser = subparsers.add_parser("health")
    health_parser.add_argument("--config", default="config.example.yaml")

    kill_switch_parser = subparsers.add_parser("kill-switch")
    kill_switch_parser.add_argument("--config", default="config.example.yaml")
    kill_switch_parser.add_argument(
        "--action",
        choices=["status", "engage", "release"],
        default="status",
    )
    kill_switch_parser.add_argument("--reason", default="manual_operator_action")

    optimize_parser = subparsers.add_parser("optimize")
    optimize_parser.add_argument("--config", default="config.example.yaml")
    optimize_parser.add_argument("--export-dir", default="reports")

    history_parser = subparsers.add_parser("fetch-history")
    history_parser.add_argument("--config", default="config.history.example.yaml")
    history_parser.add_argument("--output", required=True)

    recorder_parser = subparsers.add_parser("record-market-data")
    recorder_parser.add_argument("--config", default="config.paper.okx.example.yaml")
    recorder_parser.add_argument("--output-dir", default="data/recorder")
    recorder_parser.add_argument("--max-iterations", type=int, default=1)
    recorder_parser.add_argument("--interval-seconds", type=float, default=0)
    recorder_parser.add_argument("--max-retries", type=int, default=3)
    recorder_parser.add_argument("--initial-backoff-seconds", type=float, default=1)
    recorder_parser.add_argument("--max-backoff-seconds", type=float, default=30)

    recorder_summary_parser = subparsers.add_parser("market-data-summary")
    recorder_summary_parser.add_argument("--input-dir", default="data/recorder")
    recorder_summary_parser.add_argument("--date", default=None)
    recorder_summary_parser.add_argument("--export", default=None)

    replay_parser = subparsers.add_parser("replay-market-data")
    replay_parser.add_argument("--config", default="config.paper.okx.example.yaml")
    replay_parser.add_argument("--input-dir", default="data/recorder")
    replay_parser.add_argument("--target-timeframe", choices=["1m", "15m", "1h", "4h"], required=True)
    replay_parser.add_argument("--output-csv", default=None)
    replay_parser.add_argument("--export", default=None)

    factor_parser = subparsers.add_parser("analyze-factors")
    factor_parser.add_argument("--config", default="config.paper.okx.example.yaml")
    factor_parser.add_argument("--input-dir", default="data/recorder")
    factor_parser.add_argument("--target-timeframe", choices=["1m", "15m", "1h", "4h"], required=True)
    factor_parser.add_argument("--output-dir", default="reports/factors")
    factor_parser.add_argument("--horizons", type=_parse_positive_int_tuple, default=(1, 4, 16))
    factor_parser.add_argument("--cost-bps", type=_parse_non_negative_float_tuple, default=(0.0, 5.0, 10.0))
    factor_parser.add_argument("--quantiles", type=int, default=5)
    factor_parser.add_argument("--rank-lookback", type=int, default=40)
    factor_parser.add_argument("--rolling-train-bars", type=int, default=60)
    factor_parser.add_argument("--rolling-test-bars", type=int, default=20)
    factor_parser.add_argument("--rolling-step-bars", type=int, default=20)
    factor_parser.add_argument("--min-observations", type=int, default=20)

    validate_parser = subparsers.add_parser("validate-data")
    validate_parser.add_argument("--csv", required=True)
    validate_parser.add_argument("--timeframe", required=True)
    validate_parser.add_argument("--export", default=None)

    registry_audit_parser = subparsers.add_parser("dataset-registry-audit")
    registry_audit_parser.add_argument("--registry", default="config.datasets.example.yaml")
    registry_audit_parser.add_argument("--export", default="reports/dataset_registry_audit.json")

    panel_audit_parser = subparsers.add_parser("dataset-panel-audit")
    panel_audit_parser.add_argument("--registry", default="config.datasets.example.yaml")
    panel_audit_parser.add_argument("--panels-config", default="config.dataset-panels.example.yaml")
    panel_audit_parser.add_argument("--panel-id", required=True)
    panel_audit_parser.add_argument("--export", default=None)

    cross_sectional_parser = subparsers.add_parser("analyze-cross-sectional-factors")
    cross_sectional_parser.add_argument("--registry", default="config.datasets.example.yaml")
    cross_sectional_parser.add_argument("--panels-config", default="config.dataset-panels.example.yaml")
    cross_sectional_parser.add_argument("--panel-id", required=True)
    cross_sectional_parser.add_argument(
        "--research-config",
        default="config.cross-sectional-factor-research.example.yaml",
    )
    cross_sectional_parser.add_argument("--output-dir", default="reports/cross-sectional-factors")

    calibration_parser = subparsers.add_parser("calibrate-cross-sectional-rank-ic")
    calibration_parser.add_argument("--source-report", required=True)
    calibration_parser.add_argument("--output-dir", default="reports/cross-sectional-ic-calibration")

    oos_stability_parser = subparsers.add_parser("analyze-cross-sectional-oos-stability")
    oos_stability_parser.add_argument("--source-report", required=True)
    oos_stability_parser.add_argument(
        "--output-dir", default="reports/cross-sectional-oos-stability"
    )

    timestamp_probe_parser = subparsers.add_parser("capture-okx-timestamp-probe")
    timestamp_probe_parser.add_argument("--inst-id", default="BTC-USDT")
    timestamp_probe_parser.add_argument("--bar", default="1m")
    timestamp_probe_parser.add_argument("--output-dir", default="reports/okx-timestamp-probe")

    timestamp_audit_parser = subparsers.add_parser("audit-timestamp-semantics")
    timestamp_audit_parser.add_argument("--registry", default="config.datasets.example.yaml")
    timestamp_audit_parser.add_argument(
        "--panels-config", default="config.dataset-panels.example.yaml"
    )
    timestamp_audit_parser.add_argument(
        "--evidence-config", default="config.timestamp-semantics.example.yaml"
    )
    timestamp_audit_parser.add_argument("--probe-report", required=True)
    timestamp_audit_parser.add_argument("--output-dir", default="reports/timestamp-semantics")

    universe_capture_parser = subparsers.add_parser("capture-okx-universe-intake")
    universe_capture_parser.add_argument("--registry", default="config.datasets.example.yaml")
    universe_capture_parser.add_argument(
        "--policy", default="config.okx-universe-intake.example.yaml"
    )
    universe_capture_parser.add_argument("--semantics-report", required=True)
    universe_capture_parser.add_argument("--output-dir", default="reports/okx-universe-capture")

    universe_audit_parser = subparsers.add_parser("audit-okx-universe-intake")
    universe_audit_parser.add_argument("--capture-report", required=True)
    universe_audit_parser.add_argument("--output-dir", default="reports/okx-universe-intake")

    promotion_parser = subparsers.add_parser("promote-okx-universe-candidates")
    promotion_parser.add_argument("--base-registry", default="config.datasets.example.yaml")
    promotion_parser.add_argument(
        "--base-panels-config", default="config.dataset-panels.example.yaml"
    )
    promotion_parser.add_argument("--semantics-report", required=True)
    promotion_parser.add_argument("--capture-report", required=True)
    promotion_parser.add_argument("--intake-report", required=True)
    promotion_parser.add_argument(
        "--promotion-config", default="config.okx-universe-promotion.example.yaml"
    )
    promotion_parser.add_argument("--output-dir", default="reports/okx-universe-promotion")

    frozen_1h_capture_parser = subparsers.add_parser("capture-okx-frozen-universe-1h-history")
    frozen_1h_capture_parser.add_argument("--source-promotion-report", required=True)
    frozen_1h_capture_parser.add_argument(
        "--policy", default="config.okx-universe-1h-extension.example.yaml"
    )
    frozen_1h_capture_parser.add_argument(
        "--output-dir", default="reports/okx-frozen-universe-1h-capture"
    )

    frozen_1h_promotion_parser = subparsers.add_parser("promote-okx-frozen-universe-1h-panel")
    frozen_1h_promotion_parser.add_argument("--capture-report", required=True)
    frozen_1h_promotion_parser.add_argument("--base-registry", default="config.datasets.example.yaml")
    frozen_1h_promotion_parser.add_argument(
        "--base-panels-config", default="config.dataset-panels.example.yaml"
    )
    frozen_1h_promotion_parser.add_argument(
        "--promotion-config", default="config.okx-universe-1h-extension.example.yaml"
    )
    frozen_1h_promotion_parser.add_argument(
        "--output-dir", default="reports/okx-frozen-universe-1h-promotion"
    )

    promoted_research_parser = subparsers.add_parser(
        "analyze-promoted-cross-sectional-evidence"
    )
    promoted_research_parser.add_argument("--promotion-report", required=True)
    promoted_research_parser.add_argument(
        "--research-config",
        default="config.cross-sectional-factor-research.example.yaml",
    )
    promoted_research_parser.add_argument(
        "--output-dir", default="reports/promoted-cross-sectional-evidence"
    )

    promoted_oos_parser = subparsers.add_parser(
        "analyze-promoted-cross-sectional-oos-evidence"
    )
    promoted_oos_parser.add_argument("--promotion-report", required=True)
    promoted_oos_parser.add_argument(
        "--research-config",
        default="config.promoted-cross-sectional-1h-oos.example.yaml",
    )
    promoted_oos_parser.add_argument(
        "--output-dir", default="reports/promoted-cross-sectional-1h-oos-evidence"
    )

    portfolio_mechanism_parser = subparsers.add_parser(
        "audit-cross-sectional-portfolio-mechanism"
    )
    portfolio_mechanism_parser.add_argument("--evidence-chain", required=True)
    portfolio_mechanism_parser.add_argument(
        "--mechanism-config",
        default="config.cross-sectional-portfolio-mechanism.example.yaml",
    )
    portfolio_mechanism_parser.add_argument(
        "--output-dir", default="reports/cross-sectional-portfolio-mechanism"
    )

    legacy_mapping_parser = subparsers.add_parser("audit-legacy-1h-next-open-mapping")
    legacy_mapping_parser.add_argument("--mechanism-report", required=True)
    legacy_mapping_parser.add_argument(
        "--evidence-config",
        default="config.legacy-1h-timestamp-mapping.example.yaml",
    )
    legacy_mapping_parser.add_argument(
        "--output-dir", default="reports/legacy-1h-next-open-mapping"
    )

    direct_anchor_parser = subparsers.add_parser("capture-okx-direct-anchor-1h-history")
    direct_anchor_parser.add_argument("--source-promotion-report", required=True)
    direct_anchor_parser.add_argument(
        "--migration-config",
        default="config.okx-direct-six-1h-migration.example.yaml",
    )
    direct_anchor_parser.add_argument(
        "--output-dir", default="reports/okx-direct-anchor-1h-capture"
    )

    direct_migration_parser = subparsers.add_parser(
        "freeze-okx-direct-six-asset-1h-panel"
    )
    direct_migration_parser.add_argument("--capture-report", required=True)
    direct_migration_parser.add_argument("--source-promotion-report", required=True)
    direct_migration_parser.add_argument(
        "--migration-config",
        default="config.okx-direct-six-1h-migration.example.yaml",
    )
    direct_migration_parser.add_argument(
        "--output-dir", default="reports/okx-direct-six-asset-1h-migration"
    )

    mutability_parser = subparsers.add_parser(
        "audit-okx-public-response-mutability"
    )
    mutability_parser.add_argument("--baseline-capture", required=True)
    mutability_parser.add_argument("--comparison-capture", required=True)
    mutability_parser.add_argument(
        "--policy", default="config.public-response-mutability.example.yaml"
    )
    mutability_parser.add_argument(
        "--output-dir", default="reports/okx-public-response-mutability"
    )

    execution_mapping_parser = subparsers.add_parser(
        "audit-okx-direct-six-1h-execution-mapping"
    )
    execution_mapping_parser.add_argument("--migration-report", required=True)
    execution_mapping_parser.add_argument("--mutability-report", required=True)
    execution_mapping_parser.add_argument(
        "--policy", default="config.okx-direct-six-1h-execution-mapping.example.yaml"
    )
    execution_mapping_parser.add_argument(
        "--output-dir", default="reports/okx-direct-six-1h-execution-mapping"
    )

    variant_preregistration_parser = subparsers.add_parser(
        "freeze-cross-sectional-variant-preregistration"
    )
    variant_preregistration_parser.add_argument("--mechanism-report", required=True)
    variant_preregistration_parser.add_argument("--execution-mapping-report", required=True)
    variant_preregistration_parser.add_argument(
        "--config", default="config.cross-sectional-variant-preregistration.example.yaml"
    )
    variant_preregistration_parser.add_argument(
        "--output-dir", default="reports/cross-sectional-variant-preregistration"
    )

    execution_cost_parser = subparsers.add_parser("freeze-execution-cost-evidence")
    execution_cost_parser.add_argument("--preregistration-report", required=True)
    execution_cost_parser.add_argument("--execution-mapping-report", required=True)
    execution_cost_parser.add_argument(
        "--evidence-config", default="config.execution-cost-evidence.example.yaml"
    )
    execution_cost_parser.add_argument(
        "--output-dir", default="reports/execution-cost-evidence"
    )

    future_snapshot_parser = subparsers.add_parser(
        "capture-okx-future-universe-snapshot"
    )
    future_snapshot_parser.add_argument("--baseline-universe-report", required=True)
    future_snapshot_parser.add_argument(
        "--archive-config", default="config.okx-future-universe-archive.example.yaml"
    )
    future_snapshot_parser.add_argument(
        "--output-dir", default="reports/okx-future-universe-snapshot"
    )

    future_transition_parser = subparsers.add_parser(
        "audit-okx-future-universe-transition"
    )
    future_transition_parser.add_argument("--previous-snapshot", required=True)
    future_transition_parser.add_argument("--current-snapshot", required=True)
    future_transition_parser.add_argument(
        "--archive-config", default="config.okx-future-universe-archive.example.yaml"
    )
    future_transition_parser.add_argument(
        "--output-dir", default="reports/okx-future-universe-transition"
    )

    membership_gate_parser = subparsers.add_parser(
        "freeze-prospective-membership-bar-gate"
    )
    membership_gate_parser.add_argument("--transition-report", required=True)
    membership_gate_parser.add_argument("--execution-mapping-report", required=True)
    membership_gate_parser.add_argument(
        "--policy", default="config.prospective-membership-bar-gate.example.yaml"
    )
    membership_gate_parser.add_argument(
        "--output-dir", default="reports/prospective-membership-bar-gate"
    )

    prospective_direct_capture_parser = subparsers.add_parser(
        "capture-okx-prospective-direct-1h-extension"
    )
    prospective_direct_capture_parser.add_argument("--membership-gate", required=True)
    prospective_direct_capture_parser.add_argument(
        "--config", default="config.okx-prospective-direct-1h-extension.example.yaml"
    )
    prospective_direct_capture_parser.add_argument(
        "--output-dir", default="reports/prospective-direct-1h-capture"
    )

    prospective_direct_audit_parser = subparsers.add_parser(
        "audit-okx-prospective-direct-1h-extension"
    )
    prospective_direct_audit_parser.add_argument("--capture-report", required=True)
    prospective_direct_audit_parser.add_argument("--membership-gate", required=True)
    prospective_direct_audit_parser.add_argument(
        "--config", default="config.okx-prospective-direct-1h-extension.example.yaml"
    )
    prospective_direct_audit_parser.add_argument(
        "--output-dir", default="reports/prospective-direct-1h-extension"
    )

    portfolio_ledger_parser = subparsers.add_parser(
        "build-prospective-portfolio-ledger"
    )
    portfolio_ledger_parser.add_argument("--preregistration-report", required=True)
    portfolio_ledger_parser.add_argument("--membership-gate", required=True)
    portfolio_ledger_parser.add_argument("--market-data-extension", required=True)
    portfolio_ledger_parser.add_argument("--execution-mapping-report", required=True)
    portfolio_ledger_parser.add_argument(
        "--config", default="config.prospective-portfolio-ledger.example.yaml"
    )
    portfolio_ledger_parser.add_argument(
        "--output-dir", default="reports/prospective-portfolio-ledger"
    )

    accounting_parser = subparsers.add_parser(
        "freeze-prospective-economic-accounting-contract"
    )
    accounting_parser.add_argument("--portfolio-ledger", required=True)
    accounting_parser.add_argument("--cost-evidence", required=True)
    accounting_parser.add_argument(
        "--config", default="config.prospective-economic-accounting.example.yaml"
    )
    accounting_parser.add_argument(
        "--output-dir", default="reports/prospective-economic-accounting"
    )

    spread_semantics_parser = subparsers.add_parser(
        "freeze-spread-application-semantics"
    )
    spread_semantics_parser.add_argument("--accounting-contract", required=True)
    spread_semantics_parser.add_argument("--cost-evidence", required=True)
    spread_semantics_parser.add_argument(
        "--config", default="config.spread-application-semantics.example.yaml"
    )
    spread_semantics_parser.add_argument(
        "--output-dir", default="reports/spread-application-semantics"
    )

    economic_scenario_parser = subparsers.add_parser(
        "freeze-prospective-economic-cost-scenarios"
    )
    economic_scenario_parser.add_argument("--accounting-contract", required=True)
    economic_scenario_parser.add_argument("--cost-evidence", required=True)
    economic_scenario_parser.add_argument("--spread-semantics", required=True)
    economic_scenario_parser.add_argument(
        "--config", default="config.prospective-economic-cost-scenarios.example.yaml"
    )
    economic_scenario_parser.add_argument(
        "--output-dir", default="reports/prospective-economic-cost-scenarios"
    )

    readiness_parser = subparsers.add_parser(
        "audit-prospective-economic-readiness"
    )
    readiness_parser.add_argument("--portfolio-ledger", required=True)
    readiness_parser.add_argument("--accounting-contract", required=True)
    readiness_parser.add_argument("--cost-scenarios", required=True)
    readiness_parser.add_argument("--market-data-extension", required=True)
    readiness_parser.add_argument("--membership-gate", required=True)
    readiness_parser.add_argument("--execution-mapping", required=True)
    readiness_parser.add_argument(
        "--config", default="config.prospective-economic-readiness-gate.example.yaml"
    )
    readiness_parser.add_argument(
        "--output-dir", default="reports/prospective-economic-readiness"
    )

    sample_maturity_parser = subparsers.add_parser(
        "audit-prospective-economic-sample-maturity"
    )
    sample_maturity_parser.add_argument(
        "--readiness-report", action="append", required=True
    )
    sample_maturity_parser.add_argument(
        "--config", default="config.prospective-economic-sample-maturity.example.yaml"
    )
    sample_maturity_parser.add_argument(
        "--output-dir", default="reports/prospective-economic-sample-maturity"
    )

    accumulation_parser = subparsers.add_parser(
        "freeze-prospective-epoch-accumulation-policy"
    )
    accumulation_parser.add_argument("--sample-maturity", required=True)
    accumulation_parser.add_argument("--latest-readiness", required=True)
    accumulation_parser.add_argument(
        "--config", default="config.prospective-epoch-accumulation-policy.example.yaml"
    )
    accumulation_parser.add_argument(
        "--output-dir", default="reports/prospective-epoch-accumulation-policy"
    )

    segment_chain_parser = subparsers.add_parser(
        "freeze-prospective-direct-1h-segment-chain"
    )
    segment_chain_parser.add_argument("--accumulation-policy", required=True)
    segment_chain_parser.add_argument("--sample-maturity", required=True)
    segment_chain_parser.add_argument("--latest-readiness", required=True)
    segment_chain_parser.add_argument("--extension-report", action="append", required=True)
    segment_chain_parser.add_argument(
        "--config", default="config.prospective-direct-1h-segment-chain.example.yaml"
    )
    segment_chain_parser.add_argument(
        "--output-dir", default="reports/prospective-direct-1h-segment-chain"
    )

    assembly_parser = subparsers.add_parser(
        "freeze-prospective-epoch-assembly-state-machine"
    )
    assembly_parser.add_argument("--accumulation-policy", required=True)
    assembly_parser.add_argument("--sample-maturity", required=True)
    assembly_parser.add_argument("--latest-readiness", required=True)
    assembly_parser.add_argument("--segment-chain", required=True)
    assembly_parser.add_argument(
        "--config", default="config.prospective-epoch-assembly.example.yaml"
    )
    assembly_parser.add_argument(
        "--output-dir", default="reports/prospective-epoch-assembly"
    )

    capture_admission_parser = subparsers.add_parser(
        "freeze-prospective-epoch-capture-admission"
    )
    capture_admission_parser.add_argument("--assembly-report", required=True)
    capture_admission_parser.add_argument(
        "--config", default="config.prospective-epoch-capture-admission.example.yaml"
    )
    capture_admission_parser.add_argument(
        "--output-dir", default="reports/prospective-epoch-capture-admission"
    )

    capture_attempt_journal_parser = subparsers.add_parser(
        "freeze-prospective-capture-attempt-journal"
    )
    capture_attempt_journal_parser.add_argument("--admission-ticket", required=True)
    capture_attempt_journal_parser.add_argument(
        "--config", default="config.prospective-capture-attempt-journal.example.yaml"
    )
    capture_attempt_journal_parser.add_argument(
        "--output-dir", default="reports/prospective-capture-attempt-journal"
    )

    capture_receipt_chain_parser = subparsers.add_parser(
        "audit-prospective-capture-attempt-receipt-chain"
    )
    capture_receipt_chain_parser.add_argument("--journal-contract", required=True)
    capture_receipt_chain_parser.add_argument("--receipt", action="append", default=[])
    capture_receipt_chain_parser.add_argument(
        "--config", default="config.prospective-epoch-capture-attempt-receipt-chain.example.yaml"
    )
    capture_receipt_chain_parser.add_argument(
        "--output-dir", default="reports/prospective-capture-attempt-receipt-chain"
    )

    capture_evidence_parser = subparsers.add_parser(
        "materialize-prospective-capture-attempt-receipt"
    )
    capture_evidence_parser.add_argument("--receipt-chain", required=True)
    capture_evidence_parser.add_argument("--attempt-evidence", required=True)
    capture_evidence_parser.add_argument(
        "--config", default="config.prospective-capture-attempt-evidence-adapter.example.yaml"
    )
    capture_evidence_parser.add_argument(
        "--output-dir", default="reports/prospective-capture-attempt-evidence-adapter"
    )

    capture_window_closeout_parser = subparsers.add_parser(
        "audit-prospective-capture-window-closeout"
    )
    capture_window_closeout_parser.add_argument("--receipt-chain", required=True)
    capture_window_closeout_parser.add_argument("--admission-ticket", required=True)
    capture_window_closeout_parser.add_argument("--closeout-evidence", required=True)
    capture_window_closeout_parser.add_argument(
        "--config", default="config.prospective-capture-window-closeout.example.yaml"
    )
    capture_window_closeout_parser.add_argument(
        "--output-dir", default="reports/prospective-capture-window-closeout"
    )

    closeout_rollover_parser = subparsers.add_parser(
        "audit-prospective-epoch-closeout-rollover"
    )
    closeout_rollover_parser.add_argument("--closeout-report", required=True)
    closeout_rollover_parser.add_argument("--assembly-report", required=True)
    closeout_rollover_parser.add_argument("--accumulation-policy", required=True)
    closeout_rollover_parser.add_argument("--sample-maturity", required=True)
    closeout_rollover_parser.add_argument(
        "--config", default="config.prospective-epoch-closeout-rollover.example.yaml"
    )
    closeout_rollover_parser.add_argument(
        "--output-dir", default="reports/prospective-epoch-closeout-rollover"
    )

    snapshot_admission_parser = subparsers.add_parser(
        "freeze-prospective-snapshot-transition-admission"
    )
    snapshot_admission_parser.add_argument("--rollover-report", required=True)
    snapshot_admission_parser.add_argument("--closeout-report", required=True)
    snapshot_admission_parser.add_argument(
        "--config", default="config.prospective-snapshot-transition-admission.example.yaml"
    )
    snapshot_admission_parser.add_argument(
        "--output-dir", default="reports/prospective-snapshot-transition-admission"
    )

    snapshot_materializer_parser = subparsers.add_parser(
        "materialize-prospective-snapshot-transition"
    )
    snapshot_materializer_parser.add_argument("--transition-admission", required=True)
    snapshot_materializer_parser.add_argument(
        "--config", default="config.prospective-snapshot-transition-materializer.example.yaml"
    )
    snapshot_materializer_parser.add_argument(
        "--output-dir", default="reports/prospective-snapshot-transition"
    )

    membership_epoch_parser = subparsers.add_parser(
        "materialize-prospective-membership-epoch"
    )
    membership_epoch_parser.add_argument("--snapshot-transition", required=True)
    membership_epoch_parser.add_argument("--previous-membership-gate", required=True)
    membership_epoch_parser.add_argument("--segment-chain", required=True)
    membership_epoch_parser.add_argument(
        "--config", default="config.prospective-membership-epoch-materializer.example.yaml"
    )
    membership_epoch_parser.add_argument(
        "--output-dir", default="reports/prospective-membership-epoch"
    )

    membership_closure_parser = subparsers.add_parser(
        "close-prospective-membership-epoch"
    )
    membership_closure_parser.add_argument("--open-membership-epoch", required=True)
    membership_closure_parser.add_argument("--next-snapshot-transition", required=True)
    membership_closure_parser.add_argument(
        "--config", default="config.prospective-membership-epoch-closure.example.yaml"
    )
    membership_closure_parser.add_argument(
        "--output-dir", default="reports/prospective-membership-epoch-closure"
    )

    normalize_parser = subparsers.add_parser("normalize-csv")
    normalize_parser.add_argument("--input", required=True)
    normalize_parser.add_argument("--output", default="data/BTC_USDT_1h.csv")
    normalize_parser.add_argument("--timeframe", required=True)
    normalize_parser.add_argument("--export-report", default=None)

    readiness_parser = subparsers.add_parser("strategy-readiness")
    readiness_parser.add_argument("--reports-dir", default="reports")
    readiness_parser.add_argument("--config", default=None)
    readiness_parser.add_argument("--max-drawdown-pct", type=float, default=None)
    readiness_parser.add_argument("--backtest-summary", default=None)
    readiness_parser.add_argument("--optimization-summary", default=None)
    readiness_parser.add_argument("--walk-forward-summary", default=None)
    readiness_parser.add_argument("--walk-forward-results", default=None)
    readiness_parser.add_argument("--data-quality", default=None)
    readiness_parser.add_argument("--export", default=None)

    diagnose_parser = subparsers.add_parser("walk-forward-diagnose")
    diagnose_parser.add_argument("--walk-forward-results", required=True)
    diagnose_parser.add_argument("--equity-curve", default=None)
    diagnose_parser.add_argument("--trades", default=None)
    diagnose_parser.add_argument("--data-quality", default=None)
    diagnose_parser.add_argument("--export", default=None)

    regime_parser = subparsers.add_parser("regime-analysis")
    regime_parser.add_argument("--csv", required=True)
    regime_parser.add_argument("--walk-forward-results", required=True)
    regime_parser.add_argument("--trades", default=None)
    regime_parser.add_argument("--equity-curve", default=None)
    regime_parser.add_argument("--export", default=None)

    compare_filters_parser = subparsers.add_parser("compare-filters")
    compare_filters_parser.add_argument("--base-config", required=True)
    compare_filters_parser.add_argument("--filtered-config", required=True)
    compare_filters_parser.add_argument("--export-dir", default="reports")

    benchmark_parser = subparsers.add_parser("benchmark-strategies")
    benchmark_parser.add_argument("--config", required=True)
    benchmark_parser.add_argument("--export-dir", default="reports")

    benchmark_decision_parser = subparsers.add_parser("benchmark-decision-report")
    benchmark_decision_parser.add_argument("--benchmark-json", required=True)
    benchmark_decision_parser.add_argument("--matrix-json", required=True)
    benchmark_decision_parser.add_argument("--export", default=None)

    watchlist_parser = subparsers.add_parser("watchlist-diagnose")
    watchlist_parser.add_argument("--benchmark-json", required=True)
    watchlist_parser.add_argument("--matrix-json", required=True)
    watchlist_parser.add_argument("--decision-json", required=True)
    watchlist_parser.add_argument("--reports-dir", default="reports")
    watchlist_parser.add_argument("--export", default=None)

    freeze_parser = subparsers.add_parser("research-freeze-report")
    freeze_parser.add_argument("--benchmark-json", required=True)
    freeze_parser.add_argument("--matrix-json", required=True)
    freeze_parser.add_argument("--decision-json", required=True)
    freeze_parser.add_argument("--watchlist-json", required=True)
    freeze_parser.add_argument("--export", required=True)

    args = parser.parse_args()
    setup_logging()

    if args.command == "validate-data":
        report = validate_ohlcv_csv(args.csv, args.timeframe, export_path=args.export)
        print(format_data_quality_report(report))
        if args.export:
            print(f"exported_data_quality: {args.export}")
        raise SystemExit(0 if report.valid else 1)

    if args.command == "dataset-registry-audit":
        try:
            registry_report = audit_dataset_registry(args.registry, export_path=args.export)
        except (FileNotFoundError, ValueError) as exc:
            print(f"dataset_registry_audit_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_dataset_registry_audit(registry_report))
        if args.export:
            print(f"exported_dataset_registry_audit: {args.export}")
        raise SystemExit(0 if registry_report["valid"] else 1)

    if args.command == "dataset-panel-audit":
        export_path = args.export or f"reports/dataset_panel_audit_{args.panel_id}.json"
        try:
            panel_result = build_dataset_panel(
                args.registry,
                args.panels_config,
                args.panel_id,
                export_path=export_path,
            )
        except (FileNotFoundError, ValueError, MarketDataError) as exc:
            print(f"dataset_panel_audit_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_dataset_panel_audit(panel_result))
        print(f"exported_dataset_panel_audit: {export_path}")
        raise SystemExit(0)

    if args.command == "analyze-cross-sectional-factors":
        try:
            cross_sectional_result = run_cross_sectional_factor_research(
                args.registry,
                args.panels_config,
                args.panel_id,
                args.research_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"cross_sectional_factor_research_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_cross_sectional_factor_research(cross_sectional_result))
        for artifact_name, artifact_path in cross_sectional_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "calibrate-cross-sectional-rank-ic":
        try:
            calibration_result = run_cross_sectional_ic_calibration(
                args.source_report,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"cross_sectional_ic_calibration_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_cross_sectional_ic_calibration(calibration_result))
        for artifact_name, artifact_path in calibration_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "analyze-cross-sectional-oos-stability":
        try:
            oos_stability_result = run_cross_sectional_oos_stability(
                args.source_report,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"cross_sectional_oos_stability_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_cross_sectional_oos_stability(oos_stability_result))
        for artifact_name, artifact_path in oos_stability_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "analyze-promoted-cross-sectional-evidence":
        try:
            promoted_result = analyze_promoted_cross_sectional_evidence(
                args.promotion_report,
                args.research_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"promoted_cross_sectional_evidence_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_promoted_cross_sectional_evidence(promoted_result))
        for artifact_name, artifact_path in promoted_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "analyze-promoted-cross-sectional-oos-evidence":
        try:
            promoted_oos_result = analyze_promoted_cross_sectional_oos_evidence(
                args.promotion_report,
                args.research_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"promoted_cross_sectional_oos_evidence_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_promoted_cross_sectional_oos_evidence(promoted_oos_result))
        for artifact_name, artifact_path in promoted_oos_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-cross-sectional-portfolio-mechanism":
        try:
            mechanism_result = audit_cross_sectional_portfolio_mechanism(
                args.evidence_chain,
                args.mechanism_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"cross_sectional_portfolio_mechanism_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_cross_sectional_portfolio_mechanism(mechanism_result))
        for artifact_name, artifact_path in mechanism_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-legacy-1h-next-open-mapping":
        try:
            mapping_result = audit_legacy_1h_next_open_mapping(
                args.mechanism_report,
                args.evidence_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"legacy_1h_next_open_mapping_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_legacy_timestamp_mapping_audit(mapping_result))
        for artifact_name, artifact_path in mapping_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "capture-okx-direct-anchor-1h-history":
        try:
            direct_capture = capture_okx_direct_anchor_1h_history(
                args.source_promotion_report,
                args.migration_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_direct_anchor_1h_capture_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_okx_direct_anchor_1h_capture(direct_capture))
        for artifact_name, artifact_path in direct_capture.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-okx-direct-six-asset-1h-panel":
        try:
            direct_migration = freeze_okx_direct_six_asset_1h_panel(
                args.capture_report,
                args.source_promotion_report,
                args.migration_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_direct_six_asset_1h_migration_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_okx_direct_six_asset_1h_migration(direct_migration))
        for artifact_name, artifact_path in direct_migration.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-okx-public-response-mutability":
        try:
            mutability_result = audit_okx_public_response_mutability(
                args.baseline_capture,
                args.comparison_capture,
                args.policy,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_public_response_mutability_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_public_response_mutability(mutability_result))
        for artifact_name, artifact_path in mutability_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-okx-direct-six-1h-execution-mapping":
        try:
            execution_mapping_result = audit_okx_direct_six_1h_execution_mapping(
                args.migration_report,
                args.mutability_report,
                args.policy,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_direct_six_1h_execution_mapping_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_direct_execution_mapping_audit(execution_mapping_result))
        for artifact_name, artifact_path in execution_mapping_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-cross-sectional-variant-preregistration":
        try:
            preregistration_result = freeze_cross_sectional_variant_preregistration(
                args.mechanism_report,
                args.execution_mapping_report,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"cross_sectional_variant_preregistration_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_variant_preregistration(preregistration_result))
        for artifact_name, artifact_path in preregistration_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-execution-cost-evidence":
        try:
            execution_cost_result = freeze_execution_cost_evidence(
                args.preregistration_report,
                args.execution_mapping_report,
                args.evidence_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"execution_cost_evidence_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_execution_cost_evidence(execution_cost_result))
        for artifact_name, artifact_path in execution_cost_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "capture-okx-future-universe-snapshot":
        try:
            future_snapshot_result = capture_okx_future_universe_snapshot(
                args.baseline_universe_report,
                args.archive_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_future_universe_snapshot_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_future_universe_result(future_snapshot_result))
        for artifact_name, artifact_path in future_snapshot_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-okx-future-universe-transition":
        try:
            future_transition_result = audit_okx_future_universe_transition(
                args.previous_snapshot,
                args.current_snapshot,
                args.archive_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_future_universe_transition_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_future_universe_result(future_transition_result))
        for artifact_name, artifact_path in future_transition_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-prospective-membership-bar-gate":
        try:
            membership_gate_result = freeze_prospective_membership_bar_gate(
                args.transition_report,
                args.execution_mapping_report,
                args.policy,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_membership_bar_gate_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_membership_bar_gate_result(membership_gate_result))
        for artifact_name, artifact_path in membership_gate_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "capture-okx-prospective-direct-1h-extension":
        try:
            prospective_capture_result = capture_prospective_direct_1h_extension(
                args.membership_gate,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_direct_extension_capture_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_direct_result(prospective_capture_result))
        for artifact_name, artifact_path in prospective_capture_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-okx-prospective-direct-1h-extension":
        try:
            prospective_audit_result = audit_prospective_direct_1h_extension(
                args.capture_report,
                args.membership_gate,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_direct_extension_audit_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_direct_result(prospective_audit_result))
        for artifact_name, artifact_path in prospective_audit_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "build-prospective-portfolio-ledger":
        try:
            ledger_result = build_prospective_portfolio_ledger(
                args.preregistration_report,
                args.membership_gate,
                args.market_data_extension,
                args.execution_mapping_report,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_portfolio_ledger_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_portfolio_ledger(ledger_result))
        for artifact_name, artifact_path in ledger_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-prospective-economic-accounting-contract":
        try:
            accounting_result = freeze_prospective_economic_accounting_contract(
                args.portfolio_ledger,
                args.cost_evidence,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_economic_accounting_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_economic_accounting_contract(accounting_result))
        for artifact_name, artifact_path in accounting_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-spread-application-semantics":
        try:
            spread_semantics_result = freeze_spread_application_semantics(
                args.accounting_contract,
                args.cost_evidence,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"spread_application_semantics_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_spread_application_semantics(spread_semantics_result))
        for artifact_name, artifact_path in spread_semantics_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-prospective-economic-cost-scenarios":
        try:
            economic_scenario_result = freeze_prospective_economic_cost_scenarios(
                args.accounting_contract,
                args.cost_evidence,
                args.spread_semantics,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_economic_cost_scenarios_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_economic_cost_scenarios(economic_scenario_result))
        for artifact_name, artifact_path in economic_scenario_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-prospective-economic-readiness":
        try:
            readiness_result = audit_prospective_economic_readiness(
                args.portfolio_ledger,
                args.accounting_contract,
                args.cost_scenarios,
                args.market_data_extension,
                args.membership_gate,
                args.execution_mapping,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_economic_readiness_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_economic_readiness(readiness_result))
        for artifact_name, artifact_path in readiness_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-prospective-economic-sample-maturity":
        try:
            sample_maturity_result = audit_prospective_economic_sample_maturity(
                args.readiness_report,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_economic_sample_maturity_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_economic_sample_maturity(sample_maturity_result))
        for artifact_name, artifact_path in sample_maturity_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-prospective-epoch-accumulation-policy":
        try:
            accumulation_result = freeze_prospective_epoch_accumulation_policy(
                args.sample_maturity,
                args.latest_readiness,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_epoch_accumulation_policy_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_epoch_accumulation_policy(accumulation_result))
        for artifact_name, artifact_path in accumulation_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-prospective-direct-1h-segment-chain":
        try:
            segment_chain_result = freeze_prospective_direct_1h_segment_chain(
                args.accumulation_policy,
                args.sample_maturity,
                args.latest_readiness,
                args.extension_report,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_direct_1h_segment_chain_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_segment_chain_result(segment_chain_result))
        for artifact_name, artifact_path in segment_chain_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-prospective-epoch-assembly-state-machine":
        try:
            assembly_result = freeze_prospective_epoch_assembly_state_machine(
                args.accumulation_policy,
                args.sample_maturity,
                args.latest_readiness,
                args.segment_chain,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_epoch_assembly_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_epoch_assembly_result(assembly_result))
        for artifact_name, artifact_path in assembly_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-prospective-epoch-capture-admission":
        try:
            capture_admission_result = freeze_prospective_epoch_capture_admission(
                args.assembly_report,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_epoch_capture_admission_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_capture_admission_result(capture_admission_result))
        for artifact_name, artifact_path in capture_admission_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-prospective-capture-attempt-journal":
        try:
            capture_attempt_journal_result = freeze_prospective_capture_attempt_journal(
                args.admission_ticket,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_capture_attempt_journal_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_capture_attempt_journal_result(capture_attempt_journal_result))
        for artifact_name, artifact_path in capture_attempt_journal_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-prospective-capture-attempt-receipt-chain":
        try:
            receipt_chain_result = audit_prospective_capture_attempt_receipt_chain(
                args.journal_contract,
                args.receipt,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_capture_attempt_receipt_chain_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_receipt_chain_result(receipt_chain_result))
        for artifact_name, artifact_path in receipt_chain_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "materialize-prospective-capture-attempt-receipt":
        try:
            materialization_result = materialize_prospective_capture_attempt_receipt(
                args.receipt_chain,
                args.attempt_evidence,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_capture_attempt_receipt_materialization_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_receipt_materialization_result(materialization_result))
        for artifact_name, artifact_path in materialization_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-prospective-capture-window-closeout":
        try:
            closeout_result = audit_prospective_capture_window_closeout(
                args.receipt_chain,
                args.admission_ticket,
                args.closeout_evidence,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_capture_window_closeout_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_capture_window_closeout_result(closeout_result))
        for artifact_name, artifact_path in closeout_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-prospective-epoch-closeout-rollover":
        try:
            rollover_result = audit_prospective_epoch_closeout_rollover(
                args.closeout_report,
                args.assembly_report,
                args.accumulation_policy,
                args.sample_maturity,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_epoch_closeout_rollover_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_epoch_closeout_rollover(rollover_result))
        for artifact_name, artifact_path in rollover_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "freeze-prospective-snapshot-transition-admission":
        try:
            snapshot_admission_result = freeze_prospective_snapshot_transition_admission(
                args.rollover_report,
                args.closeout_report,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_snapshot_transition_admission_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_snapshot_transition_admission(snapshot_admission_result))
        for artifact_name, artifact_path in snapshot_admission_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "materialize-prospective-snapshot-transition":
        try:
            snapshot_materialization_result = materialize_prospective_snapshot_transition(
                args.transition_admission,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_snapshot_transition_materialization_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_snapshot_transition_materialization(snapshot_materialization_result))
        for artifact_name, artifact_path in snapshot_materialization_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "materialize-prospective-membership-epoch":
        try:
            membership_epoch_result = materialize_prospective_membership_epoch(
                args.snapshot_transition,
                args.previous_membership_gate,
                args.segment_chain,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_membership_epoch_materialization_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_membership_epoch_materialization(membership_epoch_result))
        for artifact_name, artifact_path in membership_epoch_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "close-prospective-membership-epoch":
        try:
            membership_closure_result = close_prospective_membership_epoch(
                args.open_membership_epoch,
                args.next_snapshot_transition,
                args.config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"prospective_membership_epoch_closure_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_prospective_membership_epoch_closure(membership_closure_result))
        for artifact_name, artifact_path in membership_closure_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "capture-okx-frozen-universe-1h-history":
        try:
            frozen_capture = capture_okx_frozen_universe_1h_history(
                args.source_promotion_report,
                args.policy,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_frozen_universe_1h_capture_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_frozen_universe_1h_capture(frozen_capture))
        for artifact_name, artifact_path in frozen_capture.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "promote-okx-frozen-universe-1h-panel":
        try:
            frozen_promotion = promote_okx_frozen_universe_1h_panel(
                args.capture_report,
                args.base_registry,
                args.base_panels_config,
                args.promotion_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_frozen_universe_1h_promotion_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_frozen_universe_1h_promotion(frozen_promotion))
        for artifact_name, artifact_path in frozen_promotion.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "capture-okx-timestamp-probe":
        try:
            timestamp_probe_result = capture_okx_timestamp_probe(
                args.inst_id,
                args.bar,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_timestamp_probe_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_okx_timestamp_probe(timestamp_probe_result))
        for artifact_name, artifact_path in timestamp_probe_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-timestamp-semantics":
        try:
            timestamp_audit_result = audit_timestamp_semantics(
                args.registry,
                args.panels_config,
                args.evidence_config,
                args.probe_report,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"timestamp_semantics_audit_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_timestamp_semantics_audit(timestamp_audit_result))
        for artifact_name, artifact_path in timestamp_audit_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "capture-okx-universe-intake":
        try:
            universe_capture_result = capture_okx_universe_intake(
                args.registry,
                args.policy,
                args.semantics_report,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_universe_capture_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_okx_universe_capture(universe_capture_result))
        for artifact_name, artifact_path in universe_capture_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "audit-okx-universe-intake":
        try:
            universe_audit_result = audit_okx_universe_intake(
                args.capture_report,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_universe_audit_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_okx_universe_audit(universe_audit_result))
        for artifact_name, artifact_path in universe_audit_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "promote-okx-universe-candidates":
        try:
            promotion_result = promote_okx_universe_candidates(
                args.base_registry,
                args.base_panels_config,
                args.semantics_report,
                args.capture_report,
                args.intake_report,
                args.promotion_config,
                args.output_dir,
            )
        except (FileNotFoundError, OSError, ValueError, MarketDataError) as exc:
            print(f"okx_universe_promotion_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_dataset_promotion(promotion_result))
        for artifact_name, artifact_path in promotion_result.export_paths.items():
            print(f"exported_{artifact_name}: {artifact_path}")
        raise SystemExit(0)

    if args.command == "normalize-csv":
        try:
            result = normalize_ohlcv_csv(
                args.input,
                args.output,
                timeframe=args.timeframe,
                validation_report_path=args.export_report,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"normalize_csv_failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"normalized_csv: {result.output_path}")
        print(f"input_rows: {result.input_rows}")
        print(f"output_rows: {result.output_rows}")
        print(f"dropped_duplicate_count: {result.dropped_duplicate_count}")
        print(format_data_quality_report(result.validation))
        if args.export_report:
            print(f"exported_data_quality: {args.export_report}")
        raise SystemExit(0 if result.validation.valid else 1)

    if args.command == "strategy-readiness":
        max_drawdown_pct = _readiness_drawdown_threshold(args)
        try:
            if all(
                [
                    args.backtest_summary,
                    args.optimization_summary,
                    args.walk_forward_summary,
                    args.walk_forward_results,
                    args.data_quality,
                ]
            ):
                readiness = evaluate_strategy_readiness(
                    backtest_summary_path=args.backtest_summary,
                    optimization_summary_path=args.optimization_summary,
                    walk_forward_summary_path=args.walk_forward_summary,
                    walk_forward_results_path=args.walk_forward_results,
                    data_quality_path=args.data_quality,
                    max_drawdown_pct=max_drawdown_pct,
                    export_path=args.export,
                )
            else:
                readiness = evaluate_latest_strategy_readiness(
                    reports_dir=args.reports_dir,
                    max_drawdown_pct=max_drawdown_pct,
                    export_path=args.export,
                )
        except (FileNotFoundError, ValueError) as exc:
            print(f"strategy_readiness_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_strategy_readiness_report(readiness))
        if args.export:
            print(f"exported_strategy_readiness: {args.export}")
        raise SystemExit(0)

    if args.command == "walk-forward-diagnose":
        try:
            diagnosis = diagnose_walk_forward(
                args.walk_forward_results,
                equity_curve_path=args.equity_curve,
                trades_path=args.trades,
                data_quality_path=args.data_quality,
                export_path=args.export,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"walk_forward_diagnose_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_walk_forward_diagnosis(diagnosis))
        if args.export:
            print(f"exported_walk_forward_diagnosis: {args.export}")
        raise SystemExit(0)

    if args.command == "regime-analysis":
        try:
            report = analyze_regimes(
                csv_path=args.csv,
                walk_forward_results_path=args.walk_forward_results,
                trades_path=args.trades,
                equity_curve_path=args.equity_curve,
                export_path=args.export,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"regime_analysis_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_regime_analysis_report(report))
        if args.export:
            print(f"exported_regime_analysis: {args.export}")
        raise SystemExit(0)

    if args.command == "compare-filters":
        try:
            comparison = compare_filters(args.base_config, args.filtered_config, args.export_dir)
        except (FileNotFoundError, ValueError) as exc:
            print(f"compare_filters_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_filter_comparison(comparison))
        print(f"exported_filter_comparison: {comparison['export_path']}")
        raise SystemExit(0)

    if args.command == "benchmark-strategies":
        try:
            result = run_strategy_benchmark(args.config, args.export_dir)
        except (FileNotFoundError, ValueError) as exc:
            print(f"benchmark_strategies_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_benchmark_result(result))
        raise SystemExit(0)

    if args.command == "benchmark-decision-report":
        try:
            report = create_benchmark_decision_report(args.benchmark_json, args.matrix_json, export_path=args.export)
        except (FileNotFoundError, ValueError) as exc:
            print(f"benchmark_decision_report_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_benchmark_decision_report(report))
        if args.export:
            print(f"exported_benchmark_decision_report: {args.export}")
        raise SystemExit(0)

    if args.command == "watchlist-diagnose":
        try:
            report = create_watchlist_diagnosis(
                args.benchmark_json,
                args.matrix_json,
                args.decision_json,
                args.reports_dir,
                export_path=args.export,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"watchlist_diagnose_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_watchlist_diagnosis(report))
        if args.export:
            print(f"exported_watchlist_diagnosis: {args.export}")
        raise SystemExit(0)

    if args.command == "research-freeze-report":
        try:
            report = create_research_freeze_report(
                args.benchmark_json,
                args.matrix_json,
                args.decision_json,
                args.watchlist_json,
                export_path=args.export,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"research_freeze_report_failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(format_research_freeze_report(report))
        print(f"exported_research_freeze_markdown: {report['export_paths']['markdown']}")
        print(f"exported_research_freeze_json: {report['export_paths']['json']}")
        raise SystemExit(0)

    if args.command == "market-data-summary":
        summary_day = date.fromisoformat(args.date) if args.date else date.today()
        report = summarize_market_recordings(
            args.input_dir,
            summary_day,
            export_path=args.export,
        )
        print(report)
        raise SystemExit(0 if report["healthy"] else 1)

    config = load_config(args.config)
    if getattr(args, "data", None):
        config = _with_data_path(config, args.data)

    if args.command == "backtest":
        result = _run_backtest(config)
        if args.export_dir:
            paths = export_backtest_reports(result, args.export_dir)
            for name, path in paths.items():
                print(f"exported_{name}: {path}")
        print(format_backtest_report(result))
    elif args.command == "paper":
        max_iterations = 1 if args.once or args.max_iterations is None else args.max_iterations
        result = run_paper_session(
            config,
            max_iterations=max_iterations,
            interval_seconds=args.interval_seconds,
        )
        print(format_backtest_report(result))
    elif args.command == "shadow":
        shadow_result = run_shadow_session(
            config,
            max_iterations=args.max_iterations,
            interval_seconds=args.interval_seconds,
        )
        print(shadow_result)
        if shadow_result.errors and not shadow_result.observations:
            raise SystemExit(1)
    elif args.command == "daily-summary":
        day = date.fromisoformat(args.date) if args.date else date.today()
        print(create_daily_summary(config, day))
    elif args.command == "kill-switch":
        storage = SQLiteStorage(config.storage.url)
        try:
            if args.action == "engage":
                status = storage.set_kill_switch(True, args.reason)
            elif args.action == "release":
                status = storage.set_kill_switch(False, args.reason)
            else:
                status = storage.kill_switch_status()
        finally:
            storage.close()
        print(status)
    elif args.command == "health":
        storage = SQLiteStorage(config.storage.url)
        try:
            snapshot = storage.health_snapshot()
        finally:
            storage.close()
        print(snapshot)
    elif args.command == "optimize":
        optimization_rows = run_optimization(config)
        walk_forward_rows = run_walk_forward(config)
        paths = export_optimization_reports(optimization_rows, walk_forward_rows, args.export_dir)
        for name, path in paths.items():
            print(f"exported_{name}: {path}")
        print(f"optimization_result_count: {len(optimization_rows)}")
        print(f"walk_forward_window_count: {len(walk_forward_rows)}")
    elif args.command == "fetch-history":
        try:
            result = fetch_history_to_csv(config, args.output)
        except MarketDataError as exc:
            print(f"fetch_history_failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"history_csv: {result.output_path}")
        print(f"fetched_rows: {result.fetched_rows}")
        print(f"written_rows: {result.written_rows}")
    elif args.command == "replay-market-data":
        try:
            replay_result = run_market_replay(
                config,
                args.input_dir,
                target_timeframe=args.target_timeframe,
                output_csv=args.output_csv,
                export_path=args.export,
            )
        except MarketDataError as exc:
            print(f"replay_market_data_failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(format_market_replay_result(replay_result))
        if replay_result.dataset.output_csv:
            print(f"replay_csv: {replay_result.dataset.output_csv}")
        if replay_result.export_path:
            print(f"replay_report: {replay_result.export_path}")
    elif args.command == "analyze-factors":
        try:
            factor_report = run_factor_research(
                config,
                args.input_dir,
                target_timeframe=args.target_timeframe,
                output_dir=args.output_dir,
                horizons=args.horizons,
                cost_bps=args.cost_bps,
                quantiles=args.quantiles,
                rank_lookback=args.rank_lookback,
                rolling_train_bars=args.rolling_train_bars,
                rolling_test_bars=args.rolling_test_bars,
                rolling_step_bars=args.rolling_step_bars,
                min_observations=args.min_observations,
            )
        except MarketDataError as exc:
            print(f"factor_research_failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(format_factor_research_report(factor_report))
        for name, path in factor_report["export_paths"].items():
            print(f"factor_research_{name}: {path}")
    elif args.command == "record-market-data":
        recorder_result = run_market_recorder(
            config,
            args.output_dir,
            max_iterations=args.max_iterations,
            interval_seconds=args.interval_seconds,
            max_retries=args.max_retries,
            initial_backoff_seconds=args.initial_backoff_seconds,
            max_backoff_seconds=args.max_backoff_seconds,
        )
        for recording in recorder_result.recordings:
            print(f"raw_market_data: {recording.raw_path}")
            print(f"closed_market_data: {recording.normalized_path}")
            print(f"market_data_manifest: {recording.manifest_path}")
        for error in recorder_result.errors:
            print(f"record_market_data_failed: {error}", file=sys.stderr)
        print(f"record_market_data_attempt_count: {recorder_result.attempt_count}")
        print(f"record_market_data_retry_count: {recorder_result.retry_count}")
        if recorder_result.errors and not recorder_result.recordings:
            raise SystemExit(1)


def _run_backtest(config: AppConfig):
    symbol = config.symbols[0]
    bars = load_ohlcv_csv(_csv_path_for_symbol(config, symbol))
    engine = BacktestEngine(
        strategy=_strategy_from_config(config),
        risk_manager=RiskManager(RiskSettings(**config.risk.__dict__)),
        execution_engine=PaperExecutionEngine(config.execution.fee_rate, config.execution.slippage_bps),
        account=Account(config.initial_cash),
        regime_filter=RegimeFilter(RegimeFilterSettings(**config.regime_filter.__dict__)),
    )
    return engine.run(symbol, bars)


def _strategy_from_config(config: AppConfig):
    return create_strategy(config.strategy)


def _csv_path_for_symbol(config: AppConfig, symbol: str) -> str:
    return config.market_data.csv_files.get(symbol, config.market_data.csv_path)


def _with_data_path(config: AppConfig, data_path: str) -> AppConfig:
    return AppConfig(
        mode=config.mode,
        exchange=config.exchange,
        symbols=config.symbols,
        timeframe=config.timeframe,
        initial_cash=config.initial_cash,
        quote_currency=config.quote_currency,
        live_trading=config.live_trading,
        market_data=type(config.market_data)(
            source="csv",
            csv_path=data_path,
            csv_files={},
            exchange=config.market_data.exchange,
            symbols=config.market_data.symbols,
            timeframe=config.market_data.timeframe,
            since=config.market_data.since,
            until=config.market_data.until,
            limit=config.market_data.limit,
            use_environment_proxy=config.market_data.use_environment_proxy,
            require_closed_bars=config.market_data.require_closed_bars,
            max_staleness_seconds=config.market_data.max_staleness_seconds,
            max_clock_skew_seconds=config.market_data.max_clock_skew_seconds,
        ),
        strategy=config.strategy,
        risk=config.risk,
        execution=config.execution,
        storage=config.storage,
        regime_filter=config.regime_filter,
        optimization=config.optimization,
    )


def _readiness_drawdown_threshold(args) -> float:
    if args.max_drawdown_pct is not None:
        return args.max_drawdown_pct
    if args.config:
        config = load_config(args.config)
        return config.risk.max_drawdown_pct
    return 10.0


def _parse_positive_int_tuple(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated positive integers") from exc
    if not parsed or any(item <= 0 for item in parsed):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return parsed


def _parse_non_negative_float_tuple(value: str) -> tuple[float, ...]:
    try:
        parsed = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated non-negative numbers") from exc
    if not parsed or any(item < 0 for item in parsed):
        raise argparse.ArgumentTypeError("expected comma-separated non-negative numbers")
    return parsed


if __name__ == "__main__":
    main()
