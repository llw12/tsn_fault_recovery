# Critical-Link Discovery and Fault Similarity Method

## Protection scope

The first protection scope contains physical links whose two endpoints have `switch` node type in the canonical ScenarioModel. Node identifiers are never interpreted as types. End-system-to-switch access links remain visible in analysis output but are outside this scope because the current end systems are single-homed. “Not a candidate fault” does not mean “unimportant”: an unused internal link may be an essential backup path.

## Candidate fault definition

For healthy graph G=(V,E), healthy P0 primary route R_f^0, and TT flow set F, define:

`A(e) = { f in F | e in R_f^0 }`.

The auto policy selects exactly the links for which both conditions hold:

1. e is a switch-switch link in the protection scope.
2. A(e) is non-empty.

The implementation constructs `link_id -> affected_flow_set` by traversing each P0 `link_path` once. Its complexity is `O(sum_f |R_f^0|)`, and all serialized link and flow orders are canonical.

## Why recoverability is not a selection rule

Candidate discovery uses only healthy topology, healthy P0 routes, and flow definitions. It runs before any failed-topology routing, Z3 schedule solving, gate compilation, forwarding validation, or semantic Profile hashing. `SAT`, `NO_ROUTE`, `UNSAT`, `FORWARDING_CONFLICT`, and `ERROR` are post-recovery observations, never candidate filters. This prevents selection bias toward faults the current recovery algorithm can solve.

An auto candidate cannot legitimately produce `NO_AFFECTED_TT`, because its defining A(e) is non-empty. The precompute workflow treats that outcome as a regression failure. Explicit mode continues to permit unused links for debugging and legacy reproduction.

## Raw criticality features

Pre-fault features include affected flow count and IDs, nominal affected payload rate, deadlines, release offsets, packet sizes, primary-route usage, endpoint types, and scope/classification. Nominal flow load is `(packet_size_bytes * 8) / period`; affected link load is the sum over A(e). No composite criticality score or learned weight is introduced.

Post-recovery fields are named separately in the fault dataset and include status, rerouted flow count, recovered route union and hop count, Z3 objective, semantic Profile hash, Profile bytes, and solver/precompute timings. Keeping these phases explicit permits future experiments to control recovery-result leakage.

## Similarity definitions

Affected-flow Jaccard for candidate faults i and j is:

`J(i,j) = |A(i) intersection A(j)| / |A(i) union A(j)|`.

Auto candidates have non-empty affected sets. The matrix is symmetric, has range [0,1], and has diagonal 1.

Fault edge distance for links `(a,b)` and `(c,d)` is the minimum healthy switch-graph node distance among `d(a,c)`, `d(a,d)`, `d(b,c)`, and `d(b,d)`. Shared endpoints therefore have distance 0. Primary-path position difference is the mean absolute difference between the two failed links’ zero-based positions for flows affected by both. For SAT pairs, recovery-route Jaccard compares the unions of all TT recovery-route links.

## Jaccard is not recovery equivalence

Jaccard measures only overlap of affected healthy routes. It is not sufficient evidence that two faults can share a recovery Profile. The existing semantic Profile hash is used here as a per-failure ground-truth diagnostic: equality means independently synthesized Profiles have identical logical routes, forwarding entries, and gate schedules after canonicalization. Even observed agreement at Jaccard 1 proves nothing beyond this dataset.

A future candidate group becomes a genuine Recovery Equivalence Class only when one shared Profile is synthesized and validated under every member fault for forwarding feasibility, connectivity, schedule feasibility, end-to-end deadline compliance, and deterministic recovery. This stage does not perform clustering, Profile merging, deduplication, or shared-Profile validation.

## Reproducibility artifacts

`candidate_faults.json` records the source policy, every physical link, explicit exclusions, selected candidates, affected flow sets, and `candidate_set_sha256`. ProfileStore repeats the policy and hash and rejects mismatches. Runtime manifests record candidate mode, scope, criterion, set hash/count, membership of the exercised fault, and the affected-flow-set hash.
