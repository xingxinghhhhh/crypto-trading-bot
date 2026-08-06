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
