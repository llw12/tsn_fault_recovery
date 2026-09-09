"""Outcome-independent static audit for exact fixed-release source-egress conflicts.

This module intentionally knows nothing about historical HNF observations or
solver outcomes.  It consumes byte-frozen scenarios, applies the qualified
H2S adapter's integer conversion, proves first-hop unavoidability from the
physical topology when possible, and builds a logical-flow conflict graph.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tools.deadline_feasibility_calibration import flow_kind
from tools.h2s_jrs_backend import DEFAULT_QUANTUM_NS, quantize_flow
from tools.jrs_wa_adapter import canonical_json_bytes, seconds_to_ns
from tools.tt_workload_density_calibration import DENSITIES, ORDER

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SOURCE = ROOT / "results" / "realistic_tsn_pf_cost" / "scenarios"
DENSITY_SOURCE = ROOT / "results" / "tt_workload_density_calibration" / "derived_scenarios"
HYPERCYCLE_NS = 8_000_000
HYPERCYCLE_TICKS = HYPERCYCLE_NS // DEFAULT_QUANTUM_NS
MVC_CPU_LIMIT_S = 10.0


class StaticAuditError(RuntimeError):
    """Raised when frozen inputs cannot support the declared static model."""


@dataclass(frozen=True)
class ScenarioRef:
    scenario: str
    density: str
    path: Path

    @property
    def scale(self) -> str:
        return self.scenario.split("_", 1)[0]

    @property
    def topology(self) -> str:
        return self.scenario.split("_", 1)[1]


@dataclass(frozen=True)
class TimedFlow:
    scenario: str
    density: str
    scale: str
    topology: str
    flow_id: str
    kind: str
    source: str
    destination: str
    source_egress: str
    source_egress_link: str
    qualified: bool
    period_ticks: int
    release_ticks: int
    deadline_ticks: int
    tx_ticks: int
    frame_size: int
    frames_per_hyper_cycle: int
    fixed_release: bool


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(path.read_bytes())
    return digest.hexdigest()


def scale_topology(scenario: str) -> tuple[str, str]:
    scale, topology = scenario.split("_", 1)
    return scale, topology


def scenario_ref(scenario: str, density: str) -> ScenarioRef:
    if scenario not in ORDER:
        raise StaticAuditError(f"UNKNOWN_SCENARIO:{scenario}")
    if density not in {item.tag for item in DENSITIES}:
        raise StaticAuditError(f"UNKNOWN_DENSITY:{density}")
    path = (ORIGINAL_SOURCE / f"{scenario}.json" if density == "D100"
            else DENSITY_SOURCE / f"{scenario}_{density}" / "scenario.json")
    if not path.is_file():
        raise StaticAuditError(f"MISSING_FROZEN_SCENARIO:{path}")
    return ScenarioRef(scenario=scenario, density=density, path=path)


def formal_scenario_refs(*, quick: bool = False) -> list[ScenarioRef]:
    if quick:
        return [scenario_ref("M_RING", "D070"), scenario_ref("L_RING", "D050")]
    return [scenario_ref(scenario, density.tag) for density in DENSITIES for scenario in ORDER]


def load_scenario(ref: ScenarioRef) -> dict[str, Any]:
    value = json.loads(ref.path.read_text(encoding="utf-8"))
    if not isinstance(value.get("tt_flows"), list) or not value["tt_flows"]:
        raise StaticAuditError(f"EMPTY_TT_WORKLOAD:{ref.scenario}:{ref.density}")
    return value


def _adjacency(scenario: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    node_ids = {str(node["id"]) for node in scenario["nodes"]}
    result: dict[str, list[tuple[str, str]]] = {node_id: [] for node_id in node_ids}
    for link in scenario["links"]:
        left, right, link_id = str(link["endpoint_a"]), str(link["endpoint_b"]), str(link["id"])
        if left not in result or right not in result:
            raise StaticAuditError(f"LINK_REFERENCES_UNKNOWN_NODE:{link_id}")
        result[left].append((right, link_id)); result[right].append((left, link_id))
    return {node: sorted(values) for node, values in result.items()}


def source_egress_audit(ref: ScenarioRef, scenario: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Qualify source egresses solely from frozen data-plane topology.

    A physical source with exactly one adjacent link satisfies the preregistered
    condition A.  Multi-egress sources are deliberately left unqualified;
    observed route choices are never promoted to a proof of unavoidability.
    """
    scale, topology = scale_topology(ref.scenario)
    node_types = {str(node["id"]): str(node.get("type", "")) for node in scenario["nodes"]}
    adjacency = _adjacency(scenario)
    rows: list[dict[str, Any]] = []
    by_flow: dict[str, dict[str, Any]] = {}
    for flow in sorted(scenario["tt_flows"], key=lambda item: str(item["id"])):
        flow_id, source, destination = str(flow["id"]), str(flow["source"]), str(flow["destination"])
        choices = adjacency.get(source, [])
        qualified = len(choices) == 1
        neighbor, link_id = choices[0] if qualified else ("", "")
        if qualified:
            basis = "UNIQUE_SOURCE_DATA_PLANE_EGRESS"
        elif not choices:
            basis = "SOURCE_HAS_NO_DATA_PLANE_EGRESS"
        else:
            basis = "FIRST_HOP_UNAVOIDABILITY_UNQUALIFIED"
        row = {
            "scenario": ref.scenario, "density": ref.density, "scale": scale, "topology": topology,
            "flow_id": flow_id, "flow_kind": flow_kind(flow_id), "source": source, "destination": destination,
            "source_node_type": node_types.get(source, ""), "source_outgoing_egress_count": len(choices),
            "candidate_first_egress_count": "", "first_egress_id": f"{source}->{neighbor}" if qualified else "",
            "first_egress_link": link_id, "canonical_source_egress": source if qualified else "",
            "unavoidable_first_egress": qualified, "qualification_basis": basis,
            "qualification_pass": qualified,
        }
        rows.append(row); by_flow[flow_id] = row
    return rows, by_flow


def timing_audit(ref: ScenarioRef, scenario: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Apply the qualified adapter's exact integer conversion once per flow."""
    cycle_ns = seconds_to_ns(scenario["simulation"]["cycle_time_s"], "hypercycle")
    if cycle_ns != HYPERCYCLE_NS:
        raise StaticAuditError(f"UNEXPECTED_HYPERCYCLE_NS:{ref.scenario}:{ref.density}:{cycle_ns}")
    if cycle_ns % DEFAULT_QUANTUM_NS:
        raise StaticAuditError("HYPERCYCLE_NOT_QUANTUM_ALIGNED")
    overhead = int(scenario["scheduling"]["frame_overhead_bytes"])
    rows: list[dict[str, Any]] = []
    by_flow: dict[str, dict[str, Any]] = {}
    for flow in sorted(scenario["tt_flows"], key=lambda item: str(item["id"])):
        timing = quantize_flow(flow, overhead, 1_000_000_000, DEFAULT_QUANTUM_NS)
        if cycle_ns // DEFAULT_QUANTUM_NS % int(timing["period_ticks"]):
            raise StaticAuditError(f"PERIOD_DOES_NOT_DIVIDE_HYPERCYCLE:{flow['id']}")
        timing = dict(timing)
        timing.update({
            "scenario": ref.scenario, "density": ref.density, "scale": ref.scale, "topology": ref.topology,
            "flow_id": str(flow["id"]), "flow_kind": flow_kind(str(flow["id"])),
            "frame_size": int(timing["upstream_equivalent_bytes"]),
            "frames_per_hyper_cycle": HYPERCYCLE_TICKS // int(timing["period_ticks"]),
            "fixed_release": True,
        })
        rows.append(timing); by_flow[str(flow["id"])] = timing
    return rows, by_flow


def circular_segments(start: int, duration: int, hypercycle_ticks: int = HYPERCYCLE_TICKS) -> list[tuple[int, int]]:
    """Represent one half-open circular interval by one or two ordinary intervals."""
    if duration <= 0 or duration > hypercycle_ticks:
        raise StaticAuditError(f"INVALID_INTERVAL_DURATION:{duration}")
    start %= hypercycle_ticks
    end = start + duration
    if end <= hypercycle_ticks:
        return [(start, end)]
    return [(start, hypercycle_ticks), (0, end - hypercycle_ticks)]


def intervals_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def overlap_ticks(left: tuple[int, int], right: tuple[int, int]) -> int:
    return max(0, min(left[1], right[1]) - max(left[0], right[0]))


def mandatory_instances(flow: TimedFlow, hypercycle_ticks: int = HYPERCYCLE_TICKS) -> list[dict[str, Any]]:
    expected = hypercycle_ticks // flow.period_ticks
    if expected != flow.frames_per_hyper_cycle:
        raise StaticAuditError(f"INSTANCE_COUNT_PARITY_FAILED:{flow.flow_id}")
    rows = []
    for index in range(expected):
        start = flow.release_ticks + index * flow.period_ticks
        if not 0 <= start < hypercycle_ticks:
            raise StaticAuditError(f"RELEASE_OUTSIDE_HYPERCYCLE:{flow.flow_id}:{index}")
        rows.append({"flow_id": flow.flow_id, "instance": index, "start": start,
                     "end": start + flow.tx_ticks, "segments": circular_segments(start, flow.tx_ticks, hypercycle_ticks)})
    return rows


def _self_collision(instances: list[dict[str, Any]]) -> bool:
    for index, left in enumerate(instances):
        for right in instances[index + 1:]:
            if any(intervals_overlap(a, b) for a in left["segments"] for b in right["segments"]):
                return True
    return False


def build_timed_flows(ref: ScenarioRef, scenario: dict[str, Any]) -> tuple[list[TimedFlow], list[dict[str, Any]], list[dict[str, Any]]]:
    egress_rows, egress = source_egress_audit(ref, scenario)
    timing_rows, timing = timing_audit(ref, scenario)
    flows: list[TimedFlow] = []
    instances_summary: list[dict[str, Any]] = []
    for flow_id in sorted(timing):
        row, egress_row = timing[flow_id], egress[flow_id]
        timed = TimedFlow(
            scenario=ref.scenario, density=ref.density, scale=ref.scale, topology=ref.topology,
            flow_id=flow_id, kind=str(row["flow_kind"]), source=str(egress_row["source"]),
            destination=str(egress_row["destination"]), source_egress=str(egress_row["canonical_source_egress"]),
            source_egress_link=str(egress_row["first_egress_link"]), qualified=bool(egress_row["qualification_pass"]),
            period_ticks=int(row["period_ticks"]), release_ticks=int(row["release_ticks"]),
            deadline_ticks=int(row["deadline_ticks"]), tx_ticks=int(row["tx_ticks"]), frame_size=int(row["frame_size"]),
            frames_per_hyper_cycle=int(row["frames_per_hyper_cycle"]), fixed_release=bool(row["fixed_release"]),
        )
        instances = mandatory_instances(timed)
        instances_summary.append({
            "scenario": ref.scenario, "density": ref.density, "scale": ref.scale, "topology": ref.topology,
            "flow_id": flow_id, "kind": timed.kind, "first_egress": timed.source_egress,
            "period_ticks": timed.period_ticks, "release_ticks": timed.release_ticks,
            "relative_deadline_ticks": timed.deadline_ticks, "tx_ticks": timed.tx_ticks,
            "frame_size": timed.frame_size, "instances_per_hypercycle": timed.frames_per_hyper_cycle,
            "first_interval_start": instances[0]["start"], "last_interval_start": instances[-1]["start"],
            "self_collision": _self_collision(instances), "fixed_release": timed.fixed_release,
        })
        flows.append(timed)
    return flows, egress_rows, timing_rows + instances_summary


def collision_events(flows: Iterable[TimedFlow], hypercycle_ticks: int = HYPERCYCLE_TICKS) -> list[dict[str, Any]]:
    grouped: dict[str, list[TimedFlow]] = defaultdict(list)
    for flow in flows:
        if flow.qualified and flow.fixed_release:
            grouped[flow.source_egress].append(flow)
    output: list[dict[str, Any]] = []
    for egress, group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda row: row.flow_id)
        cached = {flow.flow_id: mandatory_instances(flow, hypercycle_ticks) for flow in ordered}
        for position, left in enumerate(ordered):
            for right in ordered[position + 1:]:
                for left_instance in cached[left.flow_id]:
                    for right_instance in cached[right.flow_id]:
                        for left_segment in left_instance["segments"]:
                            for right_segment in right_instance["segments"]:
                                if intervals_overlap(left_segment, right_segment):
                                    output.append({
                                        "scenario": left.scenario, "scale": left.scale, "topology": left.topology,
                                        "density": left.density, "source_egress": egress,
                                        "flow_a": left.flow_id, "kind_a": left.kind, "instance_a": left_instance["instance"],
                                        "start_a": left_segment[0], "end_a": left_segment[1],
                                        "flow_b": right.flow_id, "kind_b": right.kind, "instance_b": right_instance["instance"],
                                        "start_b": right_segment[0], "end_b": right_segment[1],
                                        "overlap_ticks": overlap_ticks(left_segment, right_segment),
                                    })
    return sorted(output, key=lambda row: (row["scenario"], row["density"], row["source_egress"], row["flow_a"], row["flow_b"], row["instance_a"], row["instance_b"], row["start_a"], row["start_b"]))


def logical_conflicts(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[(event["scenario"], event["density"], event["source_egress"], event["flow_a"], event["flow_b"])].append(event)
    rows = []
    for key, values in sorted(grouped.items()):
        first = min(values, key=lambda row: (row["instance_a"], row["instance_b"], row["start_a"], row["start_b"]))
        rows.append({
            "scenario": key[0], "density": key[1], "source_egress": key[2], "flow_a": key[3], "flow_b": key[4],
            "kind_a": first["kind_a"], "kind_b": first["kind_b"], "collision_event_count": len(values),
            "minimum_overlap_ticks": min(int(row["overlap_ticks"]) for row in values),
            "maximum_overlap_ticks": max(int(row["overlap_ticks"]) for row in values),
            "first_collision_instance_a": first["instance_a"], "first_collision_instance_b": first["instance_b"],
        })
    return rows


def graph_from_conflicts(flow_ids: Iterable[str], conflicts: Iterable[dict[str, Any]]) -> tuple[list[str], list[tuple[str, str]]]:
    vertices = sorted(set(flow_ids))
    edges = sorted({tuple(sorted((str(row["flow_a"]), str(row["flow_b"])))) for row in conflicts})
    if any(left == right for left, right in edges) or any(left not in vertices or right not in vertices for left, right in edges):
        raise StaticAuditError("INVALID_LOGICAL_CONFLICT_GRAPH")
    return vertices, edges


def graph_sha256(vertices: Iterable[str], edges: Iterable[tuple[str, str]]) -> str:
    return canonical_sha256({"vertices": sorted(vertices), "edges": [list(edge) for edge in sorted(edges)]})


def graph_components(vertices: Iterable[str], edges: Iterable[tuple[str, str]]) -> list[tuple[str, ...]]:
    adjacency: dict[str, set[str]] = {vertex: set() for vertex in vertices}
    for left, right in edges:
        adjacency[left].add(right); adjacency[right].add(left)
    result = []
    unseen = set(adjacency)
    while unseen:
        start = min(unseen); unseen.remove(start); queue = deque([start]); component = [start]
        while queue:
            current = queue.popleft()
            for neighbor in sorted(adjacency[current]):
                if neighbor in unseen:
                    unseen.remove(neighbor); component.append(neighbor); queue.append(neighbor)
        result.append(tuple(sorted(component)))
    return sorted(result, key=lambda item: (item[0], len(item), item))


def is_vertex_cover(edges: Iterable[tuple[str, str]], cover: Iterable[str]) -> bool:
    chosen = set(cover)
    return all(left in chosen or right in chosen for left, right in edges)


def is_independent(edges: Iterable[tuple[str, str]], retained: Iterable[str]) -> bool:
    values = set(retained)
    return all(left not in values or right not in values for left, right in edges)


def _component_edges(component: Iterable[str], edges: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    allowed = set(component)
    return sorted((left, right) for left, right in edges if left in allowed and right in allowed)


def _bipartition(vertices: Iterable[str], edges: Iterable[tuple[str, str]]) -> dict[str, int] | None:
    adjacency: dict[str, set[str]] = {vertex: set() for vertex in vertices}
    for left, right in edges:
        adjacency[left].add(right); adjacency[right].add(left)
    colors: dict[str, int] = {}
    for start in sorted(adjacency):
        if start in colors:
            continue
        colors[start] = 0; queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in sorted(adjacency[current]):
                if neighbor not in colors:
                    colors[neighbor] = 1 - colors[current]; queue.append(neighbor)
                elif colors[neighbor] == colors[current]:
                    return None
    return colors


def _bipartite_minimum_cover(vertices: Iterable[str], edges: Iterable[tuple[str, str]], colors: dict[str, int]) -> tuple[str, ...]:
    left = sorted(vertex for vertex in vertices if colors[vertex] == 0)
    right = sorted(vertex for vertex in vertices if colors[vertex] == 1)
    neighbors: dict[str, list[str]] = {vertex: [] for vertex in left}
    for a, b in edges:
        source, target = (a, b) if colors[a] == 0 else (b, a)
        neighbors[source].append(target)
    for values in neighbors.values(): values.sort()
    match_left: dict[str, str | None] = {vertex: None for vertex in left}
    match_right: dict[str, str | None] = {vertex: None for vertex in right}
    while True:
        seen: set[str] = set()
        def augment(vertex: str) -> bool:
            for neighbor in neighbors[vertex]:
                if neighbor in seen: continue
                seen.add(neighbor)
                if match_right[neighbor] is None or augment(str(match_right[neighbor])):
                    match_left[vertex] = neighbor; match_right[neighbor] = vertex; return True
            return False
        changed = False
        for vertex in left:
            if match_left[vertex] is None:
                seen.clear(); changed = augment(vertex) or changed
        if not changed: break
    reachable_left = {vertex for vertex in left if match_left[vertex] is None}; reachable_right: set[str] = set(); queue = deque(sorted(reachable_left))
    while queue:
        vertex = queue.popleft()
        for neighbor in neighbors[vertex]:
            if match_left[vertex] == neighbor or neighbor in reachable_right: continue
            reachable_right.add(neighbor)
            mate = match_right[neighbor]
            if mate is not None and mate not in reachable_left:
                reachable_left.add(mate); queue.append(mate)
    cover = (set(left) - reachable_left) | reachable_right
    if not is_vertex_cover(edges, cover):
        raise StaticAuditError("KONIG_COVER_CONSTRUCTION_FAILED")
    return tuple(sorted(cover))


def _maximal_matching_lower_bound(edges: Iterable[tuple[str, str]]) -> int:
    used: set[str] = set(); count = 0
    for left, right in sorted(edges):
        if left not in used and right not in used:
            used.update((left, right)); count += 1
    return count


def _greedy_cover(edges: Iterable[tuple[str, str]]) -> set[str]:
    remaining = set(edges); cover: set[str] = set()
    while remaining:
        degree: dict[str, int] = defaultdict(int)
        for left, right in remaining: degree[left] += 1; degree[right] += 1
        selected = min(((-count, vertex) for vertex, count in degree.items()))[1]
        cover.add(selected); remaining = {edge for edge in remaining if selected not in edge}
    return cover


def _branch_and_bound_cover(vertices: tuple[str, ...], edges: list[tuple[str, str]], cpu_limit_s: float) -> tuple[bool, tuple[str, ...], int, int, float]:
    started = time.process_time(); deadline = started + cpu_limit_s
    best = _greedy_cover(edges)
    lower_bound = _maximal_matching_lower_bound(edges)
    timed_out = False

    def search(remaining: tuple[tuple[str, str], ...], selected: set[str]) -> None:
        nonlocal best, timed_out
        if time.process_time() >= deadline:
            timed_out = True; return
        if not remaining:
            if len(selected) < len(best) or (len(selected) == len(best) and tuple(sorted(selected)) < tuple(sorted(best))):
                best = set(selected)
            return
        bound = _maximal_matching_lower_bound(remaining)
        if len(selected) + bound >= len(best): return
        degree: dict[str, int] = defaultdict(int)
        for left, right in remaining: degree[left] += 1; degree[right] += 1
        left, right = min(remaining)
        choices = sorted((left, right), key=lambda vertex: (-degree[vertex], vertex))
        for chosen in choices:
            next_edges = tuple(edge for edge in remaining if chosen not in edge)
            search(next_edges, selected | {chosen})
            if timed_out: return

    search(tuple(sorted(edges)), set())
    elapsed_ms = (time.process_time() - started) * 1000.0
    exact = not timed_out
    return exact, tuple(sorted(best)), lower_bound, len(best), elapsed_ms


def minimum_vertex_cover(vertices: Iterable[str], edges: Iterable[tuple[str, str]], *, cpu_limit_s: float = MVC_CPU_LIMIT_S) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    vertices = tuple(sorted(vertices)); edges = sorted(edges); rows: list[dict[str, Any]] = []; total_lower = total_upper = 0; total_cover: list[str] = []; all_exact = True; total_ms = 0.0
    for index, component in enumerate(graph_components(vertices, edges), 1):
        component_edges = _component_edges(component, edges); started = time.process_time()
        if not component_edges:
            exact, cover, lower, upper, method = True, (), 0, 0, "EMPTY_COMPONENT"
            elapsed_ms = (time.process_time() - started) * 1000.0
        else:
            colors = _bipartition(component, component_edges)
            if colors is not None:
                cover = _bipartite_minimum_cover(component, component_edges, colors)
                exact, lower, upper, method = True, len(cover), len(cover), "KONIG_HOPCROFT_KARP"
                elapsed_ms = (time.process_time() - started) * 1000.0
            else:
                exact, cover, lower, upper, elapsed_ms = _branch_and_bound_cover(component, component_edges, cpu_limit_s)
                method = "DETERMINISTIC_BRANCH_AND_BOUND_MAXIMAL_MATCHING_LOWER_BOUND"
        if exact and not is_vertex_cover(component_edges, cover):
            raise StaticAuditError("MVC_COVER_VALIDATION_FAILED")
        total_lower += lower; total_upper += upper; total_ms += elapsed_ms; all_exact &= exact
        if exact: total_cover.extend(cover)
        rows.append({
            "component_index": index, "component_vertices": len(component), "component_edges": len(component_edges),
            "component_flow_ids": ";".join(component), "bipartite": _bipartition(component, component_edges) is not None,
            "mvc_exact": exact, "mvc_size": len(cover) if exact else "", "mvc_lower_bound": lower,
            "mvc_upper_bound": upper, "one_minimum_cover_flow_ids": ";".join(cover) if exact else "",
            "solver_method": method, "solver_ms": round(elapsed_ms, 3),
        })
    summary = {"graph_vertices": len(vertices), "graph_edges": len(edges), "component_count": len(rows),
               "mvc_exact": all_exact, "mvc_size": total_upper if all_exact else "", "mvc_lower_bound": total_lower,
               "mvc_upper_bound": total_upper, "one_minimum_cover_flow_ids": ";".join(sorted(total_cover)) if all_exact else "",
               "solver_method": "COMPONENTWISE_EXACT", "solver_ms": round(total_ms, 3)}
    return rows, summary


def analyze_scenario(ref: ScenarioRef, scenario: dict[str, Any] | None = None) -> dict[str, Any]:
    scenario = scenario or load_scenario(ref)
    flows, egress_rows, timing_and_instance_rows = build_timed_flows(ref, scenario)
    timing_rows = [row for row in timing_and_instance_rows if "backend_quantum_ns" in row]
    instance_rows = [row for row in timing_and_instance_rows if "instances_per_hypercycle" in row]
    events = collision_events(flows); conflicts = logical_conflicts(events)
    vertices, edges = graph_from_conflicts((flow.flow_id for flow in flows), conflicts)
    components, mvc = minimum_vertex_cover(vertices, edges)
    self_collisions = sum(bool(row["self_collision"]) for row in instance_rows)
    nontrivial = [row for row in components if int(row["component_edges"]) > 0]
    graph_summary = {
        "scenario": ref.scenario, "scale": ref.scale, "topology": ref.topology, "density": ref.density,
        "vertex_count": len(vertices), "edge_count": len(edges),
        "conflicting_flow_count": len({vertex for edge in edges for vertex in edge}),
        "connected_component_count": len(components), "nontrivial_component_count": len(nontrivial),
        "largest_component_size": max((int(row["component_vertices"]) for row in components), default=0),
        "self_collision_count": self_collisions, "graph_sha256": graph_sha256(vertices, edges),
    }
    for row in components: row.update({"scenario": ref.scenario, "scale": ref.scale, "topology": ref.topology, "density": ref.density})
    mvc.update({"scenario": ref.scenario, "scale": ref.scale, "topology": ref.topology, "density": ref.density})
    return {"ref": ref, "flows": flows, "source_egress_rows": egress_rows, "timing_rows": timing_rows,
            "instance_rows": instance_rows, "events": events, "conflicts": conflicts, "vertices": vertices,
            "edges": edges, "components": components, "mvc": mvc, "graph_summary": graph_summary}
