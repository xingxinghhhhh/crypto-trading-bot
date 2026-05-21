from crypto_bot.config import load_config


def test_okx_public_paper_config_loads_safely():
    config = load_config("config.paper.okx.example.yaml")

    assert config.mode == "paper"
    assert config.live_trading is False
    assert config.market_data.source == "ccxt_public"
    assert config.market_data.exchange == "okx"
    assert config.market_data.symbols == ["BTC/USDT"]
    assert config.market_data.timeframe == "1m"
    assert config.market_data.limit == 100
