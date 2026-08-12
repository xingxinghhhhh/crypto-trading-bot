# Phase 2 — Prospective Direct-OKX 1H Prepared Shadow Chain

This node materializes a deterministic, non-authoritative shadow image from a
validated append plan and the current authoritative chain. It preserves every
existing segment reference and adds only the one validated candidate reference
when the plan is ready. The expected post-append identity is derived from the
canonical proposed reference rows.

The output is never a current chain: `authoritative=false`,
`may_be_used_as_current_chain=false`, `promotion_authorized=false`,
`chain_mutation_performed=false`, and `segment_appended=false`. A blocked plan
or current-chain drift produces a blocked marker with no shadow rows.
