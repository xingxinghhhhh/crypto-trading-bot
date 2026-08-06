from datetime import datetime, timezone

from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.portfolio.account import Account
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy.signals import Signal, SignalSide


def test_paper_engine_buy_fill_applies_slippage_fee_and_position_update():
    account = Account(initial_cash=1_000)
    engine = PaperExecutionEngine(fee_rate=0.001, slippage_bps=10)
    signal = Signal(
        symbol="BTC/USDT",
        side=SignalSide.BUY,
        reason="unit_test",
        timestamp=datetime.now(timezone.utc),
    )
    decision = RiskManager(
        RiskSettings(max_position_pct=0.1, min_bars_required=1)
    ).evaluate(
        signal=signal,
        account=account,
        market_price=100,
        bars_count=10,
        missing_data=False,
        abnormal_move=False,
    )
    assert decision.order is not None

    fill = engine.execute(
        order=decision.order,
        account=account,
        market_price=100,
        timestamp=datetime.now(timezone.utc),
        approval=decision.approval,
    )

    assert fill.price == 100.1
    assert fill.fee == 0.1001
    assert account.cash == 899.7999
    assert account.positions["BTC/USDT"].quantity == 1
    assert account.positions["BTC/USDT"].avg_price == 100.1
