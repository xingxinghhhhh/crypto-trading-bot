from datetime import datetime, timezone

import pandas as pd

from crypto_bot.strategy.donchian_breakout import DonchianBreakoutStrategy
from crypto_bot.strategy.signals import SignalSide


def test_donchian_breakout_emits_buy_on_entry_breakout():
    bars = _bars(
        closes=[10, 10, 10, 10, 12],
        highs=[10, 10.5, 11, 11.5, 12],
        lows=[9, 9, 9, 9, 10],
    )
    strategy = DonchianBreakoutStrategy(entry_window=4, exit_window=2, atr_window=3, atr_multiplier=2.0)

    signal = strategy.generate_signal("BTC/USDT", bars, datetime.now(timezone.utc))

    assert signal.side == SignalSide.BUY
    assert signal.reason == "donchian_entry_breakout"


def test_donchian_breakout_emits_sell_on_exit_breakdown():
    bars = _bars(
        closes=[12, 12, 11, 10, 8],
        highs=[13, 13, 12, 11, 9],
        lows=[11, 10, 9, 8.5, 8],
    )
    strategy = DonchianBreakoutStrategy(entry_window=4, exit_window=3, atr_window=3, atr_multiplier=2.0)

    signal = strategy.generate_signal("BTC/USDT", bars, datetime.now(timezone.utc))

    assert signal.side == SignalSide.SELL
    assert signal.reason == "donchian_exit_breakdown"


def test_donchian_breakout_emits_sell_on_atr_trailing_stop_after_entry():
    bars = _bars(
        closes=[10, 10, 10, 10, 12, 13, 10],
        highs=[10, 10.5, 11, 11.5, 12, 13, 11],
        lows=[9.5, 9.5, 9.5, 9.5, 11, 12, 9],
    )
    strategy = DonchianBreakoutStrategy(entry_window=4, exit_window=2, atr_window=3, atr_multiplier=1.0)

    signals = strategy.generate_signals("BTC/USDT", bars)

    assert signals[4].side == SignalSide.BUY
    assert signals[-1].side == SignalSide.SELL
    assert signals[-1].reason == "atr_trailing_stop"


def test_vectorized_donchian_signals_match_iterative_signals():
    bars = _bars(
        closes=[10, 10, 10, 10, 12, 13, 10, 9, 14],
        highs=[10, 10.5, 11, 11.5, 12, 13, 11, 10, 14],
        lows=[9.5, 9.5, 9.5, 9.5, 11, 12, 9, 8, 13],
    )
    strategy = DonchianBreakoutStrategy(entry_window=4, exit_window=2, atr_window=3, atr_multiplier=1.0)

    vectorized = strategy.generate_signals("BTC/USDT", bars)
    iterative = [
        strategy.generate_signal("BTC/USDT", bars.iloc[: index + 1], row["timestamp"])
        for index, row in bars.iterrows()
    ]

    assert [signal.side for signal in vectorized] == [signal.side for signal in iterative]
    assert [signal.reason for signal in vectorized] == [signal.reason for signal in iterative]


def _bars(closes, highs, lows):
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC"),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100] * len(closes),
        }
    )
