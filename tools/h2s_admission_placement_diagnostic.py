"""Read-only source and trace helpers for exp18h.

This module never constructs a new workload, changes a solver option, or
invokes an alternative backend.  Its only inputs are the six byte-frozen
exp18g scenarios and JSONL emitted by the optional H2S observer.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from tools.h2s_jrs_backend import (
    DEFAULT_CANDIDATE_PATHS, DEFAULT_QUANTUM_NS, FORMAL_MEMORY_LIMIT_MB,
    FORMAL_SEED, FORMAL_THREADS, UPSTREAM_COMMIT, UPSTREAM_REPOSITORY,
)
from tools.h2s_primary_policy_sensitivity import flow_kind
from tools.jrs_wa_adapter import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / ".external" / "AdvancedFlowScheduler"
OUT = ROOT / "results" / "h2s_admission_placement_diagnostic"
SCENARIOS = (
    "M_RING_D070", "M_REDSTAR_D070", "M_ROR_D070",
    "L_RING_D050", "L_REDSTAR_D050", "L_ROR_D050",
)
SUCCESS_CONTROLS = set(SCENARIOS[:3])
FAILURE_CASES = set(SCENARIOS[3:])
TRACE_SCHEMA_VERSION = "exp18h-h2s-diagnostic-v1"
SEMANTIC_PATCH_SHA256 = "5055d5acdb5553ee9807beb120c784979fa6ac98d2bd206c4ffdf1c021139bf9"
TIEBREAK_PATCH_SHA256 = "20225766871217f36765e23a4616cd5bf0cd2eaf5c2eb21dda4eeaca8dc1bfc3"


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(rel).to_bytes(8, "big")); digest.update(rel); digest.update(path.read_bytes())
    return digest.hexdigest()


def source_scenario_path(scenario: str) -> Path:
    return ROOT / "results" / "tt_workload_density_calibration" / "derived_scenarios" / scenario / "scenario.json"


def load_source_scenario(scenario: str) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ValueError(f"non-formal exp18h scenario: {scenario}")
    return json.loads(source_scenario_path(scenario).read_text(encoding="utf-8"))


def formal_backend_config() -> dict[str, Any]:
    return {
        "route_scope": "ALL_REROUTE", "algorithm": "H2S", "celf_fallback": False,
        "flow_sorting": "LOW_PERIOD_FLOWS_FIRST", "flow_sorting_cli": 4,
        "configuration_rating": "PATH_LENGTH", "configuration_rating_cli": 1,
        "routing": "DIJKSTRA_OVERLAP", "candidate_paths_k": DEFAULT_CANDIDATE_PATHS,
        "placement": "ASAP", "placement_cli": 0, "quantum_ns": DEFAULT_QUANTUM_NS,
        "seed": FORMAL_SEED, "threads": FORMAL_THREADS, "timeout_s": 30,
        "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB, "h2s_tiebreak_mode": "BASELINE",
        "h2s_tiebreak_seed": 0,
    }


def source_tree_sha256() -> str:
    digest = hashlib.sha256()
    paths = [UPSTREAM / "main.cpp", UPSTREAM / "CMakeLists.txt", UPSTREAM / "include", UPSTREAM / "src"]
    for root in paths:
        if root.is_file():
            rows = [root]
        else:
            rows = sorted(path for path in root.rglob("*") if path.is_file())
        for path in rows:
            rel = path.relative_to(UPSTREAM).as_posix().encode("utf-8")
            digest.update(len(rel).to_bytes(8, "big")); digest.update(rel); digest.update(path.read_bytes())
    return digest.hexdigest()


def command(*args: str) -> str:
    return subprocess.run(args, text=True, check=True, capture_output=True).stdout.strip()


def audit_current_patched_source() -> dict[str, Any]:
    """Audit the exact source tree built by exp18h; no inferences from stock only."""
    commit = command("git", "-C", str(UPSTREAM), "rev-parse", "HEAD")
    if commit != UPSTREAM_COMMIT:
        raise RuntimeError(f"UPSTREAM_PIN_MISMATCH: {commit}")
    source = lambda rel: (UPSTREAM / rel).read_text(encoding="utf-8")
    placement = source("src/solver/Placement.cpp")
    asap_body = placement.split("auto placement::placeConfigASAP", 1)[1].split("auto placement::placeConfigBalanced", 1)[0]
    utilization = source("src/solver/UtilizationList.cpp")
    scheduler = source("src/solver/scheduler/HierarchicalHeuristicScheduling.cpp")
    options = source("src/IO/ProgramOptions.cpp")
    manager = source("src/scenario/ScenarioManager.cpp")
    patch_path = ROOT / "third_party_patches" / "advanced_flow_scheduler" / "exp18h_diagnostic_trace.patch"
    return {
        "upstream_repository": UPSTREAM_REPOSITORY, "upstream_base_commit": commit,
        "current_patched_source_tree_sha256": source_tree_sha256(),
        "exp15_semantic_patch_sha256": SEMANTIC_PATCH_SHA256,
        "exp18d_tiebreak_patch_sha256": TIEBREAK_PATCH_SHA256,
        "exp18d_tiebreak_state": "DISABLED_BY_--h2s-tiebreak-mode_BASELINE",
        "exp18h_instrumentation_patch_sha256": sha256_file(patch_path) if patch_path.is_file() else "PENDING_PATCH_PROVENANCE",
        "scheduler_class": "HierarchicalHeuristicScheduling",
        "schedule_set": "priority_queue pop -> runtime rating prepare/rate -> ascending rating then config id -> first successful placeConfig; no backtracking",
        "flow_sorter": "LOW_PERIOD_FLOWS_FIRST (CLI -f 4)",
        "configuration_rating": "PATH_LENGTH (CLI -c 1)",
        "placement_strategy": "ASAP (qualified adapter CLI -p 0; BALANCED is audited but not substituted)",
        "candidate_route_generator": "DijkstraOverlap", "candidate_k": DEFAULT_CANDIDATE_PATHS,
        "fixed_release_functions": ["graph_structs::Flow.release_offset/fixed_release", "placement::placeConfigASAP"],
        "deadline_functions": ["graph_structs::Flow.deadline", "placement::placeConfigASAP", "NetworkUtilizationList::searchTransmissionOpportunities"],
        "mixed_period_functions": ["NetworkUtilizationList::compute_frames_per_hc", "placement::placeConfigBalanced", "NetworkUtilizationList::reserveSlot"],
        "balanced_actual_formula": {
            "release": "sub_cycle_index * utilization.getSubCycle() + frame_index * flow.period",
            "deadline": "(frame_index + 1) * flow.period",
            "subcycle_count": "flow.period / utilization.getSubCycle()",
        },
        "balanced_reads_flow_release": False,
        "balanced_reads_flow_deadline": False,
        "balanced_reads_fixed_release": False,
        "asap_uses_release_deadline": all(token in asap_body
                                          for token in ("current_flow.release_offset", "current_flow.deadline", "current_flow.fixed_release")),
        "search_uses_flow_timing": all(token in utilization for token in ("current_flow.propagation_delay", "current_flow.processing_delay")),
        "configuration_generation_from_actual_routes": "navigator->findRoutes" in manager and "insertConfiguration" in manager,
        "no_backtracking": "result_set.emplace_back" in scheduler and "break;" in scheduler,
        "continues_after_failed_flow": "while(not flow_heap.empty())" in scheduler,
        "global_attempt_hard_limit": False, "fixed_cardinality_ceiling": False,
        "diagnostic_flag_default_off": "--diagnostic-trace" in options,
        "h2s_only_observer": "diagnostic_observer_.get()" in scheduler,
        "semantic_anomaly_observed": "const auto current_release_time = sub_cycle_index * utilizationList.getSubCycle() + frame_index * current_flow.period;" in placement and "const auto deadline = (frame_index + 1) * current_flow.period;" in placement,
    }


def trace_records(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise RuntimeError("TRACE_EMPTY")
    required = {"schema_version", "experiment", "scenario", "run_id", "event_type"}
    if any(required - set(row) for row in rows):
        raise RuntimeError("TRACE_SCHEMA_MISSING_FIELDS")
    if any(row["schema_version"] != TRACE_SCHEMA_VERSION for row in rows):
        raise RuntimeError("TRACE_SCHEMA_VERSION_MISMATCH")
    return rows


def enrich_trace(rows: Iterable[dict[str, Any]], scenario: dict[str, Any], reverse_flow_map: dict[int, str],
                 arc_to_link: dict[tuple[int, int], str]) -> list[dict[str, Any]]:
    flows = {flow["id"]: flow for flow in scenario["tt_flows"]}
    output = []
    for raw in rows:
        row = dict(raw)
        if isinstance(row.get("flow_id"), int):
            flow_id = reverse_flow_map[int(row["flow_id"])]
            row["canonical_flow_id"] = flow_id
            row["flow_kind"] = flow_kind(flow_id)
            row["canonical_period_ns"] = int(round(flows[flow_id]["period_s"] * 1e9))
        if "path" in row and arc_to_link:
            row["canonical_path"] = [arc_to_link[(int(part["source"]), int(part["destination"]))] for part in row["path"]]
        if "queue_id" in row and "canonical_path" not in row:
            queue = int(row["queue_id"])
            # Queue IDs are encoded deterministically by prepare_h2s_inputs; CONFIGURATION contains the authoritative arc mapping.
            row["queue_id"] = queue
        for blocker in row.get("observed_overlapping_reservations", []):
            if int(blocker["flow_id"]) in reverse_flow_map:
                blocker["canonical_flow_id"] = reverse_flow_map[int(blocker["flow_id"])]
        output.append(row)
    return output


def trace_event_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row["event_type"]) for row in rows).items()))
