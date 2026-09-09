"""Runner for exp18i: static exact fixed-release source-egress collision audit.

Phase A reads frozen topology/timing inputs and qualifies the model.  Phase B
constructs graphs without importing historical scheduling results.  Only Phase
C joins those already-frozen graphs to historical HNF observations.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import platform
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.fixed_release_collision_audit import (
    DENSITIES, HYPERCYCLE_TICKS, ORDER, ROOT, analyze_scenario, canonical_sha256,
    formal_scenario_refs, graph_sha256, is_independent, is_vertex_cover, load_scenario,
    scenario_ref, sha256_file, source_egress_audit, timing_audit, tree_sha256,
)
from tools.h2s_jrs_backend import parse_backend_output
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.run_p0_hnf_mechanism_diagnosis import signatures

OUT = ROOT / "results" / "fixed_release_collision_audit"
DENSITY_RESULTS = ROOT / "results" / "tt_workload_density_calibration"
DIAGNOSTIC_RESULTS = ROOT / "results" / "h2s_admission_placement_diagnostic"
FROZEN_ROOTS = (
    "results/realistic_tsn_pf_cost", "results/p0_hnf_diagnosis", "results/candidate_k_sensitivity",
    "results/h2s_multistart_sensitivity", "results/deadline_feasibility_calibration",
    "results/h2s_primary_policy_sensitivity", "results/tt_workload_density_calibration",
    "results/h2s_admission_placement_diagnostic",
)
VERDICTS = {
    "FIXED_RELEASE_COLLISION_EXACTLY_EXPLAINS_ADMISSION_GAP",
    "FIXED_RELEASE_COLLISION_PARTIALLY_EXPLAINS_ADMISSION_GAP",
    "FIXED_RELEASE_COLLISION_PRESENT_BUT_CARDINALITY_MISMATCH",
    "NO_UNAVOIDABLE_FIXED_RELEASE_COLLISION_FOUND", "FIRST_HOP_UNAVOIDABILITY_UNQUALIFIED",
    "TIMING_MODEL_PARITY_FAILED", "STATIC_COLLISION_CONTRADICTS_VERIFIED_SCHEDULE",
    "MVC_EXACT_UNAVAILABLE_FOR_REQUIRED_CASES", "INPUT_PARITY_FAILED", "INCONCLUSIVE",
}
EXPECTED_DEFICIT = {
    "M": [4, 3, 2, 2, 1, 0, 0, 0],
    "L": [14, 13, 13, 12, 9, 8, 6, 3],
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


def write_csv_gz(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    values = list(rows); names = fields or sorted({key for row in values for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename=path.name, mode="wb", fileobj=raw, mtime=0) as zipped:
            with __import__("io").TextIOWrapper(zipped, encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, names, extrasaction="ignore", lineterminator="\n")
                writer.writeheader(); writer.writerows(values)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, check=True, capture_output=True).stdout.strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def frozen_preflight() -> dict[str, str]:
    return {name: tree_sha256(ROOT / name) for name in FROZEN_ROOTS}


def assert_frozen(expected: dict[str, str]) -> None:
    actual = frozen_preflight()
    if actual != expected:
        changed = {key: {"before": expected[key], "after": actual[key]} for key in expected if expected[key] != actual[key]}
        raise RuntimeError(f"FROZEN_HISTORICAL_ARTIFACT_CHANGED:{changed}")


def release_generator_audit() -> tuple[dict[str, Any], str]:
    path = ROOT / "tools" / "generate_realistic_tsn_scenarios.py"
    if not path.is_file():
        return {"status": "GENERATOR_SOURCE_NOT_LOCATED"}, "# Release-generator source audit\n\nGENERATOR_SOURCE_NOT_LOCATED.\n"
    source = path.read_text(encoding="utf-8")
    required = ("def stable_release", "hashlib.sha256", "flow_id:{SEED}", "ticks = period_ns // 500", "* 100")
    located = all(token in source for token in required)
    audit = {
        "status": "PASS" if located else "GENERATOR_SOURCE_INCOMPLETE", "generator_path": str(path.relative_to(ROOT)),
        "generator_sha256": sha256_file(path), "formal_seed": 1024, "function": "stable_release(flow_id, period_ns)",
        "hash_input": "f'{flow_id}:{SEED}'", "release_range": "[0, 20% period] inclusive at 100 ns alignment",
        "source_egress_coordination": False, "same_source_overlap_detection": False,
        "collision_avoidance": False, "per_flow_timing_only": True,
    }
    markdown = "\n".join((
        "# Release-generator source audit", "",
        "The frozen exp18 generator was located at `tools/generate_realistic_tsn_scenarios.py`.", "",
        "- `stable_release(flow_id, period_ns)` hashes `f\"{flow_id}:{SEED}\"` with SHA-256; the formal seed is `1024`.",
        "- It computes `period_ns // 500` selectable positions and multiplies by 100 ns, giving a 100 ns-aligned release in the inclusive range `[0, 20% period]`.",
        "- The input contains only flow ID, period and seed: it has no source/egress state, no aggregate same-source launch test, and no collision-avoidance step.",
        "- Thus the generator makes each flow individually representable but does not guarantee aggregate launch feasibility at a shared source egress.", "",
    ))
    return audit, markdown


def phase_a(refs: list[Any]) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read frozen scenarios and qualify source/adapter timing before graph construction."""
    records: dict[tuple[str, str], dict[str, Any]] = {}; egress_rows: list[dict[str, Any]] = []; timing_rows: list[dict[str, Any]] = []
    for ref in refs:
        scenario = load_scenario(ref); egress, _ = source_egress_audit(ref, scenario); timing, _ = timing_audit(ref, scenario)
        records[(ref.scenario, ref.density)] = {"ref": ref, "scenario": scenario, "egress": egress, "timing": timing}
        egress_rows.extend(egress); timing_rows.extend(timing)
    return records, egress_rows, timing_rows


def exp18h_input_path(scenario: str) -> Path:
    return DIAGNOSTIC_RESULTS / "raw_trace" / scenario / "trace_on_repeat_1" / "backend_input" / "scenario.json"


def timing_parity(records: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare static adapter conversion to existing formal upstream integer JSON."""
    output: list[dict[str, Any]] = []
    formal_cases = (("M_RING", "D070"), ("M_REDSTAR", "D070"), ("M_ROR", "D070"),
                    ("L_RING", "D050"), ("L_REDSTAR", "D050"), ("L_ROR", "D050"))
    cases = tuple(item for item in formal_cases if item in records)
    for scenario, density in cases:
        path = exp18h_input_path(f"{scenario}_{density}")
        if not path.is_file(): raise RuntimeError(f"MISSING_EXP18H_UPSTREAM_INPUT:{path}")
        upstream = json.loads(path.read_text(encoding="utf-8"))["time_steps"][0]["addFlows"]
        static = records[(scenario, density)]["timing"]
        ids = [row["flow_id"] for row in static]
        if len(upstream) != len(ids): raise RuntimeError(f"UPSTREAM_INPUT_FLOW_COUNT_MISMATCH:{scenario}:{density}")
        for upstream_flow in upstream:
            numeric = int(upstream_flow["flowID"])
            if not 0 <= numeric < len(ids): raise RuntimeError("UPSTREAM_FLOW_ID_OUT_OF_RANGE")
            flow_id = ids[numeric]; row = next(item for item in static if item["flow_id"] == flow_id)
            checks = {
                "period": int(upstream_flow["period"]) == int(row["period_ticks"]),
                "release_offset": int(upstream_flow["release offset"]) == int(row["release_ticks"]),
                "relative_deadline": int(upstream_flow["deadline"]) == int(row["deadline_ticks"]),
                "fixed_release": bool(upstream_flow["fixed release"]) is bool(row["fixed_release"]),
                "frame_size": int(upstream_flow["package size"]) == int(row["frame_size"]),
                "transmission_ticks": int(upstream_flow["package size"]) // 125 == int(row["tx_ticks"]),
            }
            output.append({
                "scenario": scenario, "density": density, "flow_id": flow_id, "flow_kind": row["flow_kind"],
                "upstream_input": str(path.relative_to(ROOT)), "period_ticks": row["period_ticks"],
                "release_offset_ticks": row["release_ticks"], "relative_deadline_ticks": row["deadline_ticks"],
                "fixed_release": row["fixed_release"], "frame_size": row["frame_size"],
                "transmission_ticks": row["tx_ticks"], "frames_per_hyper_cycle": row["frames_per_hyper_cycle"],
                **{f"{key}_parity": value for key, value in checks.items()}, "parity_pass": all(checks.values()),
            })
    return output


def h2s_raw_signature(scenario: str, density: str) -> dict[str, Any]:
    ref = scenario_ref(scenario, density)
    stdout = DENSITY_RESULTS / "raw_backend_output" / scenario / density / "R1" / "logs" / "0_h2s_stdout.log.gz"
    if not stdout.is_file(): raise RuntimeError(f"MISSING_FROZEN_H2S_OUTPUT:{stdout}")
    with gzip.open(stdout, "rt", encoding="utf-8") as handle:
        payload = parse_backend_output(handle.read())
    return signatures(load_scenario(ref), payload, "H2S")


def verified_h2s_rows() -> dict[tuple[str, str], dict[str, str]]:
    values = read_csv(DENSITY_RESULTS / "p0_density_results.csv")
    result: dict[tuple[str, str], dict[str, str]] = {}
    for row in values:
        if row["repeat"] == "1": result[(row["scenario"], row["density_tag"])] = row
    return result


def historical_cover_checks(analyses: dict[tuple[str, str], dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    verified = verified_h2s_rows(); rows = []; details: dict[tuple[str, str], dict[str, Any]] = {}
    for key, analysis in sorted(analyses.items()):
        scenario, density = key; signature = h2s_raw_signature(scenario, density)
        hnf = set(signature["hnf_flow_ids"]); scheduled = set(analysis["vertices"]) - hnf; mvc = analysis["mvc"]
        verification = verified.get(key, {})
        # The inherited all-flow checker intentionally reports false whenever
        # an HNF exists, because its first assertion is that *every* requested
        # TT flow was scheduled.  It is therefore not a validity check of the
        # retained subset.  Reuse the frozen upstream verifier and independently
        # certify the saved subset's source-to-destination instance continuity.
        upstream_verifier = verification.get("upstream_verifier", "") == "True"
        scheduled_subset_continuity = all(identity["flow_completion_class"] == "FULLY_SCHEDULED"
                                          for identity in signature["identities"] if identity["flow_id"] in scheduled)
        hnf_has_no_partial_instance = all(identity["flow_completion_class"] == "ZERO_SCHEDULED"
                                           for identity in signature["identities"] if identity["flow_id"] in hnf)
        verifier = upstream_verifier and scheduled_subset_continuity and hnf_has_no_partial_instance
        hnf_cover = is_vertex_cover(analysis["edges"], hnf); independent = is_independent(analysis["edges"], scheduled)
        exact = bool(mvc["mvc_exact"]); mvc_size = int(mvc["mvc_size"]) if exact else None
        cardinality_matched = bool(exact and len(hnf) == mvc_size and hnf_cover and independent and verifier)
        if not analysis["edges"]: status = "NO_COLLISION_EDGE"
        elif not hnf_cover or not independent: status = "STATIC_COLLISION_CONTRADICTION"
        elif exact and len(hnf) == mvc_size: status = "EXACT_MVC_MATCH"
        elif exact and len(hnf) > mvc_size: status = "PARTIAL_LOWER_BOUND_ONLY"
        else: status = "CARDINALITY_MISMATCH"
        row = {
            "scenario": scenario, "scale": analysis["ref"].scale, "topology": analysis["ref"].topology, "density": density,
            "observed_HNF_count": len(hnf), "conflict_edge_count": len(analysis["edges"]),
            "HNF_is_vertex_cover": hnf_cover, "scheduled_set_is_independent": independent,
            "upstream_verifier_passed": upstream_verifier, "scheduled_subset_continuity_checker": scheduled_subset_continuity,
            "hnf_has_no_partial_instance": hnf_has_no_partial_instance,
            "historical_all_flow_static_checker": verification.get("static_checker", ""), "verified_historical_schedule": verifier,
            "mvc_exact": exact, "mvc_size": mvc_size if exact else "",
            "HNF_minus_MVC": len(hnf) - mvc_size if mvc_size is not None else "", "cardinality_bound_matched": cardinality_matched,
            "full_set_infeasible_by_collision": bool(analysis["edges"]), "mechanism_explanation_status": status,
            "observed_HNF_flow_ids": ";".join(sorted(hnf)),
        }
        rows.append(row); details[key] = {"hnf": hnf, "scheduled": scheduled, "row": row}
    return rows, details


def cross_topology_graph_rows(analyses: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for scale in ("M", "L"):
        for density in [item.tag for item in DENSITIES]:
            values = [analyses[(f"{scale}_{topology}", density)]["graph_summary"] for topology in ("RING", "REDSTAR", "ROR")]
            rows.append({"scale": scale, "density": density, "graph_sha256_ring": values[0]["graph_sha256"],
                         "graph_sha256_redstar": values[1]["graph_sha256"], "graph_sha256_ror": values[2]["graph_sha256"],
                         "three_topology_graph_equal": len({row["graph_sha256"] for row in values}) == 1})
    return rows


def density_curve(analyses: dict[tuple[str, str], dict[str, Any]], checks: dict[tuple[str, str], dict[str, Any]], graph_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []; graph_equal = {(row["scale"], row["density"]): bool(row["three_topology_graph_equal"]) for row in graph_rows}
    for scale in ("M", "L"):
        for density in [item.tag for item in DENSITIES]:
            representative = analyses[(f"{scale}_RING", density)]; hnf_counts = [checks[(f"{scale}_{topology}", density)]["row"]["observed_HNF_count"] for topology in ("RING", "REDSTAR", "ROR")]
            mvc = representative["mvc"]
            result.append({"scale": scale, "density": density, "retained_flow_count": len(representative["vertices"]),
                           "observed_HNF_count": hnf_counts[0], "collision_edge_count": len(representative["edges"]),
                           "conflicting_flow_count": representative["graph_summary"]["conflicting_flow_count"],
                           "mvc_size": mvc["mvc_size"] if mvc["mvc_exact"] else "", "observed_minus_mvc": (hnf_counts[0] - int(mvc["mvc_size"])) if mvc["mvc_exact"] else "",
                           "three_topology_graph_equal": graph_equal[(scale, density)], "three_topology_HNF_count_equal": len(set(hnf_counts)) == 1})
    return result


def historical_variants(analyses: dict[tuple[str, str], dict[str, Any]], checks: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    variants: list[tuple[str, str, str, set[str]]] = []
    for scenario in ORDER:
        variants.append((scenario, "baseline", "H2S_FORMAL_D100", set(checks[(scenario, "D100")]["hnf"])))
    grouped: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in read_csv(ROOT / "results" / "h2s_multistart_sensitivity" / "hnf_set_by_seed.csv"):
        grouped[(row["scenario"], "exp18d", f"{row['mode']}:{row['tie_break_seed']}")].add(row["flow_id"])
    for row in read_csv(ROOT / "results" / "h2s_primary_policy_sensitivity" / "hnf_by_policy.csv"):
        grouped[(row["scenario"], "exp18f", row["policy_tag"])].add(row["flow_id"])
    variants.extend((scenario, source, mode, values) for (scenario, source, mode), values in grouped.items())
    rows = []
    for scenario, source, mode, hnf in sorted(variants):
        analysis = analyses[(scenario, "D100")]; mvc = analysis["mvc"]; cover = is_vertex_cover(analysis["edges"], hnf)
        exact = bool(mvc["mvc_exact"]); minimum = bool(exact and cover and len(hnf) == int(mvc["mvc_size"]))
        rows.append({"scenario": scenario, "source_experiment": source, "mode_or_policy": mode,
                     "HNF_count": len(hnf), "HNF_set_sha": canonical_sha256(sorted(hnf)), "is_vertex_cover": cover,
                     "is_minimum_vertex_cover": minimum if exact else "", "cover_excess": len(hnf) - int(mvc["mvc_size"]) if exact else ""})
    return rows


def deadline_invariance() -> dict[str, Any]:
    fields = ("period_s", "release_offset_s", "packet_size_bytes")
    rows = []; passed = True
    for scenario in ORDER:
        original = {flow["id"]: flow for flow in load_scenario(scenario_ref(scenario, "D100"))["tt_flows"]}
        for alpha in ("A100", "A110", "A120", "A125", "A133"):
            path = ROOT / "results" / "deadline_feasibility_calibration" / "derived_scenarios" / f"{scenario}_{alpha}.json"
            child = {flow["id"]: flow for flow in json.loads(path.read_text(encoding="utf-8"))["tt_flows"]}
            same_ids = set(original) == set(child); same_fields = same_ids and all(original[key][field] == child[key][field] for key in original for field in fields)
            passed &= same_fields
            rows.append({"scenario": scenario, "alpha": alpha, "path": str(path.relative_to(ROOT)), "flow_id_set_invariant": same_ids,
                         "period_release_frame_invariant": same_fields, "collision_graph_theoretically_invariant": same_fields})
    return {"status": "PASS" if passed else "INPUT_PARITY_FAILED", "fields": list(fields), "records": rows,
            "statement": "Exact source-release collision inputs do not use deadline; unchanged period/release/frame inputs imply an unchanged static collision graph."}


def exp18h_crosscheck(analyses: dict[tuple[str, str], dict[str, Any]], checks: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    failures = read_csv(DIAGNOSTIC_RESULTS / "hnf_flow_summary.csv")
    admission = read_csv(DIAGNOSTIC_RESULTS / "flow_admission_trace.csv")
    snapshots = [json.loads(line) for line in (DIAGNOSTIC_RESULTS / "first_hnf_snapshot.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    observed_starts = {(row["scenario"], row["flow_id"]): row.get("observed_first_start_ticks", "") for row in snapshots}
    rows = []
    for item in failures:
        scenario = item["scenario"]
        if not scenario.endswith("_D050"): continue
        base = scenario.removesuffix("_D050"); analysis = analyses[(base, "D050")]; flow_id = item["flow_id"]
        peers = sorted({right if left == flow_id else left for left, right in analysis["edges"] if flow_id in (left, right)})
        event_rows = [event for event in analysis["events"] if flow_id in (event["flow_a"], event["flow_b"])]
        flow_rows = [row for row in admission if row["scenario"] == scenario and row["run_tag"] == "trace_on_repeat_1"]
        rejected = next((row for row in flow_rows if row["flow_id"] == flow_id and row["accepted"] == "False"), {})
        position = int(rejected.get("sequence_index", -1)); accepted_before = {row["flow_id"] for row in flow_rows if row["accepted"] == "True" and int(row["sequence_index"]) < position}
        static_link = next((flow.source_egress_link for flow in analysis["flows"] if flow.flow_id == flow_id), "")
        examples = sorted(event_rows, key=lambda row: (row["instance_a"], row["instance_b"], row["start_a"], row["start_b"]))
        rows.append({"scenario": scenario, "flow_id": flow_id, "exp18h_failure_link": item["dominant_failure_link"],
                     "static_first_egress": static_link, "match": static_link == item["dominant_failure_link"],
                     "required_release_tick": next((flow.release_ticks for flow in analysis["flows"] if flow.flow_id == flow_id), ""),
                     "observed_later_first_start": observed_starts.get((scenario, flow_id), ""), "static_conflicting_peer_flows": ";".join(peers),
                     "static_collision_instances": ";".join(f"{row['instance_a']}:{row['instance_b']}" for row in examples[:8]),
                     "has_static_peer": bool(peers), "peer_accepted_before_hnf": bool(set(peers) & accepted_before),
                     "accepted_peer_flows": ";".join(sorted(set(peers) & accepted_before))})
    return rows


def verdict_for(egress_rows: list[dict[str, Any]], parity_rows: list[dict[str, Any]], check_rows: list[dict[str, Any]], curve: list[dict[str, Any]], trace_rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    egress_ok = all(bool(row["qualification_pass"]) for row in egress_rows)
    timing_ok = bool(parity_rows) and all(bool(row["parity_pass"]) for row in parity_rows)
    contradiction = any(not bool(row["scheduled_set_is_independent"]) or not bool(row["HNF_is_vertex_cover"]) for row in check_rows)
    exact_ok = all(bool(row["mvc_exact"]) for row in check_rows)
    collision_present = any(int(row["conflict_edge_count"]) > 0 for row in check_rows)
    trace_ok = bool(trace_rows) and all(bool(row["match"]) and bool(row["has_static_peer"]) and bool(row["peer_accepted_before_hnf"]) for row in trace_rows)
    curves = {scale: [row for row in curve if row["scale"] == scale] for scale in ("M", "L")}
    curve_match = all([row["observed_HNF_count"] == expected and row["mvc_size"] == expected and row["three_topology_graph_equal"] and row["three_topology_HNF_count_equal"] for scale, expected_values in EXPECTED_DEFICIT.items() for row, expected in zip(curves[scale], expected_values)])
    all_cover = all(bool(row["HNF_is_vertex_cover"]) and bool(row["scheduled_set_is_independent"]) for row in check_rows)
    all_cardinality = all(bool(row["cardinality_bound_matched"]) for row in check_rows)
    if not egress_ok: verdict = "FIRST_HOP_UNAVOIDABILITY_UNQUALIFIED"
    elif not timing_ok: verdict = "TIMING_MODEL_PARITY_FAILED"
    elif contradiction: verdict = "STATIC_COLLISION_CONTRADICTS_VERIFIED_SCHEDULE"
    elif not exact_ok: verdict = "MVC_EXACT_UNAVAILABLE_FOR_REQUIRED_CASES"
    elif not collision_present: verdict = "NO_UNAVOIDABLE_FIXED_RELEASE_COLLISION_FOUND"
    elif curve_match and trace_ok and all_cover and all_cardinality: verdict = "FIXED_RELEASE_COLLISION_EXACTLY_EXPLAINS_ADMISSION_GAP"
    elif any(row["mechanism_explanation_status"] == "PARTIAL_LOWER_BOUND_ONLY" for row in check_rows): verdict = "FIXED_RELEASE_COLLISION_PARTIALLY_EXPLAINS_ADMISSION_GAP"
    else: verdict = "FIXED_RELEASE_COLLISION_PRESENT_BUT_CARDINALITY_MISMATCH"
    if verdict not in VERDICTS: raise RuntimeError("INVALID_VERDICT")
    by_scale = {}
    for scale in ("M", "L"):
        d100 = [row for row in check_rows if row["scale"] == scale and row["density"] == "D100"]
        by_scale[scale] = {"infeasible": egress_ok and timing_ok and any(bool(row["full_set_infeasible_by_collision"]) for row in d100),
                           "optimal": bool(d100) and all(bool(row["cardinality_bound_matched"]) for row in d100)}
    return verdict, {"source_egress_qualification_pass": egress_ok, "timing_parity_pass": timing_ok,
                     "trace_crosscheck_pass": trace_ok, "density_curve_exact_match": curve_match,
                     "full_density_fixed_release_infeasible_proven_M": by_scale["M"]["infeasible"],
                     "full_density_fixed_release_infeasible_proven_L": by_scale["L"]["infeasible"],
                     "observed_admission_cardinality_optimal_M": by_scale["M"]["optimal"],
                     "observed_admission_cardinality_optimal_L": by_scale["L"]["optimal"]}


def summary_markdown(verdict: str, flags: dict[str, Any], graph_rows: list[dict[str, Any]], curve: list[dict[str, Any]], trace_rows: list[dict[str, Any]], release: dict[str, Any]) -> str:
    d100 = {row["scale"]: row for row in curve if row["density"] == "D100"}
    curve_text = "; ".join(f"{scale}: " + ", ".join(f"{row['density']}={row['mvc_size']}" for row in curve if row["scale"] == scale) for scale in ("M", "L"))
    trace_text = "; ".join(f"{row['flow_id']} -> {row['static_conflicting_peer_flows']}" for row in trace_rows)
    recommendation = ("REDESIGN_DETERMINISTIC_SOURCE_EGRESS_RELEASE_ASSIGNMENT" if verdict == "FIXED_RELEASE_COLLISION_EXACTLY_EXPLAINS_ADMISSION_GAP" else
                      "TARGETED_FIXED_RELEASE_AND_PLACEMENT_DIAGNOSIS" if verdict == "FIXED_RELEASE_COLLISION_PARTIALLY_EXPLAINS_ADMISSION_GAP" else
                      "BACKEND_MODEL_SEMANTIC_REVIEW")
    return "\n".join((
        "# exp18i: Static exact fixed-release source-egress collision audit", "",
        "## Result", "",
        f"Verdict: `{verdict}`. Next-stage recommendation: `{recommendation}`.", "",
        "## Evidence and interpretation", "",
        "1. exp18h observed first-hop `FIXED_RELEASE_START_MISMATCH` rejections, so this stage evaluates their necessary structural condition without rerunning a scheduler.",
        "2. In qualified ASAP semantics, `fixed_release=true` requires first-hop start `== release_offset + k*period`; it is not merely `>= release`.",
        f"3. Source-egress qualification passed for all analyzed frozen flows: `{flags['source_egress_qualification_pass']}`. Qualification uses a unique physical source data-plane egress, not an observed route choice.",
        f"4. Python conversion exactly matches every checked exp18h upstream integer input field and transmission duration: `{flags['timing_parity_pass']}`. The analysis uses 100 ns integer ticks and an 8 ms hypercycle.",
        f"5. Original D100 collision edges: Medium `{d100['M']['collision_edge_count']}`, Large `{d100['L']['collision_edge_count']}`. Graph equality across all three topologies is recorded per density in `conflict_graph_summary.csv`.",
        f"6. MVC curve: {curve_text}.",
        f"7. The three exp18h Large D050 HNF crosschecks are: {trace_text}. Their static first egress matches the traced failure link and each has an earlier accepted collision peer: `{flags['trace_crosscheck_pass']}`.",
        "8. Every historical H2S HNF set is checked as a vertex cover and every verified scheduled set as an independent set; those gates prevent the static model from contradicting a verified schedule.",
        f"9. Full original workloads are proven infeasible only under the current exact fixed-release source-egress semantics: M `{flags['full_density_fixed_release_infeasible_proven_M']}`, L `{flags['full_density_fixed_release_infeasible_proven_L']}`. Corresponding admission cardinality-optimality flags are M `{flags['observed_admission_cardinality_optimal_M']}`, L `{flags['observed_admission_cardinality_optimal_L']}`.",
        f"10. The located generator audit status is `{release['status']}`: releases are deterministic per `(flow_id, seed, period)` and are neither source-aware nor collision-avoiding.",
        "11. Deadline relaxation cannot remove a mandatory exact-start source collision when period, release and frame size are invariant; `deadline_collision_invariance.json` verifies those frozen input fields.",
        "12. K and downstream topology alternatives cannot remove a collision after a proven unavoidable source egress. Identical cross-topology graphs explain stable cardinality; alternative constructive orders can select different minimum vertex covers and thus change HNF identity.",
        "", "This is a property of the current synthetic release assignment and exact-launch constraint, not evidence that the corresponding industrial traffic roles are inherently unschedulable. No H2S, CELF, PF, OMNeT++, or INET run was performed.", "",
    ))


def run(*, quick: bool, implementation_commit: str | None) -> int:
    output = OUT / "quick_validation" if quick else OUT
    if output.exists(): raise RuntimeError(f"REFUSE_TO_OVERWRITE_EVIDENCE:{output}")
    if not quick and git("status", "--porcelain"): raise RuntimeError("FULL_AUDIT_REQUIRES_CLEAN_IMPLEMENTATION_COMMIT")
    parent = git("rev-parse", "HEAD"); origin = git("rev-parse", "origin/main"); frozen = frozen_preflight(); refs = formal_scenario_refs(quick=quick)
    records, egress_rows, timing_rows = phase_a(refs)
    parity = timing_parity(records) if not quick else timing_parity({key: value for key, value in records.items() if key in {("M_RING", "D070"), ("L_RING", "D050")}})
    if not all(bool(row["qualification_pass"]) for row in egress_rows): raise RuntimeError("FIRST_HOP_UNAVOIDABILITY_UNQUALIFIED")
    if not parity or not all(bool(row["parity_pass"]) for row in parity): raise RuntimeError("TIMING_MODEL_PARITY_FAILED")
    release, release_md = release_generator_audit(); output.mkdir(parents=True)
    write_json(output / "environment.json", {"platform": platform.platform(), "python": platform.python_version(), "os_uname": list(os.uname()), "mode": "quick" if quick else "full", "parent_commit": parent, "origin_main": origin, "quantum_ns": 100, "hypercycle_ticks": HYPERCYCLE_TICKS, "scheduler_runs": 0})
    write_json(output / "source_manifest.json", {"frozen_source_only": True, "scenario_sha256": {f"{ref.scenario}_{ref.density}": {"path": str(ref.path.relative_to(ROOT)), "sha256": sha256_file(ref.path)} for ref in refs}, "frozen_tree_sha256": frozen})
    write_text(output / "release_generator_audit.md", release_md); write_csv(output / "source_egress_audit.csv", egress_rows); write_csv(output / "timing_parity.csv", parity)
    write_text(output / "timing_semantics_audit.md", "# Timing semantics audit\n\nThe static detector calls the qualified `quantize_flow` adapter conversion. It uses the adapter's one-time frame overhead, `ceil` transmission conversion, integer 100 ns ticks, quantized release offset, relative deadline, and `fixed release=true` input. It then validates those fields against frozen exp18h upstream JSON rather than reconstructing them from floating-point seconds.\n")
    if quick:
        analyses = {(ref.scenario, ref.density): analyze_scenario(ref, records[(ref.scenario, ref.density)]["scenario"]) for ref in refs}
        events = [row for analysis in analyses.values() for row in analysis["events"]]; components = [row for analysis in analyses.values() for row in analysis["components"]]
        write_csv(output / "fixed_release_instance_summary.csv", [row for analysis in analyses.values() for row in analysis["instance_rows"]]); write_csv_gz(output / "exact_release_collision_events.csv.gz", events)
        write_csv(output / "logical_flow_conflicts.csv", [row for analysis in analyses.values() for row in analysis["conflicts"]]); write_csv(output / "conflict_graph_summary.csv", [analysis["graph_summary"] for analysis in analyses.values()]); write_csv(output / "conflict_components.csv", components); write_csv(output / "minimum_vertex_cover.csv", [analysis["mvc"] for analysis in analyses.values()])
        write_json(output / "quick_verdict.json", {"source_egress_qualification_pass": True, "timing_parity_pass": True, "collision_graph_built": True, "mvc_exact": all(analysis["mvc"]["mvc_exact"] for analysis in analyses.values()), "scheduler_runs": 0})
        assert_frozen(frozen); return 0
    analyses = {(ref.scenario, ref.density): analyze_scenario(ref, records[(ref.scenario, ref.density)]["scenario"]) for ref in refs}
    all_events = [row for analysis in analyses.values() for row in analysis["events"]]; all_conflicts = [row for analysis in analyses.values() for row in analysis["conflicts"]]
    graph_summary = [analysis["graph_summary"] for analysis in analyses.values()]; components = [row for analysis in analyses.values() for row in analysis["components"]]; mvc_rows = [analysis["mvc"] for analysis in analyses.values()]
    checks, check_details = historical_cover_checks(analyses); graph_rows = cross_topology_graph_rows(analyses); curve = density_curve(analyses, check_details, graph_rows); variants = historical_variants(analyses, check_details); trace_rows = exp18h_crosscheck(analyses, check_details); deadline = deadline_invariance()
    verdict, flags = verdict_for(egress_rows, parity, checks, curve, trace_rows)
    write_csv(output / "fixed_release_instance_summary.csv", [row for analysis in analyses.values() for row in analysis["instance_rows"]])
    write_csv_gz(output / "exact_release_collision_events.csv.gz", all_events); write_csv(output / "logical_flow_conflicts.csv", all_conflicts)
    write_csv(output / "conflict_graph_summary.csv", graph_summary); write_csv(output / "conflict_components.csv", components); write_csv(output / "minimum_vertex_cover.csv", mvc_rows)
    write_csv(output / "hnf_collision_cover_check.csv", checks); write_csv(output / "density_collision_curve.csv", curve); write_csv(output / "historical_hnf_cover_variants.csv", variants); write_csv(output / "exp18h_trace_crosscheck.csv", trace_rows); write_json(output / "deadline_collision_invariance.json", deadline)
    write_csv(output / "cross_topology_collision_graphs.csv", graph_rows)
    write_json(output / "collision_verdict.json", {"verdict": verdict, **flags, "no_solver_run": True, "next_stage_recommendation": "REDESIGN_DETERMINISTIC_SOURCE_EGRESS_RELEASE_ASSIGNMENT" if verdict == "FIXED_RELEASE_COLLISION_EXACTLY_EXPLAINS_ADMISSION_GAP" else "TARGETED_FIXED_RELEASE_AND_PLACEMENT_DIAGNOSIS" if verdict == "FIXED_RELEASE_COLLISION_PARTIALLY_EXPLAINS_ADMISSION_GAP" else "BACKEND_MODEL_SEMANTIC_REVIEW"})
    write_text(output / "summary.md", summary_markdown(verdict, flags, graph_rows, curve, trace_rows, release))
    assert_frozen(frozen)
    artifacts = {str(path.relative_to(output)): sha256_file(path) for path in sorted(output.rglob("*")) if path.is_file() and path.name != "analysis_manifest.json"}
    write_json(output / "analysis_manifest.json", {"experiment": "exp18i_fixed_release_source_egress_collision_audit", "parent_commit": parent, "implementation_commit": implementation_commit or parent, "results_commit": "RECORDED_BY_SUBSEQUENT_GIT_HISTORY", "quantum_ns": 100, "hypercycle_ticks": HYPERCYCLE_TICKS, "frozen_tree_sha256": frozen, "release_generator_audit_sha256": sha256_file(output / "release_generator_audit.md"), "timing_semantics_audit_sha256": sha256_file(output / "timing_semantics_audit.md"), "source_egress_audit_sha256": sha256_file(output / "source_egress_audit.csv"), "conflict_graphs_sha256": canonical_sha256([row["graph_sha256"] for row in graph_summary]), "mvc_solver": "componentwise Konig/Hopcroft-Karp or deterministic branch-and-bound, 10 CPU seconds per non-bipartite component", "backend_semantic_patch_sha256": sha256_file(ROOT / "third_party_patches" / "advanced_flow_scheduler" / "exp15_semantics.patch"), "exp18h_trace_artifact_sha256": {name: sha256_file(DIAGNOSTIC_RESULTS / name) for name in ("first_hnf_snapshot.jsonl", "flow_admission_trace.csv", "hnf_flow_summary.csv")}, "artifact_sha256": artifacts})
    assert_frozen(frozen)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--quick", action="store_true"); parser.add_argument("--implementation-commit")
    args = parser.parse_args(); return run(quick=args.quick, implementation_commit=args.implementation_commit)


if __name__ == "__main__":
    raise SystemExit(main())
