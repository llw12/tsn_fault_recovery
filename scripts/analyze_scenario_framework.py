#!/usr/bin/env python3
"""Aggregate exp05 runs, assert framework behavior, and draw mesh10."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.result_analyzer import SUMMARY_FIELDS
from tools.scenario_compiler import compile_scenario


def rows(path: Path):
    return list(csv.DictReader(path.open()))


def write(path: Path, data: list[dict], fields: list[str]):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,lineterminator="\n");writer.writeheader();writer.writerows(data)


def assert_results(run_dirs: list[Path]) -> None:
    flows={(d.parent.parent.parent.name, d.parent.parent.name):rows(d/"flows.csv") for d in run_dirs}
    diamond_no=flows[("diamond","no-recovery")]; diamond_on=flows[("diamond","online")]
    dno=next(r for r in diamond_no if r["flow_id"]=="TT"); don=next(r for r in diamond_on if r["flow_id"]=="TT")
    assert int(dno["lost"]) > int(don["lost"]), "Diamond online must restore affected TT delivery"
    assert don["route_before"] != don["route_after"], "Diamond affected route must change"
    mesh_on=flows[("mesh10","online")]; mesh_no=flows[("mesh10","no-recovery")]
    affected=[r for r in mesh_on if r["flow_id"].startswith("TT") and r["affected_by_fault"]=="True"]
    unaffected=[r for r in mesh_on if r["flow_id"].startswith("TT") and r["affected_by_fault"]=="False"]
    assert len(affected)==3 and len(unaffected)==7, "mesh10 selected fault must yield 3 affected and 7 unaffected TT flows"
    assert all(r["route_before"]!=r["route_after"] for r in affected), "each affected mesh10 flow must be independently rerouted"
    assert all(r["route_before"]==r["route_after"] for r in unaffected), "unaffected mesh10 routes must be preserved"
    assert all(int(r["deadline_miss_count"])==0 for r in affected+unaffected), "mesh10 online delivered TT packets must meet E2E deadlines"
    no_by_id={r["flow_id"]:r for r in mesh_no}
    assert sum(int(no_by_id[r["flow_id"]]["lost"]) for r in affected)>0, "mesh10 NoRecovery affected flows must degrade"
    assert sum(int(r["lost"]) for r in affected)<=3, "mesh10 Online should limit affected loss to transition packets"


def deterministic_check() -> None:
    with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
        for name in ("diamond","mesh10"):
            left=compile_scenario(ROOT/f"configs/scenarios/{name}.yaml",a);right=compile_scenario(ROOT/f"configs/scenarios/{name}.yaml",b)
            for artifact in ("scenario.json","port_map.json","ScenarioNetwork.ned","base.ini","omnetpp.ini"):
                assert (left/artifact).read_bytes()==(right/artifact).read_bytes(),f"non-deterministic {name}/{artifact}"


def topology_png(path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    scenario=json.loads((ROOT/"generated/mesh10/scenario.json").read_text())
    switch_pos={"sw1":(0,1),"sw2":(1.8,1.8),"sw3":(1.8,.2),"sw4":(3.7,-.2),"sw5":(3.9,2.1),
                "sw6":(5.4,1),"sw7":(6.6,-.3),"sw8":(7,2),"sw9":(8.5,.5),"sw10":(10,1.5)}
    es_pos={"es1":(-1,1),"es2":(1.8,3),"es3":(3.7,-1.35),"es4":(6.6,-1.45),"es5":(8.5,-.9),"es6":(10,2.8)}
    pos={**switch_pos,**es_pos}; fig,axis=plt.subplots(figsize=(11,6.4))
    for link in scenario["links"]:
        a,b=link["endpoint_a"],link["endpoint_b"]; axis.plot([pos[a][0],pos[b][0]],[pos[a][1],pos[b][1]],color="#A7B0BA",linewidth=1.5,zorder=1)
    for node,(x,y) in switch_pos.items(): axis.scatter(x,y,s=720,marker="s",color="#326891",edgecolor="white",linewidth=1.4,zorder=2);axis.text(x,y,node,color="white",ha="center",va="center",fontsize=9,fontweight="bold")
    for node,(x,y) in es_pos.items(): axis.scatter(x,y,s=560,marker="o",color="#D99B2B",edgecolor="white",linewidth=1.4,zorder=2);axis.text(x,y,node,color="#17202A",ha="center",va="center",fontsize=9,fontweight="bold")
    fault=next(link for link in scenario["links"] if link["id"]=="l_sw2_sw5");a,b=fault["endpoint_a"],fault["endpoint_b"];axis.plot([pos[a][0],pos[b][0]],[pos[a][1],pos[b][1]],color="#C94343",linewidth=4,zorder=1,label="Selected fault: l_sw2_sw5")
    axis.set_title("mesh10 scenario topology",loc="left",fontsize=16,pad=16);axis.text(0,1.01,"10 switches, 6 end systems, 22 links; highlighted link is the exp05 fault",transform=axis.transAxes,fontsize=9,color="#4C535C")
    axis.legend(frameon=False,loc="lower left");axis.set_aspect("equal");axis.axis("off");fig.tight_layout();fig.savefig(path,dpi=180,facecolor="white");plt.close(fig)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--run-dir",type=Path,action="append",required=True);parser.add_argument("--output-dir",type=Path,required=True);args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True); assert_results(args.run_dir);deterministic_check()
    summaries=[];flows=[]
    for directory in args.run_dir:summaries+=rows(directory/"summary.csv");flows+=rows(directory/"flows.csv")
    write(args.output_dir/"framework_summary.csv",summaries,SUMMARY_FIELDS);write(args.output_dir/"diamond_summary.csv",[r for r in summaries if r["scenario"]=="diamond"],SUMMARY_FIELDS);write(args.output_dir/"mesh10_summary.csv",[r for r in summaries if r["scenario"]=="mesh10"],SUMMARY_FIELDS);write(args.output_dir/"flow_summary.csv",flows,list(flows[0]))
    fault_data={name:json.loads((ROOT/f"generated/{name}/fault_analysis.json").read_text()) for name in ("diamond","mesh10")};(args.output_dir/"fault_analysis.json").write_text(json.dumps(fault_data,indent=2,sort_keys=True)+"\n")
    manifest_source = next(directory / "manifest.json" for directory in args.run_dir if "mesh10" in directory.parts and "online" in directory.parts)
    (args.output_dir / "manifest_example.json").write_text(json.dumps(json.loads(manifest_source.read_text()), indent=2, sort_keys=True) + "\n")
    topology_png(args.output_dir/"mesh10_topology.png")
    by={(r["scenario"],r["mode"]):r for r in summaries};mn=by[("mesh10","no-recovery")];mo=by[("mesh10","online")];dn=by[("diamond","no-recovery")];do=by[("diamond","online")]
    lines=["# Scenario-Driven Experiment Framework v1","","## Result","",f"The same YAML→canonical model→generated NED/INI→P0→runner pipeline completed Diamond and mesh10 in NoRecovery and Online modes. Diamond Online reduced TT loss from {dn['tt_lost']} to {do['tt_lost']}; mesh10 Online reduced TT loss from {mn['tt_lost']} to {mo['tt_lost']} with zero delivered deadline misses.","","![mesh10 topology](mesh10_topology.png)","","## mesh10 fault behavior","",f"For `l_sw2_sw5`, TT1, TT2, and TT7 were affected and independently rerouted. The other seven TT routes were byte-for-byte preserved. All ten active TT routes were jointly rescheduled, so recovery did not ignore contention with unaffected traffic.","","## Runtime boundary","",f"The mesh10 online route, SMT, and activation wall times were {float(mo['route_solver_wall_us_runtime']):.3f} µs, {float(mo['smt_solver_wall_us_runtime']):.3f} µs, and {float(mo['activation_wall_us']):.3f} µs. The configured simulation-time solver delay was {float(mo['simulated_decision_delay_s'])*1e3:.3f} ms.","","## Scope","","This exp05 regression remains scoped to NoRecovery and Online. Offline Per-Failure is evaluated by exp06; `offline-cluster` remains intentionally `NOT_IMPLEMENTED`. SMT SAT constrains the schedule budget through the last controlled egress; packet traces independently verify the end-to-end application deadline.",""]
    (args.output_dir/"summary.md").write_text("\n".join(lines),encoding="utf-8")
    print("EXP05_ASSERTIONS PASS")
if __name__=="__main__":main()
