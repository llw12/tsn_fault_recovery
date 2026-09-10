# exp20 PF redundancy and shared-profile opportunity audit

Formal verdict: `PF_REDUNDANCY_OPPORTUNITY_ESTABLISHED`.

## Direct answers

1. PF was not rerun: this is a read-only, post-hoc audit; no H2S/CELF synthesis, upstream scheduler, OMNeT++, or INET ran.
2. Input is the complete exp19 census: 1,099 canonical `SUCCESS_H2S` profiles with recomputed byte and semantic-hash parity.
3–7. Per-scenario structural census (the reduction is only a hypothetical candidate grouping opportunity):
   - M_RING: faults=80; distinct affected sets=80; faults in multi-member groups=0; largest group=1; candidate reduction=0.00%.
   - M_REDSTAR: faults=128; distinct affected sets=96; faults in multi-member groups=64; largest group=2; candidate reduction=25.00%.
   - M_ROR: faults=110; distinct affected sets=109; faults in multi-member groups=2; largest group=2; candidate reduction=0.91%.
   - L_RING: faults=208; distinct affected sets=208; faults in multi-member groups=0; largest group=1; candidate reduction=0.00%.
   - L_REDSTAR: faults=318; distinct affected sets=239; faults in multi-member groups=158; largest group=2; candidate reduction=24.84%.
   - L_ROR: faults=255; distinct affected sets=254; faults in multi-member groups=2; largest group=2; candidate reduction=0.39%.
8–12. Exact profile redundancy is shown per scenario below; semantic equality includes forwarding, routes, releases, GCL/windows. Across the census, route-forwarding exact duplicates=91; schedule exact duplicates=91. These component counts describe equality, not fuzzy similarity or a deployment contract.
   - M_RING: semantic unique=80/80; semantic duplicates=0; route-only unique=80; route-forwarding unique=80; schedule-only unique=80.
   - M_REDSTAR: semantic unique=101/128; semantic duplicates=27; route-only unique=101; route-forwarding unique=101; schedule-only unique=101.
   - M_ROR: semantic unique=110/110; semantic duplicates=0; route-only unique=110; route-forwarding unique=110; schedule-only unique=110.
   - L_RING: semantic unique=208/208; semantic duplicates=0; route-only unique=208; route-forwarding unique=208; schedule-only unique=208.
   - L_REDSTAR: semantic unique=254/318; semantic duplicates=64; route-only unique=254; route-forwarding unique=254; schedule-only unique=254.
   - L_ROR: semantic unique=255/255; semantic duplicates=0; route-only unique=255; route-forwarding unique=255; schedule-only unique=255.
13. S1 exact semantic dedup is analysis-only: baseline semantic bytes raw/gzip=6679776648/313532328; S1 raw+index/gzip+index=6106304690/290485566.
14. Runtime deployment contract remains `NOT_ESTABLISHED`; these are semantic-representation storage opportunities, not device storage measurements.
15. All 1,099 diagonal raw-slot replays were qualified before cross-fault analysis.
16–18. Valid same-scenario existing-profile→fault relations=1660; average source coverage=1.510; maximum source coverage=8.
19–21. Multi-fault affected-set groups with a member profile covering all singleton members=113; union-disabled static-valid multi-groups=113, covering 226 faults.
22–23. Conservative validated affected-group profile counts/reductions are in `compute_opportunity.csv`; they are ex-post evidence, not measured shared-synthesis solve savings.
24–25. The exact per-scenario ex-post existing-profile cover is an oracle computed from all observed outcomes, not an algorithm that could choose K profiles in advance:
   - M_RING: minimum=80 of 80; exact=True; solver=z3 5.1.0.
   - M_REDSTAR: minimum=64 of 128; exact=True; solver=z3 5.1.0.
   - M_ROR: minimum=88 of 110; exact=True; solver=z3 5.1.0.
   - L_RING: minimum=208 of 208; exact=True; solver=z3 5.1.0.
   - L_REDSTAR: minimum=159 of 318; exact=True; solver=z3 5.1.0.
   - L_ROR: minimum=224 of 255; exact=True; solver=z3 5.1.0.
26–29. Stored semantic payload counts S1/S2/S3 are 1008/986/823; raw+index bytes are 6106304690/5967962377/5042778349; gzip-payload+index bytes are 290485566/284855807/246883153.
30–31. `affected_group_profile_diversity.csv` distinguishes equal affected sets with differing semantic, route, or schedule components; equal affected flow IDs do not imply an equal PF profile.
32–33. Topology and M→L comparisons are intentionally reported as exact per-scenario counts above, rather than a cross-scenario merge: scenario namespaces remain isolated.
34. No: equal affected sets cannot prove one profile per group.
35–36. A stronger claim needs a fixed existing profile to pass the full static checker on the group union-disabled graph. Then E\C ⊆ E\{e} proves validity for every singleton member; the converse is not assumed.
37–39. Exp20 did not reduce PF computation or modify ProfileStore. It supplies an optimization-opportunity census and future-experiment design evidence.
40. Next-stage recommendation: EXP21_UNION_FAULT_SHARED_SYNTHESIS is worth an explicit human decision because union-valid groups were observed; exp20 itself did not run that synthesis.

All cross-fault claims are same-scenario project-independent static-checker replay claims. A union-disabled static-valid profile establishes existing-profile reuse, not that a future union-fault heuristic will discover it.
