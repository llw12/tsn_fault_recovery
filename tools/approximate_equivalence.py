"""Pre-fault approximate grouping and validation-guided class construction.

Similarity proposes candidate groups.  Only robust synthesis followed by every-member
single-fault validation may accept a multi-fault recovery class.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Callable

from tools.fault_dataset import edge_distance, jaccard, switch_distances
from tools.profile_store import (
    MODEL_VERSION, PROFILE_SCHEMA_VERSION, canonical_bytes, file_sha256,
    profile_content_hash, semantic_profile_hash, solver_config_hash, write_json,
)

STRATEGY = "approximate-equivalence"
SCHEMA_VERSION = 1
TIE_BREAK_VERSION = "complete-link-v1"
SPLIT_STRATEGY_VERSION = "merge-tree-recursive-v1"
SYNTHESIS_MODEL_VERSION = "union-disabled-bfs-z3-v1"
VALIDATION_MODEL_VERSION = "single-fault-stable-window-v1"


class ApproximateEquivalenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Policy:
    policy_id: str
    policy_type: str
    theta: float
    dmax: int | None = None

    def as_dict(self) -> dict:
        return {
            "policy_id": self.policy_id, "policy_type": self.policy_type,
            "theta": self.theta, "dmax": self.dmax, "complete_link": True,
            "tie_break_version": TIE_BREAK_VERSION,
            "split_strategy_version": SPLIT_STRATEGY_VERSION,
        }

    @property
    def hash(self) -> str:
        return hashlib.sha256(canonical_bytes(self.as_dict())).hexdigest()


def policy_grid() -> list[Policy]:
    result = [Policy(f"J{int(theta * 100):03d}", "JACCARD", theta)
              for theta in (1.0, .8, .6, .4, .2)]
    result.extend(Policy(f"JE{int(theta * 100):03d}_D{distance}", "JACCARD_EDGE", theta, distance)
                  for theta in (.8, .6, .4) for distance in (0, 1, 2))
    return result


def build_pre_fault_features(model, candidate_artifact: dict) -> dict:
    """Build the grouping input without reading any recovery artifact."""
    candidates = sorted(candidate_artifact["candidate_faults"], key=lambda item: item["fault_id"])
    links = {link.id: link for link in model.links}
    distances = switch_distances(model)
    pairs = []
    for left, right in combinations(candidates, 2):
        left_id, right_id = left["fault_id"], right["fault_id"]
        left_link, right_link = links[left_id], links[right_id]
        left_set, right_set = set(left["affected_flows"]), set(right["affected_flows"])
        pairs.append({
            "fault_i": left_id, "fault_j": right_id,
            "affected_flow_jaccard": jaccard(left_set, right_set),
            "fault_edge_distance": edge_distance(
                (left_link.endpoint_a, left_link.endpoint_b),
                (right_link.endpoint_a, right_link.endpoint_b), distances),
        })
    payload = {
        "schema_version": 1, "pre_fault_only": True,
        "scenario_name": candidate_artifact["scenario_name"],
        "scenario_sha256": candidate_artifact["scenario_sha256"],
        "candidate_set_sha256": candidate_artifact["candidate_set_sha256"],
        "faults": [{
            "fault_id": item["fault_id"], "affected_flows": sorted(item["affected_flows"]),
            "affected_flow_count": item["affected_flow_count"],
            "affected_load_bps": item["affected_load_bps"],
        } for item in candidates],
        "pairs": pairs,
    }
    payload["feature_sha256"] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return payload


def _pair_map(features: dict) -> dict[tuple[str, str], tuple[float, int]]:
    return {(row["fault_i"], row["fault_j"]):
            (float(row["affected_flow_jaccard"]), int(row["fault_edge_distance"]))
            for row in features["pairs"]}


def pair_value(pair_map: dict, left: str, right: str) -> tuple[float, int]:
    if left == right:
        return 1.0, 0
    return pair_map[tuple(sorted((left, right)))]


def cluster_metrics(members: list[str], pair_map: dict) -> dict:
    values = [pair_value(pair_map, left, right) for left, right in combinations(sorted(members), 2)]
    if not values:
        return {"min_pairwise_jaccard": 1.0, "mean_pairwise_jaccard": 1.0, "max_edge_distance": 0}
    return {
        "min_pairwise_jaccard": min(value[0] for value in values),
        "mean_pairwise_jaccard": sum(value[0] for value in values) / len(values),
        "max_edge_distance": max(value[1] for value in values),
    }


def complete_link_ok(members: list[str], pair_map: dict, policy: Policy) -> bool:
    metrics = cluster_metrics(members, pair_map)
    return (metrics["min_pairwise_jaccard"] + 1e-12 >= policy.theta and
            (policy.dmax is None or metrics["max_edge_distance"] <= policy.dmax))


def _leaf(fault_id: str) -> dict:
    return {"node_id": f"F:{fault_id}", "members": [fault_id], "leaf_fault": fault_id}


def agglomerate(features: dict, policy: Policy) -> tuple[dict, list[dict]]:
    """Deterministic complete-link agglomeration with an explicit merge tree."""
    pair_map = _pair_map(features)
    clusters = [_leaf(row["fault_id"]) for row in features["faults"]]
    trace, merge_index = [], 0
    while True:
        legal = []
        for left_index, right_index in combinations(range(len(clusters)), 2):
            left, right = clusters[left_index], clusters[right_index]
            members = sorted(left["members"] + right["members"])
            if not complete_link_ok(members, pair_map, policy):
                continue
            metrics = cluster_metrics(members, pair_map)
            priority = (-metrics["min_pairwise_jaccard"], metrics["max_edge_distance"],
                        -len(members), tuple(members))
            legal.append((priority, left_index, right_index, members, metrics))
        if not legal:
            break
        priority, left_index, right_index, members, metrics = min(legal)
        left, right = clusters[left_index], clusters[right_index]
        if tuple(left["members"]) > tuple(right["members"]):
            left, right = right, left
        merge_index += 1
        merged = {"node_id": f"M{merge_index:04d}", "members": members,
                  "left": deepcopy(left), "right": deepcopy(right), **metrics}
        trace.append({
            "step": merge_index, "left_cluster": ";".join(left["members"]),
            "right_cluster": ";".join(right["members"]), "merged_members": ";".join(members),
            **metrics, "merge_priority": json.dumps(priority, separators=(",", ":")),
        })
        clusters = [cluster for index, cluster in enumerate(clusters)
                    if index not in {left_index, right_index}] + [merged]
        clusters.sort(key=lambda cluster: tuple(cluster["members"]))
    groups = []
    for index, tree in enumerate(sorted(clusters, key=lambda item: tuple(item["members"])), 1):
        metrics = cluster_metrics(tree["members"], pair_map)
        groups.append({"group_id": f"G{index:04d}", "member_faults": tree["members"],
                       "member_count": len(tree["members"]), **metrics,
                       "merge_tree": tree, "predicted_profile_count": 1})
    artifact = {
        "schema_version": 1, "strategy": STRATEGY, "pre_fault_only": True,
        "scenario_name": features["scenario_name"], "scenario_sha256": features["scenario_sha256"],
        "candidate_set_sha256": features["candidate_set_sha256"],
        "feature_sha256": features["feature_sha256"], "policy": policy.as_dict(),
        "policy_hash": policy.hash, "groups": groups,
    }
    artifact["grouping_artifact_sha256"] = hashlib.sha256(canonical_bytes(artifact)).hexdigest()
    for row in trace:
        row["result_candidate_group_id"] = next(
            (group["group_id"] for group in groups if set(row["merged_members"].split(";")) <= set(group["member_faults"])), "")
    return artifact, trace


def prune_tree(tree: dict, eligible_faults: set[str]) -> dict | None:
    if "leaf_fault" in tree:
        return deepcopy(tree) if tree["leaf_fault"] in eligible_faults else None
    left, right = prune_tree(tree["left"], eligible_faults), prune_tree(tree["right"], eligible_faults)
    if left is None:
        return right
    if right is None:
        return left
    return {**{key: value for key, value in tree.items() if key not in {"left", "right", "members"}},
            "members": sorted(left["members"] + right["members"]), "left": left, "right": right}


def resolve_tree(tree: dict, attempt: Callable[[list[str], int], dict], depth: int = 0) -> tuple[list[dict], list[dict]]:
    """Accept a shared class or recursively split only along the fixed merge tree."""
    members = sorted(tree["members"])
    if len(members) == 1:
        return [{"class_type": "SINGLETON", "members": members, "split_depth": depth}], []
    outcome = attempt(members, depth)
    if outcome.get("status") == "SHARED_SAT" and outcome.get("validation_pass") is True:
        return [{"class_type": "SHARED", "members": members, "split_depth": depth, **outcome}], []
    rejected = [{"members": members, "split_depth": depth, **outcome,
                 "split_left": sorted(tree["left"]["members"]),
                 "split_right": sorted(tree["right"]["members"])}]
    left_classes, left_rejected = resolve_tree(tree["left"], attempt, depth + 1)
    right_classes, right_rejected = resolve_tree(tree["right"], attempt, depth + 1)
    return left_classes + right_classes, rejected + left_rejected + right_rejected


def synthesis_cache_key(scenario_sha256: str, members: list[str], solver_config_hash: str,
                        candidate_set_sha256: str) -> str:
    payload = {"scenario_sha256": scenario_sha256, "members": sorted(members),
               "solver_config_hash": solver_config_hash, "candidate_set_sha256": candidate_set_sha256,
               "synthesis_model_version": SYNTHESIS_MODEL_VERSION}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def validation_cache_key(scenario_sha256: str, profile_hash: str, fault_id: str,
                         runtime_config_hash: str) -> str:
    payload = {"scenario_sha256": scenario_sha256, "profile_hash": profile_hash,
               "fault_id": fault_id, "runtime_config_hash": runtime_config_hash,
               "validation_model_version": VALIDATION_MODEL_VERSION}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def write_cache(path: Path, key: str, kind: str, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"schema_version": 1, "cache_kind": kind, "cache_key": key, **payload}
    path.write_bytes(canonical_bytes(value))


def read_cache(path: Path, key: str, kind: str) -> dict | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if value.get("schema_version") != 1 or value.get("cache_kind") != kind or value.get("cache_key") != key:
        raise ApproximateEquivalenceError(f"stale or corrupt {kind} cache: {path}")
    return value


def observed_pareto(rows: list[dict]) -> list[dict]:
    eligible = [row for row in rows if float(row["deadline_miss_delta"]) == 0 and row.get("stable_validation_pass", True)]
    frontier = []
    for row in eligible:
        dominated = any(
            (float(other["realized_profile_compression_ratio"]) >= float(row["realized_profile_compression_ratio"]) and
             float(other["cold_synthesis_wall_ms"]) <= float(row["cold_synthesis_wall_ms"]) and
             float(other["max_recovery_delta_us"]) <= float(row["max_recovery_delta_us"]) and
             (float(other["realized_profile_compression_ratio"]) > float(row["realized_profile_compression_ratio"]) or
              float(other["cold_synthesis_wall_ms"]) < float(row["cold_synthesis_wall_ms"]) or
              float(other["max_recovery_delta_us"]) < float(row["max_recovery_delta_us"])))
            for other in eligible if other is not row)
        if not dominated:
            frontier.append(row)
    return sorted(frontier, key=lambda row: (row["scenario"], row["policy_id"]))


def artifact_sha256(path: Path) -> str:
    return file_sha256(path)


def make_shared_profile(raw: dict, scenario: dict, candidate: dict, members: list[str],
                        affected_flows: list[str], class_id: str) -> dict:
    profile = {
        "profile_schema_version": PROFILE_SCHEMA_VERSION, "profile_id": f"AP_{class_id}",
        "strategy": STRATEGY, "class_id": class_id, "class_member_faults": sorted(members),
        "affected_flows": sorted(affected_flows), "union_disabled_links": sorted(members),
        "scenario_sha256": scenario["scenario_sha256"],
        "candidate_set_sha256": candidate["candidate_set_sha256"],
        "logical_routes": raw["logical_routes"], "routes": raw["routes"],
        "gate_schedules": raw["gate_schedules"], "schedule_status": "SAT",
    }
    profile["semantic_profile_hash"] = semantic_profile_hash(profile)
    profile["profile_sha256"] = profile_content_hash(profile)
    return profile


def write_approx_store(generated: Path, policy: Policy, grouping_path: Path,
                       resolved_classes: list[dict], synthesis_attempts: list[dict],
                       rejected_groups: list[dict], solver_timeout_ms: int) -> dict:
    scenario = json.loads((generated / "scenario.json").read_text())
    port_map = json.loads((generated / "port_map.json").read_text())
    candidate = json.loads((generated / "fault_analysis/candidate_faults.json").read_text())
    grouping = json.loads(grouping_path.read_text())
    pf_path = generated / "profiles/per_failure/store.json"
    pf = json.loads(pf_path.read_text())
    root = generated / "profiles/approximate_equivalence" / policy.policy_id
    profiles = root / "profiles"; profiles.mkdir(parents=True, exist_ok=True)
    classes, fault_to_class = {}, {}
    for index, spec in enumerate(sorted(resolved_classes, key=lambda item: tuple(item["members"])), 1):
        class_id = f"C{index:04d}"; members = sorted(spec["members"])
        path = profiles / f"{class_id}.json"
        if spec["class_type"] == "SHARED":
            profile = None
            if policy.policy_id == "J100":
                exact_path = generated / "profiles/exact_equivalence/store.json"
                if exact_path.exists():
                    exact = json.loads(exact_path.read_text())
                    exact_entry = next((entry for entry in exact["classes"].values()
                                        if sorted(entry["members"]) == members), None)
                    if exact_entry is None:
                        raise ApproximateEquivalenceError("J100 shared class has no exp08 counterpart")
                    profile = json.loads((exact_path.parent / exact_entry["profile_file"]).read_text())
            if profile is None:
                profile = make_shared_profile(spec["raw_profile"], scenario, candidate, members,
                                              spec["affected_flows"], class_id)
            write_json(path, profile); profile_source = "ROBUST_SYNTHESIS"; status = "VALIDATED_SHARED"
        else:
            fault = members[0]; entry = pf["faults"][fault]
            profile = json.loads((pf_path.parent / entry["profile_file"]).read_text())
            write_json(path, profile); profile_source = "PER_FAILURE_REUSE"; status = "VALIDATED_SINGLETON"
        metrics = cluster_metrics(members, _pair_map(json.loads((generated / "fault_analysis/pre_fault_pairwise_features.json").read_text())))
        classes[class_id] = {
            "class_type": spec["class_type"], "members": members, "status": status,
            "profile_source": profile_source, "profile_id": profile["profile_id"],
            "profile_file": f"profiles/{class_id}.json", "profile_sha256": profile["profile_sha256"],
            "profile_file_sha256": file_sha256(path), "semantic_profile_hash": profile["semantic_profile_hash"],
            "profile_bytes": path.stat().st_size, "affected_flows": sorted(spec.get("affected_flows", pf["faults"][members[0]]["affected_flows"])),
            "union_disabled_links": members, "source_candidate_group": spec.get("source_candidate_group", ""),
            "split_depth": int(spec.get("split_depth", 0)), **metrics,
        }
        for fault in members:
            if fault in fault_to_class: raise ApproximateEquivalenceError(f"overlapping final classes at {fault}")
            fault_to_class[fault] = class_id
    sat_faults = sorted(fault for fault, entry in pf["faults"].items() if entry["status"] == "SAT")
    if sorted(fault_to_class) != sat_faults:
        raise ApproximateEquivalenceError("final partition does not cover every PF-SAT fault exactly once")
    store = {
        "schema_version": SCHEMA_VERSION, "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "strategy": STRATEGY, "policy_id": policy.policy_id, "policy_type": policy.policy_type,
        "theta": policy.theta, "dmax": policy.dmax, "policy": policy.as_dict(), "policy_hash": policy.hash,
        "scenario_name": scenario["scenario_name"], "scenario_sha256": scenario["scenario_sha256"],
        "candidate_set_sha256": candidate["candidate_set_sha256"],
        "solver_config_hash": solver_config_hash(scenario, port_map),
        "solver_timeout_ms": solver_timeout_ms, "per_failure_store_sha256": file_sha256(pf_path),
        "grouping_artifact_sha256": file_sha256(grouping_path),
        "grouping_semantic_sha256": grouping["grouping_artifact_sha256"],
        "model_version": MODEL_VERSION, "synthesis_model_version": SYNTHESIS_MODEL_VERSION,
        "validation_model_version": VALIDATION_MODEL_VERSION,
        "classes": classes, "fault_to_class": fault_to_class,
        "rejected_groups": rejected_groups, "synthesis_attempts": synthesis_attempts,
    }
    write_json(root / "store.json", store)
    runtime = {key: store[key] for key in (
        "schema_version", "profile_schema_version", "strategy", "policy_id", "policy_hash",
        "scenario_name", "scenario_sha256", "candidate_set_sha256", "solver_config_hash",
        "per_failure_store_sha256", "grouping_artifact_sha256", "model_version")}
    runtime["fault_to_class"] = fault_to_class
    runtime["classes"] = {class_id: {
        "members": entry["members"], "affected_flows": entry["affected_flows"],
        "class_type": entry["class_type"], "status": entry["status"],
        "profile": json.loads((root / entry["profile_file"]).read_text()),
    } for class_id, entry in classes.items()}
    write_json(root / "runtime_store.json", runtime)
    return validate_approx_store(root / "store.json", scenario, port_map, policy.policy_id)


def validate_approx_store(path: Path, scenario: dict, port_map: dict, policy_id: str) -> dict:
    if not path.exists(): raise ApproximateEquivalenceError(f"missing approximate Class Store: {path}")
    store = json.loads(path.read_text()); generated = path.parents[3]
    if store.get("schema_version") != SCHEMA_VERSION or store.get("profile_schema_version") != PROFILE_SCHEMA_VERSION:
        raise ApproximateEquivalenceError("unsupported approximate store/profile schema")
    if store.get("strategy") != STRATEGY or store.get("policy_id") != policy_id:
        raise ApproximateEquivalenceError("approximate Class Store strategy/policy mismatch")
    policy_value = store.get("policy", {})
    policy = Policy(policy_value.get("policy_id", ""), policy_value.get("policy_type", ""),
                    float(policy_value.get("theta", -1)), policy_value.get("dmax"))
    candidate = json.loads((generated / "fault_analysis/candidate_faults.json").read_text())
    pf_path = generated / "profiles/per_failure/store.json"
    grouping_path = generated / "profiles/approximate_equivalence" / policy_id / "candidate_groups.json"
    checks = {
        "scenario_sha256": scenario["scenario_sha256"], "candidate_set_sha256": candidate["candidate_set_sha256"],
        "policy_hash": policy.hash, "grouping_artifact_sha256": file_sha256(grouping_path),
        "solver_config_hash": solver_config_hash(scenario, port_map),
        "per_failure_store_sha256": file_sha256(pf_path),
    }
    for field, expected in checks.items():
        if store.get(field) != expected: raise ApproximateEquivalenceError(f"stale approximate Class Store {field}")
    mapped = []
    for class_id, entry in store.get("classes", {}).items():
        mapped.extend(entry["members"]); profile_path = path.parent / entry["profile_file"]
        if file_sha256(profile_path) != entry["profile_file_sha256"]:
            raise ApproximateEquivalenceError(f"corrupt approximate profile file {class_id}")
        profile = json.loads(profile_path.read_text())
        if profile_content_hash(profile) != entry["profile_sha256"] or semantic_profile_hash(profile) != entry["semantic_profile_hash"]:
            raise ApproximateEquivalenceError(f"corrupt approximate profile content {class_id}")
        for fault in entry["members"]:
            if store["fault_to_class"].get(fault) != class_id: raise ApproximateEquivalenceError(f"fault mapping mismatch {fault}")
    if sorted(mapped) != sorted(store.get("fault_to_class", {})) or len(mapped) != len(set(mapped)):
        raise ApproximateEquivalenceError("approximate class partition is incomplete or overlapping")
    runtime = json.loads((path.parent / "runtime_store.json").read_text())
    for field in ("strategy", "policy_id", "policy_hash", "scenario_sha256", "candidate_set_sha256",
                  "solver_config_hash", "per_failure_store_sha256", "grouping_artifact_sha256"):
        if runtime.get(field) != store.get(field): raise ApproximateEquivalenceError(f"stale approximate runtime {field}")
    return store


def approximate_store_metrics(path: Path) -> dict:
    store = json.loads(path.read_text()); pf = json.loads((path.parents[2] / "per_failure/store.json").read_text())
    pf_entries = [entry for entry in pf["faults"].values() if entry["status"] == "SAT"]
    entries = list(store["classes"].values()); shared = [entry for entry in entries if entry["class_type"] == "SHARED"]
    pf_bytes = sum(int(entry["profile_bytes"]) for entry in pf_entries); result_bytes = sum(int(entry["profile_bytes"]) for entry in entries)
    initial_bytes = (path.parents[2] / "profile0.json").stat().st_size
    return {
        "recoverable_fault_count": len(pf_entries), "final_profile_count": len(entries),
        "candidate_fault_count": len(pf["faults"]),
        "relevant_fault_count": sum(bool(entry.get("affected_flows")) for entry in pf["faults"].values()),
        "no_action_fault_count": sum(entry["status"] == "NO_AFFECTED_TT" for entry in pf["faults"].values()),
        "unrecoverable_fault_count": sum(entry["status"] not in {"SAT", "NO_AFFECTED_TT"} for entry in pf["faults"].values()),
        "recovery_profile_count": len(entries), "total_profile_count": 1 + len(entries),
        "recovery_profile_storage_bytes": result_bytes,
        "profile_store_metadata_bytes": path.stat().st_size,
        "total_profile_storage_bytes": initial_bytes + result_bytes + path.stat().st_size,
        "realized_profile_compression_ratio": 1-len(entries)/len(pf_entries) if pf_entries else 0,
        "storage_compression_ratio": 1-result_bytes/pf_bytes if pf_bytes else 0,
        "per_failure_profile_bytes": pf_bytes, "approximate_profile_bytes": result_bytes,
        "shared_fault_coverage": sum(len(entry["members"]) for entry in shared)/len(pf_entries) if pf_entries else 0,
        "multi_fault_class_count": len(shared), "singleton_class_count": len(entries)-len(shared),
        "mean_class_size": len(pf_entries)/len(entries) if entries else 0,
        "max_class_size": max((len(entry["members"]) for entry in entries), default=0),
    }
