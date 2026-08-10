# Phase 2 — Direct-OKX execution cost and capacity evidence contract

Date: 2026-08-09

## Scope and safety boundary

This node freezes a common fee, spread, slippage-stress, and participation-cap
contract for all 36 preregistered variants. It is an evidence/policy layer only:
it does not calculate a cost amount, return, turnover, equity, benchmark, or PnL,
and it does not authorize a winner, paper/live execution, or readiness.

## Confirmed repository inputs

- No existing OKX public fee-rate artifact was present; the new fee artifact is
  explicitly a `conservative_policy_assumption`, not a user-specific rate.
- Canonical Direct-OKX CSVs preserve `volume` as base volume. Raw response
  `volCcy`/`volCcyQuote` fields remain outside canonical CSV and their recapture
  mutability is already observed by the pinned mutability audit.
- Baseline migration and its first capture remain pinned; comparison capture
  cannot replace the capacity baseline.

## Frozen contract

- Preregistration identity:
  `28afc8ec130aef5da62b857064f3a4f5aa98fcffb06d390a02430748fb5c6a68`
- Direct execution mapping identity:
  `48df5c8d1d8f2ee76e45f85920ded6ce37dc25422a14e1b54626668f8aec9ddb`
- Market: spot / USDT
- Fee: taker-conservative, 100 bps policy assumption, account/VIP/private API
  not used, user-specific rate unverified
- Historical spread: not directly observed; fixed stress assumptions only
- Slippage stress: `[0, 5, 10]` bps, uniform across all 36 variants
- Capacity: 1% participation cap on canonical base `volume`
- Version warnings: `historical_volume_version_pinned=true`,
  `capacity_evidence_version_sensitive=true`,
  `capacity_not_execution_guarantee=true`

## Acceptance result

The content-addressed cost contract marker is:

- Cost identity:
  `5633858d1e4377c507953df65f3014c30b3d98d1ec4f36c72bf1fa4c5274dc21`
- Fee CSV SHA-256:
  `da05f0c77b0721ea03002de40f1a26103b2e0a501c16c7602177f1b1bf97fe23`
- Stress CSV SHA-256:
  `180781982143dfb755accbe8c90044b16bba535b122665a8367fc1ad78d42501`
- Constraints CSV SHA-256:
  `aaa2a83f602a176ff2ad811bf08d96d388c6dc5eb27c6682e7a53e37eb381595`
- Marker JSON SHA-256:
  `2d74a17fb8e530f04594e418bfa20daec86821132af620fb046ba9a71e63b5fb`

The report records `execution_price_mapping_feasible=true`,
`cost_contract_frozen=true`, and `pnl_computation_authorized=false`. Outputs
are written atomically in fee CSV → stress CSV → constraints CSV → JSON marker
order; output directory and runtime metadata do not enter the identity.
