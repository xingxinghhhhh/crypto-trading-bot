from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeRecord:
    timestamp: str
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    slippage_cost: float
    realized_pnl: float
    reason: str
    source_signal_id: str | None


@dataclass(frozen=True)
class EquityCurvePoint:
    timestamp: str
    cash: float
    position_qty: float
    position_value: float
    equity: float
    drawdown_pct: float
    close_price: float


@dataclass(frozen=True)
class RiskEventRecord:
    timestamp: str
    symbol: str
    approved: bool
    rejected: bool
    reason: str
    adjusted_size: float
    equity: float
    current_drawdown_pct: float
