# Built-in H2S primary policy audit

Pinned upstream commit: `650a9665e7bafb70fcf19c9f0a247e1d7b885ffd`.

The upstream `-f,--flow-sorting` option is an integer enum. `LOW_PERIOD_FLOWS_FIRST` (4) is the historical default. `HIGHEST_TRAFFIC_FLOWS_FIRST` (0) was discovered but is intentionally recorded-only: it is not part of the preregistered formal matrix.

| tag | enum | CLI | class | audited comparator semantics | ordering inputs read |
|---|---|---:|---|---|---|
| LOW_PERIOD | LOW_PERIOD_FLOWS_FIRST | 4 | LowPeriodFlowsFirst | priority queue top: period ascending; equal period frame_size descending; final flow ID ascending (BASELINE mode). Reads period, frame_size, flow ID. | period, frame_size, flow_id |
| LOWEST_TRAFFIC | LOWEST_TRAFFIC_FLOWS_FIRST | 1 | LowTrafficFlowsFirst | priority queue top: traffic estimate frame_size/period ascending; equal estimate flow ID descending under the upstream comparator. Reads frame_size, period, flow ID. | traffic_estimate(frame_size/period), frame_size, period, flow_id |
| LOWEST_ID | LOWEST_ID_FIRST | 2 | LowestIdFirst | priority queue top: numeric flow ID ascending. Reads flow ID only. | flow_id |
| SOURCE_NODE | SOURCE_NODE_SORTING | 3 | SourceNodeSorting | priority queue top: source fan-out descending; destination numeric ID descending; traffic estimate descending; flow ID descending. Reads source, destination, frame_size, period, flow ID. | source_node_fan_out, destination, traffic_estimate(frame_size/period), frame_size, period, flow_id |

Fixed but not swept: configuration rating 1, placement 0, offensive planning false, DIJKSTRA_OVERLAP routing, and candidate-path budget 5.

The existing exp18d tie-break extension remains in the local upstream worktree solely for order export and its conditional seeded mode. Every exp18f H2S command explicitly supplies `--h2s-tiebreak-mode BASELINE --h2s-tiebreak-seed 0`; therefore no seeded comparator path is selected. No upstream source file is changed by this experiment.
