# Pinned upstream ordering audit

At `650a9665e7bafb70fcf19c9f0a247e1d7b885ffd`, `ProgramOptions.h` defaults H2S to `LOW_PERIOD_FLOWS_FIRST`. `HierarchicalHeuristicScheduling::scheduleSet()` builds a priority queue with that flow sorter. CELF (`CelfFlowQueuing::scheduleSet`) prioritizes configurations, dynamically re-rates entries, and is not governed by the same per-flow rank. `input_rank` in the P0 CSV is the canonical scenario-flow numeric ID; it is **not** a proven scheduler-admission rank.
