from datetime import datetime, timezone

from crypto_bot.execution.models import OrderIntent, OrderSide
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.portfolio.account import Account


def test_paper_engine_buy_fill_applies_slippage_fee_and_position_update():
    account = Account(initial_cash=1_000)
    engine = PaperExecutionEngine(fee_rate=0.001, slippage_bps=10)
    order = OrderIntent(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=1,
        reason="unit_test",
        risk_checked=True,
    )

    fill = engine.execute(
        order=order,
        account=account,
        market_price=100,
        timestamp=datetime.now(timezone.utc),
    )

    assert fill.price == 100.1
    assert fill.fee == 0.1001
    assert account.cash == 899.7999
    assert account.positions["BTC/USDT"].quantity == 1
    assert account.positions["BTC/USDT"].avg_price == 100.1
