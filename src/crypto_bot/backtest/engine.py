from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from crypto_bot.backtest.metrics import BacktestMetrics, calculate_backtest_metrics
from crypto_bot.backtest.records import EquityCurvePoint, RiskEventRecord, TradeRecord
from crypto_bot.execution.models import Fill
from crypto_bot.execution.paper_engine import PaperExecutionEngine
from crypto_bot.portfolio.account import Account
from crypto_bot.regime_filter import RegimeFilter
from crypto_bot.risk.manager import RiskManager
from crypto_bot.storage.repositories import SQLiteStorage
from crypto_bot.strategy.base import Strategy


@dataclass(frozen=True)
class BacktestResult:
    metrics: BacktestMetrics
    fills: list[Fill]
    equity_curve: list[float]
    trades: list[TradeRecord] = field(default_factory=list)
    equity_points: list[EquityCurvePoint] = field(default_factory=list)
    risk_events: list[RiskEventRecord] = field(default_factory=list)


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        risk_manager: RiskManager,
        execution_engine: PaperExecutionEngine,
        account: Account,
        storage: SQLiteStorage | None = None,
        regime_filter: RegimeFilter | None = None,
    ) -> None:
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.execution_engine = execution_engine
        self.account = account
        self.storage = storage
        self.regime_filter = regime_filter

    def run(self, symbol: str, bars: pd.DataFrame) -> BacktestResult:
        fills: list[Fill] = []
        equity_curve: list[float] = [self.account.equity()]
        trades: list[TradeRecord] = []
        equity_points: list[EquityCurvePoint] = []
        risk_events: list[RiskEventRecord] = []
        trade_pnls: list[float] = []
        total_fees = 0.0
        total_slippage = 0.0
        trades_today = 0
        current_trade_date = None
        day_start_equity = self.account.equity()
        exposure_bars = 0
        rejected_reasons: Counter[str] = Counter()
        filter_rejected_reasons: Counter[str] = Counter()
        passed_filter_signal_count = 0
        timestamps = [row.to_pydatetime() for row in bars["timestamp"]]
        precomputed_signals = (
            self.strategy.generate_signals(symbol, bars)
            if hasattr(self.strategy, "generate_signals")
            else None
        )

        previous_close: float | None = None
        for index in range(len(bars)):
            window = bars.iloc[: index + 1]
            row = bars.iloc[index]
            timestamp = row["timestamp"].to_pydatetime()
            trade_date = timestamp.date()
            close = float(row["close"])
            if trade_date != current_trade_date:
                current_trade_date = trade_date
                trades_today = 0
                day_start_equity = self.account.equity({symbol: close})
            missing_data = bool(row[["open", "high", "low", "close", "volume"]].isna().any())
            abnormal_move = False
            if previous_close and previous_close > 0:
                abnormal_move = abs(close - previous_close) / previous_close >= self.risk_manager.settings.abnormal_move_pct
            previous_close = close

            signal = (
                precomputed_signals[index]
                if precomputed_signals is not None
                else self.strategy.generate_signal(symbol, window, timestamp)
            )
            if self.storage:
                self.storage.record_signal(signal, close)

            protective_decision = self.risk_manager.evaluate_protective_exit(
                signal,
                self.account,
                close,
            )
            if signal.side.value == "hold" and protective_decision is None:
                position = self.account.get_position(symbol)
                if position.quantity > 0:
                    exposure_bars += 1
                equity = self.account.equity({symbol: close})
                equity_curve.append(equity)
                equity_points.append(
                    EquityCurvePoint(
                        timestamp=timestamp.isoformat(),
                        cash=self.account.cash or 0.0,
                        position_qty=position.quantity,
                        position_value=position.quantity * close,
                        equity=equity,
                        drawdown_pct=self.account.drawdown_pct({symbol: close}) * 100,
                        close_price=close,
                    )
                )
                if self.storage:
                    self.storage.record_balance_snapshot(equity, self.account.cash or 0.0, timestamp)
                continue

            if protective_decision is None and self.regime_filter and signal.side.value != "hold":
                filter_decision = self.regime_filter.evaluate(signal, window)
                if not filter_decision.approved:
                    filter_rejected_reasons[filter_decision.reason] += 1
                    position = self.account.get_position(symbol)
                    if position.quantity > 0:
                        exposure_bars += 1
                    equity = self.account.equity({symbol: close})
                    equity_curve.append(equity)
                    equity_points.append(
                        EquityCurvePoint(
                            timestamp=timestamp.isoformat(),
                            cash=self.account.cash or 0.0,
                            position_qty=position.quantity,
                            position_value=position.quantity * close,
                            equity=equity,
                            drawdown_pct=self.account.drawdown_pct({symbol: close}) * 100,
                            close_price=close,
                        )
                    )
                    if self.storage:
                        self.storage.record_balance_snapshot(equity, self.account.cash or 0.0, timestamp)
                    continue
                passed_filter_signal_count += 1
            elif signal.side.value != "hold":
                passed_filter_signal_count += 1

            current_equity = self.account.equity({symbol: close})
            daily_loss_pct = (
                max(0.0, (day_start_equity - current_equity) / day_start_equity)
                if day_start_equity > 0
                else 0.0
            )
            decision = protective_decision or self.risk_manager.evaluate(
                signal=signal,
                account=self.account,
                market_price=close,
                bars_count=len(window),
                missing_data=missing_data,
                abnormal_move=abnormal_move,
                trades_today=trades_today,
                daily_loss_pct=daily_loss_pct,
            )
            if self.storage:
                self.storage.record_risk_event(signal, decision)
            rejected = (not decision.approved) and signal.side.value != "hold"
            if rejected:
                rejected_reasons[decision.reason] += 1
            current_equity = self.account.equity({symbol: close})
            risk_events.append(
                RiskEventRecord(
                    timestamp=timestamp.isoformat(),
                    symbol=symbol,
                    approved=decision.approved,
                    rejected=rejected,
                    reason=decision.reason,
                    adjusted_size=decision.order.quantity if decision.order else 0.0,
                    equity=current_equity,
                    current_drawdown_pct=self.account.drawdown_pct({symbol: close}) * 100,
                )
            )

            if decision.approved and decision.order is not None:
                if self.storage:
                    self.storage.record_order(decision.order)
                before_realized = self.account.realized_pnl
                fill = self.execution_engine.execute(
                    decision.order,
                    self.account,
                    close,
                    timestamp,
                    approval=decision.approval,
                )
                fills.append(fill)
                trades_today += 1
                total_fees += fill.fee
                total_slippage += fill.slippage * fill.quantity
                realized_delta = self.account.realized_pnl - before_realized
                if realized_delta != 0:
                    trade_pnls.append(realized_delta)
                trades.append(
                    TradeRecord(
                        timestamp=fill.timestamp.isoformat(),
                        symbol=fill.symbol,
                        side=fill.side.value,
                        qty=fill.quantity,
                        price=fill.price,
                        fee=fill.fee,
                        slippage_cost=fill.slippage * fill.quantity,
                        realized_pnl=realized_delta,
                        reason=fill.reason,
                        source_signal_id=decision.order.signal_id,
                    )
                )
                if self.storage:
                    self.storage.record_fill(fill)

            position = self.account.get_position(symbol)
            if position.quantity > 0:
                exposure_bars += 1
            equity = self.account.equity({symbol: close})
            equity_curve.append(equity)
            equity_points.append(
                EquityCurvePoint(
                    timestamp=timestamp.isoformat(),
                    cash=self.account.cash or 0.0,
                    position_qty=position.quantity,
                    position_value=position.quantity * close,
                    equity=equity,
                    drawdown_pct=self.account.drawdown_pct({symbol: close}) * 100,
                    close_price=close,
                )
            )
            if self.storage:
                self.storage.record_balance_snapshot(equity, self.account.cash or 0.0, timestamp)

        exposure_time_pct = 0.0 if len(bars) == 0 else (exposure_bars / len(bars)) * 100
        metrics = calculate_backtest_metrics(
            equity_curve,
            trade_pnls,
            total_fees,
            total_slippage,
            timestamps=timestamps,
            exposure_time_pct=exposure_time_pct,
            rejected_order_count=sum(rejected_reasons.values()),
            risk_reject_reason_distribution=dict(rejected_reasons),
            filter_reject_count=sum(filter_rejected_reasons.values()),
            filter_reject_reason_distribution=dict(filter_rejected_reasons),
            trades_after_filter=passed_filter_signal_count,
            filter_enabled=bool(self.regime_filter and self.regime_filter.settings.enabled),
            filter_config_snapshot=self.regime_filter.snapshot() if self.regime_filter else {},
            initial_cash=self.account.initial_cash,
            final_equity=equity_curve[-1] if equity_curve else self.account.initial_cash,
        )
        return BacktestResult(
            metrics=metrics,
            fills=fills,
            equity_curve=equity_curve,
            trades=trades,
            equity_points=equity_points,
            risk_events=risk_events,
        )
