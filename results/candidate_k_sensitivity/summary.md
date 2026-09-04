# exp18c candidate-route budget sensitivity

The controlled variable is only the DijkstraOverlap candidate-route budget K = 5, 8, 12, 16. The six formal inputs are frozen exp18 scenario bytes.

**Verdict:** `K_BUDGET_NO_MEANINGFUL_EFFECT`. `HEURISTIC_POLICY_SENSITIVITY` is a recommendation for a separately authorized next stage; this experiment did not start PF.

| Scenario | K5 | K8 | K12 | K16 |
| --- | ---: | ---: | ---: | ---: |
| M_RING | 348 | 348 | 348 | 348 |
| M_REDSTAR | 348 | 348 | 348 | 348 |
| M_ROR | 348 | 348 | 348 | 348 |
| L_RING | 914 | 914 | 914 | 914 |
| L_REDSTAR | 914 | 914 | 914 | 914 |
| L_ROR | 914 | 914 | 914 | 914 |

K propagation qualification passed: the synthetic diagnostic flow had 5 candidates at K=5 and 8 at K=8 (remaining underfilled at K=12/16 because the graph contains eight distinct routes). In the formal workloads, candidate vectors expanded for at least one flow, but no scheduled count or HNF identity improved. All repeat comparisons passed. This is not an infeasibility claim.
