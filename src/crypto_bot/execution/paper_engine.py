from __future__ import annotations

from datetime import datetime

from crypto_bot.errors import RiskError
from crypto_bot.execution.approval import RiskApproval, consume_risk_approval
from crypto_bot.execution.models import Fill, OrderIntent, OrderSide
from crypto_bot.portfolio.account import Account


class PaperExecutionEngine:
    def __init__(self, fee_rate: float = 0.001, slippage_bps: float = 5) -> None:
        self.fee_rate = fee_rate
        self.slippage_bps = slippage_bps

    def execute(
        self,
        order: OrderIntent,
        account: Account,
        market_price: float,
        timestamp: datetime,
        approval: RiskApproval | None = None,
    ) -> Fill:
        if market_price <= 0:
            raise RiskError("market_price must be positive")
        consume_risk_approval(order, approval)

        fill_price = self._apply_slippage(order.side, market_price)
        gross = order.quantity * fill_price
        fee = round(gross * self.fee_rate, 10)

        if order.side == OrderSide.BUY and gross + fee > account.cash_balance():
            raise RiskError("insufficient virtual cash")
        if order.side == OrderSide.SELL and account.get_position(order.symbol).quantity < order.quantity:
            raise RiskError("insufficient virtual position")

        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            fee=fee,
            slippage=round(abs(fill_price - market_price), 10),
            timestamp=timestamp,
            reason=order.reason,
        )
        account.apply_fill(fill)
        return fill

    def _apply_slippage(self, side: OrderSide, market_price: float) -> float:
        multiplier = self.slippage_bps / 10_000
        if side == OrderSide.BUY:
            return round(market_price * (1 + multiplier), 10)
        return round(market_price * (1 - multiplier), 10)
