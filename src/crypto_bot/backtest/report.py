from __future__ import annotations

from crypto_bot.backtest.engine import BacktestResult


def format_backtest_report(result: BacktestResult) -> str:
    metrics = result.metrics
    return "\n".join(
        [
            "回测结果",
            f"初始资金: {metrics.initial_cash:.2f}",
            f"最终权益: {metrics.final_equity:.2f}",
            f"总收益率: {metrics.total_return_pct:.2f}%",
            f"年化收益率: {metrics.annualized_return_pct:.2f}% ({metrics.annualized_return_note})",
            f"最大回撤: {metrics.max_drawdown_pct:.2f}%",
            f"胜率: {metrics.win_rate_pct:.2f}%",
            f"盈亏因子: {_format_profit_factor(metrics.profit_factor)} ({metrics.profit_factor_note})",
            f"交易次数: {metrics.trade_count}",
            f"盈利交易: {metrics.winning_trades}",
            f"亏损交易: {metrics.losing_trades}",
            f"平均盈利: {metrics.average_win:.6f}",
            f"平均亏损: {metrics.average_loss:.6f}",
            f"最大盈利: {metrics.largest_win:.6f}",
            f"最大亏损: {metrics.largest_loss:.6f}",
            f"手续费合计: {metrics.total_fees:.6f}",
            f"滑点成本: {metrics.total_slippage_cost:.6f}",
            f"持仓时间占比: {metrics.exposure_time_pct:.2f}%",
            f"风控拒单次数: {metrics.rejected_order_count}",
            f"风控拒绝原因分布: {metrics.risk_reject_reason_distribution}",
            "备注: 已计入手续费和滑点；当前为回测/虚拟盘结果，不代表未来收益。",
        ]
    )


def _format_profit_factor(profit_factor: float | None) -> str:
    return "null" if profit_factor is None else f"{profit_factor:.2f}"
