from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from crypto_bot.backtest.engine import BacktestEngine, BacktestResult
from crypto_bot.backtest.metrics import BacktestMetrics
from crypto_bot.config import AppConfig
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.market.csv_data import load_ohlcv_csv
from crypto_bot.portfolio.account import Account
from crypto_bot.regime_filter import RegimeFilter, RegimeFilterSettings
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy.factory import create_strategy_from_params


@dataclass(frozen=True)
class OptimizationScore:
    score: float
    status: str


@dataclass(frozen=True)
class OptimizationRow:
    strategy_name: str
    fast_window: int
    slow_window: int
    entry_window: int | None
    exit_window: int | None
    atr_window: int | None
    atr_multiplier: float | None
    rsi_window: int | None
    buy_threshold: float | None
    sell_threshold: float | None
    window: int | None
    num_std: float | None
    trend_ema_window: int | None
    pullback_ema_window: int | None
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float | None
    profit_factor_note: str
    trade_count: int
    rejected_order_count: int
    filter_reject_count: int
    filter_enabled: bool
    final_equity: float
    score: float
    status: str


@dataclass(frozen=True)
class WalkForwardWindow:
    window_id: int
    train: pd.DataFrame
    test: pd.DataFrame


@dataclass(frozen=True)
class WalkForwardRow:
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    selected_fast_window: int
    selected_slow_window: int
    selected_entry_window: int | None
    selected_exit_window: int | None
    selected_atr_window: int | None
    selected_atr_multiplier: float | None
    selected_rsi_window: int | None
    selected_buy_threshold: float | None
    selected_sell_threshold: float | None
    selected_window: int | None
    selected_num_std: float | None
    selected_trend_ema_window: int | None
    selected_pullback_ema_window: int | None
    train_score: float
    train_total_return_pct: float
    train_max_drawdown_pct: float
    test_total_return_pct: float
    test_max_drawdown_pct: float
    test_trade_count: int
    test_profit_factor: float | None
    test_profit_factor_note: str
    filter_reject_count: int = 0
    filter_enabled: bool = False


def generate_parameter_grid(fast_windows: list[int], slow_windows: list[int]) -> list[tuple[int, int]]:
    return [(fast, slow) for fast in fast_windows for slow in slow_windows if fast < slow]


def generate_strategy_parameter_grid(
    strategy_name: str,
    *,
    fast_windows: list[int] | None = None,
    slow_windows: list[int] | None = None,
    entry_windows: list[int] | None = None,
    exit_windows: list[int] | None = None,
    atr_windows: list[int] | None = None,
    atr_multipliers: list[float] | None = None,
    rsi_windows: list[int] | None = None,
    buy_thresholds: list[float] | None = None,
    sell_thresholds: list[float] | None = None,
    windows: list[int] | None = None,
    num_stds: list[float] | None = None,
    trend_ema_windows: list[int] | None = None,
    pullback_ema_windows: list[int] | None = None,
) -> list[dict]:
    if strategy_name == "moving_average_cross":
        return [
            {"fast_window": fast, "slow_window": slow}
            for fast, slow in generate_parameter_grid(fast_windows or [], slow_windows or [])
        ]
    if strategy_name == "donchian_breakout":
        return [
            {
                "entry_window": entry,
                "exit_window": exit_,
                "atr_window": atr_window,
                "atr_multiplier": atr_multiplier,
            }
            for entry in entry_windows or []
            for exit_ in exit_windows or []
            for atr_window in atr_windows or []
            for atr_multiplier in atr_multipliers or []
            if exit_ < entry
        ]
    if strategy_name == "rsi_mean_reversion":
        return [
            {"rsi_window": window, "buy_threshold": buy, "sell_threshold": sell}
            for window in rsi_windows or []
            for buy in buy_thresholds or []
            for sell in sell_thresholds or []
            if buy < sell
        ]
    if strategy_name == "bollinger_mean_reversion":
        return [
            {"window": window, "num_std": num_std}
            for window in windows or []
            for num_std in num_stds or []
        ]
    if strategy_name == "ema_pullback":
        return [
            {"trend_ema_window": trend, "pullback_ema_window": pullback}
            for trend in trend_ema_windows or []
            for pullback in pullback_ema_windows or []
            if pullback < trend
        ]
    raise ValueError(f"Unsupported strategy for optimization: {strategy_name}")


def score_metrics(metrics: BacktestMetrics, min_trades: int, max_drawdown_penalty: float) -> OptimizationScore:
    base_score = metrics.total_return_pct - max_drawdown_penalty * abs(metrics.max_drawdown_pct)
    if metrics.trade_count < min_trades:
        return OptimizationScore(score=round(base_score - 1000, 10), status="insufficient_trades")
    return OptimizationScore(score=round(base_score, 10), status="ok")


def run_optimization(config: AppConfig, bars: pd.DataFrame | None = None) -> list[OptimizationRow]:
    symbol = config.symbols[0]
    data = bars if bars is not None else load_ohlcv_csv(_csv_path_for_symbol(config, symbol))
    rows: list[OptimizationRow] = []
    strategy_name = config.optimization.strategy_name or config.strategy.name
    for params in generate_strategy_parameter_grid(
        strategy_name,
        fast_windows=config.optimization.fast_windows,
        slow_windows=config.optimization.slow_windows,
        entry_windows=config.optimization.entry_windows,
        exit_windows=config.optimization.exit_windows,
        atr_windows=config.optimization.atr_windows,
        atr_multipliers=config.optimization.atr_multipliers,
        rsi_windows=config.optimization.rsi_windows,
        buy_thresholds=config.optimization.buy_thresholds,
        sell_thresholds=config.optimization.sell_thresholds,
        windows=config.optimization.windows,
        num_stds=config.optimization.num_stds,
        trend_ema_windows=config.optimization.trend_ema_windows,
        pullback_ema_windows=config.optimization.pullback_ema_windows,
    ):
        result = _run_backtest_for_params(config, data, symbol, strategy_name=strategy_name, params=params)
        scored = score_metrics(
            result.metrics,
            min_trades=config.optimization.min_trades,
            max_drawdown_penalty=config.optimization.score.max_drawdown_penalty,
        )
        rows.append(
            OptimizationRow(
                strategy_name=strategy_name,
                fast_window=int(params.get("fast_window") or 0),
                slow_window=int(params.get("slow_window") or 0),
                entry_window=params.get("entry_window"),
                exit_window=params.get("exit_window"),
                atr_window=params.get("atr_window"),
                atr_multiplier=params.get("atr_multiplier"),
                rsi_window=params.get("rsi_window"),
                buy_threshold=params.get("buy_threshold"),
                sell_threshold=params.get("sell_threshold"),
                window=params.get("window"),
                num_std=params.get("num_std"),
                trend_ema_window=params.get("trend_ema_window"),
                pullback_ema_window=params.get("pullback_ema_window"),
                total_return_pct=result.metrics.total_return_pct,
                annualized_return_pct=result.metrics.annualized_return_pct,
                max_drawdown_pct=result.metrics.max_drawdown_pct,
                win_rate_pct=result.metrics.win_rate_pct,
                profit_factor=result.metrics.profit_factor,
                profit_factor_note=result.metrics.profit_factor_note,
                trade_count=result.metrics.trade_count,
                rejected_order_count=result.metrics.rejected_order_count,
                filter_reject_count=result.metrics.filter_reject_count,
                filter_enabled=result.metrics.filter_enabled,
                final_equity=result.metrics.final_equity,
                score=scored.score,
                status=scored.status,
            )
        )
    return sorted(rows, key=lambda row: row.score, reverse=True)


def split_walk_forward(
    bars: pd.DataFrame,
    train_ratio: float,
    test_ratio: float,
    min_train_bars: int,
    min_test_bars: int,
    mode: str = "single",
    train_bars: int | None = None,
    test_bars: int | None = None,
    step_bars: int | None = None,
) -> list[WalkForwardWindow]:
    if mode == "rolling":
        return _split_rolling_walk_forward(
            bars,
            train_bars=train_bars or min_train_bars,
            test_bars=test_bars or min_test_bars,
            step_bars=step_bars or test_bars or min_test_bars,
            min_train_bars=min_train_bars,
            min_test_bars=min_test_bars,
        )

    total_ratio = train_ratio + test_ratio
    train_size = int(len(bars) * (train_ratio / total_ratio))
    test_size = len(bars) - train_size
    if train_size < min_train_bars or test_size < min_test_bars:
        return []
    return [
        WalkForwardWindow(
            window_id=1,
            train=bars.iloc[:train_size].reset_index(drop=True),
            test=bars.iloc[train_size:].reset_index(drop=True),
        )
    ]


def _split_rolling_walk_forward(
    bars: pd.DataFrame,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    min_train_bars: int,
    min_test_bars: int,
) -> list[WalkForwardWindow]:
    if train_bars < min_train_bars or test_bars < min_test_bars:
        return []
    windows: list[WalkForwardWindow] = []
    start = 0
    window_id = 1
    while start + train_bars + test_bars <= len(bars):
        train_start = start
        train_end = train_start + train_bars
        test_end = train_end + test_bars
        windows.append(
            WalkForwardWindow(
                window_id=window_id,
                train=bars.iloc[train_start:train_end].reset_index(drop=True),
                test=bars.iloc[train_end:test_end].reset_index(drop=True),
            )
        )
        window_id += 1
        start += step_bars
    return windows


def run_walk_forward(config: AppConfig, bars: pd.DataFrame | None = None) -> list[WalkForwardRow]:
    if not config.optimization.walk_forward.enabled:
        return []
    symbol = config.symbols[0]
    data = bars if bars is not None else load_ohlcv_csv(_csv_path_for_symbol(config, symbol))
    windows = split_walk_forward(
        data,
        train_ratio=config.optimization.walk_forward.train_ratio,
        test_ratio=config.optimization.walk_forward.test_ratio,
        min_train_bars=config.optimization.walk_forward.min_train_bars,
        min_test_bars=config.optimization.walk_forward.min_test_bars,
        mode=config.optimization.walk_forward.mode,
        train_bars=config.optimization.walk_forward.train_bars,
        test_bars=config.optimization.walk_forward.test_bars,
        step_bars=config.optimization.walk_forward.step_bars,
    )
    rows: list[WalkForwardRow] = []
    for window in windows:
        train_rows = run_optimization(config, bars=window.train)
        if not train_rows:
            continue
        selected = train_rows[0]
        train_result = _run_backtest_for_params(
            config,
            window.train,
            symbol,
            strategy_name=selected.strategy_name,
            params=_params_from_row(selected),
        )
        test_result = _run_backtest_for_params(
            config,
            window.test,
            symbol,
            strategy_name=selected.strategy_name,
            params=_params_from_row(selected),
        )
        rows.append(
            WalkForwardRow(
                window_id=window.window_id,
                train_start=_timestamp(window.train, 0),
                train_end=_timestamp(window.train, -1),
                test_start=_timestamp(window.test, 0),
                test_end=_timestamp(window.test, -1),
                selected_fast_window=selected.fast_window,
                selected_slow_window=selected.slow_window,
                selected_entry_window=selected.entry_window,
                selected_exit_window=selected.exit_window,
                selected_atr_window=selected.atr_window,
                selected_atr_multiplier=selected.atr_multiplier,
                selected_rsi_window=selected.rsi_window,
                selected_buy_threshold=selected.buy_threshold,
                selected_sell_threshold=selected.sell_threshold,
                selected_window=selected.window,
                selected_num_std=selected.num_std,
                selected_trend_ema_window=selected.trend_ema_window,
                selected_pullback_ema_window=selected.pullback_ema_window,
                train_score=selected.score,
                train_total_return_pct=train_result.metrics.total_return_pct,
                train_max_drawdown_pct=train_result.metrics.max_drawdown_pct,
                test_total_return_pct=test_result.metrics.total_return_pct,
                test_max_drawdown_pct=test_result.metrics.max_drawdown_pct,
                test_trade_count=test_result.metrics.trade_count,
                test_profit_factor=test_result.metrics.profit_factor,
                test_profit_factor_note=test_result.metrics.profit_factor_note,
                filter_reject_count=test_result.metrics.filter_reject_count,
                filter_enabled=test_result.metrics.filter_enabled,
            )
        )
    return rows


def _run_backtest_for_params(
    config: AppConfig,
    bars: pd.DataFrame,
    symbol: str,
    strategy_name: str,
    params: dict,
) -> BacktestResult:
    engine = BacktestEngine(
        strategy=create_strategy_from_params(strategy_name, params),
        risk_manager=RiskManager(RiskSettings(**config.risk.__dict__)),
        execution_engine=PaperExecutionEngine(config.execution.fee_rate, config.execution.slippage_bps),
        account=Account(config.initial_cash),
        regime_filter=RegimeFilter(RegimeFilterSettings(**config.regime_filter.__dict__)),
    )
    return engine.run(symbol, bars)


def _params_from_row(row: OptimizationRow) -> dict:
    if row.strategy_name == "donchian_breakout":
        return {
            "entry_window": row.entry_window,
            "exit_window": row.exit_window,
            "atr_window": row.atr_window,
            "atr_multiplier": row.atr_multiplier,
        }
    if row.strategy_name == "rsi_mean_reversion":
        return {
            "rsi_window": row.rsi_window,
            "buy_threshold": row.buy_threshold,
            "sell_threshold": row.sell_threshold,
        }
    if row.strategy_name == "bollinger_mean_reversion":
        return {"window": row.window, "num_std": row.num_std}
    if row.strategy_name == "ema_pullback":
        return {
            "trend_ema_window": row.trend_ema_window,
            "pullback_ema_window": row.pullback_ema_window,
        }
    return {"fast_window": row.fast_window, "slow_window": row.slow_window}


def _csv_path_for_symbol(config: AppConfig, symbol: str) -> str:
    return config.market_data.csv_files.get(symbol, config.market_data.csv_path)


def _timestamp(frame: pd.DataFrame, index: int) -> str:
    return pd.Timestamp(frame.iloc[index]["timestamp"]).isoformat()
