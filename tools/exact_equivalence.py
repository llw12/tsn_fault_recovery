"""Exact affected-set grouping, robust class profiles, and deterministic Class Store."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from tools.profile_store import (
    MODEL_VERSION, PROFILE_SCHEMA_VERSION, canonical_bytes, file_sha256,
    profile_content_hash, semantic_profile_hash, solver_config_hash, write_json,
)

CLASS_STORE_SCHEMA_VERSION = 1
STRATEGY = "exact-affected-set-equivalence"
SHARED_STATUSES = {
    "SHARED_SAT", "SHARED_NO_ROUTE", "SHARED_UNSAT",
    "SHARED_FORWARDING_CONFLICT", "VALIDATION_FAILED", "ERROR",
}
ROOT = Path(__file__).resolve().parents[1]


class ExactEquivalenceError(RuntimeError):
    pass


def affected_set_hash(flow_ids: list[str] | tuple[str, ...]) -> str:
    return hashlib.sha256(canonical_bytes({"affected_flows": sorted(flow_ids)})).hexdigest()


def build_candidate_groups(candidate_artifact: dict, per_failure_store: dict) -> list[dict]:
    """Group only by healthy-P0 affected sets; recovery data are annotations."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in candidate_artifact["candidate_faults"]:
        flows = tuple(sorted(item["affected_flows"]))
        buckets[affected_set_hash(flows)].append({"fault_id": item["fault_id"], "affected_flows": flows})
    groups = []
    for index, (key, items) in enumerate(sorted(buckets.items()), 1):
        faults = sorted(item["fault_id"] for item in items)
        flows = list(items[0]["affected_flows"])
        statuses = {fault: per_failure_store["faults"][fault]["status"] for fault in faults}
        sat_faults = [fault for fault in faults if statuses[fault] == "SAT"]
        hashes = sorted({per_failure_store["faults"][fault].get("semantic_profile_hash", "")
                         for fault in sat_faults if per_failure_store["faults"][fault].get("semantic_profile_hash")})
        groups.append({
            "candidate_group_id": f"G{index:04d}",
            "affected_flows": flows,
            "affected_flow_set_sha256": key,
            "members": faults,
            "member_statuses": statuses,
            "sat_members": sat_faults,
            "all_members_per_failure_sat": len(sat_faults) == len(faults),
            "per_failure_semantic_hash_count": len(hashes),
            "per_failure_semantic_hashes": hashes,
        })
    return groups


def _shared_status(status: str) -> str:
    return {
        "SAT": "SHARED_SAT", "NO_ROUTE": "SHARED_NO_ROUTE", "UNSAT": "SHARED_UNSAT",
        "FORWARDING_CONFLICT": "SHARED_FORWARDING_CONFLICT", "ERROR": "ERROR",
    }.get(status, "ERROR")


def synthesis_plan(groups: list[dict]) -> list[dict]:
    return [{
        "candidate_group_id": group["candidate_group_id"],
        "affected_flows": group["affected_flows"],
        "disabled_links": group["sat_members"],
    } for group in groups if len(group["sat_members"]) > 1]


def _shared_profile(raw: dict, scenario: dict, candidate_artifact: dict,
                    group: dict, class_id: str, report: dict) -> dict:
    profile = {
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": f"EQ_{class_id}",
        "strategy": STRATEGY,
        "class_id": class_id,
        "class_member_faults": group["sat_members"],
        "affected_flows": group["affected_flows"],
        "affected_flow_set_sha256": group["affected_flow_set_sha256"],
        "union_disabled_links": group["sat_members"],
        "scenario_sha256": scenario["scenario_sha256"],
        "candidate_set_sha256": candidate_artifact["candidate_set_sha256"],
        "logical_routes": raw["logical_routes"], "routes": raw["routes"],
        "gate_schedules": raw["gate_schedules"], "schedule_status": "SAT",
        "schedule_objective": report["objective"],
        "route_solver_wall_us_precompute": report["route_solver_wall_us"],
        "smt_solver_wall_us_precompute": report["smt_solver_wall_us"],
        "profile_build_wall_us": report["profile_compile_wall_us"],
        "synthesis_wall_us": report["total_class_synthesis_wall_us"],
    }
    profile["semantic_profile_hash"] = semantic_profile_hash(profile)
    profile["profile_sha256"] = profile_content_hash(profile)
    return profile


def validate_shared_routes(profile: dict, profile0: dict, affected_flows: list[str],
                           disabled_links: list[str]) -> None:
    routes = {item["flow_id"]: item for item in profile["logical_routes"]}
    initial = {item["flow_id"]: item for item in profile0["logical_routes"]}
    affected, disabled = set(affected_flows), set(disabled_links)
    if set(routes) != set(initial): raise ExactEquivalenceError("shared Profile route set is incomplete")
    for flow_id, route in routes.items():
        if disabled.intersection(route["link_path"]):
            raise ExactEquivalenceError(f"shared route for {flow_id} uses a class fault link")
        if flow_id not in affected and route != initial[flow_id]:
            raise ExactEquivalenceError(f"shared Profile reroutes unaffected flow {flow_id}")


def build_class_store(generated: Path, synthesis_reports: dict[str, dict] | None = None) -> dict:
    synthesis_reports = synthesis_reports or {}
    scenario = json.loads((generated / "scenario.json").read_text())
    port_map = json.loads((generated / "port_map.json").read_text())
    candidate_artifact = json.loads((generated / "fault_analysis/candidate_faults.json").read_text())
    pf_path = generated / "profiles/per_failure/store.json"
    pf_store = json.loads(pf_path.read_text())
    profile0 = json.loads((generated / "profiles/profile0.json").read_text())
    grouping_start_ns = time.perf_counter_ns()
    groups = build_candidate_groups(candidate_artifact, pf_store)
    grouping_wall_us = (time.perf_counter_ns() - grouping_start_ns) / 1e3
    root = generated / "profiles/exact_equivalence"
    profile_dir = root / "profiles"; profile_dir.mkdir(parents=True, exist_ok=True)
    classes, fault_to_class, group_results = {}, {}, []
    next_class = 1
    synthesis_total_us = 0.0
    for group in groups:
        sat = group["sat_members"]
        report = synthesis_reports.get(group["candidate_group_id"])
        shared_ok = len(sat) > 1 and report is not None and report.get("status") == "SAT"
        shared_status = "NOT_ATTEMPTED" if len(sat) <= 1 else (_shared_status(report.get("status", "ERROR")) if report else "ERROR")
        final_ids = []
        if shared_ok:
            class_id = f"C{next_class:04d}"; next_class += 1; final_ids.append(class_id)
            raw_path = root / "raw" / group["candidate_group_id"] / "profile.raw.json"
            raw_profile = json.loads(raw_path.read_text())
            validate_shared_routes(raw_profile, profile0, group["affected_flows"], group["sat_members"])
            profile = _shared_profile(raw_profile, scenario, candidate_artifact, group, class_id, report)
            profile_path = profile_dir / f"{class_id}.json"; write_json(profile_path, profile)
            entry = {
                "candidate_group_id": group["candidate_group_id"], "class_type": "MULTI_FAULT_SHARED",
                "profile_source": "ROBUST_SYNTHESIS", "members": sat, "affected_flows": group["affected_flows"],
                "affected_flow_set_sha256": group["affected_flow_set_sha256"], "union_disabled_links": sat,
                "status": "PENDING_RUNTIME_VALIDATION", "profile_id": profile["profile_id"],
                "profile_file": f"profiles/{class_id}.json", "profile_sha256": profile["profile_sha256"],
                "profile_file_sha256": file_sha256(profile_path), "semantic_profile_hash": profile["semantic_profile_hash"],
                "profile_bytes": profile_path.stat().st_size,
            }
            classes[class_id] = entry
            for fault in sat: fault_to_class[fault] = class_id
            synthesis_total_us += report["total_class_synthesis_wall_us"]
        else:
            for fault in sat:
                class_id = f"C{next_class:04d}"; next_class += 1; final_ids.append(class_id)
                pf_entry = pf_store["faults"][fault]
                source = pf_path.parent / pf_entry["profile_file"]
                profile_path = profile_dir / f"{class_id}.json"; shutil.copyfile(source, profile_path)
                classes[class_id] = {
                    "candidate_group_id": group["candidate_group_id"], "class_type": "SINGLETON",
                    "profile_source": "PER_FAILURE_REUSE", "members": [fault], "affected_flows": group["affected_flows"],
                    "affected_flow_set_sha256": group["affected_flow_set_sha256"], "union_disabled_links": [fault],
                    "status": "VALIDATED_SINGLETON", "profile_id": pf_entry["profile_id"],
                    "profile_file": f"profiles/{class_id}.json", "profile_sha256": pf_entry["profile_sha256"],
                    "profile_file_sha256": file_sha256(profile_path), "semantic_profile_hash": pf_entry["semantic_profile_hash"],
                    "profile_bytes": profile_path.stat().st_size,
                }
                fault_to_class[fault] = class_id
        group_results.append({**group, "candidate_shared_synthesis_status": shared_status,
                              "final_class_ids": final_ids,
                              "diagnostic": "" if report is None else report.get("diagnostic", "")})
    config_hash = solver_config_hash(scenario, port_map)
    code_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                 text=True, stdout=subprocess.PIPE).stdout.strip()
    store = {
        "schema_version": CLASS_STORE_SCHEMA_VERSION, "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "strategy": STRATEGY, "scenario_name": scenario["scenario_name"],
        "scenario_sha256": scenario["scenario_sha256"], "candidate_set_sha256": candidate_artifact["candidate_set_sha256"],
        "candidate_policy": candidate_artifact["policy"], "per_failure_store_sha256": file_sha256(pf_path),
        "solver_config_hash": config_hash, "model_version": MODEL_VERSION, "precompute_code_commit": code_commit,
        "grouping_wall_us": grouping_wall_us,
        "all_classes_synthesis_wall_ms": synthesis_total_us / 1e3,
        "candidate_groups": group_results, "classes": classes, "fault_to_class": fault_to_class,
    }
    write_json(root / "store.json", store)
    write_runtime_store(root, store)
    return validate_class_store(root / "store.json", scenario, port_map)


def write_runtime_store(root: Path, store: dict) -> None:
    runtime_classes = {}
    for class_id, entry in store["classes"].items():
        profile = json.loads((root / entry["profile_file"]).read_text())
        runtime_classes[class_id] = {
            "members": entry["members"], "affected_flows": entry["affected_flows"],
            "class_type": entry["class_type"], "status": entry["status"], "profile": profile,
        }
    runtime = {key: store[key] for key in (
        "schema_version", "profile_schema_version", "strategy", "scenario_name", "scenario_sha256",
        "candidate_set_sha256", "candidate_policy", "per_failure_store_sha256", "solver_config_hash",
        "model_version", "precompute_code_commit")}
    runtime.update({"classes": runtime_classes, "fault_to_class": store["fault_to_class"]})
    write_json(root / "runtime_store.json", runtime)


def validate_class_store(path: Path, scenario: dict, port_map: dict) -> dict:
    if not path.exists(): raise ExactEquivalenceError(f"missing exact-equivalence Class Store: {path}")
    store = json.loads(path.read_text())
    if store.get("schema_version") != CLASS_STORE_SCHEMA_VERSION or store.get("profile_schema_version") != PROFILE_SCHEMA_VERSION:
        raise ExactEquivalenceError("unsupported exact-equivalence store/profile schema version")
    if store.get("strategy") != STRATEGY: raise ExactEquivalenceError("Class Store strategy mismatch")
    generated = path.parents[2]
    candidate = json.loads((generated / "fault_analysis/candidate_faults.json").read_text())
    pf_path = generated / "profiles/per_failure/store.json"
    checks = {
        "scenario_sha256": scenario["scenario_sha256"],
        "candidate_set_sha256": candidate["candidate_set_sha256"],
        "per_failure_store_sha256": file_sha256(pf_path),
        "solver_config_hash": solver_config_hash(scenario, port_map),
    }
    for field, expected in checks.items():
        if store.get(field) != expected: raise ExactEquivalenceError(f"stale Class Store {field}")
    if store.get("candidate_policy") != candidate.get("policy"):
        raise ExactEquivalenceError("stale Class Store candidate_policy")
    mapped = []
    for class_id, entry in store.get("classes", {}).items():
        if entry["members"] != sorted(entry["members"]): raise ExactEquivalenceError(f"unstable member order for {class_id}")
        mapped.extend(entry["members"])
        profile_path = path.parent / entry["profile_file"]
        if file_sha256(profile_path) != entry["profile_file_sha256"]: raise ExactEquivalenceError(f"corrupted class profile file {class_id}")
        profile = json.loads(profile_path.read_text())
        if profile_content_hash(profile) != entry["profile_sha256"]: raise ExactEquivalenceError(f"corrupted class profile hash {class_id}")
        if semantic_profile_hash(profile) != entry["semantic_profile_hash"]: raise ExactEquivalenceError(f"corrupted class semantic hash {class_id}")
        for fault in entry["members"]:
            if store["fault_to_class"].get(fault) != class_id: raise ExactEquivalenceError(f"fault_to_class mismatch for {fault}")
    if sorted(mapped) != sorted(store["fault_to_class"]): raise ExactEquivalenceError("fault_to_class coverage mismatch")
    runtime_path = path.parent / "runtime_store.json"
    if not runtime_path.exists(): raise ExactEquivalenceError("missing exact-equivalence runtime store")
    runtime = json.loads(runtime_path.read_text())
    for field, expected in checks.items():
        if runtime.get(field) != expected: raise ExactEquivalenceError(f"stale runtime Class Store {field}")
    if runtime.get("fault_to_class") != store["fault_to_class"]: raise ExactEquivalenceError("runtime fault_to_class mismatch")
    for class_id, entry in store["classes"].items():
        profile = runtime.get("classes", {}).get(class_id, {}).get("profile", {})
        if profile_content_hash(profile) != entry["profile_sha256"]: raise ExactEquivalenceError(f"runtime class profile mismatch {class_id}")
    return store


def class_store_metrics(store_path: Path) -> dict:
    store = json.loads(store_path.read_text())
    pf = json.loads((store_path.parents[1] / "per_failure/store.json").read_text())
    pf_entries = [entry for entry in pf["faults"].values() if entry["status"] == "SAT"]
    eq_entries = list(store["classes"].values())
    pf_bytes = sum(entry["profile_bytes"] for entry in pf_entries)
    eq_bytes = sum(entry["profile_bytes"] for entry in eq_entries)
    shared = [entry for entry in eq_entries if entry["class_type"] == "MULTI_FAULT_SHARED"]
    initial_path = store_path.parents[1] / "profile0.json"
    return {
        "candidate_fault_count": len(pf["faults"]),
        "relevant_fault_count": sum(entry["status"] != "NO_AFFECTED_TT" for entry in pf["faults"].values()),
        "no_action_fault_count": sum(entry["status"] == "NO_AFFECTED_TT" for entry in pf["faults"].values()),
        "recoverable_fault_count": len(pf_entries), "per_failure_profile_count": len(pf_entries),
        "unrecoverable_fault_count": sum(entry["status"] not in {"SAT", "NO_AFFECTED_TT"} for entry in pf["faults"].values()),
        "exact_candidate_group_count": len(store["candidate_groups"]),
        "validated_shared_class_count": sum(entry["status"] == "VALIDATED_SHARED" for entry in shared),
        "pending_shared_class_count": sum(entry["status"] == "PENDING_RUNTIME_VALIDATION" for entry in shared),
        "failed_shared_group_count": sum(group["candidate_shared_synthesis_status"] not in {"NOT_ATTEMPTED", "SHARED_SAT"}
                                         for group in store["candidate_groups"]),
        "singleton_class_count": sum(entry["class_type"] == "SINGLETON" for entry in eq_entries),
        "final_equivalence_class_count": len(eq_entries),
        "faults_in_shared_classes": sum(len(entry["members"]) for entry in shared),
        "shared_fault_coverage": sum(len(entry["members"]) for entry in shared) / len(pf_entries) if pf_entries else None,
        "per_failure_profile_bytes": pf_bytes, "equivalence_profile_bytes": eq_bytes,
        "profile_count_compression_ratio": 1 - len(eq_entries) / len(pf_entries) if pf_entries else None,
        "storage_compression_ratio": 1 - eq_bytes / pf_bytes if pf_bytes else None,
        "class_store_metadata_bytes": store_path.stat().st_size,
        "recovery_profile_count": len(eq_entries), "initial_profile_count": 1,
        "total_profile_count": 1 + len(eq_entries),
        "initial_profile_storage_bytes": initial_path.stat().st_size,
        "recovery_profile_storage_bytes": eq_bytes,
        "profile_store_metadata_bytes": store_path.stat().st_size,
        "total_profile_storage_bytes": initial_path.stat().st_size + eq_bytes + store_path.stat().st_size,
        "recovery_precompute_wall_ms": store["all_classes_synthesis_wall_ms"],
    }


def finalize_class_store(store_path: Path, validation_rows: list[dict]) -> dict:
    """Promote shared classes only after every member passes single-fault simulation."""
    started = time.perf_counter_ns()
    store = json.loads(store_path.read_text())
    by_class: dict[str, list[dict]] = defaultdict(list)
    for row in validation_rows: by_class[row["class_id"]].append(row)
    root = store_path.parent
    pf_path = store_path.parents[1] / "per_failure/store.json"
    pf = json.loads(pf_path.read_text())
    failed = []
    for class_id, entry in list(store["classes"].items()):
        if entry["class_type"] != "MULTI_FAULT_SHARED":
            continue
        rows = by_class.get(class_id, [])
        profiles = {row.get("profile_sha256") for row in rows}
        valid = (sorted(row["fault_id"] for row in rows) == entry["members"] and
                 len(profiles) == 1 and profiles == {entry["profile_sha256"]} and
                 all(bool(row.get("validation_pass")) for row in rows))
        if valid:
            entry["status"] = "VALIDATED_SHARED"
        else:
            entry["status"] = "VALIDATION_FAILED"; failed.append(class_id)
    next_id = max((int(key[1:]) for key in store["classes"]), default=0) + 1
    for failed_id in failed:
        failed_entry = store["classes"].pop(failed_id)
        group = next(group for group in store["candidate_groups"]
                     if group["candidate_group_id"] == failed_entry["candidate_group_id"])
        group["candidate_shared_synthesis_status"] = "VALIDATION_FAILED"
        group["diagnostic"] = "one or more member single-fault runtime validations failed"
        group["failed_shared_profile_sha256"] = failed_entry["profile_sha256"]
        group["final_class_ids"] = []
        for fault in failed_entry["members"]:
            class_id = f"C{next_id:04d}"; next_id += 1
            pf_entry = pf["faults"][fault]
            profile_path = root / "profiles" / f"{class_id}.json"
            shutil.copyfile(pf_path.parent / pf_entry["profile_file"], profile_path)
            store["classes"][class_id] = {
                "candidate_group_id": group["candidate_group_id"], "class_type": "SINGLETON",
                "profile_source": "PER_FAILURE_REUSE", "members": [fault],
                "affected_flows": group["affected_flows"],
                "affected_flow_set_sha256": group["affected_flow_set_sha256"],
                "union_disabled_links": [fault], "status": "VALIDATED_SINGLETON",
                "profile_id": pf_entry["profile_id"], "profile_file": f"profiles/{class_id}.json",
                "profile_sha256": pf_entry["profile_sha256"], "profile_file_sha256": file_sha256(profile_path),
                "semantic_profile_hash": pf_entry["semantic_profile_hash"], "profile_bytes": profile_path.stat().st_size,
            }
            store["fault_to_class"][fault] = class_id; group["final_class_ids"].append(class_id)
    store["validation_wall_ms"] = (time.perf_counter_ns() - started) / 1e6
    write_json(store_path, store); write_runtime_store(root, store)
    scenario = json.loads((store_path.parents[2] / "scenario.json").read_text())
    port_map = json.loads((store_path.parents[2] / "port_map.json").read_text())
    return validate_class_store(store_path, scenario, port_map)
