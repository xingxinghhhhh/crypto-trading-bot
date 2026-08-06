# Phase 2 — Legacy 1h timestamp semantics and exact next-open mapping audit

Date: 2026-08-03

## Outcome

The six-asset 1h portfolio mechanism now has an independently replayable, offline
structural execution-price mapping audit. For every common Panel signal timestamp
`t`, the audit checks the exact `open` at `t+2h` for every asset. It never uses
nearest-time matching, forward/backward fill, resampling, carry, or substitution.

The structure is complete inside the common interval, but timestamp meaning is not
uniformly proven. Therefore this node clears only the previous structural
`common_next_open_mapping_unverified` blocker. It does not authorize execution-price
use or PnL computation.

## Replayed sources

- Portfolio mechanism SHA-256:
  `dcc8e9364efd900487608090ba879194a8a84195cbeab2dc63d0266df97406d5`
- Promotion-aware 1h evidence chain SHA-256:
  `06d1ec6bbb6bfc5222cb674227d3eaf926c2c970c1c2dcb8cc635a48f652a001`
- Promotion SHA-256:
  `36739b6fcff8ffa8150b3fdd268150cc7fc0c997d90d9fb028f3f3c096d0c24e`
- Panel SHA-256:
  `b7cdebac94f728a3588cb090391fb6024cb6783ec480b1fedc830e3bd4a32054`
- Legacy semantics SHA-256:
  `fc5f911cf333e6557547b19dc126f464df1d45e0d5e5d8eff99f35d5903a0190`
- Legacy semantics marker file SHA-256:
  `cee944dadd8d678ba89061b03b06dbbc3bbc83b01e3ecfc293e831070d6c1220`

The public mechanism loader verifies its marker and CSV hashes, locates and fully
replays the source chain, recomputes the mechanism in a temporary directory, and
rejects path, hash, identity, content, or replay drift.

## Evidence assessment

- BTC/USDT 1h: `partial_unverified`; a Binance archive and an OKX append config are
  preserved, but neither proves one end-to-end provider lineage and common bar-open
  meaning over the required interval.
- ETH/USDT 1h: `unknown`; no applicable provider semantics evidence is preserved.
- SOL/USDT 1h: `unknown`; no applicable provider semantics evidence is preserved.
- KNC/USDT, SWFTC/USDT, BICO/USDT 1h: `verified_open_time` through the direct frozen
  OKX public capture lineage.

Regular spacing or numerically plausible prices never upgrade semantics. Conflicting
valid meanings fail closed.

## Real mapping result

- Audit SHA-256:
  `df699d1ce90f740390bbe3e5ef4922e1cb1d0cfa55e15799c90c3b545867936e`
- Audit marker file SHA-256:
  `fb174d39de1179ec48512ebc5621cc9edad2f092042833576ad9231b395ff1f6`
- Datasets CSV SHA-256:
  `18a4bc3b96177fa654d60c2a547282dd246cc80d1209aa01f94473244bcaf163`
- Mappings CSV SHA-256:
  `0c8a889968bd5dfcf2e2567a80b7cfdceaf7413d7828b93ac38190989646c741`
- Constraints CSV SHA-256:
  `c1125025b465f1405b00eb38e484e054b54d19d4b98cde58d87af090297799c6`
- Signal timestamps: 20,424
- Mapping rows: 122,544
- Exact finite internal mappings: 122,532
- Final two timestamp tail rows: 12
- Internal missing, duplicate, off-grid, or non-finite mappings: 0

Two independent output directories produced identical filenames and bytes.

## Feasibility and safety boundary

- `structural_common_next_open_mapping_verified=true`
- `timestamp_semantics_uniformly_verified=false`
- `execution_price_mapping_feasible=false`
- `pnl_computation_authorized=false`
- Remaining blockers:
  - `legacy_timestamp_semantics_not_verified`
  - `timestamp_semantics_not_uniform`

No signal, position, turnover, cost, return, equity, benchmark, or PnL series is
computed. Profitability evidence, readiness change, and automatic factor approval
remain false. Static current-snapshot membership and unresolved survivorship bias
also remain disclosed limitations.

## Verification

- Specialty tests cover exact/tail mapping, missing/duplicate/off-grid/non-finite
  rejection, partial/unknown/conflict semantics, an all-verified authorization
  fixture, content-addressed collision, deterministic output, and CLI freezing.
- Specialty tests: 18 passed.
- Full suite: 344 passed.
- Branch coverage: 85.25% (required minimum 85%).
- Ruff: passed for all source and test files.
- Mypy safety/research core: 35 source files passed.
- Source/live safety scan: 3 passed.
- `git diff --check`: no content error; Windows CRLF conversion warnings only.
