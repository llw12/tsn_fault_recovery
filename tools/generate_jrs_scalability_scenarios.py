#!/usr/bin/env python3
"""Deterministic medium-redundancy scenario family for exp14."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

GENERATOR_VERSION = "exp14-circulant-degree4-v1"
PACKET_SIZES = (180, 220, 260, 300, 340)
DEADLINES_US = (900, 920, 940, 950, 930)


@dataclass(frozen=True)
class Scale:
    scenario: str
    switches: int
    end_systems: int
    tt_flows: int


MAIN_SCALES = (
    Scale("S1", 30, 20, 100), Scale("S2", 60, 40, 200),
    Scale("S3", 100, 50, 500), Scale("S4", 170, 80, 500),
    Scale("S5", 170, 80, 1000), Scale("S6", 340, 160, 1000),
    Scale("S7", 340, 160, 2000), Scale("S8", 680, 320, 2000),
)
FIXED_SWEEP = tuple(Scale(f"F150_TT{count}", 100, 50, count) for count in (100, 250, 500, 750, 1000))


def _sw(index: int) -> str: return f"sw{index + 1:04d}"
def _es(index: int) -> str: return f"es{index + 1:04d}"
def _lid(a: str, b: str) -> str: return f"l_{a}_{b}"


def topology_edges(switches: int) -> list[tuple[str, str, str]]:
    pairs = set()
    for index in range(switches):
        for offset in (1, 2):
            a, b = sorted((_sw(index), _sw((index + offset) % switches)))
            pairs.add((a, b))
    return [(_lid(a, b), a, b) for a, b in sorted(pairs)]


def topology_sha(switches: int, end_systems: int) -> str:
    payload = [(f"access-{i}", _es(i), _sw((i * switches) // end_systems)) for i in range(end_systems)]
    payload += topology_edges(switches)
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def yaml_text(scale: Scale, seed: int = 1400) -> str:
    s, e, f = scale.switches, scale.end_systems, scale.tt_flows
    lines = ["schema_version: 1", f"name: exp14_{scale.scenario.lower()}", "forwardingModel: stream-aware", "",
             "simulation:", "  duration: 30ms", "  cycle_time: 1ms", "  time_quantum: 1ns",
             "  failure_time: 10ms", "  solver_delay: 0ms", f"  random_seed: {seed}", "",
             "network:", "  default_link:", "    bitrate: 1Gbps", "    propagation_delay: 0us", "",
             "scheduling:", "  ingress_margin: 25us", "  hop_margin: 3us", "  endpoint_budget: 300us",
             "  frame_overhead: 64B", "  be_traffic_class: 0", "", "topology:", "  end_systems:"]
    lines += [f"    - id: {_es(i)}" for i in range(e)]
    lines += ["  switches:"] + [f"    - id: {_sw(i)}" for i in range(s)]
    lines += ["  links:"]
    for i in range(e):
        a, b = _es(i), _sw((i * s) // e)
        lines.append(f"    - {{id: {_lid(a, b)}, endpoints: [{a}, {b}]}}")
    for ident, a, b in topology_edges(s):
        lines.append(f"    - {{id: {ident}, endpoints: [{a}, {b}]}}")
    lines += ["", "traffic:", "  tt_flows:"]
    for i in range(f):
        src = i % e
        dst = (src + e // 2 + 1 + i // e) % e
        if dst == src: dst = (dst + 1) % e
        release = round(i * 380 / max(f - 1, 1))
        lines.append("    - {id: TT%04d, source: %s, destination: %s, packet_size: %dB, period: 1ms, deadline: %dus, release_offset: %dus, pcp: 4, traffic_class: 1}" %
                     (i + 1, _es(src), _es(dst), PACKET_SIZES[i % 5], DEADLINES_US[i % 5], release))
    lines += ["  be_flows: []", "", "faults:", "  model: single_link", "  candidate_selection:",
              "    mode: auto", "    scope: switch-switch", "    criterion: tt-primary-route-used", ""]
    return "\n".join(lines)


def scenario_audit(scale: Scale) -> dict:
    internal = topology_edges(scale.switches)
    return {"scenario": scale.scenario, "switch_count": scale.switches, "ES_count": scale.end_systems,
            "total_node_count": scale.switches + scale.end_systems, "TT_count": scale.tt_flows,
            "BE_count": 0, "internal_link_count": len(internal),
            "average_switch_degree": 2 * len(internal) / scale.switches,
            "topology_sha256": topology_sha(scale.switches, scale.end_systems),
            "generator_version": GENERATOR_VERSION, "seed": 1400}
