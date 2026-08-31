# Qualification of a Stream-Aware TSNKit JRS-WA Recovery Backend

| Verdict | Result |
|---|---|
| INTEGRATION_PASS | true |
| JRS_WA_BACKEND_QUALIFIED | false |

Stream-aware forwarding is implemented through deterministic per-TT-flow VLAN-backed stream handles. INET's VLAN-aware MAC forwarding lookup consumes `(destination MAC, VID)`, while the project abstraction and validator use `(switch, flowId)`. Q00 is the decisive same-destination divergent-egress test; its OMNeT result is `True`. BE remains on VID 0 and its forwarding is checked separately.

Canonical mapping, packet size, release, deadline, route-lock, and GCL semantics are documented in `input_mapping.md` and their structured audits. JRS-WA runs only offline; every backend result records zero runtime invocation.

Solver status counts: `{"GUROBI_LICENSE_CAPACITY_LIMIT": 7, "OPTIMAL": 3}`. Repeatability stability: `{"Q01": true, "Q06": true, "Q09": true}`. Former legacy forwarding-conflict results: `Q06=GUROBI_LICENSE_CAPACITY_LIMIT/OMNeT=False; Q07=GUROBI_LICENSE_CAPACITY_LIMIT/OMNeT=False; Q08=GUROBI_LICENSE_CAPACITY_LIMIT/OMNeT=False`.

The backend suitability verdict does not require every case to be SAT. It does require at least one former legacy forwarding-conflict case to be synthesized and deployed; failure of that criterion is reported rather than hidden or replaced with an easier case.
