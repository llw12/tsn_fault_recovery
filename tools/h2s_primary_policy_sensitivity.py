"""Reusable audit and evidence helpers for exp18f built-in H2S policies."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.h2s_jrs_backend import DEFAULT_CANDIDATE_PATHS, DEFAULT_QUANTUM_NS, FORMAL_MEMORY_LIMIT_MB, FORMAL_SEED, FORMAL_THREADS
from tools.jrs_wa_adapter import canonical_json_bytes, seconds_to_ns

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/realistic_tsn_pf_cost"
OUT = ROOT / "results/h2s_primary_policy_sensitivity"
UPSTREAM = ROOT / ".external/AdvancedFlowScheduler"
ORDER = ("M_RING", "M_REDSTAR", "M_ROR", "L_RING", "L_REDSTAR", "L_ROR")


@dataclass(frozen=True)
class Policy:
    tag: str
    enum_name: str
    cli_value: int
    class_name: str


FORMAL_POLICIES = (
    Policy("LOW_PERIOD", "LOW_PERIOD_FLOWS_FIRST", 4, "LowPeriodFlowsFirst"),
    Policy("LOWEST_TRAFFIC", "LOWEST_TRAFFIC_FLOWS_FIRST", 1, "LowTrafficFlowsFirst"),
    Policy("LOWEST_ID", "LOWEST_ID_FIRST", 2, "LowestIdFirst"),
    Policy("SOURCE_NODE", "SOURCE_NODE_SORTING", 3, "SourceNodeSorting"),
)
POLICY_BY_TAG = {policy.tag: policy for policy in FORMAL_POLICIES}
FROZEN_TREE_SHA256 = {
    "results/realistic_tsn_pf_cost": "4626bf9853a735e95d949d8056600f8d076744cfea9ed0aaf743ce425449f175",
    "results/p0_hnf_diagnosis": "b15351bd19ef7ec6956eda42e8a48aba908940d6d9412cbfdeb5d2f967c49155",
    "results/candidate_k_sensitivity": "024586fbe2f9d4d9a816212996fad1d928d1f046b51b5273c6d2d01d6ce08408",
    "results/h2s_multistart_sensitivity": "beed316c5c9901c6ed9248d59d331b67d35721947ba4c7f2fa2023d91b87ede3",
    "results/deadline_feasibility_calibration": "fa621c7eec21e9ce702dff1b7375970ce6f795ccff0d87c7724318429c5a258a",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big")); digest.update(relative); digest.update(path.read_bytes())
    return digest.hexdigest()


def assert_frozen() -> dict[str, str]:
    actual = {name: tree_sha256(ROOT / name) for name in FROZEN_TREE_SHA256}
    mismatch = {name: {"expected": FROZEN_TREE_SHA256[name], "actual": actual[name]}
                for name in actual if actual[name] != FROZEN_TREE_SHA256[name]}
    if mismatch:
        raise RuntimeError(f"FROZEN_HISTORICAL_ARTIFACT_CHANGED: {mismatch}")
    return actual


def scenario_path(scenario_id: str) -> Path:
    return SOURCE / "scenarios" / f"{scenario_id}.json"


def load_scenario(scenario_id: str) -> dict[str, Any]:
    return json.loads(scenario_path(scenario_id).read_text(encoding="utf-8"))


def flow_kind(flow_id: str) -> str:
    labels = {"SF": "SensorFast", "SC": "SensorCyclic", "CMD": "ControlCommand",
              "STAT": "MachineStatus", "COORD": "MachineCoordination",
              "IC_PREV": "InterCellPrevious", "IC_NEXT": "InterCellNext"}
    return labels[next(prefix for prefix in ("IC_PREV", "IC_NEXT", "SF", "SC", "CMD", "STAT", "COORD")
                       if flow_id.startswith(prefix))]


def ns(flow: dict[str, Any], field: str) -> int:
    return seconds_to_ns(flow[field], f"{flow['id']}.{field}")


def expected_instances(scenario: dict[str, Any]) -> int:
    cycle = seconds_to_ns(scenario["simulation"]["cycle_time_s"], "cycle")
    return sum(cycle // ns(flow, "period_s") for flow in scenario["tt_flows"])


def topology_sha(scenario: dict[str, Any]) -> str:
    return canonical_sha256({"network": scenario["network"], "nodes": scenario["nodes"], "links": scenario["links"]})


def timing_sha(scenario: dict[str, Any]) -> str:
    return canonical_sha256([{key: flow[key] for key in ("id", "period_s", "release_offset_s", "schedule_deadline_budget_s", "deadline_e2e_s")}
                             for flow in sorted(scenario["tt_flows"], key=lambda row: row["id"])])


def release_sha(scenario: dict[str, Any]) -> str:
    return canonical_sha256([{ "id": flow["id"], "release_offset_s": flow["release_offset_s"]}
                             for flow in sorted(scenario["tt_flows"], key=lambda row: row["id"])])


def backend_config() -> dict[str, Any]:
    return {"route_scope": "ALL_REROUTE", "routing": "DIJKSTRA_OVERLAP", "candidate_paths_k": DEFAULT_CANDIDATE_PATHS,
            "quantum_ns": DEFAULT_QUANTUM_NS, "global_seed": FORMAL_SEED, "backend_seed": FORMAL_SEED,
            "threads": FORMAL_THREADS, "timeout_s_per_backend": 30, "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB,
            "configuration_rating": 1, "placement_type": 0, "offensive_planning": False,
            "h2s_tiebreak_mode": "BASELINE", "h2s_tiebreak_seed": 0, "multistart": False,
            "celf_fallback": True}


def assert_backend_config(value: dict[str, Any]) -> None:
    if value != backend_config():
        raise RuntimeError(f"BACKEND_CONFIGURATION_DRIFT: expected {backend_config()}, got {value}")


def _source(path: str) -> str:
    return (UPSTREAM / path).read_text(encoding="utf-8")


def audit_pinned_upstream() -> dict[str, Any]:
    """Read the pinned source; do not infer policy mappings from the prompt."""
    expected_commit = "650a9665e7bafb70fcf19c9f0a247e1d7b885ffd"
    actual_commit = subprocess.run(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], check=True,
                                   text=True, capture_output=True).stdout.strip()
    if actual_commit != expected_commit:
        raise RuntimeError(f"UPSTREAM_PIN_MISMATCH: expected {expected_commit}, got {actual_commit}")
    factory_header = _source("include/solver/FlowSorting/FlowSorterFactory.h")
    factory_cpp = _source("src/solver/FlowSorting/FlowSorterFactory.cpp")
    options_h = _source("include/IO/ProgramOptions.h")
    options_cpp = _source("src/IO/ProgramOptions.cpp")
    enum_pairs = {name: int(number) for name, number in re.findall(r"\b([A-Z_]+)\s*=\s*(\d+)", factory_header)}
    required = {policy.enum_name: policy.cli_value for policy in FORMAL_POLICIES}
    if {name: enum_pairs.get(name) for name in required} != required:
        raise RuntimeError(f"REQUIRED_BUILTIN_POLICY_MISSING_OR_REMAPPED: {enum_pairs}")
    if "flow_sorting_ = flow_sorting::FlowSorterTypes::LOW_PERIOD_FLOWS_FIRST" not in options_h:
        raise RuntimeError("LOW_PERIOD_FLOWS_FIRST is not the pinned default")
    if "-f,--flow-sorting" not in options_cpp:
        raise RuntimeError("upstream CLI lacks -f/--flow-sorting")
    files = {
        "LOW_PERIOD": "include/solver/FlowSorting/LowPeriodFlowsFirst.h",
        "LOWEST_TRAFFIC": "include/solver/FlowSorting/LowTrafficFlowsFirst.h",
        "LOWEST_ID": "include/solver/FlowSorting/LowestIdFirst.h",
        "SOURCE_NODE": "include/solver/FlowSorting/SourceNodeSorting.h",
    }
    semantic = {
        "LOW_PERIOD": "priority queue top: period ascending; equal period frame_size descending; final flow ID ascending (BASELINE mode). Reads period, frame_size, flow ID.",
        "LOWEST_TRAFFIC": "priority queue top: traffic estimate frame_size/period ascending; equal estimate flow ID descending under the upstream comparator. Reads frame_size, period, flow ID.",
        "LOWEST_ID": "priority queue top: numeric flow ID ascending. Reads flow ID only.",
        "SOURCE_NODE": "priority queue top: source fan-out descending; destination numeric ID descending; traffic estimate descending; flow ID descending. Reads source, destination, frame_size, period, flow ID.",
    }
    ordering_inputs = {
        "LOW_PERIOD": ["period", "frame_size", "flow_id"],
        "LOWEST_TRAFFIC": ["traffic_estimate(frame_size/period)", "frame_size", "period", "flow_id"],
        "LOWEST_ID": ["flow_id"],
        "SOURCE_NODE": ["source_node_fan_out", "destination", "traffic_estimate(frame_size/period)", "frame_size", "period", "flow_id"],
    }
    token_requirements = {
        "LOW_PERIOD": ("period", "frame_size", "id"), "LOWEST_TRAFFIC": ("frame_size", "period", "id"),
        "LOWEST_ID": ("lhs > rhs",), "SOURCE_NODE": ("flow_starts_", "destination", "frame_size", "period", "id"),
    }
    rows = []
    for policy in FORMAL_POLICIES:
        text = _source(files[policy.tag])
        mapped_by_default = policy.enum_name == "LOWEST_ID_FIRST" and "default:" in factory_cpp and policy.class_name in factory_cpp
        if (f"case {policy.enum_name}" not in factory_cpp and not mapped_by_default) or policy.class_name not in factory_cpp:
            raise RuntimeError(f"factory does not map {policy.enum_name} to {policy.class_name}")
        if not all(token in text for token in token_requirements[policy.tag]):
            raise RuntimeError(f"comparator audit tokens missing for {policy.enum_name}")
        rows.append({"tag": policy.tag, "enum_name": policy.enum_name, "cli_value": policy.cli_value,
                     "source_file": files[policy.tag], "class_name": policy.class_name,
                     "comparator_semantics": semantic[policy.tag], "ordering_inputs": ordering_inputs[policy.tag]})
    all_policies = [{"enum_name": name, "cli_value": value, "formal": name in required}
                    for name, value in sorted(enum_pairs.items(), key=lambda item: item[1])]
    return {"upstream_commit": actual_commit, "formal_policies": rows,
            "discovered_policies": all_policies, "default_enum": "LOW_PERIOD_FLOWS_FIRST", "default_cli_value": 4,
            "cli": "-f,--flow-sorting", "other_fixed_options": {"configuration_rating": 1, "placement_type": 0,
            "offensive_planning": False, "routing": "DIJKSTRA_OVERLAP", "candidate_paths": 5},
            "exp18d_tiebreak_patch_state": "DISABLED_BY_BASELINE_CLI_MODE", "factory_header_sha256": sha256_file(UPSTREAM / "include/solver/FlowSorting/FlowSorterFactory.h"),
            "factory_cpp_sha256": sha256_file(UPSTREAM / "src/solver/FlowSorting/FlowSorterFactory.cpp"),
            "program_options_h_sha256": sha256_file(UPSTREAM / "include/IO/ProgramOptions.h"),
            "program_options_cpp_sha256": sha256_file(UPSTREAM / "src/IO/ProgramOptions.cpp")}


def parse_order(stdout: str, scenario: dict[str, Any], policy: Policy) -> list[dict[str, Any]]:
    markers = [line[len("H2S_ORDER_JSON:"):] for line in stdout.splitlines() if line.startswith("H2S_ORDER_JSON:")]
    if len(markers) != 1:
        raise ValueError(f"expected exactly one H2S_ORDER_JSON marker, got {len(markers)}")
    raw = json.loads(markers[0])
    flows = sorted(scenario["tt_flows"], key=lambda row: row["id"])
    mapped = []
    for row in raw:
        index = int(row["flow_id"])
        if not 0 <= index < len(flows):
            raise ValueError(f"unknown H2S flow ID: {index}")
        flow = flows[index]
        mapped.append({"position": int(row["position"]), "flow_id": flow["id"], "period_ns": int(row["period"]) * 100,
                       "frame_size": int(row["frame_size"]), "source": flow["source"], "policy_name": policy.enum_name,
                       "policy_cli_value": policy.cli_value})
    mapped.sort(key=lambda row: row["position"])
    if [row["position"] for row in mapped] != list(range(len(flows))) or len({row["flow_id"] for row in mapped}) != len(flows):
        raise ValueError("invalid actual H2S flow order export")
    return mapped


def order_sha(rows: list[dict[str, Any]]) -> str:
    return canonical_sha256(rows)


def candidate_vector_sha(payload: dict[str, Any]) -> str:
    values = payload.get("candidate_path_counts", {})
    return canonical_sha256(sorted((int(key), int(value)) for key, value in values.items()))


def h2s_policy_qualification_case() -> dict[str, Any]:
    """Small non-formal case with deliberately distinct policy order keys."""
    def flow(identifier: str, source: str, period_ns: int, frame_bytes: int) -> dict[str, Any]:
        return {"id": identifier, "source": source, "destination": "d", "packet_size_bytes": frame_bytes,
                "period_s": period_ns / 1e9, "deadline_e2e_s": period_ns / 1e9,
                "schedule_deadline_budget_s": period_ns / 1e9, "release_offset_s": 0.0, "pcp": 4, "traffic_class": 1}
    nodes = [{"id": name, "type": kind} for name, kind in (("a", "end_system"), ("b", "end_system"), ("sw", "switch"), ("d", "end_system"))]
    links = [{"id": f"{left}_{right}", "endpoint_a": left, "endpoint_b": right,
              "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0.0}
             for left, right in (("a", "sw"), ("b", "sw"), ("sw", "d"))]
    return {"schema_version": 1, "scenario_name": "exp18f_builtin_policy_qualification", "forwarding_model": "stream-aware",
            "simulation": {"duration_s": .01, "cycle_time_s": .002, "time_quantum_s": 1e-9, "failure_time_s": .005,
                           "solver_delay_s": 0.0, "random_seed": 1024},
            "network": {"default_bitrate_bps": 1_000_000_000, "default_propagation_delay_s": 0.0},
            "scheduling": {"ingress_margin_s": 0.0, "hop_margin_s": 0.0, "endpoint_budget_s": 0.0,
                           "frame_overhead_bytes": 64, "be_traffic_class": 0}, "nodes": nodes, "links": links,
            "tt_flows": [flow("A_FAST", "a", 500_000, 64), flow("B_HEAVY", "b", 1_000_000, 900),
                         flow("C_LIGHT", "a", 2_000_000, 64), flow("D_MEDIUM", "a", 1_000_000, 100)],
            "be_flows": [], "fault_candidates": [], "fault_candidate_policy": {"mode": "explicit", "exclude": []}}


def expected_qualification_order(policy: Policy) -> list[str]:
    # These orders are calculated from the audited comparators and the tiny
    # case's four unique priority values, avoiding heap tie ambiguity.
    return {"LOW_PERIOD": ["A_FAST", "B_HEAVY", "D_MEDIUM", "C_LIGHT"],
            "LOWEST_TRAFFIC": ["C_LIGHT", "D_MEDIUM", "A_FAST", "B_HEAVY"],
            "LOWEST_ID": ["A_FAST", "B_HEAVY", "C_LIGHT", "D_MEDIUM"],
            "SOURCE_NODE": ["A_FAST", "D_MEDIUM", "C_LIGHT", "B_HEAVY"]}[policy.tag]
