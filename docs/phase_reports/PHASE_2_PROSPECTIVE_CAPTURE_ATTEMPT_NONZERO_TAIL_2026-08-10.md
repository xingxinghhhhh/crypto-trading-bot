# Phase 2 — Prospective Non-Zero Receipt Tail Continuation & Replay API (2026-08-10)

## Planned scope

Extend the frozen prospective capture-attempt receipt chain so a validated
non-zero failure tail can be replayed and continued offline. The existing
zero-receipt identity, receipt schema, and real baseline artifacts remain
unchanged.

## Repository-validated assumptions

- The audit CLI already materializes the canonical receipt CSV, state CSV,
  constraints CSV, and marker-last report; the public validator can replay
  those artifacts without performing network or economic work.
- The receipt CSV contains every field required to recompute each receipt
  content hash and previous-hash link.
- The evidence adapter is the only zero-parent-only admission point; it can
  derive the next attempt and previous receipt hash from a validated parent.
- The existing canonical receipt hash function is the contract for replay and
  is reused rather than introducing a second hash algorithm.

## Implemented contract

- Added public `validate_capture_attempt_receipt_chain()` and
  `load_validated_capture_attempt_receipts()` APIs.
- The validator checks report identity, all content-addressed artifacts,
  receipt CSV schema and hashes, strict receipt order, previous-hash links,
  journal/ticket/request-policy bindings, outcome semantics, and replayed
  state. It derives `receipt_count`, `next_attempt_number`,
  `chain_status`, `accepted_attempt_count`, and `next_attempt_permitted`
  instead of trusting self-reported state.
- A non-zero failure tail is explicitly `retry_open`: one or more
  `transport_failed`/`validation_failed` receipts, zero accepted attempts,
  and the next attempt permitted. An accepted receipt produces
  `accepted_closed` and cannot be used as an adapter parent.
- The evidence adapter now accepts either the validated zero-receipt
  `awaiting_attempt` state or a validated non-zero `retry_open` state. It
  derives attempt `N+1`, binds the previous receipt hash, and audits the full
  parent-plus-new receipt sequence before writing marker-last artifacts.
- No new CLI or configuration was needed; the existing audit and materialize
  commands are reused. No frontend, permissions, network request, real
  capture, sample increment, economic computation, PnL, or readiness change
  was introduced.

## Synthetic replay evidence

- Offline chain 1: `attempt 1 transport_failed` → `retry_open`, count `1`,
  next attempt `2`.
- Offline continuation: adapter materializes `attempt 2 validation_failed`
  from chain 1; chain 2 replays both receipts, preserves receipt 1 bytes and
  hash, reports count `2`, and permits attempt `3`.
- An accepted parent is rejected as non-retryable; a pass after failures is
  `accepted_closed`.
- The synthetic reports live under content-addressed prefixed directories;
  they are fixture-only and do not alter the real zero baseline.

## Verification record

- Non-zero tail tests: `3 passed`.
- Joint receipt/journal/adapter/admission/safety/live set: `80 passed`.
- Fresh full suite: `572 passed`; branch coverage `85.34%` (project threshold
  `85%`).
- CI mypy list: `56` source files, `Success: no issues found`.
- Ruff `src tests`: all checks passed; `git diff --check` passed (only
  Windows line-ending notices).
- Real zero-receipt CLI recheck preserved chain identity
  `3fef210901ee2ba1db387ff8468da069809f4d0c0a6914f87bedaa699b89b538`,
  receipt count `0`, status `awaiting_attempt`, next attempt `1`,
  `network_activity_performed=false`, and `160/500` samples (`340` remaining).

## Explicit exclusions

No stage, commit, push, reset, clean, deletion, real market request, sample
creation, economic/PnL calculation, readiness decision, paper/live
authorization, or profitability claim.
