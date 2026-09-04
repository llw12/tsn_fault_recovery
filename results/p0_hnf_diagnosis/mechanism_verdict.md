# exp18b P0 HNF mechanism-diagnosis verdict

## Scope and frozen evidence

This is an observational diagnosis of the six original P0 HNF results.  The
workload, topology, timing, payloads, seed, candidate-path count (`K=5`),
routing mode (`DIJKSTRA_OVERLAP`), backend limits, and upstream source were
not changed.  The phase-one source manifest, identity, instance-completion,
set-comparison, and scenario-diagnosis files retain their recorded SHA-256
values in `mechanism_diagnostic_limits.json`.

## Repeatability

Two serial replays were run for each of `M_RING`, `M_ROR`, and `L_ROR`, with
both H2S and CELF attempted in every replay.  All 12 attempt records match
their original scheduled-flow count, HNF-flow set hash, and instance-completion
hash; each pair of repeats also matches itself.  The repeatable result is
therefore `HEURISTIC_NOT_FOUND` / `PARTIAL_SCHEDULE`: 348/352 for Medium and
914/928 for Large.

## What the diagnostics support

* The original topology-insensitive HNF identity is reproduced: for each scale
  and backend the three topology HNF sets are exactly equal in the frozen
  phase-one evidence.  This is an association under this workload, not a claim
  that topology cannot matter in another workload.
* The HNF flows are zero-scheduled, so their scheduled-slot occupancy is zero.
  `link_contention.csv` reports only actual scheduled directional-egress busy
  time; its maximum is 1.6487% of the 8 ms hypercycle.  It cannot quantify the
  counterfactual contention an unscheduled HNF flow would create, and does not
  establish a congestion cause.
* HNF input-ID percentiles are generally below the median scheduled input-ID
  percentile, with a large-H2S high-rank outlier.  This is only an association:
  the audited `input_rank` is a canonical scenario numeric ID, not a proven H2S
  admission order, while CELF dynamically orders configurations.
* ControlCommand flows form most of the Large HNF set (11/14 H2S and 12/14
  CELF), but their per-kind HNF rate is only about 4--5%; small classes can have
  higher rates.  The business-kind table is descriptive and does not prove a
  traffic-class mechanism.

## Candidate-route limit and final conclusion

The pinned formal DijkstraOverlap K=5 raw export supplies the candidate-route
count for each HNF flow (observed counts 1, 2, 3, and 5).  It does **not**
export the actual candidate node/link paths.  Consequently every HNF route row
is explicitly marked `CANDIDATE_ROUTE_INTROSPECTION_UNAVAILABLE`; no approximate
k-shortest algorithm or upstream-core instrumentation was substituted.  We
therefore cannot make a route-overlap, forced-edge, edge-disjointness, or
route-choice causal claim.

**Verdict:** the HNF outcome is reproducible and associated with a stable
workload/implementation-level pattern, but the present experiment does not
identify a unique mechanism.  The evidence does not establish route structure,
link contention, nominal input rank, or traffic class as the causal reason for
HNF.  A mechanism claim would require a separately authorized experiment that
exports formal candidate paths or introduces a controlled factor; neither was
performed here.
