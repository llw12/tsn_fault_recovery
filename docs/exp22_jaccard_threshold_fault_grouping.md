# exp22 Jaccard-threshold fault grouping

`JSPFG-v1` keeps exp21's F1--F4 implementation, H2S/CELF configuration,
workload, timing and recovery semantics unchanged.  The only algorithmic
change is candidate eligibility: two active groups are considered when their
healthy-P0 affected-flow-set Jaccard similarity satisfies a preregistered,
exact rational threshold.

The group feature is the union of member singleton affected-flow sets.  It is
recomputed after every accepted merge.  Similarity comparisons and ordering
use integer cross-products; decimal values are reporting-only.

Jaccard rejection is a deliberate candidate-space restriction, not a
schedulability statement and never a monotone certificate.  In contrast,
F1--F4 failures retain exp21's necessary-condition certificate semantics.
H2S/CELF failure only means that the fixed provider did not construct a
complete valid shared profile.

The preregistered sweep is `1`, `4/5`, `3/5`, `2/5`, `1/5`, and
`POSITIVE_OVERLAP`.  Each threshold starts from singleton groups, has its own
checkpoint/root/profile store, and performs new backend synthesis.  The result
is a sensitivity table and a two-dimensional Pareto frontier; it does not
choose a universally optimal threshold.  Runtime activation remains
`NOT_ESTABLISHED`.
