# exp09 — Approximate Fault Equivalence Frontier

This experiment evaluates deterministic, pre-fault-only approximate fault grouping on the frozen `diamond_auto`, `mesh10_auto`, and `structured20_auto` scenarios.

Candidate groups are proposed by complete-link affected-flow Jaccard policies, optionally constrained by healthy-topology fault-edge distance. A candidate becomes a recovery-equivalence class only after union-disabled robust Profile synthesis and every-member single-fault OMNeT++ validation pass. Failed candidates split recursively along their fixed merge tree; singleton leaves reuse Per-Failure Profiles.

Run from the repository root:

```bash
bash experiments/exp09_approximate_equivalence/run.sh
```

The command runs exp01–exp08 regression, all 14 policies per scenario, synthesis and validation caches, quality comparisons, observed Pareto analysis, six figures, and a deterministic repeatability check. Formal results are written to `results/approximate_equivalence/`.

The policy thresholds are experimental grid points, not learned or universally optimal constants. Recovery-derived fields are used only in explicitly labeled post-hoc diagnostics and never influence grouping or recursive split decisions.
