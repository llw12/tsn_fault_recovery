"""Adapter and independent semantic checker for the pinned H2S backend."""

from __future__ import annotations

import hashlib
import json
import math
import os
import resource
import signal
import statistics
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.jrs_wa_adapter import canonical_json_bytes, compile_gate_schedules, deterministic_stream_handles, seconds_to_ns
from tools.recovery_backend import BackendStatus, RecoverySynthesisBackend, RecoverySynthesisRequest, RecoverySynthesisResult

UPSTREAM_REPOSITORY = "https://github.com/gepperho/AdvancedFlowScheduler.git"
UPSTREAM_COMMIT = "650a9665e7bafb70fcf19c9f0a247e1d7b885ffd"
UPSTREAM_LICENSE = "Apache-2.0"
DEFAULT_QUANTUM_NS = 100
DEFAULT_CANDIDATE_PATHS = 5
FORMAL_SEED = 1024
FORMAL_THREADS = 1
FORMAL_MEMORY_LIMIT_MB = 8192
OUTPUT_MARKER = "H2S_SCHEDULE_JSON:"


class H2sAdapterError(ValueError):
    pass


def ceil_div(value: int, divisor: int) -> int:
    if value < 0 or divisor <= 0:
        raise H2sAdapterError("ceil_div requires a nonnegative value and positive divisor")
    return (value + divisor - 1) // divisor


def quantize_flow(flow: dict[str, Any], overhead_bytes: int, bitrate_bps: int, quantum_ns: int) -> dict[str, Any]:
    if quantum_ns not in {100, 1000}:
        raise H2sAdapterError("supported backend quantum is 100 ns or 1 us")
    if bitrate_bps != 1_000_000_000:
        raise H2sAdapterError("H2S qualification supports uniform 1 Gbps links only")
    period_ns = seconds_to_ns(flow["period_s"], f"{flow['id']}.period")
    release_ns = seconds_to_ns(flow["release_offset_s"], f"{flow['id']}.release")
    deadline_ns = seconds_to_ns(flow["schedule_deadline_budget_s"], f"{flow['id']}.deadline")
    if period_ns % quantum_ns:
        raise H2sAdapterError(f"{flow['id']} period is not exactly representable at {quantum_ns} ns")
    period_ticks = period_ns // quantum_ns
    release_ticks = ceil_div(release_ns, quantum_ns)
    absolute_deadline_ns = release_ns + deadline_ns
    absolute_deadline_ticks = absolute_deadline_ns // quantum_ns
    deadline_ticks = absolute_deadline_ticks - release_ticks
    if deadline_ticks <= 0:
        raise H2sAdapterError(f"{flow['id']} has no representable scheduling window")
    wire_bytes = int(flow["packet_size_bytes"]) + overhead_bytes
    tx_ns = ceil_div(wire_bytes * 8_000_000_000, bitrate_bps)
    tx_ticks = ceil_div(tx_ns, quantum_ns)
    # Upstream computes floor(package_size * 8 / 1000).  A multiple of 125
    # bytes is therefore an exact conservative encoding of tx_ticks.
    upstream_equivalent_bytes = tx_ticks * 125
    release_quantized_ns = release_ticks * quantum_ns
    deadline_quantized_abs_ns = absolute_deadline_ticks * quantum_ns
    return {
        "flow_id": flow["id"], "period_ns": period_ns, "release_ns": release_ns,
        "deadline_ns": deadline_ns, "backend_quantum_ns": quantum_ns,
        "period_ticks": period_ticks, "release_ticks": release_ticks,
        "deadline_ticks": deadline_ticks, "period_error_ns": 0,
        "release_error_ns": release_quantized_ns - release_ns,
        "deadline_error_ns": deadline_quantized_abs_ns - absolute_deadline_ns,
        "tx_ns": tx_ns, "tx_ticks": tx_ticks,
        "tx_padding_ns": tx_ticks * quantum_ns - tx_ns,
        "on_wire_bytes": wire_bytes, "upstream_equivalent_bytes": upstream_equivalent_bytes,
        "absolute_deadline_ticks": absolute_deadline_ticks,
        "max_quantization_error_ns": max(release_quantized_ns - release_ns,
                                           absolute_deadline_ns - deadline_quantized_abs_ns,
                                           tx_ticks * quantum_ns - tx_ns),
    }


@dataclass(frozen=True)
class H2sPreparedInputs:
    scenario: dict[str, Any]
    node_map: dict[str, int]
    reverse_node_map: dict[int, str]
    flow_map: dict[str, int]
    reverse_flow_map: dict[int, str]
    arc_to_link: dict[tuple[int, int], str]
    topology_path: Path
    scenario_path: Path
    quantization_rows: list[dict[str, Any]]
    port_map: dict[str, Any] | None


def prepare_h2s_inputs(scenario_path: Path, output_directory: Path, quantum_ns: int = DEFAULT_QUANTUM_NS) -> H2sPreparedInputs:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    required = {"scenario_name", "forwarding_model", "nodes", "links", "tt_flows", "scheduling", "simulation"}
    missing = required - scenario.keys()
    if missing:
        raise H2sAdapterError(f"canonical scenario missing fields: {sorted(missing)}")
    if scenario["forwarding_model"] != "stream-aware":
        raise H2sAdapterError("H2S qualification requires stream-aware forwarding")
    if scenario.get("be_flows"):
        raise H2sAdapterError("exp15 is P0 TT-only; BE flow synthesis is unsupported")
    cycle_ns = seconds_to_ns(scenario["simulation"]["cycle_time_s"], "cycle")
    if cycle_ns % quantum_ns:
        raise H2sAdapterError("cycle is not exactly representable at backend quantum")
    nodes = sorted(node["id"] for node in scenario["nodes"])
    if len(nodes) != len(set(nodes)):
        raise H2sAdapterError("duplicate node ID")
    node_map = {node: index for index, node in enumerate(nodes)}
    reverse_node_map = {index: node for node, index in node_map.items()}
    arc_to_link: dict[tuple[int, int], str] = {}
    edge_rows = []
    for link in sorted(scenario["links"], key=lambda item: item["id"]):
        if int(link["bitrate_bps"]) != 1_000_000_000:
            raise H2sAdapterError("all links must be 1 Gbps")
        if seconds_to_ns(link["propagation_delay_s"], f"{link['id']}.propagation") != 0:
            raise H2sAdapterError("exp15 patch is qualified only for zero propagation delay")
        a, b = node_map[link["endpoint_a"]], node_map[link["endpoint_b"]]
        if a == b or (a, b) in arc_to_link or (b, a) in arc_to_link:
            raise H2sAdapterError("parallel links and self-loops are unsupported")
        arc_to_link[(a, b)] = arc_to_link[(b, a)] = link["id"]
        edge_rows.append(f"{a} {b}\n")
    flows = sorted(scenario["tt_flows"], key=lambda item: item["id"])
    if not flows:
        raise H2sAdapterError("at least one TT flow is required")
    flow_map = {flow["id"]: index for index, flow in enumerate(flows)}
    if len(flow_map) != len(flows):
        raise H2sAdapterError("duplicate flow ID")
    overhead = int(scenario["scheduling"]["frame_overhead_bytes"])
    qrows = [quantize_flow(flow, overhead, 1_000_000_000, quantum_ns) for flow in flows]
    q_by_id = {row["flow_id"]: row for row in qrows}
    upstream_flows = []
    for flow in flows:
        row = q_by_id[flow["id"]]
        upstream_flows.append({
            "flowID": flow_map[flow["id"]], "package size": row["upstream_equivalent_bytes"],
            "period": row["period_ticks"], "release offset": row["release_ticks"],
            "deadline": row["deadline_ticks"], "source": node_map[flow["source"]],
            "destination": node_map[flow["destination"]], "propagation delay": 0,
            "processing delay": 0, "fixed release": True,
        })
    output_directory.mkdir(parents=True, exist_ok=True)
    topology_path = output_directory / "network.txt"
    upstream_scenario_path = output_directory / "scenario.json"
    topology_path.write_text("".join(edge_rows), encoding="utf-8")
    upstream_scenario_path.write_bytes(canonical_json_bytes({
        "time_steps": [{"time": 0, "removeFlows": [], "addFlows": upstream_flows}]
    }))
    port_path = scenario_path.parent / "port_map.json"
    port_map = json.loads(port_path.read_text(encoding="utf-8")) if port_path.is_file() else None
    manifest = {
        "upstream_repository": UPSTREAM_REPOSITORY, "upstream_commit": UPSTREAM_COMMIT,
        "upstream_license": UPSTREAM_LICENSE, "backend_quantum_ns": quantum_ns,
        "node_map": node_map, "flow_map": flow_map,
        "stream_handles": deterministic_stream_handles(list(flow_map)),
        "topology_sha256": hashlib.sha256(topology_path.read_bytes()).hexdigest(),
        "scenario_sha256": hashlib.sha256(upstream_scenario_path.read_bytes()).hexdigest(),
        "quantization": qrows,
    }
    (output_directory / "input_manifest.json").write_bytes(canonical_json_bytes(manifest))
    return H2sPreparedInputs(scenario, node_map, reverse_node_map, flow_map,
                             {v: k for k, v in flow_map.items()}, arc_to_link,
                             topology_path, upstream_scenario_path, qrows, port_map)


def parse_backend_output(text: str) -> dict[str, Any]:
    markers = [line[len(OUTPUT_MARKER):] for line in text.splitlines() if line.startswith(OUTPUT_MARKER)]
    if len(markers) != 1:
        raise H2sAdapterError(f"expected exactly one schedule marker, found {len(markers)}")
    value = json.loads(markers[0])
    for key in ("requested_flow_count", "scheduled_flow_count", "hyper_cycle_ticks", "slots", "upstream_verifier_pass"):
        if key not in value:
            raise H2sAdapterError(f"backend output missing {key}")
    return value


def _egress_path(prepared: H2sPreparedInputs, link_id: str, source: str) -> str:
    if prepared.port_map:
        for side in ("a", "b"):
            binding = prepared.port_map["links"][link_id][side]
            if binding["node"] == source:
                return binding["egress_path"]
    return f"{source}->{link_id}"


def normalize_schedule(prepared: H2sPreparedInputs, raw: dict[str, Any], quantum_ns: int) -> dict[str, Any]:
    flows = {flow["id"]: flow for flow in prepared.scenario["tt_flows"]}
    qrows = {row["flow_id"]: row for row in prepared.quantization_rows}
    by_flow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    route_schedule = []
    for slot in raw["slots"]:
        try:
            flow_id = prepared.reverse_flow_map[int(slot["flow_id"])]
            source = prepared.reverse_node_map[int(slot["source"])]
            destination = prepared.reverse_node_map[int(slot["destination"])]
            link_id = prepared.arc_to_link[(int(slot["source"]), int(slot["destination"]))]
        except (KeyError, ValueError, TypeError) as error:
            raise H2sAdapterError(f"unknown ID in backend slot: {slot}") from error
        row = {
            "flow_id": flow_id, "logical_link": link_id, "source": source, "destination": destination,
            "start_ns": int(slot["start_tick"]) * quantum_ns,
            "end_ns": int(slot["end_tick"]) * quantum_ns,
            "queue_id": int(slot["queue_id"]), "config_id": int(slot["config_id"]),
        }
        by_flow[flow_id].append(row); route_schedule.append(row)
    logical_routes, windows = [], []
    switch_ids = {node["id"] for node in prepared.scenario["nodes"] if node["type"] == "switch"}
    for flow_id, rows in sorted(by_flow.items()):
        ordered = sorted(rows, key=lambda row: (row["start_ns"], row["queue_id"]))
        node_path = [ordered[0]["source"]] + [row["destination"] for row in ordered]
        logical_routes.append({"flow_id": flow_id, "node_path": node_path,
                               "link_path": [row["logical_link"] for row in ordered]})
        for hop, row in enumerate(ordered):
            row["hop_index"] = hop
            if row["source"] in switch_ids:
                windows.append({"flow_id": flow_id, "hop_index": hop, "logical_link": row["logical_link"],
                                "egress_path": _egress_path(prepared, row["logical_link"], row["source"]),
                                "start_ns": row["start_ns"], "end_ns": row["end_ns"]})
    cycle_ns = int(raw["hyper_cycle_ticks"]) * quantum_ns
    tt_class = int(prepared.scenario["tt_flows"][0].get("traffic_class", 1))
    be_class = int(prepared.scenario["scheduling"].get("be_traffic_class", 0))
    gate_schedules = compile_gate_schedules(windows, cycle_ns, tt_class, be_class) if windows else []
    handles = deterministic_stream_handles(list(flows))
    profile = {
        "profile_id": "P0", "forwarding_model": "stream-aware", "logical_routes": logical_routes,
        "stream_forwarding": [{"flow_id": fid, "stream_handle": handles[fid],
                                "destination": flows[fid]["destination"]} for fid in sorted(flows)],
        "release_offsets_ns": {fid: seconds_to_ns(flows[fid]["release_offset_s"], f"{fid}.release") for fid in sorted(flows)},
        "gate_schedules": gate_schedules, "schedule_windows": windows,
    }
    return {"logical_routes": logical_routes, "schedule_windows": windows, "profile": profile,
            "route_schedule": route_schedule, "quantization": list(qrows.values())}


def check_h2s_solution(prepared: H2sPreparedInputs, normalized: dict[str, Any]) -> dict[str, Any]:
    failures, checks = [], []
    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})
        if not passed: failures.append(f"{name}: {detail}")
    scenario = prepared.scenario
    flows = {flow["id"]: flow for flow in scenario["tt_flows"]}
    routes = {route["flow_id"]: route for route in normalized["logical_routes"]}
    schedules: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized["route_schedule"]: schedules[row["flow_id"]].append(row)
    qrows = {row["flow_id"]: row for row in prepared.quantization_rows}
    record("ALL_TT_FLOWS_SCHEDULED", set(schedules) == set(flows), f"{len(schedules)}/{len(flows)}")
    record("ALL_TT_ROUTES_PRESENT", set(routes) == set(flows), f"{len(routes)}/{len(flows)}")
    intervals: dict[tuple[str, str], list[tuple[int, int, str]]] = defaultdict(list)
    cycle_ns = seconds_to_ns(scenario["simulation"]["cycle_time_s"], "cycle")
    continuity_ok = loop_ok = links_ok = release_ok = precedence_ok = waiting_ok = deadline_ok = bounds_ok = duration_ok = duplicates_ok = True
    for flow_id, flow in flows.items():
        route = routes.get(flow_id, {}); rows = sorted(schedules.get(flow_id, []), key=lambda r: (r["start_ns"], r["queue_id"]))
        nodes, link_path = route.get("node_path", []), route.get("link_path", [])
        continuity_ok &= bool(nodes) and len(nodes) == len(link_path) + 1 and nodes[0] == flow["source"] and nodes[-1] == flow["destination"]
        loop_ok &= len(nodes) == len(set(nodes))
        duplicates_ok &= (len(nodes) == len(set(nodes)) and
                          len(link_path) == len(set(zip(nodes, nodes[1:]))) and
                          len({row["config_id"] for row in rows}) <= 1)
        links_ok &= len(rows) == len(link_path) and all(row["logical_link"] in {link["id"] for link in scenario["links"]} for row in rows)
        release_ns = seconds_to_ns(flow["release_offset_s"], f"{flow_id}.release")
        quantized_release = qrows[flow_id]["release_ticks"] * qrows[flow_id]["backend_quantum_ns"]
        release_ok &= bool(rows) and rows[0]["start_ns"] == quantized_release and rows[0]["start_ns"] >= release_ns
        for left, right in zip(rows, rows[1:]):
            precedence_ok &= right["start_ns"] >= left["end_ns"]
            waiting_ok &= right["start_ns"] - left["end_ns"] >= 0
        deadline_abs = release_ns + seconds_to_ns(flow["schedule_deadline_budget_s"], f"{flow_id}.deadline")
        deadline_ok &= bool(rows) and rows[-1]["end_ns"] <= deadline_abs
        bounds_ok &= all(0 <= row["start_ns"] < row["end_ns"] <= cycle_ns for row in rows)
        duration_ok &= all(row["end_ns"] - row["start_ns"] == qrows[flow_id]["tx_ticks"] * qrows[flow_id]["backend_quantum_ns"] and
                           row["end_ns"] - row["start_ns"] >= qrows[flow_id]["tx_ns"] for row in rows)
        for row in rows: intervals[(row["logical_link"], row["source"])].append((row["start_ns"], row["end_ns"], flow_id))
    overlap_ok = True
    for values in intervals.values():
        ordered = sorted(values)
        overlap_ok &= all(right[0] >= left[1] for left, right in zip(ordered, ordered[1:]))
    quant_safe = all(row["release_error_ns"] >= 0 and row["deadline_error_ns"] <= 0 and row["tx_padding_ns"] >= 0 for row in qrows.values())
    for name, passed in (("SOURCE_DESTINATION_CORRECT", continuity_ok), ("ROUTE_CONTINUOUS", continuity_ok),
                         ("ROUTE_NO_LOOP", loop_ok), ("ROUTE_USES_EXISTING_LINKS", links_ok),
                         ("SOURCE_FIXED_RELEASE", release_ok), ("HOP_PRECEDENCE", precedence_ok),
                         ("WAIT_NONNEGATIVE", waiting_ok), ("SAME_EGRESS_NON_OVERLAP", overlap_ok),
                         ("END_TO_END_DEADLINE", deadline_ok), ("WINDOWS_INSIDE_CYCLE", bounds_ok),
                         ("FRAME_SERIALIZATION_DURATION", duration_ok), ("QUANTIZATION_SAFE", quant_safe),
                         ("NO_DUPLICATE_DISCONNECTED_SEGMENTS", duplicates_ok)):
        record(name, passed, "PASS" if passed else "violation")
    return {"valid": not failures, "failure_count": len(failures), "failures": failures, "checks": checks}


def percentile(values: list[float], fraction: float) -> float:
    if not values: return 0.0
    ordered = sorted(values); return ordered[math.ceil(fraction * len(ordered)) - 1]


def route_metrics(normalized: dict[str, Any]) -> dict[str, Any]:
    hops = [len(route["link_path"]) for route in normalized["logical_routes"]]
    waits = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalized["route_schedule"]: grouped[row["flow_id"]].append(row)
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda row: row["hop_index"])
        waits.extend(right["start_ns"] - left["end_ns"] for left, right in zip(ordered, ordered[1:]))
    return {"mean_hops": statistics.mean(hops) if hops else 0, "median_hops": statistics.median(hops) if hops else 0,
            "p95_hops": percentile(hops, .95), "max_hops": max(hops, default=0),
            "mean_wait_ns": statistics.mean(waits) if waits else 0, "median_wait_ns": statistics.median(waits) if waits else 0,
            "p95_wait_ns": percentile(waits, .95), "max_wait_ns": max(waits, default=0)}


class H2sJrsBackend(RecoverySynthesisBackend):
    name = "h2s-jrs"

    def __init__(self, executable: Path, *, quantum_ns: int = DEFAULT_QUANTUM_NS,
                 candidate_paths: int = DEFAULT_CANDIDATE_PATHS, memory_limit_mb: int = FORMAL_MEMORY_LIMIT_MB):
        self.executable = Path(executable); self.quantum_ns = quantum_ns
        self.candidate_paths = candidate_paths; self.memory_limit_mb = memory_limit_mb

    def _run(self, prepared: H2sPreparedInputs, algorithm: str, timeout_s: int) -> tuple[BackendStatus | None, dict[str, Any] | None, dict[str, Any]]:
        command = [str(self.executable), "-n", str(prepared.topology_path),
                   "-s", str(prepared.scenario_path), "-a", algorithm, "--routing", "DIJKSTRA_OVERLAP",
                   "--candidate-paths", str(self.candidate_paths), "-p", "0", "--verify-schedule", "-r"]
        def limits() -> None:
            limit = self.memory_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        started = time.perf_counter_ns(); deadline = time.monotonic() + timeout_s
        process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   preexec_fn=limits, env={**os.environ, "OMP_NUM_THREADS": "1"}, start_new_session=True)
        peak_rss_bytes = 0; timed_out = memory_killed = False
        stdout = stderr = ""
        while True:
            try:
                status_text = Path(f"/proc/{process.pid}/status").read_text(encoding="utf-8")
                rss_line = next((line for line in status_text.splitlines() if line.startswith("VmRSS:")), "")
                if rss_line: peak_rss_bytes = max(peak_rss_bytes, int(rss_line.split()[1]) * 1024)
            except (FileNotFoundError, PermissionError, StopIteration, ValueError):
                pass
            if peak_rss_bytes >= self.memory_limit_mb * 1024 * 1024 and process.poll() is None:
                memory_killed = True; os.killpg(process.pid, signal.SIGKILL)
            remaining = deadline - time.monotonic()
            if remaining <= 0 and process.poll() is None:
                timed_out = True; os.killpg(process.pid, signal.SIGKILL)
            try:
                stdout, stderr = process.communicate(timeout=max(0.001, min(0.005, remaining)))
                break
            except subprocess.TimeoutExpired:
                continue
        wall_ms = (time.perf_counter_ns() - started) / 1e6
        meta = {"command": command, "stdout": stdout, "stderr": stderr,
                "returncode": process.returncode, "wall_ms": wall_ms,
                "peak_rss_bytes": peak_rss_bytes,
                "memory_measurement_method": "per-process /proc VmRSS polling"}
        if timed_out:
            return BackendStatus.TIME_LIMIT, None, meta
        if memory_killed:
            return BackendStatus.MEMORY_LIMIT, None, meta
        if process.returncode != 0:
            memory = "bad_alloc" in stderr or "Cannot allocate memory" in stderr or process.returncode in {-9, 137}
            return (BackendStatus.MEMORY_LIMIT if memory else BackendStatus.BACKEND_ERROR), None, meta
        try:
            return None, parse_backend_output(stdout), meta
        except (H2sAdapterError, json.JSONDecodeError) as error:
            meta["parse_error"] = str(error); return BackendStatus.OUTPUT_INVALID, None, meta

    def synthesize(self, request: RecoverySynthesisRequest) -> RecoverySynthesisResult:
        started = time.perf_counter_ns()
        if request.disabled_links or request.affected_flow_ids or request.route_scope != "all-reroute":
            return RecoverySynthesisResult(self.name, BackendStatus.UNSUPPORTED, diagnostic="exp15 supports healthy P0 all-reroute only")
        if request.forwarding_model != "stream-aware":
            return RecoverySynthesisResult(self.name, BackendStatus.UNSUPPORTED, diagnostic="stream-aware forwarding required")
        output = request.output_directory or request.scenario_path.parent / "h2s_input"
        conversion_started = time.perf_counter_ns()
        try:
            prepared = prepare_h2s_inputs(request.scenario_path, output, self.quantum_ns)
        except (H2sAdapterError, KeyError, TypeError, json.JSONDecodeError) as error:
            return RecoverySynthesisResult(self.name, BackendStatus.INVALID_INPUT, diagnostic=str(error))
        conversion_ms = (time.perf_counter_ns() - conversion_started) / 1e6
        attempts = []
        last_invalid = False
        for algorithm in ("H2S", "CELF"):
            run_status, raw, meta = self._run(prepared, algorithm, request.solver_timeout_s)
            attempts.append({"algorithm": algorithm, **meta})
            if run_status is not None:
                if run_status in {BackendStatus.TIME_LIMIT, BackendStatus.MEMORY_LIMIT}:
                    return RecoverySynthesisResult(self.name, run_status, statistics={"attempts": attempts}, diagnostic=f"{algorithm} resource limit")
                last_invalid |= run_status == BackendStatus.OUTPUT_INVALID
                continue
            normalization_started = time.perf_counter_ns()
            try:
                normalized = normalize_schedule(prepared, raw, self.quantum_ns)
                normalization_ms = (time.perf_counter_ns() - normalization_started) / 1e6
                verification_started = time.perf_counter_ns()
                checker = check_h2s_solution(prepared, normalized)
                verification_ms = (time.perf_counter_ns() - verification_started) / 1e6
            except H2sAdapterError as error:
                attempts[-1]["normalization_error"] = str(error); last_invalid = True; continue
            all_scheduled = raw["scheduled_flow_count"] == raw["requested_flow_count"] == len(prepared.flow_map)
            attempts[-1].update({"scheduled_flow_count": raw["scheduled_flow_count"], "requested_flow_count": raw["requested_flow_count"],
                                 "upstream_verifier_pass": raw["upstream_verifier_pass"], "project_static_checker_pass": checker["valid"]})
            if all_scheduled and raw["upstream_verifier_pass"] and checker["valid"]:
                status = BackendStatus.SUCCESS_H2S if algorithm == "H2S" else BackendStatus.SUCCESS_CELF_FALLBACK
                candidate_counts = [int(value) for value in raw.get("candidate_path_counts", {}).values()]
                stats = {"algorithm_used": algorithm, "primary_h2s_success": algorithm == "H2S",
                         "celf_fallback_used": algorithm == "CELF", "all_flows_scheduled": True,
                         "scheduled_flow_count": len(prepared.flow_map), "requested_flow_count": len(prepared.flow_map),
                         "scheduled_flow_ratio": 1.0, "semantic_valid": True,
                         "upstream_verifier_pass": True, "project_static_checker_pass": True,
                         "semantic_checks": checker, "attempts": attempts, "backend_quantum_ns": self.quantum_ns,
                         "candidate_path_count": self.candidate_paths, "routing_algorithm": "DIJKSTRA_OVERLAP",
                         "mean_candidate_paths_per_flow": statistics.mean(candidate_counts) if candidate_counts else 0,
                         "min_candidate_paths": min(candidate_counts, default=0),
                         "max_candidate_paths": max(candidate_counts, default=0),
                         "candidate_route_generation_ms": float(raw.get("candidate_route_generation_seconds", 0)) * 1000,
                         "seed": FORMAL_SEED, "threads": FORMAL_THREADS, "memory_limit_mb": self.memory_limit_mb,
                         **route_metrics(normalized),
                         "max_e2e_latency_ns": max((rows[-1]["end_ns"] - rows[0]["start_ns"]
                             for flow_id in prepared.flow_map
                             if (rows := sorted((row for row in normalized["route_schedule"] if row["flow_id"] == flow_id),
                                                key=lambda row: row["hop_index"]))), default=0),
                         "route_schedule": normalized["route_schedule"],
                         "quantization_audit": prepared.quantization_rows}
                return RecoverySynthesisResult(self.name, status, feasible=True,
                    logical_routes=normalized["logical_routes"], schedule_windows=normalized["schedule_windows"],
                    profile=normalized["profile"], statistics=stats,
                    timings_ms={"conversion": conversion_ms,
                                "candidate_route_generation": float(raw.get("candidate_route_generation_seconds", 0)) * 1000,
                                "scheduling": max(0.0, attempts[-1]["wall_ms"] - float(raw.get("candidate_route_generation_seconds", 0)) * 1000),
                                "verification": verification_ms, "profile_normalization": normalization_ms,
                                "total_backend": (time.perf_counter_ns() - started) / 1e6,
                                "h2s_wall": attempts[0]["wall_ms"],
                                "celf_wall": attempts[1]["wall_ms"] if len(attempts) > 1 else 0})
            last_invalid |= all_scheduled and not checker["valid"]
        status = BackendStatus.OUTPUT_INVALID if last_invalid else BackendStatus.HEURISTIC_NOT_FOUND
        ratios = [a.get("scheduled_flow_count", 0) / max(a.get("requested_flow_count", len(prepared.flow_map)), 1) for a in attempts]
        return RecoverySynthesisResult(self.name, status, statistics={"attempts": attempts,
            "scheduled_flow_ratio": max(ratios, default=0), "semantic_valid": False,
            "candidate_path_count": self.candidate_paths, "backend_quantum_ns": self.quantum_ns},
            timings_ms={"total_backend": (time.perf_counter_ns() - started) / 1e6},
            diagnostic="constructive heuristics did not produce an all-flow static-valid schedule")
