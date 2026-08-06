# Phase 2 — Promotion-aware six-asset 1h Rank IC/HAC/OOS evidence

Date: 2026-08-03

## Outcome

The independently promoted BTC/ETH/SOL/KNC/SWFTC/BICO 1h Panel now has a single
offline, content-addressed evidence chain covering fixed Rank IC, HAC/Holm
calibration, and purged expanding-window chronological OOS stability. The chain uses
the pre-result equivalent-duration forward-return mapping `4h:1/4/16 -> 1h:4/16/64`.
All six existing factor formulas, bar-window parameters, and encoded directions are
unchanged.

This is research evidence for one current-live convenience universe. It is not a
portfolio, cost-adjusted PnL, economic-value proof, stable-profitability result,
factor approval, strategy approval, or readiness upgrade.

## Frozen input and design

- 1h promotion SHA-256:
  `36739b6fcff8ffa8150b3fdd268150cc7fc0c997d90d9fb028f3f3c096d0c24e`
- Promotion marker file SHA-256:
  `68664799613953ee7dd279c0e9c2292178bef9306d1f1858fa2249deaa29e849`
- Registry SHA-256:
  `51ebbf3f97658fa8f36e5cd8f37742047727887957b347ba0399459b9de22975`
- Panel config SHA-256:
  `9e5a03f60327ce77a417471f513c256b740bf440e2bfa950d953a937ca068ff0`
- Panel SHA-256:
  `b7cdebac94f728a3588cb090391fb6024cb6783ec480b1fedc830e3bd4a32054`
- Common window: `2024-01-01T00:00:00Z..2026-04-30T23:00:00Z`
- Candidate timestamps: 20,424
- Required assets per valid IC: 6 of 6
- Fixed target horizons: 4, 16, and 64 one-hour bars
- Initial history: 10,212 timestamps
- OOS folds: 10 contiguous folds, first two of size 1,022 and final eight of
  size 1,021
- Train/OOS purge: the last `horizon_bars` candidate timestamps
- Minimum train/OOS valid counts: 1,000 / 500

The config and evidence identity explicitly state that related 4h results existed
before this run, the six-asset 1h design was frozen before generating its results,
and no global preregistration is claimed.

## Deterministic evidence identities

- Research SHA-256:
  `43e7b004b0302d20c1f8612f2b7cb77077fa8de9877325d0d86b6d18d6ef3eb3`
- Calibration SHA-256:
  `531dfd51af0d5738ec2370c2b315a929f2c589e3646142f19fc3d8cfb67be7cf`
- OOS analysis SHA-256:
  `150d6ffb685d2f31b894940751df21c4eb7b2061feb51e683e92d46e7a469e7a`
- Full chain SHA-256:
  `06d1ec6bbb6bfc5222cb674227d3eaf926c2c970c1c2dcb8cc635a48f652a001`
- Full chain marker file SHA-256:
  `6bf835ce8a466c325a7c36ab1dc3d5cd96d384ecfa63600e90e607b374395f26`

Two independent output directories produced the same nine filenames and exact same
bytes. The commit order is IC CSV, observations CSV, research JSON, HAC/Holm CSV,
calibration JSON, OOS folds CSV, OOS summaries CSV, OOS JSON, and final chain marker.
A failure before the final step cannot produce a marker.

## Research and calibration shape

- IC candidate rows: 367,632 (`20,424 × 18`)
- Valid IC groups: 366,846
- Valid observation rows: 2,201,076 (`366,846 × 6`)
- HAC/Holm hypotheses: 18
- Holm-adjusted p-values at or below 0.05: 13 of 18

All status counts are conserved, every valid group has exactly six observations,
and all HAC standard errors and raw/Holm p-values are finite and valid. Statistical
significance does not establish economic value or profitability.

## Chronological OOS shape

The first two versus final eight fold counts after purge are:

| Horizon | First two folds | Final eight folds | Pooled OOS |
| --- | ---: | ---: | ---: |
| 4 | 1,018 | 1,017 | 10,172 |
| 16 | 1,006 | 1,005 | 10,052 |
| 64 | 958 | 957 | 9,572 |

All 180 fold rows and 18 summary rows pass the existing 500-observation minimum.
Pooled OOS mean Rank IC signs remain descriptive and were not used to select,
remove, or flip factors: low-volatility and mean-reversion are positive at all three
horizons; both momentum factors and trend-quality are negative; volume-momentum is
positive. Several series show fold sign changes. No portfolio direction, position
weight, execution lag, turnover, fee, slippage, capacity, benchmark, or PnL rule is
defined by this result.

## Preserved limitations and identities

- Generic Panel timestamp semantics remain `unverified`.
- Promotion aggregate semantics remain `mixed_unverified` and non-uniform.
- Historical point-in-time membership is not proven.
- Survivorship bias is not resolved.
- Original Registry SHA-256 remains
  `15f2ac5e77a812196394d9d7811ee2ce66a391d6b41d83187e050fdbb40ce509`.
- Original Panel config SHA-256 remains
  `c29cca91009a27d0faad0a3fd9f2d216b9df7cdf10ca18161c33b0f5baee8d22`.
- Original 4h promotion marker file SHA-256 remains
  `f1970eb81825911827b984566e0dd7476285a31ee94056216628b421899fb39e`.
- Readiness and automatic factor approval remain false.

## Quality gates

- New wrapper plus OOS compatibility specialty tests: 17 passed.
- Full suite: 325 passed.
- Branch coverage: 85.11% (required minimum 85%).
- Ruff: passed for all source and test files.
- Mypy safety/research core: 33 source files passed.
- Source/live safety scan: 3 passed.
- `git diff --check`: no content error; Windows CRLF conversion warnings only.
