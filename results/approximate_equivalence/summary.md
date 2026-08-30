# exp09 — Approximate Fault Equivalence Frontier

## Executive summary

Jaccard and topology rules proposed candidate fault groups; no group was treated as equivalent until union-disabled robust Profile synthesis and every-member single-fault OMNeT++ validation passed. The final stores therefore preserve deterministic, zero-runtime-computation recovery while exposing the gap between proposed and validated compression.

Thresholds are experimental policy points on the evaluated grid, not learned universal constants.

## Results

### Diamond

Lowering the J-only threshold from 1.0 to 0.2 changed candidate compression from 50.0% to 50.0%, and realized compression from 50.0% to 50.0%. The J020 proposal-to-validation gap was 0.0%.

The largest observed realized compression was 50.0% at J100; this is an observed frontier result, not a generally optimal threshold. Across all 14 policies, aggregate TT-loss delta was 0 and aggregate delivered-TT deadline-miss delta was 0.

### mesh10

Lowering the J-only threshold from 1.0 to 0.2 changed candidate compression from 16.7% to 66.7%, and realized compression from 16.7% to 66.7%. The J020 proposal-to-validation gap was 0.0%.

The largest observed realized compression was 66.7% at J020; this is an observed frontier result, not a generally optimal threshold. Across all 14 policies, aggregate TT-loss delta was 0 and aggregate delivered-TT deadline-miss delta was 0.

### structured20

Lowering the J-only threshold from 1.0 to 0.2 changed candidate compression from 22.7% to 68.2%, and realized compression from 22.7% to 63.6%. The J020 proposal-to-validation gap was 4.5%.

The largest observed realized compression was 63.6% at J020; this is an observed frontier result, not a generally optimal threshold. Across all 14 policies, aggregate TT-loss delta was 0 and aggregate delivered-TT deadline-miss delta was 0.

## Feasibility, edge constraints, and cost

The grid made 153 logical shared-synthesis attempts and rejected 1; rejection causes are separated in `rejection_reason_summary.csv`. JE policies show whether restricting fault-edge distance improves acceptance at the cost of candidate compression, without changing the acceptance gate.

Summed per-policy cold estimates were 26327.507 ms for synthesis and 1129895.759 ms for validation. These estimates charge cached work at its original measured cost; `cache_summary.csv` separately records campaign reuse.

The only invalid proposed group occurred at structured20/J020: union-disabled routing returned `NO_ROUTE`, the group split recursively once, and realized compression fell from the 68.2% candidate bound to 63.6%. No J-only threshold at or above 0.4 produced a rejection, and Diamond and mesh10 had no rejection anywhere on the grid.

Within the evaluated JE grid, edge-distance constraints did not improve acceptance because the corresponding unconstrained J080/J060/J040 policies already had 100% acceptance. They did reduce compression where they excluded feasible groups. For example, structured20 at theta 0.4 realized 59.1% without the constraint versus 40.9%, 45.5%, and 54.5% for dmax 0, 1, and 2. Thus edge constraints were conservative filters here, not a source of additional validated compression.

Approximate grouping caused no measured quality degradation: every policy had aggregate TT-loss delta 0, deadline-miss delta 0, and mean/p95/max recovery-latency delta 0 µs against Per-Failure. The extra compression required modest additional cold cost on top of the fixed validation sweep: mesh10 moved from 16.7% at 16.553 s (J100) to 66.7% at 16.764 s (J020), while structured20 moved from 22.7% at 64.472 s to 63.6% at 64.751 s. Diamond compression and cost were unchanged across thresholds.

## Method and limitations

Grouping used only healthy P0 affected-flow sets, Jaccard, edge distance, affected counts/load, and topology metadata. Recovery status, recovery routes, semantic Profile hashes, Z3 objectives, latency, serialized bytes, and packet outcomes were excluded from grouping. Recovery-route similarity and PF semantic-hash counts appear only as labeled post-hoc diagnostics.

The observed Pareto frontier maximizes realized compression while minimizing cold synthesis cost and maximum recovery-latency delta, subject to zero deadline-miss delta and stable validation. It characterizes only these three frozen scenarios and 14 policy points.

## Reproducibility

Run ID: `20260830T084408Z`. Implementation commit: `819b19915c390145419b9ea91db39e4ec8e11462`. Solver timeout: 30000 ms. Runtime BFS, Z3, Profile synthesis, and grouping counters were asserted to be zero for every validation row.
