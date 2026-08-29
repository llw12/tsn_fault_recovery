# Offline Per-Failure Joint Profile Recovery

The per-failure strategy is a strong, deliberately uncompressed offline baseline.
Every candidate fault declared in the scenario receives one status entry. Every
relevant, schedulable fault receives its own complete recovery Profile, even when
another fault has the same semantic Profile hash.

## Computation boundary

`JointProfileComputer` is the single route/schedule/Profile computation seam used
by healthy P0 generation, Online recovery, and per-failure precomputation. For a
fault, it reroutes only TT flows whose P0 logical route contains the failed link,
preserves every unaffected logical route, jointly schedules all active TT flows,
compiles the complete GCL, and rejects forwarding decisions that a
destination-MAC FDB cannot represent.

`tools/precompute_profiles.py` invokes a zero-simulation-time Cmdenv configuration
that iterates the scenario's declared candidates in one process. C++ performs BFS,
Z3 optimization, and GCL compilation; Python only canonicalizes metadata, computes
SHA-256 values, validates artifacts, and constructs the store.

## Store and runtime boundary

The audit store is `generated/<scenario>/profiles/per_failure/store.json`.
Independent Profile blobs are under its `profiles/` directory. The store records
`SAT`, `NO_AFFECTED_TT`, `NO_ROUTE`, `UNSAT`, `FORWARDING_CONFLICT`, or `ERROR`
for every candidate.

Before starting OMNeT++, the runner validates the scenario hash, solver/model
configuration hash, port-map hash, schema versions, per-file hash, Profile content
hash, and semantic hash. `runtime_store.json` embeds all SAT ProfileDefinitions;
its content is validated against the audit store and then parsed once during module
initialization by `OfflinePerFailureProfileProvider`.

At the fault event, Offline Per-Failure performs only an in-memory map lookup and,
for SAT, schedules activation after `offlineLookupDelay`. Missing, stale,
unrecoverable, or no-action entries never fall back to Online computation.
Runtime route-solver and Z3 invocation counters therefore remain zero.

## Hash semantics

`profile_sha256` covers the canonical Profile document excluding the self-hash
field. `semantic_profile_hash` covers logical routes, concrete forwarding entries,
and gate schedules while excluding IDs, fault metadata, and wall-clock timings.
The semantic hash is diagnostic only; it never causes deduplication in this
strategy.
