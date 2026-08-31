# Topology Redundancy Sensitivity of Recovery-Profile Equivalence

## Technical summary

The controlled nested campaign completed five 40-switch topologies from 67 to 120 internal edges. The largest observed realized compression was 0.794 (R4_D6, J020). Connectivity is interpreted as recovery-path supply; final sharing additionally depends on the fixed BFS route, Z3 schedule, forwarding realizability, and every-member runtime validation.

## RQ1–RQ14 evidence-backed answers

1. **RQ1 — actual recovery redundancy:** average degree rose through 3.35, 3.75, 4.0, 5.0, 6.0; TT-pair edge-connectivity mean/p50/p95 rose through 2.92/3.00/4.00, 3.15/3.00/4.00, 3.55/4.00/4.00, 5.00/5.00/5.00, 5.70/6.00/6.00. The structural supply of recovery paths therefore increased, especially at R3 and R4.
2. **RQ2 — controlled-variable identity:** all five levels share workload `8d58dcd0abed`, frozen primary routes `e0b76069c6fa`, candidate faults `4b60d000a8e3`, affected sets `e84839cb64ca`, Jaccard inputs `cfb2508d1ff1`, and one group hash per policy.
3. **RQ3 — raw GRAPH_DISCONNECTED:** counts by R0→R4 were J100 [0, 0, 0, 0, 0], J040 [0, 0, 0, 0, 0], and J020 [1, 0, 0, 0, 0]; only J020 improved, from one disconnected raw group to zero.
4. **RQ4 — union-disabled connectivity:** connected fractions were J100 1.000, 1.000, 1.000, 1.000, 1.000, J040 1.000, 1.000, 1.000, 1.000, 1.000, and J020 0.889, 1.000, 1.000, 1.000, 1.000. No fixed group regressed from connected to disconnected.
5. **RQ5 — topology rescue:** 1 fixed raw group became connected; synthesis/validation rescues were 3/3 transition events.
6. **RQ6 — candidate versus realized compression:** candidate compression stayed fixed at J100=0.265, J040=0.412, J020=0.676; realized compression increased to a maximum of 0.794 at R4_D6/J020, but this coincided with fewer PF-feasible faults and must not be read as pure sharing gain.
7. **RQ7 — candidate-realized gap:** R0→R4 gaps were J100 0.000, 0.000, -0.118, -0.294, -0.412, J040 0.000, 0.000, -0.088, -0.206, -0.324, and J020 0.029, 0.000, -0.029, -0.059, -0.118. Negative values reflect final stores that omit infeasible PF cases, not extra validated sharing.
8. **RQ8 — shared fault coverage:** R0→R4 coverage was J100 0.529, 0.529, 0.412, 0.235, 0.118, J040 0.824, 0.824, 0.647, 0.353, 0.235, and J020 0.912, 0.941, 0.794, 0.441, 0.324; coverage fell at high redundancy under the fixed production BFS.
9. **RQ9 — largest validated class:** sizes were J100 2, 2, 2, 2, 2, J040 2, 2, 2, 2, 2, and J020 6, 6, 4, 4, 3; the maximum remained 6 and did not grow with added edges.
10. **RQ10 — PF recoverability:** SAT counts were 34/34, 34/34, 28/34, 19/34, 13/34 for R0→R4. Recoverability fell from 34/34 to 13/34 because deterministic BFS route drift produced forwarding conflicts despite stronger graph connectivity.
11. **RQ11 — connected synthesis failures:** after excluding successful attempts, failures were {'FORWARDING_CONFLICT': 2, 'GRAPH_DISCONNECTED': 1}; the only connected shared-synthesis rejection type observed was FORWARDING_CONFLICT, with no solver timeout or unknown result.
12. **RQ12 — added-edge use:** non-grid recovery-edge uses/total uses were 0/14516 (0.0%), 234/13810 (1.7%), 268/11551 (2.3%), 360/8438 (4.3%), 367/5917 (6.2%) for R0→R4. Added layers were therefore exercised rather than merely present.
13. **RQ13 — recovery quality:** all comparable shared validations had zero TT loss and zero deadline misses (True); the maximum absolute PF/shared mean recovery-latency difference was 0.565 us, with explicit same-level denominators.
14. **RQ14 — evidence-based conclusion:** topology redundancy was a real supply-side constraint (higher TT-pair connectivity and one connectivity rescue), but it was not sufficient for better deployable sharing under deterministic BFS: PF SAT and shared coverage fell while route hashes changed in 233/250 comparable transitions. Native-P0 candidate counts 34, 32, 32, 36, 43 also confirm why freezing healthy routes was necessary.

## Scope, method, and definitions

R0 is the 5×8 grid; R1 is exactly the current structured40 undirected switch edge set; R2/R3/R4 add 5/20/20 deterministic Manhattan-distance-2 edges. Healthy primary routes, traffic, scheduling parameters, candidate faults, affected sets, Jaccard inputs, and raw group memberships are frozen. Recovery routes are recomputed by the production BFS on each current topology.

## Limitations and robustness checks

The experiment characterizes the production deterministic BFS choice, not all feasible routes. A connected but schedule-UNSAT case does not establish that every route is infeasible. The design is descriptive for one structured workload and one deterministic nested edge policy; it does not claim regular graphs or causal generalization to arbitrary TSN topologies.

## Recommended next steps

Treat exp12 as the topology-sensitivity result and stop here; alternative-route optimization or other topology families require a separately preregistered experiment.

## Further questions

A future study could vary workload placement independently of topology while retaining the same frozen-route and full-member-validation controls.
