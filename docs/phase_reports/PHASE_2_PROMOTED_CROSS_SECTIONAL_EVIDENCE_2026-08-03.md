# Phase 2 — Promotion-aware six-asset Rank IC + HAC/Holm evidence

Date: 2026-08-03

## Outcome

Implemented and replayed an offline, marker-only research chain for the promoted
BTC/ETH/SOL/KNC/SWFTC/BICO 4h convenience Panel. The chain validates the complete
promotion lineage before computing the frozen six factors at horizons 1, 4, and 16,
then applies the existing Newey-West/Bartlett HAC and Holm procedures to all 18
hypotheses.

No asset, Registry, Panel, dataset, factor, horizon, direction, HAC, or Holm override
is exposed by the new CLI. No portfolio, PnL, OOS, factor approval, profitability
claim, or readiness transition is produced.

## Implementation

- Added `src/crypto_bot/promoted_cross_sectional_research.py`.
- Added CLI command `analyze-promoted-cross-sectional-evidence`.
- Added a public, read-only promotion marker validator and dependency replay.
- Added optional promotion identity propagation to the existing Rank IC and
  calibration engines without changing their legacy outputs when absent.
- Added `tests/test_promoted_cross_sectional_research.py` and promotion loader
  coverage.
- Added the new module to CI mypy coverage.

## Real replay

- Promotion SHA-256: `3c7cbbbd5b3fbf5420a67df7e877c9899d8c83b97823150dde9747ee414e57f9`
- Research SHA-256: `ac0cf747399b6511e5239b7eb1cd5fef0471f7718a887fc593350e5d2665446c`
- Calibration SHA-256: `28f23c83a58de2b212e764fee360856c91540017c77b604599d44e0b0dee18e8`
- Chain SHA-256: `e578a4429830e2669571225e1b80d5c98ff0fbd8d88296eaa2b2fee88720d4f2`
- Candidate timestamps: 9,486 per hypothesis.
- IC rows: 170,748 (`9,486 * 18`).
- Valid IC timestamps: 170,340.
- Every valid IC has exactly six observation rows.
- Calibration hypotheses: 18, all with finite HAC statistics and raw/Holm p-values.
- Independent output-directory replay produced identical filenames and bytes.

Fourteen of the 18 fixed hypotheses have Holm-adjusted p-values at or below 0.05.
This is statistical evidence within this selected convenience universe, not proof of
economic value or stable profitability. The sample has no historical point-in-time
membership proof, survivorship bias remains unresolved, and timestamp semantics are
`mixed_unverified`.

## OOS exclusion

The existing OOS policy reserves the first half and splits the second half into ten
folds. With 9,486 timestamps, each fold has only 474 or 475 candidates before horizon
purging, below the fixed minimum valid sample count of 500. The threshold and fold
count were not changed, so OOS was explicitly excluded from this node.

## Preserved identities

- Original Registry SHA-256:
  `15f2ac5e77a812196394d9d7811ee2ce66a391d6b41d83187e050fdbb40ce509`
- Original Panel config SHA-256:
  `c29cca91009a27d0faad0a3fd9f2d216b9df7cdf10ca18161c33b0f5baee8d22`
- Promoted Registry SHA-256:
  `cf3ecbc7cb7c84b3e127568acb65b26597c07fb79e45c6a350ea67395587a42f`
- Promoted Panel config SHA-256:
  `c18a99849be5f3eb92dd329f9073ea9a5260d16bddb7aac4b635b5f30d1dc1ae`
- Promotion marker file SHA-256:
  `f1970eb81825911827b984566e0dd7476285a31ee94056216628b421899fb39e`
