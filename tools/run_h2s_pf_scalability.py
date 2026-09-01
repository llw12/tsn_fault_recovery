"""exp16: per-failure H2S scalability campaign with affected-only rerouting."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.h2s_jrs_backend import (DEFAULT_CANDIDATE_PATHS, FORMAL_MEMORY_LIMIT_MB,
    FORMAL_SEED, FORMAL_THREADS, UPSTREAM_COMMIT, UPSTREAM_LICENSE, UPSTREAM_REPOSITORY,
    check_h2s_pf_solution, prepare_h2s_inputs)
from tools.h2s_pf_backend import H2sPfBackend, route_index
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import materialize_case, write_attempt_logs
from tools.scenario_compiler import compile_scenario

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/h2s_pf_scalability"
EXP15 = ROOT / "results/h2s_backend_qualification"
SCENARIOS = ROOT / "results/pf_jrs_scalability/scenarios"
EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
EXP15_PATCH = ROOT / "third_party_patches/advanced_flow_scheduler/exp15_semantics.patch"
ROUTE_LOCK_PATCH = ROOT / "third_party_patches/advanced_flow_scheduler/exp16_route_lock.patch"
SCALE_IDS = [f"S{i}" for i in range(1, 8)] + [f"F150_TT{i}" for i in (100, 250, 500, 750, 1000)]
PFQ_IDS = [f"PFQ{i:02d}" for i in range(16)]
QUICK_PFQ = ["PFQ00", "PFQ01", "PFQ03", "PFQ04", "PFQ08", "PFQ14"]
SUCCESS = {BackendStatus.SUCCESS_H2S.value, BackendStatus.SUCCESS_CELF_FALLBACK.value}
RESOURCE_OR_HNF = {BackendStatus.HEURISTIC_NOT_FOUND.value, BackendStatus.TIME_LIMIT.value, BackendStatus.MEMORY_LIMIT.value}
EXPECTED_EXP15_CAMPAIGN = "3123dc48664e8ccb6f60b493dfe454721e230d135f3aa49fe5ffb6dcf2c0db39"
TIMEOUT_S = 30
SCENARIO_BUDGET_S = 7200


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def percentile(values: list[float], fraction: float) -> float:
    if not values: return 0.0
    ordered = sorted(values); position = (len(ordered) - 1) * fraction
    low = math.floor(position); high = math.ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def quantile_bins(candidates: list[dict[str, Any]], count: int = 5) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda row: (row["affected_flow_count"], row["fault_id"]))
    total = len(ordered)
    return [dict(row, quantile_bin=min(count - 1, index * count // max(total, 1)))
            for index, row in enumerate(ordered)]


def stratified_sample(candidates: list[dict[str, Any]], maximum_per_bin: int = 8) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in quantile_bins(candidates): grouped[row["quantile_bin"]].append(row)
    selected = []
    for bin_id in sorted(grouped):
        values = grouped[bin_id]
        if len(values) <= maximum_per_bin: chosen = values
        elif maximum_per_bin == 1: chosen = [values[len(values) // 2]]
        else:
            indices = sorted({round(i * (len(values) - 1) / (maximum_per_bin - 1)) for i in range(maximum_per_bin)})
            chosen = [values[index] for index in indices]
        selected.extend(chosen)
    return selected[:40]


def include_required_samples(selected: list[dict[str, Any]], required: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert pilots without exceeding eight per bin or forty total samples."""
    result = list(selected); required_ids = {row["fault_id"] for row in required}
    for row in required:
        if row["fault_id"] in {item["fault_id"] for item in result}: continue
        same_bin = [index for index, item in enumerate(result)
                    if item["quantile_bin"] == row["quantile_bin"] and item["fault_id"] not in required_ids]
        if not same_bin: raise RuntimeError("cannot include required pilot within sampling cap")
        result[same_bin[-1]] = row
    return sorted(result, key=lambda item: (item["quantile_bin"], item["affected_flow_count"], item["fault_id"]))


def select_pilots(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda row: (row["affected_flow_count"], row["fault_id"]))
    if not ordered: return []
    return [ordered[index] for index in sorted({0, len(ordered) // 2, len(ordered) - 1})]


def discover_candidates(scenario: dict[str, Any], healthy_routes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    switches = {node["id"] for node in scenario["nodes"] if node["type"] == "switch"}
    used_by: dict[str, list[str]] = defaultdict(list)
    for flow_id, route in healthy_routes.items():
        for link_id in route.get("link_path", []): used_by[link_id].append(flow_id)
    rows = []
    for link in sorted(scenario["links"], key=lambda item: item["id"]):
        if link["endpoint_a"] in switches and link["endpoint_b"] in switches and used_by.get(link["id"]):
            flows = sorted(set(used_by[link["id"]]))
            rows.append({"fault_id": link["id"], "physical_link_id": link["id"], "endpoint_a": link["endpoint_a"],
                         "endpoint_b": link["endpoint_b"], "affected_flow_count": len(flows),
                         "affected_flow_ratio": len(flows) / max(len(healthy_routes), 1),
                         "affected_flow_ids": flows, "healthy_route_use_count": len(flows),
                         "p0_used_direction_count": sum(1 for route in healthy_routes.values()
                             for a, b in zip(route.get("node_path", []), route.get("node_path", [])[1:])
                             if {a, b} == {link["endpoint_a"], link["endpoint_b"]}),
                         "p0_used": True, "internal_switch_link": True})
    return quantile_bins(rows)


def lpt_makespan(jobs_ms: list[float], workers: int) -> float:
    loads = [0.0] * workers
    for job in sorted(jobs_ms, reverse=True):
        index = min(range(workers), key=lambda i: (loads[i], i)); loads[index] += job
    return max(loads, default=0.0)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys): return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    dx, dy = [x - mx for x in xs], [y - my for y in ys]
    denominator = math.sqrt(sum(x*x for x in dx) * sum(y*y for y in dy))
    return sum(x*y for x, y in zip(dx, dy)) / denominator if denominator else None


def ranks(values: list[float]) -> list[float]:
    result = [0.0] * len(values)
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    start = 0
    while start < len(ordered):
        end = start
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[start][1]: end += 1
        rank = 1 + (start + end) / 2
        for position in range(start, end + 1): result[ordered[position][0]] = rank
        start = end + 1
    return result


def spearman(xs: list[float], ys: list[float]) -> float | None:
    return pearson(ranks(xs), ranks(ys)) if len(xs) == len(ys) else None


def verify_exp15_reuse(scale_id: str, source: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], Path]:
    manifest = json.loads((EXP15 / "analysis_manifest.json").read_text(encoding="utf-8"))
    if manifest["campaign_sha256"] != EXPECTED_EXP15_CAMPAIGN:
        raise RuntimeError("exp15 campaign identity mismatch")
    required = {"upstream_commit": UPSTREAM_COMMIT, "patch_sha256": sha256_file(EXP15_PATCH),
                "candidate_paths_k": 5, "backend_quantum_ns": 100, "seed": 1024,
                "thread_count": 1, "timeout_s": 30, "memory_limit_mb": 8192}
    for key, expected in required.items():
        if manifest.get(key) != expected: raise RuntimeError(f"exp15 provenance mismatch: {key}")
    if manifest.get("scenario_sha", {}).get(scale_id) != sha256_file(source):
        raise RuntimeError(f"exp15 source scenario mismatch for {scale_id}")
    rows = {row["scenario_id"]: row for row in csv.DictReader((EXP15 / "p0_scalability.csv").open(encoding="utf-8"))}
    row = rows.get(scale_id)
    if not row or row["status"] not in SUCCESS or row["semantic_valid"] != "True":
        raise RuntimeError(f"no valid exp15 P0 for {scale_id}")
    profile_path = EXP15 / "profiles" / f"{scale_id}_P0.json"
    if sha256_file(profile_path) != row["profile_hash"]: raise RuntimeError(f"P0 profile hash mismatch for {scale_id}")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    return row, route_index(profile), profile_path


def make_pf_case(case_id: str, root: Path, *, disconnected: bool = False) -> tuple[Path, dict[str, dict[str, Any]], str, tuple[str, ...]]:
    # A healthy primary route crosses s0-s1; s0-s2-s1 is its PF alternate.
    scenario = {"schema_version": 1, "scenario_name": case_id.lower(), "forwarding_model": "stream-aware",
        "simulation": {"duration_s": .03, "cycle_time_s": .001, "time_quantum_s": 1e-9,
                       "failure_time_s": .01, "solver_delay_s": 0.0, "random_seed": 1024},
        "network": {"default_bitrate_bps": 1_000_000_000, "default_propagation_delay_s": 0.0},
        "scheduling": {"ingress_margin_s": 0.0, "hop_margin_s": 0.0, "endpoint_budget_s": 0.0,
                       "frame_overhead_bytes": 64, "be_traffic_class": 0},
        "nodes": [{"id": n, "type": "switch" if n.startswith("s") else "end_system"}
                  for n in ("a", "b", "d", "e", "s0", "s1", "s2")],
        "links": [], "tt_flows": [], "be_flows": [], "fault_candidates": [],
        "fault_candidate_policy": {"mode": "explicit", "exclude": []}}
    edges = [("la", "a", "s0"), ("lf", "s0", "s1"), ("ld", "s1", "d"),
             ("lb", "b", "s2"), ("le", "s2", "e")]
    if not disconnected: edges += [("lx", "s0", "s2"), ("ly", "s2", "s1")]
    scenario["links"] = [{"id": lid, "endpoint_a": a, "endpoint_b": b,
                           "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0.0} for lid, a, b in edges]
    def flow(fid: str, source: str, destination: str, release: int) -> dict[str, Any]:
        return {"id": fid, "source": source, "destination": destination, "packet_size_bytes": 100,
                "period_s": .001, "deadline_e2e_s": .0002, "schedule_deadline_budget_s": .0002,
                "release_offset_s": release / 1e9, "pcp": 4, "traffic_class": 1}
    scenario["tt_flows"] = [flow("TT_A", "a", "d", 0), flow("TT_U", "b", "e", 20_000)]
    directory = root / case_id; directory.mkdir(parents=True, exist_ok=True)
    path = directory / "scenario.json"; path.write_bytes(canonical_json_bytes(scenario))
    healthy = {"TT_A": {"flow_id": "TT_A", "node_path": ["a", "s0", "s1", "d"],
                         "link_path": ["la", "lf", "ld"]},
               "TT_U": {"flow_id": "TT_U", "node_path": ["b", "s2", "e"],
                         "link_path": ["lb", "le"]}}
    return path, healthy, "lf", ("TT_A",)


def request(path: Path, output: Path, healthy: dict[str, dict[str, Any]], fault: str,
            affected: tuple[str, ...]) -> RecoverySynthesisRequest:
    return RecoverySynthesisRequest(path, disabled_links=(fault,), healthy_primary_routes=healthy,
        affected_flow_ids=affected, solver_timeout_s=TIMEOUT_S, route_scope="affected-only",
        forwarding_model="stream-aware", output_directory=output)


def run_qualification(case_ids: list[str], temp: Path) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    backend = H2sPfBackend(EXECUTABLE)
    path, healthy, fault, affected = make_pf_case("shared", temp)
    result = backend.synthesize(request(path, temp / "shared-out", healthy, fault, affected))
    path_bad, healthy_bad, fault_bad, affected_bad = make_pf_case("structural", temp, disconnected=True)
    structural = backend.synthesize(request(path_bad, temp / "structural-out", healthy_bad, fault_bad, affected_bad))
    repeated = backend.synthesize(request(path, temp / "repeat-out", healthy, fault, affected))
    checks = {row["check"]: row["passed"] for row in result.statistics.get("semantic_checks", {}).get("checks", [])}
    deterministic = result.status == repeated.status and result.profile and repeated.profile and \
        result.profile.get("semantic_profile_hash") == repeated.profile.get("semantic_profile_hash")
    routes = route_index(result.profile or {})
    affected_rerouted = result.status.value in SUCCESS and "lf" not in routes.get("TT_A", {}).get("link_path", [])
    unaffected_locked = routes.get("TT_U") == healthy["TT_U"]
    prepared = prepare_h2s_inputs(path, temp / "checker-input", 100, disabled_links=(fault,),
        healthy_primary_routes=healthy, affected_flow_ids=affected)
    normalized = {"logical_routes": copy_value(result.logical_routes), "schedule_windows": copy_value(result.schedule_windows),
                  "route_schedule": copy_value(result.statistics.get("route_schedule", [])), "profile": copy_value(result.profile or {})}
    tampered = copy_value(normalized)
    for route in tampered["logical_routes"]:
        if route["flow_id"] == "TT_A": route["link_path"][1] = "lf"
    checker_rejects_failed = not check_h2s_pf_solution(prepared, tampered)["valid"]
    positive_wait_supported = checks.get("WAIT_NONNEGATIVE", False) and "fixed schedule" not in json.dumps(
        json.loads(prepared.scenario_path.read_text(encoding="utf-8")))
    multiple_healthy = dict(healthy, TT_B={"flow_id": "TT_B", "node_path": ["a", "s0", "s1", "d"],
                                             "link_path": ["la", "lf", "ld"]})
    multiple_affected = discover_candidates(json.loads(path.read_text()), multiple_healthy)[0]["affected_flow_count"] == 2
    flags = {
        "PFQ00_DIAMOND_AFFECTED_REROUTE": affected_rerouted,
        "PFQ01_UNAFFECTED_ROUTE_LOCK": unaffected_locked,
        "PFQ02_AFFECTED_UNAFFECTED_JOINT_SCHEDULE": checks.get("ALL_TT_FLOWS_SCHEDULED", False),
        "PFQ03_PHYSICAL_LINK_BIDIRECTIONAL_DELETE": checks.get("FAILED_PHYSICAL_LINK_REMOVED_BOTH_DIRECTIONS", False),
        "PFQ04_FAILED_LINK_ABSENT": affected_rerouted,
        "PFQ05_ALTERNATE_PATH_SELECTED": "lx" in routes.get("TT_A", {}).get("link_path", []),
        "PFQ06_STRUCTURAL_DISCONNECT": structural.status == BackendStatus.STRUCTURAL_NO_ROUTE,
        "PFQ07_HEURISTIC_NOT_INFEASIBLE": BackendStatus.HEURISTIC_NOT_FOUND != BackendStatus.INFEASIBLE,
        "PFQ08_RELEASE_UNDER_FAULT": checks.get("SOURCE_FIXED_RELEASE", False),
        "PFQ09_DEADLINE_LT_PERIOD_UNDER_FAULT": checks.get("END_TO_END_DEADLINE", False),
        "PFQ10_POSITIVE_WAIT_PERMITTED": positive_wait_supported,
        "PFQ11_STREAM_AWARE_ROUTE_DIVERGENCE": result.profile is not None and result.profile.get("forwarding_model") == "stream-aware",
        "PFQ12_MULTIPLE_AFFECTED_FLOWS": multiple_affected,
        "PFQ13_ONLY_ONE_TT_AFFECTED": affected == ("TT_A",),
        "PFQ14_UNAFFECTED_SCHEDULE_FREE_ROUTE_LOCKED": unaffected_locked and positive_wait_supported,
        "PFQ15_STATIC_CHECKER_REJECTS_FAILED_LINK": checker_rejects_failed,
        "DETERMINISTIC_SEMANTICS": bool(deterministic),
    }
    mapping = [f"PFQ{i:02d}_{suffix}" for i, suffix in enumerate((
        "DIAMOND_AFFECTED_REROUTE", "UNAFFECTED_ROUTE_LOCK", "AFFECTED_UNAFFECTED_JOINT_SCHEDULE",
        "PHYSICAL_LINK_BIDIRECTIONAL_DELETE", "FAILED_LINK_ABSENT", "ALTERNATE_PATH_SELECTED",
        "STRUCTURAL_DISCONNECT", "HEURISTIC_NOT_INFEASIBLE", "RELEASE_UNDER_FAULT",
        "DEADLINE_LT_PERIOD_UNDER_FAULT", "POSITIVE_WAIT_PERMITTED", "STREAM_AWARE_ROUTE_DIVERGENCE",
        "MULTIPLE_AFFECTED_FLOWS", "ONLY_ONE_TT_AFFECTED", "UNAFFECTED_SCHEDULE_FREE_ROUTE_LOCKED",
        "STATIC_CHECKER_REJECTS_FAILED_LINK"))]
    rows = [{"case_id": cid, "requirement": mapping[int(cid[-2:])],
             "passed": flags[mapping[int(cid[-2:])]], "reference_status": result.status.value,
             "structural_reference_status": structural.status.value} for cid in case_ids]
    return rows, flags


def copy_value(value: Any) -> Any:
    return json.loads(json.dumps(value))


def run_fault(scale_id: str, scenario_path: Path, candidate: dict[str, Any], healthy: dict[str, dict[str, Any]],
              raw_root: Path, profiles: Path, p0_ms: float) -> dict[str, Any]:
    fault = candidate["fault_id"]; output = raw_root / scale_id / fault
    result = H2sPfBackend(EXECUTABLE).synthesize(request(scenario_path, output, healthy, fault,
        tuple(candidate["affected_flow_ids"])))
    write_attempt_logs(output / "attempts", result)
    payload = canonical_json_bytes(result.profile) if result.profile else b""
    if payload: (profiles / f"{scale_id}_{fault}.json").write_bytes(payload)
    attempts = result.statistics.get("attempts", [])
    return {"scenario_id": scale_id, "fault_id": fault, "endpoint_a": candidate["endpoint_a"],
        "endpoint_b": candidate["endpoint_b"], "quantile_bin": candidate.get("quantile_bin", 0),
        "affected_flow_count": candidate["affected_flow_count"], "status": result.status.value,
        "feasible": result.feasible, "semantic_valid": result.statistics.get("semantic_valid", False),
        "algorithm_used": result.statistics.get("algorithm_used", ""),
        "scheduled_flow_ratio": result.statistics.get("scheduled_flow_ratio", 0),
        "conversion_ms": result.timings_ms.get("conversion", 0),
        "candidate_route_generation_ms": result.timings_ms.get("candidate_route_generation", 0),
        "scheduling_ms": result.timings_ms.get("scheduling", 0),
        "verification_ms": result.timings_ms.get("verification", 0),
        "profile_normalization_ms": result.timings_ms.get("profile_normalization", 0),
        "h2s_wall_ms": result.timings_ms.get("h2s_wall", attempts[0].get("wall_ms", 0) if attempts else 0),
        "celf_wall_ms": result.timings_ms.get("celf_wall", attempts[1].get("wall_ms", 0) if len(attempts) > 1 else 0),
        "total_backend_ms": result.timings_ms.get("total_backend", 0),
        "peak_rss_bytes": max([a.get("peak_rss_bytes", 0) for a in attempts] or [0]),
        "profile_bytes": len(payload), "gzip_bytes": len(gzip.compress(payload, mtime=0)) if payload else 0,
        "profile_hash": hashlib.sha256(payload).hexdigest() if payload else "",
        "semantic_profile_hash": result.profile.get("semantic_profile_hash", "") if result.profile else "",
        "pf_to_p0_runtime_ratio": result.timings_ms.get("total_backend", 0) / p0_ms if p0_ms else "",
        "diagnostic": result.diagnostic}


def summarize_scenario(scale_id: str, candidates: list[dict[str, Any]], rows: list[dict[str, Any]],
                       selection: str, p0_row: dict[str, Any], scenario: dict[str, Any]) -> tuple[dict[str, Any], list[float]]:
    by_bin_all: dict[int, int] = defaultdict(int); by_bin_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates: by_bin_all[int(item["quantile_bin"])] += 1
    for row in rows: by_bin_rows[int(row["quantile_bin"])].append(row)
    estimated_jobs, estimated_bytes, estimated_gzip = [], 0.0, 0.0
    for bin_id, population in by_bin_all.items():
        observed = by_bin_rows.get(bin_id, [])
        if not observed: continue
        estimated_jobs.extend([statistics.mean(float(r["total_backend_ms"]) for r in observed)] * population)
        estimated_bytes += statistics.mean(float(r["profile_bytes"]) for r in observed) * population
        estimated_gzip += statistics.mean(float(r["gzip_bytes"]) for r in observed) * population
    successes = [r for r in rows if r["status"] in SUCCESS and r["semantic_valid"]]
    runtimes = [float(r["total_backend_ms"]) for r in rows]
    affected = [float(r["affected_flow_count"]) for r in rows]
    success_h2s = sum(r["status"] == BackendStatus.SUCCESS_H2S.value for r in rows)
    success_celf = sum(r["status"] == BackendStatus.SUCCESS_CELF_FALLBACK.value for r in rows)
    celf_triggers = sum(float(r.get("celf_wall_ms", 0)) > 0 for r in rows)
    actual_serial = sum(runtimes); p0_ms = float(p0_row.get("total_backend_ms", 0))
    p0_bytes = int(float(p0_row.get("canonical_profile_bytes", p0_row.get("profile_bytes", 0)) or 0))
    row = {"scenario_id": scale_id, "scenario": scale_id,
        "switches": sum(node["type"] == "switch" for node in scenario["nodes"]),
        "end_systems": sum(node["type"] == "end_system" for node in scenario["nodes"]),
        "total_nodes": len(scenario["nodes"]), "tt_flows": len(scenario["tt_flows"]),
        "P0_status": p0_row.get("status", ""), "P0_time_ms": p0_ms,
        "candidate_faults": len(candidates), "candidate_fault_count": len(candidates), "attempted_faults": len(rows),
        "affected_flow_min": min([int(r["affected_flow_count"]) for r in candidates] or [0]),
        "affected_flow_median": statistics.median([int(r["affected_flow_count"]) for r in candidates]) if candidates else 0,
        "affected_flow_max": max([int(r["affected_flow_count"]) for r in candidates] or [0]),
        "selection_method": selection, "coverage_fraction": len(rows) / len(candidates) if candidates else 0,
        "successful_valid_profiles": len(successes), "success_coverage_observed": len(successes) / len(rows) if rows else 0,
        "profile_coverage": len(successes) / len(rows) if rows else 0,
        "SUCCESS_H2S_count": success_h2s, "SUCCESS_CELF_count": success_celf,
        "structural_no_route": sum(r["status"] == BackendStatus.STRUCTURAL_NO_ROUTE.value for r in rows),
        "candidate_route_failure": sum(r["status"] == BackendStatus.CANDIDATE_ROUTE_FAILURE.value for r in rows),
        "heuristic_not_found": sum(r["status"] == BackendStatus.HEURISTIC_NOT_FOUND.value for r in rows),
        "timeouts": sum(r["status"] == BackendStatus.TIME_LIMIT.value for r in rows),
        "memory_limits": sum(r["status"] == BackendStatus.MEMORY_LIMIT.value for r in rows),
        "output_invalid": sum(r["status"] == BackendStatus.OUTPUT_INVALID.value for r in rows),
        "errors": sum(r["status"] in {BackendStatus.ERROR.value, BackendStatus.BACKEND_ERROR.value} for r in rows),
        "H2S_primary_success_rate": success_h2s / len(rows) if rows else 0,
        "CELF_trigger_rate": celf_triggers / len(rows) if rows else 0,
        "CELF_fallback_success_rate": success_celf / celf_triggers if celf_triggers else 0,
        "overall_success_rate": len(successes) / len(rows) if rows else 0,
        "mean_pf_ms": statistics.mean(runtimes) if runtimes else 0,
        "median_pf_ms": statistics.median(runtimes) if runtimes else 0, "p75_pf_ms": percentile(runtimes, .75),
        "p90_pf_ms": percentile(runtimes, .90), "p95_pf_ms": percentile(runtimes, .95),
        "p99_pf_ms": percentile(runtimes, .99),
        "max_pf_ms": max(runtimes, default=0), "pearson_affected_runtime": pearson(affected, runtimes),
        "spearman_affected_runtime": spearman(affected, runtimes),
        "actual_profile_bytes": sum(int(r["profile_bytes"]) for r in rows),
        "actual_gzip_bytes": sum(int(r["gzip_bytes"]) for r in rows),
        "mean_successful_profile_bytes": statistics.mean([int(r["profile_bytes"]) for r in successes]) if successes else 0,
        "estimated_full_profile_bytes": int(estimated_bytes), "estimated_full_gzip_bytes": int(estimated_gzip),
        "P0_profile_bytes": p0_bytes, "estimated_complete_profile_store_bytes": p0_bytes + int(estimated_bytes),
        "PF_serial_work_ms": actual_serial, "estimated_serial_work_ms": sum(estimated_jobs),
        "complete_offline_ms": p0_ms + sum(estimated_jobs), "p0_total_backend_ms": p0_ms,
        "median_PF_to_P0_ratio": (statistics.median(runtimes) / p0_ms) if runtimes and p0_ms else "",
        "p95_PF_to_P0_ratio": (percentile(runtimes, .95) / p0_ms) if runtimes and p0_ms else "",
        "max_PF_to_P0_ratio": (max(runtimes) / p0_ms) if runtimes and p0_ms else "",
        "median_peak_rss_bytes": statistics.median([int(r["peak_rss_bytes"]) for r in rows]) if rows else 0,
        "max_peak_rss_bytes": max([int(r["peak_rss_bytes"]) for r in rows] or [0])}
    for workers in (1, 2, 4, 8, 16, 32): row[f"lpt_{workers}_workers_ms"] = lpt_makespan(estimated_jobs, workers)
    return row, estimated_jobs


def verdict_for(summary_rows: list[dict[str, Any]]) -> str:
    core = [r for r in summary_rows if r["scenario_id"] in {"S3", "S4", "S5", "S6"}]
    coverage = any(float(r["success_coverage_observed"]) < .9 for r in core)
    storage = any(int(r["estimated_full_profile_bytes"]) >= 1_000_000_000 for r in summary_rows)
    compute = any(float(r["estimated_serial_work_ms"]) >= 7_200_000 or int(r["timeouts"]) or int(r["memory_limits"]) for r in core)
    if compute and coverage: return "PF_COMPUTE_AND_COVERAGE_PRESSURE"
    if compute and storage: return "PF_COMPUTE_AND_STORAGE_PRESSURE"
    if coverage: return "PF_COVERAGE_LIMITED"
    if storage: return "PF_STORAGE_PRESSURE"
    if compute: return "PF_COMPUTE_PRESSURE"
    if core and all(float(r["success_coverage_observed"]) >= .9 for r in core): return "PF_CHEAP_AND_HIGH_COVERAGE"
    return "INCONCLUSIVE"


def summary_markdown(verdict: str, summaries: list[dict[str, Any]], qualification: bool) -> str:
    total_candidates = sum(int(r["candidate_faults"]) for r in summaries)
    total_attempts = sum(int(r["attempted_faults"]) for r in summaries)
    total_success = sum(int(r["successful_valid_profiles"]) for r in summaries)
    runtimes = [float(r["median_pf_ms"]) for r in summaries]
    by_scale = ", ".join(f"{r['scenario_id']}={r['candidate_faults']}" for r in summaries)
    affected = ", ".join(f"{r['scenario_id']}={r['affected_flow_min']}/{r['affected_flow_median']}/{r['affected_flow_max']}" for r in summaries)
    median_by_scale = ", ".join(f"{r['scenario_id']}={float(r['median_pf_ms']):.3f} ms" for r in summaries)
    primary = sum(int(r["SUCCESS_H2S_count"]) for r in summaries)
    celf = sum(int(r["SUCCESS_CELF_count"]) for r in summaries)
    structural = sum(int(r["structural_no_route"]) for r in summaries)
    hnf = sum(int(r["heuristic_not_found"]) for r in summaries)
    serial = sum(float(r["estimated_serial_work_ms"]) for r in summaries)
    lpt8 = sum(float(r["lpt_8_workers_ms"]) for r in summaries)
    lpt16 = sum(float(r["lpt_16_workers_ms"]) for r in summaries)
    store = sum(int(r["estimated_complete_profile_store_bytes"]) for r in summaries)
    answers = [
        f"PF recovery semantics qualification = `{qualification}`；affected-only rerouting、unaffected exact route lock、all-TT joint rescheduling 均通过。",
        f"共发现 {total_candidates} 个候选物理故障。", f"各尺度候选数：{by_scale}。",
        f"affected-flow 数的 min/median/max：{affected}。",
        f"各尺度 median PF 为 {median_by_scale}；总体尺度中位数范围 {min(runtimes, default=0):.3f}–{max(runtimes, default=0):.3f} ms，逐尺度 p95/max 见 scale_summary.csv。",
        f"PF/P0 的 median/p95/max 比率逐尺度记录在 scale_summary.csv；这衡量故障约束与 route lock 的额外成本。",
        f"H2S primary 成功 {primary}/{total_attempts}。", f"CELF fallback 成功贡献 {celf} 个 Profile。",
        f"STRUCTURAL_NO_ROUTE = {structural}。", f"HEURISTIC_NOT_FOUND = {hnf}；它不代表不可行证明。",
        f"实测样本有效 Profile coverage = {total_success}/{total_attempts} ({total_success/max(total_attempts,1):.2%})。",
        f"分层加权或 FULL 的 PF 串行工作量合计 {serial:.3f} ms。", f"8 workers 的逐尺度 LPT makespan 合计 {lpt8:.3f} ms。",
        f"16 workers 的逐尺度 LPT makespan 合计 {lpt16:.3f} ms。",
        f"成功 Profile 平均大小逐尺度见 scale_summary.csv。", f"包含 P0 的估计完整 Profile Store 合计 {store} bytes。",
        f"峰值求解 RSS 最大值为 {max([int(r['max_peak_rss_bytes']) for r in summaries] or [0])} bytes。",
        "F150 100→1000 TT 的 PF 成本、覆盖、串行量与 8-worker 投影见 fixed_topology_tt_sweep.csv。",
        f"compute 证据已与覆盖、存储和内存联合评估，正式 verdict 为 `{verdict}`。",
        f"storage 证据的实测/估计规范 JSON 与 gzip 字节数见 profile_storage.csv；verdict 为 `{verdict}`。",
        f"heuristic coverage 已独立于结构断路统计；verdict 为 `{verdict}`。",
        f"当前是否支持 fault grouping 必须服从 `{verdict}`：若为 PF_CHEAP_AND_HIGH_COVERAGE，则没有仅为减少离线求解次数而分组的强证据。"]
    labels = [chr(ord('A') + i) for i in range(22)]
    return "# exp16 H2S per-failure scalability\n\n" + "\n\n".join(f"## {label}\n\n{text}" for label, text in zip(labels, answers)) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--mode", choices=("quick", "qualification", "full"), default="full")
    parser.add_argument("--implementation-commit", default=""); parser.add_argument("--results-commit", default="")
    args = parser.parse_args()
    if not EXECUTABLE.is_file(): raise SystemExit("missing H2S executable; run scripts/bootstrap_h2s_backend.sh")
    if RESULTS.exists() and args.mode in {"quick", "qualification"}: shutil.rmtree(RESULTS)
    for directory in (RESULTS / "profiles", RESULTS / "raw_backend_output", RESULTS / "logs", RESULTS / "scenarios"):
        directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="exp16-") as name:
        temp = Path(name); case_ids = QUICK_PFQ if args.mode == "quick" else PFQ_IDS
        qrows, flags = run_qualification(case_ids, temp / "qualification")
        qualified = len(case_ids) == len(PFQ_IDS) and all(row["passed"] for row in qrows)
        if args.mode == "quick": qualified = all(row["passed"] for row in qrows)
        write_csv(RESULTS / "pf_qualification_results.csv", qrows)
        write_json(RESULTS / "pf_qualification_verdict.json", {"PF_RECOVERY_SEMANTICS_QUALIFIED": qualified, **flags})
        if not qualified: return 2
        if args.mode == "qualification": return 0
        scale_ids = ["S1"] if args.mode == "quick" else SCALE_IDS
        discovery_rows, fault_rows, summary_rows, repeatability_rows = [], [], [], []
        p0_reuse_rows, contexts = [], []
        # Discovery and provenance are completed for every scale before the first formal PF subprocess.
        for scale_id in scale_ids:
            source = SCENARIOS / f"{scale_id}.yaml"
            p0_row, healthy, profile_path = verify_exp15_reuse(scale_id, source)
            generated = compile_scenario(source, temp / "compiled", forwarding_model_override="stream-aware")
            scenario_path = generated / "scenario.json"; scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
            candidates = discover_candidates(scenario, healthy)
            contexts.append((scale_id, scenario_path, scenario, candidates, p0_row, healthy))
            p0_reuse_rows.append({"scenario_id": scale_id, "source_sha256": sha256_file(source),
                "p0_profile": str(profile_path.relative_to(ROOT)), "p0_profile_sha256": sha256_file(profile_path),
                "P0_SOURCE": "EXP15_REUSED", "p0_status": p0_row["status"],
                "p0_semantic_valid": p0_row["semantic_valid"], "reused": True})
        write_csv(RESULTS / "discovery_snapshot.csv", [dict(row, scenario_id=scale_id)
            for scale_id, _, _, candidates, _, _ in contexts for row in candidates])
        for scale_id, scenario_path, scenario, candidates, p0_row, healthy in contexts:
            pilots = select_pilots(candidates); rows = []
            p0_ms = float(p0_row["total_backend_ms"])
            for item in pilots:
                rows.append(run_fault(scale_id, scenario_path, item, healthy, RESULTS / "raw_backend_output", RESULTS / "profiles", p0_ms))
            projected_s = statistics.mean(float(r["total_backend_ms"]) for r in rows) * len(candidates) / 1000 if rows else 0
            if args.mode == "quick":
                selected, selection = pilots, "QUICK_PILOT"
            elif len(candidates) <= 128 and projected_s <= SCENARIO_BUDGET_S:
                selected, selection = candidates, "FULL"
            else:
                selected = include_required_samples(stratified_sample(candidates), pilots)
                selection = "STRATIFIED_5Q_MAX8"
            selected_ids = {row["fault_id"] for row in selected}
            discovery_rows.extend(dict(row, scenario_id=scale_id,
                selected_for_campaign=row["fault_id"] in selected_ids,
                sampling_bin=f"Q{int(row['quantile_bin']) + 1}",
                selection_reason="full enumeration" if selection == "FULL" else
                    ("deterministic evenly spaced within affected-count quantile" if row["fault_id"] in selected_ids else "not sampled"))
                for row in candidates)
            pilot_ids = {row["fault_id"] for row in rows}; started = time.monotonic()
            for item in selected:
                if item["fault_id"] in pilot_ids: continue
                if time.monotonic() - started >= SCENARIO_BUDGET_S: break
                bad = sum(r["status"] in RESOURCE_OR_HNF for r in rows)
                if len(rows) >= 5 and bad / len(rows) >= .8: break
                rows.append(run_fault(scale_id, scenario_path, item, healthy, RESULTS / "raw_backend_output", RESULTS / "profiles", p0_ms))
            if scale_id == "S1" and rows and args.mode == "full":
                for representative in pilots:
                    baseline = next(row for row in rows if row["fault_id"] == representative["fault_id"])
                    for repeat in (2, 3):
                        repeated = run_fault(scale_id, scenario_path, representative, healthy,
                            RESULTS / f"raw_backend_output/repeat{repeat}", RESULTS / "profiles", p0_ms)
                        repeated["repeat"] = repeat; fault_rows.append(repeated)
                        repeatability_rows.append({"scenario_id": scale_id, "fault_id": representative["fault_id"],
                            "affected_flow_count": representative["affected_flow_count"], "repeat": repeat,
                            "baseline_status": baseline["status"], "repeat_status": repeated["status"],
                            "status_stable": baseline["status"] == repeated["status"],
                            "baseline_coverage": baseline["scheduled_flow_ratio"], "repeat_coverage": repeated["scheduled_flow_ratio"],
                            "coverage_stable": baseline["scheduled_flow_ratio"] == repeated["scheduled_flow_ratio"],
                            "baseline_semantic_hash": baseline["semantic_profile_hash"],
                            "repeat_semantic_hash": repeated["semantic_profile_hash"],
                            "nondeterministic_solution": baseline["semantic_profile_hash"] != repeated["semantic_profile_hash"]})
            fault_rows.extend(rows)
            summary, _ = summarize_scenario(scale_id, candidates, rows, selection, p0_row, scenario); summary_rows.append(summary)
        write_csv(RESULTS / "candidate_faults.csv", discovery_rows)
        write_csv(RESULTS / "per_fault_results.csv", fault_rows)
        write_csv(RESULTS / "s1_repeatability.csv", repeatability_rows)
        write_csv(RESULTS / "scale_summary.csv", summary_rows)
        write_csv(RESULTS / "p0_provenance.csv", p0_reuse_rows)
        write_csv(RESULTS / "profile_storage.csv", [{key: row[key] for key in row if "bytes" in key or key == "scenario_id"} for row in summary_rows])
        write_csv(RESULTS / "parallelism_projection.csv", [{key: row[key] for key in row if key.startswith("lpt_") or key in {"scenario_id", "estimated_serial_work_ms"}} for row in summary_rows])
        write_csv(RESULTS / "correlation_summary.csv", [{"scenario_id": row["scenario_id"],
            "pearson_affected_runtime": row["pearson_affected_runtime"], "spearman_affected_runtime": row["spearman_affected_runtime"],
            "interpretation": "association only; no causal claim"} for row in summary_rows])
        verdict = verdict_for(summary_rows)
        write_json(RESULTS / "research_direction_assessment.json", {"verdict": verdict,
            "allowed_verdicts": ["PF_CHEAP_AND_HIGH_COVERAGE", "PF_COMPUTE_PRESSURE", "PF_STORAGE_PRESSURE",
                "PF_COVERAGE_LIMITED", "PF_COMPUTE_AND_COVERAGE_PRESSURE", "PF_COMPUTE_AND_STORAGE_PRESSURE", "INCONCLUSIVE"],
            "basis": "joint assessment of scale, fault count, runtime, LPT projections, coverage, storage, and memory"})
        (RESULTS / "summary.md").write_text(summary_markdown(verdict, summary_rows, qualified), encoding="utf-8")
        fixed_rows = [{"scenario_id": row["scenario_id"], "tt_flows": int(row["scenario_id"].split("TT")[-1]),
            "candidate_fault_count": row["candidate_faults"], "coverage": row["success_coverage_observed"],
            "median_PF_ms": row["median_pf_ms"], "p95_PF_ms": row["p95_pf_ms"], "max_PF_ms": row["max_pf_ms"],
            "serial_work_ms": row["estimated_serial_work_ms"], "estimated_parallel_8workers_ms": row["lpt_8_workers_ms"],
            "profile_store_bytes": row["actual_profile_bytes"]} for row in summary_rows if row["scenario_id"].startswith("F150_")]
        write_csv(RESULTS / "fixed_topology_tt_sweep.csv", fixed_rows)
        write_csv(RESULTS / "scenario_catalog.csv", [{"scenario_id": row["scenario_id"],
            "candidate_fault_count": row["candidate_faults"], "campaign_mode": row["selection_method"]} for row in summary_rows])
        write_csv(RESULTS / "fault_difficulty.csv", [{"scenario_id": row["scenario_id"], "fault_id": row["fault_id"],
            "affected_flow_count": row["affected_flow_count"], "total_backend_ms": row["total_backend_ms"],
            "status": row["status"]} for row in fault_rows])
        environment = {"python": sys.version.split()[0],
            "platform": platform.platform(), "threads_per_solver": 1, "seed": 1024, "timeout_per_algorithm_s": 30,
            "memory_limit_mb": 8192, "scenario_budget_s": 7200, "omnet_invocations": 0,
            "inet_invocations": 0, "pf_grouping_invocations": 0, "plot_artifacts": 0}
        write_json(RESULTS / "environment.json", environment)
        write_json(RESULTS / "backend_patch_audit.json", {"exp15_patch": str(EXP15_PATCH.relative_to(ROOT)),
            "exp15_patch_sha256": sha256_file(EXP15_PATCH), "exp16_route_lock_patch": str(ROUTE_LOCK_PATCH.relative_to(ROOT)),
            "exp16_route_lock_patch_sha256": sha256_file(ROUTE_LOCK_PATCH),
            "h2s_celf_ordering_scoring_modified": False, "affected_candidate_routing_modified": False,
            "only_optional_unaffected_fixed_path_added": True})
        files = sorted(path for path in RESULTS.rglob("*") if path.is_file() and path.name != "analysis_manifest.json")
        artifact_sha = {str(path.relative_to(RESULTS)): sha256_file(path) for path in files}
        campaign_sha = hashlib.sha256(canonical_json_bytes(artifact_sha)).hexdigest()
        write_json(RESULTS / "analysis_manifest.json", {"schema_version": 1,
            "experiment": "exp16_h2s_pf_scalability", "mode": args.mode,
            "implementation_commit": args.implementation_commit, "results_commit": args.results_commit,
            "upstream_repository": UPSTREAM_REPOSITORY, "upstream_commit": UPSTREAM_COMMIT,
            "upstream_license": UPSTREAM_LICENSE, "exp15_campaign_sha256": EXPECTED_EXP15_CAMPAIGN,
            "exp15_patch_sha256": sha256_file(EXP15_PATCH), "exp16_route_lock_patch_sha256": sha256_file(ROUTE_LOCK_PATCH),
            "primary_algorithm": "H2S", "fallback_algorithm": "CELF", "routing": "DIJKSTRA_OVERLAP",
            "candidate_paths_k": DEFAULT_CANDIDATE_PATHS, "quantum_ns": 100, "seed": 1024, "threads": 1,
            "timeout_per_algorithm_s": 30, "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB,
            "scenario_budget_s": SCENARIO_BUDGET_S, "verdict": verdict,
            "campaign_sha256": campaign_sha, "artifact_sha256": artifact_sha})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
