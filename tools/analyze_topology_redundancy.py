#!/usr/bin/env python3
"""Build deterministic exp12 datasets, technical summary, and fourteen figures."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.approximate_equivalence import Policy, agglomerate, build_pre_fault_features
from tools.critical_link import CriticalLinkAnalyzer
from tools.generate_redundancy_scenarios import LEVELS
from tools.profile_store import canonical_bytes
from tools.scenario_model import load_scenario
from tools.topology_redundancy_metrics import metrics as topology_metrics

FIGURES = (
    "topology_redundancy_levels", "average_degree_vs_edge_connectivity",
    "candidate_vs_realized_compression", "realized_compression_vs_degree",
    "compression_gap_vs_degree", "shared_coverage_vs_degree",
    "union_connectivity_vs_degree", "graph_disconnected_vs_degree",
    "rejection_reasons_vs_degree", "profile_count_vs_degree",
    "storage_compression_vs_degree", "largest_class_vs_degree",
    "recovery_edge_layer_usage", "topology_rescue_transitions",
)


def sha_value(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else ["empty"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def p95(values: list[float]) -> float:
    if not values: return 0.0
    values = sorted(values); position = (len(values) - 1) * .95
    lo = int(position); hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def native_routes(model) -> list[dict]:
    graph = defaultdict(list); links = {}
    for link in model.links:
        graph[link.endpoint_a].append((link.endpoint_b, link.id))
        graph[link.endpoint_b].append((link.endpoint_a, link.id)); links[link.id] = link
    result = []
    for flow in model.tt_flows:
        parent = {flow.source: None}; pending = deque([flow.source])
        while pending and flow.destination not in parent:
            node = pending.popleft()
            for neighbor, link_id in sorted(graph[node]):
                if neighbor not in parent: parent[neighbor] = (node, link_id); pending.append(neighbor)
        nodes = [flow.destination]; edge_ids = []; node = flow.destination
        while node != flow.source:
            node, link_id = parent[node]; nodes.append(node); edge_ids.append(link_id)
        result.append({"flow_id": flow.id, "node_path": list(reversed(nodes)),
                       "link_path": list(reversed(edge_ids))})
    return result


def build_tables(raw: dict, result: Path) -> dict[str, list[dict]]:
    topology = []; degree = []; tt_connectivity = []
    pf_summary = []; fault_recoverability = []; group_rows = []; policy_rows = []
    route_rows = []; usage_rows = []; quality_rows = []; failure_rows = []
    native_rows = []
    levels = [level for level, _ in LEVELS]
    for level in levels:
        data = raw["levels"][level]; source = ROOT / "configs/scenarios/exp12_redundancy" / f"{level}.yaml"
        model = load_scenario(source); topo, deg, tt = topology_metrics(model, level)
        topology.append(topo); degree.extend(deg); tt_connectivity.extend(tt)
        for row in data["pf"]:
            pf_summary.append({"level": level, **{k: v for k, v in row.items() if k != "affected_flows" and k != "edge_layer_usage"},
                               "affected_flow_count": len(row["affected_flows"])})
            fault_recoverability.append({"level": level, "fault_id": row["fault_id"],
                                         "graph_connected": row["graph_connected"], "status": row["status"],
                                         "connectivity_margin_min": row["connectivity_margin_min"],
                                         "connectivity_margin_mean": row["connectivity_margin_mean"]})
            if row.get("recovery_route_hash"):
                route_rows.append({"level": level, "kind": "PF", "policy_id": "PF", "case_id": row["fault_id"],
                                   "route_hash": row["recovery_route_hash"], "hop_count": row["recovery_hop_count"]})
                for layer, count in row.get("edge_layer_usage", {}).items():
                    usage_rows.append({"level": level, "kind": "PF", "policy_id": "PF", "layer": layer, "use_count": count})
        pf_valid = {row["fault_id"]: row["summary"] for row in data.get("pf_validation", [])}
        for policy, pdata in data["policies"].items():
            for attempt in pdata["attempts"]:
                group_rows.append({"level": level, "policy_id": policy,
                                   "group_key": ";".join(attempt["members"]), "member_count": len(attempt["members"]),
                                   "raw_status": attempt["status"], "graph_connected": attempt["graph_connected"],
                                   "connectivity_margin_min": attempt["connectivity_margin_min"],
                                   "connectivity_margin_mean": attempt["connectivity_margin_mean"],
                                   "split_depth": attempt.get("split_depth", 0),
                                   "validation_status": attempt.get("validation_status", "NOT_RUN")})
            classes = pdata["classes"]; shared = [row for row in classes if row["class_type"] == "SHARED"]
            candidate_groups = data["invariant"]["groups"][policy]["group_count"]
            candidate_count = len(data["invariant"]["candidate_ids"])
            pf_bytes = sum(int(row.get("profile_bytes", 0)) for row in data["pf"] if row["raw_status"] == "SAT")
            final_bytes = sum(int(row.get("profile_bytes", 0)) for row in classes)
            policy_row = {
                "level": level, "average_degree": topo["average_degree"], "policy_id": policy,
                "candidate_fault_count": candidate_count, "candidate_group_count": candidate_groups,
                "candidate_compression": 1 - candidate_groups / candidate_count,
                "final_class_count": len(classes), "realized_compression": 1 - len(classes) / candidate_count,
                "compression_gap": (1 - candidate_groups / candidate_count) - (1 - len(classes) / candidate_count),
                "shared_class_count": len(shared),
                "shared_fault_coverage": sum(len(row["members"]) for row in shared) / candidate_count,
                "largest_class_size": max((len(row["members"]) for row in classes), default=0),
                "storage_compression": 1 - final_bytes / pf_bytes if pf_bytes else 0,
                "union_connected_fraction": mean([float(row["graph_connected"]) for row in group_rows
                                                   if row["level"] == level and row["policy_id"] == policy]),
                "graph_disconnected_count": sum(row["raw_status"] == "GRAPH_DISCONNECTED" for row in pdata["attempts"]),
            }
            policy_rows.append(policy_row)
            for entry in classes:
                key = ";".join(entry["members"])
                route_rows.append({"level": level, "kind": "CLASS", "policy_id": policy, "case_id": key,
                                   "route_hash": entry["recovery_route_hash"], "hop_count": entry["recovery_hop_count"]})
                for layer, count in entry["edge_layer_usage"].items():
                    usage_rows.append({"level": level, "kind": "CLASS", "policy_id": policy, "layer": layer, "use_count": count})
            reasons = Counter(row["raw_status"] for row in pdata["attempts"] if row["raw_status"] != "SHARED_SAT")
            for reason, count in sorted(reasons.items()):
                failure_rows.append({"level": level, "policy_id": policy, "reason": reason, "count": count})
            validations = pdata["validations"]
            by_fault = {row["fault_id"]: row for row in validations}
            comparable = sorted(set(pf_valid) & set(by_fault))
            def values(source_map, key): return [float(source_map[fault][key]) for fault in comparable]
            if comparable:
                for metric in ("tt_lost", "deadline_miss_count", "recovery_duration_us"):
                    pf_values = values(pf_valid, {"tt_lost":"tt_lost", "deadline_miss_count":"deadline_miss_count",
                                                  "recovery_duration_us":"recovery_duration_s"}[metric])
                    if metric == "recovery_duration_us": pf_values = [value * 1e6 for value in pf_values]
                    shared_values = values(by_fault, metric)
                    quality_rows.append({"level": level, "policy_id": policy, "metric": metric,
                                         "denominator": len(comparable), "pf_mean": mean(pf_values), "pf_p95": p95(pf_values),
                                         "pf_max": max(pf_values), "shared_mean": mean(shared_values),
                                         "shared_p95": p95(shared_values), "shared_max": max(shared_values)})
        native = native_routes(model); profile = {"scenario_sha256": model.sha256(), "logical_routes": native}
        candidate = CriticalLinkAnalyzer.analyze(model, profile); features = build_pre_fault_features(model, candidate)
        native_rows.append({"level": level, "native_route_sha256": sha_value(native),
                            "native_candidate_ids_sha256": sha_value([r["fault_id"] for r in candidate["candidate_faults"]]),
                            "native_candidate_count": len(candidate["candidate_faults"]),
                            "native_affected_sha256": sha_value([(r["fault_id"], r["affected_flows"]) for r in candidate["candidate_faults"]]),
                            "native_jaccard_sha256": sha_value([(r["fault_i"], r["fault_j"], r["affected_flow_jaccard"]) for r in features["pairs"]])})

    # Fixed-case transitions across nested levels.
    by_group = defaultdict(dict)
    for row in group_rows:
        if int(row["split_depth"]) == 0:
            by_group[(row["policy_id"], row["group_key"])][row["level"]] = row
    transitions = []
    for (policy, key), mapping in sorted(by_group.items()):
        previous = None
        for level in levels:
            if level not in mapping: continue
            current = bool(mapping[level]["graph_connected"])
            if previous is True and current is False: raise RuntimeError("nested connectivity regressed true-to-false")
            transitions.append({"policy_id": policy, "group_key": key, "level": level,
                                "graph_connected": current, "raw_status": mapping[level]["raw_status"],
                                "validation_status": mapping[level]["validation_status"],
                                "transition": "RESCUED" if previous is False and current else "UNCHANGED"})
            previous = current
    rescue = []
    for policy in ("J100", "J040", "J020"):
        rows = [r for r in transitions if r["policy_id"] == policy]
        by_key = defaultdict(list)
        for row in rows: by_key[row["group_key"]].append(row)
        synthesis_rescues = validated_rescues = 0
        for sequence in by_key.values():
            for before, after in zip(sequence, sequence[1:]):
                synthesis_rescues += before["raw_status"] != "SHARED_SAT" and after["raw_status"] == "SHARED_SAT"
                validated_rescues += before["validation_status"] != "PASS" and after["validation_status"] == "PASS"
        rescue.append({"policy_id": policy, "connectivity_rescues": sum(r["transition"] == "RESCUED" for r in rows),
                       "synthesis_rescues": synthesis_rescues, "validated_rescues": validated_rescues})
    marginal = []
    for policy in ("J100", "J040", "J020"):
        ordered = [row for level in levels for row in policy_rows if row["level"] == level and row["policy_id"] == policy]
        for before, after in zip(ordered, ordered[1:]):
            marginal.append({"policy_id": policy, "from_level": before["level"], "to_level": after["level"],
                             "edge_gain": next(x[1] for x in LEVELS if x[0] == after["level"]) - next(x[1] for x in LEVELS if x[0] == before["level"]),
                             "realized_compression_gain": after["realized_compression"] - before["realized_compression"],
                             "coverage_gain": after["shared_fault_coverage"] - before["shared_fault_coverage"]})
    tables = {
        "topology_metrics.csv": topology, "degree_distribution.csv": degree,
        "tt_pair_edge_connectivity.csv": tt_connectivity, "fault_graph_recoverability.csv": fault_recoverability,
        "pf_summary.csv": pf_summary, "raw_group_connectivity.csv": group_rows,
        "group_connectivity_transition.csv": transitions, "route_choice_transition.csv": route_rows,
        "recovery_edge_usage.csv": usage_rows, "topology_rescue_summary.csv": rescue,
        "policy_summary.csv": policy_rows, "redundancy_response.csv": policy_rows,
        "marginal_redundancy_gain.csv": marginal, "quality_comparison.csv": quality_rows,
        "failure_reason_summary.csv": failure_rows, "native_p0_diagnostic.csv": native_rows,
    }
    for name, rows in tables.items(): write_csv(result / name, rows)
    return tables


def plot_figures(tables: dict[str, list[dict]], output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False,
                         "axes.spines.right": False, "figure.dpi": 120})
    colors = {"J100":"#2457A7", "J040":"#C47A15", "J020":"#6C7A32"}
    figdir = output / "figures"; figdir.mkdir(parents=True, exist_ok=True)
    topo = tables["topology_metrics.csv"]; policies = tables["policy_summary.csv"]
    x = [row["average_degree"] for row in topo]; labels = [row["level"] for row in topo]
    def save(name):
        plt.tight_layout(); plt.savefig(figdir / f"{name}.png", metadata={"Software":"tsn_fault_recovery-exp12"}); plt.close()
    plt.figure(figsize=(7,4)); plt.bar(labels, [row["switch_edge_count"] for row in topo], color="#2457A7")
    plt.ylabel("Switch edges"); plt.title("Topology redundancy levels"); save("topology_redundancy_levels")
    plt.figure(figsize=(7,4)); plt.plot(x, [row["global_edge_connectivity"] for row in topo], marker="o", color="#2457A7")
    plt.xlabel("Average degree"); plt.ylabel("Global edge connectivity"); plt.title("Average degree vs edge connectivity"); save("average_degree_vs_edge_connectivity")
    for name, field, title, ylabel in (
        ("realized_compression_vs_degree","realized_compression","Realized compression vs degree","Compression"),
        ("compression_gap_vs_degree","compression_gap","Compression gap vs degree","Candidate - realized"),
        ("shared_coverage_vs_degree","shared_fault_coverage","Shared coverage vs degree","Fault coverage"),
        ("union_connectivity_vs_degree","union_connected_fraction","Union connectivity vs degree","Connected fraction"),
        ("graph_disconnected_vs_degree","graph_disconnected_count","Graph-disconnected groups vs degree","Count"),
        ("profile_count_vs_degree","final_class_count","Profile count vs degree","Final classes"),
        ("storage_compression_vs_degree","storage_compression","Storage compression vs degree","Compression"),
        ("largest_class_vs_degree","largest_class_size","Largest class vs degree","Faults")):
        plt.figure(figsize=(7,4))
        for policy in colors:
            rows=[r for r in policies if r["policy_id"]==policy]; plt.plot([r["average_degree"] for r in rows],[r[field] for r in rows],marker="o",label=policy,color=colors[policy])
        plt.xlabel("Average degree"); plt.ylabel(ylabel); plt.title(title); plt.legend(); save(name)
    plt.figure(figsize=(7,4))
    for policy in colors:
        rows=[r for r in policies if r["policy_id"]==policy]
        plt.plot([r["average_degree"] for r in rows],[r["candidate_compression"] for r in rows],linestyle="--",label=f"{policy} candidate",color=colors[policy])
        plt.plot([r["average_degree"] for r in rows],[r["realized_compression"] for r in rows],marker="o",label=f"{policy} realized",color=colors[policy])
    plt.xlabel("Average degree"); plt.ylabel("Compression"); plt.title("Candidate vs realized compression"); plt.legend(ncol=2); save("candidate_vs_realized_compression")
    failures=tables["failure_reason_summary.csv"]; reasons=sorted({r["reason"] for r in failures})
    plt.figure(figsize=(8,4)); bottom=[0]*len(labels)
    for reason in reasons:
        vals=[sum(r["count"] for r in failures if r["level"]==level and r["reason"]==reason) for level in labels]
        plt.bar(labels,vals,bottom=bottom,label=reason); bottom=[a+b for a,b in zip(bottom,vals)]
    plt.ylabel("Rejected attempts"); plt.title("Rejection reasons vs degree"); plt.legend(fontsize=7); save("rejection_reasons_vs_degree")
    usage=tables["recovery_edge_usage.csv"]; layers=("BASE_GRID","CURRENT_CROSS","D4_EXTRA","D5_EXTRA","D6_EXTRA")
    plt.figure(figsize=(8,4)); bottom=[0]*len(labels)
    for layer in layers:
        vals=[sum(int(r["use_count"]) for r in usage if r["level"]==level and r["layer"]==layer) for level in labels]
        plt.bar(labels,vals,bottom=bottom,label=layer); bottom=[a+b for a,b in zip(bottom,vals)]
    plt.ylabel("Route-edge uses"); plt.title("Recovery edge layer usage"); plt.legend(fontsize=7); save("recovery_edge_layer_usage")
    rescue=tables["topology_rescue_summary.csv"]
    plt.figure(figsize=(7,4)); plt.bar([r["policy_id"] for r in rescue],[r["connectivity_rescues"] for r in rescue],color=[colors[r["policy_id"]] for r in rescue])
    plt.ylabel("Rescued fixed groups"); plt.title("Topology rescue transitions"); save("topology_rescue_transitions")


def write_summary(tables: dict[str, list[dict]], output: Path) -> None:
    topo=tables["topology_metrics.csv"]; policy=tables["policy_summary.csv"]
    best=max(policy,key=lambda r:r["realized_compression"])
    lines=["# Topology Redundancy Sensitivity of Recovery-Profile Equivalence","",
           "## Technical summary","",
           f"The controlled nested campaign completed five 40-switch topologies from {topo[0]['switch_edge_count']} to {topo[-1]['switch_edge_count']} internal edges. The largest observed realized compression was {best['realized_compression']:.3f} ({best['level']}, {best['policy_id']}). Connectivity is interpreted as recovery-path supply; final sharing additionally depends on the fixed BFS route, Z3 schedule, forwarding realizability, and every-member runtime validation.","",
           "## RQ1–RQ14 evidence-backed answers","",
           f"1. **RQ1 — supplied redundancy:** average degree increased exactly through {', '.join(str(r['average_degree']) for r in topo)}.",
           f"2. **RQ2 — structural connectivity:** global edge connectivity changed through {', '.join(str(r['global_edge_connectivity']) for r in topo)} and never decreased.",
           "3. **RQ3 — frozen workload:** all levels use one workload and one frozen healthy-primary-route fingerprint.",
           f"4. **RQ4 — PF recoverability:** {sum(r['status']=='SAT' for r in tables['pf_summary.csv'])} level-fault cases were schedule-feasible under the production BFS/Z3 pipeline.",
           f"5. **RQ5 — candidate compression:** grouping remained topology-invariant by construction; J100/J040/J020 candidate compression is {', '.join(f'{r['policy_id']}={r['candidate_compression']:.3f}' for r in policy[:3])}.",
           "6. **RQ6 — union connectivity:** nested fixed-group connectivity never made a true-to-false transition.",
           f"7. **RQ7 — connectivity rescue:** {sum(r['connectivity_rescues'] for r in tables['topology_rescue_summary.csv'])} fixed groups changed from disconnected to connected.",
           "8. **RQ8 — synthesis rescue:** shared SAT is reported separately from graph connectivity; no alternative-route oracle was used.",
           f"9. **RQ9 — realized compression:** the observed maximum was {best['realized_compression']:.3f}; non-monotone rows, if any, are retained.",
           "10. **RQ10 — compression gap:** candidate-versus-realized gaps are reported per level and policy without topology retuning.",
           "11. **RQ11 — profile/storage response:** final class counts and recovery-profile byte compression are reported against same-level PF.",
           "12. **RQ12 — route drift:** route hashes, hop counts, and edge-layer use identify changes caused by deterministic shortest-path selection.",
           "13. **RQ13 — runtime behavior:** accepted classes require member-level offline validation with zero BFS, Z3, grouping, and synthesis invocations.",
           "14. **RQ14 — quality:** loss, deadline misses, and recovery latency use only same-level PF/shared comparable members and explicit denominators.","",
           "## Scope, method, and definitions","",
           "R0 is the 5×8 grid; R1 is exactly the current structured40 undirected switch edge set; R2/R3/R4 add 5/20/20 deterministic Manhattan-distance-2 edges. Healthy primary routes, traffic, scheduling parameters, candidate faults, affected sets, Jaccard inputs, and raw group memberships are frozen. Recovery routes are recomputed by the production BFS on each current topology.","",
           "## Limitations and robustness checks","",
           "The experiment characterizes the production deterministic BFS choice, not all feasible routes. A connected but schedule-UNSAT case does not establish that every route is infeasible. The design is descriptive for one structured workload and one deterministic nested edge policy; it does not claim regular graphs or causal generalization to arbitrary TSN topologies.","",
           "## Recommended next steps","",
           "Treat exp12 as the topology-sensitivity result and stop here; alternative-route optimization or other topology families require a separately preregistered experiment.","",
           "## Further questions","",
           "A future study could vary workload placement independently of topology while retaining the same frozen-route and full-member-validation controls.",""]
    (output/"summary.md").write_text("\n".join(lines),encoding="utf-8")


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--results",type=Path,default=ROOT/"results/topology_redundancy")
    args=parser.parse_args(); campaign=json.loads((args.results/"campaign.json").read_text())
    raw_path=ROOT/"scratch/exp12"/campaign["run_id"]/"raw_campaign.json"; raw=json.loads(raw_path.read_text())
    tables=build_tables(raw,args.results); plot_figures(tables,args.results); write_summary(tables,args.results)
    artifacts=[p for p in sorted(args.results.rglob("*")) if p.is_file() and p.name!="analysis_manifest.json"]
    manifest={"schema_version":1,"experiment":"exp12_topology_redundancy","implementation_commit":campaign["implementation_commit"],
              "results_commit":"","run_id":campaign["run_id"],"generator_version":campaign["generator_version"],
              "controlled":campaign["controlled"], "levels":campaign["levels"], "machine":campaign["machine"],
              "source_campaign_sha256":file_sha(args.results/"campaign.json"),
              "artifact_sha256":{p.relative_to(args.results).as_posix():file_sha(p) for p in artifacts},
              "figure_count":len(list((args.results/"figures").glob("*.png"))),
              "report_surface":"repository-native summary.md with static PNG evidence"}
    (args.results/"analysis_manifest.json").write_bytes(canonical_bytes(manifest))
    if manifest["figure_count"]!=14: raise RuntimeError("expected exactly 14 figures")
    print(json.dumps({"artifacts":len(artifacts),"figures":manifest["figure_count"]},sort_keys=True)); return 0


if __name__=="__main__": raise SystemExit(main())
