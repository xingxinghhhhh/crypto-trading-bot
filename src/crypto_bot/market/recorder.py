from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from crypto_bot.config import AppConfig
from crypto_bot.errors import MarketDataError
from crypto_bot.market.public_ccxt import PublicMarketDataProvider, PublicOhlcvBatch
from crypto_bot.market.realtime_gate import gate_realtime_bars


@dataclass(frozen=True)
class MarketRecordingResult:
    raw_path: Path
    normalized_path: Path
    manifest_path: Path
    raw_row_count: int
    closed_row_count: int


@dataclass(frozen=True)
class MarketRecorderRunResult:
    recordings: list[MarketRecordingResult]
    errors: list[str]
    attempt_errors: list[str]
    attempt_count: int
    retry_count: int
    retry_delays_seconds: list[float]


def run_market_recorder(
    config: AppConfig,
    output_dir: str | Path,
    *,
    provider=None,
    max_iterations: int = 1,
    interval_seconds: float = 0.0,
    max_retries: int = 0,
    initial_backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 60.0,
    now_provider=None,
    sleep_fn=time.sleep,
) -> MarketRecorderRunResult:
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")
    if initial_backoff_seconds < 0 or max_backoff_seconds < 0:
        raise ValueError("backoff seconds must be non-negative")
    if max_backoff_seconds < initial_backoff_seconds:
        raise ValueError("max_backoff_seconds must be greater than or equal to initial_backoff_seconds")

    iterations = max(1, int(max_iterations))
    sleep_seconds = max(0.0, float(interval_seconds))
    market_provider = provider or PublicMarketDataProvider(
        config.market_data.exchange,
        use_environment_proxy=config.market_data.use_environment_proxy,
    )
    recordings: list[MarketRecordingResult] = []
    errors: list[str] = []
    attempt_errors: list[str] = []
    attempt_count = 0
    retry_count = 0
    retry_delays_seconds: list[float] = []
    for iteration in range(iterations):
        for attempt in range(max_retries + 1):
            attempt_count += 1
            try:
                recordings.append(
                    record_public_market_data(
                        config,
                        output_dir,
                        provider=market_provider,
                        now=now_provider() if now_provider else None,
                    )
                )
            except (MarketDataError, OSError, ValueError) as exc:
                message = str(exc)
                attempt_errors.append(message)
                if attempt >= max_retries:
                    errors.append(message)
                    break
                delay = min(
                    initial_backoff_seconds * (2**attempt),
                    max_backoff_seconds,
                )
                retry_count += 1
                retry_delays_seconds.append(delay)
                if delay:
                    sleep_fn(delay)
            else:
                break
        if iteration + 1 < iterations and sleep_seconds:
            sleep_fn(sleep_seconds)
    return MarketRecorderRunResult(
        recordings=recordings,
        errors=errors,
        attempt_errors=attempt_errors,
        attempt_count=attempt_count,
        retry_count=retry_count,
        retry_delays_seconds=retry_delays_seconds,
    )


def record_public_market_data(
    config: AppConfig,
    output_dir: str | Path,
    *,
    provider=None,
    now: datetime | None = None,
) -> MarketRecordingResult:
    if config.market_data.source != "ccxt_public":
        raise MarketDataError("market recorder requires market_data.source=ccxt_public")

    received_at = now or datetime.now(timezone.utc)
    symbol = config.market_data.symbols[0]
    timeframe = config.market_data.timeframe
    market_provider = provider or PublicMarketDataProvider(
        config.market_data.exchange,
        use_environment_proxy=config.market_data.use_environment_proxy,
    )
    batch = _fetch_batch(
        market_provider,
        symbol,
        timeframe,
        config.market_data.limit,
    )
    gate_result = gate_realtime_bars(
        batch.frame,
        timeframe,
        received_at,
        require_closed_bars=config.market_data.require_closed_bars,
        max_staleness_seconds=config.market_data.max_staleness_seconds,
        max_clock_skew_seconds=config.market_data.max_clock_skew_seconds,
    )
    if gate_result.reason:
        raise MarketDataError(gate_result.reason)

    root = (
        Path(output_dir)
        / config.market_data.exchange
        / symbol.replace("/", "_").replace(":", "_")
        / timeframe
        / received_at.strftime("%Y-%m-%d")
    )
    root.mkdir(parents=True, exist_ok=True)
    stamp = received_at.strftime("%Y%m%dT%H%M%S%fZ")
    raw_path = root / f"raw_{stamp}.json"
    normalized_path = root / f"closed_{stamp}.csv"
    manifest_path = root / f"manifest_{stamp}.json"

    raw_payload = {
        "exchange": config.market_data.exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "received_at": received_at.isoformat(),
        "rows": batch.raw_rows,
    }
    _write_text_atomically(
        raw_path,
        json.dumps(raw_payload, ensure_ascii=False, separators=(",", ":")),
    )
    _write_text_atomically(
        normalized_path,
        gate_result.bars.to_csv(index=False, lineterminator="\n"),
    )

    manifest = {
        "exchange": config.market_data.exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "received_at": received_at.isoformat(),
        "raw_row_count": len(batch.raw_rows),
        "closed_row_count": len(gate_result.bars),
        "last_closed_bar": pd.Timestamp(gate_result.bars.iloc[-1]["timestamp"]).isoformat(),
        "raw_file": raw_path.name,
        "raw_sha256": _sha256(raw_path),
        "normalized_file": normalized_path.name,
        "normalized_sha256": _sha256(normalized_path),
    }
    _write_text_atomically(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2),
    )
    return MarketRecordingResult(
        raw_path=raw_path,
        normalized_path=normalized_path,
        manifest_path=manifest_path,
        raw_row_count=len(batch.raw_rows),
        closed_row_count=len(gate_result.bars),
    )


def _fetch_batch(provider, symbol: str, timeframe: str, limit: int) -> PublicOhlcvBatch:
    if hasattr(provider, "fetch_ohlcv_batch"):
        return provider.fetch_ohlcv_batch(symbol, timeframe=timeframe, limit=limit)
    frame = provider.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    raw_rows = json.loads(frame.to_json(orient="values", date_format="iso"))
    return PublicOhlcvBatch(raw_rows=raw_rows, frame=frame)


def _write_text_atomically(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"market recording already exists: {path}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
