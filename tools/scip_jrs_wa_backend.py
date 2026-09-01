"""Independent open-source JRS-WA formulation implemented with PySCIPOpt.

The module deliberately reuses only the project's canonical exp13 input
mapping.  It does not import or copy TSNKit's GPL JRS-WA implementation.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.jrs_wa_adapter import (
    AdapterError, PreparedInputs, _port_binding, compile_gate_schedules,
    prepare_inputs, seconds_to_ns, sha256_value,
)
from tools.recovery_backend import (
    BackendStatus, RecoverySynthesisBackend, RecoverySynthesisRequest,
    RecoverySynthesisResult,
)

SCIP_SEED = 1024
SCIP_THREADS = 1


@dataclass(frozen=True)
class FlowData:
    flow_id: str
    source: int
    destination: int
    period_ns: int
    deadline_ns: int
    release_ns: int
    tx_ns: int


def _reachable(start: int, adjacency: dict[int, list[int]]) -> set[int]:
    seen = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for other in adjacency.get(node, []):
            if other not in seen:
                seen.add(other)
                queue.append(other)
    return seen


def build_flow_data(prepared: PreparedInputs) -> tuple[list[FlowData], list[tuple[int, int]], dict[tuple[int, int], int]]:
    arcs: list[tuple[int, int]] = []
    propagation: dict[tuple[int, int], int] = {}
    with prepared.topology_csv.open(encoding="utf-8") as handle:
        import ast
        import csv
        for row in csv.DictReader(handle):
            arc = tuple(ast.literal_eval(row["link"]))
            arcs.append(arc)
            propagation[arc] = int(row["t_prop"])
    audit = {row["flow_id"]: row for row in prepared.manifest["serialization_audit"]}
    flows = []
    for item in sorted(prepared.scenario["tt_flows"], key=lambda x: x["id"]):
        row = audit[item["id"]]
        flows.append(FlowData(
            item["id"], prepared.node_map[item["source"]], prepared.node_map[item["destination"]],
            seconds_to_ns(item["period_s"], f"{item['id']}.period"),
            int(row["schedule_deadline_ns"]), int(row["release_offset_ns"]),
            int(row["project_serialization_ns"]),
        ))
    return flows, sorted(arcs), propagation


def route_spaces(flows: list[FlowData], arcs: list[tuple[int, int]]) -> dict[str, list[tuple[int, int]]]:
    outgoing: dict[int, list[int]] = defaultdict(list)
    incoming: dict[int, list[int]] = defaultdict(list)
    for u, v in arcs:
        outgoing[u].append(v)
        incoming[v].append(u)
    spaces = {}
    for flow in flows:
        from_source = _reachable(flow.source, outgoing)
        to_destination = _reachable(flow.destination, incoming)
        spaces[flow.flow_id] = [
            (u, v) for u, v in arcs
            if u in from_source and v in to_destination and v != flow.source and u != flow.destination
        ]
    return spaces


class ScipJrsWaBackend(RecoverySynthesisBackend):
    name = "jrs-wa-scip"

    def synthesize(self, request: RecoverySynthesisRequest) -> RecoverySynthesisResult:
        started = time.perf_counter_ns()
        timings: dict[str, float] = {}
        if request.forwarding_model != "stream-aware":
            return RecoverySynthesisResult(self.name, BackendStatus.UNSUPPORTED,
                                           diagnostic="JRS-WA requires stream-aware forwarding")
        if request.route_scope not in {"affected-only", "all-reroute"}:
            return RecoverySynthesisResult(self.name, BackendStatus.UNSUPPORTED,
                                           diagnostic=f"unsupported route scope {request.route_scope}")
        output = request.output_directory or request.scenario_path.parent / "jrs_wa_scip_input"
        try:
            at = time.perf_counter_ns()
            prepared = prepare_inputs(request.scenario_path, output, request.disabled_links)
            timings["input_conversion"] = (time.perf_counter_ns() - at) / 1e6
            at = time.perf_counter_ns()
            flows, arcs, propagation = build_flow_data(prepared)
            spaces = route_spaces(flows, arcs)
            timings["route_space_build"] = (time.perf_counter_ns() - at) / 1e6
            model, variables, audit = self._build_model(prepared, request, flows, spaces, propagation)
            timings["model_build"] = audit.pop("model_build_ms")
        except (AdapterError, ValueError, KeyError, ImportError) as error:
            timings["total_backend"] = (time.perf_counter_ns() - started) / 1e6
            return RecoverySynthesisResult(self.name, BackendStatus.MODEL_BUILD_ERROR,
                                           diagnostic=f"{type(error).__name__}: {error}", timings_ms=timings)
        except Exception as error:
            timings["total_backend"] = (time.perf_counter_ns() - started) / 1e6
            return RecoverySynthesisResult(self.name, BackendStatus.MODEL_BUILD_ERROR,
                                           diagnostic=f"{type(error).__name__}: {error}", timings_ms=timings)

        try:
            at = time.perf_counter_ns()
            model.optimize()
            timings["solver_wall"] = (time.perf_counter_ns() - at) / 1e6
            status, feasible, optimal = self._map_status(str(model.getStatus()), model.getNSols())
            audit.update({
                "scip_status": str(model.getStatus()), "solution_count": model.getNSols(),
                "solver_runtime_ms": model.getSolvingTime() * 1000,
                "solver_memory_bytes": round(model.getMemUsed()),
                "threads": SCIP_THREADS, "seed": SCIP_SEED,
                "timeout_s": request.solver_timeout_s, "route_scope": request.route_scope,
                "objective_mode": "feasibility-zero-objective",
            })
            result = RecoverySynthesisResult(self.name, status, feasible=feasible,
                                             optimal_proven=optimal, statistics=audit, timings_ms=timings)
            if feasible:
                at = time.perf_counter_ns()
                self._extract(model, variables, prepared, request, flows, spaces, result)
                timings["solution_extract"] = (time.perf_counter_ns() - at) / 1e6
                result.objective = 0.0
            result.diagnostic = status.value
        except Exception as error:
            result = RecoverySynthesisResult(self.name, BackendStatus.ERROR,
                                             diagnostic=f"{type(error).__name__}: {error}",
                                             statistics=audit, timings_ms=timings)
        timings["total_backend"] = (time.perf_counter_ns() - started) / 1e6
        return result

    @staticmethod
    def _map_status(status: str, solutions: int) -> tuple[BackendStatus, bool, bool]:
        status = status.lower()
        if status == "optimal":
            return BackendStatus.OPTIMAL, True, True
        if status in {"infeasible", "inforunbd"}:
            return BackendStatus.INFEASIBLE, False, False
        if status == "timelimit":
            return ((BackendStatus.TIME_LIMIT_WITH_INCUMBENT, True, False) if solutions
                    else (BackendStatus.TIME_LIMIT_NO_INCUMBENT, False, False))
        if status == "memlimit":
            return BackendStatus.MEMORY_LIMIT, bool(solutions), False
        if solutions:
            return BackendStatus.FEASIBLE_NOT_OPTIMAL, True, False
        return BackendStatus.ERROR, False, False

    @staticmethod
    def _build_model(prepared: PreparedInputs, request: RecoverySynthesisRequest,
                     flows: list[FlowData], spaces: dict[str, list[tuple[int, int]]],
                     propagation: dict[tuple[int, int], int]):
        from pyscipopt import Model, quicksum
        started = time.perf_counter_ns()
        model = Model("jrs_wa_scip")
        model.hideOutput(True)
        model.setRealParam("limits/time", float(request.solver_timeout_s))
        model.setIntParam("parallel/maxnthreads", SCIP_THREADS)
        model.setIntParam("parallel/minnthreads", SCIP_THREADS)
        model.setIntParam("lp/threads", SCIP_THREADS)
        model.setIntParam("parallel/mode", 0)
        model.setIntParam("randomization/randomseedshift", SCIP_SEED)
        model.setIntParam("randomization/permutationseed", SCIP_SEED)
        model.setIntParam("randomization/lpseed", SCIP_SEED)
        model.setIntParam("branching/random/seed", SCIP_SEED)
        model.setBoolParam("randomization/permutevars", False)
        model.setBoolParam("randomization/permuteconss", False)
        cycle = int(prepared.manifest["project_cycle_ns"])
        flow_by_id = {f.flow_id: f for f in flows}
        family = {name: 0 for name in (
            "FRAME_BOUNDS", "ROUTING_SOURCE_DESTINATION", "ROUTING_FLOW_CONSERVATION",
            "ROUTING_LOOP_PRUNING", "ROUTE_TIME_PRESENT", "HOP_PRECEDENCE",
            "LINK_NON_OVERLAP", "END_TO_END_DEADLINE", "FIXED_RELEASE_OFFSET",
            "RECOVERY_ROUTE_LOCK")}
        nonzeros = 0
        r: dict[tuple[str, tuple[int, int]], Any] = {}
        t: dict[tuple[str, tuple[int, int]], Any] = {}
        order: dict[tuple[str, str, tuple[int, int]], Any] = {}
        for flow in flows:
            for arc in spaces[flow.flow_id]:
                key = (flow.flow_id, arc)
                r[key] = model.addVar(vtype="B", name=f"r_{flow.flow_id}_{arc[0]}_{arc[1]}")
                t[key] = model.addVar(vtype="I", lb=0, ub=cycle, name=f"t_{flow.flow_id}_{arc[0]}_{arc[1]}")

        def add(cons: Any, group: str, nz: int, suffix: str) -> None:
            nonlocal nonzeros
            model.addCons(cons, name=f"{group}_{suffix}")
            family[group] += 1
            nonzeros += nz

        for flow in flows:
            fid, space = flow.flow_id, spaces[flow.flow_id]
            outgoing: dict[int, list[tuple[int, int]]] = defaultdict(list)
            incoming: dict[int, list[tuple[int, int]]] = defaultdict(list)
            for arc in space:
                outgoing[arc[0]].append(arc); incoming[arc[1]].append(arc)
                add(t[(fid, arc)] <= (cycle - flow.tx_ns) * r[(fid, arc)], "ROUTE_TIME_PRESENT", 2, f"{fid}_{arc}")
                add(t[(fid, arc)] + flow.tx_ns * r[(fid, arc)] <= cycle, "FRAME_BOUNDS", 2, f"{fid}_{arc}")
            src_out = outgoing[flow.source]
            dst_in = incoming[flow.destination]
            add(quicksum(r[(fid, a)] for a in src_out) == 1,
                "ROUTING_SOURCE_DESTINATION", len(src_out), f"src_{fid}")
            add(quicksum(r[(fid, a)] for a in dst_in) == 1,
                "ROUTING_SOURCE_DESTINATION", len(dst_in), f"dst_{fid}")
            for node in sorted(set(outgoing) | set(incoming)):
                if node in {flow.source, flow.destination}: continue
                ins, outs = incoming[node], outgoing[node]
                add(quicksum(r[(fid, a)] for a in ins) == quicksum(r[(fid, a)] for a in outs),
                    "ROUTING_FLOW_CONSERVATION", len(ins) + len(outs), f"{fid}_{node}")
                add(quicksum(r[(fid, a)] for a in ins) <= 1,
                    "ROUTING_LOOP_PRUNING", len(ins), f"{fid}_{node}")
                for ain in ins:
                    for aout in outs:
                        big_m = cycle + flow.tx_ns + propagation[ain]
                        add(t[(fid, aout)] >= t[(fid, ain)] + flow.tx_ns + propagation[ain]
                            - big_m * (2 - r[(fid, ain)] - r[(fid, aout)]),
                            "HOP_PRECEDENCE", 4, f"{fid}_{ain}_{aout}")
            add(quicksum(t[(fid, a)] for a in src_out) == flow.release_ns,
                "FIXED_RELEASE_OFFSET", len(src_out), fid)
            add(quicksum(t[(fid, a)] + flow.tx_ns * r[(fid, a)] for a in dst_in)
                <= flow.release_ns + flow.deadline_ns,
                "END_TO_END_DEADLINE", 2 * len(dst_in), fid)

        if request.route_scope == "affected-only":
            affected = set(request.affected_flow_ids)
            unknown = affected - set(flow_by_id)
            if unknown: raise AdapterError(f"unknown affected flows: {sorted(unknown)}")
            expected_locks = set(flow_by_id) - affected
            actual_locks = set(request.healthy_primary_routes) - affected
            if actual_locks != expected_locks:
                raise AdapterError(f"healthy route locks must exactly cover unaffected TT flows; missing={sorted(expected_locks - actual_locks)} extra={sorted(actual_locks - expected_locks)}")
            for fid, locked in sorted(request.healthy_primary_routes.items()):
                if fid in affected: continue
                if fid not in flow_by_id: raise AdapterError(f"unknown locked flow: {fid}")
                node_path, link_path = locked.get("node_path", []), locked.get("link_path", [])
                if len(node_path) != len(link_path) + 1:
                    raise AdapterError(f"invalid healthy route lock for {fid}")
                ordered_arcs = [(prepared.node_map[u], prepared.node_map[v]) for u, v in zip(node_path, node_path[1:])]
                locked_arcs = set(ordered_arcs)
                for arc, link_id in zip(ordered_arcs, link_path):
                    if prepared.arc_to_logical_link.get(arc) != link_id:
                        raise AdapterError(f"healthy route lock for {fid} has inconsistent node/link path")
                if not locked_arcs <= set(spaces[fid]):
                    raise AdapterError(f"healthy route lock for {fid} uses disabled/unavailable arc")
                for arc in spaces[fid]:
                    add(r[(fid, arc)] == int(arc in locked_arcs), "RECOVERY_ROUTE_LOCK", 1, f"{fid}_{arc}")

        users: dict[tuple[int, int], list[str]] = defaultdict(list)
        for fid, space in spaces.items():
            for arc in space: users[arc].append(fid)
        for arc, ids in sorted(users.items()):
            for i, left in enumerate(sorted(ids)):
                for right in sorted(ids)[i + 1:]:
                    key = (left, right, arc)
                    order[key] = model.addVar(vtype="B", name=f"o_{left}_{right}_{arc[0]}_{arc[1]}")
                    lf, rf = flow_by_id[left], flow_by_id[right]
                    # The qualification/scalability family has one instance per common cycle.
                    big_m = cycle + max(lf.tx_ns, rf.tx_ns)
                    add(t[(left, arc)] + lf.tx_ns <= t[(right, arc)]
                        + big_m * (1 - order[key]) + big_m * (2 - r[(left, arc)] - r[(right, arc)]),
                        "LINK_NON_OVERLAP", 5, f"lr_{left}_{right}_{arc}")
                    add(t[(right, arc)] + rf.tx_ns <= t[(left, arc)]
                        + big_m * order[key] + big_m * (2 - r[(left, arc)] - r[(right, arc)]),
                        "LINK_NON_OVERLAP", 5, f"rl_{left}_{right}_{arc}")
        model.setObjective(0.0, "minimize")
        audit = {
            "num_variables": model.getNVars(), "num_binary_variables": len(r) + len(order),
            "num_integer_time_variables": len(t), "num_route_variables": len(r),
            "num_ordering_variables": len(order), "num_constraints": model.getNConss(),
            "num_nonzeros": nonzeros, "constraint_family_counts": family,
            "directed_arc_count": len({a for values in spaces.values() for a in values}),
            "flow_count": len(flows), "model_build_ms": (time.perf_counter_ns() - started) / 1e6,
        }
        return model, {"r": r, "t": t}, audit

    @staticmethod
    def _extract(model: Any, variables: dict[str, Any], prepared: PreparedInputs,
                 request: RecoverySynthesisRequest, flows: list[FlowData],
                 spaces: dict[str, list[tuple[int, int]]], result: RecoverySynthesisResult) -> None:
        port_path = request.scenario_path.with_name("port_map.json")
        if not port_path.exists(): raise AdapterError(f"missing canonical port map beside scenario: {port_path}")
        port_map = json.loads(port_path.read_text(encoding="utf-8"))
        nodes = {n["id"]: n for n in prepared.scenario["nodes"]}
        defs = {f["id"]: f for f in prepared.scenario["tt_flows"]}
        logical_routes, windows, forwarding, route_schedule = [], [], [], []
        for flow in flows:
            fid = flow.flow_id
            selected = {arc for arc in spaces[fid] if model.getVal(variables["r"][(fid, arc)]) > .5}
            current, path_nodes, path_links, seen = flow.source, [prepared.reverse_node_map[flow.source]], [], {flow.source}
            while current != flow.destination:
                choices = sorted(a for a in selected if a[0] == current)
                if len(choices) != 1: raise AdapterError(f"JRS route for {fid} has {len(choices)} outgoing arcs at {current}")
                arc = choices[0]; logical_link = prepared.arc_to_logical_link[arc]
                source_name = prepared.reverse_node_map[arc[0]]
                start = round(model.getVal(variables["t"][(fid, arc)])); end = start + flow.tx_ns
                path_links.append(logical_link)
                route_schedule.append({"flow_id": fid, "logical_link": logical_link,
                                       "source": source_name,
                                       "destination": prepared.reverse_node_map[arc[1]],
                                       "start_ns": start, "end_ns": end})
                if nodes[source_name]["type"] == "switch":
                    binding = _port_binding(port_map, logical_link, source_name)
                    windows.append({"flow_id": fid, "logical_link": logical_link, "switch": source_name,
                                    "egress_path": binding["egress_path"], "start_ns": start, "end_ns": end,
                                    "traffic_class": defs[fid]["traffic_class"]})
                    forwarding.append({"flow_id": fid, "switch": source_name,
                                       "destination": defs[fid]["destination"], "interface": binding["interface"],
                                       "logical_link": logical_link, "stream_handle": prepared.stream_handles[fid]})
                current = arc[1]
                if current in seen: raise AdapterError(f"JRS route loop for {fid}")
                seen.add(current); path_nodes.append(prepared.reverse_node_map[current])
            if len(path_links) != len(selected): raise AdapterError(f"JRS route for {fid} contains disconnected selected arcs")
            logical_routes.append({"flow_id": fid, "node_path": path_nodes, "link_path": path_links})
        scenario = prepared.scenario
        cycle = int(prepared.manifest["project_cycle_ns"])
        tt_classes = {int(f["traffic_class"]) for f in scenario["tt_flows"]}
        if len(tt_classes) != 1: raise AdapterError("profile conversion requires one TT traffic class")
        guard = seconds_to_ns(scenario["scheduling"]["ingress_margin_s"], "ingress_margin") + seconds_to_ns(scenario["scheduling"]["hop_margin_s"], "hop_margin")
        at = time.perf_counter_ns()
        profile = {"schema_version": 1, "scenario_sha256": scenario.get("scenario_sha256"),
                   "profile_id": "JRS_WA_SCIP", "forwarding_model": "stream-aware",
                   "logical_routes": sorted(logical_routes, key=lambda x: x["flow_id"]),
                   "routes": sorted(forwarding, key=lambda x: (x["flow_id"], x["switch"])),
                   "release_offsets_ns": {f.flow_id: f.release_ns for f in flows},
                   "gate_schedules": compile_gate_schedules(windows, cycle, next(iter(tt_classes)),
                                                              int(scenario["scheduling"]["be_traffic_class"]), guard),
                   "gcl_deployment_guard_ns": guard}
        profile["semantic_profile_hash"] = sha256_value({k: profile[k] for k in ("forwarding_model", "logical_routes", "routes", "gate_schedules")})
        result.timings_ms["profile_serialize"] = (time.perf_counter_ns() - at) / 1e6
        result.logical_routes = profile["logical_routes"]
        result.schedule_windows = sorted(windows, key=lambda x: (x["egress_path"], x["start_ns"], x["flow_id"]))
        result.statistics["route_schedule"] = sorted(route_schedule, key=lambda x: (x["flow_id"], x["start_ns"]))
        result.profile = profile
