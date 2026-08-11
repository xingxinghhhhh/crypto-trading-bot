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

## Phase 1 Infrastructure

Phase 1 infrastructure is under development without changing the research-freeze conclusion:

- Paper restores virtual cash, position, realized PnL, and peak equity from SQLite.
- Paper strategy construction uses the same strategy factory as backtest, optimization, and benchmark paths.
- Long-running Paper is blocked unless `strategy.readiness=paper_ready`; all included runtime configs remain `not_ready`.
- Risk approvals are signed, order-bound, short-lived, and single-use. Setting `risk_checked=true` alone cannot authorize execution.
- Stop loss, take profit, daily loss, daily trade count, and a persistent manual kill switch are enforced.
- Public real-time bars pass closed-bar, clock-skew, gap, and optional staleness gates.
- Public market-data recording archives raw rows, normalized closed bars, and SHA-256 manifests.
- Recorder retries transient failures with bounded exponential backoff and reports attempt/retry counts.
- Archived 1m data can be checksum-verified and deterministically replayed as complete 1m, 15m, 1h, or 4h bars.
- Live Shadow records signals and risk decisions but never writes orders, fills, or balance changes.
- Runtime heartbeat and database health snapshots are available.

Record one public OKX market-data batch:

```bash
python -m crypto_bot.cli record-market-data \
  --config config.paper.okx.example.yaml \
  --output-dir data/recorder \
  --max-iterations 10 \
  --interval-seconds 60 \
  --max-retries 3 \
  --initial-backoff-seconds 1 \
  --max-backoff-seconds 30
```

The included OKX public-data configs set `market_data.use_environment_proxy: true`.
This makes CCXT honor standard `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and
`NO_PROXY` environment variables. The setting defaults to `false` and never adds
credentials or enables private exchange APIs.

Generate a daily recorder integrity, delay, duplicate, and gap summary:

```bash
python -m crypto_bot.cli market-data-summary \
  --input-dir data/recorder \
  --date 2026-07-23 \
  --export reports/market_data_summary_2026-07-23.json
```

Build a deterministic 15-minute replay dataset and strategy report from archived
1-minute bars:

```bash
python -m crypto_bot.cli replay-market-data \
  --config config.paper.okx.example.yaml \
  --input-dir data/recorder \
  --target-timeframe 15m \
  --output-csv data/replay/BTC_USDT_15m.csv \
  --export reports/replay_BTC_USDT_15m.json
```

Replay verifies every raw and normalized checksum, accepts only identical
overlapping bars, rejects conflicting duplicates and source gaps, and excludes
incomplete UTC-aligned target buckets. The same data and configuration produce
the same dataset and replay SHA-256 values.

## Phase 2 Factor Research

Run reproducible price and volume factor diagnostics on a verified Replay
archive:

```bash
python -m crypto_bot.cli analyze-factors \
  --config config.paper.okx.example.yaml \
  --input-dir data/recorder \
  --target-timeframe 1m \
  --output-dir reports/factors \
  --horizons 1,4,16 \
  --cost-bps 0,5,10 \
  --quantiles 5 \
  --rank-lookback 40 \
  --rolling-train-bars 60 \
  --rolling-test-bars 20 \
  --rolling-step-bars 20 \
  --min-observations 20
```

The command computes six initial price/volume factors, strictly forward-aligned
returns, time-series Pearson IC and Rank IC, multi-horizon IC decay, conditional
quantile returns, turnover, factor correlation, cost pressure, and rolling
train/test stability. It exports a deterministic JSON summary plus CSV tables
for metrics, decay, correlations, rolling windows, stability, and timestamped
factor values.

These are single-asset time-series diagnostics, not cross-sectional IC and not
executable PnL. Reports always keep `readiness_changed=false` and
`automatic_factor_approval=false`; factor evidence cannot automatically connect
a strategy to long-running Paper, Demo, private APIs, or live trading.

Run one Live Shadow observation:

```bash
python -m crypto_bot.cli shadow \
  --config config.shadow.okx.example.yaml \
  --max-iterations 10 \
  --interval-seconds 60
```

Inspect or operate the persistent Paper kill switch:

```bash
python -m crypto_bot.cli kill-switch --config config.example.yaml --action status
python -m crypto_bot.cli kill-switch --config config.example.yaml --action engage --reason operator_incident
python -m crypto_bot.cli kill-switch --config config.example.yaml --action release --reason manual_recovery
```

Inspect SQLite integrity, kill-switch state, and latest heartbeats:

```bash
python -m crypto_bot.cli health --config config.example.yaml
```

These capabilities are infrastructure and safety controls only. They do not make any current strategy suitable for long-running Paper, Demo, or live trading.

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
python -m crypto_bot.cli compare-filters --base-config config.long.btc.1h.example.yaml --filtered-config config.long.btc.1h.filtered.example.yaml --export-dir reports
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

Audit the long-history research datasets before running a benchmark:

```bash
python -m crypto_bot.cli dataset-registry-audit \
  --registry config.datasets.example.yaml \
  --export reports/dataset_registry_audit.json
```

The registry records a stable `dataset_id`, symbol, timeframe, repository-relative
path, provenance status/evidence, expected boundaries, and expected hashes. The
audit reuses `validate_ohlcv_csv`, computes both the raw file SHA-256 and a
canonical content SHA-256, and fails on quality problems, metadata drift, missing
evidence, or paths that escape the registry directory. Provenance is explicitly
`verified`, `partial`, or `unknown`; it is never inferred from a filename.

Audit an exact-timestamp multi-asset panel built from registered datasets:

```bash
python -m crypto_bot.cli dataset-panel-audit \
  --registry config.datasets.example.yaml \
  --panels-config config.dataset-panels.example.yaml \
  --panel-id btc_eth_sol_1h_v1 \
  --export reports/dataset_panel_audit_btc_eth_sol_1h_v1.json
```

`dataset-panel-audit` currently supports only `inner_exact`: all components must
have different symbols, the same timeframe, valid registry identity/hashes, and a
clean `validate_ohlcv_csv` result. It never resamples, fills, interpolates, shifts,
or calculates returns/factors. The report records union/intersection coverage,
boundary drops, internal alignment differences, component provenance and hashes,
and a deterministic `panel_sha256`. Because complete upstream lineage is not
available for every canonical CSV, timestamp semantics are reported as
`unverified`; matching UTC values do not prove that every provider used the same
bar-open/bar-close convention.

Capture a raw OKX public candlestick response for timestamp-semantics evidence:

```bash
python -m crypto_bot.cli capture-okx-timestamp-probe \
  --inst-id BTC-USDT \
  --bar 1m \
  --output-dir reports/okx-timestamp-probe
```

The capture uses only `GET /api/v5/market/candles`, validates the official nine-field
response contract, requires completed candles, verifies newest-first order and the
timeframe grid, and commits the raw response before its content-addressed manifest.
It does not use credentials, account data, private APIs, or trading endpoints. A network,
region, contract, `confirm`, or grid failure exits without a final probe manifest.

Consume one frozen probe in the deterministic offline semantics audit:

```bash
python -m crypto_bot.cli audit-timestamp-semantics \
  --registry config.datasets.example.yaml \
  --panels-config config.dataset-panels.example.yaml \
  --evidence-config config.timestamp-semantics.example.yaml \
  --probe-report reports/okx-timestamp-probe/okx-timestamp-probe.<probe_sha256>.json \
  --output-dir reports/timestamp-semantics
```

The audit never accesses the network. It emits independent producer, dataset, and Panel
assessments without modifying existing CSV, Registry, Panel, research, calibration, or OOS
identities. A verified OKX producer probe cannot upgrade historical files whose end-to-end
lineage is partial or unknown. Future consumers that require timestamp semantics must bind
both the original data/research identity and the independent `semantics_sha256`.

Capture a deterministic three-asset OKX 4h convenience-universe intake:

```bash
python -m crypto_bot.cli capture-okx-universe-intake \
  --registry config.datasets.example.yaml \
  --policy config.okx-universe-intake.example.yaml \
  --semantics-report reports/timestamp-semantics/timestamp-semantics.<semantics_sha256>.json \
  --output-dir reports/okx-universe-capture
```

The capture freezes the current public OKX SPOT instrument response, applies the
policy before downloading data, and selects exactly three non-BTC/ETH/SOL assets by
ascending `SHA256("okx-convenience-universe-v1|" + instId)`. Eligibility requires a
live, normal-rule, crypto-category USDT spot instrument whose effective continuous
trading start (`contTdSwTime`, otherwise `listTime`) is no later than 2022-01-01.
Stablecoin bases and anchored leveraged-token names are excluded by the versioned
policy. Assets cannot be supplied through the CLI or replaced after a history failure.

For each selected asset, the command requests only public OKX `history-candles` with
`bar=4H`, `limit=300`, and the official exclusive `after` cursor. It preserves the
actual UTF-8 response body for every page in a hash-verified JSONL bundle and requires
`confirm=1`, strict newest-first pagination, exact UTC 4h coverage, no gaps, and a
valid normalized OHLCV CSV. It uses no credentials, account, private, or trading API.
The snapshot, all three raw bundles, and all three CSVs are committed before the final
content-addressed capture marker; any selected-asset failure leaves no marker and never
modifies the Registry.

Replay and audit one frozen capture without network access:

```bash
python -m crypto_bot.cli audit-okx-universe-intake \
  --capture-report reports/okx-universe-capture/okx-universe-capture.<capture_sha256>.json \
  --output-dir reports/okx-universe-intake
```

The audit reparses every saved raw response, verifies cursor progression and hashes,
reconstructs each CSV, reruns quality/canonical hashing, and emits deterministic
eligibility, dataset, and independent Registry-candidate artifacts. It does not merge
the candidates, build a Panel, calculate factors/PnL, or change readiness. The output
is explicitly a `current_okx_live_convenience_snapshot`: it does not reconstruct
historical membership and does not resolve survivorship bias or prove profitability.

Promote one fully validated intake into an independent six-asset 4h Registry and Panel:

```bash
python -m crypto_bot.cli promote-okx-universe-candidates \
  --base-registry config.datasets.example.yaml \
  --base-panels-config config.dataset-panels.example.yaml \
  --semantics-report reports/timestamp-semantics/timestamp-semantics.<semantics_sha256>.json \
  --capture-report reports/okx-universe-capture/okx-universe-capture.<capture_sha256>.json \
  --intake-report reports/okx-universe-intake/okx-universe-intake.<intake_sha256>.json \
  --promotion-config config.okx-universe-promotion.example.yaml \
  --output-dir reports/okx-universe-promotion
```

Promotion is completely offline and reuses the capture/intake raw-page replay rather
than trusting the candidate YAML. It copies KNC/SWFTC/BICO CSV bytes to the single
stable `data/promoted/okx_convenience_v1/` root, writes one content-addressed lineage
manifest per promoted dataset, and emits content-addressed promoted Registry and Panel
configs at the repository root. The original Registry, original Panel config, and old
BTC/ETH/SOL CSVs are never changed. Absolute paths, `..`, `reports/` as a stable data
root, symlink escapes, candidate drift, hash drift, and same-name content collisions
all fail closed before a final promotion marker is accepted.

The six-asset Panel uses only `inner_exact`; it does not resample or fill data. The
generic Panel audit remains `timestamp_semantics.status=unverified` for backward
compatibility, while the promotion marker separately binds the more precise aggregate:
old BTC/ETH/SOL are `partial_unverified`, direct OKX KNC/SWFTC/BICO are
`verified_open_time`, and the Panel is `mixed_unverified` with
`timestamp_semantics_uniform=false`. This convenience sample still has no historical
point-in-time membership proof, does not resolve survivorship bias, and is not factor,
profitability, strategy-readiness, paper, or live evidence.

Extend that exact frozen convenience universe with independent 1h public histories:

```bash
python -m crypto_bot.cli capture-okx-frozen-universe-1h-history \
  --source-promotion-report reports/okx-universe-promotion/okx-universe-promotion.<promotion_sha256>.json \
  --policy config.okx-universe-1h-extension.example.yaml \
  --output-dir reports/okx-frozen-universe-1h-capture
```

This command never requests a new instruments snapshot and never reselects assets. It
replays the source promotion and original 2026-08-02 snapshot, then fetches only KNC,
SWFTC, and BICO public `history-candles` at `1H`, from 2022-01-01 00:00Z through
the snapshot-derived final closed bar at 2026-08-02 14:00Z. Each dataset must contain
exactly 40,191 gap-free confirmed bars across 134 strictly cursor-ordered pages. The
capture marker is written only after all three raw bundles and normalized CSVs pass
offline replay, quality checks, and raw/canonical hashing.

Promote the validated capture into an independent six-asset 1h Registry and Panel:

```bash
python -m crypto_bot.cli promote-okx-frozen-universe-1h-panel \
  --capture-report reports/okx-frozen-universe-1h-capture/okx-frozen-universe-1h-capture.<capture_sha256>.json \
  --base-registry config.datasets.example.yaml \
  --base-panels-config config.dataset-panels.example.yaml \
  --promotion-config config.okx-universe-1h-extension.example.yaml \
  --output-dir reports/okx-frozen-universe-1h-promotion
```

The promotion copies the three validated CSVs into
`data/promoted/okx_convenience_v1/1h/`, writes stable lineage manifests, and creates
content-addressed six-asset Registry and Panel configs without modifying any old 1h,
4h, promotion, or research artifact. The exact Panel has 20,424 common timestamps
from 2024-01-01 00:00Z through 2026-04-30 23:00Z out of a 40,191 timestamp union.
Its semantics remain `mixed_unverified`: BTC is partial, ETH/SOL are unknown, and
the three direct OKX histories are verified open-time. This node only establishes a
larger frozen data input for a fixed OOS run; it produces no factor, OOS, PnL,
profitability, approval, or readiness result.

Replay the fixed six-asset promotion-aware Rank IC and HAC/Holm evidence chain:

```bash
python -m crypto_bot.cli analyze-promoted-cross-sectional-evidence \
  --promotion-report reports/okx-universe-promotion/okx-universe-promotion.<promotion_sha256>.json \
  --research-config config.cross-sectional-factor-research.example.yaml \
  --output-dir reports/promoted-cross-sectional-evidence
```

This marker-only entry point resolves the promoted Registry, promoted Panel, target
Panel ID, all six datasets, lineage, hashes, claims, and mixed timestamp semantics
from the validated promotion report. It deliberately exposes no CLI overrides for
assets, datasets, Registry, Panel, factors, horizons, or direction. The research
configuration must exactly contain the frozen six factor specifications and horizons
1, 4, and 16. It computes factors on each asset's full history before filtering to
the 9,486 exact common timestamps, requires all six observations for every valid IC,
then runs the existing fixed HAC/Holm calibration over all 18 hypotheses.

The research CSVs, reports, calibration artifacts, and final chain marker are
content-addressed and byte deterministic; the chain marker is committed last. OOS
stability is intentionally excluded: half of 9,486 split into ten folds produces
only 474 or 475 pre-purge observations per fold, below the existing minimum of 500.
The threshold and fold policy are not relaxed. This evidence is not a portfolio,
PnL, factor approval, profitability claim, or readiness upgrade.

Run the frozen six-asset 1h Rank IC, HAC/Holm, and chronological OOS chain:

```bash
python -m crypto_bot.cli analyze-promoted-cross-sectional-oos-evidence \
  --promotion-report reports/okx-frozen-universe-1h-promotion/okx-universe-1h-promotion.<promotion_sha256>.json \
  --research-config config.promoted-cross-sectional-1h-oos.example.yaml \
  --output-dir reports/promoted-cross-sectional-1h-oos-evidence
```

The wrapper fully replays the 1h promotion before running any research. Its config
must be the frozen six-factor family and the preregistered equivalent-duration
forward-return mapping `4h:1/4/16 -> 1h:4/16/64`; factor window parameters and their
encoded directions remain unchanged. The CLI exposes no asset, factor, direction,
horizon, fold, purge, or minimum-sample override.

The exact 20,424-timestamp timeline reserves 10,212 timestamps as initial history
and partitions the remaining 10,212 into ten folds. The first two folds contain
1,022 raw timestamps and the other eight contain 1,021. After fixed per-fold purge,
valid OOS counts are 1,018/1,017 for h=4, 1,006/1,005 for h=16, and 958/957 for
h=64; pooled counts are 10,172, 10,052, and 9,572. These all preserve the existing
500 minimum. Research, calibration, OOS, and the final marker are committed in that
order as content-addressed artifacts, with the marker last.

This remains a current-live convenience-universe study with mixed timestamp
semantics, no historical point-in-time membership proof, and unresolved survivorship
bias. Rank IC, HAC/Holm significance, and chronological OOS stability are not a
portfolio, cost-adjusted PnL, economic-value proof, stable-profitability claim,
factor approval, or readiness upgrade.

Audit the frozen portfolio mechanism and execution-timing feasibility without
computing PnL:

```bash
python -m crypto_bot.cli audit-cross-sectional-portfolio-mechanism \
  --evidence-chain reports/promoted-cross-sectional-1h-oos-evidence/promoted-cross-sectional-1h-oos-chain.<chain_sha256>.json \
  --mechanism-config config.cross-sectional-portfolio-mechanism.example.yaml \
  --output-dir reports/cross-sectional-portfolio-mechanism
```

The command fully replays the promotion-aware 1h chain, then registers exactly 36
unselected variants: six factors, three horizons, and both high-rank and low-rank
directions. Every variant is long-only spot, top-2, equal-weight 0.5/0.5, gross/net
exposure 1, leverage 1, and one-bar rebalance. Missing assets, non-finite signals,
or a tie that makes top-2 membership ambiguous produce an all-cash target with no
carry, fill, or substitution. Neither direction is preferred, approved, or inferred
from observed IC or OOS signs.

Signal values stamped at `t` are conservatively treated as complete at `t+1h`; the
intended execution anchor is a common next-open at `t+2h`. The current legacy and
new components do not share uniformly verified open-time semantics, so the expected
successful audit result is `execution_price_mapping_feasible=false` and
`pnl_computation_authorized=false`. The audit emits only a variant contract,
constraint ledger, and final marker—no returns, equity, turnover, fees, benchmark,
or PnL—and does not connect to paper, risk, or execution modules.

Audit the exact six-asset `t -> t+2h` common-next-open mapping and the preserved
legacy timestamp evidence without computing PnL:

```bash
python -m crypto_bot.cli audit-legacy-1h-next-open-mapping \
  --mechanism-report reports/cross-sectional-portfolio-mechanism/cross-sectional-portfolio-mechanism.<mechanism_sha256>.json \
  --evidence-config config.legacy-1h-timestamp-mapping.example.yaml \
  --output-dir reports/legacy-1h-next-open-mapping
```

The offline command fully replays the mechanism and its promotion-aware evidence
chain, validates the stable BTC/ETH/SOL semantics evidence, and checks an exact
`t+2h` open row for every asset and panel timestamp. It never uses nearest-time,
fill, resample, carry, substitution, or grid-based timestamp inference. The frozen
20,424-timestamp panel yields 122,544 mapping rows: 122,532 exact finite internal
opens and 12 disclosed rows for the final two signal timestamps beyond the common
panel tail. Structural mapping is therefore verified, but BTC remains
`partial_unverified` and ETH/SOL remain `unknown`; the three promoted OKX datasets
alone are `verified_open_time`. Consequently execution-price mapping and PnL remain
unauthorized. Outputs are content-addressed dataset, mapping, constraint, and marker
artifacts and contain no returns, turnover, costs, equity, benchmark, or PnL.

Audit whether repeated public OKX history captures are byte-identical without
silently ignoring mutable fields:

```bash
python -m crypto_bot.cli audit-okx-public-response-mutability \
  --baseline-capture reports/okx-direct-anchor-1h-capture/okx-direct-anchor-1h-capture.<baseline_sha>.json \
  --comparison-capture reports/okx-direct-anchor-1h-capture-replay/okx-direct-anchor-1h-capture.<comparison_sha>.json \
  --policy config.public-response-mutability.example.yaml \
  --output-dir reports/okx-public-response-mutability
```

This audit treats each frozen raw capture as immutable and deterministically
replayable, but does not assume that a later public recapture returns identical
bytes. It compares all nine OKX fields (`ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm`)
by instrument, page, row, and timestamp. Differences are versioned in
content-addressed captures, field-level differences, policy, and JSON marker
artifacts. A recapture never replaces the baseline migration or Panel. This is
evidence of data-source mutability, not PnL or profitability evidence.

Audit whether the frozen Direct-OKX six-asset 1h Panel has an exact execution
price mapping prerequisite:

```bash
python -m crypto_bot.cli audit-okx-direct-six-1h-execution-mapping \
  --migration-report reports/okx-direct-six-asset-1h-migration/okx-direct-six-migration.<migration_sha>.json \
  --mutability-report reports/okx-public-response-mutability/public-response-mutability.<audit_sha>.json \
  --policy config.okx-direct-six-1h-execution-mapping.example.yaml \
  --output-dir reports/okx-direct-six-1h-execution-mapping
```

The audit replays the pinned six-asset Panel and maps signal timestamp `t` to
the exact `open` at `t+2h` (`t+1h` completion plus one execution delay). It
produces 241,146 rows: 241,134 exact finite internal opens and 12 disclosed
tail rows. No nearest-time lookup, fill, resample, carry, substitution, or
tolerance is allowed. This proves the timestamp prerequisite for a future PnL
node, but `pnl_computation_authorized=false`; membership remains a current-live
convenience sample and profitability/readiness claims remain false.

Freeze the prospective 36-variant family and future multiplicity contract before
any economic evaluation:

```bash
python -m crypto_bot.cli freeze-cross-sectional-variant-preregistration \
  --mechanism-report reports/cross-sectional-portfolio-mechanism/cross-sectional-portfolio-mechanism.<mechanism_sha>.json \
  --execution-mapping-report reports/okx-direct-six-1h-execution-mapping/okx-direct-six-1h-execution-mapping.<mapping_sha>.json \
  --config config.cross-sectional-variant-preregistration.example.yaml \
  --output-dir reports/cross-sectional-variant-preregistration
```

This records all 36 factor × horizon × rank-direction variants in the existing
mechanism order, keeps both directions, prohibits result-driven deletion or
flipping, applies Holm to a future primary-endpoint family, and fails the whole
family if any member is missing. It is a prospective economic-evaluation
protocol after prior statistical evidence, not global preregistration. It does
not calculate returns, costs, PnL, or select a winner.

Freeze the shared execution-cost evidence contract after the variant family and
exact execution mapping are pinned:

```bash
python -m crypto_bot.cli freeze-execution-cost-evidence \
  --preregistration-report reports/cross-sectional-variant-preregistration/cross-sectional-variant-preregistration.<prereg_sha>.json \
  --execution-mapping-report reports/okx-direct-six-1h-execution-mapping/okx-direct-six-1h-execution-mapping.<mapping_sha>.json \
  --evidence-config config.execution-cost-evidence.example.yaml \
  --output-dir reports/execution-cost-evidence
```

This contract uses a clearly labeled conservative taker-fee policy assumption,
fixed spread/slippage stress tiers, and a 1% participation cap on canonical
base `volume`. It does not claim a user-specific fee, historical spread, or
guaranteed capacity. Raw `volCcy`/`volCcyQuote` recapture mutability remains
disclosed and the first migration capture stays pinned. No cost amount, return,
turnover, equity, or PnL is computed.

Archive future-only OKX universe snapshots and audit adjacent membership changes
without rewriting history or silently replacing assets:

```bash
python -m crypto_bot.cli capture-okx-future-universe-snapshot \
  --baseline-universe-report reports/okx-universe-capture/okx-universe-capture.<capture_sha>.json \
  --archive-config config.okx-future-universe-archive.example.yaml \
  --output-dir reports/okx-future-universe-snapshot

python -m crypto_bot.cli audit-okx-future-universe-transition \
  --previous-snapshot reports/okx-universe-capture/okx-universe-capture.<capture_sha>.json \
  --current-snapshot reports/okx-future-universe-snapshot/okx-future-universe-snapshot.<snapshot_sha>.json \
  --archive-config config.okx-future-universe-archive.example.yaml \
  --output-dir reports/okx-future-universe-transition
```

The archive reuses the frozen SPOT/USDT/live/normal/instCategory/stablecoin/
leveraged-token/exact-instId eligibility rules and records raw response bytes,
eligible rows, tracked six-asset status, and a deterministic adjacent diff.
Exit signals become effective only when a later snapshot is received; historical
membership is never backfilled, ineligible assets are not carried forward, and
there is no automatic replacement or reconstitution. This is operational
universe evidence only: it does not resolve survivorship bias, recover historical
delistings, compute PnL, or change readiness.

Convert the frozen future-only transition into a strictly aligned 1h
prospective membership gate before any economic evaluation:

```bash
python -m crypto_bot.cli freeze-prospective-membership-bar-gate \
  --transition-report reports/okx-future-universe-transition-v2/okx-universe-transition.<transition_sha>.json \
  --execution-mapping-report reports/okx-direct-six-1h-execution-mapping/okx-direct-six-1h-execution-mapping.<mapping_sha>.json \
  --policy config.prospective-membership-bar-gate.example.yaml \
  --output-dir reports/prospective-membership-bar-gate
```

The gate uses the first complete signal bar at or after the previous snapshot,
keeps membership piecewise constant between snapshots, and requires execution
anchors to be strictly before the current snapshot. With the pinned evidence it
produces 161 hourly signal timestamps from 2026-08-02 16:00 UTC through
2026-08-09 08:00 UTC and 966 six-asset eligibility rows; the last execution is
2026-08-09 10:00 UTC. A later transition cannot retroactively delete these rows
or trigger a replacement. The output contains no prices, returns, weights,
turnover, costs, or PnL, and does not claim continuous or historical PIT
membership.

Capture the first closed prospective Direct-OKX 1h market-data extension after
the membership gate is frozen:

```bash
python -m crypto_bot.cli capture-okx-prospective-direct-1h-extension \
  --membership-gate reports/prospective-membership-bar-gate/prospective-membership-bar-gate.<gate_sha>.json \
  --config config.okx-prospective-direct-1h-extension.example.yaml \
  --output-dir reports/prospective-direct-1h-capture

python -m crypto_bot.cli audit-okx-prospective-direct-1h-extension \
  --capture-report reports/prospective-direct-1h-capture/prospective-direct-1h-capture.<capture_sha>.json \
  --membership-gate reports/prospective-membership-bar-gate/prospective-membership-bar-gate.<gate_sha>.json \
  --config config.okx-prospective-direct-1h-extension.example.yaml \
  --output-dir reports/prospective-direct-1h-extension
```

This reuses the public-only paginated history helper and its strict `confirm=1`,
exact timestamp, no-gap/no-duplicate validation. Raw response pages are retained,
while canonical append CSVs are limited to 164 bars per asset from
2026-08-02 15:00 UTC through 2026-08-09 10:00 UTC. The baseline six 40,191-row
CSV files are never rewritten or replaced. The audit verifies all 984 append
rows and all 966 gate signal/completion/execution timestamps; execution only
authorizes the `open` field, not future-bar high/low/close/volume. No returns,
costs, turnover, or PnL are computed.

Build the closed-epoch prospective signal/rank/weight ledger only after the
preregistration, membership gate, Direct-OKX extension, and exact execution
mapping markers are frozen:

```bash
python -m crypto_bot.cli build-prospective-portfolio-ledger \
  --preregistration-report reports/cross-sectional-variant-preregistration/cross-sectional-variant-preregistration.<prereg_sha>.json \
  --membership-gate reports/prospective-membership-bar-gate/prospective-membership-bar-gate.<gate_sha>.json \
  --market-data-extension reports/prospective-direct-1h-extension/prospective-direct-1h-extension.<extension_sha>.json \
  --execution-mapping-report reports/okx-direct-six-1h-execution-mapping/okx-direct-six-1h-execution-mapping.<mapping_sha>.json \
  --config config.prospective-portfolio-ledger.example.yaml \
  --output-dir reports/prospective-portfolio-ledger
```

The ledger emits exactly 5,796 factor-score rows, 5,796 decisions, and 34,776
asset-weight rows for 161 signal timestamps × 36 preregistered families × six
assets. It truncates each asset to `timestamp <= t` before computing the fixed
six factors, reuses the frozen top-2/equal-weight mechanism, preserves both
rank directions and all-cash rows, and binds every upstream identity plus the
six baseline/append hashes. It does not compute returns, turnover, cost
amounts, PnL, profitability evidence, or readiness.

Freeze the prospective execution-to-execution accounting contract without
reading prices or calculating any economic values:

```bash
python -m crypto_bot.cli freeze-prospective-economic-accounting-contract \
  --portfolio-ledger reports/prospective-portfolio-ledger/prospective-portfolio-ledger.<ledger_sha>.json \
  --cost-evidence reports/execution-cost-evidence/execution-cost-evidence.<cost_sha>.json \
  --config config.prospective-economic-accounting.example.yaml \
  --output-dir reports/prospective-economic-accounting
```

This freezes 161 target states as 160 closed execution-to-execution intervals
plus one terminal-unscored target, keeps horizon 4/16/64 as family labels only,
and records the one-way trade-notional and reporting-turnover formula IDs for
the future evaluator. It also freezes a six-asset 1/6 equal-weight benchmark.
The existing spread stress schema does not resolve one-way versus full quoted
spread, so the contract explicitly records
`spread_application_semantics_resolved=false` and
`economic_cost_application_ready=false`; no `/2`, `×2`, cost amount, return,
turnover value, PnL, or readiness claim is produced.

Resolve that explicit spread-unit blocker in a separate prospective policy
marker. This does not rewrite the older cost or accounting artifacts and does
not calculate any economic amount:

```bash
python -m crypto_bot.cli freeze-spread-application-semantics \
  --accounting-contract reports/prospective-economic-accounting/prospective-economic-accounting.<accounting_sha>.json \
  --cost-evidence reports/execution-cost-evidence/execution-cost-evidence.<cost_sha>.json \
  --config config.spread-application-semantics.example.yaml \
  --output-dir reports/spread-application-semantics
```

The marker defines the existing `[0, 5, 10]` stress tiers as
`one_way_execution_friction_per_asset_trade_notional` with multiplier `1`.
It explicitly forbids half/full-spread conversion, makes no historical
quoted-spread claim, binds the accounting and cost artifact hashes, and keeps
return, turnover, cost-amount, PnL, profitability, and readiness authorization
false. Its formula is only a future-evaluator contract; no real traded notional
is substituted.

Freeze the complete prospective cost-scenario topology and independent
capacity application policy before introducing an economic evaluator:

```bash
python -m crypto_bot.cli freeze-prospective-economic-cost-scenarios \
  --accounting-contract reports/prospective-economic-accounting/prospective-economic-accounting.<accounting_sha>.json \
  --cost-evidence reports/execution-cost-evidence/execution-cost-evidence.<cost_sha>.json \
  --spread-semantics reports/spread-application-semantics/spread-application-semantics.<semantics_sha>.json \
  --config config.prospective-economic-cost-scenarios.example.yaml \
  --output-dir reports/prospective-economic-cost-scenarios
```

This pre-registers all nine combinations of spread `[0,5,10]` and slippage
`[0,5,10]` with fee `100` bps. The sole primary is the conservative `10/10`
scenario; the other eight are sensitivity-only. Capacity remains an
independent hard scale constraint on canonical base volume at `1%`, anchored
to execution open and enforced per asset/event. These are symbolic policies:
no real traded notional, cost amount, capacity pass/fail, return, or PnL is
calculated.

Before any future economic evaluator, run the cross-artifact structural
readiness gate:

```bash
python -m crypto_bot.cli audit-prospective-economic-readiness \
  --portfolio-ledger reports/prospective-portfolio-ledger/prospective-portfolio-ledger.<ledger_sha>.json \
  --accounting-contract reports/prospective-economic-accounting/prospective-economic-accounting.<accounting_sha>.json \
  --cost-scenarios reports/prospective-economic-cost-scenarios/prospective-economic-cost-scenarios.<scenario_sha>.json \
  --market-data-extension reports/prospective-direct-1h-extension/prospective-direct-1h-extension.<extension_sha>.json \
  --membership-gate reports/prospective-membership-bar-gate/prospective-membership-bar-gate.<gate_sha>.json \
  --execution-mapping reports/okx-direct-six-1h-execution-mapping/okx-direct-six-1h-execution-mapping.<mapping_sha>.json \
  --config config.prospective-economic-readiness-gate.example.yaml \
  --output-dir reports/prospective-economic-readiness
```

The gate verifies `36 × 160 = 5,760` strategy slots and `160` benchmark
slots, exact execution-open and canonical-volume availability, future-only
membership, terminal exclusion, and cross-marker cost alignment. It emits only
timestamps and boolean structural flags—not prices, weights, trade notional,
costs, returns, or PnL. A successful structural gate does not authorize
economic evaluation or trading readiness.

Freeze prospective sample maturity before calculating any economic value. The
sample unit is a unique closed execution interval—not a strategy slot, variant,
benchmark row, or cost scenario:

```bash
python -m crypto_bot.cli audit-prospective-economic-sample-maturity \
  --readiness-report reports/prospective-economic-readiness/prospective-economic-readiness.<readiness_sha>.json \
  --config config.prospective-economic-sample-maturity.example.yaml \
    --output-dir reports/prospective-economic-sample-maturity
```

Freeze the future-only weekly accumulation cadence and fail-closed stop rule
(this does not capture market data or compute economic values):

```bash
python -m crypto_bot.cli freeze-prospective-epoch-accumulation-policy \
    --sample-maturity reports/prospective-economic-sample-maturity/prospective-economic-sample-maturity.<sha>.json \
    --latest-readiness reports/prospective-economic-readiness/prospective-economic-readiness.<sha>.json \
    --config config.prospective-epoch-accumulation-policy.example.yaml \
    --output-dir reports/prospective-epoch-accumulation-policy
```

Freeze the append-only Direct-OKX 1h segment chain. Repeating
`--extension-report` is chronological; this command registers existing
segments only and does not recapture market data:

```bash
python -m crypto_bot.cli freeze-prospective-direct-1h-segment-chain \
    --accumulation-policy reports/prospective-epoch-accumulation-policy/prospective-epoch-accumulation-policy.<sha>.json \
    --sample-maturity reports/prospective-economic-sample-maturity/prospective-economic-sample-maturity.<sha>.json \
    --latest-readiness reports/prospective-economic-readiness/prospective-economic-readiness.<sha>.json \
    --extension-report reports/prospective-direct-1h-extension/prospective-direct-1h-extension.<sha>.json \
    --config config.prospective-direct-1h-segment-chain.example.yaml \
    --output-dir reports/prospective-direct-1h-segment-chain
```

Freeze the strict future-epoch admission state machine. It produces only a
pending epoch-2 contract and does not create a new epoch or capture data:

```bash
python -m crypto_bot.cli freeze-prospective-epoch-assembly-state-machine \
    --accumulation-policy reports/prospective-epoch-accumulation-policy/prospective-epoch-accumulation-policy.<sha>.json \
    --sample-maturity reports/prospective-economic-sample-maturity/prospective-economic-sample-maturity.<sha>.json \
    --latest-readiness reports/prospective-economic-readiness/prospective-economic-readiness.<sha>.json \
    --segment-chain reports/prospective-direct-1h-segment-chain/prospective-direct-1h-segment-chain.<sha>.json \
    --config config.prospective-epoch-assembly.example.yaml \
    --output-dir reports/prospective-epoch-assembly
```

Freeze the one-time public-only admission ticket for epoch 2. This is an
offline contract and does not execute the future snapshot:

```bash
python -m crypto_bot.cli freeze-prospective-epoch-capture-admission \
    --assembly-report reports/prospective-epoch-assembly/prospective-epoch-assembly.<sha>.json \
    --config config.prospective-epoch-capture-admission.example.yaml \
    --output-dir reports/prospective-epoch-capture-admission
```

Freeze the append-only attempt journal contract that will govern retries inside
the future window. This is still offline: it writes a zero-attempt sentinel,
does not make a request, and cannot create a snapshot. Only transport or
validation failure may precede another attempt; the first validation-passed
attempt is accepted and later attempts are prohibited:

```bash
python -m crypto_bot.cli freeze-prospective-capture-attempt-journal \
    --admission-ticket reports/prospective-epoch-capture-admission/prospective-epoch-capture-admission.<ticket_sha>.json \
    --config config.prospective-capture-attempt-journal.example.yaml \
    --output-dir reports/prospective-capture-attempt-journal
```

The journal contract is content-addressed and append-only. Its initial
`attempt_count` and `accepted_attempt_count` are both zero, and the sentinel
row is not a synthetic attempt. Pure receipt validation accepts only strict
`1..N` numbering, timestamps inside the governed window, retryable failures,
and one first-valid acceptance; response selection, deletion, reordering,
out-of-window attempts, backfill, economic computation, and readiness changes
fail closed.

Audit the append-only receipt chain in the declared input order. With no
`--receipt` arguments this produces the real zero-receipt state for epoch 2;
synthetic local receipts can exercise failure/retry/pass replay without any
network request:

```bash
python -m crypto_bot.cli audit-prospective-capture-attempt-receipt-chain \
    --journal-contract reports/prospective-capture-attempt-journal/prospective-capture-attempt-journal.<journal_sha>.json \
    --config config.prospective-epoch-capture-attempt-receipt-chain.example.yaml \
    --output-dir reports/prospective-capture-attempt-receipt-chain
```

Receipt numbers and `previous_receipt_sha256` must form a strict append-only
chain. `transport_failed` and `validation_failed` may precede another receipt;
the first `validation_passed` must bind a locally replayable future snapshot
marker and closes the chain permanently. The command never sorts receipts,
never selects a preferred response, and never creates a market snapshot.

The public `validate_capture_attempt_receipt_chain()` API replays the
content-addressed receipt CSV and derives the canonical state. A validated
non-zero failure tail is `retry_open` (receipt count `N`, next attempt `N+1`);
an accepted tail is `accepted_closed` and cannot be continued. The companion
`load_validated_capture_attempt_receipts()` API returns the validated report
and receipt rows for offline adapters.

Materialize a future execution result into the frozen receipt schema using a
local evidence envelope only. The adapter has no network client and derives
attempt number, parent identities, response hashes, acceptance, and snapshot
identity; callers cannot override outcome, acceptance, numbering, or hashes:

```bash
python -m crypto_bot.cli materialize-prospective-capture-attempt-receipt \
    --receipt-chain reports/prospective-capture-attempt-receipt-chain/prospective-capture-attempt-receipt-chain.<chain_sha>.json \
    --attempt-evidence reports/prospective-capture-attempt-evidence/<evidence>.json \
    --config config.prospective-capture-attempt-evidence-adapter.example.yaml \
    --output-dir reports/prospective-capture-attempt-evidence-adapter
```

The evidence discriminator is one of `transport_failure`,
`snapshot_validation_failure`, or `snapshot_validation_pass`. Response hashes
come from local raw response artifacts, and a successful snapshot must pass the
existing public future-universe validator. The adapter immediately replays the
full parent-plus-generated receipt sequence through the chain auditor before
writing its marker-last, content-addressed report. It accepts a validated
zero-receipt `awaiting_attempt` parent or a validated non-zero `retry_open`
parent, and derives the next attempt and previous receipt hash. Synthetic
fixtures remain fixture-only; the real zero-receipt chain stays at 160/500
with no network or economic state.

Close the governed epoch-2 capture window using a minimal local evidence
envelope. The closeout validator derives the final state from the validated
receipt chain, admission ticket, frozen window, and `observed_at`; callers
cannot provide `final_state`, `accepted`, `missed`, or a snapshot identity:

```bash
python -m crypto_bot.cli audit-prospective-capture-window-closeout \
    --receipt-chain reports/prospective-capture-attempt-receipt-chain/prospective-capture-attempt-receipt-chain.<chain_sha>.json \
    --admission-ticket reports/prospective-epoch-capture-admission/prospective-epoch-capture-admission.<ticket_sha>.json \
    --closeout-evidence reports/prospective-capture-window-closeout/closeout-evidence.json \
    --config config.prospective-capture-window-closeout.example.yaml \
    --output-dir reports/prospective-capture-window-closeout
```

Before `2026-08-16T11:00:00Z`, an unaccepted chain remains
`pending_window_end` and retryable. At or after the governed end, an
`awaiting_attempt` or `retry_open` chain becomes `missed_no_backfill`; an
`accepted_closed` chain remains accepted and keeps its first snapshot identity.
Missed windows cannot receive retroactive receipts, snapshots, sample credit,
or schedule shifts. The command is offline and does not change the real
zero-receipt chain.

Consume the closeout into the next governed-epoch contract without performing
the transition itself:

```bash
python -m crypto_bot.cli audit-prospective-epoch-closeout-rollover \
    --closeout-report reports/prospective-capture-window-closeout/prospective-capture-window-closeout.<closeout_sha>.json \
    --assembly-report reports/prospective-epoch-assembly/prospective-epoch-assembly.<assembly_sha>.json \
    --accumulation-policy reports/prospective-epoch-accumulation-policy/prospective-epoch-accumulation-policy.<policy_sha>.json \
    --sample-maturity reports/prospective-economic-sample-maturity/prospective-economic-sample-maturity.<maturity_sha>.json \
    --config config.prospective-epoch-closeout-rollover.example.yaml \
    --output-dir reports/prospective-epoch-closeout-rollover
```

The derived action is fail-closed and future-only: `pending_window_end` holds
epoch 2, `accepted_closed` is only `transition_eligible`, and
`missed_no_backfill` rolls to epoch 3 at the fixed Sunday
2026-08-23 10:00–11:00 UTC window with zero sample credit. The validator
replays the public closeout validator and all content-addressed parent
artifacts before writing marker-last state, dependency, and constraint files.
It does not perform a transition, fetch network data, backfill receipts,
compute economics/PnL, or change readiness.

Freeze the accepted-closeout transition admission without creating a
transition artifact:

```bash
python -m crypto_bot.cli freeze-prospective-snapshot-transition-admission \
    --rollover-report reports/prospective-epoch-closeout-rollover/prospective-epoch-closeout-rollover.<rollover_sha>.json \
    --closeout-report reports/prospective-capture-window-closeout/prospective-capture-window-closeout.<closeout_sha>.json \
    --config config.prospective-snapshot-transition-admission.example.yaml \
    --output-dir reports/prospective-snapshot-transition-admission
```

The admission is `blocked_pending_closeout` for the current real
`pending_window_end`/`hold` state and `blocked_missed_epoch` for a missed
window. Only `accepted_closed` plus `transition_eligible` can be `admitted`.
The admitted branch derives the previous snapshot from the validated epoch
lineage and the current snapshot from the closeout's single first-validator
pass receipt, then replays the existing public future-snapshot validator and
requires `current.received_at > previous.received_at`. Snapshot identities
cannot be supplied through config or CLI. Outputs are marker-last,
content-addressed admission/dependency/constraint artifacts; no transition,
network request, sample credit, economic/PnL calculation, or readiness change
is performed.

The current prospective slice has 160 eligible closed intervals against the
frozen minimum of 500, so the expected successful result is
`sample_maturity_met=false` with 340 intervals remaining. Future readiness
epochs may be supplied in chronological order; overlaps and unexplained gaps
fail closed. This gate does not calculate returns, costs, capacity pass/fail,
turnover, or PnL and never lowers the 500-interval policy based on results.

Run the independent cross-sectional Rank IC evidence workflow on a frozen panel:

```bash
python -m crypto_bot.cli analyze-cross-sectional-factors \
  --registry config.datasets.example.yaml \
  --panels-config config.dataset-panels.example.yaml \
  --panel-id btc_eth_sol_1h_v1 \
  --research-config config.cross-sectional-factor-research.example.yaml \
  --output-dir reports/cross-sectional-factors/1h
```

This command computes each factor and forward return independently on each
constituent's full validated history, then restricts results to the frozen Panel
timestamps. A timestamp receives a cross-sectional Spearman Rank IC only when
all Panel assets have finite factor/return pairs; ties use average ranks and
warm-up, tail, non-finite, and constant cases receive explicit skip statuses.
The output is a content-addressed JSON report, complete IC time-series CSV, and
valid-observation CSV that can reproduce every numeric IC. The research identity
binds the Panel hash, component raw/canonical hashes, factor specs, horizons,
policies, and artifact hashes.

The included panels contain only BTC, ETH, and SOL, so each IC uses a very small
and statistically weak three-asset cross-section. This workflow does not build a
portfolio, calculate executable PnL, correct selection or survivorship bias, run
significance/multiple-testing procedures, approve a factor, or change strategy
readiness. Timestamp semantics remain `unverified` and are propagated into every
report.

Calibrate the mean Rank IC evidence from one completed cross-sectional report:

```bash
python -m crypto_bot.cli calibrate-cross-sectional-rank-ic \
  --source-report reports/cross-sectional-factors/1h/cross-sectional-factor-research.<research_sha256>.json \
  --output-dir reports/cross-sectional-ic-calibration/1h
```

This independent command verifies the source report identity and both referenced
CSV artifacts before reading any IC values. For every factor/horizon pair it uses
a fixed Newey-West/Bartlett HAC mean test with bandwidth
`max(horizon - 1, floor(4 * (n / 100)^(2/9)))`, then applies Holm adjustment to
all hypotheses in that one source report. The policy has no random seed or
tunable statistical CLI options. Internal gaps in the valid IC interval,
insufficient samples, non-positive long-run variance, artifact drift, path escape,
or an incomplete hypothesis family fail closed.

The content-addressed JSON and hypothesis CSV are statistical calibration evidence
only. Asymptotic p-values and confidence intervals do not prove economic value,
tradability, stability, or profitability; the command does not select factors,
construct a portfolio, calculate PnL, or change readiness.

Measure fixed, post-hoc chronological OOS stability without selecting factors or changing
their directions:

```bash
python -m crypto_bot.cli analyze-cross-sectional-oos-stability \
  --source-report reports/cross-sectional-factors/1h/cross-sectional-factor-research.<research_sha256>.json \
  --output-dir reports/cross-sectional-oos-stability/1h
```

This descriptive evidence uses the common uncompressed candidate timeline, reserves the
first half as initial history, partitions the second half into ten contiguous folds, and
purges the last `horizon_bars` candidates from both each fold's expanding training window
and OOS window. It does not run hypothesis tests, claim PnL, approve factors, or change
strategy readiness.

Run the research benchmark matrix:

```bash
python -m crypto_bot.cli benchmark-strategies --config config.benchmark.example.yaml --export-dir reports
```

The benchmark config lists datasets and strategy configs:

```yaml
benchmark:
  dataset_registry: config.datasets.example.yaml
  datasets:
    - dataset_id: btc_usdt_1h_v1
      name: BTC_USDT_1h
    - dataset_id: btc_usdt_4h_v1
      name: BTC_USDT_4h
    - dataset_id: eth_usdt_1h_v1
      name: ETH_USDT_1h
    - dataset_id: eth_usdt_4h_v1
      name: ETH_USDT_4h
    - dataset_id: sol_usdt_1h_v1
      name: SOL_USDT_1h
    - dataset_id: sol_usdt_4h_v1
      name: SOL_USDT_4h
  strategies:
    - name: moving_average_cross
      config: config.long.btc.1h.example.yaml
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

Each registered dataset is resolved and audited before any strategy runs. Invalid,
missing, or hash-mismatched CSV files are skipped and recorded as `error` rows, so
one bad dataset does not contaminate the rest of the benchmark. Benchmark CSV/JSON
rows and dataset summaries include `dataset_id`, `raw_sha256`, and
`canonical_sha256`, matching the registry audit report.

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
