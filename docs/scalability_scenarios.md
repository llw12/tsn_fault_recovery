# exp10 structured redundant mesh scenarios

`structured20_auto.yaml` remains the frozen exp08/exp09 baseline.  The exp10
generator creates 5×6, 5×8, and 5×10 members of the same structured redundant
mesh family: grid neighbours plus one deterministic non-grid diagonal per
column.  This grows the topology without turning it into either a tree or a
complete graph.

End systems are attached at evenly spaced switches in canonical order.  TT
sources cycle across them and destinations advance by a scale-relative half
turn plus a deterministic round; thus sources and destinations remain
distributed and differ.  TT releases are uniformly distributed in a fixed
0–380 µs horizon, packet sizes cycle through 180/220/260/300/340 B, and E2E
deadlines cycle through 900/920/940/950/930 µs.  BE flows keep a 0.2 BE/TT
ratio, 500 µs interval, and the existing fixed packet-size pattern.

All generated scenarios use seed 0, one TT class, a 1 ms period/hyperperiod,
1 Gbps links, and automatic `switch-switch` / `tt-primary-route-used`
candidate selection.  Generator output and its manifest are deterministic.
