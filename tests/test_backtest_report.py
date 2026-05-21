from crypto_bot.backtest.engine import BacktestResult
from crypto_bot.backtest.metrics import BacktestMetrics
from crypto_bot.backtest.report import format_backtest_report


def test_backtest_report_uses_chinese_labels_and_risk_note():
    result = BacktestResult(
        metrics=BacktestMetrics(
            total_return_pct=-1.6,
            max_drawdown_pct=1.94,
            win_rate_pct=0.0,
            trade_count=1,
            total_fees=3.846231,
            total_slippage=1.923077,
        ),
        fills=[],
        equity_curve=[],
    )

    report = format_backtest_report(result)

    assert "回测结果" in report
    assert "总收益率: -1.60%" in report
    assert "最大回撤: 1.94%" in report
    assert "备注: 已计入手续费和滑点" in report
    assert "不代表未来收益" in report
