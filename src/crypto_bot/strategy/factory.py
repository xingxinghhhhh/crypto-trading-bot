from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from crypto_bot.config import StrategyConfig
from crypto_bot.strategy.base import Strategy
from crypto_bot.strategy.bollinger_mean_reversion import BollingerMeanReversionStrategy
from crypto_bot.strategy.donchian_breakout import DonchianBreakoutStrategy
from crypto_bot.strategy.ema_pullback import EmaPullbackStrategy
from crypto_bot.strategy.moving_average_cross import MovingAverageCrossStrategy
from crypto_bot.strategy.rsi_mean_reversion import RsiMeanReversionStrategy


def create_strategy(config: StrategyConfig) -> Strategy:
    return create_strategy_from_params(config.name, config.__dict__)


def create_strategy_from_params(strategy_name: str, params: Mapping[str, Any]) -> Strategy:
    if strategy_name == "moving_average_cross":
        return MovingAverageCrossStrategy(
            fast_window=int(params["fast_window"]),
            slow_window=int(params["slow_window"]),
        )
    if strategy_name == "donchian_breakout":
        return DonchianBreakoutStrategy(
            entry_window=int(params["entry_window"]),
            exit_window=int(params["exit_window"]),
            atr_window=int(params["atr_window"]),
            atr_multiplier=float(params["atr_multiplier"]),
        )
    if strategy_name == "rsi_mean_reversion":
        return RsiMeanReversionStrategy(
            rsi_window=int(params["rsi_window"]),
            buy_threshold=float(params["buy_threshold"]),
            sell_threshold=float(params["sell_threshold"]),
        )
    if strategy_name == "bollinger_mean_reversion":
        return BollingerMeanReversionStrategy(
            window=int(params["window"]),
            num_std=float(params["num_std"]),
        )
    if strategy_name == "ema_pullback":
        return EmaPullbackStrategy(
            trend_ema_window=int(params["trend_ema_window"]),
            pullback_ema_window=int(params["pullback_ema_window"]),
        )
    raise ValueError(f"Unsupported strategy: {strategy_name}")
