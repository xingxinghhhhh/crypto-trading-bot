from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True)
class RealtimeBarGateResult:
    bars: pd.DataFrame
    reason: str | None = None


def gate_realtime_bars(
    bars: pd.DataFrame,
    timeframe: str,
    now: datetime,
    *,
    require_closed_bars: bool = True,
    max_staleness_seconds: float | None = None,
    max_clock_skew_seconds: float = 5.0,
) -> RealtimeBarGateResult:
    if bars is None or bars.empty or "timestamp" not in bars.columns:
        return RealtimeBarGateResult(bars, "no_market_data")

    duration = timeframe_duration(timeframe)
    timestamps = pd.to_datetime(bars["timestamp"], utc=True, errors="coerce")
    current_time = pd.Timestamp(now)
    if current_time.tzinfo is None:
        current_time = current_time.tz_localize("UTC")
    else:
        current_time = current_time.tz_convert("UTC")

    if timestamps.isna().any():
        return RealtimeBarGateResult(bars, "abnormal_ohlcv_timestamp")
    future_limit = current_time + pd.Timedelta(seconds=max_clock_skew_seconds)
    if (timestamps > future_limit).any():
        return RealtimeBarGateResult(bars, "clock_skew_exceeded")

    gated = bars.copy()
    if require_closed_bars:
        closed_mask = timestamps + duration <= current_time
        gated = gated.loc[closed_mask].reset_index(drop=True)
        timestamps = timestamps.loc[closed_mask].reset_index(drop=True)
        if gated.empty:
            return RealtimeBarGateResult(gated, "no_closed_market_data")

    if len(timestamps) > 1:
        intervals = timestamps.diff().dropna()
        if (intervals != duration).any():
            return RealtimeBarGateResult(gated, "market_data_gap")

    if max_staleness_seconds is not None:
        last_close_time = timestamps.iloc[-1] + duration
        staleness = (current_time - last_close_time).total_seconds()
        if staleness > max_staleness_seconds:
            return RealtimeBarGateResult(gated, "stale_market_data")

    return RealtimeBarGateResult(gated)


def timeframe_duration(timeframe: str) -> pd.Timedelta:
    match = re.fullmatch(r"([1-9][0-9]*)([mhdw])", timeframe.strip().lower())
    if match is None:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    value = int(match.group(1))
    unit = match.group(2)
    units = {
        "m": "minutes",
        "h": "hours",
        "d": "days",
        "w": "weeks",
    }
    return pd.Timedelta(**{units[unit]: value})
