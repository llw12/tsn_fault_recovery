"""Read-only exp18b diagnosis from the two upstream P0 attempts saved by exp18."""
from __future__ import annotations
import csv, json, statistics
from collections import defaultdict
from pathlib import Path
from typing import Any
from tools.h2s_jrs_backend import OUTPUT_MARKER
from tools.jrs_wa_adapter import canonical_json_bytes

ROOT=Path(__file__).resolve().parents[1]; SOURCE=ROOT/"results/realistic_tsn_pf_cost"; OUT=ROOT/"results/p0_hnf_diagnosis"
ORDER=("M_RING","M_REDSTAR","M_ROR","L_RING","L_REDSTAR","L_ROR")
def write_csv(path:Path,rows:list[dict]):
    path.parent.mkdir(parents=True,exist_ok=True); fields=sorted({k for r in rows for k in r})
    with path.open("w",encoding="utf-8",newline="") as h:
        w=csv.DictWriter(h,fields,lineterminator="\n");w.writeheader();w.writerows(rows)
def raw(identifier:str,attempt:str)->tuple[dict,dict,dict]:
    scenario=json.loads((SOURCE/"scenarios"/f"{identifier}.json").read_text())
    ordinal="0" if attempt=="h2s" else "1"
    meta=json.loads((SOURCE/"logs"/identifier/"P0"/f"{ordinal}_{attempt}_metadata.json").read_text())
    text=(SOURCE/"logs"/identifier/"P0"/f"{ordinal}_{attempt}_stdout.log").read_text()
    payload=json.loads(next(x[len(OUTPUT_MARKER):] for x in text.splitlines() if x.startswith(OUTPUT_MARKER)))
    return scenario,meta,payload
def kind(flow:str)->str:
    return {"SF":"SensorFast","SC":"SensorCyclic","CMD":"ControlCommand","STAT":"MachineStatus","COORD":"MachineCoordination","IC_PREV":"InterCellPrevious","IC_NEXT":"InterCellNext"}[next(p for p in ("IC_PREV","IC_NEXT","SF","SC","CMD","STAT","COORD") if flow.startswith(p))]
def diagnose(identifier:str,attempt:str)->tuple[list[dict],list[dict],dict]:
    scenario,meta,payload=raw(identifier,attempt); flows=sorted(scenario["tt_flows"],key=lambda x:x["id"]); fmap={i:f for i,f in enumerate(flows)}
    node_map={node["id"]:i for i,node in enumerate(sorted(scenario["nodes"],key=lambda x:x["id"]))}
    slots=defaultdict(list)
    for s in payload["slots"]: slots[fmap[int(s["flow_id"])]["id"]].append(s)
    H=8_000_000; identity=[]; instances=[]; hnf=[]
    for rank,f in enumerate(flows):
        fid=f["id"]; period=round(f["period_s"]*1e9); release=round(f["release_offset_s"]*1e9); expected=H//period; grouped=defaultdict(list)
        for s in slots[fid]: grouped[(int(s["start_tick"])*100-release)//period].append(s)
        complete=partial=missing=0
        for ix in range(expected):
            values=sorted(grouped.get(ix,[]),key=lambda x:x["start_tick"]); expected_release=release+ix*period
            status="MISSING_INSTANCE" if not values else "PARTIAL_INSTANCE"
            # Source/destination node IDs are checked directly; a complete instance is a continuous source-to-destination chain.
            if values:
                source=node_map[f["source"]]; destination=node_map[f["destination"]]
                continuous=values[0]["source"]==source and values[-1]["destination"]==destination and all(a["destination"]==b["source"] for a,b in zip(values,values[1:]))
                status="COMPLETE_INSTANCE" if continuous else "PARTIAL_INSTANCE"
            complete += status=="COMPLETE_INSTANCE"; partial += status=="PARTIAL_INSTANCE"; missing += status=="MISSING_INSTANCE"
            instances.append({"scenario":identifier,"backend":attempt.upper(),"flow_id":fid,"flow_kind":kind(fid),"period_ns":period,"instance_index":ix,"expected_release_ns":expected_release,"observed_slot_count":len(values),"completion_status":status,"first_slot_ns":int(values[0]["start_tick"])*100 if values else "","last_slot_ns":int(values[-1]["end_tick"])*100 if values else ""})
        cls="FULLY_SCHEDULED" if complete==expected else "ZERO_SCHEDULED" if not complete and not partial else "PARTIAL_INSTANCES" if complete else "PARTIALLY_PLACED_INSTANCE"
        row={"scale":identifier[0],"topology":identifier.split("_",1)[1],"backend":attempt.upper(),"flow_id":fid,"flow_kind":kind(fid),"source":f["source"],"destination":f["destination"],"cross_cell":("IC_" in fid),"period_ns":period,"deadline_ns":round(f["deadline_e2e_s"]*1e9),"release_ns":release,"payload_bytes":f["packet_size_bytes"],"expected_instances":expected,"complete_instances":complete,"partial_instances":partial,"missing_instances":missing,"flow_completion_class":cls,"input_rank":rank,"input_rank_percentile":rank/max(len(flows)-1,1),"candidate_route_count":payload.get("candidate_path_counts",{}).get(str(rank),0)}
        identity.append(row)
        if cls!="FULLY_SCHEDULED": hnf.append(fid)
    summary={"scenario":identifier,"scale":identifier[0],"topology":identifier.split("_",1)[1],"backend":attempt.upper(),"total_flows":len(flows),"fully_scheduled":len(flows)-len(hnf),"HNF_flows":len(hnf),"scheduled_flow_count_upstream":payload["scheduled_flow_count"],"total_expected_instances":sum(x["expected_instances"] for x in identity),"complete_instances":sum(x["complete_instances"] for x in identity),"partial_instances":sum(x["partial_instances"] for x in identity),"missing_instances":sum(x["missing_instances"] for x in identity)}
    return identity,instances,summary
def main()->int:
    allid=[]; allinst=[]; summaries=[]; source=[]
    for sid in ORDER:
        for attempt in ("h2s","celf"):
            identity,instances,summary=diagnose(sid,attempt);allid+=identity;allinst+=instances;summaries.append(summary)
    for scale in ("M","L"):
        for backend in ("H2S","CELF"):
            rows=[r for r in allid if r["scale"]==scale and r["backend"]==backend]; sets={t:{r["flow_id"] for r in rows if r["topology"]==t and r["flow_completion_class"]!="FULLY_SCHEDULED"} for t in ("RING","REDSTAR","ROR")}
            for a,b in (("RING","REDSTAR"),("RING","ROR"),("REDSTAR","ROR")):
                u=sets[a]|sets[b];source.append({"scale":scale,"backend":backend,"topology_a":a,"topology_b":b,"missing_a":len(sets[a]),"missing_b":len(sets[b]),"intersection":len(sets[a]&sets[b]),"union":len(u),"jaccard":len(sets[a]&sets[b])/len(u) if u else 1,"exact_equal":sets[a]==sets[b]})
    write_csv(OUT/"unscheduled_flow_identity.csv",allid);write_csv(OUT/"instance_completion.csv",allinst);write_csv(OUT/"scenario_diagnosis.csv",summaries);write_csv(OUT/"flow_set_comparison.csv",source)
    (OUT/"source_p0_manifest.json").write_bytes(canonical_json_bytes({"source":"results/realistic_tsn_pf_cost","diagnostic_replay":False,"p0_summary_sha256":__import__("hashlib").sha256((SOURCE/"p0_summary.csv").read_bytes()).hexdigest()}))
    return 0
if __name__=="__main__":raise SystemExit(main())
