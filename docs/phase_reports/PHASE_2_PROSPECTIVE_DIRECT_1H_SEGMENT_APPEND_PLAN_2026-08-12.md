# Phase 2 — Prospective Direct-OKX 1H Segment Append Plan

This node freezes a deterministic, replayable dry-run transaction plan from a
validated append admission and the current chain. It derives the planned
segment count, candidate tail, and next canonical start without mutating the
chain or writing a replacement chain marker.

The current chain is revalidated at plan time. If its identity differs from
the identity bound by append admission, the result is
`blocked_current_chain_drift`. A blocked or non-admitted candidate produces
`blocked_append_admission_not_eligible` and no plan asset rows.

The plan is content-addressed and marker-last. It preserves existing segment
references, performs no network or economic work, credits no samples, and is
not a paper/live trading authorization. A future append writer must consume
this plan but is outside this node.
