from __future__ import annotations

from datetime import datetime

import pandas as pd

from crypto_bot.strategy.base import Strategy
from crypto_bot.strategy.signals import Signal, SignalSide


class EmaPullbackStrategy(Strategy):
    def __init__(self, trend_ema_window: int, pullback_ema_window: int) -> None:
        if trend_ema_window <= 0 or pullback_ema_window <= 0:
            raise ValueError("EMA windows must be positive")
        if pullback_ema_window >= trend_ema_window:
            raise ValueError("pullback_ema_window must be smaller than trend_ema_window")
        self.trend_ema_window = trend_ema_window
        self.pullback_ema_window = pullback_ema_window

    def generate_signal(self, symbol: str, bars: pd.DataFrame, timestamp: datetime) -> Signal:
        if len(bars) < self.trend_ema_window + 1:
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp)
        if "close" not in bars.columns or bars["close"].tail(self.trend_ema_window + 1).isna().any():
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=timestamp)
        close = bars["close"].astype(float)
        trend_ema = close.ewm(span=self.trend_ema_window, adjust=False).mean()
        pullback_ema = close.ewm(span=self.pullback_ema_window, adjust=False).mean()
        return _signal(symbol, timestamp, close, trend_ema, pullback_ema, len(close) - 1)

    def generate_signals(self, symbol: str, bars: pd.DataFrame) -> list[Signal]:
        if "timestamp" not in bars.columns:
            raise ValueError("bars must contain timestamp column")
        if "close" not in bars.columns:
            return [
                Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=row["timestamp"])
                for _, row in bars.iterrows()
            ]
        close = bars["close"].astype(float)
        trend_ema = close.ewm(span=self.trend_ema_window, adjust=False).mean()
        pullback_ema = close.ewm(span=self.pullback_ema_window, adjust=False).mean()
        signals = []
        for index, row in bars.iterrows():
            timestamp = row["timestamp"]
            if index + 1 < self.trend_ema_window + 1:
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp))
            elif close.iloc[index - self.trend_ema_window : index + 1].isna().any():
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_close_data", timestamp=timestamp))
            else:
                signals.append(_signal(symbol, timestamp, close, trend_ema, pullback_ema, index))
        return signals


def _signal(
    symbol: str,
    timestamp: datetime,
    close: pd.Series,
    trend_ema: pd.Series,
    pullback_ema: pd.Series,
    index: int,
) -> Signal:
    current_close = float(close.iloc[index])
    current_trend = float(trend_ema.iloc[index])
    current_pullback = float(pullback_ema.iloc[index])
    previous_close = float(close.iloc[index - 1])
    previous_pullback = float(pullback_ema.iloc[index - 1])
    if current_close < current_pullback or current_close < current_trend:
        return Signal(symbol=symbol, side=SignalSide.SELL, reason="ema_pullback_exit", timestamp=timestamp)
    if current_close > current_trend and previous_close <= previous_pullback and current_close > current_pullback:
        return Signal(symbol=symbol, side=SignalSide.BUY, reason="ema_pullback_reclaim", timestamp=timestamp)
    return Signal(symbol=symbol, side=SignalSide.HOLD, reason="ema_pullback_neutral", timestamp=timestamp)
