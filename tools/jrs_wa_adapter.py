"""Canonical-scenario adapter for the pinned TSNKit JRS-WA backend.

All core time calculations use integer nanoseconds.  TSNKit v0.3.0 has a
hard-coded 100 ns board slot and a deprecated uniform-1-Gbps transmission
helper.  The adapter configures its imported model modules to a 1 ns slot and
encodes uniform-link serialization as an exact 1-Gbps-equivalent frame size.
No installed third-party file is modified.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from tools.recovery_backend import (
    BackendStatus,
    RecoverySynthesisBackend,
    RecoverySynthesisRequest,
    RecoverySynthesisResult,
)

TSNKIT_VERSION = "0.3.0"
TSNKIT_COMMIT = "f8492f76753e75aa2254feb3e326feec3faad4a8"
TSNKIT_LICENSE = "GPL-3.0"
TSNKIT_INTERNAL_TIME_UNIT_NS = 1
GUROBI_SEED = 1024


class AdapterError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def seconds_to_ns(value: float | str, label: str) -> int:
    ns = Decimal(str(value)) * Decimal(1_000_000_000)
    integral = ns.to_integral_value()
    if abs(ns - integral) > Decimal("0.000001"):
        raise AdapterError(f"{label} is not exactly representable in integer ns: {value}")
    return int(integral)


def deterministic_stream_handles(flow_ids: list[str]) -> dict[str, int]:
    ordered = sorted(flow_ids)
    if len(ordered) > 4094:
        raise AdapterError("IEEE 802.1Q VID-backed stream handles exceed 4094")
    if len(set(ordered)) != len(ordered):
        raise AdapterError("duplicate flow ID in stream-handle mapping")
    return {flow_id: index + 1 for index, flow_id in enumerate(ordered)}


def tsnkit_rate_code(bitrate_bps: int) -> int:
    mapping = {1_000_000_000: 1, 100_000_000: 10, 10_000_000: 100, 1_000_000: 1000}
    try:
        return mapping[bitrate_bps]
    except KeyError as error:
        raise AdapterError(f"TSNKit v0.3.0 cannot represent bitrate {bitrate_bps} bps") from error


def exact_equivalent_size_bytes(on_wire_bytes: int, bitrate_bps: int) -> int:
    numerator = on_wire_bytes * 1_000_000_000
    if numerator % bitrate_bps:
        raise AdapterError("serialization-equivalent 1-Gbps size is not an integer byte count")
    return numerator // bitrate_bps


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


@dataclass(frozen=True)
class PreparedInputs:
    scenario: dict[str, Any]
    node_map: dict[str, int]
    reverse_node_map: dict[int, str]
    link_map: dict[str, list[dict[str, int | str]]]
    arc_to_logical_link: dict[tuple[int, int], str]
    flow_map: dict[str, int]
    reverse_flow_map: dict[int, str]
    stream_handles: dict[str, int]
    topology_csv: Path
    task_csv: Path
    manifest: dict[str, Any]


def prepare_inputs(scenario_path: Path, output_directory: Path,
                   disabled_links: tuple[str, ...] = ()) -> PreparedInputs:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    required = {"scenario_name", "nodes", "links", "tt_flows", "scheduling", "simulation"}
    missing = sorted(required - scenario.keys())
    if missing:
        raise AdapterError(f"canonical scenario missing fields: {', '.join(missing)}")
    if scenario.get("forwarding_model", "destination-mac") != "stream-aware":
        raise AdapterError("JRS-WA qualification requires forwarding_model=stream-aware")

    node_ids = sorted(node["id"] for node in scenario["nodes"])
    node_map = {node_id: index for index, node_id in enumerate(node_ids)}
    reverse_node_map = {value: key for key, value in node_map.items()}
    disabled = set(disabled_links)
    known_links = {link["id"] for link in scenario["links"]}
    if not disabled <= known_links:
        raise AdapterError(f"unknown disabled logical links: {sorted(disabled - known_links)}")

    topology_rows: list[dict[str, Any]] = []
    link_map: dict[str, list[dict[str, int | str]]] = {}
    arc_to_logical: dict[tuple[int, int], str] = {}
    row_id = 0
    bitrates = {int(link["bitrate_bps"]) for link in scenario["links"]}
    if len(bitrates) != 1:
        raise AdapterError("UNSUPPORTED_MIXED_LINK_BITRATE: stock JRS-WA uses one transmission duration per flow")
    common_bitrate = next(iter(bitrates))
    for link in sorted(scenario["links"], key=lambda item: item["id"]):
        arcs = []
        for source, destination in ((link["endpoint_a"], link["endpoint_b"]),
                                    (link["endpoint_b"], link["endpoint_a"])):
            arc = (node_map[source], node_map[destination])
            entry = {"source": arc[0], "destination": arc[1], "state": "disabled" if link["id"] in disabled else "enabled"}
            arcs.append(entry)
            if link["id"] not in disabled:
                propagation_ns = seconds_to_ns(link["propagation_delay_s"], f"{link['id']}.propagation_delay")
                topology_rows.append({
                    "link": str(arc), "q_num": 8, "rate": tsnkit_rate_code(int(link["bitrate_bps"])),
                    # Stock JRS-WA uses network.max_t_proc, but not t_prop, in
                    # hop-precedence constraints.  Encode physical propagation
                    # there as well so the generated gates are deployable.
                    "t_proc": propagation_ns, "t_prop": propagation_ns,
                })
                arc_to_logical[arc] = link["id"]
                row_id += 1
        link_map[link["id"]] = arcs

    flows = sorted(scenario["tt_flows"], key=lambda item: item["id"])
    flow_map = {flow["id"]: index for index, flow in enumerate(flows)}
    reverse_flow_map = {value: key for key, value in flow_map.items()}
    stream_handles = deterministic_stream_handles(list(flow_map))
    overhead = int(scenario["scheduling"]["frame_overhead_bytes"])
    task_rows = []
    serialization_audit = []
    periods = []
    for flow in flows:
        wire_bytes = int(flow["packet_size_bytes"]) + overhead
        equivalent_bytes = exact_equivalent_size_bytes(wire_bytes, common_bitrate)
        period_ns = seconds_to_ns(flow["period_s"], f"{flow['id']}.period")
        deadline_ns = seconds_to_ns(flow["schedule_deadline_budget_s"], f"{flow['id']}.schedule_deadline_budget")
        release_ns = seconds_to_ns(flow["release_offset_s"], f"{flow['id']}.release_offset")
        periods.append(period_ns)
        task_rows.append({
            "stream": flow_map[flow["id"]], "src": node_map[flow["source"]],
            "dst": str([node_map[flow["destination"]]]), "size": equivalent_bytes,
            "period": period_ns, "deadline": deadline_ns, "jitter": 0,
        })
        expected_tx_ns = math.ceil(wire_bytes * 8_000_000_000 / common_bitrate)
        serialization_audit.append({
            "flow_id": flow["id"], "payload_bytes": flow["packet_size_bytes"],
            "frame_overhead_bytes": overhead, "on_wire_bytes": wire_bytes,
            "link_bitrate_bps": common_bitrate, "tsnkit_equivalent_size_bytes": equivalent_bytes,
            "project_serialization_ns": expected_tx_ns, "tsnkit_serialization_ns": equivalent_bytes * 8,
            "exact_match": expected_tx_ns == equivalent_bytes * 8,
            "release_offset_ns": release_ns, "schedule_deadline_ns": deadline_ns,
        })
    hyperperiod_ns = math.lcm(*periods)
    cycle_ns = seconds_to_ns(scenario["simulation"]["cycle_time_s"], "cycle_time")
    if hyperperiod_ns != cycle_ns:
        raise AdapterError("UNSUPPORTED_HYPERPERIOD: qualification requires hyperperiod equal to cycle")

    topology_csv = output_directory / "topology.csv"
    task_csv = output_directory / "task.csv"
    _write_csv(topology_csv, ["link", "q_num", "rate", "t_proc", "t_prop"], topology_rows)
    _write_csv(task_csv, ["stream", "src", "dst", "size", "period", "deadline", "jitter"], task_rows)
    node_payload = {"logical_to_tsnkit": node_map, "tsnkit_to_logical": {str(k): v for k, v in reverse_node_map.items()}}
    link_payload = {"logical_to_directed_arcs": link_map}
    (output_directory / "node_map.json").write_bytes(canonical_json_bytes(node_payload))
    (output_directory / "link_map.json").write_bytes(canonical_json_bytes(link_payload))
    manifest = {
        "schema_version": 1, "scenario": scenario["scenario_name"],
        "scenario_sha256": scenario.get("scenario_sha256"), "disabled_links": sorted(disabled),
        "tsnkit_version": TSNKIT_VERSION, "tsnkit_commit": TSNKIT_COMMIT,
        "tsnkit_internal_time_unit_ns": TSNKIT_INTERNAL_TIME_UNIT_NS,
        "project_cycle_ns": cycle_ns, "hyperperiod_ns": hyperperiod_ns,
        "link_bitrate_bps": common_bitrate, "stream_handles": stream_handles,
        "flow_map": flow_map, "serialization_audit": serialization_audit,
        "topology_sha256": hashlib.sha256(topology_csv.read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(task_csv.read_bytes()).hexdigest(),
    }
    (output_directory / "case_manifest.json").write_bytes(canonical_json_bytes(manifest))
    return PreparedInputs(scenario, node_map, reverse_node_map, link_map, arc_to_logical,
                          flow_map, reverse_flow_map, stream_handles, topology_csv, task_csv, manifest)


def merge_intervals(intervals: list[tuple[int, int]], cycle_ns: int,
                    allow_overlap: bool = False) -> list[tuple[int, int]]:
    ordered = sorted(intervals)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if start < 0 or end <= start or end > cycle_ns:
            raise AdapterError(f"invalid GCL interval [{start},{end}) for cycle {cycle_ns}")
        if merged and start < merged[-1][1]:
            if not allow_overlap:
                raise AdapterError("overlapping JRS-WA windows on one egress")
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        if merged and start == merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def compile_gate_schedules(windows: list[dict[str, Any]], cycle_ns: int,
                           tt_class: int, be_class: int, deployment_guard_ns: int = 0) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for window in windows:
        grouped[window["egress_path"]].append(
            (window["start_ns"], min(cycle_ns, window["end_ns"] + deployment_guard_ns)))
    result = []
    for egress, raw in sorted(grouped.items()):
        intervals = merge_intervals(raw, cycle_ns, allow_overlap=deployment_guard_ns > 0)
        durations = []
        for index, (_, end) in enumerate(intervals):
            start = intervals[index][0]
            durations.append((end - start) / 1_000_000_000)
            next_start = intervals[index + 1][0] if index + 1 < len(intervals) else cycle_ns + intervals[0][0]
            closed = next_start - end
            if closed <= 0:
                raise AdapterError("TT windows consume the complete cycle; BE complement is impossible")
            durations.append(closed / 1_000_000_000)
        offset_ns = 0 if intervals[0][0] == 0 else cycle_ns - intervals[0][0]
        for traffic_class, initially_open in ((tt_class, True), (be_class, False)):
            result.append({
                "gate_path": f"{egress}.macLayer.queue.transmissionGate[{traffic_class}]",
                "traffic_class": traffic_class, "initially_open": initially_open,
                "offset_s": offset_ns / 1_000_000_000, "durations_s": durations,
            })
    return result


def _port_binding(port_map: dict[str, Any], logical_link: str, node: str) -> dict[str, Any]:
    link = port_map["links"][logical_link]
    for side in ("a", "b"):
        if link[side]["node"] == node:
            return link[side]
    raise AdapterError(f"port map has no binding for {logical_link} at {node}")


class TsnkitJrsWaBackend(RecoverySynthesisBackend):
    name = "tsnkit-jrs-wa"

    @staticmethod
    def _configure_one_ns_tsnkit() -> None:
        import tsnkit.core._config as config_module
        import tsnkit.core._constants as constants_module
        import tsnkit.core._network as network_module
        import tsnkit.core._stream as stream_module
        constants_module.T_SLOT = TSNKIT_INTERNAL_TIME_UNIT_NS
        stream_module.T_SLOT = TSNKIT_INTERNAL_TIME_UNIT_NS
        network_module.T_SLOT = TSNKIT_INTERNAL_TIME_UNIT_NS
        config_module.T_SLOT = TSNKIT_INTERNAL_TIME_UNIT_NS

    def synthesize(self, request: RecoverySynthesisRequest) -> RecoverySynthesisResult:
        started = time.perf_counter_ns()
        if request.forwarding_model != "stream-aware":
            return RecoverySynthesisResult(self.name, BackendStatus.UNSUPPORTED,
                                           diagnostic="JRS-WA qualification requires stream-aware forwarding")
        if request.route_scope not in {"affected-only", "all-reroute"}:
            return RecoverySynthesisResult(self.name, BackendStatus.UNSUPPORTED,
                                           diagnostic=f"unsupported route scope {request.route_scope}")
        output = request.output_directory or request.scenario_path.parent / "jrs_wa_input"
        try:
            conversion_start = time.perf_counter_ns()
            prepared = prepare_inputs(request.scenario_path, output, request.disabled_links)
            conversion_ms = (time.perf_counter_ns() - conversion_start) / 1e6
            self._configure_one_ns_tsnkit()
            import gurobipy as gp
            from tsnkit.algorithms.jrs_wa import jrs_wa

            directed_arc_count = len(prepared.topology_csv.read_text(encoding="utf-8").splitlines()) - 1
            routing_binary_upper_bound = len(prepared.flow_map) * directed_arc_count
            probe = gp.Model("exp13_license_capacity_probe")
            restricted_license = probe.Params.LicenseID == 0
            probe.dispose()
            if restricted_license and routing_binary_upper_bound > 900:
                return RecoverySynthesisResult(
                    self.name, BackendStatus.GUROBI_LICENSE_CAPACITY_LIMIT,
                    diagnostic=("restricted Gurobi license capacity precheck: "
                                f"{len(prepared.flow_map)} streams x {directed_arc_count} directed arcs; "
                                "JRS-WA formulation exceeds the 2000-variable size-limited license"),
                    statistics={"restricted_license": True,
                                "routing_binary_upper_bound": routing_binary_upper_bound,
                                "threads": 1, "seed": GUROBI_SEED, "timeout_s": request.solver_timeout_s},
                    timings_ms={"input_conversion": conversion_ms,
                                "total_backend": (time.perf_counter_ns() - started) / 1e6})

            model_start = time.perf_counter_ns()
            engine = jrs_wa(1)
            engine.init(str(prepared.task_csv), str(prepared.topology_csv))
            engine.prepare()
            engine.solver.Params.LogToConsole = 0
            engine.solver.Params.Threads = 1
            engine.solver.Params.Seed = GUROBI_SEED
            engine.solver.Params.TimeLimit = request.solver_timeout_s

            flows_by_id = {flow["id"]: flow for flow in prepared.scenario["tt_flows"]}
            affected = set(request.affected_flow_ids)
            if not affected <= flows_by_id.keys():
                raise AdapterError(f"unknown affected flows: {sorted(affected - flows_by_id.keys())}")
            locks = request.healthy_primary_routes if request.route_scope == "affected-only" else {}
            for flow_id, locked in sorted(locks.items()):
                if flow_id in affected:
                    continue
                stream = engine.task[prepared.flow_map[flow_id]]
                node_path = locked.get("node_path", [])
                link_path = locked.get("link_path", [])
                if len(node_path) != len(link_path) + 1:
                    raise AdapterError(f"invalid healthy route lock for {flow_id}")
                locked_arcs = {
                    (prepared.node_map[source], prepared.node_map[destination])
                    for source, destination in zip(node_path, node_path[1:])
                }
                for link in engine.routing_space[stream]:
                    arc = (int(link.src), int(link.dst))
                    engine.solver.addConstr(engine.r[stream][link] == (1 if arc in locked_arcs else 0),
                                            name=f"route_lock_{int(stream)}_{int(link)}")

            for flow_id, flow in sorted(flows_by_id.items()):
                stream = engine.task[prepared.flow_map[flow_id]]
                release_ns = seconds_to_ns(flow["release_offset_s"], f"{flow_id}.release_offset")
                for link in engine.net.get_outcome_links(stream.src):
                    if link in engine.routing_space[stream]:
                        engine.solver.addGenConstrIndicator(engine.r[stream][link], True,
                                                            engine.t[stream][link] == release_ns,
                                                            name=f"fixed_release_{int(stream)}_{int(link)}")
            engine.solver.update()
            model_ms = (time.perf_counter_ns() - model_start) / 1e6
            solve_start = time.perf_counter_ns()
            engine.solver.optimize()
            solve_ms = (time.perf_counter_ns() - solve_start) / 1e6
            status, feasible, optimal = self._map_status(engine.solver.Status, engine.solver.SolCount, gp)
            statistics = {
                "gurobi_status_code": engine.solver.Status, "solution_count": engine.solver.SolCount,
                "num_variables": engine.solver.NumVars, "num_constraints": engine.solver.NumConstrs,
                "num_general_constraints": engine.solver.NumGenConstrs,
                "solver_runtime_ms": engine.solver.Runtime * 1000,
                "mip_gap": engine.solver.MIPGap if engine.solver.SolCount else None,
                "threads": 1, "seed": GUROBI_SEED, "timeout_s": request.solver_timeout_s,
                "route_scope": request.route_scope, "fixed_release_extension": True,
                "route_lock_extension": request.route_scope == "affected-only",
            }
            result = RecoverySynthesisResult(self.name, status, feasible=feasible,
                                             optimal_proven=optimal, statistics=statistics,
                                             timings_ms={"input_conversion": conversion_ms,
                                                         "model_build": model_ms, "solver_wall": solve_ms})
            if feasible:
                extract_start = time.perf_counter_ns()
                self._extract(engine, prepared, request, result)
                result.timings_ms["output_extract"] = (time.perf_counter_ns() - extract_start) / 1e6
                result.objective = engine.solver.ObjVal
            result.timings_ms["total_backend"] = (time.perf_counter_ns() - started) / 1e6
            result.diagnostic = status.value
            return result
        except Exception as error:
            diagnostic = f"{type(error).__name__}: {error}"
            lowered = diagnostic.lower()
            if "model too large for size-limited" in lowered:
                status = BackendStatus.GUROBI_LICENSE_CAPACITY_LIMIT
            elif "license" in lowered:
                status = BackendStatus.GUROBI_LICENSE_UNAVAILABLE
            else:
                status = BackendStatus.ERROR
            return RecoverySynthesisResult(self.name, status,
                                           diagnostic=diagnostic,
                                           timings_ms={"total_backend": (time.perf_counter_ns() - started) / 1e6})

    @staticmethod
    def _map_status(code: int, solution_count: int, gp: Any) -> tuple[BackendStatus, bool, bool]:
        if code == gp.GRB.OPTIMAL:
            return BackendStatus.OPTIMAL, True, True
        if code == gp.GRB.INFEASIBLE:
            return BackendStatus.INFEASIBLE, False, False
        if code == gp.GRB.TIME_LIMIT:
            return (BackendStatus.TIME_LIMIT_WITH_INCUMBENT, True, False) if solution_count else (BackendStatus.TIME_LIMIT_NO_INCUMBENT, False, False)
        if solution_count:
            return BackendStatus.FEASIBLE_NOT_OPTIMAL, True, False
        return BackendStatus.ERROR, False, False

    @staticmethod
    def _extract(engine: Any, prepared: PreparedInputs, request: RecoverySynthesisRequest,
                 result: RecoverySynthesisResult) -> None:
        scenario = prepared.scenario
        port_map_path = request.scenario_path.with_name("port_map.json")
        if not port_map_path.exists():
            raise AdapterError(f"missing canonical port map beside scenario: {port_map_path}")
        port_map = json.loads(port_map_path.read_text(encoding="utf-8"))
        flow_defs = {flow["id"]: flow for flow in scenario["tt_flows"]}
        windows = []
        logical_routes = []
        forwarding = []
        for stream in engine.task:
            flow_id = prepared.reverse_flow_map[int(stream)]
            selected = {(int(link.src), int(link.dst)): link for link in engine.routing_space[stream]
                        if float(engine.r[stream][link].X) > 0.5}
            current = prepared.node_map[flow_defs[flow_id]["source"]]
            destination = prepared.node_map[flow_defs[flow_id]["destination"]]
            nodes = [prepared.reverse_node_map[current]]
            links = []
            visited = {current}
            while current != destination:
                outgoing = [(arc, link) for arc, link in selected.items() if arc[0] == current]
                if len(outgoing) != 1:
                    raise AdapterError(f"JRS route for {flow_id} has {len(outgoing)} outgoing arcs at node {current}")
                arc, link = outgoing[0]
                logical_link = prepared.arc_to_logical_link[arc]
                links.append(logical_link)
                start_ns = int(round(float(engine.t[stream][link].X)))
                end_ns = start_ns + int(stream.t_trans_1g)
                logical_source = prepared.reverse_node_map[arc[0]]
                if any(node["id"] == logical_source and node["type"] == "switch" for node in scenario["nodes"]):
                    binding = _port_binding(port_map, logical_link, logical_source)
                    windows.append({"flow_id": flow_id, "logical_link": logical_link,
                                    "switch": logical_source, "egress_path": binding["egress_path"],
                                    "start_ns": start_ns, "end_ns": end_ns,
                                    "traffic_class": flow_defs[flow_id]["traffic_class"]})
                    forwarding.append({
                        "flow_id": flow_id, "switch": logical_source,
                        "destination": flow_defs[flow_id]["destination"], "interface": binding["interface"],
                        "logical_link": logical_link, "stream_handle": prepared.stream_handles[flow_id],
                    })
                current = arc[1]
                if current in visited:
                    raise AdapterError(f"JRS route loop for {flow_id}")
                visited.add(current)
                nodes.append(prepared.reverse_node_map[current])
            if len(links) != len(selected):
                raise AdapterError(f"JRS route for {flow_id} contains disconnected selected arcs")
            logical_routes.append({"flow_id": flow_id, "node_path": nodes, "link_path": links})
        cycle_ns = seconds_to_ns(scenario["simulation"]["cycle_time_s"], "cycle_time")
        tt_classes = {int(flow["traffic_class"]) for flow in scenario["tt_flows"]}
        if len(tt_classes) != 1:
            raise AdapterError("qualification profile conversion requires one TT traffic class")
        deployment_guard_ns = (
            seconds_to_ns(scenario["scheduling"]["ingress_margin_s"], "ingress_margin")
            + seconds_to_ns(scenario["scheduling"]["hop_margin_s"], "hop_margin")
        )
        profile = {
            "schema_version": 1, "scenario_sha256": scenario.get("scenario_sha256"),
            "profile_id": "JRS_WA", "forwarding_model": "stream-aware",
            "logical_routes": sorted(logical_routes, key=lambda item: item["flow_id"]),
            "routes": sorted(forwarding, key=lambda item: (item["flow_id"], item["switch"])),
            "gate_schedules": compile_gate_schedules(windows, cycle_ns, next(iter(tt_classes)),
                                                      int(scenario["scheduling"]["be_traffic_class"]),
                                                      deployment_guard_ns),
            "gcl_deployment_guard_ns": deployment_guard_ns,
        }
        profile["semantic_profile_hash"] = sha256_value({key: profile[key] for key in
                                                         ("forwarding_model", "logical_routes", "routes", "gate_schedules")})
        result.logical_routes = profile["logical_routes"]
        result.schedule_windows = sorted(windows, key=lambda item: (item["egress_path"], item["start_ns"], item["flow_id"]))
        result.profile = profile
