from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time

import pandas as pd

from crypto_bot.config import AppConfig
from crypto_bot.errors import MarketDataError
from crypto_bot.logging import redact_secret_text
from crypto_bot.market.public_ccxt import PublicMarketDataProvider
from crypto_bot.market.realtime_gate import gate_realtime_bars
from crypto_bot.portfolio.account import Account
from crypto_bot.risk.manager import RiskManager, RiskSettings
from crypto_bot.storage.repositories import SQLiteStorage
from crypto_bot.strategy.factory import create_strategy


@dataclass(frozen=True)
class ShadowResult:
    symbol: str
    bar_timestamp: str
    signal_side: str | None
    risk_reason: str | None
    proposed_order: bool
    duplicate: bool = False


@dataclass(frozen=True)
class ShadowSessionResult:
    observations: list[ShadowResult]
    errors: list[str]


def run_shadow_session(
    config: AppConfig,
    *,
    market_data_provider=None,
    max_iterations: int = 1,
    interval_seconds: float = 0.0,
    now_provider=None,
) -> ShadowSessionResult:
    iterations = max(1, int(max_iterations))
    sleep_seconds = max(0.0, float(interval_seconds))
    provider = market_data_provider or PublicMarketDataProvider(
        config.market_data.exchange,
        use_environment_proxy=config.market_data.use_environment_proxy,
    )
    observations: list[ShadowResult] = []
    errors: list[str] = []
    for iteration in range(iterations):
        try:
            observations.append(
                run_shadow_once(
                    config,
                    market_data_provider=provider,
                    now=now_provider() if now_provider else None,
                )
            )
        except (MarketDataError, OSError, ValueError) as exc:
            errors.append(redact_secret_text(str(exc)))
        if iteration + 1 < iterations and sleep_seconds:
            time.sleep(sleep_seconds)
    return ShadowSessionResult(observations=observations, errors=errors)


def run_shadow_once(
    config: AppConfig,
    *,
    market_data_provider=None,
    now: datetime | None = None,
) -> ShadowResult:
    if config.market_data.source != "ccxt_public":
        raise MarketDataError("shadow mode requires market_data.source=ccxt_public")

    observed_at = now or datetime.now(timezone.utc)
    symbol = config.market_data.symbols[0]
    timeframe = config.market_data.timeframe
    provider = market_data_provider or PublicMarketDataProvider(
        config.market_data.exchange,
        use_environment_proxy=config.market_data.use_environment_proxy,
    )
    bars = provider.fetch_ohlcv(
        symbol=symbol,
        timeframe=timeframe,
        limit=config.market_data.limit,
    )
    gate_result = gate_realtime_bars(
        bars,
        timeframe,
        observed_at,
        require_closed_bars=config.market_data.require_closed_bars,
        max_staleness_seconds=config.market_data.max_staleness_seconds,
        max_clock_skew_seconds=config.market_data.max_clock_skew_seconds,
    )
    if gate_result.reason:
        raise MarketDataError(gate_result.reason)
    bars = gate_result.bars
    if len(bars) < config.risk.min_bars_required:
        raise MarketDataError("insufficient_bars")

    latest = bars.iloc[-1]
    bar_timestamp = pd.Timestamp(latest["timestamp"]).isoformat()
    storage = SQLiteStorage(config.storage.url)
    run_id = f"shadow:{config.strategy.name}:{bar_timestamp}"
    storage.record_runtime_heartbeat(
        "shadow",
        run_id,
        "started",
        detail=f"symbol={symbol}",
        timestamp=observed_at,
    )
    try:
        if storage.has_shadow_observation(
            config.market_data.exchange,
            timeframe,
            symbol,
            bar_timestamp,
            config.strategy.name,
        ):
            result = ShadowResult(
                symbol=symbol,
                bar_timestamp=bar_timestamp,
                signal_side=None,
                risk_reason=None,
                proposed_order=False,
                duplicate=True,
            )
            storage.record_runtime_heartbeat(
                "shadow",
                run_id,
                "completed",
                detail="duplicate=true",
                timestamp=observed_at,
            )
            return result

        strategy = create_strategy(config.strategy)
        signal = strategy.generate_signal(
            symbol,
            bars,
            pd.Timestamp(latest["timestamp"]).to_pydatetime(),
        )
        account = Account(config.initial_cash)
        risk_manager = RiskManager(RiskSettings(**config.risk.__dict__))
        close = float(latest["close"])
        risk_decision = risk_manager.evaluate(
            signal=signal,
            account=account,
            market_price=close,
            bars_count=len(bars),
            missing_data=False,
            abnormal_move=_has_abnormal_latest_move(
                bars,
                risk_manager.settings.abnormal_move_pct,
            ),
        )
        storage.record_signal(signal, close)
        storage.record_risk_event(signal, risk_decision)
        order = risk_decision.order
        storage.record_shadow_observation(
            {
                "observed_at": observed_at.isoformat(),
                "exchange": config.market_data.exchange,
                "timeframe": timeframe,
                "symbol": symbol,
                "bar_timestamp": bar_timestamp,
                "strategy_name": config.strategy.name,
                "signal_id": signal.id,
                "signal_side": signal.side.value,
                "signal_reason": signal.reason,
                "risk_approved": risk_decision.approved,
                "risk_reason": risk_decision.reason,
                "proposed_order_side": order.side.value if order else None,
                "proposed_quantity": order.quantity if order else None,
                "proposed_notional": order.notional if order else None,
            }
        )
        result = ShadowResult(
            symbol=symbol,
            bar_timestamp=bar_timestamp,
            signal_side=signal.side.value,
            risk_reason=risk_decision.reason,
            proposed_order=order is not None,
        )
        storage.record_runtime_heartbeat(
            "shadow",
            run_id,
            "completed",
            detail=f"signal={signal.side.value}",
            timestamp=observed_at,
        )
        return result
    except Exception as exc:
        storage.record_runtime_heartbeat(
            "shadow",
            run_id,
            "failed",
            detail=redact_secret_text(str(exc)),
            timestamp=observed_at,
        )
        raise
    finally:
        storage.close()


def _has_abnormal_latest_move(bars: pd.DataFrame, threshold: float) -> bool:
    if len(bars) < 2:
        return False
    previous_close = float(bars.iloc[-2]["close"])
    latest_close = float(bars.iloc[-1]["close"])
    if previous_close <= 0:
        return True
    return abs(latest_close - previous_close) / previous_close >= threshold
