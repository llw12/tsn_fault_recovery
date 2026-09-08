# exp18d: H2S equal-priority tie-break sensitivity

All 198 formal constructions use byte-frozen exp18 whole-P0 inputs: H2S only, DijkstraOverlap, K=5, one thread, seed 1024, 30 s timeout, and 8192 MB cap. Baseline parity is exact.

All six scenarios have 32 distinct actual seeded order sequences and invariant candidate vectors. HNF membership changes (8 distinct sets on each M scenario; 31 on each L scenario), but scheduled-flow count never exceeds baseline: 348/352 for M and 914/928 for L. No start completes P0, so best-of-N and the serial practical policy yield no valid whole-P0 rescue. For each fixed seed, the three topology variants at a scale still have exactly equal HNF sets.

The formal verdict is `TIEBREAK_IDENTITY_ONLY_EFFECT`: the evidence establishes sensitivity of *which* flows H2S leaves unscheduled, not a whole-P0 benefit from multi-start tie-breaking. The recommended next study is heuristic search-trajectory diagnosis; PF is intentionally not started.
