# Phase 2 — Prospective Closed-Epoch Direct-OKX 1h Segment Admission & Request Contract MVP (2026-08-11)

## Outcome

The request-contract command connects a replay-validated closed membership
epoch to the append-only segment-chain tail without downloading market data.
It derives the segment start only from `next_canonical_segment_start`, the end
only from the closed epoch's last execution anchor, and the fixed six-asset
order from the validated chain lineage.

The current real blocked closure produces
`blocked_membership_epoch_not_closed`, zero request/asset rows, zero expected
canonical rows, and no capture. A synthetic closed epoch derives:

- `segment_start=2026-08-09T11:00:00Z`;
- `segment_end=2026-08-23T10:00:00Z`;
- `expected_bars_per_asset=336`;
- `expected_total_canonical_rows=2016`;
- six fixed assets and `capture_performed=false`.

The 336 bars are only request structure; they do not become maturity samples.

## Frozen invariants

- `current_samples=160`, `new_samples_counted=0`, `remaining_samples=340`;
- `network_activity_performed=false`, `network_capture_authorized=false`;
- `market_data_segment_is_not_sample=true`, `sample_credit_authorized=false`;
- canonical gap/overlap and historical recapture are prohibited;
- no asset replacement or universe mutation is permitted;
- request/asset/dependency/constraint artifacts are marker-last and
  output-directory independent.

## Verification

The focused suite covers the real blocked baseline, synthetic window derivation
and two-directory determinism, report/config tampering, and window/low-level
guards. Replay revalidates both parent markers and all derived rows before
accepting the content-addressed admission marker.

## Explicit exclusions

No HTTP request, OKX capture, OHLCV, segment append, sample credit, ledger,
readiness, economics/PnL, paper/live trading, or frontend work is included.
