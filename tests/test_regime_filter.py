from datetime import datetime

import pandas as pd

from crypto_bot.backtest.engine import BacktestEngine
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.portfolio.account import Account
from crypto_bot.regime_filter import RegimeFilter, RegimeFilterSettings
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


class CountingRiskManager(RiskManager):
    def __init__(self, settings: RiskSettings) -> None:
        super().__init__(settings)
        self.evaluate_count = 0

    def evaluate(self, *args, **kwargs):
        self.evaluate_count += 1
        return super().evaluate(*args, **kwargs)


def test_regime_filter_disabled_does_not_block_signal():
    filter_ = RegimeFilter(RegimeFilterSettings(enabled=False, min_moving_average_slope=100))

    decision = filter_.evaluate(Signal("BTC/USDT", SignalSide.BUY, "buy", _bars().iloc[-1]["timestamp"]), _bars())

    assert decision.approved is True
    assert decision.reason == "filter_disabled"


def test_regime_filter_min_moving_average_slope_blocks_signal():
    filter_ = RegimeFilter(RegimeFilterSettings(enabled=True, min_moving_average_slope=1))

    decision = filter_.evaluate(Signal("BTC/USDT", SignalSide.BUY, "buy", _bars(down=True).iloc[-1]["timestamp"]), _bars(down=True))

    assert decision.approved is False
    assert decision.reason == "min_moving_average_slope"


def test_filter_reject_does_not_enter_risk_manager_and_is_counted():
    bars = _bars(down=True)
    risk_manager = CountingRiskManager(RiskSettings(min_bars_required=1))
    engine = BacktestEngine(
        strategy=SequenceStrategy([SignalSide.BUY, SignalSide.BUY, SignalSide.BUY]),
        risk_manager=risk_manager,
        execution_engine=PaperExecutionEngine(fee_rate=0, slippage_bps=0),
        account=Account(initial_cash=1000),
        regime_filter=RegimeFilter(RegimeFilterSettings(enabled=True, min_moving_average_slope=1)),
    )

    result = engine.run("BTC/USDT", bars)

    assert risk_manager.evaluate_count == 0
    assert result.fills == []
    assert result.metrics.filter_reject_count == 3
    assert result.metrics.filter_reject_reason_distribution == {"min_moving_average_slope": 3}
    assert result.metrics.rejected_order_count == 0
    assert result.metrics.risk_reject_reason_distribution == {}


def test_filter_passes_signals_to_risk_manager_and_reports_snapshot():
    bars = _bars()
    risk_manager = CountingRiskManager(RiskSettings(min_bars_required=1))
    engine = BacktestEngine(
        strategy=SequenceStrategy([SignalSide.BUY, SignalSide.SELL, SignalSide.HOLD]),
        risk_manager=risk_manager,
        execution_engine=PaperExecutionEngine(fee_rate=0, slippage_bps=0),
        account=Account(initial_cash=1000),
        regime_filter=RegimeFilter(RegimeFilterSettings(enabled=True, min_moving_average_slope=-1)),
    )

    result = engine.run("BTC/USDT", bars)

    assert risk_manager.evaluate_count == 2
    assert result.metrics.filter_enabled is True
    assert result.metrics.filter_config_snapshot["min_moving_average_slope"] == -1
    assert result.metrics.trades_after_filter == 2


def _bars(down: bool = False):
    closes = [10, 11, 12] if not down else [12, 11, 10]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [100, 100, 100],
        }
    )
