# Phase 2 — Prospective Accepted-Closeout → Snapshot Transition Admission Contract MVP (2026-08-11)

## Outcome

The accepted closeout branch now has a separate, pure offline admission
contract. It answers which snapshot pair may be consumed by a later transition
builder without creating that transition:

- `pending_window_end` + `hold` → `blocked_pending_closeout`;
- `missed_no_backfill` + `rollover_next_window` → `blocked_missed_epoch`;
- `accepted_closed` + `transition_eligible` → `admitted`.

The admitted branch binds the previous snapshot to the validated pre-epoch
latest accepted snapshot and the current snapshot to the one accepted receipt
retained by the closeout's validated receipt chain. The current marker is
replayed through the existing public future-universe snapshot validator, must
match the retained identity, and must have a received timestamp strictly after
the previous snapshot. No CLI or config field can override either identity.

## Frozen invariants

- epoch 2 window: 2026-08-16 10:00–11:00 UTC;
- current maturity remains 160/500, with 340 intervals remaining;
- `transition_created=false` for every result;
- pending and missed branches have no current snapshot identity;
- accepted branch has exactly one accepted receipt and no response
  cherry-picking or snapshot replacement;
- historical backfill, network activity, economic/PnL computation, and
  readiness changes remain prohibited.

Outputs are content-addressed and marker-last:

1. `admission.csv`;
2. `dependencies.csv`;
3. `constraints.csv`;
4. JSON marker.

Same-name/same-byte replay is idempotent; same-name/different-byte collision,
parent drift, path escape, identity drift, ordering drift, and manual override
fail closed.

## Verification

The node includes real pending, synthetic missed, synthetic accepted, snapshot
ordering, CLI/config/path, and tamper tests. The real zero-receipt chain and
all existing parent identities remain unchanged. The node is intentionally
offline and does not create a snapshot or transition.

Final quality evidence: the admission/closeout/rollover/receipt/journal/
adapter/admission/safety/live joint set passed 98 tests; the fresh full suite
passed 593 tests with branch coverage 85.19% (threshold 85%). Ruff passed for
`src tests`, the CI mypy list passed for 59 source targets, and `git diff
--check` passed. The real zero-chain replay still reports chain
`3fef2109...`, zero receipts, `awaiting_attempt`, 160/500 samples, and all
network/economic/readiness flags false. The real admission CLI/API reports
`pending_window_end`, `hold`, `blocked_pending_closeout`, and
`transition_created=false`.

## Explicit exclusions

No Aug-16 capture, attempt/receipt creation, snapshot transition, membership
gate, candle download, segment update, ledger/readiness update, sample credit,
turnover/cost/capacity/return/PnL, winner selection, paper trading, or live
trading is performed.
