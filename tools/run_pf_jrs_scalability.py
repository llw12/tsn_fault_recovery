#!/usr/bin/env python3
"""Run exp14 per-failure JRS-WA scalability diagnosis without OMNeT++."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.generate_jrs_scalability_scenarios import (
    FIXED_SWEEP, GENERATOR_VERSION, MAIN_SCALES, Scale, scenario_audit,
    topology_edges, yaml_text,
)
from tools.jrs_scalability_utils import (
    canonical_profile_bytes, deterministic_gzip, discover_candidates,
    lpt_projection, quantile_bin_map, sha256_bytes, stratified_sample,
)
from tools.jrs_wa_adapter import TSNKIT_COMMIT, TSNKIT_VERSION, canonical_json_bytes
from tools.scenario_compiler import build_port_map
from tools.scenario_model import load_scenario
from tools.scip_jrs_wa_backend import SCIP_MEMORY_LIMIT_MB, SCIP_SEED, SCIP_THREADS, ScipJrsWaBackend
from tools.jrs_wa_static_checker import check_solution
from tools.recovery_backend import RecoverySynthesisRequest

TIMEOUT_S = 30
SCALE_BUDGET_S = 7200
EXP12_SHA = "c306a4d5de34761aba96dead957bdcda27cbaed7e3614bd573effd8515333274"
EXP13_SHA = "2bb4fbf6c5a39b5b0d873165c661ad847d965eb13570aea112a36385ae80e5c3"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or sorted({k for row in rows for k in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def percentile(values: list[float], fraction: float) -> float:
    if not values: return 0.0
    ordered = sorted(values)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


def compile_solver_only(source: Path, destination: Path) -> Path:
    model = load_scenario(source)
    destination.mkdir(parents=True, exist_ok=True)
    value = model.canonical_dict(); value["fault_candidates"] = []; value["scenario_sha256"] = model.sha256()
    write_json(destination / "scenario.json", value)
    write_json(destination / "port_map.json", build_port_map(model))
    return destination


def parse_time_v(path: Path) -> int | None:
    if not path.exists(): return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "Maximum resident set size (kbytes)" in line:
            try: return int(line.rsplit(":", 1)[1].strip()) * 1024
            except ValueError: return None
    return None


def worker(request_path: Path, response_path: Path) -> int:
    data = json.loads(request_path.read_text(encoding="utf-8"))
    request = RecoverySynthesisRequest(
        Path(data["scenario_path"]), tuple(data["disabled_links"]), data["healthy_routes"],
        tuple(data["affected_flow_ids"]), TIMEOUT_S, data["route_scope"], "stream-aware",
        Path(data["output_directory"]),
    )
    result = ScipJrsWaBackend().synthesize(request)
    scenario = json.loads(request.scenario_path.read_text(encoding="utf-8"))
    payload = result.to_dict()
    payload["static_checker"] = (check_solution(scenario, request.disabled_links, request.healthy_primary_routes,
                                                  request.affected_flow_ids, payload, request.route_scope)
                                 if result.feasible else {"valid": False, "failures": ["no feasible solution"], "checks": []})
    write_json(response_path, payload)
    return 0


def run_subprocess(root: Path, request: dict[str, Any], work: Path) -> tuple[dict[str, Any], int | None, float]:
    request_path, response_path, memory_path = work / "request.json", work / "response.json", work / "time-v.txt"
    write_json(request_path, request)
    base_command = [sys.executable, "-m", "tools.run_pf_jrs_scalability", "--worker", str(request_path), str(response_path)]
    command = (["/usr/bin/time", "-v", "-o", str(memory_path)] + base_command
               if Path("/usr/bin/time").exists() else base_command)
    started = time.perf_counter_ns()
    process = subprocess.run(command, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    elapsed = (time.perf_counter_ns() - started) / 1e6
    if process.returncode or not response_path.exists():
        payload = {"status": "ERROR", "feasible": False, "diagnostic": process.stdout[-4000:],
                   "statistics": {}, "timings_ms": {"total_backend": elapsed}, "static_checker": {"valid": False}}
    else: payload = json.loads(response_path.read_text(encoding="utf-8"))
    return payload, parse_time_v(memory_path), elapsed


def profile_metrics(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    profile = payload.get("profile")
    if not profile: return {"profile_hash": "", "profile_bytes": 0, "compressed_profile_bytes": 0}
    raw = canonical_profile_bytes(profile); compressed = deterministic_gzip(raw)
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); path.with_suffix(".json.gz").write_bytes(compressed)
    return {"profile_hash": sha256_bytes(raw), "profile_bytes": len(raw), "compressed_profile_bytes": len(compressed)}


def base_result_row(scale: Scale, fault_id: str, affected_count: int, candidate_count: int,
                    payload: dict[str, Any], rss: int | None, profile: dict[str, Any]) -> dict[str, Any]:
    timings, stats = payload.get("timings_ms", {}), payload.get("statistics", {})
    return {"scenario_id": scale.scenario, "scale_id": scale.scenario, "fault_id": fault_id,
            "affected_flow_count": affected_count, "total_tt_count": scale.tt_flows,
            "candidate_fault_count": candidate_count, "status": payload.get("status", "ERROR"),
            "feasible": payload.get("feasible", False), "input_conversion_ms": timings.get("input_conversion", ""),
            "route_space_build_ms": timings.get("route_space_build", ""), "model_build_ms": timings.get("model_build", ""),
            "solver_wall_ms": timings.get("solver_wall", ""), "solution_extract_ms": timings.get("solution_extract", ""),
            "profile_serialize_ms": timings.get("profile_serialize", ""), "total_backend_ms": timings.get("total_backend", ""),
            "timeout_s": TIMEOUT_S, "model_total_vars": stats.get("num_variables", ""),
            "routing_binary_vars": stats.get("num_route_variables", ""), "ordering_binary_vars": stats.get("num_ordering_variables", ""),
            "timing_integer_vars": stats.get("num_integer_time_variables", ""), "total_constraints": stats.get("num_constraints", ""),
            "nonoverlap_constraints": stats.get("constraint_family_counts", {}).get("LINK_NON_OVERLAP", ""),
            "SCIP_raw_status": stats.get("scip_status", ""), "semantic_valid": payload.get("static_checker", {}).get("valid", False),
            **profile, "peak_solver_memory_bytes": stats.get("solver_memory_bytes", rss or ""),
            "subprocess_peak_rss_bytes": rss or "",
            "memory_measurement_method": ("SCIP getMemUsed; /usr/bin/time -v subprocess RSS corroboration"
                                          if rss is not None else "SCIP getMemUsed")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("results/pf_jrs_scalability"))
    parser.add_argument("--run-id", default="exp14-formal")
    parser.add_argument("--implementation-commit", default="WORKTREE")
    parser.add_argument("--results-commit", default="PENDING")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--worker", nargs=2, metavar=("REQUEST", "RESPONSE"))
    args = parser.parse_args()
    if args.worker: return worker(Path(args.worker[0]), Path(args.worker[1]))
    root = Path(__file__).resolve().parents[1]; os.chdir(root)
    if hashlib.sha256((root / "results/topology_redundancy/campaign.json").read_bytes()).hexdigest() != EXP12_SHA: raise RuntimeError("exp12 hash changed")
    if hashlib.sha256((root / "results/jrs_wa_qualification/campaign.json").read_bytes()).hexdigest() != EXP13_SHA: raise RuntimeError("exp13 hash changed")
    qualification = root / "results/jrs_wa_scip_qualification/verdict.json"
    if not qualification.exists() or not json.loads(qualification.read_text())["SCIP_FORMULATION_QUALIFIED"]:
        raise RuntimeError("exp13b did not authorize exp14")
    output = args.results.resolve()
    if output.exists() and not args.overwrite: raise RuntimeError(f"output already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    scales = (MAIN_SCALES[0], FIXED_SWEEP[0]) if args.quick else MAIN_SCALES + FIXED_SWEEP
    catalog, p0_rows, repeat_rows, candidate_rows, fault_rows, storage_rows, scale_rows, projection_rows = ([] for _ in range(8))
    frontier = False
    for scale in scales:
        scenario_started = time.perf_counter_ns()
        source = output / "scenarios" / f"{scale.scenario}.yaml"; source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(yaml_text(scale), encoding="utf-8")
        scenario_dir = compile_solver_only(source, output / "scenarios" / f"{scale.scenario}_solver")
        scenario = json.loads((scenario_dir / "scenario.json").read_text(encoding="utf-8"))
        audit = scenario_audit(scale)
        degrees = [4] * scale.switches
        audit.update({"scenario_sha256": scenario["scenario_sha256"], "workload_sha256": hashlib.sha256(repr(scenario["tt_flows"]).encode()).hexdigest(),
                      "min_degree": min(degrees), "p50_degree": statistics.median(degrees), "p95_degree": percentile(degrees, .95), "max_degree": max(degrees)})
        catalog.append(audit)
        p0_payloads = []
        repeat_count = 3 if scale.scenario in {"S1", "S2"} else 1
        for repeat in range(1, repeat_count + 1):
            work = output / "backend_results" / scale.scenario / f"P0_R{repeat}"
            request = {"scenario_path": str(scenario_dir / "scenario.json"), "disabled_links": [], "healthy_routes": {},
                       "affected_flow_ids": [f["id"] for f in scenario["tt_flows"]], "route_scope": "all-reroute",
                       "output_directory": str(work / "inputs")}
            payload, rss, elapsed = run_subprocess(root, request, work)
            write_json(work / "backend_result.json", payload)
            metrics = profile_metrics(payload, output / "profiles" / scale.scenario / f"P0_R{repeat}.json")
            p0_payloads.append((payload, rss, elapsed, metrics))
            repeat_rows.append({"scenario": scale.scenario, "repeat": repeat, "status": payload["status"],
                                "semantic_valid": payload.get("static_checker", {}).get("valid", False),
                                "profile_hash": metrics["profile_hash"]})
        p0, p0_rss, _, p0_metrics = p0_payloads[0]
        stats, timing = p0.get("statistics", {}), p0.get("timings_ms", {})
        routes = p0.get("logical_routes", [])
        candidates = discover_candidates(scenario, routes) if p0.get("feasible") and p0.get("static_checker", {}).get("valid") else []
        mode, selected = stratified_sample(candidates)
        bin_map = quantile_bin_map(candidates)
        selected_ids = {r["fault_id"] for r in selected}
        links = {l["id"]: l for l in scenario["links"]}
        for item in candidates:
            link = links[item["fault_id"]]
            candidate_rows.append({"scenario": scale.scenario, "fault_id": item["fault_id"],
                                   "endpoint_a": link["endpoint_a"], "endpoint_b": link["endpoint_b"],
                                   "affected_flow_count": item["affected_flow_count"],
                                   "affected_flow_ratio": item["affected_flow_count"] / scale.tt_flows,
                                   "healthy_route_use_count": item["affected_flow_count"], "sampling_bin": bin_map[item["fault_id"]],
                                   "selected_for_campaign": item["fault_id"] in selected_ids,
                                   "selection_reason": mode if item["fault_id"] in selected_ids else "NOT_SELECTED_STRATIFIED"})
        p0_rows.append({"scenario": scale.scenario, "switches": scale.switches, "end_systems": scale.end_systems,
                        "total_nodes": scale.switches + scale.end_systems, "tt_flows": scale.tt_flows,
                        "internal_links": len(topology_edges(scale.switches)), "avg_switch_degree": audit["average_switch_degree"],
                        "status": p0["status"], "conversion_ms": timing.get("input_conversion", ""),
                        "route_space_build_ms": timing.get("route_space_build", ""), "model_build_ms": timing.get("model_build", ""),
                        "solver_ms": timing.get("solver_wall", ""), "total_backend_ms": timing.get("total_backend", ""),
                        "vars": stats.get("num_variables", ""), "constraints": stats.get("num_constraints", ""),
                        "nonoverlap_constraints": stats.get("constraint_family_counts", {}).get("LINK_NON_OVERLAP", ""),
                        "peak_memory": stats.get("solver_memory_bytes", p0_rss or ""), "profile_bytes": p0_metrics["profile_bytes"],
                        "candidate_fault_count": len(candidates), "semantic_valid": p0.get("static_checker", {}).get("valid", False),
                        "P0_hash": p0_metrics["profile_hash"]})
        p0_available = p0.get("feasible") and p0.get("static_checker", {}).get("valid") and p0["status"] not in {"MEMORY_LIMIT", "MODEL_BUILD_ERROR", "INFEASIBLE", "TIME_LIMIT_NO_INCUMBENT"}
        if frontier: selected = []
        attempted = []
        healthy = {r["flow_id"]: r for r in routes}
        if p0_available:
            for item in selected:
                if (time.perf_counter_ns() - scenario_started) / 1e9 >= SCALE_BUDGET_S: break
                if attempted and sum(r["status"] in {"TIME_LIMIT_NO_INCUMBENT", "MEMORY_LIMIT"} for r in attempted) / len(attempted) >= .8: break
                fault_id = item["fault_id"]; work = output / "backend_results" / scale.scenario / fault_id
                request = {"scenario_path": str(scenario_dir / "scenario.json"), "disabled_links": [fault_id],
                           "healthy_routes": healthy, "affected_flow_ids": item["affected_flow_ids"],
                           "route_scope": "affected-only", "output_directory": str(work / "inputs")}
                payload, rss, _ = run_subprocess(root, request, work); write_json(work / "backend_result.json", payload)
                metrics = profile_metrics(payload, output / "profiles" / scale.scenario / f"{fault_id}.json")
                row = base_result_row(scale, fault_id, item["affected_flow_count"], len(candidates), payload, rss, metrics)
                attempted.append(row); fault_rows.append(row)
                write_json(output / "model_audits" / scale.scenario / f"{fault_id}.json", payload.get("statistics", {}))
        pressure = attempted and sum(r["status"] in {"TIME_LIMIT_NO_INCUMBENT", "MEMORY_LIMIT"} for r in attempted) / len(attempted) >= .8
        if pressure: frontier = True
        durations = [float(r["total_backend_ms"]) for r in attempted if r["total_backend_ms"] != ""]
        successful = [r for r in attempted if r["feasible"] and r["semantic_valid"]]
        profile_sizes = [p0_metrics["profile_bytes"]] + [int(r["profile_bytes"]) for r in successful]
        compressed_sizes = [p0_metrics["compressed_profile_bytes"]] + [int(r["compressed_profile_bytes"]) for r in successful]
        actual_pf = sum(durations)
        estimated = actual_pf
        estimated_capped = sum(TIMEOUT_S * 1000 if r["status"] == "TIME_LIMIT_NO_INCUMBENT" else float(r["total_backend_ms"]) for r in attempted)
        if mode != "FULL":
            estimated = estimated_capped = 0.0
            by_fault = {r["fault_id"]: r for r in attempted}
            for bin_id in range(5):
                population = [c for c in candidates if bin_map[c["fault_id"]] == bin_id]
                observed = [by_fault[c["fault_id"]] for c in population if c["fault_id"] in by_fault]
                if not observed: continue
                estimated += statistics.mean(float(r["total_backend_ms"]) for r in observed) * len(population)
                estimated_capped += statistics.mean(TIMEOUT_S * 1000 if r["status"] == "TIME_LIMIT_NO_INCUMBENT" else float(r["total_backend_ms"]) for r in observed) * len(population)
        status_counts = {status: sum(r["status"] == status for r in attempted) for status in
                         ("INFEASIBLE", "TIME_LIMIT_NO_INCUMBENT", "TIME_LIMIT_WITH_INCUMBENT", "MEMORY_LIMIT", "ERROR", "MODEL_BUILD_ERROR")}
        elapsed = (time.perf_counter_ns() - scenario_started) / 1e6
        scale_rows.append({"scenario": scale.scenario, "switches": scale.switches, "end_systems": scale.end_systems,
                           "total_nodes": scale.switches + scale.end_systems, "tt_flows": scale.tt_flows,
                           "candidate_faults": len(candidates), "campaign_mode": mode, "attempted_faults": len(attempted),
                           "successful_profiles": len(successful), "infeasible": status_counts["INFEASIBLE"],
                           "timeouts": status_counts["TIME_LIMIT_NO_INCUMBENT"] + status_counts["TIME_LIMIT_WITH_INCUMBENT"],
                           "memory_limits": status_counts["MEMORY_LIMIT"],
                           "errors": status_counts["ERROR"] + status_counts["MODEL_BUILD_ERROR"],
                           "timeout_rate": (status_counts["TIME_LIMIT_NO_INCUMBENT"] + status_counts["TIME_LIMIT_WITH_INCUMBENT"]) / max(len(attempted), 1),
                           "PF_solver_sum_ms": sum(float(r["solver_wall_ms"] or 0) for r in attempted), "PF_backend_sum_ms": actual_pf,
                           "actual_serial_elapsed_ms": elapsed, "estimated_serial_pf_ms": estimated if mode != "FULL" else "",
                           "estimated_timeout_capped_pf_ms": estimated_capped if mode != "FULL" else "",
                           "P0_plus_PF_ms": float(timing.get("total_backend", 0)) + actual_pf,
                           "mean_fault_ms": statistics.mean(durations) if durations else 0, "median_fault_ms": statistics.median(durations) if durations else 0,
                           "p95_fault_ms": percentile(durations, .95), "max_fault_ms": max(durations, default=0),
                           "mean_peak_memory": statistics.mean([float(r["peak_solver_memory_bytes"]) for r in attempted if r["peak_solver_memory_bytes"] != ""]) if attempted else 0,
                           "max_peak_memory": max([float(r["peak_solver_memory_bytes"]) for r in attempted if r["peak_solver_memory_bytes"] != ""] or [0]),
                           "total_profile_store_bytes": sum(profile_sizes), "compressed_profile_store_bytes": sum(compressed_sizes),
                           "stop_reason": "P0_NOT_AVAILABLE" if not p0_available else "SCALABILITY_FRONTIER_REACHED" if frontier else "CAMPAIGN_COMPLETE"})
        storage_rows.append({"scenario": scale.scenario, "P0_bytes": p0_metrics["profile_bytes"],
                             "successful_PF_profile_count": len(successful), "PF_profile_bytes_total": sum(int(r["profile_bytes"]) for r in successful),
                             "P0_plus_PF_bytes_total": sum(profile_sizes), "compressed_total": sum(compressed_sizes),
                             "mean_profile_bytes": statistics.mean(profile_sizes), "median_profile_bytes": statistics.median(profile_sizes),
                             "p95_profile_bytes": percentile(profile_sizes, .95), "max_profile_bytes": max(profile_sizes)})
        for workers in (1, 2, 4, 8, 16, 32):
            makespan = lpt_projection(durations, workers)
            projection_rows.append({"scenario": scale.scenario, "worker_count": workers, "serial_work_ms": actual_pf,
                                    "idealized_makespan_ms": makespan, "idealized_speedup": actual_pf / makespan if makespan else 0})
    write_csv(output / "scenario_catalog.csv", catalog); write_csv(output / "p0_results.csv", p0_rows)
    write_csv(output / "p0_repeatability.csv", repeat_rows)
    write_csv(output / "candidate_faults.csv", candidate_rows,
              ["scenario","fault_id","endpoint_a","endpoint_b","affected_flow_count","affected_flow_ratio",
               "healthy_route_use_count","sampling_bin","selected_for_campaign","selection_reason"])
    write_csv(output / "per_fault_results.csv", fault_rows, ["scenario_id","scale_id","fault_id","affected_flow_count","total_tt_count","candidate_fault_count","status","feasible","input_conversion_ms","route_space_build_ms","model_build_ms","solver_wall_ms","solution_extract_ms","profile_serialize_ms","total_backend_ms","timeout_s","model_total_vars","routing_binary_vars","ordering_binary_vars","timing_integer_vars","total_constraints","nonoverlap_constraints","SCIP_raw_status","semantic_valid","profile_hash","profile_bytes","compressed_profile_bytes","peak_solver_memory_bytes","subprocess_peak_rss_bytes","memory_measurement_method"])
    write_csv(output / "profile_storage.csv", storage_rows); write_csv(output / "scale_summary.csv", scale_rows)
    fixed_rows = [{"tt_flows": row["tt_flows"], "P0_status": next(p["status"] for p in p0_rows if p["scenario"] == row["scenario"]),
                   "P0_solver_time": next(p["solver_ms"] for p in p0_rows if p["scenario"] == row["scenario"]),
                   "P0_vars": next(p["vars"] for p in p0_rows if p["scenario"] == row["scenario"]),
                   "P0_constraints": next(p["constraints"] for p in p0_rows if p["scenario"] == row["scenario"]),
                   "candidate_faults": row["candidate_faults"], "PF_campaign_mode": row["campaign_mode"],
                   "median_PF_time": row["median_fault_ms"], "p95_PF_time": row["p95_fault_ms"], "timeout_rate": row["timeout_rate"],
                   "serial_work": row["PF_backend_sum_ms"], "profile_store_bytes": row["total_profile_store_bytes"],
                   "peak_solver_memory": row["max_peak_memory"]} for row in scale_rows if row["scenario"].startswith("F150")]
    write_csv(output / "fixed_topology_tt_sweep.csv", fixed_rows); write_csv(output / "parallelism_projection.csv", projection_rows)
    timeout_any = any(float(r["timeout_rate"]) > 0 or r["stop_reason"] != "CAMPAIGN_COMPLETE" for r in scale_rows)
    storage_max = max((int(r["total_profile_store_bytes"]) for r in scale_rows), default=0)
    compute = timeout_any; storage = storage_max >= 100 * 1024 * 1024
    verdict = "COMPUTE_AND_STORAGE_PRESSURE" if compute and storage else "COMPUTE_PRESSURE_OBSERVED" if compute else "STORAGE_PRESSURE_OBSERVED" if storage else "NO_CLEAR_BOTTLENECK"
    summary = "# exp14 PF JRS-WA scalability diagnosis\n\n" + f"Research Direction Assessment: **{verdict}**.\n\n" + \
              f"Observed {len(scale_rows)} scales, {len(fault_rows)} PF solves, maximum stored profile set {storage_max} bytes. " + \
              "BE=0 intentionally isolates offline JRS-WA synthesis. No OMNeT++ run and no figures were produced. " + \
              "Parallel rows are deterministic idealized LPT projections, not measured parallel benchmarks.\n"
    (output / "summary.md").write_text(summary, encoding="utf-8")
    import pyscipopt
    from pyscipopt import Model
    m = Model(); scip_version = f"{m.getMajorVersion()}.{m.getMinorVersion()}.{m.getTechVersion()}"
    environment = {"python": sys.version.split()[0], "platform": platform.platform(), "pyscipopt": pyscipopt.__version__,
                   "scip": scip_version, "threads": SCIP_THREADS, "seed": SCIP_SEED, "timeout_s": TIMEOUT_S,
                   "memory_limit_mb": SCIP_MEMORY_LIMIT_MB,
                   "scale_budget_s": SCALE_BUDGET_S, "omnet_invocations": 0, "plot_artifacts": 0}
    write_json(output / "environment.json", environment)
    files = sorted(p for p in output.rglob("*") if p.is_file() and p.name != "analysis_manifest.json")
    write_json(output / "analysis_manifest.json", {"schema_version": 1, "experiment": "exp14_pf_jrs_scalability",
               "run_id": args.run_id, "implementation_commit": args.implementation_commit, "results_commit": args.results_commit,
               "SCIP_version": scip_version, "PySCIPOpt_version": pyscipopt.__version__, "Python_version": sys.version.split()[0],
               "TSNKit_version": TSNKIT_VERSION, "TSNKit_commit": TSNKIT_COMMIT,
               "solver_params": {"threads": 1, "timeout_s": TIMEOUT_S, "memory_limit_mb": SCIP_MEMORY_LIMIT_MB,
                                 "seed": SCIP_SEED, "parallel_mode": 0},
               "scenario_generator_version": GENERATOR_VERSION,
               "scenario_SHAs": {r["scenario"]: r["scenario_sha256"] for r in catalog},
               "workload_SHAs": {r["scenario"]: r["workload_sha256"] for r in catalog},
               "campaign_modes": {r["scenario"]: r["campaign_mode"] for r in scale_rows},
               "sampling_rules": {"full_threshold": 128, "quantile_bins": 5, "per_bin": 8, "maximum": 40},
               "verdict": verdict, "artifact_SHA256": {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
    return 0


if __name__ == "__main__": raise SystemExit(main())
