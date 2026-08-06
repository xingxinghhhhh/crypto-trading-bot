from dataclasses import replace

import pytest

from crypto_bot.config import load_config, validate_config
from crypto_bot.errors import ConfigError


def test_okx_public_paper_config_loads_safely():
    config = load_config("config.paper.okx.example.yaml")

    assert config.mode == "paper"
    assert config.live_trading is False
    assert config.market_data.source == "ccxt_public"
    assert config.market_data.exchange == "okx"
    assert config.market_data.symbols == ["BTC/USDT"]
    assert config.market_data.timeframe == "1m"
    assert config.market_data.limit == 100
    assert config.market_data.use_environment_proxy is True


def test_environment_proxy_setting_must_be_boolean():
    config = load_config("config.paper.okx.example.yaml")
    invalid = replace(
        config,
        market_data=replace(config.market_data, use_environment_proxy="true"),
    )

    with pytest.raises(ConfigError, match="use_environment_proxy must be a boolean"):
        validate_config(invalid)
