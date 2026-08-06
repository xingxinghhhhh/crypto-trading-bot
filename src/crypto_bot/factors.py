from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from crypto_bot.errors import MarketDataError
from crypto_bot.market.csv_data import REQUIRED_COLUMNS


@dataclass(frozen=True)
class FactorSpec:
    family: str
    window: int

    @property
    def name(self) -> str:
        return f"{self.family}_{self.window}"


DEFAULT_FACTOR_SPECS = (
    FactorSpec("momentum", 4),
    FactorSpec("momentum", 12),
    FactorSpec("mean_reversion", 20),
    FactorSpec("low_volatility", 20),
    FactorSpec("volume_momentum", 20),
    FactorSpec("trend_quality", 20),
)


def compute_factor_frame(
    bars: pd.DataFrame,
    specs: tuple[FactorSpec, ...] = DEFAULT_FACTOR_SPECS,
) -> pd.DataFrame:
    normalized = _normalize_bars(bars)
    close = normalized["close"]
    volume = normalized["volume"]
    returns = close.pct_change(fill_method=None)
    builders: dict[str, Callable[[int], pd.Series]] = {
        "momentum": lambda window: close.pct_change(window, fill_method=None),
        "mean_reversion": lambda window: -_rolling_zscore(close, window),
        "low_volatility": lambda window: -returns.rolling(window).std(ddof=0),
        "volume_momentum": lambda window: volume / volume.rolling(window).mean() - 1.0,
        "trend_quality": lambda window: (
            returns.rolling(window).mean()
            / returns.rolling(window).std(ddof=0)
        ),
    }
    output = pd.DataFrame({"timestamp": normalized["timestamp"]})
    seen: set[str] = set()
    for spec in specs:
        if spec.window <= 1:
            raise MarketDataError(f"factor_window_must_be_greater_than_one:{spec.name}")
        if spec.family not in builders:
            raise MarketDataError(f"unsupported_factor_family:{spec.family}")
        if spec.name in seen:
            raise MarketDataError(f"duplicate_factor_name:{spec.name}")
        seen.add(spec.name)
        output[spec.name] = builders[spec.family](spec.window).replace(
            [np.inf, -np.inf],
            np.nan,
        )
    return output


def compute_forward_returns(
    bars: pd.DataFrame,
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    normalized = _normalize_bars(bars)
    output = pd.DataFrame({"timestamp": normalized["timestamp"]})
    for horizon in horizons:
        if horizon <= 0:
            raise MarketDataError("factor_horizons_must_be_positive")
        output[f"forward_return_{horizon}"] = (
            normalized["close"].shift(-horizon) / normalized["close"] - 1.0
        )
    return output


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars is None or bars.empty:
        raise MarketDataError("factor_bars_empty")
    if list(bars.columns) != REQUIRED_COLUMNS:
        raise MarketDataError("factor_invalid_columns")
    normalized = bars[REQUIRED_COLUMNS].copy()
    normalized["timestamp"] = pd.to_datetime(
        normalized["timestamp"],
        utc=True,
        errors="coerce",
    )
    for column in ["open", "high", "low", "close", "volume"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if normalized[REQUIRED_COLUMNS].isna().any().any():
        raise MarketDataError("factor_missing_values")
    if not normalized["timestamp"].is_monotonic_increasing:
        raise MarketDataError("factor_timestamps_not_ascending")
    if normalized["timestamp"].duplicated().any():
        raise MarketDataError("factor_duplicate_timestamp")
    if (normalized["close"] <= 0).any():
        raise MarketDataError("factor_close_must_be_positive")
    if (normalized["volume"] < 0).any():
        raise MarketDataError("factor_volume_must_be_non_negative")
    return normalized.reset_index(drop=True)


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window).mean()
    standard_deviation = series.rolling(window).std(ddof=0)
    return (series - mean) / standard_deviation
