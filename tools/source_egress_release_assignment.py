"""Pure input construction and integrity checks for exp18j.

This module deliberately has no Z3 import and no scheduling-result import.
It reads the frozen D100 scenarios, reuses exp18i's timing/source-egress
model, and prepares the source-local exact-release problem for a separate
solver process.  Keeping this boundary explicit prevents release assignment
from being influenced by HNF identities or any constructive scheduler result.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from tools.fixed_release_collision_audit import (
    HYPERCYCLE_TICKS, ORDER, ROOT, ScenarioRef, analyze_scenario, build_timed_flows,
    canonical_sha256, collision_events, graph_from_conflicts, load_scenario,
    minimum_vertex_cover, scenario_ref, sha256_file,
)
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.tt_workload_density_calibration import expected_instances, flow_kind, topology_projection

OUT = ROOT / "results" / "source_egress_aware_release_assignment"
SOURCE = ROOT / "results" / "realistic_tsn_pf_cost" / "scenarios"
QUANTUM_NS = 100
RELEASE_WINDOW_DIVISOR = 5
EXPECTED_FLOW_COUNTS = {"M": 352, "L": 928}
EXPECTED_INSTANCE_COUNTS = {"M": 3616, "L": 9632}

# This is a configuration declaration, not a parameter sweep.  The adapter
# supplies its rating default of one, which upstream defines as PATH_LENGTH.
FORMAL_BACKEND_CONFIG = {
    "route_scope": "ALL_REROUTE", "primary_algorithm": "H2S", "celf_fallback": True,
    "h2s_flow_sorting": 4, "h2s_sorter": "LOW_PERIOD_FLOWS_FIRST",
    "configuration_rating": "PATH_LENGTH", "configuration_rating_cli": 1,
    "placement": "ASAP", "placement_cli": 0, "routing": "DIJKSTRA_OVERLAP",
    "candidate_paths_k": 5, "quantum_ns": 100, "global_seed": 1024,
    "backend_seed": 1024, "threads": 1, "timeout_s_h2s": 30,
    "timeout_s_celf": 30, "memory_limit_mb": 8192,
    "h2s_tiebreak_mode": "BASELINE", "h2s_tiebreak_seed": 0,
    "multistart": False, "diagnostic_trace": False,
}


class ReleaseAssignmentError(RuntimeError):
    """A frozen-input or release-assignment contract failure."""


def source_scenario_path(scenario_id: str) -> Path:
    if scenario_id not in ORDER:
        raise ReleaseAssignmentError(f"UNKNOWN_SCENARIO:{scenario_id}")
    return SOURCE / f"{scenario_id}.json"


def source_scenarios() -> dict[str, dict[str, Any]]:
    return {scenario_id: load_scenario(scenario_ref(scenario_id, "D100")) for scenario_id in ORDER}


def ticks_to_seconds(ticks: int) -> float:
    """Return a JSON number whose decimal representation is 100-ns exact."""
    return float(Decimal(ticks) * Decimal(QUANTUM_NS) / Decimal(1_000_000_000))


def release_map_sha256(releases: dict[str, int]) -> str:
    return canonical_sha256([{"flow_id": flow_id, "release_ticks": int(releases[flow_id])}
                             for flow_id in sorted(releases)])


def _ref(scenario_id: str) -> ScenarioRef:
    return ScenarioRef(scenario_id, "D100", source_scenario_path(scenario_id))


def _flow_rows(scenario_id: str, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    flows, _, _ = build_timed_flows(_ref(scenario_id), scenario)
    return [{"flow_id": flow.flow_id, "flow_kind": flow.kind, "source": flow.source,
             "destination": flow.destination, "source_egress": flow.source_egress,
             "source_egress_link": flow.source_egress_link, "qualified": flow.qualified,
             "period_ticks": flow.period_ticks, "tx_ticks": flow.tx_ticks,
             "original_release_ticks": flow.release_ticks,
             "upper_release_ticks": flow.period_ticks // RELEASE_WINDOW_DIVISOR,
             "frames_per_hypercycle": flow.frames_per_hyper_cycle,
             "deadline_ticks": flow.deadline_ticks}
            for flow in sorted(flows, key=lambda item: item.flow_id)]


def source_egress_grouping(scenarios: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], bool]:
    """Read and compare grouping for all three topologies of each scale.

    The optimizer consumes the returned scale-only groups.  A mismatch is not
    papered over: callers must stop before optimization or scenario writing.
    """
    rows: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    parity_pass = True
    for scale in ("M", "L"):
        ids = [f"{scale}_{topology}" for topology in ("RING", "REDSTAR", "ROR")]
        by_scenario = {scenario_id: _flow_rows(scenario_id, scenarios[scenario_id]) for scenario_id in ids}
        reference = {row["flow_id"]: row for row in by_scenario[ids[0]]}
        reference_signature = {flow_id: (row["source"], row["source_egress"], row["qualified"])
                               for flow_id, row in reference.items()}
        scale_pass = True
        for scenario_id in ids:
            current = {row["flow_id"]: row for row in by_scenario[scenario_id]}
            signature = {flow_id: (row["source"], row["source_egress"], row["qualified"])
                         for flow_id, row in current.items()}
            equal = signature == reference_signature
            scale_pass &= equal
            for row in by_scenario[scenario_id]:
                rows.append({"scale": scale, "scenario": scenario_id, "topology": scenario_id.split("_", 1)[1],
                             **row, "same_scale_grouping_pass": equal,
                             "optimizer_input_excludes_topology": True})
        parity_pass &= scale_pass
        groups[scale] = [reference[flow_id] for flow_id in sorted(reference)]
    return rows, groups, parity_pass


def source_groups_for_optimizer(scale: str, flows: Iterable[dict[str, Any]],
                                original_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Produce topology-free, source-local exact-optimization groups."""
    degrees: Counter[str] = Counter()
    for left, right in original_analysis["edges"]:
        degrees[left] += 1; degrees[right] += 1
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for flow in flows:
        if not bool(flow["qualified"]):
            raise ReleaseAssignmentError(f"UNQUALIFIED_SOURCE_EGRESS:{scale}:{flow['flow_id']}")
        original = int(flow["original_release_ticks"])
        upper = int(flow["upper_release_ticks"])
        if not 0 <= original <= upper:
            raise ReleaseAssignmentError(f"SOURCE_RELEASE_INPUT_INVALID:{scale}:{flow['flow_id']}:{original}:{upper}")
        item = dict(flow)
        item["original_collision_degree"] = int(degrees[flow["flow_id"]])
        grouped[(str(flow["source"]), str(flow["source_egress"]))].append(item)
    result = []
    for (source, egress), values in sorted(grouped.items()):
        flow_ids = [str(row["flow_id"]) for row in sorted(values, key=lambda item: item["flow_id"])]
        local_edges = [edge for edge in original_analysis["edges"] if edge[0] in flow_ids and edge[1] in flow_ids]
        _, mvc = minimum_vertex_cover(flow_ids, local_edges)
        result.append({"scale": scale, "source": source, "source_egress": egress,
                       "flow_count": len(values),
                       "instance_count": sum(int(row["frames_per_hypercycle"]) for row in values),
                       "original_collision_edges": len(local_edges),
                       "original_mvc_size": int(mvc["mvc_size"]),
                       "flows": sorted(values, key=lambda item: item["flow_id"])})
    if sum(len(row["flows"]) for row in result) != EXPECTED_FLOW_COUNTS[scale]:
        raise ReleaseAssignmentError(f"FLOW_GROUPING_NOT_EXACT_PARTITION:{scale}")
    return result


def optimizer_problem(scenarios: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Return (grouping CSV rows, topology-free solver groups, parity pass)."""
    grouping, by_scale, parity = source_egress_grouping(scenarios)
    groups: list[dict[str, Any]] = []
    for scale in ("M", "L"):
        analysis = analyze_scenario(_ref(f"{scale}_RING"), scenarios[f"{scale}_RING"])
        groups.extend(source_groups_for_optimizer(scale, by_scale[scale], analysis))
    return grouping, groups, parity


def assignment_rows(groups: Iterable[dict[str, Any]], releases: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in groups:
        scale = str(group["scale"])
        for flow in group["flows"]:
            old = int(flow["original_release_ticks"]); new = int(releases[scale][flow["flow_id"]])
            shift = abs(new - old)
            rows.append({"scale": scale, "flow_id": flow["flow_id"], "flow_kind": flow["flow_kind"],
                         "source": flow["source"], "source_egress": flow["source_egress"],
                         "period_ticks": flow["period_ticks"], "tx_ticks": flow["tx_ticks"],
                         "original_release_ticks": old, "new_release_ticks": new,
                         "changed": new != old, "absolute_shift_ticks": shift,
                         "absolute_shift_ns": shift * QUANTUM_NS,
                         "relative_shift_over_period": shift / int(flow["period_ticks"]),
                         "original_collision_degree": flow["original_collision_degree"]})
    return sorted(rows, key=lambda item: (item["scale"], item["flow_id"]))


def apply_release_assignment(parent: dict[str, Any], scenario_id: str, releases: dict[str, int]) -> dict[str, Any]:
    child = copy.deepcopy(parent)
    child["scenario_name"] = f"{scenario_id}_SAR"
    ids = {str(flow["id"]) for flow in child["tt_flows"]}
    if ids != set(releases):
        raise ReleaseAssignmentError(f"RELEASE_MAP_FLOW_SET_MISMATCH:{scenario_id}")
    for flow in child["tt_flows"]:
        flow["release_offset_s"] = ticks_to_seconds(int(releases[str(flow["id"])]))
    return child


def _diff(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "<root>"]
    if isinstance(left, dict):
        output: list[str] = []
        for key in sorted(set(left) | set(right)):
            name = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right: output.append(name)
            else: output.extend(_diff(left[key], right[key], name))
        return output
    if isinstance(left, list):
        output = [f"{path}.length"] if len(left) != len(right) else []
        for index, (item_left, item_right) in enumerate(zip(left, right)):
            output.extend(_diff(item_left, item_right, f"{path}[{index}]"))
        return output
    return [] if left == right else [path or "<root>"]


def derived_integrity(parent: dict[str, Any], child: dict[str, Any], scenario_id: str,
                      releases: dict[str, int]) -> dict[str, Any]:
    """Prove that only release offsets and the allowed scenario name changed."""
    scale = scenario_id.split("_", 1)[0]
    source_flows = {str(flow["id"]): flow for flow in parent["tt_flows"]}
    derived_flows = {str(flow["id"]): flow for flow in child["tt_flows"]}
    non_release_diffs: list[str] = []
    release_mismatch: list[str] = []
    for flow_id in sorted(set(source_flows) | set(derived_flows)):
        if flow_id not in source_flows or flow_id not in derived_flows:
            non_release_diffs.append(f"tt_flows.{flow_id}")
            continue
        left = copy.deepcopy(source_flows[flow_id]); right = copy.deepcopy(derived_flows[flow_id])
        old_release, new_release = left.pop("release_offset_s"), right.pop("release_offset_s")
        non_release_diffs.extend(_diff(left, right, f"tt_flows.{flow_id}"))
        actual_ticks = round(float(new_release) * 1_000_000_000 / QUANTUM_NS)
        if actual_ticks != int(releases.get(flow_id, -1)):
            release_mismatch.append(flow_id)
        if not isinstance(old_release, (int, float)):
            non_release_diffs.append(f"tt_flows.{flow_id}.release_offset_s_type")
    left_non_tt, right_non_tt = copy.deepcopy(parent), copy.deepcopy(child)
    left_non_tt.pop("tt_flows", None); right_non_tt.pop("tt_flows", None)
    left_non_tt.pop("scenario_name", None); right_non_tt.pop("scenario_name", None)
    non_release_diffs.extend(_diff(left_non_tt, right_non_tt))
    expected_flow_count, expected_instance_count = EXPECTED_FLOW_COUNTS[scale], EXPECTED_INSTANCE_COUNTS[scale]
    changed = sum(source_flows[flow_id]["release_offset_s"] != derived_flows[flow_id]["release_offset_s"]
                  for flow_id in source_flows.keys() & derived_flows.keys())
    topology_source, topology_child = canonical_sha256(topology_projection(parent)), canonical_sha256(topology_projection(child))
    return {"scenario": scenario_id, "scale": scale, "source_scenario_sha": sha256_file(source_scenario_path(scenario_id)),
            "derived_scenario_sha": canonical_sha256(child), "topology_sha_source": topology_source,
            "topology_sha_derived": topology_child, "source_flow_count": len(source_flows),
            "derived_flow_count": len(derived_flows), "expected_instance_count": expected_instances(child),
            "release_changed_flow_count": changed, "non_release_semantic_diff_count": len(non_release_diffs),
            "non_release_semantic_diffs": ";".join(non_release_diffs),
            "release_map_mismatch_count": len(release_mismatch), "release_map_sha": release_map_sha256(releases),
            "integrity_pass": (len(source_flows) == len(derived_flows) == expected_flow_count
                               and expected_instances(child) == expected_instance_count
                               and topology_source == topology_child and not non_release_diffs and not release_mismatch)}


def derived_static_validation(scenario_id: str, child: dict[str, Any], release_map_sha: str) -> dict[str, Any]:
    analysis = analyze_scenario(_ref(scenario_id), child)
    qualified = {flow.source_egress for flow in analysis["flows"] if flow.qualified}
    return {"scenario": scenario_id, "scale": scenario_id.split("_", 1)[0],
            "topology": scenario_id.split("_", 1)[1], "flow_count": len(analysis["flows"]),
            "source_egress_count": len(qualified), "collision_event_count": len(analysis["events"]),
            "logical_collision_edge_count": len(analysis["edges"]), "MVC_size": int(analysis["mvc"]["mvc_size"]),
            "collision_free": not analysis["events"] and not analysis["edges"] and int(analysis["mvc"]["mvc_size"]) == 0,
            "same_scale_release_map_sha": release_map_sha,
            "static_collision_graph_sha": analysis["graph_summary"]["graph_sha256"]}


def independent_pairwise_nonoverlap(scenario_id: str, child: dict[str, Any]) -> bool:
    """A compact test-only sanity layer; formal gating uses exp18i analysis."""
    flows, _, _ = build_timed_flows(_ref(scenario_id), child)
    return not collision_events(flows)


def shift_summaries(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    values = list(rows); summaries: list[dict[str, Any]] = []; by_kind: list[dict[str, Any]] = []
    for scale in ("M", "L"):
        selected = [row for row in values if row["scale"] == scale]
        shifts = sorted(int(row["absolute_shift_ticks"]) for row in selected)
        changed = [row for row in selected if row["changed"]]
        def percentile(percent: float) -> int:
            return shifts[0] if len(shifts) == 1 else shifts[round((len(shifts) - 1) * percent)]
        summaries.append({"scale": scale, "total_flows": len(selected), "changed_flows": len(changed),
                          "changed_ratio": len(changed) / len(selected), "unchanged_flows": len(selected) - len(changed),
                          "total_shift_ticks": sum(shifts), "mean_shift_ticks": sum(shifts) / len(shifts),
                          "p50_shift_ticks": percentile(.5), "p95_shift_ticks": percentile(.95),
                          "max_shift_ticks": max(shifts), "total_shift_ns": sum(shifts) * QUANTUM_NS})
        for kind in sorted({str(row["flow_kind"]) for row in selected}):
            group = [row for row in selected if row["flow_kind"] == kind]
            group_shifts = sorted(int(row["absolute_shift_ticks"]) for row in group)
            by_kind.append({"scale": scale, "flow_kind": kind, "flow_count": len(group),
                            "changed_count": sum(bool(row["changed"]) for row in group),
                            "changed_ratio": sum(bool(row["changed"]) for row in group) / len(group),
                            "median_absolute_shift_ticks": group_shifts[(len(group_shifts) - 1) // 2],
                            "max_absolute_shift_ticks": max(group_shifts)})
    return summaries, by_kind


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
