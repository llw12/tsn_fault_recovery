"""exp18j: exact source-egress-aware fixed-release assignment and healthy P0.

The release solver receives only frozen workload/source-egress/timing data.
Static exp18i functions are the sole formal collision gate.  P0 is run only
after the source-local assignment, semantic-integrity, and zero-collision
gates have all passed.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.fixed_release_collision_audit import HYPERCYCLE_TICKS, ROOT, canonical_sha256, sha256_file, tree_sha256
from tools.h2s_jrs_backend import H2sJrsBackend, parse_backend_output
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_p0_hnf_mechanism_diagnosis import signatures
from tools.source_egress_release_assignment import (
    EXPECTED_FLOW_COUNTS, EXPECTED_INSTANCE_COUNTS, FORMAL_BACKEND_CONFIG, OUT, QUANTUM_NS,
    ReleaseAssignmentError, apply_release_assignment, assignment_rows, derived_integrity,
    derived_static_validation, optimizer_problem, release_map_sha256, shift_summaries,
    source_scenario_path, source_scenarios,
)

EXECUTABLE = ROOT / ".external" / "AdvancedFlowScheduler" / "build-release" / "AdvancedFlowSchedulerExec"
SOLVER_SCRIPT = ROOT / "tools" / "source_egress_release_z3_optimizer.py"
SOLVER_PYTHON = ROOT / ".venv-jrs" / "bin" / "python"
FROZEN_ROOTS = (
    "results/realistic_tsn_pf_cost", "results/p0_hnf_diagnosis", "results/candidate_k_sensitivity",
    "results/h2s_multistart_sensitivity", "results/deadline_feasibility_calibration",
    "results/h2s_primary_policy_sensitivity", "results/tt_workload_density_calibration",
    "results/h2s_admission_placement_diagnostic", "results/fixed_release_collision_audit",
)
FROZEN_TREE_SHA256 = {
    "results/realistic_tsn_pf_cost": "4626bf9853a735e95d949d8056600f8d076744cfea9ed0aaf743ce425449f175",
    "results/p0_hnf_diagnosis": "b15351bd19ef7ec6956eda42e8a48aba908940d6d9412cbfdeb5d2f967c49155",
    "results/candidate_k_sensitivity": "024586fbe2f9d4d9a816212996fad1d928d1f046b51b5273c6d2d01d6ce08408",
    "results/h2s_multistart_sensitivity": "beed316c5c9901c6ed9248d59d331b67d35721947ba4c7f2fa2023d91b87ede3",
    "results/deadline_feasibility_calibration": "fa621c7eec21e9ce702dff1b7375970ce6f795ccff0d87c7724318429c5a258a",
    "results/h2s_primary_policy_sensitivity": "2d53e699d01d3d91372db4c46f4a0c34f8ce385a76472534ef4e6f516ec8b7d7",
    "results/tt_workload_density_calibration": "2b2b3d2b592a4d736aa381c20be1e7f0fcaace56a56420d07d6a34f380297e8d",
    "results/h2s_admission_placement_diagnostic": "99ccba68e3e03c4203d64e2f8e6ae384fc62984b450272888aaf689ce58d6984",
    "results/fixed_release_collision_audit": "c2441071f6c87f659c126b5435ec9208bac72ea796dc1b0cafcda6a6a6526f2b",
}
VERDICTS = {
    "FULL_DENSITY_PF_BENCHMARK_QUALIFIED", "SOURCE_RELEASE_ASSIGNMENT_UNSAT_WITHIN_ORIGINAL_WINDOW",
    "SOURCE_RELEASE_OPTIMALITY_UNPROVEN", "SOURCE_EGRESS_GROUPING_MISMATCH", "SOURCE_RELEASE_INPUT_INVALID",
    "RELEASE_ASSIGNMENT_COLLISION_REMAINS", "DERIVED_RELEASE_SCENARIO_INTEGRITY_FAILED",
    "SOURCE_RELEASE_TIMING_PARITY_FAILED", "SOURCE_AWARE_RELEASE_P0_PARTIALLY_COMPLETE",
    "SOURCE_AWARE_RELEASE_P0_STILL_INCOMPLETE", "RELEASE_REDESIGN_P0_NONDETERMINISTIC", "INCONCLUSIVE",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value, encoding="utf-8")


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    values = list(rows); names = fields or sorted({key for row in values for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, names, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(values)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, check=True, capture_output=True).stdout.strip()


def frozen_preflight() -> dict[str, str]:
    return {item: tree_sha256(ROOT / item) for item in FROZEN_ROOTS}


def assert_frozen() -> dict[str, str]:
    actual = frozen_preflight()
    mismatch = {item: {"expected": FROZEN_TREE_SHA256[item], "actual": actual[item]}
                for item in FROZEN_ROOTS if actual[item] != FROZEN_TREE_SHA256[item]}
    if mismatch:
        raise RuntimeError(f"FROZEN_HISTORICAL_ARTIFACT_CHANGED:{mismatch}")
    return actual


def _exact_problem(groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {"hypercycle_ticks": HYPERCYCLE_TICKS, "group_timeout_s": 60, "groups": groups}


def run_exact_problem(problem: dict[str, Any], working: Path) -> dict[str, Any]:
    """Invoke only the existing project virtual environment's Z3 binding."""
    if not SOLVER_PYTHON.is_file() or not SOLVER_SCRIPT.is_file():
        raise RuntimeError("SOURCE_RELEASE_OPTIMALITY_UNPROVEN:PROJECT_Z3_RUNTIME_UNAVAILABLE")
    working.mkdir(parents=True, exist_ok=True)
    input_path, output_path = working / "optimizer_problem.json", working / "optimizer_result.json"
    write_json(input_path, problem)
    limit_s = max(70, 65 * len(problem["groups"]))
    try:
        process = subprocess.run([str(SOLVER_PYTHON), str(SOLVER_SCRIPT), "--input", str(input_path), "--output", str(output_path)],
                                 cwd=ROOT, text=True, capture_output=True, timeout=limit_s)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("SOURCE_RELEASE_OPTIMALITY_UNPROVEN:SOLVER_PROCESS_TIMEOUT") from error
    if process.returncode or not output_path.is_file():
        raise RuntimeError(f"SOURCE_RELEASE_OPTIMALITY_UNPROVEN:SOLVER_PROCESS:{process.stderr.strip()}")
    result = json.loads(output_path.read_text(encoding="utf-8"))
    result["solver_stdout"] = process.stdout.strip(); result["solver_stderr"] = process.stderr.strip()
    return result


def _toy_flow(flow_id: str, *, period: int, tx: int, release: int, upper: int) -> dict[str, Any]:
    return {"flow_id": flow_id, "flow_kind": "Toy", "source": "toy", "destination": "dst", "source_egress": "toy",
            "period_ticks": period, "tx_ticks": tx, "original_release_ticks": release,
            "upper_release_ticks": upper, "frames_per_hypercycle": 1, "deadline_ticks": period,
            "qualified": True, "source_egress_link": "toy", "original_collision_degree": 0}


def optimizer_toy_qualification(working: Path) -> dict[str, Any]:
    """Exercise SAT, UNSAT and lexicographic minima through the production helper."""
    groups = [
        {"scale": "TOY", "source": "sat", "source_egress": "sat", "flows": [_toy_flow("a", period=100, tx=10, release=0, upper=20), _toy_flow("b", period=100, tx=10, release=0, upper=20)]},
        {"scale": "TOY", "source": "unsat", "source_egress": "unsat", "flows": [_toy_flow("c", period=100, tx=60, release=0, upper=0), _toy_flow("d", period=100, tx=60, release=0, upper=0)]},
        {"scale": "TOY", "source": "star", "source_egress": "star", "flows": [_toy_flow("e", period=100, tx=10, release=0, upper=30), _toy_flow("f", period=100, tx=10, release=0, upper=30), _toy_flow("g", period=100, tx=10, release=20, upper=30)]},
    ]
    result = run_exact_problem({"hypercycle_ticks": 100, "group_timeout_s": 60, "groups": groups}, working)
    observed = {item["source"]: item for item in result["groups"]}
    checks = {"sat": observed["sat"]["solver_status"] == "SAT" and observed["sat"]["optimal"] and observed["sat"]["changed_flow_count"] == 1,
              "unsat": observed["unsat"]["solver_status"] == "UNSAT",
              "star_minimum": observed["star"]["solver_status"] == "SAT" and observed["star"]["changed_flow_count"] == 1,
              "z3_solver": bool(result.get("z3_version"))}
    return {"checks": checks, "pass": all(checks.values()), "z3_version": result.get("z3_version"), "groups": result["groups"]}


def solve_release_groups(groups: list[dict[str, Any]], working: Path) -> tuple[dict[str, dict[str, int]], list[dict[str, Any]], dict[str, Any]]:
    result = run_exact_problem(_exact_problem(groups), working)
    returned = {(item["scale"], item["source"], item["source_egress"]): item for item in result["groups"]}
    releases: dict[str, dict[str, int]] = {"M": {}, "L": {}}
    summary: list[dict[str, Any]] = []
    for group in groups:
        item = returned.get((group["scale"], group["source"], group["source_egress"]))
        if item is None:
            raise RuntimeError("SOURCE_RELEASE_OPTIMALITY_UNPROVEN:MISSING_GROUP_RESULT")
        row = {key: group[key] for key in ("scale", "source", "source_egress", "flow_count", "instance_count", "original_collision_edges", "original_mvc_size")}
        row.update({key: item.get(key, "") for key in ("solver_status", "optimal", "changed_flow_count", "total_shift_ticks", "max_shift_ticks", "constraint_count", "solve_ms")})
        summary.append(row)
        if item["solver_status"] == "UNSAT":
            continue
        if item["solver_status"] != "SAT" or not bool(item.get("optimal")):
            continue
        assignments = {str(flow_id): int(ticks) for flow_id, ticks in item["assignments"].items()}
        expected = {str(flow["flow_id"]) for flow in group["flows"]}
        if assignments.keys() != expected:
            raise RuntimeError("SOURCE_RELEASE_OPTIMALITY_UNPROVEN:INCOMPLETE_ASSIGNMENT")
        releases[str(group["scale"])].update(assignments)
    return releases, sorted(summary, key=lambda row: (row["scale"], row["source_egress"], row["source"])), result


def _backend(path: Path, output: Path) -> Any:
    backend = H2sJrsBackend(EXECUTABLE, candidate_paths=5, quantum_ns=100, memory_limit_mb=8192,
                            h2s_flow_sorting=4, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0,
                            attempt_celf_fallback=True)
    result = backend.synthesize(RecoverySynthesisRequest(path, solver_timeout_s=30, route_scope="all-reroute",
                                                         forwarding_model="stream-aware", output_directory=output))
    write_attempt_logs(output / "logs", result)
    return result


def _attempt(scenario: dict[str, Any], value: dict[str, Any] | None, algorithm: str) -> dict[str, Any]:
    if value is None:
        return {"algorithm": algorithm, "status": "NOT_ATTEMPTED", "complete": False, "signature": None,
                "expected_instances": EXPECTED_INSTANCE_COUNTS[scenario["scenario_name"][0]], "complete_instances": 0,
                "upstream_verifier": False, "static_checker": False, "candidate_vector_sha": "", "wall_ms": 0.0, "peak_rss_bytes": 0}
    try:
        payload = parse_backend_output(str(value.get("stdout", ""))); signature = signatures(scenario, payload, algorithm)
        complete = (signature["scheduled_flow_count"] == signature["requested_flow_count"] == len(scenario["tt_flows"])
                    and signature["hnf_flow_count"] == 0
                    and bool(payload.get("upstream_verifier_pass")) and bool(value.get("project_static_checker_pass")))
        return {"algorithm": algorithm, "status": "P0_COMPLETE" if complete else "PARTIAL_SCHEDULE", "complete": complete,
                "signature": signature, "expected_instances": sum(int(row["expected_instances"]) for row in signature["identities"]),
                "complete_instances": sum(int(row["complete_instances"]) for row in signature["identities"]),
                "upstream_verifier": bool(payload.get("upstream_verifier_pass")),
                "static_checker": bool(value.get("project_static_checker_pass")),
                "candidate_vector_sha": canonical_sha256(sorted((int(k), int(v)) for k, v in payload.get("candidate_path_counts", {}).items())),
                "wall_ms": float(value.get("wall_ms") or 0.0), "peak_rss_bytes": int(value.get("peak_rss_bytes") or 0)}
    except Exception as error:
        return {"algorithm": algorithm, "status": "OUTPUT_INVALID", "complete": False, "signature": None,
                "expected_instances": EXPECTED_INSTANCE_COUNTS[scenario["scenario_name"][0]], "complete_instances": 0,
                "upstream_verifier": False, "static_checker": False, "candidate_vector_sha": "", "wall_ms": float(value.get("wall_ms") or 0.0),
                "peak_rss_bytes": int(value.get("peak_rss_bytes") or 0), "parse_error": str(error)}


def observe(scenario: dict[str, Any], result: Any) -> dict[str, Any]:
    attempts = {str(row.get("algorithm", "")).upper(): row for row in result.statistics.get("attempts", [])}
    h2s, celf = _attempt(scenario, attempts.get("H2S"), "H2S"), _attempt(scenario, attempts.get("CELF"), "CELF") if "CELF" in attempts else None
    formal = h2s if h2s["complete"] else (celf if celf and celf["complete"] else h2s)
    schedule_sha = canonical_sha256(result.statistics.get("route_schedule", [])) if result.feasible else ""
    return {"backend_status": result.status.value, "h2s": h2s, "celf": celf, "formal": formal,
            "normalized_schedule_sha": schedule_sha, "total_wall_ms": float(result.timings_ms.get("total_backend", 0.0)),
            "peak_rss_bytes": max([item["peak_rss_bytes"] for item in (h2s, celf) if item] or [0])}


def p0_row(scenario_id: str, repeat: int, scenario: dict[str, Any], observation: dict[str, Any],
           assignment: list[dict[str, Any]], static: dict[str, Any]) -> dict[str, Any]:
    scale, topology = scenario_id.split("_", 1); h2s, celf, formal = observation["h2s"], observation["celf"], observation["formal"]
    hs, cs, fs = h2s["signature"], celf["signature"] if celf else None, formal["signature"]
    changed = sum(bool(row["changed"]) for row in assignment if row["scale"] == scale)
    return {"scenario": scenario_id, "scale": scale, "topology": topology, "repeat": repeat,
            "original_flow_count": EXPECTED_FLOW_COUNTS[scale], "derived_flow_count": len(scenario["tt_flows"]),
            "changed_release_flow_count": changed, "static_collision_edges": static["logical_collision_edge_count"],
            "H2S_status": h2s["status"], "H2S_scheduled_count": hs["scheduled_flow_count"] if hs else 0,
            "H2S_HNF_count": hs["hnf_flow_count"] if hs else len(scenario["tt_flows"]), "H2S_HNF_set_sha": hs["hnf_set_sha256"] if hs else "",
            "H2S_complete": h2s["complete"], "H2S_ms": h2s["wall_ms"], "CELF_attempted": celf is not None,
            "CELF_status": celf["status"] if celf else "NOT_ATTEMPTED", "CELF_scheduled_count": cs["scheduled_flow_count"] if cs else 0,
            "CELF_HNF_count": cs["hnf_flow_count"] if cs else len(scenario["tt_flows"]), "CELF_HNF_set_sha": cs["hnf_set_sha256"] if cs else "",
            "CELF_complete": celf["complete"] if celf else False, "CELF_ms": celf["wall_ms"] if celf else 0.0,
            "formal_backend_status": observation["backend_status"], "formal_algorithm": formal["algorithm"],
            "formal_scheduled_count": fs["scheduled_flow_count"] if fs else 0, "formal_HNF_count": fs["hnf_flow_count"] if fs else len(scenario["tt_flows"]),
            "formal_complete": formal["complete"], "expected_instances": formal["expected_instances"],
            "complete_instances": formal["complete_instances"], "upstream_verifier": formal["upstream_verifier"],
            "static_checker": formal["static_checker"], "candidate_vector_sha": formal["candidate_vector_sha"],
            "normalized_schedule_sha": observation["normalized_schedule_sha"],
            "instance_completion_sha": fs["instance_completion_sha256"] if fs else "", "peak_RSS": observation["peak_rss_bytes"],
            "total_wall_ms": observation["total_wall_ms"]}


def repeatability_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows: grouped[str(row["scenario"])].append(row)
    output: list[dict[str, Any]] = []; passed = True
    keys = ("formal_backend_status", "formal_scheduled_count", "formal_HNF_count", "H2S_HNF_set_sha", "CELF_HNF_set_sha",
            "candidate_vector_sha", "normalized_schedule_sha", "instance_completion_sha", "upstream_verifier", "static_checker")
    for scenario_id, values in sorted(grouped.items()):
        if len(values) != 2:
            passed = False; output.append({"scenario": scenario_id, "repeatability_pass": False, "missing_repeat": True}); continue
        left, right = sorted(values, key=lambda row: int(row["repeat"]))
        exact = {f"{key}_exact": left[key] == right[key] for key in keys}
        row_pass = all(exact.values()); passed &= row_pass
        output.append({"scenario": scenario_id, **exact, "repeatability_pass": row_pass})
    return output, passed


def optimizer_design() -> str:
    return """# Exact source-egress-aware release optimizer

Each TT flow has an integer 100-ns release variable `R'` with domain
`0 <= R' <= floor(P/5)`.  Its original release is checked to be in that same
domain.  For every pair of flows on one proven unavoidable source egress and
for every periodic instance pair, the half-open interval `[R'+kP,R'+kP+C)` is
made non-overlapping.  Three `-H, 0, +H` shifted copies of each opposite
interval encode circular 8-ms boundary semantics without truncation.  No
cross-egress, routing, downstream-link, placement, deadline, or scheduler
outcome constraint is present.

Z3's `Solver` is used with a 60-second limit per source group.  It first finds
SAT, then proves the exact lexicographic objective by repeated SAT checks:
(1) minimum changed-flow count, (2) minimum total absolute shift, (3) minimum
maximum absolute shift, and (4) canonical-flow-ID-order minimization of every
remaining `R'`.  `Optimize` and heuristic fallbacks are deliberately absent.
An `unknown` result or process timeout is reported as
`SOURCE_RELEASE_OPTIMALITY_UNPROVEN`; an UNSAT group stays inside the original
20%-period window and is reported as
`SOURCE_RELEASE_ASSIGNMENT_UNSAT_WITHIN_ORIGINAL_WINDOW`.
"""


def _base_artifacts(output: Path, *, mode: str, parent: str, implementation: str, frozen: dict[str, str],
                    scenarios: dict[str, dict[str, Any]], grouping: list[dict[str, Any]], toy: dict[str, Any] | None = None) -> None:
    write_json(output / "environment.json", {"platform": platform.platform(), "python": platform.python_version(),
               "os_uname": list(os.uname()), "mode": mode, "executable": str(EXECUTABLE),
               "executable_sha256": sha256_file(EXECUTABLE) if EXECUTABLE.is_file() else "MISSING",
               "solver_python": str(SOLVER_PYTHON), "solver_script_sha256": sha256_file(SOLVER_SCRIPT),
               "backend_config": FORMAL_BACKEND_CONFIG, "scheduler_runs": 0})
    write_json(output / "source_manifest.json", {"input_source": "frozen original exp18 D100 scenarios only",
               "source_scenario_sha256": {scenario_id: sha256_file(source_scenario_path(scenario_id)) for scenario_id in scenarios},
               "source_counts": {scale: {"flow_count": EXPECTED_FLOW_COUNTS[scale], "instance_count": EXPECTED_INSTANCE_COUNTS[scale]} for scale in ("M", "L")},
               "frozen_tree_sha256": frozen, "exp18i_collision_verdict_sha256": sha256_file(ROOT / "results" / "fixed_release_collision_audit" / "collision_verdict.json"),
               "release_allocator_reads_hnf_or_scheduler_outcomes": False, "optimizer_input_excludes_topology": True,
               "timing_model": "exp18i qualified quantize_flow, one-time frame accounting, 100-ns integer ticks"})
    write_csv(output / "source_egress_grouping.csv", grouping); write_text(output / "optimizer_design.md", optimizer_design())
    if toy is not None: write_json(output / "optimizer_toy_qualification.json", toy)


def _write_final(output: Path, *, mode: str, parent: str, implementation: str, frozen: dict[str, str], scenarios: dict[str, dict[str, Any]],
                 grouping: list[dict[str, Any]], groups: list[dict[str, Any]], group_rows: list[dict[str, Any]], solver: dict[str, Any],
                 assignment: list[dict[str, Any]], integrity: list[dict[str, Any]], static: list[dict[str, Any]],
                 p0: list[dict[str, Any]], repeat: list[dict[str, Any]], verdict_name: str, toy: dict[str, Any], failure: str = "") -> int:
    assert verdict_name in VERDICTS
    _base_artifacts(output, mode=mode, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, grouping=grouping, toy=toy)
    write_csv(output / "source_group_optimization.csv", group_rows); write_csv(output / "release_assignment.csv", assignment)
    summaries, kinds = shift_summaries(assignment) if assignment else ([], [])
    write_csv(output / "release_shift_summary.csv", summaries); write_csv(output / "release_shift_by_flow_kind.csv", kinds)
    write_csv(output / "derived_scenario_integrity.csv", integrity); write_csv(output / "static_collision_validation.csv", static)
    write_csv(output / "p0_qualification.csv", p0); write_csv(output / "repeatability.csv", repeat)
    releases = {scale: {row["flow_id"]: int(row["new_release_ticks"]) for row in assignment if row["scale"] == scale} for scale in ("M", "L")}
    original_mvc = {scale: sum(int(row["original_mvc_size"]) for row in group_rows if row["scale"] == scale) for scale in ("M", "L")}
    changed = {scale: sum(bool(row["changed"]) for row in assignment if row["scale"] == scale) for scale in ("M", "L")}
    certificate = {"solver": solver.get("solver", ""), "z3_version": solver.get("z3_version", ""), "objective_hierarchy": ["minimum changed-flow count", "minimum total absolute shift", "minimum maximum absolute shift", "canonical flow_id release lexicographic minimum"],
                   "per_group_all_optimal": bool(group_rows) and all(bool(row.get("optimal")) for row in group_rows),
                   "Medium": {"minimum_changed_count": changed["M"], "exp18i_MVC_lower_bound": original_mvc["M"], "matches_MVC_lower_bound": changed["M"] == original_mvc["M"], "release_map_sha": release_map_sha256(releases["M"]) if releases["M"] else ""},
                   "Large": {"minimum_changed_count": changed["L"], "exp18i_MVC_lower_bound": original_mvc["L"], "matches_MVC_lower_bound": changed["L"] == original_mvc["L"], "release_map_sha": release_map_sha256(releases["L"]) if releases["L"] else ""}}
    write_json(output / "release_optimality_certificate.json", certificate)
    all_integrity = bool(integrity) and all(bool(row["integrity_pass"]) for row in integrity)
    all_static = bool(static) and all(bool(row["collision_free"]) for row in static)
    repeat_pass = bool(repeat) and all(bool(row["repeatability_pass"]) for row in repeat)
    complete = [row for row in p0 if bool(row["formal_complete"])]
    h2s_only = len(p0) == 12 and all(bool(row["H2S_complete"]) for row in p0)
    verdict = {"formal_verdict": verdict_name, "mode": mode, "failure": failure or None,
               "optimizer_exact_optimal": certificate["per_group_all_optimal"], "derived_integrity_pass": all_integrity,
               "static_collision_free_pass": all_static, "formal_p0_complete_count": f"{len(complete)}/{len(p0)}",
               "repeatability_pass": repeat_pass, "H2S_ONLY_P0_QUALIFIED": h2s_only if mode == "full" else False,
               "next_stage_recommendation": "REALISTIC_FULL_DENSITY_PF_COST_CAMPAIGN" if verdict_name == "FULL_DENSITY_PF_BENCHMARK_QUALIFIED" else "STOP_AND_REVIEW_RECORDED_VERDICT"}
    write_json(output / "qualification_verdict.json", verdict)
    table = ["| scenario | formal complete (repeat 1) | H2S complete | CELF complete |", "|---|---:|---:|---:|"]
    for scenario_id in ("M_RING", "M_REDSTAR", "M_ROR", "L_RING", "L_REDSTAR", "L_ROR"):
        row = next((item for item in p0 if item["scenario"] == scenario_id and item["repeat"] == 1), None)
        table.append(f"| {scenario_id} | {row['formal_complete'] if row else 'not run'} | {row['H2S_complete'] if row else 'not run'} | {row['CELF_complete'] if row else 'not run'} |")
    shift_text = "; ".join(f"{row['scale']}: {row['changed_flows']} changed, total {row['total_shift_ticks']} ticks, max {row['max_shift_ticks']} ticks" for row in summaries) or "not available"
    write_text(output / "summary.md", "\n".join(("# exp18j: source-egress-aware fixed-release assignment", "",
        "exp18j does not lower density: it preserves Medium 352/3616 and Large 928/9632 logical-flow/instance counts. exp18i proved that independently hashed per-flow releases could force impossible same-source-egress exact launches.",
        "Fixed-release semantics were not changed: first-hop start remains exactly `release + k*period`. The sole workload semantic change in each SAR scenario is `release_offset_s`; periods, deadlines, payloads, endpoints, roles, topology, routing and backend configuration remain frozen.",
        "The allocator reads no HNF, CELF, or scheduler outcome. It uses source-local 100-ns intervals, the original 20%-of-period release window, exact non-overlap constraints, and proven lexicographic minimum perturbation. No new source collision is accepted by the exp18i static detector.",
        f"Release impact: {shift_text}. Original MVC lower bounds / exact changed counts are M {original_mvc['M']}/{changed['M']} and L {original_mvc['L']}/{changed['L']}.",
        "", *table, "", f"Formal verdict: **`{verdict_name}`**. H2S-alone 6/6 status: `{h2s_only if mode == 'full' else 'not formal'}`. CELF is recorded separately above; verifier/checker and repeatability are hard gates.",
        "", "A qualified result concerns this fixed backend/model only. It does not mean the industrial roles are inherently easy or that original independent release hashing was the only possible generator. It repairs an internal source-launch construction defect; exp18j performs no PF, fault enumeration, Profile Store, OMNeT++, or INET run.", "")))
    if verdict_name == "FULL_DENSITY_PF_BENCHMARK_QUALIFIED":
        derived = {scenario_id: output / "derived_scenarios" / f"{scenario_id}_SAR.json" for scenario_id in scenarios}
        write_json(output / "selected_pf_benchmark_manifest.json", {"experiment": "exp18j_source_egress_aware_release_assignment", "release_assignment_method": certificate["solver"], "objective_hierarchy": certificate["objective_hierarchy"], "z3_version": certificate["z3_version"], "source_exp18_scenario_sha256": {sid: sha256_file(source_scenario_path(sid)) for sid in scenarios}, "exp18i_verdict_sha256": sha256_file(ROOT / "results/fixed_release_collision_audit/collision_verdict.json"), "changed_flow_counts": changed, "total_shift_ticks": {row["scale"]: row["total_shift_ticks"] for row in summaries}, "derived_scenario_paths": {sid: str(path.relative_to(ROOT)) for sid, path in derived.items()}, "derived_scenario_sha256": {sid: sha256_file(path) for sid, path in derived.items()}, "workload_counts": EXPECTED_FLOW_COUNTS, "instance_counts": EXPECTED_INSTANCE_COUNTS, "release_map_sha256": {scale: release_map_sha256(releases[scale]) for scale in releases}, "static_collision_graph_sha": {row["scenario"]: row["static_collision_graph_sha"] for row in static}, "backend_config": FORMAL_BACKEND_CONFIG, "p0_verification_status": "PASS", "repeatability_status": "PASS"})
    artifacts = {str(path.relative_to(output)): sha256_file(path) for path in output.rglob("*") if path.is_file() and path.name != "analysis_manifest.json"}
    write_json(output / "analysis_manifest.json", {"experiment": "exp18j_source_egress_aware_release_assignment", "mode": mode, "parent_commit": parent, "implementation_commit": implementation, "results_commit": "RECORDED_BY_SUBSEQUENT_GIT_HISTORY", "source_exp18_scenario_sha256": {sid: sha256_file(source_scenario_path(sid)) for sid in scenarios}, "exp18i_verdict_sha256": sha256_file(ROOT / "results/fixed_release_collision_audit/collision_verdict.json"), "frozen_tree_sha256": frozen, "source_grouping_sha256": sha256_file(output / "source_egress_grouping.csv"), "optimizer_implementation_sha256": sha256_file(SOLVER_SCRIPT), "solver": certificate["solver"], "z3_version": certificate["z3_version"], "objective_hierarchy": certificate["objective_hierarchy"], "release_map_sha256": {scale: release_map_sha256(releases[scale]) if releases[scale] else "" for scale in releases}, "derived_scenario_sha256": {str(path.relative_to(output)): sha256_file(path) for path in sorted((output / "derived_scenarios").glob("*.json"))}, "static_validation_sha256": sha256_file(output / "static_collision_validation.csv"), "backend_config": FORMAL_BACKEND_CONFIG, "p0_result_sha256": sha256_file(output / "p0_qualification.csv"), "artifact_sha256": artifacts})
    assert_frozen()
    return 0 if verdict_name in {"FULL_DENSITY_PF_BENCHMARK_QUALIFIED", "SOURCE_AWARE_RELEASE_P0_PARTIALLY_COMPLETE", "SOURCE_AWARE_RELEASE_P0_STILL_INCOMPLETE", "INCONCLUSIVE"} else 2


def run(*, quick: bool, implementation_commit: str | None) -> int:
    mode = "quick" if quick else "full"; output = OUT / "quick_validation" if quick else OUT
    if output.exists(): raise RuntimeError(f"REFUSE_TO_OVERWRITE_EVIDENCE:{output}")
    if not EXECUTABLE.is_file(): raise RuntimeError(f"MISSING_H2S_EXECUTABLE:{EXECUTABLE}")
    if not quick and git("status", "--porcelain"): raise RuntimeError("FULL_CAMPAIGN_REQUIRES_CLEAN_IMPLEMENTATION_COMMIT")
    parent = git("rev-parse", "HEAD"); implementation = implementation_commit or parent
    if not quick and implementation != parent: raise RuntimeError("FORMAL_PHASE_B_MUST_START_AT_IMPLEMENTATION_COMMIT")
    frozen = assert_frozen(); scenarios = source_scenarios()
    try:
        grouping, groups, grouping_pass = optimizer_problem(scenarios)
    except ReleaseAssignmentError as error:
        output.mkdir(parents=True); return _write_final(output, mode=mode, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, grouping=[], groups=[], group_rows=[], solver={}, assignment=[], integrity=[], static=[], p0=[], repeat=[], verdict_name="SOURCE_RELEASE_INPUT_INVALID", toy={"pass": False}, failure=str(error))
    selected_groups = [group for group in groups if not quick or group["scale"] == "M"]
    with tempfile.TemporaryDirectory(prefix="exp18j-toy-") as temporary:
        toy = optimizer_toy_qualification(Path(temporary))
    if not toy["pass"] or not grouping_pass:
        output.mkdir(parents=True); verdict = "SOURCE_EGRESS_GROUPING_MISMATCH" if not grouping_pass else "SOURCE_RELEASE_OPTIMALITY_UNPROVEN"
        return _write_final(output, mode=mode, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, grouping=grouping, groups=selected_groups, group_rows=[], solver={}, assignment=[], integrity=[], static=[], p0=[], repeat=[], verdict_name=verdict, toy=toy, failure="GROUPING_OR_TOY_GATE_FAILED")
    output.mkdir(parents=True)
    releases, group_rows, solver = solve_release_groups(selected_groups, output / "optimizer_raw")
    if any(row["solver_status"] == "UNSAT" for row in group_rows):
        return _write_final(output, mode=mode, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, grouping=grouping, groups=selected_groups, group_rows=group_rows, solver=solver, assignment=[], integrity=[], static=[], p0=[], repeat=[], verdict_name="SOURCE_RELEASE_ASSIGNMENT_UNSAT_WITHIN_ORIGINAL_WINDOW", toy=toy)
    if any(row["solver_status"] != "SAT" or not bool(row["optimal"]) for row in group_rows):
        return _write_final(output, mode=mode, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, grouping=grouping, groups=selected_groups, group_rows=group_rows, solver=solver, assignment=[], integrity=[], static=[], p0=[], repeat=[], verdict_name="SOURCE_RELEASE_OPTIMALITY_UNPROVEN", toy=toy)
    assignment = assignment_rows(selected_groups, releases)
    scenario_ids = ["M_RING", "M_REDSTAR", "M_ROR"] if quick else list(scenarios)
    derived: dict[str, dict[str, Any]] = {}; integrity: list[dict[str, Any]] = []; static: list[dict[str, Any]] = []
    for scenario_id in scenario_ids:
        scale = scenario_id[0]; child = apply_release_assignment(scenarios[scenario_id], scenario_id, releases[scale]); derived[scenario_id] = child
        path = output / "derived_scenarios" / f"{scenario_id}_SAR.json"; write_json(path, child)
        integrity.append(derived_integrity(scenarios[scenario_id], child, scenario_id, releases[scale]))
        static.append(derived_static_validation(scenario_id, child, release_map_sha256(releases[scale])))
    if not all(bool(row["integrity_pass"]) for row in integrity):
        return _write_final(output, mode=mode, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, grouping=grouping, groups=selected_groups, group_rows=group_rows, solver=solver, assignment=assignment, integrity=integrity, static=static, p0=[], repeat=[], verdict_name="DERIVED_RELEASE_SCENARIO_INTEGRITY_FAILED", toy=toy)
    if not all(bool(row["collision_free"]) for row in static):
        return _write_final(output, mode=mode, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, grouping=grouping, groups=selected_groups, group_rows=group_rows, solver=solver, assignment=assignment, integrity=integrity, static=static, p0=[], repeat=[], verdict_name="RELEASE_ASSIGNMENT_COLLISION_REMAINS", toy=toy)
    plan = [("M_RING", 1)] if quick else [(scenario_id, repeat) for scenario_id in scenario_ids for repeat in (1, 2)]
    static_by = {row["scenario"]: row for row in static}; p0: list[dict[str, Any]] = []
    for scenario_id, repeat in plan:
        path = output / "derived_scenarios" / f"{scenario_id}_SAR.json"
        result = _backend(path, output / "raw_backend_output" / scenario_id / f"repeat_{repeat}")
        p0.append(p0_row(scenario_id, repeat, derived[scenario_id], observe(derived[scenario_id], result), assignment, static_by[scenario_id]))
    repeat, repeat_pass = repeatability_rows(p0) if not quick else ([], False)
    if quick:
        verdict = "INCONCLUSIVE"
    elif not repeat_pass:
        verdict = "RELEASE_REDESIGN_P0_NONDETERMINISTIC"
    elif all(bool(row["formal_complete"]) for row in p0):
        verdict = "FULL_DENSITY_PF_BENCHMARK_QUALIFIED"
    elif any(bool(row["formal_complete"]) for row in p0):
        verdict = "SOURCE_AWARE_RELEASE_P0_PARTIALLY_COMPLETE"
    else:
        verdict = "SOURCE_AWARE_RELEASE_P0_STILL_INCOMPLETE"
    return _write_final(output, mode=mode, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, grouping=grouping, groups=selected_groups, group_rows=group_rows, solver=solver, assignment=assignment, integrity=integrity, static=static, p0=p0, repeat=repeat, verdict_name=verdict, toy=toy)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--quick", action="store_true"); parser.add_argument("--implementation-commit")
    args = parser.parse_args(); return run(quick=args.quick, implementation_commit=args.implementation_commit)


if __name__ == "__main__":
    raise SystemExit(main())
