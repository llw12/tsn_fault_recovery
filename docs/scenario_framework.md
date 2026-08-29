# Scenario-Driven Experiment Framework v1

An experiment is the independent composition of `Scenario + RecoveryMode + Fault`.
Scenario YAML contains topology, traffic, timing, link properties, and stable
logical fault candidates; it never selects a recovery method.

Run one complete experiment from the repository root:

```bash
python tools/run_experiment.py \
  --scenario configs/scenarios/diamond.yaml \
  --mode online \
  --fault l_s1_s2
```

`--mode all` runs `no-recovery` and `online`. `offline-per-failure` and
`offline-cluster` deliberately return `NOT_IMPLEMENTED`; they are registry
entries for the next research stage, not simulated substitutes.

The compiler validates YAML once, converts units into a canonical
`ScenarioModel`, and deterministically writes `scenario.json`,
`ScenarioNetwork.ned`, `base.ini`/`omnetpp.ini`, and `port_map.json`. It assigns
UDP ports and `ethN` indexes in stable order. Generated runtime files and raw
run directories are ignored by Git; the small exp05 aggregate is committed.

At precompute time, the C++ controller independently finds every TT primary
route on a pure `NetworkGraph`, jointly solves all TT windows, compiles gates,
and serializes P0. A fault disables both directional transmission channels of
the selected logical link. This models a physical bidirectional link outage
without deleting gates while a frame may be in transmission.

Online recovery classifies a flow as affected only when its P0 `linkPath`
contains the failed link. It recomputes each affected source/destination route,
preserves all unaffected routes, and jointly reschedules all active TT flows.
The `ScenarioRuntimeAdapter` is the boundary that reads current channel state
and maps logical links to OMNeT++ interface/gate paths. The shared
`ProfileSwitcher` performs the atomic forwarding/GCL activation.

Each run stores a manifest plus packet, flow, timing, and summary CSVs under
`results/scenarios/<scenario>/<mode>/<fault>/<run-id>/`. Packet delivery uses
explicit `<flowId>-<sequence>` identity. See `deadline_model.md` for the strict
separation between application E2E deadlines and SMT schedule budgets.
