#!/usr/bin/env python3
"""Generate the frozen, nested 5x8 topology-redundancy experiment family."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.generate_scalability_scenarios import yaml_text
from tools.scenario_model import load_scenario
GENERATOR_VERSION = "nested-grid-balanced-distance2-v1"
LEVELS = (("R0_GRID", 67), ("R1_CURRENT", 75), ("R2_D4", 80),
          ("R3_D5", 100), ("R4_D6", 120))
CURRENT_CROSS = (
    ("sw01", "sw11"), ("sw05", "sw15"), ("sw10", "sw20"), ("sw14", "sw24"),
    ("sw19", "sw29"), ("sw23", "sw25"), ("sw28", "sw38"), ("sw32", "sw34"),
)


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def coordinates(node: str) -> tuple[int, int]:
    index = int(node[2:]) - 1
    return divmod(index, 8)


def pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def base_grid() -> set[tuple[str, str]]:
    edges = set()
    for row in range(5):
        for col in range(7):
            edges.add(pair(f"sw{row * 8 + col + 1:02d}", f"sw{row * 8 + col + 2:02d}"))
    for row in range(4):
        for col in range(8):
            edges.add(pair(f"sw{row * 8 + col + 1:02d}", f"sw{(row + 1) * 8 + col + 1:02d}"))
    return edges


def distance2_candidates(existing: set[tuple[str, str]]) -> list[tuple[str, str]]:
    nodes = [f"sw{i:02d}" for i in range(1, 41)]
    return [pair(a, b) for i, a in enumerate(nodes) for b in nodes[i + 1:]
            if sum(abs(x - y) for x, y in zip(coordinates(a), coordinates(b))) == 2
            and pair(a, b) not in existing]


def balanced_add(existing: set[tuple[str, str]], count: int,
                 incidence: Counter[str], spatial: Counter[tuple[str, int]]) -> list[tuple[str, str]]:
    """Greedy score: degree balance, added incidence, spatial coverage, canonical IDs."""
    selected = []
    for _ in range(count):
        degree = Counter(node for edge in existing for node in edge)
        candidates = distance2_candidates(existing)
        if not candidates:
            raise RuntimeError("insufficient Manhattan-distance-2 candidates")
        def score(edge: tuple[str, str]):
            a, b = edge
            ar, ac = coordinates(a); br, bc = coordinates(b)
            coverage = spatial[("row", ar)] + spatial[("row", br)] + spatial[("col", ac)] + spatial[("col", bc)]
            return (max(degree[a], degree[b]), degree[a] + degree[b],
                    incidence[a] + incidence[b], coverage, a, b)
        chosen = min(candidates, key=score)
        existing.add(chosen); selected.append(chosen)
        for node in chosen:
            incidence[node] += 1
            row, col = coordinates(node)
            spatial[("row", row)] += 1; spatial[("col", col)] += 1
    return selected


def topology_family() -> tuple[dict[str, set[tuple[str, str]]], dict[tuple[str, str], str]]:
    edges = base_grid()
    layers = {edge: "BASE_GRID" for edge in edges}
    result = {"R0_GRID": set(edges)}
    for edge in map(lambda value: pair(*value), CURRENT_CROSS):
        edges.add(edge); layers[edge] = "CURRENT_CROSS"
    result["R1_CURRENT"] = set(edges)
    incidence: Counter[str] = Counter()
    spatial: Counter[tuple[str, int]] = Counter()
    for level, count, layer in (("R2_D4", 5, "D4_EXTRA"),
                                ("R3_D5", 20, "D5_EXTRA"),
                                ("R4_D6", 20, "D6_EXTRA")):
        for edge in balanced_add(edges, count, incidence, spatial):
            layers[edge] = layer
        result[level] = set(edges)
    return result, layers


def workload_payload(model) -> dict:
    return {
        "simulation": model.canonical_dict()["simulation"],
        "network": model.canonical_dict()["network"],
        "scheduling": model.canonical_dict()["scheduling"],
        "end_systems": [n.id for n in model.nodes if n.type == "end_system"],
        "attachments": sorted((l.id, l.endpoint_a, l.endpoint_b) for l in model.links
                              if "end_system" in {next(n.type for n in model.nodes if n.id == l.endpoint_a),
                                                  next(n.type for n in model.nodes if n.id == l.endpoint_b)}),
        "tt_flows": model.canonical_dict()["tt_flows"],
        "be_flows": model.canonical_dict()["be_flows"],
        "fault_policy": model.canonical_dict()["fault_candidate_policy"],
    }


def render(level: str, edges: set[tuple[str, str]], layers: dict[tuple[str, str], str]) -> str:
    name = f"exp12_{level.lower()}"
    original = yaml_text(switches=40, rows=5, cols=8, end_systems=20,
                         tt_flows=40, be_flows=8, seed=0, name=name)
    lines = original.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("    - {id: l_sw"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "")
    internal = []
    layer_count = Counter()
    for index, edge in enumerate(sorted(edges)):
        layer = layers[edge]; layer_count[layer] += 1
        ident = f"l_{edge[0]}_{edge[1]}" if layer == "BASE_GRID" else f"z_{layer.lower()}_{index:03d}"
        internal.append(f"    - {{id: {ident}, endpoints: [{edge[0]}, {edge[1]}]}}")
    return "\n".join(lines[:start] + internal + lines[end:])


def generate(output: Path) -> dict:
    family, layers = topology_family()
    output.mkdir(parents=True, exist_ok=True)
    manifests = {}
    workload_hash = None
    for level, expected_edges in LEVELS:
        path = output / f"{level}.yaml"
        path.write_text(render(level, family[level], layers), encoding="utf-8")
        model = load_scenario(path)
        payload = workload_payload(model)
        current_workload = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        workload_hash = workload_hash or current_workload
        if current_workload != workload_hash:
            raise RuntimeError("workload identity drift across redundancy levels")
        edge_rows = [{"endpoints": list(edge), "layer": layers[edge]} for edge in sorted(family[level])]
        manifest = {
            "schema_version": 1, "generator_version": GENERATOR_VERSION,
            "level": level, "target_average_degree": 2 * expected_edges / 40,
            "switch_count": 40, "switch_edge_count": len(family[level]),
            "physical_link_count": len(model.links), "workload_sha256": current_workload,
            "topology_edge_set_sha256": hashlib.sha256(canonical_bytes(edge_rows)).hexdigest(),
            "scenario_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "edges": edge_rows,
        }
        if len(family[level]) != expected_edges:
            raise RuntimeError(f"{level} edge count mismatch")
        manifest_path = output / f"{level}.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifests[level] = manifest
    return manifests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "configs/scenarios/exp12_redundancy")
    args = parser.parse_args()
    manifests = generate(args.output)
    print(json.dumps({key: value["switch_edge_count"] for key, value in manifests.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
