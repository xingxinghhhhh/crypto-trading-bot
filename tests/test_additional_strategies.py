from datetime import datetime, timezone

import pandas as pd

from crypto_bot.strategy.bollinger_mean_reversion import BollingerMeanReversionStrategy
from crypto_bot.strategy.ema_pullback import EmaPullbackStrategy
from crypto_bot.strategy.rsi_mean_reversion import RsiMeanReversionStrategy
from crypto_bot.strategy.signals import SignalSide


def test_rsi_mean_reversion_generates_buy_and_sell():
    buy_bars = _bars([100, 98, 96, 94, 92, 90, 88, 86, 84])
    sell_bars = _bars([84, 86, 88, 90, 92, 94, 96, 98, 100])
    strategy = RsiMeanReversionStrategy(rsi_window=3, buy_threshold=30, sell_threshold=60)

    assert strategy.generate_signal("BTC/USDT", buy_bars, datetime.now(timezone.utc)).side == SignalSide.BUY
    assert strategy.generate_signal("BTC/USDT", sell_bars, datetime.now(timezone.utc)).side == SignalSide.SELL


def test_bollinger_mean_reversion_generates_buy_and_sell():
    buy_bars = _bars([10, 10, 10, 10, 7])
    sell_bars = _bars([10, 10, 10, 9, 10])
    strategy = BollingerMeanReversionStrategy(window=5, num_std=1.0)

    assert strategy.generate_signal("BTC/USDT", buy_bars, datetime.now(timezone.utc)).side == SignalSide.BUY
    assert strategy.generate_signal("BTC/USDT", sell_bars, datetime.now(timezone.utc)).side == SignalSide.SELL


def test_ema_pullback_generates_buy_and_sell():
    buy_bars = _bars([10, 10.2, 10.4, 10.6, 10.8, 10.45, 10.9])
    sell_bars = _bars([10, 10.2, 10.4, 10.6, 10.8, 10.7, 10.1])
    strategy = EmaPullbackStrategy(trend_ema_window=5, pullback_ema_window=3)

    assert strategy.generate_signal("BTC/USDT", buy_bars, datetime.now(timezone.utc)).side == SignalSide.BUY
    assert strategy.generate_signal("BTC/USDT", sell_bars, datetime.now(timezone.utc)).side == SignalSide.SELL


def _bars(closes):
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC"),
            "open": closes,
            "high": [value + 0.1 for value in closes],
            "low": [value - 0.1 for value in closes],
            "close": closes,
            "volume": [100] * len(closes),
        }
    )
