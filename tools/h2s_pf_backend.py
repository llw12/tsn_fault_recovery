"""Affected-only per-failure adapter for the pinned H2S/CELF backend."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import deque
from pathlib import Path
from typing import Any

from tools.h2s_jrs_backend import (
    DEFAULT_CANDIDATE_PATHS, DEFAULT_QUANTUM_NS, FORMAL_MEMORY_LIMIT_MB,
    FORMAL_SEED, FORMAL_THREADS, H2sAdapterError, H2sJrsBackend,
    check_h2s_pf_solution, normalize_schedule, prepare_h2s_inputs, route_metrics,
)
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest, RecoverySynthesisResult


def route_index(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {route["flow_id"]: route for route in profile.get("logical_routes", [])}


def reachable_nodes(scenario: dict[str, Any], disabled_links: set[str], source: str) -> set[str]:
    adjacency: dict[str, list[str]] = {node["id"]: [] for node in scenario["nodes"]}
    for link in scenario["links"]:
        if link["id"] in disabled_links:
            continue
        a, b = link["endpoint_a"], link["endpoint_b"]
        adjacency[a].append(b); adjacency[b].append(a)
    reached = {source}; pending = deque([source])
    while pending:
        node = pending.popleft()
        for neighbor in adjacency[node]:
            if neighbor not in reached:
                reached.add(neighbor); pending.append(neighbor)
    return reached


def semantic_profile_hash(profile: dict[str, Any]) -> str:
    semantic = {key: profile[key] for key in (
        "forwarding_model", "logical_routes", "stream_forwarding",
        "release_offsets_ns", "gate_schedules", "schedule_windows")}
    return hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()


class H2sPfBackend(H2sJrsBackend):
    name = "h2s-pf"

    def __init__(self, executable: Path, *, quantum_ns: int = DEFAULT_QUANTUM_NS,
                 candidate_paths: int = DEFAULT_CANDIDATE_PATHS,
                 memory_limit_mb: int = FORMAL_MEMORY_LIMIT_MB):
        super().__init__(executable, quantum_ns=quantum_ns,
                         candidate_paths=candidate_paths, memory_limit_mb=memory_limit_mb)

    def synthesize(self, request: RecoverySynthesisRequest) -> RecoverySynthesisResult:
        started = time.perf_counter_ns()
        if request.forwarding_model != "stream-aware" or request.route_scope not in {"affected-only", "all-reroute"}:
            return RecoverySynthesisResult(self.name, BackendStatus.UNSUPPORTED,
                                           diagnostic="PF requires stream-aware affected-only or all-reroute mode")
        if len(request.disabled_links) != 1 or not request.healthy_primary_routes:
            return RecoverySynthesisResult(self.name, BackendStatus.INVALID_INPUT,
                                           diagnostic="PF requires one physical fault and healthy P0 routes")
        try:
            scenario = json.loads(request.scenario_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return RecoverySynthesisResult(self.name, BackendStatus.INVALID_INPUT, diagnostic=str(error))
        flows = {flow["id"]: flow for flow in scenario.get("tt_flows", [])}
        affected = set(request.affected_flow_ids)
        if not affected or affected > set(flows):
            return RecoverySynthesisResult(self.name, BackendStatus.INVALID_INPUT,
                                           diagnostic="affected flow set is empty or unknown")
        disabled = set(request.disabled_links)
        disconnected = [fid for fid in sorted(affected)
                        if flows[fid]["destination"] not in reachable_nodes(scenario, disabled, flows[fid]["source"])]
        if disconnected:
            return RecoverySynthesisResult(self.name, BackendStatus.STRUCTURAL_NO_ROUTE,
                statistics={"structurally_disconnected_flows": disconnected,
                            "affected_flow_count": len(affected), "semantic_valid": False},
                timings_ms={"total_backend": (time.perf_counter_ns() - started) / 1e6},
                diagnostic="failed topology disconnects at least one affected TT flow")
        output = request.output_directory or request.scenario_path.parent / "h2s_pf_input"
        conversion_started = time.perf_counter_ns()
        try:
            prepared = prepare_h2s_inputs(
                request.scenario_path, output, self.quantum_ns,
                disabled_links=request.disabled_links,
                healthy_primary_routes=request.healthy_primary_routes,
                affected_flow_ids=request.affected_flow_ids, route_scope=request.route_scope)
        except (H2sAdapterError, KeyError, TypeError, json.JSONDecodeError) as error:
            return RecoverySynthesisResult(self.name, BackendStatus.INVALID_INPUT, diagnostic=str(error))
        conversion_ms = (time.perf_counter_ns() - conversion_started) / 1e6
        attempts: list[dict[str, Any]] = []; invalid = False; resource_statuses = []
        for algorithm in ("H2S", "CELF"):
            run_status, raw, meta = self._run(prepared, algorithm, request.solver_timeout_s)
            attempts.append({"algorithm": algorithm, **meta})
            if run_status is not None:
                resource_statuses.append(run_status)
                invalid |= run_status == BackendStatus.OUTPUT_INVALID
                continue
            normalization_started = time.perf_counter_ns()
            try:
                normalized = normalize_schedule(prepared, raw, self.quantum_ns)
                normalization_ms = (time.perf_counter_ns() - normalization_started) / 1e6
                verification_started = time.perf_counter_ns()
                checker = check_h2s_pf_solution(prepared, normalized)
                verification_ms = (time.perf_counter_ns() - verification_started) / 1e6
            except H2sAdapterError as error:
                attempts[-1]["normalization_error"] = str(error); invalid = True; continue
            all_scheduled = raw["scheduled_flow_count"] == raw["requested_flow_count"] == len(prepared.flow_map)
            attempts[-1].update({"scheduled_flow_count": raw["scheduled_flow_count"],
                "requested_flow_count": raw["requested_flow_count"],
                "upstream_verifier_pass": raw["upstream_verifier_pass"],
                "project_static_checker_pass": checker["valid"]})
            if all_scheduled and raw["upstream_verifier_pass"] and checker["valid"]:
                status = BackendStatus.SUCCESS_H2S if algorithm == "H2S" else BackendStatus.SUCCESS_CELF_FALLBACK
                profile = normalized["profile"]
                fault_id = request.disabled_links[0]
                profile.update({"profile_id": f"PF_{fault_id}", "fault_id": fault_id,
                    "disabled_physical_links": [fault_id], "route_scope": request.route_scope,
                    "affected_flow_ids": sorted(affected), "backend": self.name})
                profile["semantic_profile_hash"] = semantic_profile_hash(profile)
                candidates = [int(value) for value in raw.get("candidate_path_counts", {}).values()]
                stats = {"algorithm_used": algorithm, "primary_h2s_success": algorithm == "H2S",
                    "celf_fallback_used": algorithm == "CELF", "all_flows_scheduled": True,
                    "scheduled_flow_count": len(prepared.flow_map), "requested_flow_count": len(prepared.flow_map),
                    "scheduled_flow_ratio": 1.0, "semantic_valid": True,
                    "upstream_verifier_pass": True, "project_static_checker_pass": True,
                    "semantic_checks": checker, "attempts": attempts, "affected_flow_count": len(affected),
                    "unaffected_flow_count": len(prepared.flow_map) - len(affected),
                    "route_scope": request.route_scope,
                    "backend_quantum_ns": self.quantum_ns, "candidate_path_count": self.candidate_paths,
                    "routing_algorithm": "DIJKSTRA_OVERLAP", "seed": FORMAL_SEED,
                    "threads": FORMAL_THREADS, "memory_limit_mb": self.memory_limit_mb,
                    "mean_candidate_paths_per_flow": statistics.mean(candidates) if candidates else 0,
                    "min_candidate_paths": min(candidates, default=0), "max_candidate_paths": max(candidates, default=0),
                    "candidate_route_generation_ms": float(raw.get("candidate_route_generation_seconds", 0)) * 1000,
                    "route_schedule": normalized["route_schedule"], "quantization_audit": prepared.quantization_rows,
                    **route_metrics(normalized)}
                return RecoverySynthesisResult(self.name, status, feasible=True,
                    logical_routes=normalized["logical_routes"], schedule_windows=normalized["schedule_windows"],
                    profile=profile, statistics=stats,
                    timings_ms={"conversion": conversion_ms,
                        "candidate_route_generation": float(raw.get("candidate_route_generation_seconds", 0)) * 1000,
                        "scheduling": max(0.0, meta["wall_ms"] - float(raw.get("candidate_route_generation_seconds", 0)) * 1000),
                        "verification": verification_ms, "profile_normalization": normalization_ms,
                        "h2s_wall": attempts[0]["wall_ms"],
                        "celf_wall": attempts[1]["wall_ms"] if len(attempts) > 1 else 0,
                        "total_backend": (time.perf_counter_ns() - started) / 1e6})
            invalid |= all_scheduled and not checker["valid"]
        if BackendStatus.MEMORY_LIMIT in resource_statuses:
            status = BackendStatus.MEMORY_LIMIT
        elif BackendStatus.TIME_LIMIT in resource_statuses:
            status = BackendStatus.TIME_LIMIT
        elif invalid:
            status = BackendStatus.OUTPUT_INVALID
        elif all(a.get("requested_flow_count") and a.get("scheduled_flow_count", 0) == 0 for a in attempts):
            status = BackendStatus.CANDIDATE_ROUTE_FAILURE
        else:
            status = BackendStatus.HEURISTIC_NOT_FOUND
        ratios = [a.get("scheduled_flow_count", 0) / max(a.get("requested_flow_count", len(prepared.flow_map)), 1)
                  for a in attempts]
        return RecoverySynthesisResult(self.name, status,
            statistics={"attempts": attempts, "affected_flow_count": len(affected),
                        "scheduled_flow_ratio": max(ratios, default=0), "semantic_valid": False},
            timings_ms={"conversion": conversion_ms, "total_backend": (time.perf_counter_ns() - started) / 1e6},
            diagnostic="H2S and CELF did not produce an all-flow static-valid PF schedule")
