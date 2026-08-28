# exp01 recovery metrics

From WSL/Linux, run:

```bash
./experiments/exp01_recovery_metrics/run.sh
```

The command builds the existing project in release mode, runs Baseline,
LinkFailure, and ManualRecovery with Cmdenv, and analyzes native INET result
vectors. Timestamped raw `.sca/.vec` files are kept under `raw/`; stable CSV,
Markdown, and PNG outputs are written to `results/recovery_metrics/`.

The loss denominator uses a 1 ms drain window. The TT packet generated exactly
at the 20 ms simulation limit is retained in the per-packet CSV with
`eligible_for_loss=false`, so it is not misclassified as loss.
