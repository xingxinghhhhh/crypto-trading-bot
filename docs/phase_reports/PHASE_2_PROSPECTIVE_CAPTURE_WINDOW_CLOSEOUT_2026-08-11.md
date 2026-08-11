# Phase 2 — Prospective Epoch-2 Capture Window Closeout (2026-08-11)

## Planned scope

Freeze the window-level finalization contract above the existing admission
ticket, attempt journal, receipt-chain replay API, and evidence adapter. The
contract is offline and derives a closeout state from validated artifacts plus
one local `observed_at` timestamp; it does not perform the Aug-16 capture.

## Derived state contract

- A validated `accepted_closed` receipt chain remains `accepted_closed` and
  retains its first accepted snapshot identity.
- Before `2026-08-16T11:00:00Z`, an unaccepted `awaiting_attempt` or
  `retry_open` chain remains `pending_window_end` and retryable.
- At or after the governed end, an unaccepted chain derives
  `missed_no_backfill`, disables retry, and cannot receive retroactive
  receipts, snapshots, sample credit, or schedule shifts.
- The closeout evidence is exactly `{schema_version, observed_at}`. Final
  state, acceptance, missed status, retry permission, and snapshot identity
  are never accepted as caller overrides.

## Implemented artifacts and CLI

- `src/crypto_bot/market/prospective_capture_window_closeout.py`
- `config.prospective-capture-window-closeout.example.yaml`
- `tests/test_prospective_capture_window_closeout.py`
- `docs/phase_reports/PHASE_2_PROSPECTIVE_CAPTURE_WINDOW_CLOSEOUT_2026-08-11.md`
- Existing `src/crypto_bot/cli.py`, `README.md`, and CI mypy list updated.
- CLI: `audit-prospective-capture-window-closeout --receipt-chain ...
  --admission-ticket ... --closeout-evidence ... --config ... --output-dir ...`
- Marker-last deterministic artifacts:
  `state.csv → dependencies.csv → constraints.csv → marker.json`.

## Explicit exclusions

No network request, real OKX capture, attempt/receipt creation, snapshot,
transition, membership gate, segment-chain update, ledger/readiness update,
sample increment, turnover/fee/spread/slippage/capacity/return/PnL, winner
selection, paper/live authorization, retroactive backfill, schedule shift,
reset, clean, deletion, stage, commit, or push is part of this node.

## Baseline invariants

- Real receipt chain: `3fef210901ee2ba1db387ff8468da069809f4d0c0a6914f87bedaa699b89b538`.
- Journal: `c3eb67624e1797aaed7b9ef7ad97c17c358dfadcaba30743d0b760585466d2da`.
- Admission ticket: `ecffe826d4dbae57584bc1f63edb1c05898fbab52fd6b0d7bbeb545022fa3df3`.
- Epoch-2 window: `[2026-08-16T10:00:00Z, 2026-08-16T11:00:00Z)`.
- Samples remain `160/500`, with `340` remaining; network/economic/readiness
  flags remain false.

## Verification record

- Closeout targeted tests: `7 passed`.
- Joint closeout/receipt/journal/adapter/admission/safety/live set:
  `87 passed`.
- Fresh full suite: `579 passed`; coverage `85.26%` with branch coverage
  enabled (project threshold `85%`).
- Ruff `src tests`: all checks passed.
- CI mypy list: `57` source files, `Success: no issues found`.
- `git diff --check` passed (only Windows line-ending notices).
- Real zero-receipt CLI closeout recheck with local pre-window evidence
  derived `pending_window_end`, `closeout_eligible=false`,
  `replayed_chain_status=awaiting_attempt`, `receipt_count=0`,
  `retry_permitted=true`, and preserved chain identity
  `3fef210901ee2ba1db387ff8468da069809f4d0c0a6914f87bedaa699b89b538`.
- The same marker was revalidated through the public closeout validator;
  network activity, sample count, economic authorization, and readiness all
  remained unchanged.
