# Phase 2 - Prospective Direct-OKX 1h Segment Evidence Materialization & Replay (2026-08-12)

## Outcome

The segment-evidence materializer closes the offline boundary between a frozen
future segment admission request and a local Direct-OKX capture marker. An
eligible request is accepted only after replaying every raw bundle, rebuilding
the canonical CSV, validating the exact 1H window and six-asset order, checking
raw/canonical hashes, and passing the shared OHLCV quality validator. The
result is a content-addressed `segment_candidate`; it is not an append.

The blocked path is intentionally complete without a capture argument. The
current real admission remains blocked, so it emits
`blocked_segment_admission_not_eligible` with zero asset/artifact rows and
`capture_consumed=false`. It never searches for or reads a synthetic capture.

## Frozen invariants

- `current_samples=160`, `sample_threshold=500`, `remaining_samples=340`;
  `new_samples_counted=0`.
- `segment_appended=false`, `segment_chain_append_authorized=false`, and no
  segment-chain writer is called.
- No network request, real capture, economic/PnL computation, readiness change,
  paper/live execution, or frontend work is performed.
- The canonical OHLCV validator in `market/data_quality.py` remains the only
  quality gate. Raw bundle replay and canonical reconstruction are independent
  of capture self-reported `valid` fields.
- Artifacts are content-addressed, collision-guarded, written marker-last, and
  deterministic across output directories.
- Synthetic lineage is propagated as `fixture_only=true`,
  `market_evidence=false`, `sample_evidence=false`, and
  `profitability_evidence=false`; these values are not CLI overrides.

## CLI

```bash
python -m crypto_bot.cli materialize-prospective-direct-1h-segment-evidence \
  --segment-admission <admission.json> \
  [--capture-report <capture-or-extension.json>] \
  --config config.prospective-direct-1h-segment-evidence.example.yaml \
  --output-dir reports/prospective-direct-1h-segment-evidence
```

`--capture-report` is required for an eligible admission and forbidden in
meaning (ignored) for a blocked admission. Start/end, assets, bar counts,
hashes, network, append, and sample-credit overrides are not exposed.

## Verification target

The focused synthetic suite proves the blocked baseline, an exact-match
336-bars-per-asset candidate (2016 total canonical rows), replay validation,
two-directory determinism, and fail-closed tampering of start/count/raw and
canonical evidence. Joint coverage keeps the prior admission, extension,
segment-chain, and closure identities unchanged.

This node only proves that an already-existing local capture exactly implements
the frozen request contract. It does not claim market profitability or readiness.
