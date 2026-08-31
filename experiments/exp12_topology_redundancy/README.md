# exp12 — Topology Redundancy Sensitivity of Recovery-Profile Equivalence

This experiment holds the structured40 workload and healthy primary routes fixed
while increasing only the undirected switch-edge supply through five nested 5×8
grid topologies.  The levels have 67, 75, 80, 100, and 120 internal edges (average
degree 3.35, 3.75, 4, 5, and 6); these are target average degrees, not regular-graph
claims.

R1 has exactly the current structured40 switch edge set.  R2–R4 use a frozen,
deterministic balanced greedy policy over Manhattan-distance-2 pairs.  Healthy P0
routes come once from the R0 production BFS.  Every level schedules those routes
with the production Z3/GCL/forwarding pipeline, while fault recovery continues to
use production BFS on the current topology.

Only PF, J100, J040, and J020 are formal.  Shared synthesis union-disables every
member fault edge, performs an attachment-pair connectivity precheck, preserves
unaffected frozen routes, and applies the exp09 fixed merge-tree recursive split.
Accepted shared classes require every-member single-link OMNeT++ validation with
zero runtime route, Z3, grouping, and synthesis computation.

Run the formal campaign from a clean implementation commit:

```bash
bash experiments/exp12_topology_redundancy/run.sh
```

The checkpoint is `scratch/exp12/<run_id>/checkpoint.json`; final versioned
artifacts are written to `results/topology_redundancy/`.
