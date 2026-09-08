# exp18d source audit

`LOW_PERIOD_FLOWS_FIRST` preserves its primary semantic keys: period ascending, then frame size descending. `BASELINE` uses the original comparator unchanged. Only when both semantic keys are equal does `SEEDED_TIEBREAK` replace the final numeric-flow-ID ordering with `stable_seeded_tiebreak_key(tie_break_seed XOR numeric_flow_id)`; numeric ID remains a collision fallback.

The value flows only through `ProgramOptions` → `main.cpp` → `HierarchicalHeuristicScheduling` → `FlowSorterFactory` → `LowPeriodFlowsFirst`. CELF is not selected or invoked. The order export records actual pop position, primary keys, tie key, mode, and seed. Routing, K=5, global scenario seed, rating, placement, and all other policy choices are unchanged.

The non-formal qualification contains four equal-priority 1 ms flows and one lower-priority 2 ms flow. Seed 0, 1, and 2 alter only the equal-priority suborder; the ordered primary-priority tuple is byte-identical across modes and candidate vectors are identical. Formal candidate vectors are a per-scenario hard invariant across baseline plus all 32 seeded runs.
