# exp15 H2S backend qualification

Research Direction Assessment: **H2S_SCALABLE_AND_VALID**.

## Qualification

The QH00-QH10 gate is `True`. Release offsets, independent deadline budgets, wait-allowed forwarding, on-wire frame serialization, conservative time quantization, stream-aware same-destination routing, deterministic outcome, and upstream/project verifier parity are recorded in `qualification_verdict.json`. The formal quantum is 100 ns, routing is DIJKSTRA_OVERLAP, K=5, and the fixed seed is 1024.

## P0 scalability

| Scenario | TT | Status | Scheduled | Total backend ms | Peak RSS bytes | Semantic valid |
|---|---:|---|---:|---:|---:|---|
| S1 | 100 | SUCCESS_H2S | 100/100 | 24.906 | 12124160 | True |
| S2 | 200 | SUCCESS_H2S | 200/200 | 57.701 | 12288000 | True |
| S3 | 500 | SUCCESS_H2S | 500/500 | 235.186 | 19578880 | True |
| S4 | 500 | SUCCESS_H2S | 500/500 | 502.781 | 31797248 | True |
| S5 | 1000 | SUCCESS_H2S | 1000/1000 | 1232.303 | 55660544 | True |
| S6 | 1000 | SUCCESS_H2S | 1000/1000 | 3480.362 | 115326976 | True |
| S7 | 2000 | SUCCESS_H2S | 2000/2000 | 11358.807 | 231907328 | True |
| S8 | 2000 | HEURISTIC_NOT_FOUND | 1390/2000 | 21869.478 | 325947392 | False |
| F150_TT100 | 100 | SUCCESS_H2S | 100/100 | 53.912 | 12288000 | True |
| F150_TT250 | 250 | SUCCESS_H2S | 250/250 | 111.170 | 13090816 | True |
| F150_TT500 | 500 | SUCCESS_H2S | 500/500 | 233.491 | 19574784 | True |
| F150_TT750 | 750 | SUCCESS_H2S | 750/750 | 360.117 | 25305088 | True |
| F150_TT1000 | 1000 | SUCCESS_H2S | 1000/1000 | 496.672 | 29294592 | True |

S1 is the key identical-workload comparison: exp14 JRS-WA reached 804,200 variables and 1,653,600 constraints and returned `MEMORY_LIMIT`. H2S scheduled 100/100 TT flows in 24.906 ms with 12124160 bytes measured peak RSS; semantic validation was `True`. This is a backend runtime comparison under identical scenario semantics, not an exact-solver speedup claim.

H2S primary successes: 12/13. CELF successful fallback contributions: 0. Compute frontier: `NONE`. Quality frontier: `S8`. Semantic frontier: `NONE`.

## Interpretation

JRS-WA is an exact feasibility formulation and may report `INFEASIBLE` only after a solver proof. H2S and CELF are constructive heuristics; `HEURISTIC_NOT_FOUND` is not an infeasibility proof. The tested heuristic exhibits better empirical scalability where it returns valid schedules, without establishing an algorithmic complexity result or superiority over an exact formulation.

The recommended next step for `H2S_SCALABLE_AND_VALID` is a separate rerun of PF precomputation scalability with H2S primary and CELF fallback. No PF campaign is started by exp15.
