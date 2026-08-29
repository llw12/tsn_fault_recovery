# Shared class example: diamond_auto / C0001

Members: l_s1_s2, l_s2_s4.

Common affected flows: TT.

Union-disabled synthesis links: l_s1_s2, l_s2_s4.

## P0 and robust routes

| Flow | Healthy P0 | Shared robust route |
|---|---|---|
| TT | source → s1 → s2 → s4 → destination | source → s1 → s3 → s4 → destination |

## Member Per-Failure routes

### l_s1_s2

- TT: source → s1 → s3 → s4 → destination

### l_s2_s4

- TT: source → s1 → s3 → s4 → destination

## Shared logical GCL windows

The shared Profile contains 6 complete gate entries generated from one all-active-TT Z3 schedule.

## Per-member runtime validation

| Fault | Profile SHA | Activation | Stable delivery | Stable deadlines | Pass |
|---|---|---:|---:|---:|---:|
| l_s1_s2 | `ba3d478c8f56deba260a6923601942c72e20c3c4af874c91d69a7b5936360dcc` | True | True | True | True |
| l_s2_s4 | `ba3d478c8f56deba260a6923601942c72e20c3c4af874c91d69a7b5936360dcc` | True | True | True | True |
