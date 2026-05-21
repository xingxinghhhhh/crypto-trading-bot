from datetime import datetime, timezone

import pandas as pd

from crypto_bot.strategy.moving_average_cross import MovingAverageCrossStrategy
from crypto_bot.strategy.signals import SignalSide


def test_moving_average_cross_emits_buy_on_bullish_cross():
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=6, freq="1min", tz="UTC"),
            "open": [10, 10, 10, 10, 10, 20],
            "high": [10, 10, 10, 10, 10, 20],
            "low": [10, 10, 10, 10, 10, 20],
            "close": [10, 10, 10, 10, 10, 20],
            "volume": [1, 1, 1, 1, 1, 1],
        }
    )
    strategy = MovingAverageCrossStrategy(fast_window=2, slow_window=3)

    signal = strategy.generate_signal("BTC/USDT", bars, datetime.now(timezone.utc))

    assert signal.side == SignalSide.BUY
    assert signal.symbol == "BTC/USDT"
    assert "bullish_cross" in signal.reason


def test_moving_average_cross_holds_when_not_enough_bars():
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=2, freq="1min", tz="UTC"),
            "open": [10, 11],
            "high": [10, 11],
            "low": [10, 11],
            "close": [10, 11],
            "volume": [1, 1],
        }
    )
    strategy = MovingAverageCrossStrategy(fast_window=2, slow_window=3)

    signal = strategy.generate_signal("BTC/USDT", bars, datetime.now(timezone.utc))

    assert signal.side == SignalSide.HOLD
    assert "insufficient_bars" in signal.reason


def test_vectorized_moving_average_signals_match_iterative_signals():
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=10, freq="1min", tz="UTC"),
            "open": [10, 10, 10, 11, 12, 11, 10, 9, 10, 11],
            "high": [10, 10, 10, 11, 12, 11, 10, 9, 10, 11],
            "low": [10, 10, 10, 11, 12, 11, 10, 9, 10, 11],
            "close": [10, 10, 10, 11, 12, 11, 10, 9, 10, 11],
            "volume": [1] * 10,
        }
    )
    strategy = MovingAverageCrossStrategy(fast_window=2, slow_window=3)

    vectorized = strategy.generate_signals("BTC/USDT", bars)
    iterative = [
        strategy.generate_signal("BTC/USDT", bars.iloc[: index + 1], row["timestamp"])
        for index, row in bars.iterrows()
    ]

    assert [signal.side for signal in vectorized] == [signal.side for signal in iterative]
    assert [signal.reason for signal in vectorized] == [signal.reason for signal in iterative]
