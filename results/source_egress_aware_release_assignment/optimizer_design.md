# Exact source-egress-aware release optimizer

Each TT flow has an integer 100-ns release variable `R'` with domain
`0 <= R' <= floor(P/5)`.  Its original release is checked to be in that same
domain.  For every pair of flows on one proven unavoidable source egress and
for every periodic instance pair, the half-open interval `[R'+kP,R'+kP+C)` is
made non-overlapping.  Three `-H, 0, +H` shifted copies of each opposite
interval encode circular 8-ms boundary semantics without truncation.  No
cross-egress, routing, downstream-link, placement, deadline, or scheduler
outcome constraint is present.

Z3's `Solver` is used with a 60-second limit per source group.  It first finds
SAT, then proves the exact lexicographic objective by repeated SAT checks:
(1) minimum changed-flow count, (2) minimum total absolute shift, (3) minimum
maximum absolute shift, and (4) canonical-flow-ID-order minimization of every
remaining `R'`.  `Optimize` and heuristic fallbacks are deliberately absent.
An `unknown` result or process timeout is reported as
`SOURCE_RELEASE_OPTIMALITY_UNPROVEN`; an UNSAT group stays inside the original
20%-period window and is reported as
`SOURCE_RELEASE_ASSIGNMENT_UNSAT_WITHIN_ORIGINAL_WINDOW`.
