"""exp18e: pre-registered healthy-P0 deadline feasibility calibration.

This script intentionally does not import a scenario generator, PF code, OMNeT++
or INET.  It consumes only frozen exp18 scenario JSON files and sends derived,
deadline-only copies to the existing qualified healthy-P0 H2S/CELF adapter.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import platform
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.deadline_feasibility_calibration import (
    ALPHAS, FROZEN_TREE_SHA256, HYPERCYCLE_NS, ORDER, OUT, ROOT, SOURCE, Alpha,
    assert_backend_config, assert_frozen_tree, backend_config, canonical_sha256,
    cycle_boundary_rows, deadline_calibration_rows, derived_integrity, expected_instance_count,
    flow_kind, load_derived, materialize_derived_scenarios, sha256_file, topology_projection,
)
from tools.h2s_jrs_backend import (H2sJrsBackend, check_h2s_solution,
    parse_backend_output, prepare_h2s_inputs)
from tools.jrs_wa_adapter import canonical_json_bytes, seconds_to_ns
from tools.recovery_backend import RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_p0_hnf_diagnosis import raw as frozen_raw
from tools.run_p0_hnf_mechanism_diagnosis import signatures

EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
TIMEOUT_S = 30
VALID_VERDICTS = {"CALIBRATED_WORKLOAD_QUALIFIED", "NO_PF_ELIGIBLE_ALPHA_IN_PREREGISTERED_LADDER",
                  "BASELINE_PARITY_FAILED", "CALIBRATION_CYCLE_SEMANTICS_UNQUALIFIED",
                  "CALIBRATION_NONDETERMINISTIC", "DERIVED_SCENARIO_INTEGRITY_FAILED", "INCONCLUSIVE"}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def shell(command: list[str]) -> str:
    return subprocess.run(command, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def git_clean() -> bool:
    return not shell(["git", "status", "--porcelain"])


def scale_topology(scenario_id: str) -> tuple[str, str]:
    scale, topology = scenario_id.split("_", 1)
    return scale, topology


def result_output(mode: str) -> Path:
    return OUT if mode == "full" else OUT / "quick_validation"


def make_cycle_boundary_case() -> dict[str, Any]:
    """A non-formal mixed-period D=T case whose last deadlines cross H.

    The compact topology has no alternate variables: the proof relies on actual
    upstream scheduling plus the adapter's normalizer and independent checker.
    """
    nodes = [{"id": "a", "type": "end_system"}, {"id": "b", "type": "end_system"},
             {"id": "sw", "type": "switch"}, {"id": "d", "type": "end_system"}]
    links = [{"id": "a_sw", "endpoint_a": "a", "endpoint_b": "sw", "bitrate_bps": 1_000_000_000,
              "propagation_delay_s": 0.0},
             {"id": "b_sw", "endpoint_a": "b", "endpoint_b": "sw", "bitrate_bps": 1_000_000_000,
              "propagation_delay_s": 0.0},
             {"id": "sw_d", "endpoint_a": "sw", "endpoint_b": "d", "bitrate_bps": 1_000_000_000,
              "propagation_delay_s": 0.0}]
    def flow(identifier: str, source: str, period_ns: int, release_ns: int) -> dict[str, Any]:
        return {"id": identifier, "source": source, "destination": "d", "packet_size_bytes": 64,
                "period_s": period_ns / 1e9, "deadline_e2e_s": period_ns / 1e9,
                "schedule_deadline_budget_s": period_ns / 1e9, "release_offset_s": release_ns / 1e9,
                "pcp": 4, "traffic_class": 1}
    return {"schema_version": 1, "scenario_name": "exp18e_cycle_boundary_qualification",
            "forwarding_model": "stream-aware",
            # The upstream hypercycle is the LCM of 0.5 ms and 1 ms, namely
            # 1 ms.  Keep the canonical scenario cycle equal to that LCM so
            # instance grouping is checked against the same boundary.
            "simulation": {"duration_s": .01, "cycle_time_s": .001, "time_quantum_s": 1e-9,
                           "failure_time_s": .005, "solver_delay_s": 0.0, "random_seed": 1024},
            "network": {"default_bitrate_bps": 1_000_000_000, "default_propagation_delay_s": 0.0},
            "scheduling": {"ingress_margin_s": 0.0, "hop_margin_s": 0.0, "endpoint_budget_s": 0.0,
                           "frame_overhead_bytes": 64, "be_traffic_class": 0},
            "nodes": nodes, "links": links,
            "tt_flows": [flow("SF_BOUNDARY", "a", 500_000, 100_000),
                         flow("SC_BOUNDARY", "b", 1_000_000, 200_000)],
            "be_flows": [], "fault_candidates": [], "fault_candidate_policy": {"mode": "explicit", "exclude": []}}


def run_backend(path: Path, output: Path) -> Any:
    backend = H2sJrsBackend(EXECUTABLE, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0,
                            attempt_celf_fallback=True)
    request = RecoverySynthesisRequest(path, solver_timeout_s=TIMEOUT_S, route_scope="all-reroute",
                                       forwarding_model="stream-aware", output_directory=output)
    result = backend.synthesize(request)
    write_attempt_logs(output / "logs", result)
    return result


def complete_signature(scenario: dict[str, Any], payload: dict[str, Any], algorithm: str,
                       attempt: dict[str, Any]) -> dict[str, Any]:
    signature = signatures(scenario, payload, algorithm)
    requested = signature["requested_flow_count"]
    complete = (signature["scheduled_flow_count"] == requested == len(scenario["tt_flows"])
                and bool(payload.get("upstream_verifier_pass"))
                and bool(attempt.get("project_static_checker_pass")))
    instances = signature["identities"]
    return {"algorithm": algorithm, "status": "P0_COMPLETE" if complete else "PARTIAL_SCHEDULE",
            "complete": complete, "signature": signature,
            "expected_instances": sum(int(row["expected_instances"]) for row in instances),
            "complete_instances": sum(int(row["complete_instances"]) for row in instances),
            "missing_instances": sum(int(row["missing_instances"]) for row in instances),
            "upstream_verifier": bool(payload.get("upstream_verifier_pass")),
            "static_checker": bool(attempt.get("project_static_checker_pass")),
            "peak_rss_bytes": int(attempt.get("peak_rss_bytes") or 0),
            "wall_ms": float(attempt.get("wall_ms") or 0.0)}


def observe_result(scenario: dict[str, Any], result: Any) -> dict[str, Any]:
    observed: dict[str, dict[str, Any]] = {}
    for attempt in result.statistics.get("attempts", []):
        algorithm = str(attempt.get("algorithm", "")).upper()
        if algorithm not in {"H2S", "CELF"}:
            continue
        try:
            payload = parse_backend_output(str(attempt.get("stdout", "")))
            observed[algorithm] = complete_signature(scenario, payload, algorithm, attempt)
        except Exception as error:  # Preserve a non-constructive backend outcome as evidence.
            observed[algorithm] = {"algorithm": algorithm, "status": "OUTPUT_UNAVAILABLE", "complete": False,
                                   "signature": None, "expected_instances": expected_instance_count(scenario),
                                   "complete_instances": 0, "missing_instances": expected_instance_count(scenario),
                                   "upstream_verifier": False, "static_checker": False,
                                   "peak_rss_bytes": int(attempt.get("peak_rss_bytes") or 0),
                                   "wall_ms": float(attempt.get("wall_ms") or 0.0), "parse_error": str(error)}
    h2s = observed.get("H2S", {"algorithm": "H2S", "status": "NOT_ATTEMPTED", "complete": False,
                                "signature": None, "expected_instances": expected_instance_count(scenario),
                                "complete_instances": 0, "missing_instances": expected_instance_count(scenario),
                                "upstream_verifier": False, "static_checker": False, "peak_rss_bytes": 0, "wall_ms": 0.0})
    celf = observed.get("CELF")
    formal = h2s if h2s["complete"] else (celf if celf and celf["complete"] else h2s)
    return {"backend_status": result.status.value, "h2s": h2s, "celf": celf, "formal": formal,
            "total_wall_ms": float(result.timings_ms.get("total_backend", 0.0)),
            "peak_rss_bytes": max([entry["peak_rss_bytes"] for entry in observed.values()] or [0])}


def p0_row(scenario_id: str, alpha: Alpha, repeat: int, scenario: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    h2s, celf, formal = observed["h2s"], observed["celf"], observed["formal"]
    signature = formal["signature"]
    total_flows = len(scenario["tt_flows"])
    formal_count = signature["scheduled_flow_count"] if signature else 0
    return {"scenario": scenario_id, "scale": scale_topology(scenario_id)[0], "topology": scale_topology(scenario_id)[1],
            "alpha_num": alpha.numerator, "alpha_den": alpha.denominator, "alpha_decimal": alpha.decimal,
            "alpha": alpha.label, "repeat": repeat, "H2S_status": h2s["status"],
            "H2S_scheduled_count": h2s["signature"]["scheduled_flow_count"] if h2s["signature"] else 0,
            "H2S_ms": h2s["wall_ms"], "CELF_attempted": celf is not None,
            "CELF_status": celf["status"] if celf else "NOT_ATTEMPTED",
            "CELF_scheduled_count": celf["signature"]["scheduled_flow_count"] if celf and celf["signature"] else 0,
            "CELF_ms": celf["wall_ms"] if celf else 0.0, "formal_status": observed["backend_status"],
            "formal_algorithm": formal["algorithm"], "formal_scheduled_count": formal_count,
            "total_flows": total_flows, "scheduled_ratio": formal_count / total_flows,
            "P0_complete": formal["complete"], "expected_instances": formal["expected_instances"],
            "complete_instances": formal["complete_instances"], "missing_instances": formal["missing_instances"],
            "upstream_verifier": formal["upstream_verifier"], "static_checker": formal["static_checker"],
            "peak_RSS": observed["peak_rss_bytes"], "HNF_count": signature["hnf_flow_count"] if signature else total_flows,
            "HNF_set_sha": signature["hnf_set_sha256"] if signature else "",
            "instance_completion_sha": signature["instance_completion_sha256"] if signature else ""}


def cycle_qualification(output: Path) -> tuple[dict[str, Any], bool]:
    root = output / "cycle_boundary_qualification"
    path = root / "scenario.json"
    write_json(path, make_cycle_boundary_case())
    result = run_backend(path, root / "raw_backend_output")
    prepared = prepare_h2s_inputs(path, root / "normalizer_input")
    h2s_attempt = next((row for row in result.statistics.get("attempts", []) if row.get("algorithm") == "H2S"), None)
    checks: dict[str, bool] = {"upstream_accepts_d_equals_period": False, "normalizer_accepts": False,
                                "instance_grouping_consistent": False, "deadline_checker_pass": False,
                                "windows_inside_cycle": False, "last_deadline_crosses_cycle": False,
                                "no_silent_clipping": False}
    detail: dict[str, Any] = {"result_status": result.status.value, "h2s_attempted": h2s_attempt is not None}
    if h2s_attempt:
        try:
            payload = parse_backend_output(str(h2s_attempt.get("stdout", "")))
            from tools.h2s_jrs_backend import normalize_schedule
            normalized = normalize_schedule(prepared, payload, 100)
            report = check_h2s_solution(prepared, normalized)
            by_name = {row["check"]: bool(row["passed"]) for row in report["checks"]}
            cycle_ns = seconds_to_ns(prepared.scenario["simulation"]["cycle_time_s"], "cycle")
            qrows = {row["flow_id"]: row for row in prepared.quantization_rows}
            expected = {row["flow_id"]: cycle_ns // row["period_ns"] for row in prepared.quantization_rows}
            grouped = defaultdict(set)
            for row in normalized["route_schedule"]:
                grouped[row["flow_id"]].add(int(row["instance_index"]))
            crossing = []
            for flow in prepared.scenario["tt_flows"]:
                row = qrows[flow["id"]]
                last_release = row["release_ns"] + (expected[flow["id"]] - 1) * row["period_ns"]
                crossing.append(last_release + row["deadline_ns"] > cycle_ns)
            checks.update({"upstream_accepts_d_equals_period": bool(payload["upstream_verifier_pass"]),
                           "normalizer_accepts": True,
                           "instance_grouping_consistent": all(grouped[flow_id] == set(range(count))
                                                              for flow_id, count in expected.items()),
                           "deadline_checker_pass": by_name.get("END_TO_END_DEADLINE", False),
                           "windows_inside_cycle": by_name.get("WINDOWS_INSIDE_CYCLE", False),
                           "last_deadline_crosses_cycle": all(crossing),
                           "no_silent_clipping": all(row["deadline_ns"] == row["period_ns"] for row in qrows.values())})
            detail.update({"checker_valid": report["valid"], "checker_checks": report["checks"],
                           "expected_instances": expected, "observed_instance_indices":
                           {key: sorted(value) for key, value in grouped.items()}, "cycle_ns": cycle_ns})
        except Exception as error:
            detail["qualification_error"] = str(error)
    qualified = all(checks.values())
    verdict = {"formal_verdict": "CALIBRATION_CYCLE_SEMANTICS_UNQUALIFIED" if not qualified else "QUALIFIED",
               "cycle_semantics_qualified": qualified, "checks": checks, "detail": detail,
               "interpretation": "D=T is accepted with nonzero release; final absolute deadlines may cross the hyperperiod while scheduled slots remain inside it." if qualified else "No clipping or release change was applied; formal calibration is stopped."}
    write_json(output / "qualification_verdict.json", verdict)
    return verdict, qualified


def frozen_signature(scenario_id: str, algorithm: str) -> dict[str, Any]:
    scenario, _, payload = frozen_raw(scenario_id, algorithm.lower())
    return signatures(scenario, payload, algorithm)


def baseline_parity(rows: list[dict[str, Any]], observations: dict[tuple[str, str, int], dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    parity: list[dict[str, Any]] = []
    all_pass = True
    alpha = ALPHAS[0]
    for scenario_id in ORDER:
        expected_count = 348 if scenario_id.startswith("M_") else 914
        for repeat in (1, 2):
            observed = observations[(scenario_id, alpha.tag, repeat)]
            for algorithm in ("H2S", "CELF"):
                entry = observed[algorithm.lower()]
                if entry is None:
                    parity.append({"scenario": scenario_id, "repeat": repeat, "algorithm": algorithm,
                                   "attempted": False, "parity_pass": False, "reason": "fallback_missing"})
                    all_pass = False
                    continue
                source = frozen_signature(scenario_id, algorithm)
                signature = entry["signature"]
                row = {"scenario": scenario_id, "repeat": repeat, "algorithm": algorithm, "attempted": True,
                       "source_scheduled_count": source["scheduled_flow_count"],
                       "observed_scheduled_count": signature["scheduled_flow_count"] if signature else -1,
                       "expected_scale_count": expected_count,
                       "source_HNF_set_sha": source["hnf_set_sha256"],
                       "observed_HNF_set_sha": signature["hnf_set_sha256"] if signature else "",
                       "source_instance_completion_sha": source["instance_completion_sha256"],
                       "observed_instance_completion_sha": signature["instance_completion_sha256"] if signature else ""}
                row["scheduled_count_exact"] = row["source_scheduled_count"] == row["observed_scheduled_count"] == expected_count
                row["HNF_set_exact"] = row["source_HNF_set_sha"] == row["observed_HNF_set_sha"]
                row["instance_completion_exact"] = row["source_instance_completion_sha"] == row["observed_instance_completion_sha"]
                row["parity_pass"] = all(row[key] for key in ("scheduled_count_exact", "HNF_set_exact", "instance_completion_exact"))
                parity.append(row); all_pass &= row["parity_pass"]
    return parity, all_pass


def repeatability_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, set[tuple[str, str]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["scenario"], row["alpha"])].append(row)
    results = []; failed: set[tuple[str, str]] = set()
    for (scenario, alpha), pair in sorted(grouped.items()):
        pair.sort(key=lambda row: row["repeat"])
        if len(pair) != 2:
            failed.add((scenario, alpha)); continue
        left, right = pair
        equality = {"formal_status_exact": left["formal_status"] == right["formal_status"],
                    "scheduled_count_exact": left["formal_scheduled_count"] == right["formal_scheduled_count"],
                    "HNF_set_exact": left["HNF_set_sha"] == right["HNF_set_sha"],
                    "instance_completion_exact": left["instance_completion_sha"] == right["instance_completion_sha"]}
        passed = all(equality.values())
        if not passed: failed.add((scenario, alpha))
        results.append({"scenario": scenario, "alpha": alpha, "repeat_1_status": left["formal_status"],
                        "repeat_2_status": right["formal_status"], "repeat_1_scheduled_count": left["formal_scheduled_count"],
                        "repeat_2_scheduled_count": right["formal_scheduled_count"], "repeat_1_HNF_set_sha": left["HNF_set_sha"],
                        "repeat_2_HNF_set_sha": right["HNF_set_sha"], "repeat_1_instance_completion_sha": left["instance_completion_sha"],
                        "repeat_2_instance_completion_sha": right["instance_completion_sha"], **equality,
                        "repeatability_pass": passed})
    return results, not failed, failed


def alpha_summary(rows: list[dict[str, Any]], nondeterministic: set[tuple[str, str]]) -> list[dict[str, Any]]:
    summaries = []
    for alpha in ALPHAS:
        values = [row for row in rows if row["alpha"] == alpha.label and row["repeat"] == 1]
        complete = [row for row in values if row["P0_complete"] and (row["scenario"], alpha.label) not in nondeterministic]
        summaries.append({"alpha": alpha.label, "alpha_num": alpha.numerator, "alpha_den": alpha.denominator,
                          "complete_scenario_count": len(complete), "complete_scenario_count_over_6": f"{len(complete)}/6",
                          "medium_complete_count": sum(row["scale"] == "M" for row in complete),
                          "large_complete_count": sum(row["scale"] == "L" for row in complete),
                          "minimum_scheduled_ratio": min((row["scheduled_ratio"] for row in values), default=0),
                          "median_scheduled_ratio": statistics.median([row["scheduled_ratio"] for row in values]) if values else 0,
                          "total_HNF_count": sum(int(row["HNF_count"]) for row in values),
                          "H2S_complete_count": sum(row["P0_complete"] and row["formal_algorithm"] == "H2S" for row in complete),
                          "CELF_fallback_complete_count": sum(row["P0_complete"] and row["formal_algorithm"] == "CELF" for row in complete),
                          "median_P0_ms": statistics.median([row["H2S_ms"] + row["CELF_ms"] for row in values]) if values else 0,
                          "max_P0_ms": max((row["H2S_ms"] + row["CELF_ms"] for row in values), default=0),
                          "max_RSS": max((row["peak_RSS"] for row in values), default=0)})
    return summaries


def hnf_trajectory(rows: list[dict[str, Any]], observations: dict[tuple[str, str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for scenario_id in ORDER:
        baseline = observations[(scenario_id, "A100", 1)]["formal"]["signature"]
        baseline_map = {row["flow_id"]: row["flow_completion_class"] for row in baseline["identities"]} if baseline else {}
        for alpha in ALPHAS:
            current = observations[(scenario_id, alpha.tag, 1)]["formal"]["signature"]
            current_map = {row["flow_id"]: row["flow_completion_class"] for row in current["identities"]} if current else {}
            for flow_id in sorted(set(baseline_map) | set(current_map)):
                base, now = baseline_map.get(flow_id, "UNAVAILABLE"), current_map.get(flow_id, "UNAVAILABLE")
                result.append({"scenario": scenario_id, "alpha": alpha.label, "flow_id": flow_id,
                               "flow_kind": flow_kind(flow_id), "baseline_completion_class": base,
                               "alpha_completion_class": now, "baseline_HNF": base != "FULLY_SCHEDULED",
                               "alpha_HNF": now != "FULLY_SCHEDULED", "rescued_from_baseline_HNF": base != "FULLY_SCHEDULED" and now == "FULLY_SCHEDULED",
                               "regressed_from_baseline_success": base == "FULLY_SCHEDULED" and now != "FULLY_SCHEDULED"})
    return result


def selected_alpha(summary: list[dict[str, Any]]) -> Alpha | None:
    eligible = {row["alpha"] for row in summary if row["complete_scenario_count"] == 6}
    return next((alpha for alpha in ALPHAS if alpha.label in eligible), None)


def source_manifest(scenarios: dict[str, dict[str, Any]], frozen: dict[str, str]) -> dict[str, Any]:
    return {"input_source": "direct frozen exp18 scenario JSON reads; no workload generator invoked",
            "historical_tree_sha256": frozen, "formal_scenarios": {
                scenario_id: {"path": str((SOURCE / "scenarios" / f"{scenario_id}.json").relative_to(ROOT)),
                              "sha256": sha256_file(SOURCE / "scenarios" / f"{scenario_id}.json"),
                              "tt_flow_count": len(scenarios[scenario_id]["tt_flows"]),
                              "expected_instances": expected_instance_count(scenarios[scenario_id])}
                for scenario_id in ORDER}}


def final_reports(output: Path, *, mode: str, parent_commit: str, implementation_commit: str,
                  frozen: dict[str, str], scenarios: dict[str, dict[str, Any]], transforms: list[dict[str, Any]],
                  integrity: list[dict[str, Any]], cycle_ok: bool, qualification: dict[str, Any],
                  rows: list[dict[str, Any]], observations: dict[tuple[str, str, int], dict[str, Any]],
                  baseline: list[dict[str, Any]], baseline_ok: bool) -> int:
    write_csv(output / "deadline_calibration_values.csv", deadline_calibration_rows(scenarios["M_RING"]))
    write_csv(output / "flow_deadline_transform.csv", transforms)
    write_csv(output / "derived_scenario_integrity.csv", integrity)
    all_integrity = all(bool(row["integrity_pass"]) for row in integrity)
    boundary = [row for scenario_id, parent in scenarios.items() for alpha in ALPHAS
                for row in cycle_boundary_rows(parent, scenario_id, alpha, cycle_ok)]
    write_csv(output / "deadline_cycle_boundary_audit.csv", boundary)
    write_csv(output / "baseline_parity.csv", baseline)
    write_csv(output / "p0_deadline_calibration.csv", rows)
    repeats, repeats_ok, nondeterministic = repeatability_rows(rows)
    write_csv(output / "repeatability.csv", repeats)
    summaries = alpha_summary(rows, nondeterministic)
    write_csv(output / "alpha_summary.csv", summaries)
    if rows and mode == "full":
        write_csv(output / "hnf_alpha_trajectory.csv", hnf_trajectory(rows, observations))
    elif mode == "quick":
        write_csv(output / "hnf_alpha_trajectory.csv", [])
    selected = selected_alpha(summaries) if (mode == "full" and cycle_ok and all_integrity and baseline_ok and repeats_ok) else None
    if mode == "quick":
        verdict_name = "INCONCLUSIVE"
    elif not all_integrity:
        verdict_name = "DERIVED_SCENARIO_INTEGRITY_FAILED"
    elif not cycle_ok:
        verdict_name = "CALIBRATION_CYCLE_SEMANTICS_UNQUALIFIED"
    elif not baseline_ok:
        verdict_name = "BASELINE_PARITY_FAILED"
    elif not repeats_ok:
        verdict_name = "CALIBRATION_NONDETERMINISTIC"
    elif selected:
        verdict_name = "CALIBRATED_WORKLOAD_QUALIFIED"
    elif rows:
        verdict_name = "NO_PF_ELIGIBLE_ALPHA_IN_PREREGISTERED_LADDER"
    else:
        verdict_name = "INCONCLUSIVE"
    assert verdict_name in VALID_VERDICTS
    selected_shas = {sid: sha256_file(output / "derived_scenarios" / f"{sid}_{selected.tag}.json")
                     for sid in ORDER} if selected else {}
    verdict = {"formal_verdict": verdict_name, "selected_alpha": selected.label if selected else None,
               "complete_scenarios_by_alpha": {row["alpha"]: row["complete_scenario_count"] for row in summaries},
               "baseline_parity": baseline_ok, "cycle_semantics_qualified": cycle_ok,
               "scenario_integrity_passed": all_integrity, "repeatability_passed": repeats_ok,
               "selected_scenario_shas": selected_shas,
               "next_stage_recommendation": "REALISTIC_PF_COST_WITH_CALIBRATED_WORKLOAD" if selected else "MANUAL_CALIBRATION_REVIEW_REQUIRED"}
    write_json(output / "calibration_verdict.json", verdict)
    if selected:
        write_json(output / "calibrated_workload_manifest.json", {"selected_alpha": selected.label,
                   "calibrated_scenario_sha256": selected_shas,
                   "parent_scenario_sha256": {sid: sha256_file(SOURCE / "scenarios" / f"{sid}.json") for sid in ORDER},
                   "deadline_transformation_rule": "D'(f,alpha)=min(alpha*D_original(f),T(f)); exact Fraction/integer-ns; 100-ns alignment",
                   "backend_config": backend_config(), "verification_status": "P0_COMPLETE for six scenarios",
                   "repeatability_status": "PASS"})
    summary_lines = ["# exp18e: Deadline feasibility calibration", "",
                     "The original literature-grounded, role-based synthetic industrial workload remains frozen. Its tight D/T=0.75–0.80 timing configuration was not P0-complete under the fixed qualified heuristic backend; this does not prove infeasibility or identify deadline tightness as the unique cause.", "",
                     "To make a legitimate healthy-P0 input for a future PF-cost study, this separate experiment used only the pre-registered uniform rule `D'=min(alpha·D,T)` over α={1, 11/10, 6/5, 5/4, 4/3}. Exact Fraction/integer-ns arithmetic was used; periods, flow population, roles, source/destination, payloads, release offsets, topology, routing, K, seed and backend were unchanged. Periods were not relaxed because that would change packet-instance counts and PF synthesis complexity.", "",
                     f"Cycle-boundary qualification: **{'PASS' if cycle_ok else 'FAIL'}**. It tests D=T with nonzero release and a final absolute deadline beyond the cycle; no deadline clipping, release change, PF run, OMNeT++ run, INET run, or figure generation occurred.", "",
                     f"Baseline α=1 exact parity: **{'PASS' if baseline_ok else 'FAIL'}**. Formal verdict: **`{verdict_name}`**.", "",
                     "| alpha | complete scenarios | min scheduled ratio |", "|---:|---:|---:|"]
    summary_lines += [f"| {row['alpha']} | {row['complete_scenario_count']}/6 | {row['minimum_scheduled_ratio']:.6f} |" for row in summaries]
    summary_lines += ["", "The deadline-calibrated workload, if qualified, is a derivative of the literature-grounded synthetic workload—not a real factory trace and not evidence that the original workload was infeasible. Heuristic output need not be monotonic even though deadline relaxation does not shrink the mathematical feasible set.", "",
                      "No PF, Profile Store, parallel campaign, or OMNeT simulation is started here; advancing to a realistic PF-cost experiment requires manual confirmation."]
    (output / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    artifacts = {str(path.relative_to(output)): sha256_file(path) for path in output.rglob("*")
                 if path.is_file() and path.name != "analysis_manifest.json"}
    write_json(output / "analysis_manifest.json", {"experiment": "exp18e_deadline_feasibility_calibration", "mode": mode,
               "parent_commit": parent_commit, "implementation_commit": implementation_commit,
               "results_commit": "RECORDED_BY_SUBSEQUENT_GIT_HISTORY", "historical_tree_sha256": frozen,
               "exp18_original_scenario_sha256": {sid: sha256_file(SOURCE / "scenarios" / f"{sid}.json") for sid in ORDER},
               "exp18b_verdict_sha256": sha256_file(ROOT / "results/p0_hnf_diagnosis/mechanism_verdict.md"),
               "exp18c_verdict_sha256": sha256_file(ROOT / "results/candidate_k_sensitivity/research_direction_assessment.json"),
               "exp18d_verdict_sha256": sha256_file(ROOT / "results/h2s_multistart_sensitivity/verdict.json"),
               "alpha_ladder": [{"tag": alpha.tag, "numerator": alpha.numerator, "denominator": alpha.denominator} for alpha in ALPHAS],
               "deadline_transform_rule": "D'(f,alpha)=min(alpha*D_original(f),T(f))", "selected_alpha": selected.label if selected else None,
               "derived_scenario_sha256": {str(path.relative_to(output)): sha256_file(path) for path in sorted((output / "derived_scenarios").glob("*.json"))},
               "backend_config": backend_config(), "advanced_flow_scheduler_commit": shell(["git", "-C", str(ROOT / ".external/AdvancedFlowScheduler"), "rev-parse", "HEAD"]),
               "patch_provenance": "exp18d tie-break patch present in source tree but formal exp18e invokes BASELINE mode only",
               "artifact_sha256": artifacts})
    assert_frozen_tree()
    if mode == "quick":
        return 0 if cycle_ok and all_integrity and baseline_ok else 2
    return 0 if verdict_name in {"CALIBRATED_WORKLOAD_QUALIFIED", "NO_PF_ELIGIBLE_ALPHA_IN_PREREGISTERED_LADDER"} else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="run cycle qualification plus M_RING A100/A125/A133 once")
    parser.add_argument("--implementation-commit", default=None)
    args = parser.parse_args()
    mode = "quick" if args.quick else "full"
    if not EXECUTABLE.is_file():
        raise SystemExit(f"missing H2S executable: {EXECUTABLE}")
    if mode == "full" and not git_clean():
        raise SystemExit("full campaign requires a clean implementation commit before any result is created")
    output = result_output(mode)
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing evidence: {output}")
    parent_commit = shell(["git", "rev-parse", "HEAD"])
    implementation_commit = args.implementation_commit or parent_commit
    frozen = assert_frozen_tree()
    config = backend_config(); assert_backend_config(config)
    scenarios, transforms, integrity, _ = materialize_derived_scenarios(output)
    write_json(output / "environment.json", {"platform": platform.platform(), "python": platform.python_version(),
               "os_uname": list(os.uname()), "executable": str(EXECUTABLE), "executable_sha256": sha256_file(EXECUTABLE),
               "advanced_flow_scheduler_commit": shell(["git", "-C", str(ROOT / ".external/AdvancedFlowScheduler"), "rev-parse", "HEAD"]),
               "backend_config": config, "mode": mode})
    write_json(output / "source_scenario_manifest.json", source_manifest(scenarios, frozen))
    if not all(row["integrity_pass"] for row in integrity):
        return final_reports(output, mode=mode, parent_commit=parent_commit, implementation_commit=implementation_commit,
                             frozen=frozen, scenarios=scenarios, transforms=transforms, integrity=integrity, cycle_ok=False,
                             qualification={"not_run": "derived integrity failed"}, rows=[], observations={}, baseline=[], baseline_ok=False)
    qualification, cycle_ok = cycle_qualification(output)
    if not cycle_ok:
        return final_reports(output, mode=mode, parent_commit=parent_commit, implementation_commit=implementation_commit,
                             frozen=frozen, scenarios=scenarios, transforms=transforms, integrity=integrity, cycle_ok=False,
                             qualification=qualification, rows=[], observations={}, baseline=[], baseline_ok=False)
    planned = [("M_RING", alpha, 1) for alpha in (ALPHAS[0], ALPHAS[3], ALPHAS[4])] if mode == "quick" else [
        (scenario_id, alpha, repeat) for alpha in ALPHAS for scenario_id in ORDER for repeat in (1, 2)]
    rows: list[dict[str, Any]] = []
    observations: dict[tuple[str, str, int], dict[str, Any]] = {}
    for scenario_id, alpha, repeat in planned:
        scenario = load_derived(output, scenario_id, alpha)
        path = output / "derived_scenarios" / f"{scenario_id}_{alpha.tag}.json"
        observed = observe_result(scenario, run_backend(path, output / "raw_backend_output" / scenario_id / alpha.tag / f"R{repeat}"))
        observations[(scenario_id, alpha.tag, repeat)] = observed
        rows.append(p0_row(scenario_id, alpha, repeat, scenario, observed))
        # α=1 must be proven before any α>1 formal run.  This branch executes
        # after the six A100 repeats in the full alpha-major matrix.
        if mode == "full" and scenario_id == ORDER[-1] and alpha == ALPHAS[0] and repeat == 2:
            baseline, baseline_ok = baseline_parity(rows, observations)
            if not baseline_ok:
                return final_reports(output, mode=mode, parent_commit=parent_commit, implementation_commit=implementation_commit,
                                     frozen=frozen, scenarios=scenarios, transforms=transforms, integrity=integrity, cycle_ok=True,
                                     qualification=qualification, rows=rows, observations=observations, baseline=baseline, baseline_ok=False)
    if mode == "quick":
        # Quick uses one repeat only, but H2S fails on frozen M_RING and thus
        # verifies both the primary attempt and the production CELF fallback.
        quick_observed = observations[("M_RING", "A100", 1)]
        baseline = []
        for algorithm in ("H2S", "CELF"):
            source = frozen_signature("M_RING", algorithm)
            entry = quick_observed[algorithm.lower()]
            signature = entry["signature"] if entry else None
            baseline.append({"scenario": "M_RING", "repeat": 1, "algorithm": algorithm,
                             "attempted": entry is not None, "source_scheduled_count": source["scheduled_flow_count"],
                             "observed_scheduled_count": signature["scheduled_flow_count"] if signature else -1,
                             "source_HNF_set_sha": source["hnf_set_sha256"],
                             "observed_HNF_set_sha": signature["hnf_set_sha256"] if signature else "",
                             "source_instance_completion_sha": source["instance_completion_sha256"],
                             "observed_instance_completion_sha": signature["instance_completion_sha256"] if signature else "",
                             "parity_pass": bool(signature and signature["scheduled_flow_count"] == source["scheduled_flow_count"] and signature["hnf_set_sha256"] == source["hnf_set_sha256"] and signature["instance_completion_sha256"] == source["instance_completion_sha256"] )})
        baseline_ok = all(row["parity_pass"] for row in baseline)
    else:
        baseline, baseline_ok = baseline_parity(rows, observations)
    return final_reports(output, mode=mode, parent_commit=parent_commit, implementation_commit=implementation_commit,
                         frozen=frozen, scenarios=scenarios, transforms=transforms, integrity=integrity, cycle_ok=cycle_ok,
                         qualification=qualification, rows=rows, observations=observations, baseline=baseline, baseline_ok=baseline_ok)


if __name__ == "__main__":
    raise SystemExit(main())
