#!/usr/bin/env python3
"""Generate the frozen structured-redundant-mesh exp10 scenario family.

The generator deliberately has no data-dependent tuning: a parameter tuple always
produces byte-identical YAML and the same topology/workload semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_VERSION = "structured-redundant-mesh-v1"
PACKET_SIZES = (180, 220, 260, 300, 340)
DEADLINES_US = (900, 920, 940, 950, 930)


def sw(index: int) -> str:
    return f"sw{index + 1:02d}"


def es(index: int) -> str:
    return f"es{index + 1:02d}"


def link_id(left: str, right: str) -> str:
    return f"l_{left}_{right}"


def yaml_text(*, switches: int, rows: int, cols: int, end_systems: int,
              tt_flows: int, be_flows: int, seed: int, name: str) -> str:
    if rows * cols != switches:
        raise ValueError("rows * cols must equal switches")
    if min(end_systems, tt_flows, be_flows) < 1:
        raise ValueError("flow and end-system counts must be positive")
    links: list[tuple[str, str, str]] = []
    # Canonical grid links, followed by one non-grid diagonal per column.  This
    # keeps density near the structured20 family without creating a dense graph.
    for row in range(rows):
        for col in range(cols - 1):
            a, b = sw(row * cols + col), sw(row * cols + col + 1)
            links.append((link_id(a, b), a, b))
    for row in range(rows - 1):
        for col in range(cols):
            a, b = sw(row * cols + col), sw((row + 1) * cols + col)
            links.append((link_id(a, b), a, b))
    for col in range(cols):
        a = sw((col % (rows - 1)) * cols + col)
        b = sw(((col % (rows - 1)) + 1) * cols + ((col + 2) % cols))
        pair = {a, b}
        if not any({left, right} == pair for _, left, right in links):
            links.append((link_id(a, b), a, b))
    links.sort()

    lines = [
        "schema_version: 1", f"name: {name}", "", "simulation:",
        "  duration: 30ms", "  cycle_time: 1ms", "  time_quantum: 1us",
        "  failure_time: 10ms", "  solver_delay: 1ms", f"  random_seed: {seed}", "",
        "network:", "  default_link:", "    bitrate: 1Gbps", "    propagation_delay: 0us", "",
        "scheduling:", "  ingress_margin: 25us", "  hop_margin: 3us",
        "  endpoint_budget: 300us", "  frame_overhead: 64B", "  be_traffic_class: 0", "",
        "topology:", "  end_systems:",
    ]
    lines += [f"    - id: {es(index)}" for index in range(end_systems)]
    lines += ["  switches:"] + [f"    - id: {sw(index)}" for index in range(switches)]
    lines += ["  links:"]
    # Evenly spaced canonical attachment positions; access links are outside the
    # auto switch-switch fault scope by schema definition.
    for index in range(end_systems):
        attachment = (index * switches) // end_systems
        lines.append(f"    - {{id: {link_id(es(index), sw(attachment))}, endpoints: [{es(index)}, {sw(attachment)}]}}")
    for ident, left, right in links:
        lines.append(f"    - {{id: {ident}, endpoints: [{left}, {right}]}}")
    lines += ["", "traffic:", "  tt_flows:"]
    for index in range(tt_flows):
        source = index % end_systems
        destination = (source + end_systems // 2 + 1 + (index // end_systems)) % end_systems
        if destination == source:
            destination = (destination + 1) % end_systems
        # A fixed 0..380us horizon makes the release-load shape invariant with
        # scale rather than extending it with the number of flows.
        release_us = round(index * 380 / max(tt_flows - 1, 1))
        lines.append(
            "    - {id: TT%02d, source: %s, destination: %s, packet_size: %dB, period: 1ms, "
            "deadline: %dus, release_offset: %dus, pcp: 4, traffic_class: 1}" %
            (index + 1, es(source), es(destination), PACKET_SIZES[index % len(PACKET_SIZES)],
             DEADLINES_US[index % len(DEADLINES_US)], release_us))
    lines += ["  be_flows:"]
    for index in range(be_flows):
        source = (index * 2) % end_systems
        destination = (source + end_systems // 2 + 1) % end_systems
        lines.append(
            "    - {id: BE%02d, source: %s, destination: %s, packet_size: %dB, interval: 500us, "
            "release_offset: %dus, pcp: 0, traffic_class: 0}" %
            (index + 1, es(source), es(destination), 1000 + 100 * (index % 4), (index * 80) % 500))
    lines += ["", "faults:", "  model: single_link", "  candidate_selection:",
              "    mode: auto", "    scope: switch-switch", "    criterion: tt-primary-route-used", ""]
    return "\n".join(lines)


def manifest(path: Path, args: argparse.Namespace) -> dict:
    payload = {
        "generator_version": GENERATOR_VERSION,
        "generator_parameters": {key: getattr(args, key) for key in
                                 ("switches", "rows", "cols", "end_systems", "tt_flows", "be_flows", "seed")},
        "scenario_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "switch_count": args.switches, "ES_count": args.end_systems,
        "TT_count": args.tt_flows, "BE_count": args.be_flows,
        "topology_family": "structured-redundant-mesh",
        "rows": args.rows, "cols": args.cols,
        "cross_link_policy": "one deterministic non-grid diagonal per column",
        "flow_generation_policy": "cyclic distributed endpoints v1",
        "release_offset_policy": "uniform 0..380us inclusive v1",
        "deadline_policy": "fixed cyclic 900/920/940/950/930us v1",
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--switches", required=True, type=int)
    parser.add_argument("--rows", required=True, type=int)
    parser.add_argument("--cols", required=True, type=int)
    parser.add_argument("--end-systems", required=True, type=int)
    parser.add_argument("--tt-flows", required=True, type=int)
    parser.add_argument("--be-flows", required=True, type=int)
    parser.add_argument("--seed", default=0, type=int)
    parser.add_argument("--name")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    name = args.name or f"structured{args.switches}_auto"
    output = args.output or ROOT / "configs/scenarios" / f"{name}.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml_text(name=name, **{key: getattr(args, key) for key in
                      ("switches", "rows", "cols", "end_systems", "tt_flows", "be_flows", "seed")}), encoding="utf-8")
    manifest_path = args.manifest or output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest(output, args), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
