from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    def apply_buy(self, quantity: float, price: float) -> None:
        current_cost = self.quantity * self.avg_price
        new_cost = quantity * price
        self.quantity += quantity
        self.avg_price = (current_cost + new_cost) / self.quantity

    def apply_sell(self, quantity: float, price: float) -> float:
        if quantity > self.quantity:
            raise ValueError("cannot sell more than current position")
        pnl = (price - self.avg_price) * quantity
        self.quantity -= quantity
        self.realized_pnl += pnl
        if self.quantity == 0:
            self.avg_price = 0.0
        return pnl

    def market_value(self, price: float | None = None) -> float:
        mark = self.avg_price if price is None else price
        return self.quantity * mark
