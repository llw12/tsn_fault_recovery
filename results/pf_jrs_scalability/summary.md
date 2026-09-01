# exp14 PF JRS-WA scalability diagnosis

Research Direction Assessment: **COMPUTE_PRESSURE_OBSERVED**.

All 13 planned scenarios completed a real SCIP P0 diagnostic, and all 13 returned `MEMORY_LIMIT`; therefore `P0_NOT_AVAILABLE` correctly prevented every PF solve. S1 (50 nodes, 100 TT) reached 804,200 variables and 1,653,600 constraints. Its three P0 repeats were status-stable (`MEMORY_LIMIT`, `MEMORY_LIMIT`, `MEMORY_LIMIT`). S2 was likewise stable across three repeats (`MEMORY_LIMIT`).

On the fixed 150-node topology, the 100-TT P0 reached 2,554,900 variables and 5,249,000 constraints before the 8,192 MB SCIP limit. Across all partial/full model audits, the largest recorded construction had 8,260,000 variables, 9,405,000 constraints, and 12,804,513,548 bytes of SCIP-reported working memory. These are direct observations, not complexity-based claims.

## PF computation and parallelism

No PF candidate campaign was legally reachable because no healthy P0 profile existed. Consequently serial PF work is 0 only as **not executed**, not as evidence that PF is cheap. The 1/2/4/8/16/32-worker LPT rows are all zero and must be read as not applicable; parallel workers cannot remove the observed P0 formulation bottleneck.

## Profile storage

No valid P0 or PF profile was produced, so measured profile-store bytes are 0 because generation was blocked. This experiment therefore shows no storage-pressure evidence, but it also cannot estimate a populated controller store. Solver working memory and profile storage are separate quantities.

BE=0 intentionally isolates offline JRS-WA synthesis. The campaign used one solver thread per case, invoked no OMNeT++/INET process, and produced no plot artifact. No grouping, compression method, heuristic backend, warm start, or new recovery algorithm was introduced.
