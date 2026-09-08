"""Outcome-independent source selection and integrity checks for exp18g.

This module deliberately consumes frozen scenario JSON only.  It does not
import a solver, a result parser, or any historical HNF evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

from tools.deadline_feasibility_calibration import flow_kind as canonical_flow_kind
from tools.h2s_jrs_backend import DEFAULT_CANDIDATE_PATHS, DEFAULT_QUANTUM_NS, FORMAL_MEMORY_LIMIT_MB, FORMAL_SEED, FORMAL_THREADS
from tools.jrs_wa_adapter import canonical_json_bytes, seconds_to_ns

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/realistic_tsn_pf_cost"
OUT = ROOT / "results/tt_workload_density_calibration"
ORDER = ("M_RING", "M_REDSTAR", "M_ROR", "L_RING", "L_REDSTAR", "L_ROR")
NAMESPACE = "exp18g-role-stratified-density-v1"
HYPERCYCLE_NS = 8_000_000


@dataclass(frozen=True)
class Density:
    tag: str
    numerator: int
    denominator: int

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    @property
    def label(self) -> str:
        return f"{self.numerator}/{self.denominator}"

    @property
    def decimal(self) -> str:
        return format(Decimal(self.numerator) / Decimal(self.denominator), "f")


DENSITIES = (Density("D100", 1, 1), Density("D095", 19, 20), Density("D090", 9, 10),
             Density("D085", 17, 20), Density("D080", 4, 5), Density("D070", 7, 10),
             Density("D060", 3, 5), Density("D050", 1, 2))

FROZEN_TREE_SHA256 = {
    "results/realistic_tsn_pf_cost": "4626bf9853a735e95d949d8056600f8d076744cfea9ed0aaf743ce425449f175",
    "results/p0_hnf_diagnosis": "b15351bd19ef7ec6956eda42e8a48aba908940d6d9412cbfdeb5d2f967c49155",
    "results/candidate_k_sensitivity": "024586fbe2f9d4d9a816212996fad1d928d1f046b51b5273c6d2d01d6ce08408",
    "results/h2s_multistart_sensitivity": "beed316c5c9901c6ed9248d59d331b67d35721947ba4c7f2fa2023d91b87ede3",
    "results/deadline_feasibility_calibration": "fa621c7eec21e9ce702dff1b7375970ce6f795ccff0d87c7724318429c5a258a",
    "results/h2s_primary_policy_sensitivity": "2d53e699d01d3d91372db4c46f4a0c34f8ce385a76472534ef4e6f516ec8b7d7",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            name = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(name).to_bytes(8, "big")); digest.update(name); digest.update(path.read_bytes())
    return digest.hexdigest()


def assert_frozen() -> dict[str, str]:
    actual = {name: tree_sha256(ROOT / name) for name in FROZEN_TREE_SHA256}
    mismatch = {name: {"expected": FROZEN_TREE_SHA256[name], "actual": actual[name]}
                for name in actual if actual[name] != FROZEN_TREE_SHA256[name]}
    if mismatch:
        raise RuntimeError(f"FROZEN_HISTORICAL_ARTIFACT_CHANGED: {mismatch}")
    return actual


def flow_kind(flow_id: str) -> str:
    """The existing canonical role classifier, re-exported for selector users."""
    return canonical_flow_kind(flow_id)


def scenario_path(scenario_id: str) -> Path:
    return SOURCE / "scenarios" / f"{scenario_id}.json"


def load_scenario(scenario_id: str) -> dict[str, Any]:
    return json.loads(scenario_path(scenario_id).read_text(encoding="utf-8"))


def scale_topology(scenario_id: str) -> tuple[str, str]:
    return tuple(scenario_id.split("_", 1))  # type: ignore[return-value]


def topology_projection(scenario: dict[str, Any]) -> dict[str, Any]:
    return {"network": scenario["network"], "nodes": scenario["nodes"], "links": scenario["links"]}


def workload_descriptor(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Every TT flow field is fingerprinted, not just the minimum contract."""
    return [copy.deepcopy(flow) for flow in sorted(scenario["tt_flows"], key=lambda row: row["id"])]


def workload_sha(scenario: dict[str, Any]) -> str:
    return canonical_sha256(workload_descriptor(scenario))


def flow_set_sha(flow_ids: Iterable[str]) -> str:
    return canonical_sha256(sorted(flow_ids))


def expected_instances(scenario: dict[str, Any]) -> int:
    cycle_ns = seconds_to_ns(scenario["simulation"]["cycle_time_s"], "cycle")
    return sum(cycle_ns // seconds_to_ns(flow["period_s"], f"{flow['id']}.period") for flow in scenario["tt_flows"])


def flow_instances(scenario: dict[str, Any], flow: dict[str, Any]) -> int:
    return seconds_to_ns(scenario["simulation"]["cycle_time_s"], "cycle") // seconds_to_ns(flow["period_s"], f"{flow['id']}.period")


def rank_hash(scale: str, kind: str, flow_id: str) -> str:
    return hashlib.sha256(f"{NAMESPACE}|{scale}|{kind}|{flow_id}".encode("utf-8")).hexdigest()


def ranked_flows(scale: str, scenario: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for flow in scenario["tt_flows"]:
        kind = flow_kind(flow["id"])
        grouped.setdefault(kind, []).append({"flow_id": flow["id"], "flow_kind": kind,
                                              "rank_sha256": rank_hash(scale, kind, flow["id"])})
    for kind, rows in grouped.items():
        rows.sort(key=lambda row: (row["rank_sha256"], row["flow_id"]))
        for position, row in enumerate(rows, 1): row["rank_position_within_kind"] = position
    return grouped


def retained_count(original_count: int, density: Density) -> int:
    return density.numerator * original_count // density.denominator


def retained_ids(scale: str, scenario: dict[str, Any], density: Density) -> set[str]:
    return {row["flow_id"] for rows in ranked_flows(scale, scenario).values()
            for row in rows[:retained_count(len(rows), density)]}


def derive_scenario(parent: dict[str, Any], scenario_id: str, density: Density) -> dict[str, Any]:
    """Return the sole allowed exp18g semantic transformation: whole-flow removal."""
    scale, _ = scale_topology(scenario_id)
    if density.tag == "D100":
        return copy.deepcopy(parent)
    selected = retained_ids(scale, parent, density)
    child = copy.deepcopy(parent)
    child["scenario_name"] = f"{scenario_id}_{density.tag}"
    child["tt_flows"] = [flow for flow in child["tt_flows"] if flow["id"] in selected]
    return child


def _differences(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right): return [path or "<root>"]
    if isinstance(left, dict):
        return [item for key in sorted(set(left) | set(right)) for item in (
            [f"{path}.{key}" if path else key] if key not in left or key not in right else
            _differences(left[key], right[key], f"{path}.{key}" if path else key))]
    if isinstance(left, list):
        changes = [f"{path}.length"] if len(left) != len(right) else []
        return changes + [item for index, (a, b) in enumerate(zip(left, right))
                          for item in _differences(a, b, f"{path}[{index}]")]
    return [] if left == right else [path or "<root>"]


def derived_integrity(parent: dict[str, Any], child: dict[str, Any], scenario_id: str,
                      density: Density, source_sha: str) -> dict[str, Any]:
    scale, _ = scale_topology(scenario_id); expected = retained_ids(scale, parent, density)
    parent_flows = {flow["id"]: flow for flow in parent["tt_flows"]}
    child_flows = {flow["id"]: flow for flow in child["tt_flows"]}
    retained_diffs = [flow_id for flow_id in sorted(child_flows)
                      if flow_id not in parent_flows or _differences(parent_flows[flow_id], child_flows[flow_id])]
    parent_non_tt = copy.deepcopy(parent); child_non_tt = copy.deepcopy(child)
    parent_non_tt.pop("tt_flows", None); child_non_tt.pop("tt_flows", None)
    parent_non_tt.pop("scenario_name", None); child_non_tt.pop("scenario_name", None)
    non_tt_diffs = _differences(parent_non_tt, child_non_tt)
    topology_source = canonical_sha256(topology_projection(parent)); topology_child = canonical_sha256(topology_projection(child))
    actual_ids = set(child_flows)
    row = {"scenario": scenario_id, "density_tag": density.tag, "rho_num": density.numerator, "rho_den": density.denominator,
           "source_scenario_sha": source_sha, "derived_scenario_sha": canonical_sha256(child),
           "topology_sha_source": topology_source, "topology_sha_derived": topology_child,
           "retained_flow_set_sha": flow_set_sha(actual_ids), "workload_sha": workload_sha(child),
           "non_TT_population_semantic_diff_count": len(non_tt_diffs), "retained_flow_attribute_diff_count": len(retained_diffs),
           "dropped_flow_count": len(parent_flows) - len(child_flows), "retained_TT_count": len(child_flows),
           "unexpected_retained_or_dropped": ";".join(sorted(actual_ids ^ expected)),
           "integrity_pass": actual_ids == expected and not retained_diffs and not non_tt_diffs and topology_source == topology_child}
    return row


def source_identity_rows(scenarios: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    rows = []; all_equal = True
    for scale in ("M", "L"):
        ids = [f"{scale}_{topology}" for topology in ("RING", "REDSTAR", "ROR")]
        shas = {scenario_id: workload_sha(scenarios[scenario_id]) for scenario_id in ids}
        equal = len(set(shas.values())) == 1; all_equal &= equal
        rows.extend({"scale": scale, "scenario": scenario_id, "source_workload_sha": shas[scenario_id],
                     "same_scale_workload_identity": equal} for scenario_id in ids)
    return rows, all_equal


def selection_rows(scale: str, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for kind, ranked in sorted(ranked_flows(scale, scenario).items()):
        for row in ranked:
            item = {"scale": scale, **row}
            item.update({f"retained_{density.tag}": row["rank_position_within_kind"] <= retained_count(len(ranked), density)
                         for density in DENSITIES})
            rows.append(item)
    return rows


def density_class_rows(scale: str, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    ranking = ranked_flows(scale, scenario); rows = []
    for density in DENSITIES:
        for kind, values in sorted(ranking.items()):
            count = retained_count(len(values), density)
            rows.append({"scale": scale, "density_tag": density.tag, "rho": density.label,
                         "rho_num": density.numerator, "rho_den": density.denominator, "flow_kind": kind,
                         "original_count": len(values), "retained_count": count,
                         "retained_ratio": f"{count}/{len(values)}", "dropped_count": len(values) - count})
    return rows


def traffic_rows(scale: str, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    scale_parent = scenario
    rows = []
    for density in DENSITIES:
        selected = retained_ids(scale, scenario, density)
        child_flows = [flow for flow in scenario["tt_flows"] if flow["id"] in selected]
        for kind in sorted({flow_kind(flow["id"]) for flow in scenario["tt_flows"]} | {"TOTAL"}):
            flows = child_flows if kind == "TOTAL" else [flow for flow in child_flows if flow_kind(flow["id"]) == kind]
            instances = sum(flow_instances(scale_parent, flow) for flow in flows)
            bytes_on_wire = sum(flow_instances(scale_parent, flow) * (int(flow["packet_size_bytes"]) + int(scenario["scheduling"]["frame_overhead_bytes"])) for flow in flows)
            rows.append({"scale": scale, "density_tag": density.tag, "rho": density.label, "rho_num": density.numerator,
                         "rho_den": density.denominator, "flow_kind": kind, "logical_flow_count": len(flows),
                         "packet_instance_count": instances, "on_wire_bytes_per_hyperperiod": bytes_on_wire})
    return rows


def endpoint_coverage(scale: str, scenario: dict[str, Any], density: Density) -> dict[str, Any]:
    selected = retained_ids(scale, scenario, density); flows = [flow for flow in scenario["tt_flows"] if flow["id"] in selected]
    def values(pattern: str) -> set[str]:
        return {match.group(0) for flow in flows for endpoint in (flow["source"], flow["destination"])
                for match in [re.search(pattern, endpoint)] if match}
    return {"scale": scale, "density_tag": density.tag, "active_TT_source_ES_count": len({flow["source"] for flow in flows}),
            "active_TT_destination_ES_count": len({flow["destination"] for flow in flows}),
            "machines_with_retained_TT_flows": len(values(r"C\d+_M\d+")), "cells_with_retained_TT_flows": len(values(r"C\d+"))}


def nestedness_rows(scale: str, scenario: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    ranking = ranked_flows(scale, scenario); rows = []; passed = True
    for kind, values in sorted(ranking.items()):
        ascending = tuple(reversed(DENSITIES))
        for low, high in zip(ascending, ascending[1:]):
            low_ids = {row["flow_id"] for row in values[:retained_count(len(values), low)]}
            high_ids = {row["flow_id"] for row in values[:retained_count(len(values), high)]}
            subset = low_ids <= high_ids; passed &= subset
            rows.append({"scale": scale, "flow_kind": kind, "rho_low": low.label, "rho_high": high.label,
                         "low_count": len(low_ids), "high_count": len(high_ids), "low_subset_of_high": subset,
                         "unexpected_removed": ";".join(sorted(low_ids - high_ids)), "unexpected_added": ""})
    return rows, passed


def backend_config() -> dict[str, Any]:
    return {"route_scope": "ALL_REROUTE", "primary_algorithm": "H2S", "celf_fallback": True,
            "h2s_flow_sorting": 4, "h2s_sorter": "LOW_PERIOD_FLOWS_FIRST", "routing": "DIJKSTRA_OVERLAP",
            "candidate_paths_k": DEFAULT_CANDIDATE_PATHS, "quantum_ns": DEFAULT_QUANTUM_NS,
            "global_seed": FORMAL_SEED, "backend_seed": FORMAL_SEED, "threads": FORMAL_THREADS,
            "timeout_s_per_backend": 30, "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB,
            "h2s_tiebreak_mode": "BASELINE", "h2s_tiebreak_seed": 0, "multistart": False}


def assert_backend_config(value: dict[str, Any]) -> None:
    if value != backend_config(): raise RuntimeError(f"BACKEND_CONFIGURATION_DRIFT: {value}")


def density_by_tag(tag: str) -> Density:
    return next(density for density in DENSITIES if density.tag == tag)
