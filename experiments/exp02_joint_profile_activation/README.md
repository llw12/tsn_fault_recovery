# exp02 — Joint profile activation

Runs Baseline, Failure, routing-only ManualRecovery, and JointProfileRecovery. The last case activates route profile T1 and backup-path GCL profile P1 in the same 6 ms event.

```bash
./experiments/exp02_joint_profile_activation/run.sh
```

Run twice to generate `results/joint_profile_activation/reproducibility.csv`.
