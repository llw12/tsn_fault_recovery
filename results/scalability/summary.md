# exp10 — Large-scale scalability analysis

## Technical summary

Candidate faults grew 20→22, 30→26, 40→32, 50→52 as the structured mesh increased from 20 to 50 switches. PF precompute rose from 5171.7 ms to 98148.4 ms, while P0 Z3 rose from 215.7 ms to 2117.5 ms. J020 retained 63.5% profile-count compression at 50 switches, with 63.1% storage compression.

The experimental diagnosis is **VALIDATION_CAMPAIGN_BOTTLENECK**: at 50 switches the four logical validation sums total 2206.0 s, versus 98.1 s for PF precompute. This is campaign-engineering cost, not online recovery computation. The formal artifact does not justify GA or k-shortest routing: only one NO_ROUTE occurred across all recorded policy attempts, at structured20/J020; no UNSAT or TIMEOUT was recorded.

## Profile compression remains useful but scale-dependent

Profile counts were: 20: PF 22, J100 17, J060 13, J040 9, J020 8; 30: PF 26, J100 21, J060 20, J040 16, J020 10; 40: PF 32, J100 26, J060 24, J040 21, J020 17; 50: PF 52, J100 39, J060 36, J040 26, J020 19. J020 compression by scale was 20→63.6%, 30→61.5%, 40→46.9%, 50→63.5%. Count and storage compression track closely at every scale because serialized recovery Profiles have similar sizes within each scenario. The J020 largest validated class changed 20→6, 30→4, 40→3, 50→5; it did not grow monotonically. Candidate-to-realized J020 compression gaps were 20→4.5%, 30→0.0%, 40→0.0%, 50→0.0%; only structured20 had a non-zero gap, caused by its recorded NO_ROUTE and recursive split.

## Solver and routing costs

PF per-fault Z3 mean/p95/max increased from 231.4/298.2/310.1 ms at 20 switches to 1879.7/2188.6/2289.3 ms at 50 switches. PF route mean remained 0.427 ms at 50 switches, far below Z3. Z3 scaling is therefore the main measured algorithmic growth signal, but the absence of solver timeout/UNSAT means the formal evidence does not yet establish a scheduling feasibility bottleneck.

## Scope, definitions, and measurement boundaries

Compression uses PF-SAT recovery Profiles as the denominator and excludes P0. PF recovery coverage uses all candidate faults; shared fault coverage is the fraction of all candidate faults in multi-fault classes and is not total recovery coverage. Storage excludes metadata; PF storage is deterministically recovered from each policy's recorded Profile bytes and storage-compression definition, with cross-policy consistency checks.

Wall-clock values are empirical measurements on the formal experiment host. `bottleneck_breakdown.csv` reports only captured P0 route/Z3, PF total, and logical validation sums; it is not campaign elapsed time and must not be interpreted as mutually exclusive process-stage accounting.

## Missing formal metrics

- **NOT_CAPTURED:** shared-synthesis Z3 mean/p50/p95/max and shared-stage detailed timing.
- **NOT_CAPTURED:** representative Online audit; no audit figure is generated.
- **NOT_CAPTURED:** peak RSS / memory.
- **NOT_CAPTURED:** actual campaign/stage timestamps, cache counts, and orchestration elapsed time.
- **NOT_CAPTURED:** explicit forwarding-conflict and validation-failed counters. Their absence is not converted to zero.

The missing metrics cannot be reconstructed from `campaign.json` or the retained checkpoint metadata without rerunning simulation/solver stages, which this post-processing patch intentionally does not do.

## Method and provenance

This report is deterministic post-processing of the immutable formal artifact only. No OMNeT++, Z3, route solver, grouping, Profile synthesis, or member validation is invoked.

- Simulation implementation commit: `ed97466cf46d79a74171833af0ea69114d3fdb48`
- Simulation run_id: `20260830T133528Z`
- Raw formal artifact: `results/scalability/campaign.json`
- Analysis code commit: `ded9cce2051dba45df2cd85456cfa25fe7d25493`

## Recommendation

Do not introduce GA/k-shortest routing based on exp10. The next evidence-driven step is to keep the recovery algorithm frozen and either optimize validation infrastructure for experiment throughput or study SMT scalability separately. Any new solver work should preserve the distinction between offline algorithm wall-clock and OMNeT++ validation cost.

## Further questions

The existing artifact cannot answer whether shared synthesis Z3 scales differently from PF Z3, how peak memory grows, or whether representative Online behavior remains identical at every scale. Those questions require a future explicitly instrumented campaign, not retroactive inference.
