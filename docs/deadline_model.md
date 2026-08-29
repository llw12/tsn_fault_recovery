# Deadline semantics in scenario-driven experiments

Scenario YAML `deadline` is the end-to-end application deadline measured from
source packet generation to destination application reception. It is stored as
`deadline_e2e_s` and is evaluated only from explicit `flowId-sequence` packet
records.

The current Z3 model controls transmission windows from the first scheduled
switch egress through the last scheduled switch egress. It therefore receives a
separate `schedule_deadline_budget_s`:

```text
schedule_deadline_budget = deadline_e2e - endpoint_budget
```

`endpoint_budget` is an explicit scenario scheduling parameter covering the
uncontrolled source stack/link and final link/destination stack. It is a
conservative experiment assumption, not a calibrated industrial guarantee.
`ingress_margin` and `hop_margin` remain separate scheduling constraints.

Consequently, SMT SAT means that the controlled-egress schedule fits its
budget. It does **not** by itself prove the end-to-end application deadline.
Every scenario-driven run independently reports delivery and measured end-to-end
deadline success. A non-positive schedule budget is rejected by validation.
