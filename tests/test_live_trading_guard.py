import pytest

from crypto_bot.config import AppConfig
from crypto_bot.errors import SafetyError
from crypto_bot.execution.live_guard import LiveExecutionClient, assert_live_trading_allowed
from crypto_bot.execution.models import OrderIntent, OrderSide


def test_live_trading_guard_rejects_without_env_flag(monkeypatch):
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    config = AppConfig(mode="live", live_trading=True)

    with pytest.raises(SafetyError):
        assert_live_trading_allowed(config)


def test_live_execution_client_place_order_always_raises_for_mvp():
    client = LiveExecutionClient()
    order = OrderIntent(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=1,
        reason="must_not_trade_live",
    )

    with pytest.raises(SafetyError):
        client.place_order(order)
