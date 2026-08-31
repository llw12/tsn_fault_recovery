# Exp13: Stream-Aware TSNKit JRS-WA Backend Qualification

This experiment qualifies, but does not optimize or compare as a new recovery algorithm, an offline TSNKit JRS-WA joint routing/scheduling backend.

The new data-plane mode is explicit `forwardingModel: stream-aware`. TT flow IDs receive deterministic IEEE 802.1Q VID-backed stream handles; INET performs real VLAN-aware forwarding lookups and OMNeT packet observations verify each expected egress. Scenarios without the field retain the historical `destination-mac` default.

TSNKit is pinned to v0.3.0 commit `f8492f76753e75aa2254feb3e326feec3faad4a8`. The adapter uses exact integer nanoseconds, maps on-wire bytes once, adds fixed-release and exact directed-route-lock constraints without changing the JRS-WA objective, and emits the existing profile schema. Gurobi runs with a 30-second limit, one thread, and seed 1024.

Setup and formal run:

```bash
scripts/setup_jrs_backend.sh
experiments/exp13_jrs_wa_qualification/run.sh
```

The formal run requires a clean implementation commit and absent `results/jrs_wa_qualification/`. It produces only CSV, JSON, and Markdown. Q04–Q09 are selected from committed exp12 artifacts by deterministic lexical rules; case IDs and faults are not manually substituted after observing JRS outcomes.

JRS-WA is offline only. Runtime OMNeT activation loads a converted profile and records zero JRS/Gurobi invocations.
