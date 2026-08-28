# exp04 — BFS rerouting with SMT-based TAS rescheduling

This experiment validates the reusable Z3 scheduling backend independently and through the online recovery controller. Routing is computed by BFS from the live topology; Z3 schedules the resulting fixed route and does not perform joint route-and-schedule optimization.

The run includes ten solver/compiler self-tests, single-flow compatibility, a feasible three-flow contention case, an intentionally infeasible deadline case, and online recovery at `solverDelay` values of 0.1, 1, 5, and 10 ms.

```bash
./experiments/exp04_smt_schedule/run.sh
```

Run it twice to generate `results/smt_schedule/reproducibility.csv` and `wall_clock_runs.csv`. Deterministic simulation/model outputs must match; host route/Z3 wall-clock measurements are reported separately and excluded from equality checks.
