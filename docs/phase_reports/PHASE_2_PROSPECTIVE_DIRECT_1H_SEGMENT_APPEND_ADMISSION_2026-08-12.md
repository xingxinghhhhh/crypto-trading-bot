# Phase 2 — Prospective Direct-OKX 1H Segment Append Admission

## Scope

This node creates an immutable admission contract between a replay-validated
segment candidate and the current append-only segment chain. It revalidates
both parent markers, derives the current chain tail and next segment ordinal,
and admits a candidate only when its start is exactly the chain's next
canonical start.

The node does not append the candidate or modify the chain. A blocked evidence
parent remains blocked and produces zero candidate asset rows. Stale, duplicate,
tampered, or non-contiguous candidates fail closed.

## CLI

```bash
python -m crypto_bot.cli freeze-prospective-direct-1h-segment-append-admission \
  --segment-evidence <validated-evidence.json> \
  --segment-chain <current-chain.json> \
  --config config.prospective-direct-1h-segment-append-admission.example.yaml \
  --output-dir reports/prospective-direct-1h-segment-append-admission
```

The output is content-addressed and marker-last. It contains admission,
asset, dependency, constraint, and report artifacts. `admitted` means only
that a future append materializer may consume the candidate; it is not a
mutation or trading authorization.

## Invariants

- `segment_appended=false` and `chain_mutation_performed=false`;
- no network, sample credit, economic/PnL, readiness, paper, or live action;
- candidate start and chain next start must match exactly;
- candidate ordinal is derived as current segment count plus one;
- stale and duplicate candidates are rejected;
- old evidence, chain, capture, and canonical artifacts are read-only.

## Verification target

Targeted tests cover admitted synthetic continuity, blocked evidence, stale
candidate rejection, tampered chain state, deterministic output, and the
no-override interface. The real local smoke remains blocked until the
membership epoch is eligible.
