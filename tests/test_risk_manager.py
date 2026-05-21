from datetime import datetime, timezone

from crypto_bot.portfolio.account import Account
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy.signals import Signal, SignalSide


def test_risk_manager_caps_buy_order_by_max_position_pct():
    account = Account(initial_cash=10_000)
    risk = RiskManager(RiskSettings(max_position_pct=0.20, min_bars_required=3))
    signal = Signal(
        symbol="BTC/USDT",
        side=SignalSide.BUY,
        reason="test_buy",
        timestamp=datetime.now(timezone.utc),
    )

    decision = risk.evaluate(
        signal=signal,
        account=account,
        market_price=100,
        bars_count=10,
        missing_data=False,
        abnormal_move=False,
    )

    assert decision.approved is True
    assert decision.order is not None
    assert decision.order.quantity == 20
    assert decision.order.notional == 2_000


def test_risk_manager_rejects_missing_market_data():
    account = Account(initial_cash=10_000)
    risk = RiskManager(RiskSettings(max_position_pct=0.20, min_bars_required=3))
    signal = Signal(
        symbol="BTC/USDT",
        side=SignalSide.BUY,
        reason="test_buy",
        timestamp=datetime.now(timezone.utc),
    )

    decision = risk.evaluate(
        signal=signal,
        account=account,
        market_price=100,
        bars_count=10,
        missing_data=True,
        abnormal_move=False,
    )

    assert decision.approved is False
    assert decision.reason == "missing_market_data"
    assert decision.order is None
