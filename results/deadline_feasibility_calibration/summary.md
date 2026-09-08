# exp18e: Deadline feasibility calibration

The original literature-grounded, role-based synthetic industrial workload remains frozen. Its tight D/T=0.75–0.80 timing configuration was not P0-complete under the fixed qualified heuristic backend; this does not prove infeasibility or identify deadline tightness as the unique cause.

To make a legitimate healthy-P0 input for a future PF-cost study, this separate experiment used only the pre-registered uniform rule `D'=min(alpha·D,T)` over α={1, 11/10, 6/5, 5/4, 4/3}. Exact Fraction/integer-ns arithmetic was used; periods, flow population, roles, source/destination, payloads, release offsets, topology, routing, K, seed and backend were unchanged. Periods were not relaxed because that would change packet-instance counts and PF synthesis complexity.

Cycle-boundary qualification: **PASS**. It tests D=T with nonzero release and a final absolute deadline beyond the cycle; no deadline clipping, release change, PF run, OMNeT++ run, INET run, or figure generation occurred.

Baseline α=1 exact parity: **PASS**. Formal verdict: **`NO_PF_ELIGIBLE_ALPHA_IN_PREREGISTERED_LADDER`**.

| alpha | complete scenarios | min scheduled ratio |
|---:|---:|---:|
| 1/1 | 0/6 | 0.984914 |
| 11/10 | 0/6 | 0.984914 |
| 6/5 | 0/6 | 0.984914 |
| 5/4 | 0/6 | 0.984914 |
| 4/3 | 0/6 | 0.984914 |

The deadline-calibrated workload, if qualified, is a derivative of the literature-grounded synthetic workload—not a real factory trace and not evidence that the original workload was infeasible. Heuristic output need not be monotonic even though deadline relaxation does not shrink the mathematical feasible set.

No PF, Profile Store, parallel campaign, or OMNeT simulation is started here; advancing to a realistic PF-cost experiment requires manual confirmation.
