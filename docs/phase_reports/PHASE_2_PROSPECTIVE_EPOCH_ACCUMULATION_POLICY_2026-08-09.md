# Phase 2 — Prospective Epoch Accumulation Policy (2026-08-09)

## Outcome

The project now freezes a weekly, future-only snapshot cadence while the
prospective economic sample remains below its 500 closed-interval floor. The
policy accepts at most the first validator-complete snapshot in the Sunday
10:00–11:00 UTC window, allows transport/validation retries only within that
window, rejects out-of-window captures and historical backfill, and keeps the
schedule fixed after a missed window.

The first epoch (160 intervals) is explicitly marked as collected before this
cadence policy; it is not retrospectively treated as preregistered. The only
stop condition is `sample_maturity_met`; all economic computation and
profitability claims remain false.

## Current offline acceptance

- current unique closed intervals: 160
- minimum required: 500
- remaining: 340
- cadence: weekly
- first governed window: `[2026-08-16T10:00:00Z, 2026-08-16T11:00:00Z)`
- accumulation should continue: true
- turnover/cost/capacity/return/PnL rows: 0

The four outputs are content-addressed and written atomically: schedule,
protocol, constraints, then the JSON marker. Re-running in another output
directory yields identical identities, filenames, bytes, and hashes.

## Explicit exclusions

This node does not connect to OKX, capture a new snapshot, extend candles,
accumulate samples, calculate turnover/cost/capacity/returns/PnL, select a
variant, authorize trading, or stage/commit/push changes.
