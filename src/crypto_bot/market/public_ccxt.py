from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from crypto_bot.errors import MarketDataError

LOGGER = logging.getLogger(__name__)
SUPPORTED_EXCHANGES = {"binance", "okx"}
OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class PublicOhlcvBatch:
    raw_rows: list[list[float]]
    frame: pd.DataFrame


class PublicMarketDataProvider:
    def __init__(
        self,
        exchange_id: str,
        exchange_factory: Callable[[dict[str, Any]], Any] | None = None,
        *,
        use_environment_proxy: bool = False,
    ) -> None:
        if exchange_id not in SUPPORTED_EXCHANGES:
            raise MarketDataError(f"unsupported_exchange:{exchange_id}")
        self.exchange_id = exchange_id
        exchange_config = {
            "enableRateLimit": True,
            "requests_trust_env": use_environment_proxy,
        }
        self.exchange = exchange_factory(exchange_config) if exchange_factory else self._build_exchange(
            exchange_id,
            exchange_config,
        )

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> pd.DataFrame:
        return self.fetch_ohlcv_batch(symbol, timeframe=timeframe, limit=limit).frame

    def fetch_ohlcv_batch(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
    ) -> PublicOhlcvBatch:
        try:
            rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as exc:  # ccxt raises exchange-specific NetworkError/ExchangeError subclasses.
            LOGGER.warning(
                "public_ohlcv_fetch_skipped exchange=%s symbol=%s timeframe=%s reason=fetch_ohlcv_failed",
                self.exchange_id,
                symbol,
                timeframe,
            )
            raise MarketDataError(f"fetch_ohlcv_failed:{exc}") from exc

        frame = self._normalize_ohlcv(rows)
        LOGGER.info(
            "public_ohlcv_fetched exchange=%s symbol=%s timeframe=%s bar_count=%s",
            self.exchange_id,
            symbol,
            timeframe,
            len(frame),
        )
        return PublicOhlcvBatch(raw_rows=rows, frame=frame)

    def _build_exchange(self, exchange_id: str, exchange_config: dict[str, Any]) -> Any:
        import ccxt

        exchange_cls = getattr(ccxt, exchange_id, None)
        if exchange_cls is None:
            raise MarketDataError(f"unsupported_exchange:{exchange_id}")
        return exchange_cls(exchange_config)

    def _normalize_ohlcv(self, rows: list[list[float]]) -> pd.DataFrame:
        if not rows:
            raise MarketDataError("empty_ohlcv")
        for row in rows:
            if len(row) < 6:
                raise MarketDataError("invalid_ohlcv_row")

        frame = pd.DataFrame([row[:6] for row in rows], columns=OHLCV_COLUMNS)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True, errors="coerce")
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        if frame[OHLCV_COLUMNS].isna().any().any():
            raise MarketDataError("missing_ohlcv_values")
        if not frame["timestamp"].is_monotonic_increasing:
            raise MarketDataError("abnormal_ohlcv_timestamp")
        if frame["timestamp"].duplicated().any():
            raise MarketDataError("abnormal_ohlcv_timestamp")

        now = datetime.now(timezone.utc)
        if (frame["timestamp"] > pd.Timestamp(now)).any():
            raise MarketDataError("abnormal_ohlcv_timestamp")
        return frame.reset_index(drop=True)
