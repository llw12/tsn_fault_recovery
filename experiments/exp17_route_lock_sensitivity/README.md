# exp17 route-lock sensitivity

This paired diagnosis reuses only the 106 `HEURISTIC_NOT_FOUND` fault scenarios from exp16.  The physical fault, H2S/CELF fallback, 100 ns quantum, K=5 `DIJKSTRA_OVERLAP`, seed 1024, one thread, 30 s per algorithm, and 8192 MB limit are unchanged.  `affected-only` is reused strictly as the exp16 baseline; `all-reroute` removes all fixed paths while retaining the failed physical-link deletion and scheduling all TT flows jointly.

Run `./experiments/exp17_route_lock_sensitivity/run.sh --quick` for semantic qualification, three baseline-parity guards, and three all-reroute probes. Run without arguments for the formal HNF cohort.
