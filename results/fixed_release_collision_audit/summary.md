# exp18i: Static exact fixed-release source-egress collision audit

## Result

Verdict: `FIXED_RELEASE_COLLISION_EXACTLY_EXPLAINS_ADMISSION_GAP`. Next-stage recommendation: `REDESIGN_DETERMINISTIC_SOURCE_EGRESS_RELEASE_ASSIGNMENT`.

## Evidence and interpretation

1. exp18h observed first-hop `FIXED_RELEASE_START_MISMATCH` rejections, so this stage evaluates their necessary structural condition without rerunning a scheduler.
2. In qualified ASAP semantics, `fixed_release=true` requires first-hop start `== release_offset + k*period`; it is not merely `>= release`.
3. Source-egress qualification passed for all analyzed frozen flows: `True`. Qualification uses a unique physical source data-plane egress, not an observed route choice.
4. Python conversion exactly matches every checked exp18h upstream integer input field and transmission duration: `True`. The analysis uses 100 ns integer ticks and an 8 ms hypercycle.
5. Original D100 collision edges: Medium `4`, Large `14`. Graph equality across all three topologies is recorded per density in `conflict_graph_summary.csv`.
6. MVC curve: M: D100=4, D095=3, D090=2, D085=2, D080=1, D070=0, D060=0, D050=0; L: D100=14, D095=13, D090=13, D085=12, D080=9, D070=8, D060=6, D050=3.
7. The three exp18h Large D050 HNF crosschecks are: CMD_PLC_C06_M02_A2_C06_M02 -> CMD_PLC_C06_M02_A1_C06_M02; CMD_PLC_C06_M02_A4_C06_M02 -> CMD_PLC_C06_M02_A3_C06_M02; COORD_CC_C05_PLC_C05_M02 -> COORD_CC_C05_PLC_C05_M01; CMD_PLC_C06_M02_A2_C06_M02 -> CMD_PLC_C06_M02_A1_C06_M02; CMD_PLC_C06_M02_A4_C06_M02 -> CMD_PLC_C06_M02_A3_C06_M02; COORD_CC_C05_PLC_C05_M02 -> COORD_CC_C05_PLC_C05_M01; CMD_PLC_C06_M02_A2_C06_M02 -> CMD_PLC_C06_M02_A1_C06_M02; CMD_PLC_C06_M02_A4_C06_M02 -> CMD_PLC_C06_M02_A3_C06_M02; COORD_CC_C05_PLC_C05_M02 -> COORD_CC_C05_PLC_C05_M01. Their static first egress matches the traced failure link and each has an earlier accepted collision peer: `True`.
8. Every historical H2S HNF set is checked as a vertex cover and every verified scheduled set as an independent set; those gates prevent the static model from contradicting a verified schedule.
9. Full original workloads are proven infeasible only under the current exact fixed-release source-egress semantics: M `True`, L `True`. Corresponding admission cardinality-optimality flags are M `True`, L `True`.
10. The located generator audit status is `GENERATOR_SOURCE_INCOMPLETE`: releases are deterministic per `(flow_id, seed, period)` and are neither source-aware nor collision-avoiding.
11. Deadline relaxation cannot remove a mandatory exact-start source collision when period, release and frame size are invariant; `deadline_collision_invariance.json` verifies those frozen input fields.
12. K and downstream topology alternatives cannot remove a collision after a proven unavoidable source egress. Identical cross-topology graphs explain stable cardinality. All `228` frozen exp18d/exp18f/baseline HNF variants are vertex covers: `True`; all are exact minimum covers: `True`. This explains identity-only changes as different minimum covers.
13. The workload-construction defect is therefore not an industrial-role impossibility: independent per-flow release hashing permits mutually overlapping mandatory launches on one local source egress.

This is a property of the current synthetic release assignment and exact-launch constraint, not evidence that the corresponding industrial traffic roles are inherently unschedulable. No H2S, CELF, PF, OMNeT++, or INET run was performed.
