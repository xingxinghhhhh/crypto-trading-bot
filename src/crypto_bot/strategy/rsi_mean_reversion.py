from __future__ import annotations

from datetime import datetime

import pandas as pd

from crypto_bot.strategy.base import Strategy
from crypto_bot.strategy.signals import Signal, SignalSide


class RsiMeanReversionStrategy(Strategy):
    def __init__(self, rsi_window: int, buy_threshold: float, sell_threshold: float) -> None:
        if rsi_window <= 0:
            raise ValueError("rsi_window must be positive")
        if buy_threshold >= sell_threshold:
            raise ValueError("buy_threshold must be smaller than sell_threshold")
        self.rsi_window = rsi_window
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    def generate_signal(self, symbol: str, bars: pd.DataFrame, timestamp: datetime) -> Signal:
        if len(bars) < self.rsi_window + 1:
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp)
        if "close" not in bars.columns or bars["close"].tail(self.rsi_window + 1).isna().any():
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=timestamp)
        rsi_value = _rsi(bars["close"].astype(float), self.rsi_window).iloc[-1]
        if pd.isna(rsi_value):
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="rsi_not_ready", timestamp=timestamp)
        if float(rsi_value) < self.buy_threshold:
            return Signal(symbol=symbol, side=SignalSide.BUY, reason="rsi_oversold", timestamp=timestamp)
        if float(rsi_value) > self.sell_threshold:
            return Signal(symbol=symbol, side=SignalSide.SELL, reason="rsi_mean_reversion_exit", timestamp=timestamp)
        return Signal(symbol=symbol, side=SignalSide.HOLD, reason="rsi_neutral", timestamp=timestamp)

    def generate_signals(self, symbol: str, bars: pd.DataFrame) -> list[Signal]:
        if "timestamp" not in bars.columns:
            raise ValueError("bars must contain timestamp column")
        if "close" not in bars.columns:
            return [
                Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=row["timestamp"])
                for _, row in bars.iterrows()
            ]
        close = bars["close"].astype(float)
        rsi_values = _rsi(close, self.rsi_window)
        signals = []
        for index, row in bars.iterrows():
            timestamp = row["timestamp"]
            if index + 1 < self.rsi_window + 1:
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp))
            elif close.iloc[index - self.rsi_window : index + 1].isna().any():
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=timestamp))
            elif pd.isna(rsi_values.iloc[index]):
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="rsi_not_ready", timestamp=timestamp))
            elif float(rsi_values.iloc[index]) < self.buy_threshold:
                signals.append(Signal(symbol=symbol, side=SignalSide.BUY, reason="rsi_oversold", timestamp=timestamp))
            elif float(rsi_values.iloc[index]) > self.sell_threshold:
                signals.append(Signal(symbol=symbol, side=SignalSide.SELL, reason="rsi_mean_reversion_exit", timestamp=timestamp))
            else:
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="rsi_neutral", timestamp=timestamp))
        return signals


def _rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.rolling(window).mean()
    average_loss = losses.rolling(window).mean()
    relative_strength = average_gain / average_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + relative_strength))
    return rsi.fillna(100)
