# exp03 — Online joint recovery

Runs Baseline, Failure, and OnlineJointRecovery. The online case detects the failure, computes a live-topology BFS route and route-derived GCL, waits the configured control-plane `solverDelay`, then invokes the shared profile activator.

```bash
./experiments/exp03_online_joint_recovery/run.sh
```

Run twice to generate `results/online_joint_recovery/reproducibility.csv`. Host wall-clock times are reported but deliberately excluded from deterministic equality checks.
