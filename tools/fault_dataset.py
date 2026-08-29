"""Fault-equivalence dataset construction after per-failure precomputation."""

from __future__ import annotations

import csv
import json
from collections import defaultdict, deque
from itertools import combinations
from pathlib import Path

from tools.critical_link import affected_flow_set_hash
from tools.scenario_model import ScenarioModel


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def affected_flow_matrix(candidate_rows: list[dict], flow_ids: list[str]) -> list[dict]:
    rows = []
    for item in candidate_rows:
        affected = set(item["affected_flows"])
        rows.append({"fault_id": item["fault_id"], **{flow: int(flow in affected) for flow in flow_ids}})
    return rows


def jaccard_matrix(candidate_rows: list[dict]) -> tuple[list[str], list[list[float]]]:
    faults = [item["fault_id"] for item in candidate_rows]
    sets = [set(item["affected_flows"]) for item in candidate_rows]
    return faults, [[jaccard(left, right) for right in sets] for left in sets]


def switch_distances(model: ScenarioModel) -> dict[str, dict[str, int]]:
    switches = {node.id for node in model.nodes if node.type == "switch"}
    adjacency = {node: set() for node in switches}
    for link in model.links:
        if link.endpoint_a in switches and link.endpoint_b in switches:
            adjacency[link.endpoint_a].add(link.endpoint_b)
            adjacency[link.endpoint_b].add(link.endpoint_a)
    result = {}
    for source in sorted(switches):
        distance = {source: 0}; pending = deque([source])
        while pending:
            current = pending.popleft()
            for neighbor in sorted(adjacency[current]):
                if neighbor not in distance:
                    distance[neighbor] = distance[current] + 1; pending.append(neighbor)
        result[source] = distance
    return result


def edge_distance(endpoints_i: tuple[str, str], endpoints_j: tuple[str, str],
                  distances: dict[str, dict[str, int]]) -> int:
    values = [distances[left][right] for left in endpoints_i for right in endpoints_j
              if right in distances.get(left, {})]
    if not values:
        raise ValueError("fault edges are disconnected in the healthy switch graph")
    return min(values)


def recovery_route_union(profile: dict) -> set[str]:
    return {link for route in profile.get("logical_routes", []) for link in route["link_path"]}


def exact_affected_groups(candidate_rows: list[dict], store: dict) -> list[dict]:
    grouped: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for item in candidate_rows:
        grouped[tuple(item["affected_flows"])].append(item["fault_id"])
    result = []
    for affected, faults in sorted(grouped.items()):
        hashes = sorted({store["faults"][fault].get("semantic_profile_hash", "") for fault in faults
                         if store["faults"][fault].get("semantic_profile_hash")})
        result.append({
            "affected_flow_set": ";".join(affected),
            "affected_flow_set_sha256": affected_flow_set_hash(list(affected)),
            "fault_count": len(faults), "fault_ids": ";".join(sorted(faults)),
            "semantic_profile_hash_count": len(hashes), "semantic_profile_hashes": ";".join(hashes),
        })
    return result


def _primary_positions(profile0: dict) -> dict[str, dict[str, int]]:
    return {route["flow_id"]: {link: index for index, link in enumerate(route["link_path"])}
            for route in profile0["logical_routes"]}


def pairwise_rows(model: ScenarioModel, candidate_rows: list[dict], store: dict,
                  profile0: dict, profiles: dict[str, dict]) -> list[dict]:
    link_by_id = {link.id: link for link in model.links}
    distances = switch_distances(model)
    positions = _primary_positions(profile0)
    result = []
    for left, right in combinations(candidate_rows, 2):
        fault_i, fault_j = left["fault_id"], right["fault_id"]
        set_i, set_j = set(left["affected_flows"]), set(right["affected_flows"])
        entry_i, entry_j = store["faults"][fault_i], store["faults"][fault_j]
        both_sat = entry_i["status"] == entry_j["status"] == "SAT"
        common = sorted(set_i & set_j)
        position_difference = (sum(abs(positions[flow][fault_i] - positions[flow][fault_j]) for flow in common) / len(common)) if common else ""
        edge_i, edge_j = link_by_id[fault_i], link_by_id[fault_j]
        union_i = recovery_route_union(profiles[fault_i]) if both_sat else set()
        union_j = recovery_route_union(profiles[fault_j]) if both_sat else set()
        profile_hash_i = entry_i.get("semantic_profile_hash", "")
        profile_hash_j = entry_j.get("semantic_profile_hash", "")
        result.append({
            "fault_i": fault_i, "fault_j": fault_j,
            "jaccard": jaccard(set_i, set_j), "affected_set_jaccard": jaccard(set_i, set_j),
            "affected_count_i": len(set_i), "affected_count_j": len(set_j),
            "same_affected_set": int(set_i == set_j), "same_affected_count": int(len(set_i) == len(set_j)),
            "status_i": entry_i["status"], "status_j": entry_j["status"], "both_sat": int(both_sat),
            "same_semantic_profile": int(both_sat and profile_hash_i == profile_hash_j),
            "profile_hash_i": profile_hash_i, "profile_hash_j": profile_hash_j,
            "faults_share_endpoint": int(bool({edge_i.endpoint_a, edge_i.endpoint_b} & {edge_j.endpoint_a, edge_j.endpoint_b})),
            "fault_edge_distance": edge_distance((edge_i.endpoint_a, edge_i.endpoint_b),
                                                  (edge_j.endpoint_a, edge_j.endpoint_b), distances),
            "primary_path_position_difference_mean": position_difference,
            "recovery_route_link_jaccard": jaccard(union_i, union_j) if both_sat else "",
        })
    return result


def jaccard_bin(value: float) -> str:
    if value == 0: return "0"
    if value <= .25: return "(0,0.25]"
    if value <= .5: return "(0.25,0.5]"
    if value <= .75: return "(0.5,0.75]"
    if value < 1: return "(0.75,1)"
    return "1"


def profile_similarity_bins(rows: list[dict]) -> list[dict]:
    labels = ["0", "(0,0.25]", "(0.25,0.5]", "(0.5,0.75]", "(0.75,1)", "1"]
    result = []
    for label in labels:
        selected = [row for row in rows if jaccard_bin(float(row["jaccard"])) == label]
        both = [row for row in selected if row["both_sat"]]
        same = [row for row in both if row["same_semantic_profile"]]
        result.append({"jaccard_bin": label, "pair_count": len(selected),
                       "both_sat_count": len(both), "same_profile_count": len(same),
                       "same_profile_ratio": len(same) / len(both) if both else ""})
    return result


DATASET_FIELDS = [
    "scenario", "fault_id", "affected_flow_count", "affected_flow_ids", "affected_load_bps",
    "min_deadline_us", "mean_deadline_us", "max_deadline_us", "status", "rerouted_flow_count",
    "primary_route_links_affected", "recovery_route_link_union", "recovery_route_total_hops",
    "schedule_objective_ticks", "profile_semantic_hash", "profile_bytes", "route_solver_wall_us",
    "smt_solver_wall_us", "total_precompute_wall_us", "pre_fault_affected_flow_count",
    "pre_fault_affected_flow_ids", "pre_fault_affected_load_bps", "post_recovery_status",
    "post_recovery_rerouted_flow_count", "post_recovery_profile_semantic_hash",
]


def dataset_rows(model: ScenarioModel, analysis: dict, store: dict, profile0: dict,
                 profiles: dict[str, dict]) -> list[dict]:
    initial = {route["flow_id"]: route["link_path"] for route in profile0["logical_routes"]}
    all_link = {item["link_id"]: item for item in analysis["all_links"]}
    rows = []
    for candidate in analysis["candidate_faults"]:
        fault = candidate["fault_id"]; entry = store["faults"][fault]
        profile = profiles.get(fault)
        recovered = {route["flow_id"]: route["link_path"] for route in profile["logical_routes"]} if profile else {}
        rerouted = sum(initial[flow] != recovered.get(flow, initial[flow]) for flow in initial) if profile else ""
        union = sorted(recovery_route_union(profile)) if profile else []
        link = all_link[fault]
        common = {
            "scenario": model.scenario_name, "fault_id": fault,
            "affected_flow_count": candidate["affected_flow_count"],
            "affected_flow_ids": ";".join(candidate["affected_flows"]),
            "affected_load_bps": candidate["affected_load_bps"],
            "min_deadline_us": link["min_affected_deadline_us"],
            "mean_deadline_us": link["mean_affected_deadline_us"],
            "max_deadline_us": link["max_affected_deadline_us"],
            "status": entry["status"], "rerouted_flow_count": rerouted,
            "primary_route_links_affected": fault,
            "recovery_route_link_union": ";".join(union),
            "recovery_route_total_hops": sum(len(path) for path in recovered.values()) if profile else "",
            "schedule_objective_ticks": entry.get("objective", "") if profile else "",
            "profile_semantic_hash": entry.get("semantic_profile_hash", ""),
            "profile_bytes": entry.get("profile_bytes", ""),
            "route_solver_wall_us": entry["route_solver_wall_us"],
            "smt_solver_wall_us": entry["smt_solver_wall_us"],
            "total_precompute_wall_us": entry["total_precompute_wall_us"],
        }
        common.update({
            "pre_fault_affected_flow_count": common["affected_flow_count"],
            "pre_fault_affected_flow_ids": common["affected_flow_ids"],
            "pre_fault_affected_load_bps": common["affected_load_bps"],
            "post_recovery_status": common["status"],
            "post_recovery_rerouted_flow_count": common["rerouted_flow_count"],
            "post_recovery_profile_semantic_hash": common["profile_semantic_hash"],
        })
        rows.append(common)
    return rows


def _write_rows(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def build_fault_dataset(model: ScenarioModel, generated: Path) -> dict:
    output = generated / "fault_analysis"; output.mkdir(parents=True, exist_ok=True)
    analysis = json.loads((output / "candidate_faults.json").read_text())
    store = json.loads((generated / "profiles/per_failure/store.json").read_text())
    profile0 = json.loads((generated / "profiles/profile0.json").read_text())
    profiles = {}
    for fault, entry in store["faults"].items():
        if entry["status"] == "SAT":
            profiles[fault] = json.loads((generated / "profiles/per_failure" / entry["profile_file"]).read_text())
    candidates = analysis["candidate_faults"]
    rows = dataset_rows(model, analysis, store, profile0, profiles)
    _write_rows(output / "fault_equivalence_dataset.csv", rows, DATASET_FIELDS)
    flows = [flow.id for flow in model.tt_flows]
    _write_rows(output / "affected_flow_matrix.csv", affected_flow_matrix(candidates, flows), ["fault_id", *flows])
    faults, matrix = jaccard_matrix(candidates)
    _write_rows(output / "jaccard_similarity_matrix.csv",
                [{"fault_id": fault, **{other: matrix[i][j] for j, other in enumerate(faults)}} for i, fault in enumerate(faults)],
                ["fault_id", *faults])
    pairs = pairwise_rows(model, candidates, store, profile0, profiles)
    _write_rows(output / "pairwise_fault_similarity.csv", pairs)
    _write_rows(output / "profile_similarity_by_jaccard.csv", profile_similarity_bins(pairs))
    groups = exact_affected_groups(candidates, store)
    _write_rows(output / "exact_affected_set_groups.csv", groups)
    return {"dataset": rows, "pairs": pairs, "groups": groups, "analysis": analysis, "store": store}
