from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from crypto_bot.backtest.engine import BacktestEngine, BacktestResult
from crypto_bot.config import AppConfig
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.market.archive_replay import ReplayDatasetReport, build_replay_dataset
from crypto_bot.portfolio.account import Account
from crypto_bot.regime_filter import RegimeFilter, RegimeFilterSettings
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy.factory import create_strategy


@dataclass(frozen=True)
class MarketReplayResult:
    dataset: ReplayDatasetReport
    backtest: BacktestResult
    replay_sha256: str
    export_path: Path | None


def run_market_replay(
    config: AppConfig,
    input_dir: str | Path,
    *,
    target_timeframe: str,
    output_csv: str | Path | None = None,
    export_path: str | Path | None = None,
) -> MarketReplayResult:
    symbol = config.market_data.symbols[0]
    dataset = build_replay_dataset(
        input_dir,
        exchange=config.market_data.exchange,
        symbol=symbol,
        source_timeframe=config.market_data.timeframe,
        target_timeframe=target_timeframe,
        output_csv=output_csv,
    )
    engine = BacktestEngine(
        strategy=create_strategy(config.strategy),
        risk_manager=RiskManager(RiskSettings(**config.risk.__dict__)),
        execution_engine=PaperExecutionEngine(
            config.execution.fee_rate,
            config.execution.slippage_bps,
        ),
        account=Account(config.initial_cash),
        regime_filter=RegimeFilter(
            RegimeFilterSettings(**config.regime_filter.__dict__)
        ),
    )
    backtest = engine.run(symbol, dataset.bars)
    evidence = _deterministic_evidence(
        config,
        dataset.report,
        backtest,
        target_timeframe=target_timeframe,
    )
    hash_evidence = {
        **evidence,
        "dataset": {
            "exchange": dataset.report.exchange,
            "symbol": dataset.report.symbol,
            "source_timeframe": dataset.report.source_timeframe,
            "target_timeframe": dataset.report.target_timeframe,
            "replay_bar_count": dataset.report.replay_bar_count,
            "dataset_sha256": dataset.report.dataset_sha256,
        },
    }
    replay_sha256 = hashlib.sha256(
        json.dumps(
            hash_evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    report_path = Path(export_path) if export_path is not None else None
    if report_path is not None:
        _write_json_atomically(
            report_path,
            {
                "replay_sha256": replay_sha256,
                **evidence,
            },
        )
    return MarketReplayResult(
        dataset=dataset.report,
        backtest=backtest,
        replay_sha256=replay_sha256,
        export_path=report_path,
    )


def format_market_replay_result(result: MarketReplayResult) -> str:
    metrics = result.backtest.metrics
    return "\n".join(
        [
            f"replay_sha256: {result.replay_sha256}",
            f"dataset_sha256: {result.dataset.dataset_sha256}",
            f"source_timeframe: {result.dataset.source_timeframe}",
            f"target_timeframe: {result.dataset.target_timeframe}",
            f"manifest_count: {result.dataset.manifest_count}",
            f"input_bar_count: {result.dataset.input_bar_count}",
            f"duplicate_bar_count: {result.dataset.duplicate_bar_count}",
            f"replay_bar_count: {result.dataset.replay_bar_count}",
            f"dropped_incomplete_source_bar_count: {result.dataset.dropped_incomplete_source_bar_count}",
            f"trade_count: {metrics.trade_count}",
            f"total_return_pct: {metrics.total_return_pct}",
            f"max_drawdown_pct: {metrics.max_drawdown_pct}",
        ]
    )


def _deterministic_evidence(
    config: AppConfig,
    dataset: ReplayDatasetReport,
    backtest: BacktestResult,
    *,
    target_timeframe: str,
) -> dict:
    dataset_payload = dataset.to_dict()
    dataset_payload.pop("output_csv", None)
    trades = []
    for trade in backtest.trades:
        payload = asdict(trade)
        payload.pop("source_signal_id", None)
        trades.append(payload)
    return {
        "dataset": dataset_payload,
        "configuration": {
            "symbol": config.market_data.symbols[0],
            "target_timeframe": target_timeframe,
            "initial_cash": config.initial_cash,
            "strategy": asdict(config.strategy),
            "risk": asdict(config.risk),
            "execution": asdict(config.execution),
            "regime_filter": asdict(config.regime_filter),
        },
        "metrics": asdict(backtest.metrics),
        "trades": trades,
        "equity_points": [asdict(point) for point in backtest.equity_points],
        "risk_events": [asdict(event) for event in backtest.risk_events],
    }


def _write_json_atomically(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
