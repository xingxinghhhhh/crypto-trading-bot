from __future__ import annotations

from datetime import datetime

import pandas as pd

from crypto_bot.strategy.base import Strategy
from crypto_bot.strategy.signals import Signal, SignalSide


class MovingAverageCrossStrategy(Strategy):
    def __init__(self, fast_window: int, slow_window: int) -> None:
        if fast_window <= 0 or slow_window <= 0:
            raise ValueError("moving average windows must be positive")
        if fast_window >= slow_window:
            raise ValueError("fast_window must be smaller than slow_window")
        self.fast_window = fast_window
        self.slow_window = slow_window

    def generate_signal(self, symbol: str, bars: pd.DataFrame, timestamp: datetime) -> Signal:
        min_required = self.slow_window + 1
        if len(bars) < min_required:
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp)
        if "close" not in bars.columns:
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=timestamp)

        recent_bars = bars.tail(min_required)
        if recent_bars["close"].isna().any():
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=timestamp)

        close = recent_bars["close"].astype(float)
        fast_ma = close.rolling(self.fast_window).mean()
        slow_ma = close.rolling(self.slow_window).mean()

        fast_prev, fast_now = fast_ma.iloc[-2], fast_ma.iloc[-1]
        slow_prev, slow_now = slow_ma.iloc[-2], slow_ma.iloc[-1]
        if pd.isna(fast_prev) or pd.isna(slow_prev) or pd.isna(fast_now) or pd.isna(slow_now):
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="moving_average_not_ready", timestamp=timestamp)

        if fast_prev <= slow_prev and fast_now > slow_now:
            return Signal(symbol=symbol, side=SignalSide.BUY, reason="bullish_cross", timestamp=timestamp)
        if fast_prev >= slow_prev and fast_now < slow_now:
            return Signal(symbol=symbol, side=SignalSide.SELL, reason="bearish_cross", timestamp=timestamp)
        return Signal(symbol=symbol, side=SignalSide.HOLD, reason="no_cross", timestamp=timestamp)

    def generate_signals(self, symbol: str, bars: pd.DataFrame) -> list[Signal]:
        if "timestamp" not in bars.columns:
            raise ValueError("bars must contain timestamp column")
        if "close" not in bars.columns:
            return [
                Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=row["timestamp"])
                for _, row in bars.iterrows()
            ]

        close = bars["close"].astype(float)
        fast_ma = close.rolling(self.fast_window).mean()
        slow_ma = close.rolling(self.slow_window).mean()
        min_required = self.slow_window + 1
        signals: list[Signal] = []
        for index, row in bars.iterrows():
            timestamp = row["timestamp"]
            if index + 1 < min_required:
                signals.append(
                    Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp)
                )
                continue
            recent_close = close.iloc[index - self.slow_window : index + 1]
            if recent_close.isna().any():
                signals.append(
                    Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=timestamp)
                )
                continue

            fast_prev, fast_now = fast_ma.iloc[index - 1], fast_ma.iloc[index]
            slow_prev, slow_now = slow_ma.iloc[index - 1], slow_ma.iloc[index]
            if pd.isna(fast_prev) or pd.isna(slow_prev) or pd.isna(fast_now) or pd.isna(slow_now):
                signals.append(
                    Signal(symbol=symbol, side=SignalSide.HOLD, reason="moving_average_not_ready", timestamp=timestamp)
                )
            elif fast_prev <= slow_prev and fast_now > slow_now:
                signals.append(
                    Signal(symbol=symbol, side=SignalSide.BUY, reason="bullish_cross", timestamp=timestamp)
                )
            elif fast_prev >= slow_prev and fast_now < slow_now:
                signals.append(
                    Signal(symbol=symbol, side=SignalSide.SELL, reason="bearish_cross", timestamp=timestamp)
                )
            else:
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="no_cross", timestamp=timestamp))
        return signals
