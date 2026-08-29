# exp07 Critical-Link Analysis

Run from any shell:

```bash
bash experiments/exp07_critical_link_analysis/run.sh
```

The formal workflow requires a clean tracked tree at the implementation commit. It runs exp01–exp06 regression, discovers candidates for `diamond_auto` and `mesh10_auto`, performs per-failure precompute, constructs similarity datasets, renders figures, and verifies deterministic discovery artifacts.
