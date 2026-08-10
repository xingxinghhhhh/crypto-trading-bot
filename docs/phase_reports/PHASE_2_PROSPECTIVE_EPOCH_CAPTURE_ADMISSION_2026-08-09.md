# Phase 2 — Prospective Epoch-2 Snapshot Admission (2026-08-09)

## Outcome

An immutable, one-time admission ticket is now frozen for epoch 2. It binds
the validated assembly state, the Sunday 2026-08-16 10:00–11:00 UTC window,
the latest accepted snapshot, and the append-only market-chain start at
2026-08-09 11:00 UTC.

The request policy is public OKX `instruments` with `instType=SPOT`, no
authentication or account context. Only the first validator-complete capture
in the window can be accepted; retry is limited to transport/validation
failure within the same window, with no out-of-window acceptance or backfill.

## Current offline acceptance

- ticket status: `pending_future_window`
- epoch: 2; state: `awaiting_capture_window`
- current samples: 160 / 500; remaining 340
- accepted snapshots: 0; network activity: false; snapshot created: false
- economic rows: 0; economic authorization: false

Outputs are content-addressed and atomically written as ticket,
request-policy, constraints, then the JSON marker. No future response is
stored and no market data is fetched.

## Explicit exclusions

This node does not connect to OKX, execute the Aug-16 capture, create a
snapshot/transition/gate/segment/ledger/readiness, increase maturity,
calculate economic values, select a winner, authorize paper/live trading, or
stage/commit/push changes.
