# Experiment 08: Exact Affected-Set Equivalence

Run from the repository root:

```bash
bash experiments/exp08_exact_equivalence/run.sh
```

The experiment first regresses exp01 through exp07, then discovers auto candidates for Diamond, mesh10, and structured20, builds Per-Failure baselines, synthesizes union-disabled shared Profiles for exact affected-set groups, validates every class member as an independent single-link simulation, performs matched runtime sweeps, generates the compression/quality report, and repeats deterministic structural synthesis checks.

Formal output is written to `results/exact_equivalence/` only from a clean implementation commit.
