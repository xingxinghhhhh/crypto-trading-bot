from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from crypto_bot.errors import ConfigError


@dataclass(frozen=True)
class MarketDataConfig:
    source: str = "csv"
    csv_path: str = "data/sample_ohlcv.csv"
    csv_files: dict[str, str] = field(default_factory=dict)
    exchange: str = "binance"
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDT"])
    timeframe: str = "1m"
    since: str | None = None
    until: str | None = None
    limit: int = 100


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "moving_average_cross"
    fast_window: int = 10
    slow_window: int = 30
    entry_window: int = 20
    exit_window: int = 10
    atr_window: int = 14
    atr_multiplier: float = 2.0
    rsi_window: int = 14
    buy_threshold: float = 30
    sell_threshold: float = 55
    window: int = 20
    num_std: float = 2.0
    trend_ema_window: int = 200
    pullback_ema_window: int = 20


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 0.2
    max_daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.1
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    allow_pyramiding: bool = False
    max_trades_per_day: int = 20
    min_bars_required: int = 50
    pause_on_missing_data: bool = True
    abnormal_move_pct: float = 0.08
    min_order_notional: float = 10


@dataclass(frozen=True)
class ExecutionConfig:
    fee_rate: float = 0.001
    slippage_bps: float = 5


@dataclass(frozen=True)
class StorageConfig:
    url: str = "sqlite:///data/trading.db"


@dataclass(frozen=True)
class RegimeFilterConfig:
    enabled: bool = False
    min_moving_average_slope: float | None = None
    min_trend_strength: float | None = None
    max_range_bound_score: float | None = None
    max_window_drawdown_pct: float | None = None
    min_price_above_slow_ma_pct: float | None = None
    min_volume_change_pct: float | None = None


@dataclass(frozen=True)
class OptimizationScoreConfig:
    max_drawdown_penalty: float = 2.0


@dataclass(frozen=True)
class WalkForwardConfig:
    enabled: bool = True
    mode: str = "single"
    train_ratio: float = 0.7
    test_ratio: float = 0.3
    train_bars: int = 3000
    test_bars: int = 720
    step_bars: int = 720
    min_train_bars: int = 100
    min_test_bars: int = 50


@dataclass(frozen=True)
class OptimizationConfig:
    strategy_name: str | None = None
    fast_windows: list[int] = field(default_factory=lambda: [5, 10, 15])
    slow_windows: list[int] = field(default_factory=lambda: [30, 50])
    entry_windows: list[int] = field(default_factory=lambda: [20, 40, 60])
    exit_windows: list[int] = field(default_factory=lambda: [10, 20, 30])
    atr_windows: list[int] = field(default_factory=lambda: [14])
    atr_multipliers: list[float] = field(default_factory=lambda: [1.5, 2.0, 3.0])
    rsi_windows: list[int] = field(default_factory=lambda: [7, 14, 21])
    buy_thresholds: list[float] = field(default_factory=lambda: [25, 30, 35])
    sell_thresholds: list[float] = field(default_factory=lambda: [50, 55, 60, 65])
    windows: list[int] = field(default_factory=lambda: [20, 30, 40])
    num_stds: list[float] = field(default_factory=lambda: [1.5, 2.0, 2.5])
    trend_ema_windows: list[int] = field(default_factory=lambda: [100, 200])
    pullback_ema_windows: list[int] = field(default_factory=lambda: [10, 20, 30])
    min_trades: int = 3
    score: OptimizationScoreConfig = field(default_factory=OptimizationScoreConfig)
    walk_forward: WalkForwardConfig = field(default_factory=WalkForwardConfig)


@dataclass(frozen=True)
class AppConfig:
    mode: str = "paper"
    exchange: str = "local_csv"
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDT"])
    timeframe: str = "1m"
    initial_cash: float = 10_000
    quote_currency: str = "USDT"
    live_trading: bool = False
    market_data: MarketDataConfig = field(default_factory=MarketDataConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    regime_filter: RegimeFilterConfig = field(default_factory=RegimeFilterConfig)
    optimization: OptimizationConfig = field(default_factory=OptimizationConfig)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    optimization_raw = raw.get("optimization", {})
    optimization_score_raw = optimization_raw.get("score", {})
    walk_forward_raw = optimization_raw.get("walk_forward", {})

    config = AppConfig(
        mode=raw.get("mode", "paper"),
        exchange=raw.get("exchange", "local_csv"),
        symbols=list(raw.get("symbols", ["BTC/USDT"])),
        timeframe=raw.get("timeframe", "1m"),
        initial_cash=float(raw.get("initial_cash", 10_000)),
        quote_currency=raw.get("quote_currency", "USDT"),
        live_trading=bool(raw.get("live_trading", False)),
        market_data=MarketDataConfig(**raw.get("market_data", {})),
        strategy=StrategyConfig(**raw.get("strategy", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        execution=ExecutionConfig(**raw.get("execution", {})),
        storage=StorageConfig(**raw.get("storage", {})),
        regime_filter=RegimeFilterConfig(**raw.get("regime_filter", {})),
        optimization=OptimizationConfig(
            strategy_name=optimization_raw.get("strategy_name"),
            fast_windows=list(optimization_raw.get("fast_windows", [5, 10, 15])),
            slow_windows=list(optimization_raw.get("slow_windows", [30, 50])),
            entry_windows=list(optimization_raw.get("entry_windows", [20, 40, 60])),
            exit_windows=list(optimization_raw.get("exit_windows", [10, 20, 30])),
            atr_windows=list(optimization_raw.get("atr_windows", [14])),
            atr_multipliers=[float(value) for value in optimization_raw.get("atr_multipliers", [1.5, 2.0, 3.0])],
            rsi_windows=list(optimization_raw.get("rsi_windows", [7, 14, 21])),
            buy_thresholds=[float(value) for value in optimization_raw.get("buy_thresholds", [25, 30, 35])],
            sell_thresholds=[float(value) for value in optimization_raw.get("sell_thresholds", [50, 55, 60, 65])],
            windows=list(optimization_raw.get("windows", [20, 30, 40])),
            num_stds=[float(value) for value in optimization_raw.get("num_stds", [1.5, 2.0, 2.5])],
            trend_ema_windows=list(optimization_raw.get("trend_ema_windows", [100, 200])),
            pullback_ema_windows=list(optimization_raw.get("pullback_ema_windows", [10, 20, 30])),
            min_trades=int(optimization_raw.get("min_trades", 3)),
            score=OptimizationScoreConfig(**optimization_score_raw),
            walk_forward=WalkForwardConfig(**walk_forward_raw),
        ),
    )
    validate_config(config)
    return config


def validate_config(config: AppConfig) -> None:
    if config.mode not in {"backtest", "paper", "live"}:
        raise ConfigError("mode must be one of: backtest, paper, live")
    if config.mode == "live":
        raise ConfigError("live mode is reserved and disabled in the MVP")
    if config.live_trading:
        raise ConfigError("live_trading must remain false in the MVP")
    if config.initial_cash <= 0:
        raise ConfigError("initial_cash must be positive")
    if not config.symbols:
        raise ConfigError("at least one symbol is required")
    if config.market_data.source not in {"csv", "ccxt_public"}:
        raise ConfigError("market_data.source must be one of: csv, ccxt_public")
    if config.market_data.exchange not in {"binance", "okx"}:
        raise ConfigError("market_data.exchange must be one of: binance, okx")
    if config.market_data.limit <= 0:
        raise ConfigError("market_data.limit must be positive")
    if not config.market_data.symbols:
        raise ConfigError("market_data.symbols must contain at least one symbol")
    supported_strategies = {
        "moving_average_cross",
        "donchian_breakout",
        "rsi_mean_reversion",
        "bollinger_mean_reversion",
        "ema_pullback",
    }
    if config.strategy.name not in supported_strategies:
        raise ConfigError("strategy.name must be one of: " + ", ".join(sorted(supported_strategies)))
    if config.strategy.name == "moving_average_cross":
        if config.strategy.fast_window <= 0 or config.strategy.slow_window <= 0:
            raise ConfigError("strategy windows must be positive")
        if config.strategy.fast_window >= config.strategy.slow_window:
            raise ConfigError("fast_window must be smaller than slow_window")
    if config.strategy.name == "donchian_breakout":
        if config.strategy.entry_window <= 0 or config.strategy.exit_window <= 0 or config.strategy.atr_window <= 0:
            raise ConfigError("donchian and ATR windows must be positive")
        if config.strategy.exit_window >= config.strategy.entry_window:
            raise ConfigError("exit_window must be smaller than entry_window")
        if config.strategy.atr_multiplier <= 0:
            raise ConfigError("atr_multiplier must be positive")
    if config.strategy.name == "rsi_mean_reversion":
        if config.strategy.rsi_window <= 0:
            raise ConfigError("rsi_window must be positive")
        if config.strategy.buy_threshold >= config.strategy.sell_threshold:
            raise ConfigError("buy_threshold must be smaller than sell_threshold")
    if config.strategy.name == "bollinger_mean_reversion":
        if config.strategy.window <= 1:
            raise ConfigError("window must be greater than 1")
        if config.strategy.num_std <= 0:
            raise ConfigError("num_std must be positive")
    if config.strategy.name == "ema_pullback":
        if config.strategy.trend_ema_window <= 0 or config.strategy.pullback_ema_window <= 0:
            raise ConfigError("EMA windows must be positive")
        if config.strategy.pullback_ema_window >= config.strategy.trend_ema_window:
            raise ConfigError("pullback_ema_window must be smaller than trend_ema_window")
    optimization_strategy = config.optimization.strategy_name or config.strategy.name
    if optimization_strategy not in supported_strategies:
        raise ConfigError("optimization.strategy_name must be one of: " + ", ".join(sorted(supported_strategies)))
    if config.optimization.min_trades < 0:
        raise ConfigError("optimization.min_trades must be non-negative")
    if config.optimization.walk_forward.train_ratio <= 0 or config.optimization.walk_forward.test_ratio <= 0:
        raise ConfigError("walk_forward train_ratio and test_ratio must be positive")
    if config.optimization.walk_forward.mode not in {"single", "rolling"}:
        raise ConfigError("walk_forward mode must be one of: single, rolling")
    if config.optimization.walk_forward.train_bars <= 0:
        raise ConfigError("walk_forward train_bars must be positive")
    if config.optimization.walk_forward.test_bars <= 0:
        raise ConfigError("walk_forward test_bars must be positive")
    if config.optimization.walk_forward.step_bars <= 0:
        raise ConfigError("walk_forward step_bars must be positive")


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if _is_sensitive_key(key) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    upper_key = key.upper()
    return any(token in upper_key for token in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSPHRASE"))
