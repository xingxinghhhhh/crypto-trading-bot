from __future__ import annotations

import os
from collections.abc import Mapping

from crypto_bot.config import AppConfig
from crypto_bot.errors import SafetyError
from crypto_bot.execution.models import OrderIntent


def assert_live_trading_allowed(config: AppConfig, env: Mapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    if config.mode != "live":
        raise SafetyError("live trading requires mode=live")
    if not config.live_trading:
        raise SafetyError("live trading requires config live_trading=true")
    if env.get("LIVE_TRADING", "").lower() != "true":
        raise SafetyError("live trading requires environment LIVE_TRADING=true")


class LiveExecutionClient:
    def place_order(self, order: OrderIntent) -> None:
        raise SafetyError("real order placement is disabled in the MVP")

    def cancel_order(self, order_id: str) -> None:
        raise SafetyError("real order cancellation is disabled in the MVP")
