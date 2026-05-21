from __future__ import annotations

from datetime import datetime

import pandas as pd

from crypto_bot.strategy.base import Strategy
from crypto_bot.strategy.signals import Signal, SignalSide


class DonchianBreakoutStrategy(Strategy):
    def __init__(
        self,
        entry_window: int,
        exit_window: int,
        atr_window: int,
        atr_multiplier: float,
    ) -> None:
        if entry_window <= 0 or exit_window <= 0 or atr_window <= 0:
            raise ValueError("donchian and ATR windows must be positive")
        if exit_window >= entry_window:
            raise ValueError("exit_window must be smaller than entry_window")
        if atr_multiplier <= 0:
            raise ValueError("atr_multiplier must be positive")
        self.entry_window = entry_window
        self.exit_window = exit_window
        self.atr_window = atr_window
        self.atr_multiplier = atr_multiplier
        self._in_position = False
        self._highest_close_since_entry: float | None = None

    def generate_signal(self, symbol: str, bars: pd.DataFrame, timestamp: datetime) -> Signal:
        signal = self._signal_for_window(symbol, bars, timestamp)
        self._update_state(signal, bars)
        return signal

    def generate_signals(self, symbol: str, bars: pd.DataFrame) -> list[Signal]:
        if "timestamp" not in bars.columns:
            raise ValueError("bars must contain timestamp column")
        missing = [column for column in ["high", "low", "close"] if column not in bars.columns]
        if missing:
            return [
                Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_ohlc_data", timestamp=row["timestamp"])
                for _, row in bars.iterrows()
            ]
        high = bars["high"].astype(float)
        low = bars["low"].astype(float)
        close = bars["close"].astype(float)
        previous_entry_high = high.rolling(self.entry_window).max().shift(1)
        previous_exit_low = low.rolling(self.exit_window).min().shift(1)
        atr = _atr_series(high, low, close, self.atr_window)
        required = max(self.entry_window, self.exit_window, self.atr_window) + 1
        in_position = False
        highest_close_since_entry: float | None = None
        signals: list[Signal] = []
        for index, row in bars.iterrows():
            timestamp = row["timestamp"]
            current_close = float(close.iloc[index])
            if index + 1 < required:
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp))
                continue
            if bars[["high", "low", "close"]].iloc[index - required + 1 : index + 1].isna().any().any():
                signals.append(Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_ohlc_data", timestamp=timestamp))
                continue

            atr_value = atr.iloc[index]
            if in_position and highest_close_since_entry is not None and not pd.isna(atr_value):
                stop_price = highest_close_since_entry - self.atr_multiplier * float(atr_value)
                if current_close < stop_price:
                    signal = Signal(symbol=symbol, side=SignalSide.SELL, reason="atr_trailing_stop", timestamp=timestamp)
                    in_position = False
                    highest_close_since_entry = None
                    signals.append(signal)
                    continue

            if current_close > float(previous_entry_high.iloc[index]):
                signal = Signal(symbol=symbol, side=SignalSide.BUY, reason="donchian_entry_breakout", timestamp=timestamp)
                in_position = True
                highest_close_since_entry = current_close
            elif current_close < float(previous_exit_low.iloc[index]):
                signal = Signal(symbol=symbol, side=SignalSide.SELL, reason="donchian_exit_breakdown", timestamp=timestamp)
                in_position = False
                highest_close_since_entry = None
            else:
                signal = Signal(symbol=symbol, side=SignalSide.HOLD, reason="no_donchian_breakout", timestamp=timestamp)
                if in_position:
                    highest_close_since_entry = (
                        current_close
                        if highest_close_since_entry is None
                        else max(highest_close_since_entry, current_close)
                    )
            signals.append(signal)
        return signals

    def _signal_for_window(self, symbol: str, bars: pd.DataFrame, timestamp: datetime) -> Signal:
        required = max(self.entry_window, self.exit_window, self.atr_window) + 1
        if len(bars) < required:
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="insufficient_bars", timestamp=timestamp)
        missing = [column for column in ["high", "low", "close"] if column not in bars.columns]
        if missing:
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_ohlc_data", timestamp=timestamp)
        recent = bars[["high", "low", "close"]].astype(float)
        if recent.tail(required).isna().any().any():
            return Signal(symbol=symbol, side=SignalSide.HOLD, reason="missing_ohlc_data", timestamp=timestamp)

        close = float(recent["close"].iloc[-1])
        atr = _atr(recent, self.atr_window)
        if self._in_position and self._highest_close_since_entry is not None and atr is not None:
            stop_price = self._highest_close_since_entry - self.atr_multiplier * atr
            if close < stop_price:
                return Signal(symbol=symbol, side=SignalSide.SELL, reason="atr_trailing_stop", timestamp=timestamp)

        previous_highest = float(recent["high"].iloc[-self.entry_window - 1 : -1].max())
        previous_lowest = float(recent["low"].iloc[-self.exit_window - 1 : -1].min())
        if close > previous_highest:
            return Signal(symbol=symbol, side=SignalSide.BUY, reason="donchian_entry_breakout", timestamp=timestamp)
        if close < previous_lowest:
            return Signal(symbol=symbol, side=SignalSide.SELL, reason="donchian_exit_breakdown", timestamp=timestamp)
        return Signal(symbol=symbol, side=SignalSide.HOLD, reason="no_donchian_breakout", timestamp=timestamp)

    def _update_state(self, signal: Signal, bars: pd.DataFrame) -> None:
        close = float(bars["close"].iloc[-1])
        if signal.side == SignalSide.BUY:
            self._in_position = True
            self._highest_close_since_entry = close
        elif signal.side == SignalSide.SELL:
            self._in_position = False
            self._highest_close_since_entry = None
        elif self._in_position:
            self._highest_close_since_entry = (
                close
                if self._highest_close_since_entry is None
                else max(self._highest_close_since_entry, close)
            )


def _atr(bars: pd.DataFrame, window: int) -> float | None:
    if len(bars) < window + 1:
        return None
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    close = bars["close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = true_range.tail(window).mean()
    return None if pd.isna(value) else float(value)


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window).mean()
