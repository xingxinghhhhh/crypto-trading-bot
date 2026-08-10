# Phase 2 — Prospective Direct-OKX 1h Segment Chain (2026-08-09)

## Outcome

The existing immutable baseline and first future-only Direct-OKX extension are
now registered as an append-only segment chain. Segment 1 covers
`2026-08-02T15:00:00Z` through `2026-08-09T10:00:00Z` with 164 canonical bars
per asset (984 total). The baseline tail is `2026-08-02T14:00:00Z`, so the only
legal next canonical start is derived as `2026-08-09T11:00:00Z`.

The chain stores identities and hashes, not a rewritten cumulative OHLCV CSV.
Previously accepted segments cannot be replaced, overlapping or gapped
segments fail closed, and raw page rows outside the canonical window cannot
leak into the segment. Segment metadata is not a sample and does not change
the 160/500 maturity gate.

## Current offline acceptance

- segment count: 1
- asset-segment rows: 6
- current chain tail: `2026-08-09T10:00:00Z`
- next canonical segment start: `2026-08-09T11:00:00Z`
- next segment end: unresolved until a future membership gate is accepted
- unique closed execution intervals: 160 / 500; remaining 340
- turnover/cost/capacity/return/PnL rows: 0

Outputs are content-addressed and committed atomically in the order
`segments.csv`, `assets.csv`, `constraints.csv`, then the JSON marker. Replay
in an independent output directory is byte-identical.

## Explicit exclusions

This node does not connect to OKX, capture the 2026-08-16 snapshot, fetch new
candles, create a second membership/readiness epoch, increase maturity,
calculate economic values, select a winner, authorize paper/live trading, or
stage/commit/push changes.
