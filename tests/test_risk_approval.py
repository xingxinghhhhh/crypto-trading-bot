from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from crypto_bot.errors import SafetyError
from crypto_bot.execution.models import OrderIntent, OrderSide
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.portfolio.account import Account
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy.signals import Signal, SignalSide


def _approved_order():
    account = Account(initial_cash=1_000)
    signal = Signal(
        symbol="BTC/USDT",
        side=SignalSide.BUY,
        reason="approval_test",
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
    assert decision.approval is not None
    return account, decision.order, decision.approval


def test_boolean_risk_checked_cannot_replace_approval() -> None:
    account = Account(initial_cash=1_000)
    order = OrderIntent(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=1,
        reason="forged_boolean",
        risk_checked=True,
    )

    with pytest.raises(SafetyError, match="require a RiskApproval"):
        PaperExecutionEngine().execute(
            order,
            account,
            100,
            datetime.now(timezone.utc),
        )


def test_risk_approval_is_bound_to_exact_order() -> None:
    account, order, approval = _approved_order()
    tampered_order = replace(order, quantity=order.quantity + 1)

    with pytest.raises(SafetyError, match="does not match order"):
        PaperExecutionEngine().execute(
            tampered_order,
            account,
            100,
            datetime.now(timezone.utc),
            approval=approval,
        )


def test_risk_approval_can_only_be_consumed_once() -> None:
    account, order, approval = _approved_order()
    engine = PaperExecutionEngine(fee_rate=0, slippage_bps=0)

    engine.execute(
        order,
        account,
        100,
        datetime.now(timezone.utc),
        approval=approval,
    )

    with pytest.raises(SafetyError, match="already been consumed"):
        engine.execute(
            order,
            account,
            100,
            datetime.now(timezone.utc),
            approval=approval,
        )


def test_expired_risk_approval_is_rejected() -> None:
    account, order, approval = _approved_order()
    expired = replace(
        approval,
        issued_at=datetime.now(timezone.utc) - timedelta(seconds=2),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    with pytest.raises(SafetyError, match="expired"):
        PaperExecutionEngine().execute(
            order,
            account,
            100,
            datetime.now(timezone.utc),
            approval=expired,
        )
