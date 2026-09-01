# exp14 Per-Failure JRS-WA Scalability

This is a pure offline-synthesis diagnosis.  It measures healthy P0 and
singleton per-failure profiles using the qualified SCIP JRS-WA backend.  The
scenario family uses deterministic degree-four switch topologies, 1 Gbps
links, a common 1 ms TT period, and the structured-family workload scale.

BE traffic is intentionally zero because BE does not participate in JRS-WA
offline synthesis.  This isolates network/TT-flow scaling and is not an
omission.  The experiment does not build or run OMNeT++, does not perform
member validation, and emits no plots.

Each solve runs in a separate serial subprocess.  Solver memory and subprocess
maximum RSS are recorded separately from canonical profile-store bytes.
Reported 2/4/8/16/32-worker values are deterministic idealized LPT projections,
not measured parallel benchmarks.

