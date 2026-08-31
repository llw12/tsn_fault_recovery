#!/usr/bin/env python3
"""Deterministic graph metrics for exp12 nested topology redundancy."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.scenario_model import load_scenario


def switch_graph(model) -> tuple[list[str], set[tuple[str, str]]]:
    switches = sorted(n.id for n in model.nodes if n.type == "switch")
    switch_set = set(switches)
    edges = {tuple(sorted((l.endpoint_a, l.endpoint_b))) for l in model.links
             if l.endpoint_a in switch_set and l.endpoint_b in switch_set}
    return switches, edges


def adjacency(nodes: list[str], edges: set[tuple[str, str]]) -> dict[str, set[str]]:
    result = {node: set() for node in nodes}
    for left, right in edges:
        result[left].add(right); result[right].add(left)
    return result


def component_count(nodes: list[str], edges: set[tuple[str, str]]) -> int:
    graph = adjacency(nodes, edges); seen = set(); count = 0
    for root in nodes:
        if root in seen: continue
        count += 1; pending = [root]; seen.add(root)
        while pending:
            for node in graph[pending.pop()]:
                if node not in seen: seen.add(node); pending.append(node)
    return count


def bridges(nodes: list[str], edges: set[tuple[str, str]]) -> set[tuple[str, str]]:
    graph = adjacency(nodes, edges); entered = {}; low = {}; result = set(); tick = 0
    def visit(node: str, parent: str | None) -> None:
        nonlocal tick
        tick += 1; entered[node] = low[node] = tick
        for neighbor in sorted(graph[node]):
            if neighbor == parent: continue
            if neighbor in entered:
                low[node] = min(low[node], entered[neighbor])
            else:
                visit(neighbor, node); low[node] = min(low[node], low[neighbor])
                if low[neighbor] > entered[node]: result.add(tuple(sorted((node, neighbor))))
    for node in nodes:
        if node not in entered: visit(node, None)
    return result


def edge_connectivity(nodes: list[str], edges: set[tuple[str, str]], source: str, sink: str) -> int:
    """Unit-capacity undirected max-flow using symmetric residual arcs."""
    if source == sink: return 0
    capacity = defaultdict(int)
    for left, right in edges:
        capacity[(left, right)] += 1; capacity[(right, left)] += 1
    residual = defaultdict(int, capacity); total = 0
    while True:
        parent = {source: None}; pending = deque([source])
        while pending and sink not in parent:
            node = pending.popleft()
            for neighbor in nodes:
                if neighbor not in parent and residual[(node, neighbor)] > 0:
                    parent[neighbor] = node; pending.append(neighbor)
        if sink not in parent: break
        node = sink
        while parent[node] is not None:
            previous = parent[node]; residual[(previous, node)] -= 1
            residual[(node, previous)] += 1; node = previous
        total += 1
    return total


def global_edge_connectivity(nodes: list[str], edges: set[tuple[str, str]]) -> int:
    if component_count(nodes, edges) != 1: return 0
    return min(edge_connectivity(nodes, edges, nodes[0], node) for node in nodes[1:])


def attachment_switches(model) -> dict[str, str]:
    node_type = {n.id: n.type for n in model.nodes}; result = {}
    for link in model.links:
        if node_type[link.endpoint_a] == "end_system" and node_type[link.endpoint_b] == "switch":
            result[link.endpoint_a] = link.endpoint_b
        elif node_type[link.endpoint_b] == "end_system" and node_type[link.endpoint_a] == "switch":
            result[link.endpoint_b] = link.endpoint_a
    return result


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values); position = (len(ordered) - 1) * fraction
    lower = int(position); upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def metrics(model, level: str) -> tuple[dict, list[dict], list[dict]]:
    nodes, edges = switch_graph(model); graph = adjacency(nodes, edges)
    degrees = [len(graph[node]) for node in nodes]
    components = component_count(nodes, edges)
    row = {
        "level": level, "switch_count": len(nodes), "switch_edge_count": len(edges),
        "physical_link_count": len(model.links), "access_edge_count": len(model.links) - len(edges),
        "average_degree": sum(degrees) / len(degrees), "degree_min": min(degrees),
        "degree_p50": percentile(degrees, .5), "degree_p95": percentile(degrees, .95),
        "degree_max": max(degrees), "degree_stddev": statistics.pstdev(degrees),
        "degree_cv": statistics.pstdev(degrees) / statistics.mean(degrees),
        "density": 2 * len(edges) / (len(nodes) * (len(nodes) - 1)),
        "component_count": components, "bridge_count": len(bridges(nodes, edges)),
        "cycle_rank": len(edges) - len(nodes) + components,
        "global_edge_connectivity": global_edge_connectivity(nodes, edges),
    }
    degree_rows = [{"level": level, "switch": node, "degree": len(graph[node])} for node in nodes]
    attachment = attachment_switches(model); tt_rows = []
    for flow in model.tt_flows:
        source, sink = attachment[flow.source], attachment[flow.destination]
        tt_rows.append({"level": level, "flow_id": flow.id, "source_es": flow.source,
                        "destination_es": flow.destination, "source_switch": source,
                        "destination_switch": sink,
                        "edge_connectivity": edge_connectivity(nodes, edges, source, sink)})
    return row, degree_rows, tt_rows


def connectivity_margin(model, flow_ids: list[str], disabled_link_ids: set[str]) -> tuple[int, float]:
    nodes, edges = switch_graph(model)
    link_edges = {l.id: tuple(sorted((l.endpoint_a, l.endpoint_b))) for l in model.links}
    edges -= {link_edges[item] for item in disabled_link_ids if item in link_edges}
    attachment = attachment_switches(model); by_id = {f.id: f for f in model.tt_flows}
    values = [edge_connectivity(nodes, edges, attachment[by_id[item].source],
                                attachment[by_id[item].destination]) for item in flow_ids]
    return (min(values), statistics.mean(values)) if values else (0, 0.0)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        if rows: writer.writeheader(); writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    topology_rows = []; degree_rows = []; tt_rows = []
    for path in sorted(args.scenario_dir.glob("R*.yaml")):
        level = path.stem; row, degree, tt = metrics(load_scenario(path), level)
        topology_rows.append(row); degree_rows.extend(degree); tt_rows.extend(tt)
    write_csv(args.output / "topology_metrics.csv", topology_rows)
    write_csv(args.output / "degree_distribution.csv", degree_rows)
    write_csv(args.output / "tt_pair_edge_connectivity.csv", tt_rows)
    print(json.dumps(topology_rows, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
