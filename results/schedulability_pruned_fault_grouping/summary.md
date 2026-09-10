# exp21 schedulability-pruned fault grouping

1. Formal verdict: SCHEDULABILITY_PRUNED_GROUPING_ESTABLISHED.
2. Algorithm version: SPFG-v1.
3. Formal search is scenario-local.
4. Only identical affected-flow sets are pooled.
5. The candidate catalog has exact exp19 parity.
6. P0 routes are reconstructed from exp18j raw output.
7. F1 checks all TT endpoint connectivity.
8. F2 checks minimum-hop serialization delay.
9. F3 checks only the declared finite cut family.
10. F3 is a necessary condition, not a sufficiency claim.
11. F4 uses exact critical-window range-add/range-max sweeps.
12. Circular jobs retain deadlines beyond one hyperperiod.
13. Failure certificates prune only fault supersets.
14. Backend failures do not become infeasibility certificates.
15. H2S is primary and CELF is fallback.
16. Routing is DIJKSTRA_OVERLAP with K=5.
17. Flow sorting is LOW_PERIOD (4).
18. Placement is ASAP and the quantum is 100 ns.
19. Tie breaking is BASELINE.
20. Seed is 1024 and execution is single-threaded.
21. Each backend algorithm has a 30 s limit.
22. Memory limit is 8192 MiB.
23. Diagnostic tracing is disabled.
24. Accepted multi-fault profiles pass the upstream verifier.
25. Accepted multi-fault profiles pass the project static checker.
26. Every accepted group is replayed against each singleton topology.
27. Queue identifiers alone are rebound during singleton replay.
28. Final singleton groups are synthesized anew with the exp19 contract.
29. Final singleton results are compared with exp19 status and semantic hash.
30. The active partition starts with singleton groups.
31. Only pairwise active-group unions are proposed.
32. The first successful ranked proposal is accepted.
33. Candidates overlapping an accepted merge become stale.
34. Search terminates at a deterministic local optimum.
35. Final profile count: 986.
36. Profile reduction from 1099: 113.
37. Mapped fault count: 1099.
38. Accepted merge count: 113.
39. F1--F4 evaluated proposal count: 113.
40. Actual group synthesis attempts: 113.
41. Singleton replay rows: 226.
42. Final singleton reruns: 873.
43. Checked directed-cut rows: 109954.
44. Time-window audit rows: 109954.
45. Failure certificate count: 0.
46. Frozen final-group SHA-256: e235af4fccc158ad95a241bd25a26043350776d648eae681847fa5c9881fa228.
47. Exp20 is read only after the final group set is frozen.
48. Exp20 comparison counts are 1099/1008/986/823 plus exp21.
49. The online runtime activation contract remains NOT_ESTABLISHED.
50. No OMNeT++ or INET simulation was invoked.
