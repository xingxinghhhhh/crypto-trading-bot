from __future__ import annotations

import argparse
import sys
from datetime import date

from crypto_bot.backtest.engine import BacktestEngine
from crypto_bot.backtest.export import export_backtest_reports
from crypto_bot.backtest.report import format_backtest_report
from crypto_bot.benchmark_decision import create_benchmark_decision_report, format_benchmark_decision_report
from crypto_bot.config import AppConfig, load_config
from crypto_bot.errors import MarketDataError
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.logging import setup_logging
from crypto_bot.market.csv_data import load_ohlcv_csv
from crypto_bot.market.csv_normalizer import normalize_ohlcv_csv
from crypto_bot.market.data_quality import format_data_quality_report, validate_ohlcv_csv
from crypto_bot.market.history import fetch_history_to_csv
from crypto_bot.paper.runner import run_paper_session
from crypto_bot.paper.summary import create_daily_summary
from crypto_bot.portfolio.account import Account
from crypto_bot.regime_filter import RegimeFilter, RegimeFilterSettings
from crypto_bot.research_freeze import create_research_freeze_report, format_research_freeze_report
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy_readiness import (
    evaluate_latest_strategy_readiness,
    evaluate_strategy_readiness,
    format_strategy_readiness_report,
)
from crypto_bot.strategy.bollinger_mean_reversion import BollingerMeanReversionStrategy
from crypto_bot.strategy.donchian_breakout import DonchianBreakoutStrategy
from crypto_bot.strategy.ema_pullback import EmaPullbackStrategy
from crypto_bot.strategy.moving_average_cross import MovingAverageCrossStrategy
from crypto_bot.strategy.rsi_mean_reversion import RsiMeanReversionStrategy
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

    summary_parser = subparsers.add_parser("daily-summary")
    summary_parser.add_argument("--config", default="config.example.yaml")
    summary_parser.add_argument("--date", default=None)

    optimize_parser = subparsers.add_parser("optimize")
    optimize_parser.add_argument("--config", default="config.example.yaml")
    optimize_parser.add_argument("--export-dir", default="reports")

    history_parser = subparsers.add_parser("fetch-history")
    history_parser.add_argument("--config", default="config.history.example.yaml")
    history_parser.add_argument("--output", required=True)

    validate_parser = subparsers.add_parser("validate-data")
    validate_parser.add_argument("--csv", required=True)
    validate_parser.add_argument("--timeframe", required=True)
    validate_parser.add_argument("--export", default=None)

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
    elif args.command == "daily-summary":
        day = date.fromisoformat(args.date) if args.date else date.today()
        print(create_daily_summary(config, day))
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
    if config.strategy.name == "donchian_breakout":
        return DonchianBreakoutStrategy(
            entry_window=config.strategy.entry_window,
            exit_window=config.strategy.exit_window,
            atr_window=config.strategy.atr_window,
            atr_multiplier=config.strategy.atr_multiplier,
        )
    if config.strategy.name == "rsi_mean_reversion":
        return RsiMeanReversionStrategy(
            rsi_window=config.strategy.rsi_window,
            buy_threshold=config.strategy.buy_threshold,
            sell_threshold=config.strategy.sell_threshold,
        )
    if config.strategy.name == "bollinger_mean_reversion":
        return BollingerMeanReversionStrategy(
            window=config.strategy.window,
            num_std=config.strategy.num_std,
        )
    if config.strategy.name == "ema_pullback":
        return EmaPullbackStrategy(
            trend_ema_window=config.strategy.trend_ema_window,
            pullback_ema_window=config.strategy.pullback_ema_window,
        )
    return MovingAverageCrossStrategy(config.strategy.fast_window, config.strategy.slow_window)


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


if __name__ == "__main__":
    main()
