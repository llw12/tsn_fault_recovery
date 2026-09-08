# exp18g: Deterministic role-stratified TT workload density calibration

## Research answers

1. The full-density workload remained P0-incomplete after the preregistered backend checks, so this stage calibrates a transparent PF benchmark workload.

2. H2S tuning stops because K, deadlines, tie-breaks, and all official built-in sorters had already been tested without cardinality improvement.

3. Only complete logical TT flows are removed; packet instances are never individually thinned.

4. Periods are unchanged, preserving each retained flow's packet-instance multiplicity and PF synthesis semantics.

5. Original exp18 deadlines are used because exp18e deadline relaxation did not improve P0 construction.

6. The exact rational ladder is: D100=1/1, D095=19/20, D090=9/10, D085=17/20, D080=4/5, D070=7/10, D060=3/5, D050=1/2.

7. Selection is outcome-independent and uses only frozen source-flow metadata, a density rational, and the fixed namespace.

8. No HNF identity or any solver result was read to decide which flows to drop.

9. Each scale × role ranks SHA256(namespace|scale|flow_kind|flow_id), then retains the floor(p*N_c/q) prefix.

10. Role stratification preserves approximate proportional representation of every canonical traffic role rather than selecting globally.

11. Cross-topology retained-flow identity: PASS.

12. Per-role nestedness: PASS.

13. Medium retained logical-flow counts: D100=352, D095=331, D090=314, D085=295, D080=278, D070=243, D060=207, D050=176.

14. Large retained logical-flow counts: D100=928, D095=879, D090=832, D085=785, D080=738, D070=647, D060=553, D050=464.

15. Exact recomputed packet instances: D100=M3616/L9632, D095=M3422/L9138, D090=M3236/L8650, D085=M3048/L8162, D080=M2862/L7674, D070=M2518/L6730, D060=M2144/L5754, D050=M1808/L4816.

16. On-wire bytes per hyperperiod (Medium/Large): D100=M522240/L1386496, D095=M492928/L1314432, D090=M466688/L1244288, D085=M439296/L1174144, D080=M413056/L1104000, D070=M362368/L967808, D060=M308736/L827520, D050=M261120/L693248; they are calculated from retained instances and unchanged frame-overhead accounting.

For compact comparison, the retained logical-flow and exact instance census is:

| density | M flows / instances | L flows / instances |
|---|---:|---:|
| D100 | 352 / 3616 | 928 / 9632 |
| D095 | 331 / 3422 | 879 / 9138 |
| D090 | 314 / 3236 | 832 / 8650 |
| D085 | 295 / 3048 | 785 / 8162 |
| D080 | 278 / 2862 | 738 / 7674 |
| D070 | 243 / 2518 | 647 / 6730 |
| D060 | 207 / 2144 | 553 / 5754 |
| D050 | 176 / 1808 | 464 / 4816 |

17. D100 baseline parity: PASS.

18. The per-density six-scenario P0 scheduled counts are:

| density | M_RING | M_REDSTAR | M_ROR | L_RING | L_REDSTAR | L_ROR |
|---|---:|---:|---:|---:|---:|---:|
| D100 | 348/352 | 348/352 | 348/352 | 914/928 | 914/928 | 914/928 |
| D095 | 328/331 | 328/331 | 328/331 | 866/879 | 866/879 | 866/879 |
| D090 | 312/314 | 312/314 | 312/314 | 819/832 | 819/832 | 819/832 |
| D085 | 293/295 | 293/295 | 293/295 | 773/785 | 773/785 | 773/785 |
| D080 | 277/278 | 277/278 | 277/278 | 729/738 | 729/738 | 729/738 |
| D070 | 243/243 | 243/243 | 243/243 | 639/647 | 639/647 | 639/647 |
| D060 | 207/207 | 207/207 | 207/207 | 547/553 | 547/553 | 547/553 |
| D050 | 176/176 | 176/176 | 176/176 | 461/464 | 461/464 | 461/464 |

19. H2S-alone complete counts: D100=M0/3,L0/3, D095=M0/3,L0/3, D090=M0/3,L0/3, D085=M0/3,L0/3, D080=M0/3,L0/3, D070=M3/3,L0/3, D060=M3/3,L0/3, D050=M3/3,L0/3. CELF fallback is never relabeled as H2S success.

20. CELF fallback attempts (M/L): D100=M3/3,L3/3, D095=M3/3,L3/3, D090=M3/3,L3/3, D085=M3/3,L3/3, D080=M3/3,L3/3, D070=M0/3,L3/3, D060=M0/3,L3/3, D050=M0/3,L3/3; scenarios completed only through CELF fallback: none.

21. Formal-backend complete counts by density (M/3 + L/3): D100=0/3+0/3, D095=0/3+0/3, D090=0/3+0/3, D085=0/3+0/3, D080=0/3+0/3, D070=3/3+0/3, D060=3/3+0/3, D050=3/3+0/3; selected point: none.

22. Highest qualified preregistered density rho*: none.

23. The selected Medium actual global density is not applicable.

24. Per-role selected retained counts: not applicable.

25. Heuristic non-monotonicity was not observed on the preregistered qualification predicate; selection always uses the highest qualified ladder point.

26. A selected workload changes only the TT logical-flow population; retained timing, payload, role/endpoints, topology, and backend are unchanged.

27. It is not a real factory trace.

28. It is a deterministically density-calibrated, literature-grounded, role-based synthetic industrial workload.

29. This experiment does not prove the original full-density workload mathematically infeasible; it only reports the fixed qualified heuristic backend's construction outcome.

30. No PF benchmark workload was selected because no preregistered density achieved 6/6 formal-backend complete P0.

31. Next stage: `REVIEW_WORKLOAD_CONSTRUCTION_OR_BACKEND_MODEL` using the exact selected scenario bytes if qualified; this experiment does not start it.

No PF, fault enumeration, Profile Store, parallel PF, OMNeT++, or INET run is performed.
