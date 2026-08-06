from __future__ import annotations

import pytest

from crypto_bot.config import StrategyConfig
from crypto_bot.strategy.bollinger_mean_reversion import BollingerMeanReversionStrategy
from crypto_bot.strategy.donchian_breakout import DonchianBreakoutStrategy
from crypto_bot.strategy.ema_pullback import EmaPullbackStrategy
from crypto_bot.strategy.factory import create_strategy, create_strategy_from_params
from crypto_bot.strategy.moving_average_cross import MovingAverageCrossStrategy
from crypto_bot.strategy.rsi_mean_reversion import RsiMeanReversionStrategy


@pytest.mark.parametrize(
    ("config", "expected_type"),
    [
        (StrategyConfig(name="moving_average_cross"), MovingAverageCrossStrategy),
        (StrategyConfig(name="donchian_breakout"), DonchianBreakoutStrategy),
        (StrategyConfig(name="rsi_mean_reversion"), RsiMeanReversionStrategy),
        (StrategyConfig(name="bollinger_mean_reversion"), BollingerMeanReversionStrategy),
        (StrategyConfig(name="ema_pullback"), EmaPullbackStrategy),
    ],
)
def test_create_strategy_supports_every_configured_strategy(config, expected_type) -> None:
    assert isinstance(create_strategy(config), expected_type)


def test_create_strategy_from_params_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="Unsupported strategy"):
        create_strategy_from_params("unknown", {})
