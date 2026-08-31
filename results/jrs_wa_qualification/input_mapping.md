# Exp13 canonical-input mapping

- Source: existing canonical `scenario.json` plus sibling `port_map.json`; no second scenario parser.
- Nodes: lexical logical IDs mapped deterministically to zero-based TSNKit IDs.
- Links: every Ethernet link becomes two directed arcs; disabling a logical link removes both arcs. Endpoint access links remain in complete routes. Stock JRS-WA precedence consumes `max_t_proc` but not `t_prop`, so physical propagation is explicitly recorded in both columns.
- Time: project seconds are converted exactly to integer nanoseconds; TSNKit internal slot is configured to 1 ns. Hyperperiod must exactly equal the configured cycle or conversion returns `UNSUPPORTED_HYPERPERIOD`.
- Frame size: TSNKit JRS-WA `size` drives `t_trans_1g`; the adapter passes serialization-equivalent bytes for `(packetBytes + frameOverheadBytes)` exactly once.
- Deadline: TSNKit deadline receives `scheduleDeadlineBudget`; OMNeT destination validation retains `deadlineE2E`.
- Release: a minimal recovery extension fixes first-hop transmission time to the canonical release offset.
- Route scope: unaffected TT streams are locked to exact directed healthy-route arcs; affected TT streams remain free. All TT streams remain in scheduling constraints.
- Output: selected directed arcs and integer-ns transmissions are normalized, converted to per-flow forwarding rules and complementary TT/BE GCLs, then serialized in the existing ProfileDefinition schema. Each TT gate interval is extended by the canonical `ingress_margin + hop_margin`, with overlapping same-class intervals merged, to absorb INET guard-band implementation overhead without changing JRS feasibility.
