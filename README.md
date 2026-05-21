# Crypto Trading Bot MVP

Safety-first cryptocurrency trading framework for backtesting and paper trading.

This MVP deliberately does **not** implement real order placement. Strategies only produce signals. Every order must pass through `RiskManager`. Live execution is reserved for a future phase and currently raises `SafetyError`.

Current market data modes:

- `csv`: replay local OHLCV data.
- `ccxt_public`: read public OHLCV with `ccxt.fetch_ohlcv` only.

## Current Project Status: Offline Research Freeze

The latest multi-dataset benchmark concluded that the current strategy pool is **not ready**:

- Long-running paper trading is not allowed for the current strategies.
- Live-trading discussion is not allowed.
- No `not_ready` strategy may be connected to paper/live workflows.
- Readiness gates must not be bypassed or relaxed.
- API keys, private APIs, `create_order`, `fetch_balance`, and `fetch_positions` remain forbidden.

The project is currently an offline research framework. Future work should stay in historical analysis unless a strategy passes the conservative readiness process with stable out-of-sample evidence.

Create a research freeze archive from benchmark outputs:

```bash
python -m crypto_bot.cli research-freeze-report \
  --benchmark-json reports/strategy_benchmark_20260520T085132Z.json \
  --matrix-json reports/strategy_benchmark_matrix_20260520T085132Z.json \
  --decision-json reports/benchmark_decision_report_20260520T085132Z.json \
  --watchlist-json reports/watchlist_diagnosis_20260520T085132Z.json \
  --export reports/research_freeze_report_20260520T085132Z.md
```

This writes both Markdown and JSON reports. The freeze report is a research archive, not permission to start paper or live trading.

## What This MVP Includes

- CLI commands for `fetch-history`, `backtest`, `paper`, `optimize`, and `daily-summary`
- Local CSV OHLCV data loading
- Public `ccxt` OHLCV data loading for paper trading
- Moving average cross and simple offline research strategies
- Virtual account, positions, fees, and slippage
- Risk checks before order creation
- SQLite audit storage
- Pytest coverage for strategy, risk, paper execution, live guard, and metrics

## Safety Rules

- Default mode is `paper`
- `live_trading` defaults to `false`
- API keys are read only from environment variables in future phases
- Logs must not print secrets
- Real order functions raise `SafetyError`
- Strategies cannot call exchange clients or execution clients
- The current version reads public market data only and never calls `create_order`, `cancel_order`, `fetch_balance`, private APIs, or API-key based exchange methods.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Run Backtest

```bash
crypto-bot backtest --config config.example.yaml
```

Equivalent:

```bash
python -m crypto_bot.cli backtest --config config.example.yaml
```

Export backtest reports:

```bash
python -m crypto_bot.cli backtest --config config.example.yaml --export-dir reports
```

Generated files:

- `backtest_summary_<timestamp>.json`: headline metrics, including return, estimated annualized return, drawdown, win rate, profit factor, fees, slippage, exposure, and risk reject distribution.
- `trades_<timestamp>.csv`: simulated fill details with fees, slippage cost, realized PnL, reason, and source signal ID.
- `equity_curve_<timestamp>.csv`: bar-by-bar cash, position, equity, drawdown, and close price.
- `risk_events_<timestamp>.csv`: every risk decision with approval/rejection reason, adjusted size, equity, and current drawdown.

Backtest reports are historical simulations only. They are not a profit guarantee and are not evidence that the strategy will work in future market conditions.

## Fetch Historical Data

Fetch a longer local OHLCV CSV through public `ccxt.fetch_ohlcv`:

```bash
crypto-bot fetch-history --config config.history.example.yaml --output data/BTC_USDT_1h.csv
```

Equivalent:

```bash
python -m crypto_bot.cli fetch-history --config config.history.example.yaml --output data/BTC_USDT_1h.csv
```

`config.history.example.yaml` uses OKX public market data for `BTC/USDT` 1h candles from `2024-01-01` through `2026-05-16`. The output CSV columns are:

```text
timestamp,open,high,low,close,volume
```

If the output file already exists, the importer resumes from the last timestamp, fetches only later candles, removes duplicate timestamps, sorts by time, and replaces the CSV only after a successful complete fetch. Network failures are reported and the existing file is left untouched.

Use the fetched CSV for backtesting:

```bash
python -m crypto_bot.cli backtest --config config.history.example.yaml --data data/BTC_USDT_1h.csv --export-dir reports
```

Use the same CSV for optimization and walk-forward validation:

```bash
python -m crypto_bot.cli optimize --config config.history.example.yaml --export-dir reports
```

The history config points `market_data.csv_path` and `market_data.csv_files` at `data/BTC_USDT_1h.csv`, so optimization reads the fetched file once it exists.

If public exchange APIs are unavailable from your network, manually download exchange OHLCV history and place a CSV with the required columns in the `data` directory. Then run `backtest` or `optimize` against that local file. This remains offline historical simulation; the bot still does not use API keys, private endpoints, `fetch_balance`, `fetch_positions`, or `create_order`.

### Manually Downloaded Long History CSV

When public exchange APIs are unavailable, download historical OHLCV candles from an external source and save the file as:

```text
data/BTC_USDT_1h.csv
```

The CSV header must remain exactly:

```text
timestamp,open,high,low,close,volume
```

External sources often use headers such as `Date`, `unix`, `time`, `Open`, `High`, `Low`, `Close`, or `Volume`, and may include unrelated columns. Normalize those files first:

```bash
python -m crypto_bot.cli normalize-csv --input downloads/Binance_BTCUSDT_1h.csv --output data/BTC_USDT_1h.csv --timeframe 1h --export-report reports/data_quality_BTC_USDT_1h.json
```

`normalize-csv` maps common column names, drops unrelated columns, converts timestamps to UTC ISO strings, sorts by timestamp, removes duplicate timestamps, writes the project-standard CSV, and then automatically runs `validate-data`.

If the file is already in project format, use ISO timestamps or parseable timestamps such as `2024-01-01 00:00:00`. Keep rows in ascending time order and use one row per 1h candle. The long-sample config template is:

```text
config.long.btc.1h.example.yaml
```

It points to `data/BTC_USDT_1h.csv` and is intended for offline validation, backtesting, optimization, walk-forward checks, and readiness review only.

Validate a local historical CSV before using it:

```bash
python -m crypto_bot.cli validate-data --csv data/BTC_USDT_1h.csv --timeframe 1h --export reports/data_quality_BTC_USDT_1h.json
```

Run backtest:

```bash
python -m crypto_bot.cli backtest --config config.long.btc.1h.example.yaml --export-dir reports
```

Run optimization and walk-forward:

```bash
python -m crypto_bot.cli optimize --config config.long.btc.1h.example.yaml --export-dir reports
```

The long-sample template uses rolling walk-forward validation:

```yaml
optimization:
  walk_forward:
    enabled: true
    mode: rolling
    train_bars: 3000
    test_bars: 720
    step_bars: 720
    min_train_bars: 1000
    min_test_bars: 300
```

Each rolling window scans parameters on a fixed 3,000-bar training segment, selects the best training score, and validates that parameter set on the next 720-bar test segment. The next window advances by `step_bars`. The older single split mode remains available with `mode: single`, `train_ratio`, and `test_ratio`, but rolling mode is recommended for long samples because it gives multiple out-of-sample windows instead of one.

Generate a strategy-readiness report from the latest exported reports:

```bash
python -m crypto_bot.cli strategy-readiness --reports-dir reports --config config.long.btc.1h.example.yaml --export reports/strategy_readiness_latest.json
```

Diagnose walk-forward instability:

```bash
python -m crypto_bot.cli walk-forward-diagnose --walk-forward-results reports/walk_forward_results_20260516T165257Z.csv --export reports/walk_forward_diagnosis_20260516T165257Z.json
```

Research market regimes for walk-forward test windows:

```bash
python -m crypto_bot.cli regime-analysis --csv data/BTC_USDT_1h.csv --walk-forward-results reports/walk_forward_results_20260516T165257Z.csv --trades reports/trades_20260516T153612Z.csv --export reports/regime_analysis_20260516T165257Z.json
```

`regime-analysis` is an offline research tool. It matches each walk-forward test window to the historical OHLCV bars and summarizes market-state features such as return, volatility, drawdown, trend strength, moving-average slope, price-above-slow-MA percentage, range-bound score, and volume change. It compares profitable and losing windows, ranks potentially discriminating features, and emits candidate filter rules for further study.

Candidate filter rules from `regime-analysis` are not trading signals. They must be implemented separately and pass a new walk-forward validation before they can be considered for long-running paper trading. Do not use these research suggestions directly in live trading or to bypass readiness gates.

## Regime Filter Research

`regime_filter` is a historical research tool only. It can be enabled in backtest, optimization, and walk-forward runs to test candidate market-state filters discovered by `regime-analysis`.

```yaml
regime_filter:
  enabled: true
  min_moving_average_slope: 0
  min_trend_strength: null
  max_range_bound_score: null
  max_window_drawdown_pct: null
  min_price_above_slow_ma_pct: null
  min_volume_change_pct: null
```

The included research starting point is:

```text
config.long.btc.1h.filtered.example.yaml
```

Run filtered historical validation:

```bash
python -m crypto_bot.cli backtest --config config.long.btc.1h.filtered.example.yaml --export-dir reports
python -m crypto_bot.cli optimize --config config.long.btc.1h.filtered.example.yaml --export-dir reports
```

Compare the unfiltered and filtered configurations:

```bash
python -m crypto_bot.cli compare-filters --base-config config.long.btc.1h.yaml --filtered-config config.long.btc.1h.filtered.example.yaml --export-dir reports
```

The filter is applied after strategy signal generation and before risk checks. Filtered signals do not enter `RiskManager`, do not generate orders, and are reported separately as `filter_reject_count` and `filter_reject_reason_distribution`.

A better single backtest is not enough. Any filter must improve rolling walk-forward evidence and pass `strategy-readiness` before it can be considered for long-running paper trading. Filters can easily overfit a historical sample, especially when they are derived from the same walk-forward windows being evaluated.

## Donchian Breakout Research

The Donchian Channel Breakout + ATR Stop strategy is available for offline research:

```yaml
strategy:
  name: donchian_breakout
  entry_window: 20
  exit_window: 10
  atr_window: 14
  atr_multiplier: 2.0
```

Run the long-sample Donchian template:

```bash
python -m crypto_bot.cli backtest --config config.long.btc.1h.donchian.example.yaml --export-dir reports
python -m crypto_bot.cli optimize --config config.long.btc.1h.donchian.example.yaml --export-dir reports
```

The optimizer supports Donchian parameter scans:

```yaml
optimization:
  strategy_name: donchian_breakout
  entry_windows: [20, 40, 60]
  exit_windows: [10, 20, 30]
  atr_windows: [14]
  atr_multipliers: [1.5, 2.0, 3.0]
```

Combinations where `exit_window >= entry_window` are skipped. Donchian is still only a research strategy. It does not imply an edge, does not place orders, and must pass rolling walk-forward validation plus `strategy-readiness` before any long-running paper-trading discussion.

## Additional Research Strategies

The benchmark pool also includes three simple offline research strategies:

- `rsi_mean_reversion`: buys when RSI is below a buy threshold and sells when RSI is above a sell threshold.
- `bollinger_mean_reversion`: buys below the lower Bollinger band and sells back near or above the middle/upper band.
- `ema_pullback`: only looks for pullback reclaims while price is above a longer trend EMA.

Long-sample templates:

```bash
python -m crypto_bot.cli backtest --config config.long.btc.1h.rsi.example.yaml --export-dir reports
python -m crypto_bot.cli optimize --config config.long.btc.1h.rsi.example.yaml --export-dir reports
python -m crypto_bot.cli backtest --config config.long.btc.1h.bollinger.example.yaml --export-dir reports
python -m crypto_bot.cli optimize --config config.long.btc.1h.bollinger.example.yaml --export-dir reports
python -m crypto_bot.cli backtest --config config.long.btc.1h.ema_pullback.example.yaml --export-dir reports
python -m crypto_bot.cli optimize --config config.long.btc.1h.ema_pullback.example.yaml --export-dir reports
```

These strategies are research candidates only. They are not connected to paper/live trading and do not imply an edge. Use rolling walk-forward, `strategy-readiness`, `walk-forward-diagnose`, and benchmark results before considering any further study.

## Strategy Benchmark

Run the research benchmark matrix:

```bash
python -m crypto_bot.cli benchmark-strategies --config config.benchmark.example.yaml --export-dir reports
```

The benchmark config lists datasets and strategy configs:

```yaml
benchmark:
  datasets:
    - name: BTC_USDT_1h
      symbol: BTC/USDT
      timeframe: 1h
      csv_path: data/BTC_USDT_1h.csv
    - name: BTC_USDT_4h
      symbol: BTC/USDT
      timeframe: 4h
      csv_path: data/BTC_USDT_4h.csv
    - name: ETH_USDT_1h
      symbol: ETH/USDT
      timeframe: 1h
      csv_path: data/ETH_USDT_1h.csv
    - name: ETH_USDT_4h
      symbol: ETH/USDT
      timeframe: 4h
      csv_path: data/ETH_USDT_4h.csv
    - name: SOL_USDT_1h
      symbol: SOL/USDT
      timeframe: 1h
      csv_path: data/SOL_USDT_1h.csv
    - name: SOL_USDT_4h
      symbol: SOL/USDT
      timeframe: 4h
      csv_path: data/SOL_USDT_4h.csv
  strategies:
    - name: moving_average_cross
      config: config.long.btc.1h.yaml
    - name: moving_average_cross_filtered
      config: config.long.btc.1h.filtered.example.yaml
    - name: donchian_breakout
      config: config.long.btc.1h.donchian.example.yaml
    - name: rsi_mean_reversion
      config: config.long.btc.1h.rsi.example.yaml
    - name: bollinger_mean_reversion
      config: config.long.btc.1h.bollinger.example.yaml
    - name: ema_pullback
      config: config.long.btc.1h.ema_pullback.example.yaml
```

The expected multi-dataset CSV paths are:

```text
data/BTC_USDT_1h.csv
data/BTC_USDT_4h.csv
data/ETH_USDT_1h.csv
data/ETH_USDT_4h.csv
data/SOL_USDT_1h.csv
data/SOL_USDT_4h.csv
```

Each dataset is validated before any strategy runs. Invalid or missing CSV files are skipped and recorded as `error` rows, so one bad dataset does not contaminate the rest of the benchmark.

For each valid dataset and strategy pair, the benchmark runs backtest, optimization, rolling walk-forward, strategy-readiness, and walk-forward diagnosis, then writes:

- `strategy_benchmark_<timestamp>.csv` and `.json`: per strategy/dataset results.
- `strategy_benchmark_matrix_<timestamp>.csv` and `.json`: cross-dataset strategy summaries, dataset best-strategy summaries, readiness matrix, and single-dataset-effectiveness flags.
- `strategy_benchmark_dashboard_<timestamp>.html`: an offline HTML dashboard for visually reviewing the benchmark matrix.

This is a multi-asset, multi-timeframe research comparison tool only. It is not a profit guarantee and does not change paper/live behavior. Any strategy must show stable evidence across multiple datasets before it has value for further paper-trading research. Strategies with `not_ready` readiness are not candidates for long-running paper trading or live-trading discussion.

Create a research decision report from the benchmark outputs:

```bash
python -m crypto_bot.cli benchmark-decision-report \
  --benchmark-json reports/strategy_benchmark_20260519T170039Z.json \
  --matrix-json reports/strategy_benchmark_matrix_20260519T170039Z.json \
  --export reports/benchmark_decision_report_20260519T170039Z.json
```

`benchmark-decision-report` groups strategies into `eliminate`, `watchlist`, `insufficient_evidence`, or `continue_research`, and marks datasets that need more history. It is only a research triage aid. It is not a profit guarantee, does not relax readiness gates, and does not authorize long-running paper trading or live-trading discussion.

The validator checks required columns, timestamp parsing, ascending order, duplicate timestamps, missing values, OHLC consistency, non-negative volume, and missing bars implied by the timeframe.

The readiness gate is intentionally conservative: data quality must be valid, the history must contain at least 8,000 bars, walk-forward must have at least 10 windows and 100 total test trades, average test profit factor must be above 1.2, average test drawdown must stay below the configured risk threshold, and performance cannot come from only a small number of windows. Windows with `no_losing_trades` are flagged for review rather than treated as infinite profit factor.

Only when `strategy-readiness` outputs `paper_ready` should the strategy be considered for long-running paper trading. When the conclusion is `not_ready`, do not start live-trading discussions; improve the data sample, validation quality, strategy robustness, or walk-forward evidence first.

## Run Paper Trading MVP

CSV paper mode replays local CSV data through the virtual account and paper execution engine.

```bash
crypto-bot paper --config config.example.yaml --once
```

Public `ccxt` paper mode reads public OHLCV and still routes all orders through `RiskManager` and `PaperExecutionEngine`.

```bash
crypto-bot paper --config config.paper.ccxt.example.yaml --once
```

If Binance public API is unavailable in your network, use the OKX public OHLCV config:

```bash
crypto-bot paper --config config.paper.okx.example.yaml --once
```

Run multiple paper iterations with an explicit interval and maximum iteration count:

```bash
crypto-bot paper --config config.paper.ccxt.example.yaml --interval-seconds 60 --max-iterations 10
```

If your shell does not resolve `crypto-bot`, run the module directly:

```bash
python -m crypto_bot.cli paper --config config.paper.ccxt.example.yaml --once
```

If Binance public API is unavailable from your network, switch the paper config to OKX:

```yaml
market_data:
  source: ccxt_public
  exchange: okx
  symbols:
    - BTC/USDT
  timeframe: 1m
  limit: 100
```

Or use the included `config.paper.okx.example.yaml` directly.

The current version still reads public OHLCV only and will not place, cancel, or manage real exchange orders.

## Daily Summary

```bash
crypto-bot daily-summary --config config.example.yaml
```

## Strategy Optimization

Run a moving-average parameter scan:

```bash
python -m crypto_bot.cli optimize --config config.example.yaml --export-dir reports
```

This writes:

- `optimization_results_<timestamp>.csv`: one row per valid `fast_window` / `slow_window` pair.
- `optimization_summary_<timestamp>.json`: best parameter summary and scan metadata.
- `walk_forward_results_<timestamp>.csv`: train/test window results when walk-forward is enabled.
- `walk_forward_summary_<timestamp>.json`: stability metrics across test windows, including positive/negative window counts, average and median test return, average test drawdown, total and average test trade count, selected parameter distribution, and best/worst test return.

The conservative score is:

```text
score = total_return_pct - 2 * abs(max_drawdown_pct)
```

Parameter scans are not profit guarantees. They can overfit historical data, especially when the number of trades is small. Prefer walk-forward `test` results over `train` results when judging whether a strategy deserves more paper trading.

## Tests

```bash
pytest
```
