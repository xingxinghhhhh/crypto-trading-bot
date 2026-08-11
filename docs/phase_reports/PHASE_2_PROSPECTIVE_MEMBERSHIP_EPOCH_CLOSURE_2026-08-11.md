# Phase 2 — Prospective Open Membership Epoch Closure & Closed-Gate Materialization MVP (2026-08-11)

## Outcome

The closure contract accepts only an `open_pending_future_close` membership
epoch and a later, replay-validated materialized transition. The transition
must be the exact `B→C` successor of the open epoch's current snapshot; no
snapshot, timestamp, epoch boundary, membership, or sample-count override is
accepted.

The real pending chain remains blocked with
`status=blocked_no_open_membership_epoch`, zero membership rows, zero closed
intervals, and zero sample credit. A synthetic local chain closes the open
epoch using the existing 1h timing semantics. For an open start at
`2026-08-16T11:00Z` and a next snapshot at `2026-08-23T10:30Z`, the unique
last signal is `2026-08-23T08:00Z`, its execution anchor is
`2026-08-23T10:00Z`, and the structural interval count is 166.

## Frozen invariants

- closed membership rows are byte-identical to the open epoch;
- `epoch_end_resolved=true` only after the next transition is validated;
- `strict_execution_before_next_snapshot=true`;
- `future_only=true`, historical PIT membership and survivorship resolution are
  not claimed;
- `sample_credit=0`, `new_samples_counted=0`, and the global 160/500 gate is
  unchanged;
- network, economic/PnL, readiness, paper, and live flags remain false;
- no candles, segment append, ledger, replacement, or backfill occurs.

## Verification

The focused suite covers real blocked parents, synthetic `B→open epoch→B→C`
closure, strict last-signal/execution derivation, output-directory
determinism, lineage/config guards, and marker tamper/path guards. Replay
rebuilds the closure timing, membership bytes, dependencies, constraints, and
identity from the two validated parents.

## Explicit exclusions

No real Aug-16 capture, receipt, snapshot, transition, membership change,
candle download, segment append, sample credit, turnover/cost/capacity/
return/PnL, winner selection, readiness, paper/live trading, or frontend work
is included.
