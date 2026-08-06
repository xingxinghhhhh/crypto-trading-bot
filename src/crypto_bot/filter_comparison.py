from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from crypto_bot.backtest.engine import BacktestEngine
from crypto_bot.backtest.export import export_backtest_reports
from crypto_bot.config import AppConfig, load_config
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.market.csv_data import load_ohlcv_csv
from crypto_bot.market.data_quality import validate_ohlcv_csv
from crypto_bot.optimization.engine import run_optimization, run_walk_forward
from crypto_bot.optimization.export import export_optimization_reports
from crypto_bot.portfolio.account import Account
from crypto_bot.regime_filter import RegimeFilter, RegimeFilterSettings
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy.factory import create_strategy
from crypto_bot.strategy_readiness import evaluate_strategy_readiness


def compare_filters(
    base_config_path: str | Path,
    filtered_config_path: str | Path,
    export_dir: str | Path,
) -> dict:
    output_dir = Path(export_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = _run_case("base", load_config(base_config_path), output_dir, stamp)
    filtered = _run_case("filtered", load_config(filtered_config_path), output_dir, stamp)
    payload = {
        "base_total_return_pct": base["backtest_summary"]["total_return_pct"],
        "filtered_total_return_pct": filtered["backtest_summary"]["total_return_pct"],
        "base_max_drawdown_pct": base["backtest_summary"]["max_drawdown_pct"],
        "filtered_max_drawdown_pct": filtered["backtest_summary"]["max_drawdown_pct"],
        "base_walk_forward_window_count": base["walk_forward_summary"]["window_count"],
        "filtered_walk_forward_window_count": filtered["walk_forward_summary"]["window_count"],
        "base_positive_test_window_count": base["walk_forward_summary"]["positive_test_window_count"],
        "filtered_positive_test_window_count": filtered["walk_forward_summary"]["positive_test_window_count"],
        "base_negative_test_window_count": base["walk_forward_summary"]["negative_test_window_count"],
        "filtered_negative_test_window_count": filtered["walk_forward_summary"]["negative_test_window_count"],
        "base_average_test_return_pct": base["walk_forward_summary"]["average_test_return_pct"],
        "filtered_average_test_return_pct": filtered["walk_forward_summary"]["average_test_return_pct"],
        "base_total_test_trade_count": base["walk_forward_summary"]["total_test_trade_count"],
        "filtered_total_test_trade_count": filtered["walk_forward_summary"]["total_test_trade_count"],
        "base_readiness_conclusion": base["readiness_conclusion"],
        "filtered_readiness_conclusion": filtered["readiness_conclusion"],
        "base_reports": {name: str(path) for name, path in base["paths"].items()},
        "filtered_reports": {name: str(path) for name, path in filtered["paths"].items()},
    }
    export_path = output_dir / f"filter_comparison_{stamp}.json"
    payload["export_path"] = str(export_path)
    export_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def format_filter_comparison(comparison: dict) -> str:
    return "\n".join(
        [
            f"base_total_return_pct: {comparison['base_total_return_pct']}",
            f"filtered_total_return_pct: {comparison['filtered_total_return_pct']}",
            f"base_max_drawdown_pct: {comparison['base_max_drawdown_pct']}",
            f"filtered_max_drawdown_pct: {comparison['filtered_max_drawdown_pct']}",
            f"base_readiness_conclusion: {comparison['base_readiness_conclusion']}",
            f"filtered_readiness_conclusion: {comparison['filtered_readiness_conclusion']}",
        ]
    )


def _run_case(prefix: str, config: AppConfig, output_dir: Path, stamp: str) -> dict:
    symbol = config.symbols[0]
    csv_path = _csv_path_for_symbol(config, symbol)
    bars = load_ohlcv_csv(csv_path)
    backtest_result = _run_backtest(config, bars, symbol)
    backtest_paths = export_backtest_reports(backtest_result, output_dir, timestamp=f"{prefix}_{stamp}")
    optimization_rows = run_optimization(config, bars=bars)
    walk_forward_rows = run_walk_forward(config, bars=bars)
    optimization_paths = export_optimization_reports(
        optimization_rows,
        walk_forward_rows,
        output_dir,
        timestamp=f"{prefix}_{stamp}",
    )
    data_quality_path = output_dir / f"data_quality_{prefix}_{stamp}.json"
    validate_ohlcv_csv(csv_path, config.timeframe, export_path=data_quality_path)
    readiness = evaluate_strategy_readiness(
        backtest_summary_path=backtest_paths["summary"],
        optimization_summary_path=optimization_paths["optimization_summary"],
        walk_forward_summary_path=optimization_paths["walk_forward_summary"],
        walk_forward_results_path=optimization_paths["walk_forward_results"],
        data_quality_path=data_quality_path,
        max_drawdown_pct=config.risk.max_drawdown_pct,
        export_path=output_dir / f"strategy_readiness_{prefix}_{stamp}.json",
    )
    return {
        "backtest_summary": _load_json(backtest_paths["summary"]),
        "walk_forward_summary": _load_json(optimization_paths["walk_forward_summary"]),
        "readiness_conclusion": readiness.conclusion,
        "paths": {
            **backtest_paths,
            **optimization_paths,
            "data_quality": data_quality_path,
        },
    }


def _run_backtest(config: AppConfig, bars, symbol: str):
    engine = BacktestEngine(
        strategy=create_strategy(config.strategy),
        risk_manager=RiskManager(RiskSettings(**config.risk.__dict__)),
        execution_engine=PaperExecutionEngine(config.execution.fee_rate, config.execution.slippage_bps),
        account=Account(config.initial_cash),
        regime_filter=RegimeFilter(RegimeFilterSettings(**config.regime_filter.__dict__)),
    )
    return engine.run(symbol, bars)


def _csv_path_for_symbol(config: AppConfig, symbol: str) -> str:
    return config.market_data.csv_files.get(symbol, config.market_data.csv_path)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))
