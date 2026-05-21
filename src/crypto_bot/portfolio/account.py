from __future__ import annotations

from dataclasses import dataclass, field

from crypto_bot.execution.models import Fill, OrderSide
from crypto_bot.portfolio.position import Position


@dataclass
class Account:
    initial_cash: float
    cash: float | None = None
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0
    peak_equity: float | None = None

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.cash is None:
            self.cash = float(self.initial_cash)
        if self.peak_equity is None:
            self.peak_equity = float(self.initial_cash)

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def equity(self, prices: dict[str, float] | None = None) -> float:
        total = float(self.cash or 0.0)
        prices = prices or {}
        for symbol, position in self.positions.items():
            total += position.market_value(prices.get(symbol))
        if self.peak_equity is None or total > self.peak_equity:
            self.peak_equity = total
        return total

    def drawdown_pct(self, prices: dict[str, float] | None = None) -> float:
        equity = self.equity(prices)
        peak = self.peak_equity or equity
        if peak <= 0:
            return 0.0
        return max(0.0, (peak - equity) / peak)

    def apply_fill(self, fill: Fill) -> float:
        position = self.get_position(fill.symbol)
        gross = fill.quantity * fill.price
        if fill.side == OrderSide.BUY:
            total_cost = gross + fill.fee
            if total_cost > float(self.cash):
                raise ValueError("insufficient cash for fill")
            self.cash = round(float(self.cash) - total_cost, 10)
            position.apply_buy(fill.quantity, fill.price)
            return 0.0

        pnl = position.apply_sell(fill.quantity, fill.price)
        self.cash = round(float(self.cash) + gross - fill.fee, 10)
        self.realized_pnl += pnl - fill.fee
        return pnl - fill.fee
