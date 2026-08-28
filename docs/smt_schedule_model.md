# Discrete-time SMT model for TAS schedule recovery

## Scope and terminology

This stage is **BFS-based online rerouting plus SMT-based TAS rescheduling**. The
route is selected before the SMT model is built. Route choice is not an SMT
decision variable, so this implementation must not be described as joint
routing-scheduling optimization.

The solver consumes one fixed route per affected TT flow and produces logical
TT transmission windows. A separate `GateScheduleCompiler` converts those
windows into INET `PeriodicGate` parameters. The solver does not encode INET's
`initiallyOpen`, `offset`, or alternating-duration representation.

## Integer time domain

All model times are integer ticks. The first implementation uses a configurable
quantum `q = 1 us`; no floating-point or Real-valued Z3 time is used.

For cycle duration `cycleTime`, the cycle length is

```text
C = exactTicks(cycleTime / q)
```

and configuration is rejected when a cycle, period, deadline, release offset,
or hop margin cannot be represented exactly in ticks.

For TT flow `f`, serialization length is calculated, not hard-coded:

```text
txTicks[f] = ceil(((packetBytes[f] + frameOverheadBytes) * 8)
                  / (linkBitrate * q))
```

With the current 200 B payload, 64 B model overhead, 100 Mbit/s link, and
1 us quantum, this evaluates to `ceil(21.12) = 22` ticks.

The first version supports one common 1 ms period/hyperperiod. Multi-period
hyperperiod construction is deliberately out of scope.

## Inputs

Each affected flow contains:

- stable `flowId`;
- source and destination module paths;
- payload bytes and traffic class;
- period and deadline;
- release/reference offset within the cycle;
- an ordered fixed route expressed as egress interface paths.

Global scheduling inputs are cycle, quantum, link bitrate, modeled frame
overhead, configurable source-to-first-egress `ingressMargin`, configurable
forwarding `hopMargin`, TT class, and BE class.

## Variables

For each affected flow `f` and hop `h` on its fixed route:

```text
x[f,h] : Int
```

is the start tick of that flow's transmission window on the hop's egress.
The window is the half-open interval
`[x[f,h], x[f,h] + txTicks[f])`.

An auxiliary integer `maxCompletion` is constrained to be at least every
flow's last-hop completion.

## Constraints

### Release and cycle bounds

For every `f,h`:

```text
0 <= x[f,h]
x[f,h] + txTicks[f] <= C
```

For the first hop:

```text
x[f,0] >= releaseOffsetTicks[f] + ingressMarginTicks
```

`ingressMargin` conservatively covers source-link serialization, propagation,
and processing before the packet becomes eligible at the first scheduled
switch egress. The deadline reference remains the original source release.

### Hop precedence

For every successive pair of route hops:

```text
x[f,h+1] >= x[f,h] + txTicks[f] + hopMarginTicks
```

`ingressMargin` and `hopMargin` are explicit inputs. `hopMargin` represents the conservative forwarding
and processing allowance between scheduled transmissions and is never embedded
as a solver constant.

### Shared-egress non-overlap

For any two logical windows `i` and `j` that use the same egress path:

```text
end(i) <= start(j) OR end(j) <= start(i)
```

This prevents TT transmissions from overlapping on a shared output link. No
constraint is added for different egresses, allowing pipeline concurrency.

### Deadline

For every flow, with `last(f)` denoting its final hop:

```text
x[f,last(f)] + txTicks[f] - releaseOffsetTicks[f]
    <= deadlineTicks[f]
```

This is the boundary of the controllable TAS scheduling model, not a claim that
the destination application receives the packet at that instant. Source-stack,
uncontrolled ingress/final-link, and destination-stack latency remain outside
the first-version constraint. exp04 therefore also checks the configured
end-to-end deadline against explicit packet send/receive identities; its
450/500/550 us SAT deadlines exceed the calculated 216 us controlled-egress
makespan and the measured endpoint-inclusive delays. Extending the formal bound
through the endpoints requires a calibrated latency model in a later phase.

Packet delivery and deadline success are reported separately in experiments:
a lost packet is not silently counted as a deadline miss, while a delivered
packet whose measured end-to-end delay exceeds its deadline is a deadline miss.

## Optimization and deterministic tie-breaking

The solver uses Z3 `Optimize` with lexicographic objectives:

1. minimize `maxCompletion`;
2. minimize the sum of all last-hop completion ticks;
3. minimize every `x[f,h]` in stable `(flowId, hop)` order.

The final objectives remove unconstrained model choices and make repeated runs
with identical inputs return the same logical windows. The result records
`SAT`, `UNSAT`, or `UNKNOWN`, the primary objective value, and solver wall-clock
time. Wall-clock execution never advances OMNeT++ simulation time.

For an UNSAT result, deterministic prechecks identify capacity and per-flow
minimum-deadline contradictions when possible; otherwise the diagnostic states
that the combined SMT constraints are inconsistent.

## Gate schedule compilation

The solver returns `GateWindow {flowId, gatePath, trafficClass, start, end}`.
For each egress/class, `GateScheduleCompiler` sorts the windows, rejects overlap,
merges adjacent intervals, and emits an even, positive duration vector whose
sum is exactly one cycle.

Given sorted TT windows, durations alternate:

```text
TT open, TT closed, TT open, TT closed, ...
```

The final closed duration includes the cycle tail plus the gap before the first
window. `initiallyOpen=true`; `offset=C-firstWindowStart` when the first window
does not begin at zero. This matches INET 4.7.0 `PeriodicGate` runtime offset
semantics.

The BE gate is generated programmatically from the same durations and offset
with `initiallyOpen=false`. Therefore TT and BE are exact complements throughout
the cycle. The compiler verifies:

- every duration is positive;
- duration count is even;
- durations sum exactly to the cycle;
- TT and BE never share the same state;
- no implicit all-closed interval is introduced.

## Guard-band interpretation

Each logical window conservatively covers the complete modeled frame
serialization interval. INET's existing implicit guard-band behavior remains
enabled. This is not a complete IEEE 802.1Q guard-band or frame-preemption
optimization model.

## Validation cases

The dedicated solver tests cover single-flow SAT, shared-link non-overlap,
precedence, deadline satisfaction, impossible capacity, impossible deadline,
GCL duration invariants, TT/BE complement, and deterministic repeatability.

The multi-flow SAT simulation uses three 1 ms TT flows with different packet
sizes and deadlines on a shared three-hop path. A separate configuration makes
the deadline impossible and must return UNSAT. Existing exp01-exp03 use their
unchanged deterministic backend unless a configuration explicitly selects Z3.
