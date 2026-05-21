from __future__ import annotations

from datetime import datetime

import pandas as pd

from crypto_bot.strategy.base import Strategy
from crypto_bot.strategy.signals import Signal, SignalSide


class BollingerMeanReversionStrategy(Strategy):
    def __init__(self, window: int, num_std: float) -> None:
        if window <= 1:
            raise ValueError("window must be greater than 1")
        if num_std <= 0:
            raise ValueError("num_std must be positive")
        self.window = window
        self.num_std = num_std

    def generate_signal(self, symbol: str, bars: pd.DataFrame, timestamp: datetime) -> Signal:
        if len(bars) < self.window:
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp)
        if "close" not in bars.columns or bars["close"].tail(self.window).isna().any():
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=timestamp)
        close = bars["close"].astype(float)
        middle = close.tail(self.window).mean()
        std = close.tail(self.window).std(ddof=0)
        latest = float(close.iloc[-1])
        lower = middle - self.num_std * std
        upper = middle + self.num_std * std
        return _signal(symbol, timestamp, latest, middle, lower, upper)

    def generate_signals(self, symbol: str, bars: pd.DataFrame) -> list[Signal]:
        if "timestamp" not in bars.columns:
            raise ValueError("bars must contain timestamp column")
        if "close" not in bars.columns:
            return [
                Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=row["timestamp"])
                for _, row in bars.iterrows()
            ]
        close = bars["close"].astype(float)
        middle = close.rolling(self.window).mean()
        std = close.rolling(self.window).std(ddof=0)
        lower = middle - self.num_std * std
        upper = middle + self.num_std * std
        signals = []
        for index, row in bars.iterrows():
            timestamp = row["timestamp"]
            if index + 1 < self.window:
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp))
            elif close.iloc[index - self.window + 1 : index + 1].isna().any():
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=timestamp))
            else:
                signals.append(
                    _signal(
                        symbol,
                        timestamp,
                        float(close.iloc[index]),
                        float(middle.iloc[index]),
                        float(lower.iloc[index]),
                        float(upper.iloc[index]),
                    )
                )
        return signals


def _signal(symbol: str, timestamp: datetime, close: float, middle: float, lower: float, upper: float) -> Signal:
    if close < lower:
        return Signal(symbol=symbol, side=SignalSide.BUY, reason="bollinger_lower_band", timestamp=timestamp)
    if close >= middle or close > upper:
        return Signal(symbol=symbol, side=SignalSide.SELL, reason="bollinger_mean_reversion_exit", timestamp=timestamp)
    return Signal(symbol=symbol, side=SignalSide.HOLD, reason="inside_bollinger_band", timestamp=timestamp)
