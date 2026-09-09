# exp18j: source-egress-aware fixed-release assignment

exp18j does not lower density: it preserves Medium 352/3616 and Large 928/9632 logical-flow/instance counts. exp18i proved that independently hashed per-flow releases could force impossible same-source-egress exact launches.
Fixed-release semantics were not changed: first-hop start remains exactly `release + k*period`. The sole workload semantic change in each SAR scenario is `release_offset_s`; periods, deadlines, payloads, endpoints, roles, topology, routing and backend configuration remain frozen.
The allocator reads no HNF, CELF, or scheduler outcome. It uses source-local 100-ns intervals, the original 20%-of-period release window, exact non-overlap constraints, and proven lexicographic minimum perturbation. No new source collision is accepted by the exp18i static detector.
Release impact: M: 4 changed, total 48 ticks, max 21 ticks; L: 14 changed, total 113 ticks, max 21 ticks. Original MVC lower bounds / exact changed counts are M 4/4 and L 14/14.

| scenario | formal complete (repeat 1) | H2S complete | CELF complete |
|---|---:|---:|---:|
| M_RING | True | True | False |
| M_REDSTAR | True | True | False |
| M_ROR | True | True | False |
| L_RING | True | True | False |
| L_REDSTAR | True | True | False |
| L_ROR | True | True | False |

Formal verdict: **`FULL_DENSITY_PF_BENCHMARK_QUALIFIED`**. H2S-alone 6/6 status: `True`. CELF is recorded separately above; verifier/checker and repeatability are hard gates.

A qualified result concerns this fixed backend/model only. It does not mean the industrial roles are inherently easy or that original independent release hashing was the only possible generator. It repairs an internal source-launch construction defect; exp18j performs no PF, fault enumeration, Profile Store, OMNeT++, or INET run.
