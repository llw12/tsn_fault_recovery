# exp18h read-only H2S admission and placement audit

M_D070 is the first preregistered Medium 3/3 complete point; L_D050 is the lowest preregistered Large point still at 461/464. Both are direct byte reuse from exp18g.

## Decision and parity gates

No solver decision was changed: all formal commands use H2S only, LOW_PERIOD (`-f 4`), PATH_LENGTH (`-c 1`), ASAP (`-p 0`), DIJKSTRA_OVERLAP K=5, baseline tie-break, 100 ns, seed 1024, one thread, 30 s, and no CELF fallback.
TRACE_OFF/TRACE_ON parity passed for all qualification comparisons: `True`. It compares count, HNF set, instance completion, order, slots, candidate vector, verifier, and checker state.

## Source semantics

The qualified ASAP path does consume fixed release and relative deadline. BALANCED is not selected by the historical qualified adapter; its source still ignores those fields, so the source-only anomaly is recorded without a repair.
H2S is constructive and non-backtracking; it continues after a failed flow. There is no observed hard global attempt/cardinality limit.

## Trace observations

All Medium controls completed at 243/243: `True`.
The earliest observed HNF is scenario `L_RING_D050`, position `39`, flow `CMD_PLC_C06_M02_A2_C06_M02`; later successful admissions in that trace: `422`.
For every reported Large HNF, the report distinguishes configuration count from original placement attempts, records actual ASAP frame windows, failed hop/egress snapshot, and observed overlapping reservations. These are observational overlaps, not causal counterfactual claims.

## Limits and recommendation

This does not prove the Large workload infeasible, and it does not prove H2S is the sole problem. It cannot be described as merely the final three flows not fitting unless a controlled intervention establishes that claim.
Verdict: `BACKEND_SEMANTIC_ANOMALY_OBSERVED`. Recommended next authorised stage: `FIX_BACKEND_SEMANTIC_ANOMALY` (review/repair the non-qualified BALANCED semantics separately; do not make that repair here).
