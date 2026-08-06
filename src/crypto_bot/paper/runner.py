from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd

from crypto_bot.backtest.engine import BacktestResult
from crypto_bot.backtest.metrics import calculate_backtest_metrics
from crypto_bot.config import AppConfig
from crypto_bot.errors import SafetyError
from crypto_bot.execution.models import Fill
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.logging import redact_secret_text
from crypto_bot.market.csv_data import REQUIRED_COLUMNS, load_ohlcv_csv
from crypto_bot.market.public_ccxt import PublicMarketDataProvider
from crypto_bot.market.realtime_gate import gate_realtime_bars
from crypto_bot.portfolio.account import Account
from crypto_bot.risk.manager import RiskDecision, RiskManager, RiskSettings
from crypto_bot.storage.repositories import SQLiteStorage
from crypto_bot.strategy.base import Strategy
from crypto_bot.strategy.factory import create_strategy
from crypto_bot.strategy.signals import Signal, SignalSide

LOGGER = logging.getLogger(__name__)

def run_paper_session(
    config: AppConfig,
    market_data_provider=None,
    *,
    max_iterations: int | None = None,
    interval_seconds: float | None = None,
    now_provider=None,
) -> BacktestResult:
    iterations = max(1, int(max_iterations or 1))
    if iterations > 1 and config.strategy.readiness != "paper_ready":
        raise SafetyError("long-running paper requires strategy.readiness=paper_ready")
    sleep_seconds = max(0.0, float(interval_seconds or 0.0))
    run_id = str(uuid4())
    symbol = _paper_symbol(config)
    strategy = create_strategy(config.strategy)
    risk_manager = RiskManager(RiskSettings(**config.risk.__dict__))
    execution_engine = PaperExecutionEngine(config.execution.fee_rate, config.execution.slippage_bps)
    storage = SQLiteStorage(config.storage.url)
    account = storage.restore_paper_account(config.initial_cash, symbol)

    fills: list[Fill] = []
    equity_curve: list[float] = [account.equity()]
    total_fees = 0.0
    total_slippage = 0.0

    storage.record_runtime_heartbeat(
        "paper",
        run_id,
        "started",
        detail=f"symbol={symbol} iterations={iterations}",
    )
    try:
        for iteration in range(1, iterations + 1):
            iteration_fills = _run_paper_iteration(
                config=config,
                run_id=run_id,
                iteration=iteration,
                symbol=symbol,
                account=account,
                strategy=strategy,
                risk_manager=risk_manager,
                execution_engine=execution_engine,
                storage=storage,
                market_data_provider=market_data_provider,
                now_provider=now_provider,
            )
            fills.extend(iteration_fills)
            total_fees += sum(fill.fee for fill in iteration_fills)
            total_slippage += sum(fill.slippage * fill.quantity for fill in iteration_fills)
            latest_close = _latest_close_from_snapshot(storage, run_id, iteration)
            prices = {symbol: latest_close} if latest_close is not None else None
            equity_curve.append(account.equity(prices))
            storage.record_runtime_heartbeat(
                "paper",
                run_id,
                "healthy",
                detail=f"iteration={iteration}",
            )
            if iteration < iterations and sleep_seconds:
                time.sleep(sleep_seconds)
        storage.record_runtime_heartbeat(
            "paper",
            run_id,
            "completed",
            detail=f"iterations={iterations}",
        )
    except Exception as exc:
        storage.rollback_paper_iteration()
        storage.record_runtime_heartbeat(
            "paper",
            run_id,
            "failed",
            detail=str(exc),
        )
        raise
    finally:
        storage.close()

    metrics = calculate_backtest_metrics(equity_curve, [], total_fees, total_slippage)
    return BacktestResult(metrics=metrics, fills=fills, equity_curve=equity_curve)


def _run_paper_iteration(
    *,
    config: AppConfig,
    run_id: str,
    iteration: int,
    symbol: str,
    account: Account,
    strategy: Strategy,
    risk_manager: RiskManager,
    execution_engine: PaperExecutionEngine,
    storage: SQLiteStorage,
    market_data_provider=None,
    now_provider=None,
) -> list[Fill]:
    now = now_provider() if now_provider else datetime.now(timezone.utc)
    source = config.market_data.source
    exchange = config.market_data.exchange
    timeframe = config.market_data.timeframe
    fills: list[Fill] = []

    kill_switch = storage.kill_switch_status()
    if kill_switch["engaged"]:
        _record_snapshot(
            storage=storage,
            config=config,
            run_id=run_id,
            iteration=iteration,
            timestamp=now,
            symbol=symbol,
            account=account,
            source=source,
            exchange=exchange,
            timeframe=timeframe,
            order_status="skipped",
            reason="kill_switch_engaged",
            skip_reason="kill_switch_engaged",
            error_message=str(kill_switch["reason"]),
        )
        LOGGER.warning(
            "paper_trade_skipped symbol=%s reason=kill_switch_engaged",
            symbol,
        )
        return fills

    try:
        bars = _load_paper_bars(config, symbol, market_data_provider)
    except Exception as exc:
        _record_snapshot(
            storage=storage,
            config=config,
            run_id=run_id,
            iteration=iteration,
            timestamp=now,
            symbol=symbol,
            account=account,
            source=source,
            exchange=exchange,
            timeframe=timeframe,
            order_status="skipped",
            reason="market_data_error",
            skip_reason="market_data_error",
            error_message=str(exc),
        )
        LOGGER.warning("paper_trade_skipped symbol=%s reason=%s", symbol, redact_secret_text(str(exc)))
        return fills

    realtime_error: str | None = None
    if source == "ccxt_public":
        try:
            gate_result = gate_realtime_bars(
                bars,
                timeframe,
                now,
                require_closed_bars=config.market_data.require_closed_bars,
                max_staleness_seconds=config.market_data.max_staleness_seconds,
                max_clock_skew_seconds=config.market_data.max_clock_skew_seconds,
            )
        except ValueError:
            realtime_error = "unsupported_timeframe"
        else:
            bars = gate_result.bars
            realtime_error = gate_result.reason

    validation_error = realtime_error or _validate_bars(
        bars,
        min_bars_required=config.risk.min_bars_required,
    )
    latest = bars.iloc[-1] if validation_error is None and not bars.empty else None
    close = float(latest["close"]) if latest is not None else None
    bar_timestamp = _timestamp_to_str(latest["timestamp"]) if latest is not None else None

    if validation_error:
        _record_snapshot(
            storage=storage,
            config=config,
            run_id=run_id,
            iteration=iteration,
            timestamp=now,
            symbol=symbol,
            account=account,
            source=source,
            exchange=exchange,
            timeframe=timeframe,
            bar_timestamp=bar_timestamp,
            close=close,
            order_status="skipped",
            reason=validation_error,
            skip_reason=validation_error,
        )
        if bar_timestamp:
            _record_processed_bar(storage, config, symbol, run_id, iteration, now, bar_timestamp)
        LOGGER.warning("paper_trade_skipped symbol=%s reason=%s", symbol, validation_error)
        return fills

    assert latest is not None
    assert close is not None
    assert bar_timestamp is not None

    if storage.has_processed_bar(source, exchange, symbol, timeframe, bar_timestamp):
        _record_snapshot(
            storage=storage,
            config=config,
            run_id=run_id,
            iteration=iteration,
            timestamp=now,
            symbol=symbol,
            account=account,
            source=source,
            exchange=exchange,
            timeframe=timeframe,
            bar_timestamp=bar_timestamp,
            close=close,
            order_status="skipped",
            reason="duplicate_bar",
            skip_reason="duplicate_bar",
        )
        LOGGER.info("paper_trade_skipped symbol=%s reason=duplicate_bar bar_timestamp=%s", symbol, bar_timestamp)
        return fills

    signal: Signal | None = None
    risk_decision: RiskDecision | None = None
    order_status = "no_order"
    reason = "no_signal"
    skip_reason: str | None = None
    error_message: str | None = None

    storage.begin_paper_iteration()
    try:
        signal = strategy.generate_signal(symbol, bars, latest["timestamp"].to_pydatetime())
        storage.record_signal(signal, close, commit=False)
    except Exception as exc:
        order_status = "skipped"
        reason = "strategy_error"
        skip_reason = "strategy_error"
        error_message = str(exc)
        LOGGER.warning("paper_trade_skipped symbol=%s reason=strategy_error error=%s", symbol, redact_secret_text(str(exc)))
    else:
        risk_decision = risk_manager.evaluate_protective_exit(signal, account, close)
        if signal.side == SignalSide.HOLD and risk_decision is None:
            reason = signal.reason
            order_status = "no_order"
        else:
            try:
                if risk_decision is None:
                    bar_day = latest["timestamp"].date()
                    current_equity = account.equity({symbol: close})
                    starting_equity = storage.starting_equity_for_day(bar_day, account.initial_cash)
                    daily_loss_pct = (
                        max(0.0, (starting_equity - current_equity) / starting_equity)
                        if starting_equity > 0
                        else 0.0
                    )
                    risk_decision = risk_manager.evaluate(
                        signal=signal,
                        account=account,
                        market_price=close,
                        bars_count=len(bars),
                        missing_data=False,
                        abnormal_move=_has_abnormal_latest_move(bars, risk_manager.settings.abnormal_move_pct),
                        trades_today=storage.count_fills_for_day(bar_day),
                        daily_loss_pct=daily_loss_pct,
                    )
                storage.record_risk_event(signal, risk_decision, commit=False)
            except Exception as exc:
                order_status = "skipped"
                reason = "risk_error"
                skip_reason = "risk_error"
                error_message = str(exc)
                LOGGER.warning("paper_trade_skipped symbol=%s reason=risk_error error=%s", symbol, redact_secret_text(str(exc)))
            else:
                reason = risk_decision.reason
                if risk_decision.approved and risk_decision.order is not None:
                    storage.record_order(risk_decision.order, commit=False)
                    fill = execution_engine.execute(
                        risk_decision.order,
                        account,
                        close,
                        latest["timestamp"].to_pydatetime(),
                        approval=risk_decision.approval,
                    )
                    storage.record_fill(fill, commit=False)
                    fills.append(fill)
                    order_status = "filled"
                    reason = risk_decision.order.reason
                else:
                    order_status = "risk_rejected"

    equity = account.equity({symbol: close})
    storage.record_balance_snapshot(
        equity,
        account.cash or 0.0,
        latest["timestamp"].to_pydatetime(),
        commit=False,
    )
    _record_snapshot(
        storage=storage,
        config=config,
        run_id=run_id,
        iteration=iteration,
        timestamp=now,
        symbol=symbol,
        account=account,
        source=source,
        exchange=exchange,
        timeframe=timeframe,
        bar_timestamp=bar_timestamp,
        close=close,
        signal=signal.side.value if signal else None,
        risk_decision=risk_decision.reason if risk_decision else None,
        order_status=order_status,
        equity=equity,
        reason=reason,
        skip_reason=skip_reason,
        error_message=error_message,
        commit=False,
    )
    _record_processed_bar(
        storage,
        config,
        symbol,
        run_id,
        iteration,
        now,
        bar_timestamp,
        commit=False,
    )
    storage.commit_paper_iteration()
    return fills


def _load_paper_bars(config: AppConfig, symbol: str, market_data_provider=None) -> pd.DataFrame:
    if config.market_data.source == "csv":
        bars = load_ohlcv_csv(config.market_data.csv_path)
        LOGGER.info(
            "paper_market_data_loaded source=csv symbol=%s timeframe=%s bar_count=%s",
            symbol,
            config.market_data.timeframe,
            len(bars),
        )
        return bars

    provider = market_data_provider or PublicMarketDataProvider(
        config.market_data.exchange,
        use_environment_proxy=config.market_data.use_environment_proxy,
    )
    return provider.fetch_ohlcv(
        symbol=symbol,
        timeframe=config.market_data.timeframe,
        limit=config.market_data.limit,
    )


def _validate_bars(bars: pd.DataFrame, min_bars_required: int) -> str | None:
    if bars is None or bars.empty:
        return "no_market_data"
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in bars.columns]
    if missing_columns:
        return "missing_ohlcv_values"
    if len(bars) < min_bars_required:
        return "insufficient_bars"
    if bars[REQUIRED_COLUMNS].isna().any().any():
        return "missing_ohlcv_values"
    timestamps = pd.to_datetime(bars["timestamp"], utc=True, errors="coerce")
    if timestamps.isna().any() or not timestamps.is_monotonic_increasing or timestamps.duplicated().any():
        return "abnormal_ohlcv_timestamp"
    if (timestamps > pd.Timestamp(datetime.now(timezone.utc))).any():
        return "abnormal_ohlcv_timestamp"
    return None


def _record_snapshot(
    *,
    storage: SQLiteStorage,
    config: AppConfig,
    run_id: str,
    iteration: int,
    timestamp: datetime,
    symbol: str,
    account: Account,
    source: str,
    exchange: str,
    timeframe: str,
    order_status: str,
    reason: str,
    bar_timestamp: str | None = None,
    close: float | None = None,
    signal: str | None = None,
    risk_decision: str | None = None,
    equity: float | None = None,
    skip_reason: str | None = None,
    error_message: str | None = None,
    commit: bool = True,
) -> None:
    position = account.get_position(symbol)
    snapshot_equity = equity if equity is not None else account.equity({symbol: close} if close is not None else None)
    storage.record_paper_state_snapshot(
        {
            "run_id": run_id,
            "iteration": iteration,
            "timestamp": timestamp.isoformat(),
            "source": source,
            "exchange": exchange,
            "timeframe": timeframe,
            "symbol": symbol,
            "bar_timestamp": bar_timestamp,
            "close": close,
            "signal": signal,
            "risk_decision": risk_decision,
            "order_status": order_status,
            "cash": account.cash or 0.0,
            "position_quantity": position.quantity,
            "position_avg_price": position.avg_price,
            "account_realized_pnl": account.realized_pnl,
            "position_realized_pnl": position.realized_pnl,
            "peak_equity": account.peak_equity or snapshot_equity,
            "equity": snapshot_equity,
            "reason": reason,
            "skip_reason": skip_reason,
            "error_message": error_message,
        },
        commit=commit,
    )


def _record_processed_bar(
    storage: SQLiteStorage,
    config: AppConfig,
    symbol: str,
    run_id: str,
    iteration: int,
    processed_at: datetime,
    bar_timestamp: str,
    *,
    commit: bool = True,
) -> None:
    storage.record_processed_bar(
        source=config.market_data.source,
        exchange=config.market_data.exchange,
        symbol=symbol,
        timeframe=config.market_data.timeframe,
        bar_timestamp=bar_timestamp,
        run_id=run_id,
        iteration=iteration,
        processed_at=processed_at.isoformat(),
        commit=commit,
    )


def _latest_close_from_snapshot(storage: SQLiteStorage, run_id: str, iteration: int) -> float | None:
    row = storage.connection.execute(
        """
        SELECT close FROM paper_state_snapshots
        WHERE run_id = ? AND iteration = ?
        ORDER BY id DESC LIMIT 1
        """,
        (run_id, iteration),
    ).fetchone()
    return None if row is None or row[0] is None else float(row[0])


def _has_abnormal_latest_move(bars: pd.DataFrame, threshold: float) -> bool:
    if len(bars) < 2:
        return False
    previous_close = float(bars.iloc[-2]["close"])
    latest_close = float(bars.iloc[-1]["close"])
    if previous_close <= 0:
        return True
    return abs(latest_close - previous_close) / previous_close >= threshold


def _timestamp_to_str(value) -> str:
    return pd.Timestamp(value).isoformat()


def _paper_symbol(config: AppConfig) -> str:
    if config.market_data.source == "ccxt_public":
        return config.market_data.symbols[0]
    return config.symbols[0]
