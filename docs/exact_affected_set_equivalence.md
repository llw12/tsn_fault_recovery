# Exact Affected-Set Recovery Equivalence

## Scope and terminology

An `ExactAffectedSetCandidateGroup` contains candidate link faults whose healthy-state P0 affected-flow sets are identical. If

\[
A(e)=\{f\mid e\in R_f^0\},
\]

then faults enter one candidate group only when their canonical `affected_flow_set_sha256` values match. Grouping is deterministic and does not inspect recovery routes, Z3 results, objectives, semantic Profile hashes, or runtime outcomes.

A candidate group is not yet a Recovery Equivalence Class. A multi-fault class is validated only when one fixed, complete Profile passes every member fault independently. Singleton recoverable groups reuse their existing Per-Failure Profile and are recorded as `VALIDATED_SINGLETON`.

## Robust union-fault synthesis

For candidate group \(C=\{e_1,\ldots,e_k\}\), the synthesis topology is

\[
G_C=G-\bigcup_{e\in C}\{e\}.
\]

A fixed route that must remain valid when any individual member fails cannot use any member link. Computing it on the union-disabled topology therefore constructs a member-independent route. This does not model a simultaneous multi-link runtime failure: runtime validation disables exactly one member \(e_i\) at a time and activates the same Profile.

`JointProfileComputer.computeForDisabledLinks` is the single synthesis seam. It reuses `NetworkGraph`, `BfsRouteSolver`, `Z3ScheduleSolver`, `GateScheduleCompiler`, `ForwardingRealizabilityValidator`, `ProfileDefinition`, and `ProfileSerializer`. It verifies that the declared common affected set equals the healthy P0 routes intersecting the disabled-link union. Unaffected TT routes must remain byte-for-byte identical to P0; every shared route must avoid every class link.

The result is a complete target state, not a delta: it contains all TT logical routes, all required forwarding entries, and the GCL compiled from one all-active-TT Z3 schedule. Gate compilation retains the existing no-overlap, full-cycle, even-duration, and TT/BE complement checks. A forwarding-key conflict produces `SHARED_FORWARDING_CONFLICT`.

## Synthesis outcomes and fallback

Multi-fault synthesis records `SHARED_SAT`, `SHARED_NO_ROUTE`, `SHARED_UNSAT`, `SHARED_FORWARDING_CONFLICT`, `VALIDATION_FAILED`, or `ERROR`. Version 1 attempts the complete exact group only. It performs no subset search, greedy merge, or combinatorial partition optimization. A failed group conservatively becomes one Per-Failure singleton class per recoverable member, while the original candidate group and failure diagnostic remain in metadata. Non-SAT Per-Failure candidates remain in the candidate-group data but receive no fabricated recovery Profile.

## Class Store and stale-state protection

The deterministic Class Store contains candidate groups, final classes, `fault_to_class`, and complete Profile references. It binds to:

- `scenario_sha256`;
- `candidate_set_sha256` and candidate policy;
- the byte hash of the Per-Failure Store;
- solver/model configuration hash;
- Profile schema version and model version.

Any mismatch fails before simulation. Runtime preloads the compact runtime store during initialization. At fault time it performs only `fault -> class -> Profile` lookup plus `ProfileSwitcher` activation. Instrumentation requires route-solver, Z3-solver, and Profile-synthesis invocation counts all to remain zero. The simulated lookup delay is the same 0 μs lower bound used by Offline Per-Failure; measured map lookup wall times do not advance simulation time.

## Per-member validation

Every synthesized multi-fault Profile is run once for each member with only that physical link disabled. All rows for one class must carry the same `profile_sha256`. A row passes when:

1. Profile activation succeeds;
2. the shared logical routes avoid the current failed link;
3. forwarding validation and activation readback succeed;
4. simulation reaches a recovered TT reception;
5. runtime BFS, Z3, and synthesis invocation counts are zero;
6. after the fixed stable-window start, no persistent TT loss occurs;
7. delivered TT packets have no E2E deadline misses in that window.

The stable post-recovery window starts at `activation_time + cycle_time` for every mode and fault. Total transition-window TT loss is reported separately; it is not the sole validity criterion. A class may remain valid while showing worse transition loss or recovery latency than its Per-Failure baseline.

## Compression and quality metrics

Let \(M\) be the number of SAT Per-Failure recovery Profiles and \(K\) the final number of validated shared or singleton recovery Profiles. P0 is excluded:

\[
CR_{profile}=1-K/M.
\]

Storage compression includes only serialized recovery Profile files, excluding P0, store metadata, CSV, and figures:

\[
CR_{storage}=1-Bytes_{EQ}/Bytes_{PF}.
\]

Shared fault coverage is the number of recoverable faults represented by validated multi-fault classes divided by all recoverable faults. Per-fault comparisons also retain transition TT loss, delivered deadline misses, recovery duration, activation wall time, and their Exact-minus-Per-Failure deltas.

## Interpretation boundary

Semantic Profile hashes are post-synthesis diagnostics only. Equal Per-Failure hashes do not create a class, and unequal hashes do not prevent robust synthesis. Likewise, exact affected-flow equality proposes a candidate group but is not claimed to be a generally sufficient equivalence condition. Only shared synthesis plus every-member validation establishes an observed Recovery Equivalence Class in the tested scenarios.
