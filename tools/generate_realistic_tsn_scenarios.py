"""Deterministic literature-grounded synthetic industrial TSN scenarios for exp18."""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from tools.jrs_wa_adapter import canonical_json_bytes

SEED = 1024
FORMAL_SCALES = {"M": (8, 3), "L": (16, 4)}
TOPOLOGIES = ("RING", "REDSTAR", "ROR")


def digest(value: Any) -> str: return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def stable_release(flow_id: str, period_ns: int) -> int:
    """Stable 100 ns tick in [0, 20% of period], independent of topology."""
    ticks = period_ns // 500
    return (int.from_bytes(hashlib.sha256(f"{flow_id}:{SEED}".encode()).digest()[:8], "big") % (ticks + 1)) * 100


def logical_factory(cells: int, machines_per_cell: int) -> dict[str, Any]:
    devices=[]; flows=[]
    def device(identifier: str, role: str, cell: int, machine: int | None = None):
        devices.append({"id":identifier,"role":role,"cell":cell,"machine":machine})
    def flow(identifier: str, kind: str, src: str, dst: str, period_ns: int, payload: int, deadline_ns: int):
        flows.append({"id":identifier,"kind":kind,"source":src,"destination":dst,"packet_size_bytes":payload,
                      "period_ns":period_ns,"deadline_ns":deadline_ns,"release_ns":stable_release(identifier,period_ns),"pcp":4,"traffic_class":1})
    controllers=[]
    for c in range(1,cells+1):
        cc=f"CC_C{c:02d}"; controllers.append(cc); device(cc,"CellController",c)
        for m in range(1,machines_per_cell+1):
            stem=f"C{c:02d}_M{m:02d}"; plc=f"PLC_{stem}"; device(plc,"PLC",c,m)
            sensors=[f"S{n}_{stem}" for n in range(1,5)]; actuators=[f"A{n}_{stem}" for n in range(1,5)]
            for item in sensors: device(item,"Sensor",c,m)
            for item in actuators: device(item,"Actuator",c,m)
            for sensor in sensors:
                flow(f"SF_{sensor}","SensorFast",sensor,plc,500_000,64,400_000)
                flow(f"SC_{sensor}","SensorCyclic",sensor,plc,2_000_000,128,1_500_000)
            for actuator in actuators: flow(f"CMD_{plc}_{actuator}","ControlCommand",plc,actuator,500_000,64,400_000)
            flow(f"STAT_{plc}","MachineStatus",plc,cc,2_000_000,256,1_500_000)
            flow(f"COORD_{cc}_{plc}","MachineCoordination",cc,plc,4_000_000,256,3_000_000)
    for c, cc in enumerate(controllers, 1):
        flow(f"IC_PREV_{cc}","InterCellPrevious",cc,controllers[(c-2)%cells],8_000_000,512,6_000_000)
        flow(f"IC_NEXT_{cc}","InterCellNext",cc,controllers[c%cells],8_000_000,512,6_000_000)
    return {"schema_version":1,"cells":cells,"machines_per_cell":machines_per_cell,"devices":sorted(devices,key=lambda x:x["id"]),"flows":sorted(flows,key=lambda x:x["id"])}


def attachment_switches(factory: dict[str,Any]) -> tuple[list[str],dict[str,str]]:
    switches=[]; attachment={}
    for device in factory["devices"]:
        c=device["cell"]; m=device["machine"]; role=device["role"]
        if role=="CellController": sw=f"SW_C{c:02d}_CC"
        else: sw=f"SW_C{c:02d}_M{m:02d}_{'SENS' if role=='Sensor' else 'CTRL' if role=='PLC' else 'ACT'}"
        attachment[device["id"]]=sw; switches.append(sw)
    return sorted(set(switches)),attachment


def topology(factory: dict[str,Any], kind: str) -> tuple[list[str],list[tuple[str,str]],dict[str,str]]:
    access, attachment=attachment_switches(factory); cells=factory["cells"]; mpc=factory["machines_per_cell"]
    edges:set[tuple[str,str]]=set(); switches=set(access)
    def edge(a:str,b:str):
        if a==b: raise ValueError("self loop")
        edges.add(tuple(sorted((a,b))))
    if kind=="RING":
        for a,b in zip(access,access[1:]+access[:1]): edge(a,b)
    elif kind=="REDSTAR":
        for c in range(1,cells+1):
            ca=f"SW_C{c:02d}_CORE_A"; cb=f"SW_C{c:02d}_CORE_B"; switches.update((ca,cb)); edge(ca,cb)
            local=[x for x in access if x.startswith(f"SW_C{c:02d}_")]
            for sw in local: edge(sw,ca); edge(sw,cb)
            for core in (ca,cb):
                for fc in ("SW_FACTORY_CORE_A","SW_FACTORY_CORE_B"): edge(core,fc)
        switches.update(("SW_FACTORY_CORE_A","SW_FACTORY_CORE_B")); edge("SW_FACTORY_CORE_A","SW_FACTORY_CORE_B")
    elif kind=="ROR":
        backbone=[]
        for c in range(1,cells+1):
            local=[x for x in access if x.startswith(f"SW_C{c:02d}_")]
            for a,b in zip(local,local[1:]+local[:1]): edge(a,b)
            a,b=f"SW_BB_C{c:02d}_A",f"SW_BB_C{c:02d}_B"; switches.update((a,b)); backbone += [a,b]
            edge(local[0],a); edge(local[1],b)
        for a,b in zip(backbone,backbone[1:]+backbone[:1]): edge(a,b)
    else: raise ValueError(kind)
    return sorted(switches),sorted(edges),attachment


def bridge_count(switches:list[str], edges:list[tuple[str,str]]) -> int:
    graph=defaultdict(set)
    for a,b in edges: graph[a].add(b); graph[b].add(a)
    def connected(skip:tuple[str,str]|None=None):
        start=switches[0]; seen={start}; q=deque([start])
        while q:
            n=q.popleft()
            for x in graph[n]:
                if tuple(sorted((n,x)))==skip or x in seen: continue
                seen.add(x);q.append(x)
        return len(seen)==len(switches)
    if not connected(): raise ValueError("switch graph disconnected")
    return sum(not connected(edge) for edge in edges)


def build_scenario(scale:str, kind:str) -> tuple[dict[str,Any],dict[str,Any]]:
    cells,machines=FORMAL_SCALES[scale]; factory=logical_factory(cells,machines); switches,internal,attachment=topology(factory,kind)
    nodes=[{"id":d["id"],"type":"end_system"} for d in factory["devices"]]+[{"id":x,"type":"switch"} for x in switches]
    links=[]
    for a,b in internal: links.append({"id":f"l_{a}_{b}","endpoint_a":a,"endpoint_b":b,"bitrate_bps":1_000_000_000,"propagation_delay_s":0.0})
    for es,sw in sorted(attachment.items()): links.append({"id":f"a_{es}_{sw}","endpoint_a":es,"endpoint_b":sw,"bitrate_bps":1_000_000_000,"propagation_delay_s":0.0})
    flows=[{"id":f["id"],"source":f["source"],"destination":f["destination"],"packet_size_bytes":f["packet_size_bytes"],"period_s":f["period_ns"]/1e9,"deadline_e2e_s":f["deadline_ns"]/1e9,"schedule_deadline_budget_s":f["deadline_ns"]/1e9,"release_offset_s":f["release_ns"]/1e9,"pcp":4,"traffic_class":1} for f in factory["flows"]]
    scenario={"schema_version":1,"scenario_name":f"{scale}_{kind}","forwarding_model":"stream-aware","simulation":{"duration_s":.03,"cycle_time_s":.008,"time_quantum_s":1e-9,"failure_time_s":.01,"solver_delay_s":0.0,"random_seed":SEED},"network":{"default_bitrate_bps":1_000_000_000,"default_propagation_delay_s":0.0},"scheduling":{"ingress_margin_s":0.0,"hop_margin_s":0.0,"endpoint_budget_s":0.0,"frame_overhead_bytes":64,"be_traffic_class":0},"nodes":sorted(nodes,key=lambda x:x["id"]),"links":sorted(links,key=lambda x:x["id"]),"tt_flows":flows,"be_flows":[],"fault_candidates":[],"fault_candidate_policy":{"mode":"explicit","exclude":[]}}
    degree=defaultdict(int)
    for a,b in internal: degree[a]+=1;degree[b]+=1
    audit={"scale":scale,"topology":kind,"switch_count":len(switches),"ES_count":len(factory["devices"]),"TT_flow_count":len(flows),"internal_link_count":len(internal),"bridge_count":bridge_count(switches,internal),"min_degree":min(degree.values()),"max_degree":max(degree.values()),"average_switch_degree":statistics.mean(degree.values()),"logical_factory_sha256":digest(factory),"workload_sha256":digest(factory["flows"]),"topology_sha256":digest(internal)}
    return scenario,audit


def write_formal(output:Path)->list[dict[str,Any]]:
    output.mkdir(parents=True,exist_ok=True); audits=[]
    for scale in FORMAL_SCALES:
        for kind in TOPOLOGIES:
            scenario,audit=build_scenario(scale,kind); (output/f"{scenario['scenario_name']}.json").write_bytes(canonical_json_bytes(scenario));audits.append(audit)
    return audits
