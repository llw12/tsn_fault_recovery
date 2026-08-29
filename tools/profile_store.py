"""Deterministic per-failure ProfileStore construction and validation."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import time
from copy import deepcopy
from pathlib import Path

PROFILE_SCHEMA_VERSION = 2
STORE_SCHEMA_VERSION = 1
STRATEGY = "per-failure"
MODEL_VERSION = "bfs-z3-joint-tas-v1"
VALID_STATUSES = {"SAT", "NO_AFFECTED_TT", "NO_ROUTE", "UNSAT", "FORWARDING_CONFLICT", "ERROR"}
ROOT = Path(__file__).resolve().parents[1]


class ProfileStoreError(RuntimeError):
    pass


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def solver_config_hash(scenario: dict, port_map: dict) -> str:
    payload = {
        "model_version": MODEL_VERSION,
        "simulation": {key: scenario["simulation"][key] for key in ("cycle_time_s", "time_quantum_s")},
        "scheduling": scenario["scheduling"],
        "tt_flows": scenario["tt_flows"],
        "topology": {"nodes": scenario["nodes"], "links": scenario["links"]},
        "port_map": port_map,
    }
    return sha256_bytes(canonical_bytes(payload))


def semantic_payload(profile: dict) -> dict:
    logical = sorted(({
        "flow_id": item["flow_id"], "node_path": item["node_path"], "link_path": item["link_path"]
    } for item in profile["logical_routes"]), key=lambda item: item["flow_id"])
    routes = sorted(({
        "flow_id": item["flow_id"], "switch": item["switch"], "destination": item["destination"],
        "interface": item["interface"], "logical_link": item["logical_link"]
    } for item in profile["routes"]), key=lambda item: (
        item["flow_id"], item["switch"], item["destination"], item["interface"], item["logical_link"]))
    gates = sorted(({
        "gate_path": item["gate_path"], "traffic_class": item["traffic_class"],
        "initially_open": item["initially_open"], "offset_s": item["offset_s"],
        "durations_s": item["durations_s"]
    } for item in profile["gate_schedules"]), key=lambda item: (item["gate_path"], item["traffic_class"]))
    return {"logical_routes": logical, "routes": routes, "gate_schedules": gates}


def semantic_profile_hash(profile: dict) -> str:
    return sha256_bytes(canonical_bytes(semantic_payload(profile)))


def profile_content_hash(profile: dict) -> str:
    value = deepcopy(profile)
    value.pop("profile_sha256", None)
    return sha256_bytes(canonical_bytes(value))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def _percentile(values: list[float], fraction: float) -> float:
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position); upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _profile_from_raw(raw: dict, scenario: dict, fault_id: str, row: dict) -> dict:
    profile = {
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": f"PF_{fault_id}",
        "strategy": STRATEGY,
        "scenario_sha256": scenario["scenario_sha256"],
        "fault_id": fault_id,
        "affected_flows": row["affected_flows"],
        "logical_routes": raw["logical_routes"],
        "routes": raw["routes"],
        "gate_schedules": raw["gate_schedules"],
        "schedule_status": "SAT",
        "schedule_objective": row["objective"],
        "route_solver_wall_us_precompute": row["route_solver_wall_us"],
        "smt_solver_wall_us_precompute": row["smt_solver_wall_us"],
        "profile_build_wall_us": row["profile_compile_wall_us"],
    }
    profile["semantic_profile_hash"] = semantic_profile_hash(profile)
    profile["profile_sha256"] = profile_content_hash(profile)
    return profile


def build_store(generated: Path) -> dict:
    build_start_ns = time.perf_counter_ns()
    scenario = json.loads((generated / "scenario.json").read_text(encoding="utf-8"))
    port_map = json.loads((generated / "port_map.json").read_text(encoding="utf-8"))
    root = generated / "profiles/per_failure"
    report = json.loads((root / "precompute_report.json").read_text(encoding="utf-8"))
    if report["scenario_sha256"] != scenario["scenario_sha256"]:
        raise ProfileStoreError("precompute report scenario hash does not match generated scenario")
    profile_dir = root / "profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    faults: dict[str, dict] = {}
    runtime_faults: dict[str, dict] = {}
    csv_rows = []
    for fault_id in scenario["fault_candidates"]:
        if fault_id not in report["faults"]:
            raise ProfileStoreError(f"precompute report is missing candidate fault {fault_id}")
        row = report["faults"][fault_id]
        status = row["status"]
        if status not in VALID_STATUSES:
            raise ProfileStoreError(f"invalid status {status!r} for fault {fault_id}")
        entry = {
            "status": status, "affected_flows": row["affected_flows"],
            "diagnostic": row.get("diagnostic", ""),
            "route_solver_wall_us": row["route_solver_wall_us"],
            "smt_solver_wall_us": row["smt_solver_wall_us"],
            "profile_compile_wall_us": row["profile_compile_wall_us"],
            "serialization_wall_us": row["serialization_wall_us"],
            "total_precompute_wall_us": row["total_precompute_wall_us"],
            "objective": row["objective"],
        }
        runtime_entry = {"status": status, "affected_flows": row["affected_flows"], "diagnostic": entry["diagnostic"]}
        profile_bytes = 0
        semantic_hash = ""
        if status == "SAT":
            raw_path = root / "raw" / row["profile_file_raw"]
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            profile = _profile_from_raw(raw, scenario, fault_id, row)
            profile_path = profile_dir / f"{fault_id}.json"
            serialization_start_ns = time.perf_counter_ns()
            write_json(profile_path, profile)
            final_serialization_us = (time.perf_counter_ns() - serialization_start_ns) / 1e3
            entry["serialization_wall_us"] += final_serialization_us
            entry["total_precompute_wall_us"] += final_serialization_us
            profile_bytes = profile_path.stat().st_size
            semantic_hash = profile["semantic_profile_hash"]
            entry.update({
                "profile_id": profile["profile_id"],
                "profile_file": f"profiles/{fault_id}.json",
                "profile_sha256": profile["profile_sha256"],
                "profile_file_sha256": file_sha256(profile_path),
                "semantic_profile_hash": semantic_hash,
                "profile_bytes": profile_bytes,
            })
            runtime_entry["profile"] = profile
        faults[fault_id] = entry
        runtime_faults[fault_id] = runtime_entry
        csv_rows.append({
            "scenario": scenario["scenario_name"], "fault_id": fault_id, "status": status,
            "affected_flow_count": len(row["affected_flows"]),
            "route_solver_wall_us": row["route_solver_wall_us"],
            "smt_solver_wall_us": row["smt_solver_wall_us"],
            "profile_compile_wall_us": row["profile_compile_wall_us"],
            "serialization_wall_us": entry["serialization_wall_us"],
            "total_precompute_wall_us": entry["total_precompute_wall_us"],
            "objective": row["objective"], "profile_bytes": profile_bytes,
            "semantic_profile_hash": semantic_hash, "diagnostic": row.get("diagnostic", ""),
        })
    config_hash = solver_config_hash(scenario, port_map)
    code_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                 text=True, stdout=subprocess.PIPE).stdout.strip()
    per_fault_us = [row["total_precompute_wall_us"] for row in csv_rows]
    store = {
        "schema_version": STORE_SCHEMA_VERSION,
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "scenario_name": scenario["scenario_name"],
        "scenario_sha256": scenario["scenario_sha256"],
        "port_map_sha256": file_sha256(generated / "port_map.json"),
        "strategy": STRATEGY,
        "solver_config_hash": config_hash,
        "model_version": MODEL_VERSION,
        "precompute_code_commit": code_commit,
        "recovery_precompute_wall_ms": report["total_precompute_wall_s"] * 1e3 + (time.perf_counter_ns() - build_start_ns) / 1e6,
        "precompute_per_fault_wall_us": {
            "mean": sum(per_fault_us) / len(per_fault_us), "p50": _percentile(per_fault_us, .5),
            "p95": _percentile(per_fault_us, .95), "max": max(per_fault_us),
        },
        "faults": faults,
    }
    write_json(root / "store.json", store)
    runtime_store = {
        "schema_version": STORE_SCHEMA_VERSION,
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "scenario_name": scenario["scenario_name"],
        "scenario_sha256": scenario["scenario_sha256"],
        "solver_config_hash": config_hash,
        "strategy": STRATEGY,
        "precompute_code_commit": code_commit,
        "faults": runtime_faults,
    }
    write_json(root / "runtime_store.json", runtime_store)
    fields = ["scenario", "fault_id", "status", "affected_flow_count", "route_solver_wall_us", "smt_solver_wall_us", "profile_compile_wall_us", "serialization_wall_us", "total_precompute_wall_us", "objective", "profile_bytes", "semantic_profile_hash", "diagnostic"]
    with (root / "precompute_per_fault.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(csv_rows)
    return validate_store(root / "store.json", scenario, port_map)


def validate_store(path: Path, scenario: dict, port_map: dict) -> dict:
    if not path.exists():
        raise ProfileStoreError(f"missing per-failure ProfileStore: {path}; run precompute_profiles.py first")
    store = json.loads(path.read_text(encoding="utf-8"))
    if store.get("schema_version") != STORE_SCHEMA_VERSION or store.get("profile_schema_version") != PROFILE_SCHEMA_VERSION:
        raise ProfileStoreError("unsupported ProfileStore/profile schema version")
    if store.get("strategy") != STRATEGY:
        raise ProfileStoreError("ProfileStore strategy is not per-failure")
    if store.get("scenario_sha256") != scenario["scenario_sha256"]:
        raise ProfileStoreError("stale ProfileStore scenario_sha256")
    if store.get("port_map_sha256") != file_sha256(path.parents[2] / "port_map.json"):
        raise ProfileStoreError("stale ProfileStore port_map_sha256")
    expected_config = solver_config_hash(scenario, port_map)
    if store.get("solver_config_hash") != expected_config:
        raise ProfileStoreError("stale ProfileStore solver_config_hash")
    if set(store.get("faults", {})) != set(scenario["fault_candidates"]):
        raise ProfileStoreError("ProfileStore candidate faults/order do not match scenario")
    for fault_id, entry in store["faults"].items():
        if entry["status"] not in VALID_STATUSES:
            raise ProfileStoreError(f"invalid status for fault {fault_id}")
        if entry["status"] != "SAT":
            continue
        profile_path = path.parent / entry["profile_file"]
        if not profile_path.exists() or file_sha256(profile_path) != entry["profile_file_sha256"]:
            raise ProfileStoreError(f"corrupted profile file hash for fault {fault_id}")
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        if profile.get("profile_schema_version") != PROFILE_SCHEMA_VERSION:
            raise ProfileStoreError(f"profile schema mismatch for fault {fault_id}")
        if profile.get("scenario_sha256") != scenario["scenario_sha256"] or profile.get("fault_id") != fault_id:
            raise ProfileStoreError(f"stale or misbound profile for fault {fault_id}")
        if profile_content_hash(profile) != profile.get("profile_sha256") or entry["profile_sha256"] != profile.get("profile_sha256"):
            raise ProfileStoreError(f"corrupted profile content hash for fault {fault_id}")
        if semantic_profile_hash(profile) != profile.get("semantic_profile_hash"):
            raise ProfileStoreError(f"corrupted semantic profile hash for fault {fault_id}")
    runtime_path = path.parent / "runtime_store.json"
    if not runtime_path.exists():
        raise ProfileStoreError(f"missing runtime ProfileStore: {runtime_path}; run precompute_profiles.py first")
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if runtime.get("scenario_sha256") != scenario["scenario_sha256"] or runtime.get("solver_config_hash") != expected_config:
        raise ProfileStoreError("stale runtime ProfileStore metadata")
    if set(runtime.get("faults", {})) != set(store["faults"]):
        raise ProfileStoreError("runtime ProfileStore candidate faults do not match store.json")
    for fault_id, entry in store["faults"].items():
        runtime_entry = runtime["faults"][fault_id]
        if runtime_entry.get("status") != entry["status"] or runtime_entry.get("affected_flows") != entry["affected_flows"]:
            raise ProfileStoreError(f"runtime ProfileStore entry mismatch for fault {fault_id}")
        if entry["status"] == "SAT":
            embedded = runtime_entry.get("profile", {})
            if profile_content_hash(embedded) != entry["profile_sha256"] or semantic_profile_hash(embedded) != entry["semantic_profile_hash"]:
                raise ProfileStoreError(f"corrupted runtime embedded profile for fault {fault_id}")
    return store


def store_metrics(path: Path, profile0_path: Path) -> dict:
    store = json.loads(path.read_text(encoding="utf-8"))
    profiles = [path.parent / entry["profile_file"] for entry in store["faults"].values() if entry["status"] == "SAT"]
    recovery_bytes = sum(item.stat().st_size for item in profiles)
    metadata_bytes = path.stat().st_size
    initial_bytes = profile0_path.stat().st_size
    statuses = [entry["status"] for entry in store["faults"].values()]
    return {
        "candidate_fault_count": len(statuses),
        "relevant_fault_count": sum(status != "NO_AFFECTED_TT" for status in statuses),
        "no_action_fault_count": statuses.count("NO_AFFECTED_TT"),
        "recoverable_fault_count": statuses.count("SAT"),
        "unrecoverable_fault_count": sum(status in {"NO_ROUTE", "UNSAT", "FORWARDING_CONFLICT", "ERROR"} for status in statuses),
        "initial_profile_count": 1,
        "recovery_profile_count": len(profiles),
        "total_profile_count": 1 + len(profiles),
        "initial_profile_storage_bytes": initial_bytes,
        "recovery_profile_storage_bytes": recovery_bytes,
        "profile_store_metadata_bytes": metadata_bytes,
        "total_profile_storage_bytes": initial_bytes + recovery_bytes + metadata_bytes,
        "recovery_precompute_wall_ms": store["recovery_precompute_wall_ms"],
    }
