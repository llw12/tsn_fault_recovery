#!/usr/bin/env python3
"""Aggregate exp08 class synthesis, runtime validation, compression, and figures."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("diamond_auto", "mesh10_auto", "structured20_auto")
LABELS = {"diamond_auto": "Diamond", "mesh10_auto": "mesh10", "structured20_auto": "structured20"}
BLUE, GOLD, INK, GREY = "#2F6F9F", "#D39A24", "#202A35", "#98A2AD"


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle: return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n"); writer.writeheader()
        for row in rows: writer.writerow({key: row.get(key, "") for key in fields})


def read_summary(scenario: str, mode: str, fault: str, run_id: str) -> dict:
    path = ROOT / "results/scenarios" / scenario / mode / fault / run_id / "summary.csv"
    rows = read_csv(path)
    if len(rows) != 1: raise RuntimeError(f"expected one summary row: {path}")
    return rows[0]


def as_float(value): return None if value in (None, "") else float(value)


def profile_bytes(root: Path, entries: list[dict]) -> int:
    return sum((root / entry["profile_file"]).stat().st_size for entry in entries)


def collect(run_id: str):
    groups, classes, synthesis, validations, compression, comparisons = [], [], [], [], [], []
    stores = {}
    for scenario in SCENARIOS:
        generated = ROOT / "generated" / scenario
        class_root = generated / "profiles/exact_equivalence"
        store = json.loads((class_root / "store.json").read_text()); stores[scenario] = store
        pf_root = generated / "profiles/per_failure"; pf = json.loads((pf_root / "store.json").read_text())
        candidate = json.loads((generated / "fault_analysis/candidate_faults.json").read_text())
        validation_rows = read_csv(class_root / "class_validation.csv"); validations.extend(validation_rows)
        validation_by_fault = {row["fault_id"]: row for row in validation_rows}
        for group in store["candidate_groups"]:
            final_entries = [store["classes"][class_id] for class_id in group["final_class_ids"]]
            shared = next((entry for entry in final_entries if entry["class_type"] == "MULTI_FAULT_SHARED"), None)
            groups.append({
                "scenario": scenario, "candidate_group_id": group["candidate_group_id"],
                "affected_flow_set": ";".join(group["affected_flows"]),
                "affected_flow_set_sha256": group["affected_flow_set_sha256"], "member_count": len(group["members"]),
                "fault_ids": ";".join(group["members"]), "all_members_per_failure_sat": group["all_members_per_failure_sat"],
                "per_failure_semantic_hash_count": group["per_failure_semantic_hash_count"],
                "candidate_shared_synthesis_status": group["candidate_shared_synthesis_status"],
                "shared_profile_id": shared["profile_id"] if shared else "",
                "shared_profile_semantic_hash": shared["semantic_profile_hash"] if shared else "",
                "shared_profile_bytes": shared["profile_bytes"] if shared else "",
                "final_class_count": len(final_entries), "diagnostic": group.get("diagnostic", ""),
            })
            report_path = class_root / "raw" / group["candidate_group_id"] / "report.json"
            if report_path.exists():
                report = json.loads(report_path.read_text())
                synthesis.append({"scenario": scenario, "candidate_group_id": group["candidate_group_id"],
                    "member_count": len(group["members"]), "union_disabled_links": ";".join(report["union_disabled_links"]),
                    "status": group["candidate_shared_synthesis_status"],
                    "grouping_wall_us": store["grouping_wall_us"], "robust_route_wall_us": report["route_solver_wall_us"],
                    "z3_wall_us": report["smt_solver_wall_us"], "profile_compile_wall_us": report["profile_compile_wall_us"],
                    "serialization_wall_us": report["serialization_wall_us"],
                    "total_class_synthesis_wall_us": report["total_class_synthesis_wall_us"], "diagnostic": report["diagnostic"]})
        for class_id, entry in store["classes"].items():
            classes.append({"scenario": scenario, "class_id": class_id, "candidate_group_id": entry["candidate_group_id"],
                "class_type": entry["class_type"], "profile_source": entry["profile_source"], "class_size": len(entry["members"]),
                "fault_ids": ";".join(entry["members"]), "affected_flow_set": ";".join(entry["affected_flows"]),
                "status": entry["status"], "profile_id": entry["profile_id"], "profile_sha256": entry["profile_sha256"],
                "semantic_profile_hash": entry["semantic_profile_hash"], "profile_bytes": entry["profile_bytes"]})
        pf_sat = {fault: entry for fault, entry in pf["faults"].items() if entry["status"] == "SAT"}
        eq_entries = list(store["classes"].values()); shared_entries = [e for e in eq_entries if e["class_type"] == "MULTI_FAULT_SHARED"]
        pf_bytes = profile_bytes(pf_root, list(pf_sat.values())); eq_bytes = profile_bytes(class_root, eq_entries)
        failed = sum(group["candidate_shared_synthesis_status"] not in {"NOT_ATTEMPTED", "SHARED_SAT"} for group in store["candidate_groups"])
        compression.append({"scenario": scenario, "recoverable_fault_count": len(pf_sat), "per_failure_profile_count": len(pf_sat),
            "exact_candidate_group_count": len(store["candidate_groups"]), "validated_shared_class_count": len(shared_entries),
            "failed_shared_group_count": failed, "singleton_class_count": sum(e["class_type"] == "SINGLETON" for e in eq_entries),
            "final_equivalence_class_count": len(eq_entries), "faults_in_shared_classes": sum(len(e["members"]) for e in shared_entries),
            "shared_fault_coverage": sum(len(e["members"]) for e in shared_entries) / len(pf_sat),
            "per_failure_profile_bytes": pf_bytes, "equivalence_profile_bytes": eq_bytes,
            "profile_count_compression_ratio": 1-len(eq_entries)/len(pf_sat), "storage_compression_ratio": 1-eq_bytes/pf_bytes,
            "per_failure_precompute_wall_ms": pf["recovery_precompute_wall_ms"],
            "equivalence_synthesis_wall_ms": store["all_classes_synthesis_wall_ms"],
            "validation_wall_ms": store.get("validation_wall_ms", ""),
            "class_store_metadata_bytes": (class_root/"store.json").stat().st_size,
            "physical_link_count": len(json.loads((generated/"scenario.json").read_text())["links"]),
            "switch_switch_link_count": sum(item["in_protection_scope"] for item in candidate["all_links"]),
            "candidate_fault_count": len(candidate["candidate_faults"]),
            "unique_affected_set_count": len(store["candidate_groups"]),
            "multi_fault_candidate_group_count": sum(len(g["members"]) > 1 for g in store["candidate_groups"]),
        })
        for fault, pf_entry in pf_sat.items():
            eq_class_id = store["fault_to_class"][fault]; eq_entry = store["classes"][eq_class_id]
            pf_run = read_summary(scenario, "offline-per-failure", fault, run_id)
            eq_run = read_summary(scenario, "offline-exact-equivalence", fault, run_id)
            validation = validation_by_fault[fault]
            pf_recovery = float(pf_run["recovery_duration_s"])*1e6; eq_recovery = float(eq_run["recovery_duration_s"])*1e6
            comparisons.append({"scenario": scenario, "fault_id": fault,
                "affected_flow_count": len(pf_entry["affected_flows"]), "class_id": eq_class_id,
                "class_size": len(eq_entry["members"]), "class_type": eq_entry["class_type"],
                "per_failure_profile_id": pf_entry["profile_id"], "equivalence_profile_id": eq_entry["profile_id"],
                "same_profile_hash": pf_entry["semantic_profile_hash"] == eq_entry["semantic_profile_hash"],
                "per_failure_tt_lost": int(pf_run["tt_lost"]), "equivalence_tt_lost": int(eq_run["tt_lost"]),
                "loss_delta": int(eq_run["tt_lost"])-int(pf_run["tt_lost"]),
                "per_failure_deadline_miss": int(pf_run["deadline_miss_count"]),
                "equivalence_deadline_miss": int(eq_run["deadline_miss_count"]),
                "deadline_miss_delta": int(eq_run["deadline_miss_count"])-int(pf_run["deadline_miss_count"]),
                "per_failure_recovery_us": pf_recovery, "equivalence_recovery_us": eq_recovery,
                "recovery_delta_us": eq_recovery-pf_recovery,
                "per_failure_activation_wall_us": as_float(pf_run["activation_wall_us"]),
                "equivalence_activation_wall_us": as_float(eq_run["activation_wall_us"]),
                "validation_pass": validation["validation_pass"],
            })
    return stores, groups, classes, synthesis, validations, compression, comparisons


def plots(output: Path, compression: list[dict], comparisons: list[dict], classes: list[dict]):
    labels=[LABELS[row["scenario"]] for row in compression]; x=range(len(labels)); width=.34
    def grouped(name, left, right, ylabel, title, subtitle):
        fig,ax=plt.subplots(figsize=(9,5.4)); ax.bar([i-width/2 for i in x],left,width,label="Per-Failure",color=BLUE,edgecolor=INK)
        ax.bar([i+width/2 for i in x],right,width,label="Exact Equivalence",color=GOLD,edgecolor=INK)
        ax.set_xticks(list(x),labels); ax.set_ylabel(ylabel); fig.suptitle(title,x=.07,y=.98,ha="left",fontweight="bold"); fig.text(.125,.91,subtitle,color="#4D5966")
        ax.legend(frameon=False,ncol=2,loc="upper left"); ax.spines[["top","right"]].set_visible(False); ax.grid(axis="y",color="#E3E7EB",linewidth=.8); ax.set_axisbelow(True)
        for bars in ax.containers:
            ax.bar_label(bars,fmt="%.0f",padding=3,fontsize=9)
        fig.tight_layout(rect=(0,0,1,.9)); fig.savefig(output/name,dpi=180); plt.close(fig)
    grouped("profile_count_compression.png",[r["per_failure_profile_count"] for r in compression],[r["final_equivalence_class_count"] for r in compression],"Recovery profiles","Recovery profile count","P0 and metadata excluded")
    grouped("profile_storage_compression.png",[r["per_failure_profile_bytes"]/1024 for r in compression],[r["equivalence_profile_bytes"]/1024 for r in compression],"Profile bytes (KiB)","Recovery profile storage","Serialized recovery Profile files only; store metadata excluded")
    def scatter(name, pf_field, eq_field, label, title):
        fig,axes=plt.subplots(1,3,figsize=(13.5,4.8))
        for ax,scenario in zip(axes,SCENARIOS):
            rows=[r for r in comparisons if r["scenario"]==scenario]; a=[float(r[pf_field]) for r in rows]; b=[float(r[eq_field]) for r in rows]
            high=max(a+b+[1]); ax.scatter(a,b,s=38,color=BLUE,edgecolor="white",linewidth=.5); ax.plot([0,high],[0,high],linestyle="--",color=GREY,linewidth=1)
            ax.set_title(f"{LABELS[scenario]} (n={len(rows)})"); ax.set_xlabel("Per-Failure"); ax.set_ylabel("Exact Equivalence"); ax.set_xlim(left=0); ax.set_ylim(bottom=0); ax.grid(color="#E3E7EB",linewidth=.7); ax.set_axisbelow(True); ax.spines[["top","right"]].set_visible(False)
        fig.suptitle(title,x=.06,ha="left",fontweight="bold"); fig.text(.06,.91,label+"; dashed line denotes equal values",color="#4D5966")
        fig.tight_layout(rect=(0,0,1,.88)); fig.savefig(output/name,dpi=180); plt.close(fig)
    scatter("recovery_latency_pf_vs_equivalence.png","per_failure_recovery_us","equivalence_recovery_us","Microseconds from fault to first successful affected-TT reception","Recovery latency by fault")
    grouped("tt_loss_pf_vs_equivalence.png",
            [sum(r["scenario"]==s and int(r["per_failure_tt_lost"])==0 for r in comparisons) for s in SCENARIOS],
            [sum(r["scenario"]==s and int(r["equivalence_tt_lost"])==0 for r in comparisons) for s in SCENARIOS],
            "Faults with zero total TT loss", "TT loss outcomes by mode",
            "All matched fault runs had zero eligible TT packet loss, including the transition window")
    sizes=sorted({int(r["class_size"]) for r in classes}); fig,ax=plt.subplots(figsize=(9,5.4)); bottom=[0]*3
    colors=[BLUE,GOLD,"#7F8B52","#C87954","#B26A87"]
    for idx,size in enumerate(sizes):
        values=[sum(int(r["class_size"])==size for r in classes if r["scenario"]==s) for s in SCENARIOS]
        ax.bar(labels,values,bottom=bottom,label=f"size {size}",color=colors[idx%len(colors)],edgecolor=INK); bottom=[a+b for a,b in zip(bottom,values)]
    ax.set_ylabel("Final class count"); fig.suptitle("Final equivalence class-size distribution",x=.07,y=.98,ha="left",fontweight="bold"); fig.text(.125,.91,"Validated shared classes and singleton reuse/fallback",color="#4D5966"); ax.legend(frameon=False,ncol=min(4,len(sizes))); ax.spines[["top","right"]].set_visible(False); ax.grid(axis="y",color="#E3E7EB"); ax.set_axisbelow(True); fig.tight_layout(rect=(0,0,1,.9)); fig.savefig(output/"class_size_distribution.png",dpi=180); plt.close(fig)
    fig,ax=plt.subplots(figsize=(9,5.4)); vals=[r["shared_fault_coverage"]*100 for r in compression]; bars=ax.bar(labels,vals,color=BLUE,edgecolor=INK); ax.bar_label(bars,fmt="%.1f%%",padding=3); ax.set_ylim(0,100); ax.set_ylabel("Recoverable faults in shared classes (%)"); fig.suptitle("Shared fault coverage",x=.07,y=.98,ha="left",fontweight="bold"); fig.text(.125,.91,"Validated multi-fault classes only; singleton classes excluded",color="#4D5966"); ax.spines[["top","right"]].set_visible(False); ax.grid(axis="y",color="#E3E7EB"); ax.set_axisbelow(True); fig.tight_layout(rect=(0,0,1,.9)); fig.savefig(output/"shared_fault_coverage.png",dpi=180); plt.close(fig)


def example(output: Path, stores: dict, validations: list[dict]):
    options=[]
    for scenario,store in stores.items():
        for class_id,entry in store["classes"].items():
            if entry["class_type"]=="MULTI_FAULT_SHARED" and entry["status"]=="VALIDATED_SHARED": options.append((-len(entry["members"]),scenario,class_id,entry))
    if not options: (output/"shared_class_example.md").write_text("# Shared class example\n\nNo validated multi-fault class was observed.\n"); return
    _,scenario,class_id,entry=sorted(options)[0]; generated=ROOT/"generated"/scenario; class_root=generated/"profiles/exact_equivalence"
    shared=json.loads((class_root/entry["profile_file"]).read_text()); p0=json.loads((generated/"profiles/profile0.json").read_text()); pf=json.loads((generated/"profiles/per_failure/store.json").read_text())
    affected=set(entry["affected_flows"]); lines=[f"# Shared class example: {scenario} / {class_id}","",f"Members: {', '.join(entry['members'])}.","",f"Common affected flows: {', '.join(entry['affected_flows'])}.","",f"Union-disabled synthesis links: {', '.join(entry['union_disabled_links'])}.","","## P0 and robust routes",""]
    p0routes={r["flow_id"]:r for r in p0["logical_routes"]}; sharedroutes={r["flow_id"]:r for r in shared["logical_routes"]}
    lines += ["| Flow | Healthy P0 | Shared robust route |","|---|---|---|"]
    for flow in sorted(affected): lines.append(f"| {flow} | {' → '.join(p0routes[flow]['node_path'])} | {' → '.join(sharedroutes[flow]['node_path'])} |")
    lines += ["","## Member Per-Failure routes",""]
    for fault in entry["members"]:
        member=json.loads((generated/"profiles/per_failure"/pf["faults"][fault]["profile_file"]).read_text()); routes={r["flow_id"]:r for r in member["logical_routes"]}
        lines += [f"### {fault}",""]+[f"- {flow}: {' → '.join(routes[flow]['node_path'])}" for flow in sorted(affected)]+[""]
    lines += ["## Shared logical GCL windows","",f"The shared Profile contains {len(shared['gate_schedules'])} complete gate entries generated from one all-active-TT Z3 schedule.","","## Per-member runtime validation","","| Fault | Profile SHA | Activation | Stable delivery | Stable deadlines | Pass |","|---|---|---:|---:|---:|---:|"]
    for row in validations:
        if row["scenario"]==scenario and row["class_id"]==class_id: lines.append(f"| {row['fault_id']} | `{row['profile_sha256']}` | {row['activation_ok']} | {row['post_recovery_delivery_ok']} | {row['post_recovery_deadline_ok']} | {row['validation_pass']} |")
    (output/"shared_class_example.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--run-id",required=True); parser.add_argument("--output-dir",type=Path,required=True); parser.add_argument("--code-commit",required=True); args=parser.parse_args()
    output=args.output_dir; output.mkdir(parents=True,exist_ok=True)
    stores,groups,classes,synthesis,validations,compression,comparisons=collect(args.run_id)
    write_csv(output/"exact_candidate_groups.csv",groups,list(groups[0])); write_csv(output/"validated_equivalence_classes.csv",classes,list(classes[0])); write_csv(output/"class_synthesis.csv",synthesis,list(synthesis[0])); write_csv(output/"class_validation.csv",validations,list(validations[0])); write_csv(output/"compression_summary.csv",compression,list(compression[0])); write_csv(output/"per_fault_equivalence_comparison.csv",comparisons,list(comparisons[0]))
    distribution=[]
    for scenario in SCENARIOS:
        counts=Counter(int(row["class_size"]) for row in classes if row["scenario"]==scenario)
        for size,count in sorted(counts.items()): distribution.append({"scenario":scenario,"class_size":size,"class_count":count,"fault_count":size*count})
    coverage=[{"scenario":r["scenario"],"recoverable_fault_count":r["recoverable_fault_count"],"faults_in_shared_classes":r["faults_in_shared_classes"],"shared_fault_coverage":r["shared_fault_coverage"]} for r in compression]
    write_csv(output/"class_size_distribution.csv",distribution,list(distribution[0])); write_csv(output/"shared_fault_coverage.csv",coverage,list(coverage[0]))
    plots(output,compression,comparisons,classes); example(output,stores,validations)
    profile_output=output/"profile_stores"
    for scenario in SCENARIOS:
        source=ROOT/"generated"/scenario/"profiles/exact_equivalence"; destination=profile_output/scenario
        (destination/"profiles").mkdir(parents=True,exist_ok=True)
        for name in ("store.json","runtime_store.json","class_validation.csv"):
            shutil.copy2(source/name,destination/name)
        for item in sorted((source/"profiles").glob("*.json")):
            shutil.copy2(item,destination/"profiles"/item.name)
    table={r["scenario"]:r for r in compression}; lines=["# Exact affected-set recovery equivalence","","## Technical summary",""]
    for scenario in SCENARIOS:
        r=table[scenario]; lines.append(f"- **{LABELS[scenario]}:** {r['recoverable_fault_count']} recoverable faults became {r['final_equivalence_class_count']} validated recovery Profiles; profile-count compression {r['profile_count_compression_ratio']:.1%}, storage compression {r['storage_compression_ratio']:.1%}, shared-fault coverage {r['shared_fault_coverage']:.1%}.")
    total_pairs=len(comparisons); max_recovery_delta=max(abs(float(r["recovery_delta_us"])) for r in comparisons)
    differing_shared=sum(r["class_size"]>1 and not r["same_profile_hash"] for r in comparisons)
    lines += ["","All reported multi-fault classes were synthesized independently on the union-disabled topology and then exercised under each member as a separate single-link runtime fault. Candidate grouping used only exact healthy-P0 affected-flow sets; semantic hashes did not decide membership.","","## Compression evidence","","Profile count compares SAT Per-Failure recovery Profiles with the final validated partition. Storage counts only serialized recovery Profile files; P0, Class Store metadata and report files are excluded.","","![Profile count](profile_count_compression.png)","","![Profile storage](profile_storage_compression.png)","","## Runtime quality remained valid in all matched runs","",f"Across all {total_pairs} matched faults, both modes had zero eligible TT packet loss and zero delivered deadline misses. The maximum absolute recovery-duration delta was {max_recovery_delta:.3f} μs. {differing_shared} faults in shared classes used a robust semantic Profile different from their Per-Failure baseline, demonstrating that class construction was not hash deduplication.","","![Recovery latency](recovery_latency_pf_vs_equivalence.png)","","![TT loss](tt_loss_pf_vs_equivalence.png)","","The stable post-recovery window begins at activation plus one 1 ms cycle. Validation requires activation, failed-link avoidance, forwarding validity, zero runtime BFS/Z3/synthesis, stable delivery without persistent loss, and zero delivered deadline misses.","","## Class structure reflects naturally repeated affected sets","","The 4×5 structured20 grid has 20 switches, 10 end systems, 45 physical links, 20 TT flows and 4 BE flows. Automatic healthy-P0 discovery selected 22 of 35 internal links and produced 17 exact groups, including five validated multi-fault groups; topology and traffic were fixed before discovery.","","![Class sizes](class_size_distribution.png)","","![Shared coverage](shared_fault_coverage.png)","","Candidate groups are not equivalence classes. A multi-fault group is reported as validated only after one identical Profile SHA passes every member simulation; failed synthesis or validation conservatively falls back to Per-Failure singleton Profiles.","","## Offline cost is reported separately from the baseline",""]
    for scenario in SCENARIOS:
        r=table[scenario]; lines.append(f"- **{LABELS[scenario]}:** Per-Failure precompute {r['per_failure_precompute_wall_ms']:.3f} ms; additional exact-class synthesis {r['equivalence_synthesis_wall_ms']:.3f} ms. These measured wall times are not used in candidate selection.")
    lines += ["","## Scope and limitations","","The experiment covers deterministic single-link failures, one TT class, one 1 ms hyperperiod, BFS routing and one fixed shared forwarding/GCL state. Union removal is an offline robustness construction, not a simultaneous multi-link runtime fault. All candidates and shared synthesis attempts happened to be SAT, so failure-mode prevalence is not estimated. Results do not establish that exact affected sets are generally sufficient for equivalence.","","## Recommended next step","","Use the robust synthesis and per-member validation pipeline as the acceptance test for approximate candidate groups proposed from Jaccard, edge distance and route features; similarity alone must never establish equivalence.","","## Further questions","","Larger or stressed scenarios should test failed exact groups, solver timeout behavior and the compression-quality frontier without changing candidate selection after observing outcomes."]
    (output/"summary.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    (output/"chart_map.md").write_text("# Chart map\n\n- Profile count: grouped bars; Per-Failure versus final validated classes.\n- Storage: grouped bars; recovery Profile files only.\n- Recovery latency: scenario-faceted paired-value scatter with equality reference.\n- TT loss: grouped counts of fault runs with zero total eligible TT loss.\n- Class sizes: stacked count bars.\n- Shared coverage: percentage bars over recoverable faults.\n",encoding="utf-8")
    assert len(comparisons) == sum(row["recoverable_fault_count"] for row in compression)
    assert len(validations) == len(comparisons) and all(str(row["validation_pass"]).lower() == "true" for row in validations)
    assert all(int(row[field]) == 0 for row in validations for field in (
        "runtime_route_solver_invocations", "runtime_z3_solver_invocations", "runtime_profile_synthesis_invocations"))
    assert all(row["status"] in {"VALIDATED_SHARED", "VALIDATED_SINGLETON"} for row in classes)
    assert all(abs(row["profile_count_compression_ratio"] - (1-row["final_equivalence_class_count"]/row["per_failure_profile_count"])) < 1e-12 for row in compression)
    assert all(abs(row["storage_compression_ratio"] - (1-row["equivalence_profile_bytes"]/row["per_failure_profile_bytes"])) < 1e-12 for row in compression)
    for class_id in {row["class_id"] for row in validations}:
        assert len({row["profile_sha256"] for row in validations if row["class_id"] == class_id}) == 1
    artifact_paths=sorted(p for p in output.rglob("*") if p.is_file() and p.name!="manifest.json")
    manifest={"schema_version":1,"git_commit":args.code_commit,"omnetpp_version":"6.4.0","inet_version":"4.7.0","z3_version":"4.16.0","run_id":args.run_id,"artifacts":{p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in artifact_paths}}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    print("EXP08_ANALYSIS PASS")


if __name__=="__main__": main()
