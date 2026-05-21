from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from crypto_bot.strategy.signals import Signal, SignalSide


@dataclass(frozen=True)
class RegimeFilterSettings:
    enabled: bool = False
    min_moving_average_slope: float | None = None
    min_trend_strength: float | None = None
    max_range_bound_score: float | None = None
    max_window_drawdown_pct: float | None = None
    min_price_above_slow_ma_pct: float | None = None
    min_volume_change_pct: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RegimeFilterDecision:
    approved: bool
    reason: str
    features: dict[str, float | None]


class RegimeFilter:
    def __init__(self, settings: RegimeFilterSettings) -> None:
        self.settings = settings

    def evaluate(self, signal: Signal, bars: pd.DataFrame) -> RegimeFilterDecision:
        if not self.settings.enabled:
            return RegimeFilterDecision(True, "filter_disabled", {})
        if signal.side == SignalSide.HOLD:
            return RegimeFilterDecision(True, "hold_signal", {})
        features = calculate_regime_features(bars.tail(720))
        checks = [
            ("min_moving_average_slope", self.settings.min_moving_average_slope, _lt),
            ("min_trend_strength", self.settings.min_trend_strength, _lt),
            ("max_range_bound_score", self.settings.max_range_bound_score, _gt),
            ("max_window_drawdown_pct", self.settings.max_window_drawdown_pct, _gt),
            ("min_price_above_slow_ma_pct", self.settings.min_price_above_slow_ma_pct, _lt),
            ("min_volume_change_pct", self.settings.min_volume_change_pct, _lt),
        ]
        for reason, threshold, fails in checks:
            if threshold is None:
                continue
            feature_name = _FEATURE_BY_SETTING[reason]
            value = features.get(feature_name)
            if value is None or fails(float(value), float(threshold)):
                return RegimeFilterDecision(False, reason, features)
        return RegimeFilterDecision(True, "passed", features)

    def snapshot(self) -> dict:
        return self.settings.to_dict()


def calculate_regime_features(bars: pd.DataFrame) -> dict[str, float | None]:
    if bars.empty:
        return {
            "window_return_pct": None,
            "volatility_pct": None,
            "max_drawdown_pct": None,
            "trend_strength": None,
            "moving_average_slope": None,
            "price_above_slow_ma_pct": None,
            "range_bound_score": None,
            "volume_change_pct": None,
        }
    ordered = bars.sort_values("timestamp").reset_index(drop=True)
    first_close = float(ordered["close"].iloc[0])
    last_close = float(ordered["close"].iloc[-1])
    first_volume = float(ordered["volume"].iloc[0])
    window_return_pct = _pct_change(first_close, last_close)
    close_returns = ordered["close"].astype(float).pct_change().dropna() * 100
    volatility_pct = round(float(close_returns.std(ddof=0)), 10) if not close_returns.empty else 0.0
    moving_average = ordered["close"].astype(float).rolling(window=min(30, len(ordered)), min_periods=1).mean()
    moving_average_slope = _pct_change(float(moving_average.iloc[0]), float(moving_average.iloc[-1]))
    price_above_slow_ma_pct = round(float((ordered["close"] > moving_average).mean() * 100), 10)
    high_low_range_pct = _pct_change(first_close, float(ordered["high"].max()), float(ordered["low"].min()))
    abs_return = None if window_return_pct is None else abs(window_return_pct)
    range_bound_score = None if high_low_range_pct is None else round(high_low_range_pct / max(abs_return or 0.0, 0.01), 10)
    return {
        "window_return_pct": window_return_pct,
        "volatility_pct": volatility_pct,
        "max_drawdown_pct": _max_drawdown_pct(ordered["close"].astype(float)),
        "trend_strength": None if volatility_pct == 0 or abs_return is None else round(abs_return / volatility_pct, 10),
        "moving_average_slope": moving_average_slope,
        "price_above_slow_ma_pct": price_above_slow_ma_pct,
        "range_bound_score": range_bound_score,
        "volume_change_pct": _pct_change(first_volume, float(ordered["volume"].iloc[-1])),
    }


def _pct_change(base: float, value: float, low_value: float | None = None) -> float | None:
    if base == 0:
        return None
    if low_value is None:
        return round(((value - base) / base) * 100, 10)
    return round(((value - low_value) / base) * 100, 10)


def _max_drawdown_pct(closes: pd.Series) -> float:
    peak = closes.cummax()
    drawdown = ((closes - peak) / peak) * 100
    return round(abs(float(drawdown.min())), 10)


def _lt(value: float, threshold: float) -> bool:
    return value < threshold


def _gt(value: float, threshold: float) -> bool:
    return value > threshold


_FEATURE_BY_SETTING = {
    "min_moving_average_slope": "moving_average_slope",
    "min_trend_strength": "trend_strength",
    "max_range_bound_score": "range_bound_score",
    "max_window_drawdown_pct": "max_drawdown_pct",
    "min_price_above_slow_ma_pct": "price_above_slow_ma_pct",
    "min_volume_change_pct": "volume_change_pct",
}
