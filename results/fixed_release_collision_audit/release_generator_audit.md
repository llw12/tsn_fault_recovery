# Release-generator source audit

The frozen exp18 generator was located at `tools/generate_realistic_tsn_scenarios.py`.

- `stable_release(flow_id, period_ns)` hashes `f"{flow_id}:{SEED}"` with SHA-256; the formal seed is `1024`.
- It computes `period_ns // 500` selectable positions and multiplies by 100 ns, giving a 100 ns-aligned release in the inclusive range `[0, 20% period]`.
- The input contains only flow ID, period and seed: it has no source/egress state, no aggregate same-source launch test, and no collision-avoidance step.
- Thus the generator makes each flow individually representable but does not guarantee aggregate launch feasibility at a shared source egress.
