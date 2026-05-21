from crypto_bot.backtest.metrics import calculate_backtest_metrics


def test_backtest_metrics_calculates_return_drawdown_and_win_rate():
    metrics = calculate_backtest_metrics(
        equity_curve=[1000, 1100, 1050, 1200],
        trade_pnls=[100, -20, 40],
        total_fees=3,
        total_slippage=2,
    )

    assert metrics.total_return_pct == 20
    assert round(metrics.max_drawdown_pct, 2) == 4.55
    assert round(metrics.win_rate_pct, 2) == 66.67
    assert metrics.trade_count == 3
    assert metrics.total_fees == 3
    assert metrics.total_slippage == 2
    assert metrics.total_slippage_cost == 2


def test_backtest_metrics_calculates_profit_factor_and_trade_stats():
    metrics = calculate_backtest_metrics(
        equity_curve=[1000, 1100, 1050, 1125],
        trade_pnls=[100, -50, 25],
        total_fees=0,
        total_slippage=0,
    )

    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert metrics.average_win == 62.5
    assert metrics.average_loss == -50
    assert metrics.largest_win == 100
    assert metrics.largest_loss == -50
    assert metrics.profit_factor == 2.5
    assert metrics.profit_factor_note == "calculated"


def test_backtest_metrics_marks_profit_factor_when_there_are_no_losing_trades():
    metrics = calculate_backtest_metrics(
        equity_curve=[1000, 1100, 1125],
        trade_pnls=[100, 25],
        total_fees=0,
        total_slippage=0,
    )

    assert metrics.trade_count == 2
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 0
    assert metrics.profit_factor is None
    assert metrics.profit_factor_note == "no_losing_trades"


def test_backtest_metrics_handles_no_trades_without_crashing():
    metrics = calculate_backtest_metrics(
        equity_curve=[1000, 1000],
        trade_pnls=[],
        total_fees=0,
        total_slippage=0,
    )

    assert metrics.trade_count == 0
    assert metrics.win_rate_pct == 0
    assert metrics.profit_factor is None
    assert metrics.profit_factor_note == "no_trades"
    assert metrics.average_win == 0
    assert metrics.average_loss == 0
