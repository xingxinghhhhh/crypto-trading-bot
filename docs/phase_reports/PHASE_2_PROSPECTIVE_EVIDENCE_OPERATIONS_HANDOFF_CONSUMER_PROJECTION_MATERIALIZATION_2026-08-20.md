# Phase 2 — Governance-fresh consumer projection materialization MVP (2026-08-20)

## Scope

This node adds a controlled persistence boundary for the existing
`project_governance_fresh_current_operations_handoff` consumer projection. It
does not add a new validator, reducer, status model, manifest, sidecar, or
business/readiness state. The materializer is a writer adapter only:

```text
four explicit evidence inputs
  → existing freshness-gated projector
  → existing canonical projection formatter
  → UTF-8 bytes without terminal newline
  → SHA-256 content-addressed JSON artifact under artifacts/
```

The public result reports the projection, its byte digest, and the operational
path. The path is not included in the projection and cannot affect its digest.

## Frozen contract

- Python entry point:
  `materialize_governance_fresh_current_operations_handoff_projection(...)`.
- CLI entry point:
  `materialize-verified-current-operations-handoff-projection`.
- Default output directory:
  `artifacts/prospective-evidence-operations-handoff-consumer-projection`.
- Artifact filename:
  `prospective-evidence-operations-handoff-consumer-projection.<sha256>.json`.
- File bytes are exactly the existing formatter output encoded as UTF-8, with
  no terminal newline.
- First creation is atomic. Same bytes at the same name is an idempotent
  success; different bytes, symlinks, and non-file collisions fail closed
  without overwrite.
- Output paths must resolve below the repository `artifacts/` tree. `reports/`
  and path traversal are rejected.
- All upstream freshness, rollover, assembly, parent, and schema decisions
  remain owned by the existing projector and its chain. No latest discovery,
  automatic repair, network activity, execution, or economic computation is
  introduced.

## Verification

Targeted materialization tests: **14 passed**.

Projection/freshness/read-model regression (including the new materializer):
**59 passed**. Complete rollover-to-verifier joint chain: **142 passed**.
Fresh full suite: **862 passed**, branch coverage **85.07%**. Ruff, source
safety/live guard (**3 passed**), `git diff --check`, and the complete fixed
mypy target set (**82 source files**) also pass locally.

Remote Python 3.11/3.12 CI closure is still pending for this node.

## Explicit exclusions

This remains Phase 2 offline evidence/governance/consumer hardening. It does
not add API or frontend pages, economic/PnL evidence, strategy promotion,
Paper/Live execution, exchange trading, or any readiness claim.
