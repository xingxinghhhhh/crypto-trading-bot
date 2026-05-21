from datetime import datetime

import pandas as pd

from crypto_bot.backtest.engine import BacktestEngine
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.portfolio.account import Account
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.strategy.base import Strategy
from crypto_bot.strategy.signals import Signal, SignalSide


class SequenceStrategy(Strategy):
    def __init__(self, sides):
        self.sides = list(sides)

    def generate_signal(self, symbol: str, bars: pd.DataFrame, timestamp: datetime) -> Signal:
        index = len(bars) - 1
        side = self.sides[index] if index < len(self.sides) else SignalSide.HOLD
        return Signal(symbol=symbol, side=side, reason=f"signal_{side.value}", timestamp=timestamp)


def test_backtest_resets_max_trades_per_day_on_new_date():
    bars = _bars(
        [
            "2026-01-01T00:00:00Z",
            "2026-01-01T01:00:00Z",
            "2026-01-02T00:00:00Z",
        ]
    )
    engine = BacktestEngine(
        strategy=SequenceStrategy([SignalSide.BUY, SignalSide.SELL, SignalSide.SELL]),
        risk_manager=RiskManager(RiskSettings(max_trades_per_day=1, min_bars_required=1)),
        execution_engine=PaperExecutionEngine(fee_rate=0, slippage_bps=0),
        account=Account(initial_cash=1000),
    )

    result = engine.run("BTC/USDT", bars)

    assert len(result.fills) == 2
    assert result.metrics.risk_reject_reason_distribution == {"max_trades_per_day": 1}


def test_repeated_buy_while_position_open_is_not_classified_as_max_trades_per_day():
    bars = _bars(
        [
            "2026-01-01T00:00:00Z",
            "2026-01-01T01:00:00Z",
            "2026-01-01T02:00:00Z",
        ]
    )
    engine = BacktestEngine(
        strategy=SequenceStrategy([SignalSide.BUY, SignalSide.BUY, SignalSide.BUY]),
        risk_manager=RiskManager(
            RiskSettings(
                allow_pyramiding=False,
                max_trades_per_day=1,
                min_bars_required=1,
            )
        ),
        execution_engine=PaperExecutionEngine(fee_rate=0, slippage_bps=0),
        account=Account(initial_cash=1000),
    )

    result = engine.run("BTC/USDT", bars)

    assert len(result.fills) == 1
    assert result.metrics.risk_reject_reason_distribution == {"pyramiding_not_allowed": 2}


def test_sell_without_position_is_not_classified_as_max_trades_per_day():
    bars = _bars(
        [
            "2026-01-01T00:00:00Z",
            "2026-01-01T01:00:00Z",
        ]
    )
    engine = BacktestEngine(
        strategy=SequenceStrategy([SignalSide.SELL, SignalSide.SELL]),
        risk_manager=RiskManager(RiskSettings(max_trades_per_day=0, min_bars_required=1)),
        execution_engine=PaperExecutionEngine(fee_rate=0, slippage_bps=0),
        account=Account(initial_cash=1000),
    )

    result = engine.run("BTC/USDT", bars)

    assert result.fills == []
    assert result.metrics.risk_reject_reason_distribution == {"no_position_to_sell": 2}


def _bars(timestamps):
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps, utc=True),
            "open": [10] * len(timestamps),
            "high": [10] * len(timestamps),
            "low": [10] * len(timestamps),
            "close": [10] * len(timestamps),
            "volume": [1] * len(timestamps),
        }
    )
