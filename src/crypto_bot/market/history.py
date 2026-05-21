from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from crypto_bot.config import AppConfig
from crypto_bot.errors import MarketDataError
from crypto_bot.market.csv_data import REQUIRED_COLUMNS, load_ohlcv_csv
from crypto_bot.market.public_ccxt import SUPPORTED_EXCHANGES

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryFetchResult:
    output_path: Path
    symbol: str
    fetched_rows: int
    written_rows: int


def fetch_history_to_csv(
    config: AppConfig,
    output: str | Path,
    exchange_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> HistoryFetchResult:
    market_config = config.market_data
    if market_config.source != "ccxt_public":
        raise MarketDataError("history_source_must_be_ccxt_public")
    if not market_config.since or not market_config.until:
        raise MarketDataError("history_since_until_required")

    symbol = market_config.symbols[0]
    output_path = Path(output)
    existing = _load_existing(output_path)
    start = _start_timestamp(market_config.since, existing, market_config.timeframe)
    until = _parse_timestamp(market_config.until)

    if start > until:
        if existing.empty:
            raise MarketDataError("empty_ohlcv")
        merged = _normalize_for_csv(existing)
        _write_csv_atomically(merged, output_path)
        return HistoryFetchResult(output_path, symbol, fetched_rows=0, written_rows=len(merged))

    exchange = _build_exchange(market_config.exchange, exchange_factory)
    fetched = _fetch_pages(
        exchange=exchange,
        symbol=symbol,
        timeframe=market_config.timeframe,
        start=start,
        until=until,
        limit=market_config.limit,
    )

    if existing.empty and fetched.empty:
        raise MarketDataError("empty_ohlcv")

    merged = _merge_frames(existing, fetched)
    _write_csv_atomically(merged, output_path)
    LOGGER.info(
        "history_csv_written exchange=%s symbol=%s timeframe=%s fetched_rows=%s written_rows=%s output=%s",
        market_config.exchange,
        symbol,
        market_config.timeframe,
        len(fetched),
        len(merged),
        output_path,
    )
    return HistoryFetchResult(output_path, symbol, fetched_rows=len(fetched), written_rows=len(merged))


def _build_exchange(exchange_id: str, exchange_factory: Callable[[dict[str, Any]], Any] | None) -> Any:
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise MarketDataError(f"unsupported_exchange:{exchange_id}")
    config = {"enableRateLimit": True}
    if exchange_factory:
        return exchange_factory(config)

    import ccxt

    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise MarketDataError(f"unsupported_exchange:{exchange_id}")
    return exchange_cls(config)


def _fetch_pages(
    exchange: Any,
    symbol: str,
    timeframe: str,
    start: pd.Timestamp,
    until: pd.Timestamp,
    limit: int,
) -> pd.DataFrame:
    if limit <= 0:
        raise MarketDataError("history_limit_must_be_positive")

    timeframe_ms = _timeframe_to_milliseconds(timeframe)
    since_ms = _timestamp_to_milliseconds(start)
    until_ms = _timestamp_to_milliseconds(until)
    pages: list[pd.DataFrame] = []

    while since_ms <= until_ms:
        try:
            rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
        except Exception as exc:
            LOGGER.error(
                "history_fetch_failed symbol=%s timeframe=%s since=%s reason=%s",
                symbol,
                timeframe,
                since_ms,
                exc,
            )
            raise MarketDataError(f"fetch_ohlcv_failed:{exc}") from exc

        if not rows:
            break

        page = _rows_to_frame(rows)
        pages.append(page)
        last_ms = _timestamp_to_milliseconds(page["timestamp"].max())
        next_since_ms = last_ms + timeframe_ms
        if next_since_ms <= since_ms:
            raise MarketDataError("abnormal_ohlcv_timestamp")
        since_ms = next_since_ms

    if not pages:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    fetched = _merge_frames(pd.DataFrame(columns=REQUIRED_COLUMNS), pd.concat(pages, ignore_index=True))
    fetched = fetched[
        (fetched["timestamp"] >= start)
        & (fetched["timestamp"] <= until)
    ].reset_index(drop=True)
    return fetched


def _rows_to_frame(rows: list[list[float]]) -> pd.DataFrame:
    for row in rows:
        if len(row) < 6:
            raise MarketDataError("invalid_ohlcv_row")
    frame = pd.DataFrame([row[:6] for row in rows], columns=REQUIRED_COLUMNS)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[REQUIRED_COLUMNS].isna().any().any():
        raise MarketDataError("missing_ohlcv_values")
    return frame


def _load_existing(output_path: Path) -> pd.DataFrame:
    if not output_path.exists():
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    return load_ohlcv_csv(output_path)


def _start_timestamp(since: str, existing: pd.DataFrame, timeframe: str) -> pd.Timestamp:
    configured = _parse_timestamp(since)
    if existing.empty:
        return configured
    next_existing = pd.Timestamp(existing["timestamp"].max()) + pd.Timedelta(
        milliseconds=_timeframe_to_milliseconds(timeframe)
    )
    return max(configured, next_existing)


def _parse_timestamp(value: str) -> pd.Timestamp:
    timestamp = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(timestamp):
        raise MarketDataError(f"invalid_timestamp:{value}")
    return pd.Timestamp(timestamp)


def _timestamp_to_milliseconds(timestamp: pd.Timestamp) -> int:
    return int(pd.Timestamp(timestamp).timestamp() * 1000)


def _timeframe_to_milliseconds(timeframe: str) -> int:
    unit = timeframe[-1]
    try:
        amount = int(timeframe[:-1])
    except ValueError as exc:
        raise MarketDataError(f"unsupported_timeframe:{timeframe}") from exc
    multipliers = {
        "m": 60_000,
        "h": 3_600_000,
        "d": 86_400_000,
    }
    if amount <= 0 or unit not in multipliers:
        raise MarketDataError(f"unsupported_timeframe:{timeframe}")
    return amount * multipliers[unit]


def _merge_frames(existing: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    merged = pd.concat([existing, fetched], ignore_index=True)
    if merged.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    return _normalize_for_csv(merged)


def _normalize_for_csv(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame[REQUIRED_COLUMNS].copy()
    normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if normalized[REQUIRED_COLUMNS].isna().any().any():
        raise MarketDataError("missing_ohlcv_values")
    normalized = normalized.drop_duplicates(subset=["timestamp"], keep="last")
    normalized = normalized.sort_values("timestamp").reset_index(drop=True)
    return normalized


def _write_csv_atomically(frame: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    csv_frame = frame[REQUIRED_COLUMNS].copy()
    csv_frame["timestamp"] = pd.to_datetime(csv_frame["timestamp"], utc=True).map(lambda item: item.isoformat())
    temporary_path = output_path.with_name(f".{output_path.name}.tmp")
    try:
        csv_frame.to_csv(temporary_path, index=False)
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
