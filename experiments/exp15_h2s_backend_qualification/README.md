# exp15 H2S backend qualification

This experiment qualifies the exact-pinned AdvancedFlowScheduler H2S constructive heuristic, with CELF fallback, for healthy P0 route and schedule synthesis only.

Upstream provenance: `https://github.com/gepperho/AdvancedFlowScheduler.git`, commit `650a9665e7bafb70fcf19c9f0a247e1d7b885ffd`, Apache-2.0. The tracked patch adds only release/deadline propagation, fixed-release verification, schedule/candidate export, exp14 zero-delay alignment, and a bfd Release-link fallback; it does not alter H2S ordering, scoring, placement selection, or route generation.

Bootstrap the external checkout with `scripts/bootstrap_h2s_backend.sh`. Run `run.sh --quick`, then `run.sh --qualification`, and only after the qualification gate passes run `run.sh`. Every backend process is single-threaded, limited to 30 seconds and 8192 MB. The formal routing policy is DIJKSTRA_OVERLAP with K=5 and a conservative 100 ns tick.

No OMNeT++ simulation, fault profile campaign, candidate fault discovery, or plotting is performed. A heuristic failure is reported as `HEURISTIC_NOT_FOUND`, never as `INFEASIBLE`.
