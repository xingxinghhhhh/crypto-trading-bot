# Phase 2 — Prospective Capture Attempt Journal & Acceptance Receipt Contract (2026-08-10)

## Outcome

The repository now freezes the immutable attempt semantics that will govern
the future epoch-2 capture window. This node is deliberately offline: it
replays and validates the existing epoch-2 admission ticket, emits a
zero-attempt sentinel journal, and does not contact OKX or create a snapshot.

The only permitted future lifecycle is:

```text
attempt_started
  -> transport_failed
  -> attempt_started
  -> response_received -> validation_failed
  -> attempt_started
  -> response_received -> validation_passed -> accepted
```

Attempts are strictly numbered from `1`, immutable and append-only. A retry is
allowed only after transport or validation failure. The first
`validation_passed` attempt is immediately the sole accepted attempt; later
attempts, response selection, deletion, reordering, out-of-window receipts,
backfill, and cross-epoch ticket reuse fail closed.

## Frozen offline result

- journal status: `awaiting_future_attempt`
- journal identity: `c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da`
- epoch: `2`
- governed window: `[2026-08-16T10:00:00Z, 2026-08-16T11:00:00Z)`
- admission ticket: `ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3`
- attempt count: `0`
- accepted attempt count: `0`
- accepted snapshot identity: `null`
- current samples: `160 / 500`; remaining `340`
- network activity: `false`; actual attempt rows: `0`
- economic, PnL, and readiness authorization: `false`

The journal, policy, constraints, and JSON marker are content-addressed and
written atomically in that order. Replaying into two independent report
directories must produce the same identity, filenames, bytes, and hashes. The
sentinel row is a contract row and never pretends that a real attempt occurred.

## Explicit exclusions

This node does not execute the Aug-16 capture, make transport requests, store a
future response, create a snapshot/transition/gate/segment/ledger/readiness,
increase maturity, calculate turnover/cost/capacity/return/PnL, select a
winner, authorize paper/live, or stage/commit/push changes.
