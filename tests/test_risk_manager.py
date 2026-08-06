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


def test_risk_manager_creates_stop_loss_exit_for_open_position():
    account = Account(initial_cash=10_000)
    account.get_position("BTC/USDT").apply_buy(1, 100)
    risk = RiskManager(RiskSettings(stop_loss_pct=0.02, take_profit_pct=0.04))
    signal = Signal(
        symbol="BTC/USDT",
        side=SignalSide.HOLD,
        reason="no_cross",
        timestamp=datetime.now(timezone.utc),
    )

    decision = risk.evaluate_protective_exit(signal, account, market_price=97)

    assert decision is not None
    assert decision.approved is True
    assert decision.reason == "stop_loss"
    assert decision.order is not None
    assert decision.order.side.value == "sell"
    assert decision.approval is not None


def test_risk_manager_creates_take_profit_exit_for_open_position():
    account = Account(initial_cash=10_000)
    account.get_position("BTC/USDT").apply_buy(1, 100)
    risk = RiskManager(RiskSettings(stop_loss_pct=0.02, take_profit_pct=0.04))
    signal = Signal(
        symbol="BTC/USDT",
        side=SignalSide.HOLD,
        reason="no_cross",
        timestamp=datetime.now(timezone.utc),
    )

    decision = risk.evaluate_protective_exit(signal, account, market_price=105)

    assert decision is not None
    assert decision.reason == "take_profit"
    assert decision.order is not None
    assert decision.order.quantity == 1


def test_risk_manager_rejects_new_exposure_after_daily_loss_limit():
    account = Account(initial_cash=10_000)
    risk = RiskManager(
        RiskSettings(
            max_daily_loss_pct=0.03,
            min_bars_required=1,
        )
    )
    signal = Signal(
        symbol="BTC/USDT",
        side=SignalSide.BUY,
        reason="new_exposure",
        timestamp=datetime.now(timezone.utc),
    )

    decision = risk.evaluate(
        signal=signal,
        account=account,
        market_price=100,
        bars_count=10,
        missing_data=False,
        abnormal_move=False,
        daily_loss_pct=0.03,
    )

    assert decision.approved is False
    assert decision.reason == "max_daily_loss"
