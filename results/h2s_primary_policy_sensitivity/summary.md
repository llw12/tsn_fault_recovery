# exp18f: Built-in H2S primary policy sensitivity

## Research answers

1. This experiment tests only whether an already built-in upstream H2S sorter can construct a complete healthy P0 on the frozen realistic workload; it is not a new scheduling-algorithm design study.

2. It returns to the byte-identical original exp18 scenarios and original deadlines so any success would apply directly to the original realistic workload.

3. Deadline relaxation stops here because exp18e found no P0 improvement even through D=T.

4. The preregistered built-in sorters are LOW_PERIOD_FLOWS_FIRST, LOWEST_TRAFFIC_FLOWS_FIRST, LOWEST_ID_FIRST, and SOURCE_NODE_SORTING.

5. LOW_PERIOD is period-ascending, then larger-frame-first, then deterministic ID; LOWEST_TRAFFIC is ascending frame-size/period; LOWEST_ID is numeric-ID ascending; SOURCE_NODE prioritizes source fan-out, then destination, traffic, and ID. The full source audit and read fields are in `built_in_policy_audit.md`.

6. The sole formal intervention is the upstream H2S `--flow-sorting` policy value.

7. Topology, workload roles and population, periods, original deadlines and releases, payloads, route scope, DIJKSTRA_OVERLAP, K=5, 100 ns quantum, seed 1024, one thread, 30 s per heuristic, 8192 MB, CELF behavior, and all other H2S knobs are fixed.

8. LOW_PERIOD historical baseline parity is **PASS**: counts, HNF identities, instance completion, and H2S candidate vectors match the frozen reference.

9. The policies did produce distinct actual H2S orders; qualification passed before the formal matrix.

10. Candidate vectors remained invariant across policies for each scenario.

11. The per-policy, per-scenario scheduled-flow results are:

| policy | M_RING | M_REDSTAR | M_ROR | L_RING | L_REDSTAR | L_ROR |
|---|---:|---:|---:|---:|---:|---:|
| LOW_PERIOD | 348/352 | 348/352 | 348/352 | 914/928 | 914/928 | 914/928 |
| LOWEST_TRAFFIC | 348/352 | 348/352 | 348/352 | 914/928 | 914/928 | 914/928 |
| LOWEST_ID | 348/352 | 348/352 | 348/352 | 914/928 | 914/928 | 914/928 |
| SOURCE_NODE | 348/352 | 348/352 | 348/352 | 914/928 | 914/928 | 914/928 |

12. No H2S policy reaches complete P0 in this matrix; H2S complete-scenario counts are {'LOW_PERIOD': 0, 'LOWEST_TRAFFIC': 0, 'LOWEST_ID': 0, 'SOURCE_NODE': 0}.

13. There is no policy with 6/6 H2S-complete scenarios.

14. PF-eligible H2S policies: none.

15. Policies with only 6/6 CELF-fallback completeness: none; none is counted as an H2S success.

16. Different policies do change HNF identity in at least one same-scenario pair.

17. The matrix does not improve scheduled-flow cardinality over LOW_PERIOD.

18. Cross-topology HNF-set invariance remains for every tested scale-policy comparison.

19. Repeatability passes for order SHA, candidate vector, scheduled count, HNF set, and instance completion.

20. These heuristic results cannot prove that the original workload is infeasible.

21. No policy constructed 6/6 complete P0 here; if one had done so with the upstream verifier and independent static checker passing, it would demonstrate that the original frozen workload has a complete P0.

22. The result cannot establish LOW_PERIOD as the unique root cause of historical HNF.

23. Recommended next stage: `TT_WORKLOAD_DENSITY_CALIBRATION`; do not automatically start it.

No PF, fault enumeration, Profile Store study, parallel campaign, OMNeT++, or INET simulation is run by exp18f. The experiment stops after this policy matrix and awaits manual direction.
