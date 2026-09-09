# Current patched-source audit

- Upstream: `650a9665e7bafb70fcf19c9f0a247e1d7b885ffd`; current source tree SHA-256: `c8d3830d74c8b8626d350af86de6426ef8647823031e8ffd567ed848b2f06884`.
- H2S class: `HierarchicalHeuristicScheduling`. `priority_queue pop -> runtime rating prepare/rate -> ascending rating then config id -> first successful placeConfig; no backtracking`
- Formal sorter/rating/placement: LOW_PERIOD_FLOWS_FIRST (CLI -f 4); PATH_LENGTH (CLI -c 1); ASAP (qualified adapter CLI -p 0; BALANCED is audited but not substituted).
- Candidate routing: DijkstraOverlap, K=5.
- Fixed release/deadline are consumed by `placeConfigASAP`; the search consumes per-flow propagation and processing delays.
- Mixed-period chain is `compute_frames_per_hc` → placement frame loop → `searchTransmissionOpportunities` → `reserveSlot`.
- BALANCED (not the qualified exp18h placement) uses release `sub_cycle_index * utilization.getSubCycle() + frame_index * flow.period` and deadline `(frame_index + 1) * flow.period`. It does not consume fixed release/deadline fields.
- No backtracking/retry/repair is present. A failed flow remains absent, and the priority-queue loop continues with later flows.
- No global flow/config/attempt hard limit or explicit fixed cardinality ceiling was found.

## Semantic difference from pinned source

The existing exp15 patch makes ASAP use `release_offset`, relative `deadline`, `fixed_release`, and per-flow delay fields. The BALANCED implementation retains the stock period-bound formulas above; this is a recorded semantic anomaly, not repaired or exercised by the qualified ASAP cohort.
