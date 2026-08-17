# Phase 2 — Prospective Evidence Operations Portable Handoff Bundle

## Scope

This node adds a deterministic, directory-based handoff artifact for an
already validated prospective-evidence operations snapshot. It does not add
market capture, sample credit, append, economic/PnL computation, readiness,
Paper, live execution, frontend, HTTP API, or trading behavior.

## Implemented contract

- `crypto_bot.market.prospective_evidence_operations_bundle` validates the
  source snapshot, traces the files actually read by its governed validators,
  and materializes the minimal transitive closure.
- Payload files retain their original bytes. Inventory, dependency, and policy
  sidecars are deterministic and content-addressed.
- An explicit `replay_root` was added to the operations-snapshot validator;
  `None` preserves the original repository behavior.
- Bundle validation stages only the declared payload and replays the existing
  public validators. Missing, extra, tampered, unsafe, or authorization-
  escalating content fails closed.
- CLI entry point:
  `build-prospective-evidence-operations-bundle --operations-snapshot ...`
- The default CLI output is
  `artifacts/prospective-evidence-operations-bundle`, outside `reports/`.

## Real blocked-state smoke

The current frozen snapshot remains unchanged after bundle replay:

| Field | Value |
|---|---|
| current / threshold / remaining | `160 / 500 / 340` |
| governed stage | `awaiting_real_membership_epoch_progress` |
| next legal action | `await_real_membership_epoch_progress` |
| append / economic / PnL / Paper / live | all `false` |
| state changed / network activity | `false / false` |
| new samples counted | `0` |

The bundle is evidence transport only; it does not change those fields or
grant any authorization.

## Verification record

- Targeted bundle suite: `python -m pytest tests/test_prospective_evidence_operations_bundle.py -q` — **12 passed**.
- Joint replay/fixture suite (bundle, snapshot, append authorization/preflight/
  evidence/admission, economic readiness/maturity, epoch assembly, membership
  closure/materializer, and fixture restore): **127 passed**.
- Full suite: `python -m pytest tests -q --cov=crypto_bot --cov-report=term --cov-report=xml` — **748 passed**, total branch coverage **85.10%**.
- Safety/live guard regression: **6 passed**.
- `python -m ruff check src tests` — passed.
- Fixed-module mypy check for the bundle and snapshot validators — passed.
- `git diff --check` — passed.
- Real CLI smoke produced a portable bundle with **135 payload files** and
  replayed the frozen `160/500/340` blocked state from the source-isolated
  staging root.

Remote GitHub Actions and the final commit/push remain release steps for this
node. This report is not a strategy-profitability or launch-readiness decision.
