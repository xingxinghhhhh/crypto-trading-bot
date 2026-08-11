# Phase 2 — Prospective Admitted Snapshot Transition Materialization & Replay Contract MVP (2026-08-11)

## Outcome

An admission-gated materializer now turns only an `admitted`
`prospective_snapshot_transition_admission` into canonical future-only
snapshot transition evidence. It consumes no snapshot CLI inputs. Previous and
current markers are derived from the validated admission lineage and replayed
through the existing public snapshot validator; the existing
`build_future_universe_transition_rows()` seam supplies the established diff,
tracked-six, exit, and no-automatic-replacement semantics.

Pending and missed admissions are deterministic blocked materializations:

- `blocked_pending_closeout` → `transition_materialized=false`, zero changes;
- `blocked_missed_epoch` → `transition_materialized=false`, zero changes.

An admitted synthetic pair materializes the canonical changes, tracked-assets,
and policy rows, then writes dependencies, constraints, and the marker last.
The public materialization validator revalidates the admission, snapshots, and
row bytes, so drift, replacement, backfill, identity mismatch, and manual
override fail closed.

## Frozen invariants

- epoch 2 remains 160/500 with 340 intervals remaining;
- `future_only=true`, `replacement=false`, `historical_backfill=false`;
- `transition_created=false`; no membership gate is produced;
- no network, economic/PnL, readiness, paper, or live action occurs;
- old `okx-universe-transition.<sha>.json` identity semantics remain unchanged;
  the new contract uses the shared pure row builder without duplicating the
  diff algorithm.

## Verification

The node covers real pending blocking, synthetic accepted materialization and
replay, CLI/config, tamper, and path guards. The real pending admission remains
blocked and produces no transition rows. Full project quality gates are run
after the node is complete.

Final quality evidence: materializer-focused tests passed 7; the combined
materializer/admission/closeout/rollover/archive/safety set passed 122; the
fresh full suite passed 600 tests with branch coverage 85.26% (threshold 85%).
Ruff passed for `src tests`, CI mypy passed for 60 source targets, and
`git diff --check` passed. The real pending admission remains
`blocked_pending_closeout`, `transition_materialized=false`, zero change rows,
and 160/500 samples.

## Explicit exclusions

No Aug-16 capture, attempt/receipt/snapshot creation, real transition,
membership gate, candle download, segment update, sample credit,
turnover/cost/capacity/return/PnL, winner selection, readiness change, paper
trading, or live trading is performed.
