# exp06: Offline Per-Failure Joint Profile Recovery

Run from the repository root:

```bash
bash experiments/exp06_offline_per_failure/run.sh
```

The script requires a clean tracked worktree. It runs exp01–exp05 regression,
precomputes every YAML-declared candidate fault, sweeps No Recovery, Online, and
Offline Per-Failure, checks runtime solver counters and semantic equality, adds a
100 µs lookup-delay sensitivity reference, then writes formal artifacts under
`results/offline_per_failure/`.
