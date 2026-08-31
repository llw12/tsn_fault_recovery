# exp11: SMT Solver Scalability and Model Complexity Characterization

This is a solver-only experiment. It freezes production routing, timing, deadline,
TAS, hard-constraint, and Optimize semantics while measuring P0, every automatic
single-link PF case, and raw multi-fault J100/J040/J020 shared-synthesis cases.
It does not run member validation, recursive splitting, or a full OMNeT++ traffic
campaign.

The production mode uses the existing lexicographic Optimize objectives. The
benchmark-only feasibility mode uses the same hard-constraint builder with no
objectives and never writes or activates a recovery profile. Both modes use a
fixed 30,000 ms timeout and execute serially.

Run the formal workflow from WSL with:

```bash
bash experiments/exp11_smt_scalability/run.sh
```

The resumable checkpoint is stored under `scratch/exp11/<run_id>/`. Formal data,
tables, figures, provenance, and the technical summary are written to
`results/smt_scalability/`.
