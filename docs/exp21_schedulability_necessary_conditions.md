# exp21 schedulability necessary conditions

exp21 uses four deterministic, monotone rejection filters before invoking the
pinned H2S/CELF backend.  Passing a filter is only permission to try the
backend.  It is never reported as a schedulability or feasibility proof.

1. **F1 connectivity.**  In the physical graph after deleting every fault in
   the proposed group, every TT source must remain connected to its
   destination.
2. **F2 minimum serialization delay.**  For every TT flow, the surviving
   minimum hop count multiplied by its conservative 100 ns transmission time
   must not exceed its quantized deadline budget.
3. **F3 checked-cut capacity.**  Over the 8 ms hyperperiod, demand is compared
   with full-duplex directional capacity on a finite checked family: every
   bridge-induced cut and one deterministic minimum physical-edge cut for each
   distinct affected-flow endpoint pair.  The result applies only to this
   named family; it is not an all-cuts claim.
4. **F4 checked-cut time windows.**  For the same cuts, each packet obtains an
   earliest possible crossing time and latest possible crossing completion.
   Mandatory work is checked for all critical circular windows using an exact
   range-add/range-maximum segment-tree sweep.  Deadlines beyond the first
   hyperperiod remain unwrapped and jobs are duplicated by one hyperperiod.

The first failed filter emits a witness and a certificate containing its exact
disabled-link set.  Because link deletion can only remove routes or capacity,
that certificate may reject a superset of the same faults.  H2S/CELF failure
does not generate such a certificate: it remains `HEURISTIC_NOT_FOUND` (or its
specific resource/output status), not an infeasibility proof.

Candidates are confined to one scenario and one identical affected-flow-set
pool.  Passed candidates are ordered by time-window slack, checked-cut slack,
minimum-delay slack, group size, and canonical group identifier.  A successful
multi-fault profile is accepted only after the upstream verifier, the project
static checker, and static replay against every member's singleton topology.
During replay only topology-dependent queue identifiers may be rebound.

The implemented algorithm is `SPFG-v1`.  Its final grouping is a deterministic
local optimum under the stated pairwise merge order; it is not a global
minimum-profile proof.  The runtime profile activation contract remains
`NOT_ESTABLISHED`.
