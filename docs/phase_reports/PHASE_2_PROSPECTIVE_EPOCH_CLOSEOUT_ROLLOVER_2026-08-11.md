# Phase 2 — Prospective Epoch Closeout Consumption & Rollover Contract MVP (2026-08-11)

## Outcome

The closeout result is now consumed by a deterministic, marker-last rollover
contract. The contract binds the validated epoch-2 closeout to the frozen
assembly, accumulation policy, and sample-maturity artifacts and derives one
of three future-only actions:

- `pending_window_end` → `action=hold`; epoch 2 remains unfinished and no
  next-window admission is created.
- `accepted_closed` → `action=transition_eligible`; epoch ordinal remains 2,
  the accepted snapshot identity is required, and no rollover is emitted.
- `missed_no_backfill` → `action=rollover_next_window`; epoch 2 receives zero
  new sample credit, `epoch_2_closed_without_sample=true`, and the next fixed
  weekly window is epoch 3, 2026-08-23 10:00–11:00 UTC.

## Frozen inputs and invariants

- epoch 2: 2026-08-16 10:00–11:00 UTC; 160/500 eligible closed intervals;
  340 remain;
- assembly identity: `674116...`;
- accumulation-policy identity: `8f4a...`;
- sample-maturity identity: `32cf...`;
- latest accepted snapshot remains `d7e5...` and the next market-segment
  start remains 2026-08-09 11:00 UTC;
- weekly Sunday 10:00–11:00 UTC cadence is unchanged; historical backfill,
  schedule shifts, network activity, economic/PnL computation, and readiness
  changes are prohibited.

All parent reports are revalidated through their public validators and their
content-addressed artifact bytes are checked before rollover output is
accepted. Output includes deterministic state, dependencies, constraints, and
claims artifacts followed by the JSON marker; tampering or path escape fails
closed.

## Verification

The node has focused tests for pending, accepted, missed, CLI/config, parent
identity, artifact, and output-path guards. The real zero-receipt baseline is
unchanged: 160 samples, 340 remaining, no network/economic/readiness state.

## Explicit exclusions

This node does not execute the next capture window, create an admission ticket,
perform a snapshot or transition, fetch OKX data, backfill a missed window,
calculate returns/costs/PnL, approve a factor/strategy, or authorize paper/live
trading.
