from __future__ import annotations

from dataclasses import dataclass

from crypto_bot.execution.approval import RiskApproval, _issue_risk_approval
from crypto_bot.execution.models import OrderIntent, OrderSide
from crypto_bot.portfolio.account import Account
from crypto_bot.strategy.signals import Signal, SignalSide


@dataclass(frozen=True)
class RiskSettings:
    max_position_pct: float = 0.2
    max_daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.1
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    allow_pyramiding: bool = False
    max_trades_per_day: int = 20
    min_bars_required: int = 50
    pause_on_missing_data: bool = True
    abnormal_move_pct: float = 0.08
    min_order_notional: float = 10


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    order: OrderIntent | None = None
    approval: RiskApproval | None = None


class RiskManager:
    def __init__(self, settings: RiskSettings) -> None:
        self.settings = settings

    def evaluate(
        self,
        signal: Signal,
        account: Account,
        market_price: float,
        bars_count: int,
        missing_data: bool,
        abnormal_move: bool,
        trades_today: int = 0,
        daily_loss_pct: float = 0.0,
    ) -> RiskDecision:
        if signal.side == SignalSide.HOLD:
            return RiskDecision(False, "hold_signal")
        if market_price <= 0:
            return RiskDecision(False, "invalid_market_price")
        if self.settings.pause_on_missing_data and missing_data:
            return RiskDecision(False, "missing_market_data")
        if bars_count < self.settings.min_bars_required:
            return RiskDecision(False, "insufficient_bars")
        if abnormal_move:
            return RiskDecision(False, "abnormal_market_move")
        if daily_loss_pct >= self.settings.max_daily_loss_pct:
            return RiskDecision(False, "max_daily_loss")
        if account.drawdown_pct({signal.symbol: market_price}) >= self.settings.max_drawdown_pct:
            return RiskDecision(False, "max_drawdown")

        if signal.side == SignalSide.BUY:
            decision = self._evaluate_buy(signal, account, market_price)
        else:
            decision = self._evaluate_sell(signal, account, market_price)
        if decision.approved and trades_today >= self.settings.max_trades_per_day:
            return RiskDecision(False, "max_trades_per_day")
        return decision

    def evaluate_protective_exit(
        self,
        signal: Signal,
        account: Account,
        market_price: float,
    ) -> RiskDecision | None:
        if market_price <= 0 or signal.side == SignalSide.SELL:
            return None
        position = account.get_position(signal.symbol)
        if position.quantity <= 0 or position.avg_price <= 0:
            return None

        loss_pct = (position.avg_price - market_price) / position.avg_price
        gain_pct = (market_price - position.avg_price) / position.avg_price
        if loss_pct >= self.settings.stop_loss_pct:
            return self._protective_sell(signal, position.quantity, market_price, "stop_loss")
        if gain_pct >= self.settings.take_profit_pct:
            return self._protective_sell(signal, position.quantity, market_price, "take_profit")
        return None

    def _evaluate_buy(self, signal: Signal, account: Account, market_price: float) -> RiskDecision:
        position = account.get_position(signal.symbol)
        if position.quantity > 0 and not self.settings.allow_pyramiding:
            return RiskDecision(False, "pyramiding_not_allowed")

        equity = account.equity({signal.symbol: market_price})
        max_notional = equity * self.settings.max_position_pct
        if max_notional < self.settings.min_order_notional:
            return RiskDecision(False, "below_min_order_notional")
        quantity = max_notional / market_price
        order = OrderIntent(
            symbol=signal.symbol,
            side=OrderSide.BUY,
            quantity=quantity,
            reason=signal.reason,
            signal_id=signal.id,
            reference_price=market_price,
            risk_checked=True,
        )
        return RiskDecision(True, "approved", order, _issue_risk_approval(order))

    def _evaluate_sell(self, signal: Signal, account: Account, market_price: float) -> RiskDecision:
        position = account.get_position(signal.symbol)
        if position.quantity <= 0:
            return RiskDecision(False, "no_position_to_sell")
        order = OrderIntent(
            symbol=signal.symbol,
            side=OrderSide.SELL,
            quantity=position.quantity,
            reason=signal.reason,
            signal_id=signal.id,
            reference_price=market_price,
            risk_checked=True,
        )
        return RiskDecision(True, "approved", order, _issue_risk_approval(order))

    def _protective_sell(
        self,
        signal: Signal,
        quantity: float,
        market_price: float,
        reason: str,
    ) -> RiskDecision:
        order = OrderIntent(
            symbol=signal.symbol,
            side=OrderSide.SELL,
            quantity=quantity,
            reason=reason,
            signal_id=signal.id,
            reference_price=market_price,
            risk_checked=True,
        )
        return RiskDecision(True, reason, order, _issue_risk_approval(order))
