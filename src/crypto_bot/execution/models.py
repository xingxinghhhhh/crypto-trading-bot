from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: OrderSide
    quantity: float
    reason: str
    signal_id: str | None = None
    reference_price: float | None = None
    risk_checked: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if not self.id:
            object.__setattr__(self, "id", str(uuid4()))

    @property
    def notional(self) -> float:
        if self.reference_price is None:
            return 0.0
        return self.quantity * self.reference_price


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    fee: float
    slippage: float
    timestamp: datetime
    reason: str
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", str(uuid4()))
