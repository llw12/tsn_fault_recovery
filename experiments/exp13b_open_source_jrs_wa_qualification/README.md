# exp13b Open-source JRS-WA Qualification

This experiment reimplements the exp13 JRS-WA mathematical semantics with
PySCIPOpt and SCIP.  It reuses the canonical exp13 input conversion but does
not copy or import TSNKit's GPL JRS-WA formulation.  Gurobi is used only as a
small-case Q00--Q02 reference oracle.

The SCIP model is a zero-objective feasibility MIP with binary route and link
ordering variables, integer nanosecond transmission-start variables, named
constraint-family audits, one solver thread, a 30-second limit, and fixed
randomization seeds.  Every feasible result must pass the independent static
checker.  No OMNeT++ process is invoked.

Run a development qualification with:

```sh
.venv-jrs/bin/python -m tools.run_open_source_jrs_qualification --quick \
  --results /tmp/exp13b-quick --run-id exp13b-quick
```

