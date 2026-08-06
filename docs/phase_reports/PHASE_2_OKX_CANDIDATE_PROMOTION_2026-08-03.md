# Phase 2 — Audited OKX Candidate Promotion and Six-Asset 4h Panel

Date: 2026-08-03

## Outcome

The frozen KNC-USDT, SWFTC-USDT, and BICO-USDT OKX convenience-universe capture is now
promoted into a stable, independently auditable six-asset 4h Registry and `inner_exact`
Panel without modifying the original Registry, original Panel config, or any old
BTC/ETH/SOL CSV. The promotion is an offline data-governance step only. It does not run
factors, IC, HAC, OOS, a portfolio, PnL, paper trading, or live trading.

## Implemented surface

- `src/crypto_bot/market/dataset_promotion.py`
- `config.okx-universe-promotion.example.yaml`
- `tests/test_dataset_promotion.py`
- `promote-okx-universe-candidates` CLI
- public, read-only capture/intake validation results in
  `src/crypto_bot/market/okx_universe_intake.py`
- README, CI mypy coverage, and generated-artifact ignore rules

The existing offline intake audit and promotion now share the same raw response replay,
cursor, nine-field, `confirm=1`, CSV reconstruction, quality, raw hash, canonical hash,
selection, and capture identity validation. Promotion does not duplicate or weaken that
trust boundary.

## Frozen inputs

- Base Registry SHA-256:
  `15f2ac5e77a812196394d9d7811ee2ce66a391d6b41d83187e050fdbb40ce509`
- Base Panel config SHA-256:
  `c29cca91009a27d0faad0a3fd9f2d216b9df7cdf10ca18161c33b0f5baee8d22`
- Timestamp semantics SHA-256:
  `52877c340a0531381e26eaa5e45ed4e84d84b420b6c5324948df529acae5f408`
- OKX capture SHA-256:
  `b96aa6011796c8e2f1e0d7826c0f95b9f5be15fbdf2cf38862493f3fc75da451`
- Offline intake SHA-256:
  `d39c44ea177cc735330e0cea5071bc59527846402de397df6458f1ef65725c03`
- Candidate order and dataset IDs:
  - `KNC-USDT` → `okx_knc_usdt_4h_b96aa6011796`
  - `SWFTC-USDT` → `okx_swftc_usdt_4h_b96aa6011796`
  - `BICO-USDT` → `okx_bico_usdt_4h_b96aa6011796`

## Stable artifacts and commit order

Stable promoted data root:

`data/promoted/okx_convenience_v1/`

The atomic order is:

1. three byte-exact promoted CSVs;
2. three content-addressed lineage manifests;
3. repository-root promoted Registry;
4. report-directory Registry audit;
5. repository-root promoted Panel config;
6. report-directory Panel audit;
7. final report-directory promotion marker.

No marker is produced after a failed stage. Orphaned content-addressed dependencies may
remain, but consumers must require the final marker. An existing same-name/same-byte
artifact is idempotent; same-name/different-byte content is rejected.

Real artifact identities:

- Promotion SHA-256:
  `3c7cbbbd5b3fbf5420a67df7e877c9899d8c83b97823150dde9747ee414e57f9`
- Promoted Registry SHA-256:
  `cf3ecbc7cb7c84b3e127568acb65b26597c07fb79e45c6a350ea67395587a42f`
- Promoted Panel config SHA-256:
  `c18a99849be5f3eb92dd329f9073ea9a5260d16bddb7aac4b635b5f30d1dc1ae`
- Registry audit SHA-256:
  `42ade91745385a5deda651d2b4a1b01bf335240e1403a6a80b18be1448e3b663`
- Six-asset Panel SHA-256:
  `68f57ecd7fcfef759de401578f4c87b915b5c25e97d875afbf0ff434f34c5627`

## Real Panel acceptance

- common range: `2022-01-01T00:00:00+00:00` through
  `2026-04-30T20:00:00+00:00`
- intersection bars: `9486`
- union bars: `14135`
- coverage: `0.671100106120`
- BTC and ETH dropped before the common start: `4088` each
- SOL dropped before the common start: `3047`
- KNC, SWFTC, and BICO dropped after the common end: `561` each
- dropped or missing inside the common window: `0` for all six components

The three promoted CSV copies preserve the capture hashes:

- KNC raw/canonical:
  `90adec15128c4502f18d241e8704c8ed51d024ae93891a43e85d9657889b9ee9`
- SWFTC raw:
  `418f6b4d75e65b4c66d0ac47593b31ce8bcae58fde8fc6ed80adf7c56bea1e4e`;
  canonical:
  `990d5abdf89d155cc0b34fa11d3c8b8371fb806aa47d77ccb243699643ecc89d`
- BICO raw/canonical:
  `8340d7e461ea13e55b181deeb96312368d07b0a48f0a92f8066f33b2d9a39195`

Two independent report output directories produced the same filenames, promotion
identity, and marker bytes. The original Registry and Panel config hashes remained
unchanged.

## Timestamp semantics and claims boundary

The generic Panel builder remains unchanged and reports `unverified`. The promotion
marker binds a separate evidence assessment:

- BTC/ETH/SOL 4h: `partial_unverified`
- KNC/SWFTC/BICO 4h: `verified_open_time`
- aggregate: `mixed_unverified`
- `timestamp_semantics_uniform=false`
- old datasets upgraded: `false`

The fixed claims remain:

- `universe_kind=current_okx_live_convenience_snapshot`
- `historical_point_in_time_membership=false`
- `survivorship_bias_resolved=false`
- `profitability_evidence=false`
- `strategy_approval=false`
- readiness unchanged

## Quality gates

- promotion/intake specialty tests: `24 passed`
- full suite: `306 passed`
- total branch coverage: `85.29%` (required: 85%)
- Ruff: passed
- mypy safety/research core: `30 source files` passed
- source/live safety: `3 passed`
- diff check: no content errors (only existing Windows CRLF conversion warnings)
