from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Sequence
from datetime import datetime


@dataclass(frozen=True)
class BacktestMetrics:
    total_return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    trade_count: int
    total_fees: float
    total_slippage: float
    initial_cash: float = 0.0
    final_equity: float = 0.0
    annualized_return_pct: float = 0.0
    annualized_return_note: str = "not_estimated"
    profit_factor: float | None = None
    profit_factor_note: str = "no_trades"
    winning_trades: int = 0
    losing_trades: int = 0
    average_win: float = 0.0
    average_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    exposure_time_pct: float = 0.0
    rejected_order_count: int = 0
    risk_reject_reason_distribution: dict[str, int] = field(default_factory=dict)
    filter_reject_count: int = 0
    filter_reject_reason_distribution: dict[str, int] = field(default_factory=dict)
    trades_after_filter: int = 0
    filter_enabled: bool = False
    filter_config_snapshot: dict = field(default_factory=dict)

    @property
    def total_slippage_cost(self) -> float:
        return self.total_slippage


def calculate_backtest_metrics(
    equity_curve: Sequence[float],
    trade_pnls: Sequence[float],
    total_fees: float,
    total_slippage: float,
    timestamps: Sequence[datetime] | None = None,
    exposure_time_pct: float = 0.0,
    rejected_order_count: int = 0,
    risk_reject_reason_distribution: dict[str, int] | None = None,
    filter_reject_count: int = 0,
    filter_reject_reason_distribution: dict[str, int] | None = None,
    trades_after_filter: int = 0,
    filter_enabled: bool = False,
    filter_config_snapshot: dict | None = None,
    initial_cash: float | None = None,
    final_equity: float | None = None,
) -> BacktestMetrics:
    if not equity_curve:
        starting_equity = float(initial_cash or 0)
        ending_equity = float(final_equity if final_equity is not None else starting_equity)
        return BacktestMetrics(
            0,
            0,
            0,
            len(trade_pnls),
            total_fees,
            total_slippage,
            initial_cash=starting_equity,
            final_equity=ending_equity,
            rejected_order_count=rejected_order_count,
            risk_reject_reason_distribution=risk_reject_reason_distribution or {},
            filter_reject_count=filter_reject_count,
            filter_reject_reason_distribution=filter_reject_reason_distribution or {},
            trades_after_filter=trades_after_filter,
            filter_enabled=filter_enabled,
            filter_config_snapshot=filter_config_snapshot or {},
        )

    first_equity = float(initial_cash if initial_cash is not None else equity_curve[0])
    last_equity = float(final_equity if final_equity is not None else equity_curve[-1])
    total_return_pct = 0.0 if first_equity == 0 else ((last_equity - first_equity) / first_equity) * 100

    peak = equity_curve[0]
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    wins_list = [float(pnl) for pnl in trade_pnls if pnl > 0]
    losses_list = [float(pnl) for pnl in trade_pnls if pnl < 0]
    wins = len(wins_list)
    losses = len(losses_list)
    trade_count = len(trade_pnls)
    win_rate_pct = 0.0 if trade_count == 0 else (wins / trade_count) * 100
    gross_profit = sum(wins_list)
    gross_loss = abs(sum(losses_list))
    profit_factor, profit_factor_note = _calculate_profit_factor(trade_count, gross_profit, gross_loss)
    average_win = 0.0 if not wins_list else gross_profit / wins
    average_loss = 0.0 if not losses_list else sum(losses_list) / losses
    largest_win = 0.0 if not wins_list else max(wins_list)
    largest_loss = 0.0 if not losses_list else min(losses_list)
    annualized_return_pct, annualized_note = _calculate_annualized_return(
        first_equity,
        last_equity,
        timestamps,
    )

    return BacktestMetrics(
        total_return_pct=round(total_return_pct, 10),
        max_drawdown_pct=round(max_drawdown * 100, 10),
        win_rate_pct=round(win_rate_pct, 10),
        trade_count=trade_count,
        total_fees=round(total_fees, 10),
        total_slippage=round(total_slippage, 10),
        initial_cash=round(first_equity, 10),
        final_equity=round(last_equity, 10),
        annualized_return_pct=round(annualized_return_pct, 10),
        annualized_return_note=annualized_note,
        profit_factor=None if profit_factor is None else round(profit_factor, 10),
        profit_factor_note=profit_factor_note,
        winning_trades=wins,
        losing_trades=losses,
        average_win=round(average_win, 10),
        average_loss=round(average_loss, 10),
        largest_win=round(largest_win, 10),
        largest_loss=round(largest_loss, 10),
        exposure_time_pct=round(exposure_time_pct, 10),
        rejected_order_count=rejected_order_count,
        risk_reject_reason_distribution=risk_reject_reason_distribution or {},
        filter_reject_count=filter_reject_count,
        filter_reject_reason_distribution=filter_reject_reason_distribution or {},
        trades_after_filter=trades_after_filter,
        filter_enabled=filter_enabled,
        filter_config_snapshot=filter_config_snapshot or {},
    )


def _calculate_profit_factor(
    trade_count: int,
    gross_profit: float,
    gross_loss: float,
) -> tuple[float | None, str]:
    if trade_count == 0:
        return None, "no_trades"
    if gross_loss > 0:
        return gross_profit / gross_loss, "calculated"
    if gross_profit > 0:
        return None, "no_losing_trades"
    return None, "no_profit_or_loss"


def _calculate_annualized_return(
    initial_equity: float,
    final_equity: float,
    timestamps: Sequence[datetime] | None,
) -> tuple[float, str]:
    if not timestamps or len(timestamps) < 2 or initial_equity <= 0 or final_equity <= 0:
        return 0.0, "not_estimated"

    elapsed_days = (timestamps[-1] - timestamps[0]).total_seconds() / 86_400
    if elapsed_days <= 0:
        return 0.0, "not_estimated"
    if elapsed_days < 1:
        interval_return = ((final_equity - initial_equity) / initial_equity) * 100
        return interval_return, "estimated_from_less_than_one_year"

    try:
        annualized = ((final_equity / initial_equity) ** (365 / elapsed_days) - 1) * 100
    except OverflowError:
        annualized = ((final_equity - initial_equity) / initial_equity) * 100
    note = "estimated_from_less_than_one_year" if elapsed_days < 365 else "calculated"
    return annualized, note
