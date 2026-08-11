# Phase 2 — Prospective Transition-Gated Membership Epoch Materialization & Replay MVP (2026-08-11)

## Outcome

The materializer consumes only three validated parent markers: a canonical
prospective snapshot transition, the previous membership bar gate, and the
append-only 1h segment chain. It derives the next membership epoch entirely
from that lineage; snapshot identity, `received_at`, epoch ordinal, signal
timestamps, execution timestamps, and membership symbols are not CLI inputs.

The real pending transition remains fail-closed:

- `status=blocked_transition_not_materialized`;
- `membership_epoch_materialized=false`;
- zero membership rows;
- `new_samples_counted=0`, `160/500`, and no network/economic/readiness work.

An admitted synthetic transition opens `epoch-0002` at the first full 1h bar
at or after the transition snapshot `received_at`. It emits six future-only
membership rows and one timing row with `epoch_end_resolved=false`; a future
snapshot is required to close the epoch. Replay recomputes parent lineage,
timing, membership rows, artifact bytes, and the content-addressed marker.

## Frozen invariants

- previous closed membership gate identity and bytes are unchanged;
- `future_only=true`, `historical_point_in_time_membership=false`;
- `retroactive_membership_change_prohibited=true`;
- `automatic_replacement_prohibited=true`, `historical_backfill_prohibited=true`;
- epoch end is never guessed and remains unresolved until a future snapshot;
- epoch 2 remains 160/500 with 340 intervals remaining;
- `new_samples_counted=0`, `network_activity_performed=false`;
- economic/PnL/readiness flags remain false;
- no candles, segment append, ledger, paper, or live action occurs.

## Verification

The focused suite covers real pending blocking and replay, synthetic accepted
transition → open epoch derivation, two output-directory determinism, lineage
and config guards, artifact tamper, and path/low-level guards. The real smoke
result is `blocked_transition_not_materialized`, zero membership rows, and
`epoch_end_resolved=false`. The synthetic fixture resolves
`membership_effective_at=2026-08-16T11:00:00Z`, emits six rows, and grants no
sample credit.

## Explicit exclusions

No real Aug-16 capture, receipt, snapshot, transition, membership gate
replacement, candle download, segment append, sample credit, turnover/cost/
capacity/return/PnL, winner selection, readiness change, paper trading, live
trading, or frontend work is included.
