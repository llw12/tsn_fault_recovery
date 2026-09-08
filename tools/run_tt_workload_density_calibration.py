"""exp18g: deterministic, role-stratified healthy-P0 density calibration only."""
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
from decimal import Decimal
from pathlib import Path
from typing import Any

from tools.h2s_jrs_backend import H2sJrsBackend, parse_backend_output
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_p0_hnf_diagnosis import raw as frozen_raw
from tools.run_p0_hnf_mechanism_diagnosis import signatures
from tools.tt_workload_density_calibration import (
    DENSITIES, FROZEN_TREE_SHA256, NAMESPACE, ORDER, OUT, ROOT, SOURCE, Density,
    assert_backend_config, assert_frozen, backend_config, canonical_sha256, density_class_rows,
    derive_scenario, derived_integrity, endpoint_coverage, expected_instances, flow_kind,
    flow_set_sha, load_scenario, nestedness_rows, rank_hash, ranked_flows, retained_count,
    retained_ids, scale_topology, scenario_path, selection_rows, sha256_file, source_identity_rows,
    traffic_rows, workload_sha,
)

EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
TIMEOUT_S = 30
VALID_VERDICTS = {"PF_BENCHMARK_DENSITY_QUALIFIED", "NO_PF_ELIGIBLE_DENSITY_IN_PREREGISTERED_LADDER",
                  "DENSITY_BASELINE_PARITY_FAILED", "DENSITY_SELECTION_NOT_NESTED",
                  "CROSS_TOPOLOGY_SELECTION_MISMATCH", "DERIVED_DENSITY_WORKLOAD_INTEGRITY_FAILED",
                  "DENSITY_CALIBRATION_NONDETERMINISTIC", "INCONCLUSIVE"}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def shell(command: list[str]) -> str:
    return subprocess.run(command, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def clean_tree() -> bool:
    return not shell(["git", "status", "--porcelain"])


def output_root(quick: bool) -> Path:
    return OUT / "quick_validation" if quick else OUT


def source_sha(scenario_id: str) -> str:
    return sha256_file(scenario_path(scenario_id))


def recorded_path(path: Path) -> str:
    """Use repository-relative evidence paths when available, otherwise keep tests portable."""
    try: return str(path.relative_to(ROOT))
    except ValueError: return str(path)


def selection_manifest(scale: str, scenario: dict[str, Any], density: Density, source_shas: dict[str, str]) -> dict[str, Any]:
    ranked = ranked_flows(scale, scenario); retained = retained_ids(scale, scenario, density)
    rows = []
    for kind, values in sorted(ranked.items()):
        keep = [row["flow_id"] for row in values[:retained_count(len(values), density)]]
        drop = [row["flow_id"] for row in values[retained_count(len(values), density):]]
        rows.append({"flow_kind": kind, "original_count": len(values), "retained_count": len(keep),
                     "dropped_count": len(drop), "retained_flow_ids_sha256": flow_set_sha(keep),
                     "dropped_flow_ids_sha256": flow_set_sha(drop)})
    return {"scale": scale, "density_tag": density.tag, "rho_num": density.numerator, "rho_den": density.denominator,
            "rho_decimal": density.decimal, "selection_namespace": NAMESPACE,
            "ranking_algorithm": "SHA256(namespace|scale|flow_kind|flow_id), ascending (rank_hash, flow_id), prefix floor(p*N_c/q)",
            "source_scenario_sha256": {scenario_id: source_shas[scenario_id] for scenario_id in ORDER if scenario_id.startswith(scale + "_")},
            "retained_flow_set_sha256": flow_set_sha(retained), "classes": rows}


def materialize(output: Path, scenarios: dict[str, dict[str, Any]], source_shas: dict[str, str]) -> tuple[dict[tuple[str, str], tuple[Path, dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    refs: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}; integrity = []; coverage = []
    ranking_rows = selection_rows("M", scenarios["M_RING"]) + selection_rows("L", scenarios["L_RING"])
    for scale in ("M", "L"):
        parent = scenarios[f"{scale}_RING"]
        for density in DENSITIES:
            write_json(output / "selection_manifests" / f"{scale}_{density.tag}.json", selection_manifest(scale, parent, density, source_shas))
            coverage.append(endpoint_coverage(scale, parent, density))
    for scenario_id, parent in scenarios.items():
        for density in DENSITIES:
            child = derive_scenario(parent, scenario_id, density)
            if density.tag == "D100":
                path = scenario_path(scenario_id)
            else:
                path = output / "derived_scenarios" / f"{scenario_id}_{density.tag}" / "scenario.json"
                write_json(path, child)
            row = derived_integrity(parent, child, scenario_id, density, source_shas[scenario_id])
            row["derived_scenario_sha"] = sha256_file(path)
            row["derived_scenario_path"] = recorded_path(path)
            refs[(scenario_id, density.tag)] = (path, child); integrity.append(row)
    return refs, integrity, ranking_rows, coverage


def cross_topology_rows(refs: dict[tuple[str, str], tuple[Path, dict[str, Any]]]) -> tuple[list[dict[str, Any]], bool]:
    rows = []; passed = True
    for scale in ("M", "L"):
        ids = [f"{scale}_{topology}" for topology in ("RING", "REDSTAR", "ROR")]
        for density in DENSITIES:
            sets = {scenario_id: flow_set_sha(flow["id"] for flow in refs[(scenario_id, density.tag)][1]["tt_flows"]) for scenario_id in ids}
            workloads = {scenario_id: workload_sha(refs[(scenario_id, density.tag)][1]) for scenario_id in ids}
            exact = len(set(sets.values())) == 1 and len(set(workloads.values())) == 1; passed &= exact
            rows.append({"scale": scale, "density_tag": density.tag, "rho": density.label,
                         "ring_retained_flow_set_sha": sets[ids[0]], "redstar_retained_flow_set_sha": sets[ids[1]], "ror_retained_flow_set_sha": sets[ids[2]],
                         "ring_workload_sha": workloads[ids[0]], "redstar_workload_sha": workloads[ids[1]], "ror_workload_sha": workloads[ids[2]],
                         "retained_flow_set_exact_equal": len(set(sets.values())) == 1,
                         "workload_fingerprint_exact_equal": len(set(workloads.values())) == 1, "identity_pass": exact})
    return rows, passed


def run_backend(path: Path, output: Path) -> Any:
    backend = H2sJrsBackend(EXECUTABLE, h2s_flow_sorting=4, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0,
                            attempt_celf_fallback=True)
    result = backend.synthesize(RecoverySynthesisRequest(path, solver_timeout_s=TIMEOUT_S, route_scope="all-reroute",
                                                         forwarding_model="stream-aware", output_directory=output))
    write_attempt_logs(output / "logs", result)
    return result


def candidate_sha(payload: dict[str, Any]) -> str:
    return canonical_sha256(sorted((int(key), int(value)) for key, value in payload.get("candidate_path_counts", {}).items()))


def parsed_attempt(scenario: dict[str, Any], attempt: dict[str, Any], algorithm: str) -> dict[str, Any]:
    try:
        payload = parse_backend_output(str(attempt.get("stdout", ""))); signature = signatures(scenario, payload, algorithm)
        complete = (signature["scheduled_flow_count"] == signature["requested_flow_count"] == len(scenario["tt_flows"])
                    and bool(payload.get("upstream_verifier_pass")) and bool(attempt.get("project_static_checker_pass")))
        return {"algorithm": algorithm, "status": "P0_COMPLETE" if complete else "HEURISTIC_NOT_FOUND", "complete": complete,
                "signature": signature, "candidate_vector_sha": candidate_sha(payload),
                "upstream_verifier": bool(payload.get("upstream_verifier_pass")), "static_checker": bool(attempt.get("project_static_checker_pass")),
                "wall_ms": float(attempt.get("wall_ms") or 0), "peak_rss_bytes": int(attempt.get("peak_rss_bytes") or 0)}
    except Exception as error:
        return {"algorithm": algorithm, "status": "OUTPUT_INVALID", "complete": False, "signature": None, "candidate_vector_sha": "",
                "upstream_verifier": False, "static_checker": False, "wall_ms": float(attempt.get("wall_ms") or 0),
                "peak_rss_bytes": int(attempt.get("peak_rss_bytes") or 0), "parse_error": str(error)}


def observe(scenario: dict[str, Any], result: Any) -> dict[str, Any]:
    attempts = {str(row.get("algorithm", "")).upper(): row for row in result.statistics.get("attempts", [])}
    h2s = parsed_attempt(scenario, attempts["H2S"], "H2S") if "H2S" in attempts else {"algorithm": "H2S", "status": "NOT_ATTEMPTED", "complete": False, "signature": None, "candidate_vector_sha": "", "upstream_verifier": False, "static_checker": False, "wall_ms": 0.0, "peak_rss_bytes": 0}
    celf = parsed_attempt(scenario, attempts["CELF"], "CELF") if "CELF" in attempts else None
    formal = h2s if h2s["complete"] else (celf if celf and celf["complete"] else h2s)
    return {"backend_status": result.status.value, "h2s": h2s, "celf": celf, "formal": formal,
            "total_wall_ms": float(result.timings_ms.get("total_backend", 0)),
            "peak_rss_bytes": max([item["peak_rss_bytes"] for item in (h2s, celf) if item] or [0])}


def traffic_total(traffic: list[dict[str, Any]], scale: str, density: Density) -> dict[str, Any]:
    return next(row for row in traffic if row["scale"] == scale and row["density_tag"] == density.tag and row["flow_kind"] == "TOTAL")


def p0_row(scenario_id: str, density: Density, repeat: int, source: dict[str, Any], scenario: dict[str, Any], observed: dict[str, Any], traffic: list[dict[str, Any]], integrity: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    scale, topology = scale_topology(scenario_id); h2s, celf, formal = observed["h2s"], observed["celf"], observed["formal"]
    hs, cs, fs = h2s["signature"], celf["signature"] if celf else None, formal["signature"]
    total = traffic_total(traffic, scale, density)
    return {"scenario": scenario_id, "scale": scale, "topology": topology, "density_tag": density.tag, "rho_num": density.numerator,
            "rho_den": density.denominator, "rho_decimal": density.decimal, "repeat": repeat,
            "original_TT_count": len(source["tt_flows"]), "retained_TT_count": len(scenario["tt_flows"]),
            "actual_global_density": f"{len(scenario['tt_flows'])}/{len(source['tt_flows'])}",
            "actual_global_density_decimal": str(Decimal(len(scenario["tt_flows"])) / Decimal(len(source["tt_flows"]))), "expected_instances": expected_instances(scenario),
            "on_wire_bytes_per_hyperperiod": total["on_wire_bytes_per_hyperperiod"], "H2S_status": h2s["status"],
            "H2S_scheduled_count": hs["scheduled_flow_count"] if hs else 0, "H2S_HNF_count": hs["hnf_flow_count"] if hs else len(scenario["tt_flows"]),
            "H2S_HNF_set_sha": hs["hnf_set_sha256"] if hs else "", "H2S_complete": h2s["complete"], "H2S_ms": h2s["wall_ms"],
            "CELF_attempted": celf is not None, "CELF_status": celf["status"] if celf else "NOT_ATTEMPTED",
            "CELF_scheduled_count": cs["scheduled_flow_count"] if cs else 0, "CELF_HNF_count": cs["hnf_flow_count"] if cs else "",
            "CELF_HNF_set_sha": cs["hnf_set_sha256"] if cs else "", "CELF_complete": celf["complete"] if celf else False,
            "CELF_ms": celf["wall_ms"] if celf else 0.0, "formal_status": formal["status"],
            "formal_scheduled_count": fs["scheduled_flow_count"] if fs else 0, "formal_HNF_count": fs["hnf_flow_count"] if fs else len(scenario["tt_flows"]),
            "formal_complete": formal["complete"], "upstream_verifier": formal["upstream_verifier"], "static_checker": formal["static_checker"],
            "candidate_vector_sha": h2s["candidate_vector_sha"], "instance_completion_sha": fs["instance_completion_sha256"] if fs else "",
            "total_wall_ms": observed["total_wall_ms"], "peak_RSS": observed["peak_rss_bytes"], "input_integrity_pass": integrity[(scenario_id, density.tag)]["integrity_pass"]}


def source_signature(scenario_id: str, algorithm: str) -> tuple[dict[str, Any], str]:
    scenario, _, payload = frozen_raw(scenario_id, algorithm.lower())
    return signatures(scenario, payload, algorithm), candidate_sha(payload)


def baseline_parity(rows: list[dict[str, Any]], observations: dict[tuple[str, str, int], dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    result = []; passed = True
    for scenario_id in ORDER:
        expected = 348 if scenario_id.startswith("M_") else 914
        for repeat in (1, 2):
            observed = observations[(scenario_id, "D100", repeat)]
            for algorithm in ("H2S", "CELF"):
                item = observed[algorithm.lower()]; source, candidate = source_signature(scenario_id, algorithm); signature = item["signature"] if item else None
                row = {"scenario": scenario_id, "repeat": repeat, "algorithm": algorithm, "source_scheduled_count": source["scheduled_flow_count"],
                       "observed_scheduled_count": signature["scheduled_flow_count"] if signature else -1, "expected_scale_scheduled_count": expected,
                       "source_HNF_set_sha": source["hnf_set_sha256"], "observed_HNF_set_sha": signature["hnf_set_sha256"] if signature else "",
                       "source_instance_completion_sha": source["instance_completion_sha256"], "observed_instance_completion_sha": signature["instance_completion_sha256"] if signature else "",
                       "source_candidate_vector_sha": candidate, "observed_candidate_vector_sha": item["candidate_vector_sha"] if item else ""}
                row["count_exact"] = row["source_scheduled_count"] == row["observed_scheduled_count"] == expected
                row["hnf_exact"] = row["source_HNF_set_sha"] == row["observed_HNF_set_sha"]
                row["instance_exact"] = row["source_instance_completion_sha"] == row["observed_instance_completion_sha"]
                row["candidate_exact"] = algorithm != "H2S" or row["source_candidate_vector_sha"] == row["observed_candidate_vector_sha"]
                row["parity_pass"] = all(row[key] for key in ("count_exact", "hnf_exact", "instance_exact", "candidate_exact")); passed &= row["parity_pass"]
                result.append(row)
    return result, passed


def repeatability(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, set[tuple[str, str]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows: grouped[(row["scenario"], row["density_tag"])].append(row)
    report = []; failed: set[tuple[str, str]] = set()
    keys = ("H2S_status", "H2S_scheduled_count", "H2S_HNF_set_sha", "CELF_status", "CELF_scheduled_count", "CELF_HNF_set_sha", "formal_status", "formal_scheduled_count", "formal_complete", "instance_completion_sha", "candidate_vector_sha")
    for key, values in sorted(grouped.items()):
        values.sort(key=lambda row: row["repeat"]); left, right = values
        exact = {f"{name}_exact": left[name] == right[name] for name in keys}; ok = all(exact.values())
        if not ok: failed.add(key)
        report.append({"scenario": key[0], "density_tag": key[1], **exact, "repeatability_pass": ok})
    return report, not failed, failed


def archive_logs(output: Path) -> None:
    for path in list(output.rglob("*_stdout.log")) + list(output.rglob("*_stderr.log")):
        with path.open("rb") as source, Path(f"{path}.gz").open("wb") as compressed:
            with gzip.GzipFile(filename=f"{path.name}.gz", mode="wb", fileobj=compressed, mtime=0) as target: target.write(source.read())
        path.unlink()


def reports(output: Path, *, quick: bool, parent: str, implementation: str, frozen: dict[str, str], scenarios: dict[str, dict[str, Any]], source_shas: dict[str, str],
            refs: dict[tuple[str, str], tuple[Path, dict[str, Any]]], integrity_rows: list[dict[str, Any]], traffic: list[dict[str, Any]], coverage: list[dict[str, Any]],
            nested: list[dict[str, Any]], nested_ok: bool, cross: list[dict[str, Any]], cross_ok: bool, source_identity_ok: bool,
            rows: list[dict[str, Any]], observations: dict[tuple[str, str, int], dict[str, Any]], baseline: list[dict[str, Any]], baseline_ok: bool) -> int:
    write_csv(output / "derived_workload_integrity.csv", integrity_rows); write_csv(output / "density_traffic_census.csv", traffic); write_csv(output / "endpoint_coverage_diagnostics.csv", coverage)
    write_csv(output / "density_nestedness.csv", nested); write_csv(output / "cross_topology_density_identity.csv", cross); write_csv(output / "baseline_parity.csv", baseline); write_csv(output / "p0_density_results.csv", rows)
    class_counts = density_class_rows("M", scenarios["M_RING"]) + density_class_rows("L", scenarios["L_RING"])
    write_csv(output / "density_class_counts.csv", class_counts)
    integrity_by = {(row["scenario"], row["density_tag"]): row for row in integrity_rows}
    if quick:
        repeats = []; repeat_ok = False; failures: set[tuple[str, str]] = set(); summary = []; global_rows = []; verdict_name = "INCONCLUSIVE"; selected = None
    else:
        repeats, repeat_ok, failures = repeatability(rows); write_csv(output / "repeatability.csv", repeats)
        summary = []
        for scale in ("M", "L"):
            for density in DENSITIES:
                values = [row for row in rows if row["scale"] == scale and row["density_tag"] == density.tag and row["repeat"] == 1]
                total = traffic_total(traffic, scale, density)
                summary.append({"scale": scale, "density_tag": density.tag, "rho_num": density.numerator, "rho_den": density.denominator,
                                "retained_TT_count": total["logical_flow_count"], "actual_global_density": f"{total['logical_flow_count']}/{len(scenarios[f'{scale}_RING']['tt_flows'])}",
                                "packet_instances": total["packet_instance_count"], "on_wire_bytes_per_hyperperiod": total["on_wire_bytes_per_hyperperiod"],
                                "three_topology_complete_count": sum(row["formal_complete"] for row in values),
                                "minimum_scheduled_ratio": min((row["formal_scheduled_count"] / row["retained_TT_count"] for row in values), default=0),
                                "median_H2S_ms": statistics.median(row["H2S_ms"] for row in values), "max_H2S_ms": max(row["H2S_ms"] for row in values),
                                "CELF_fallback_count": sum(row["CELF_attempted"] for row in values), "max_RSS": max(row["peak_RSS"] for row in values)})
        write_csv(output / "density_summary.csv", summary)
        global_rows = []
        for density in DENSITIES:
            scale_counts = {}
            scale_repeat = {}
            scale_integrity = {}
            for scale in ("M", "L"):
                ids = [f"{scale}_{topology}" for topology in ("RING", "REDSTAR", "ROR")]
                complete_ids = [scenario_id for scenario_id in ids if all(next(row for row in rows if row["scenario"] == scenario_id and row["density_tag"] == density.tag and row["repeat"] == repeat)["formal_complete"] for repeat in (1, 2))]
                scale_counts[scale] = len(complete_ids); scale_repeat[scale] = all((scenario_id, density.tag) not in failures for scenario_id in ids)
                scale_integrity[scale] = all(integrity_by[(scenario_id, density.tag)]["integrity_pass"] for scenario_id in ids)
            all_six = scale_counts["M"] == scale_counts["L"] == 3
            qualified = all_six and all(scale_repeat.values()) and all(scale_integrity.values()) and nested_ok and cross_ok and source_identity_ok
            global_rows.append({"density_tag": density.tag, "rho_num": density.numerator, "rho_den": density.denominator, "rho_decimal": density.decimal,
                                "M_complete_count": f"{scale_counts['M']}/3", "L_complete_count": f"{scale_counts['L']}/3", "all_six_complete": all_six,
                                "repeatability_all_pass": all(scale_repeat.values()), "input_integrity_pass": all(scale_integrity.values()) and nested_ok and cross_ok and source_identity_ok,
                                "qualified_for_PF_benchmark": qualified})
        qualified_rows = [row for row in global_rows if row["qualified_for_PF_benchmark"]]
        selected = next((density for density in DENSITIES if any(row["density_tag"] == density.tag for row in qualified_rows)), None)
        for row in global_rows:
            index = next(index for index, density in enumerate(DENSITIES) if density.tag == row["density_tag"])
            lower = global_rows[index + 1:]
            row["heuristic_monotonicity_violated"] = row["qualified_for_PF_benchmark"] and any(not item["qualified_for_PF_benchmark"] for item in lower)
        write_csv(output / "density_global_qualification.csv", global_rows)
        if not nested_ok: verdict_name = "DENSITY_SELECTION_NOT_NESTED"
        elif not cross_ok or not source_identity_ok: verdict_name = "CROSS_TOPOLOGY_SELECTION_MISMATCH"
        elif not all(row["integrity_pass"] for row in integrity_rows): verdict_name = "DERIVED_DENSITY_WORKLOAD_INTEGRITY_FAILED"
        elif not baseline_ok: verdict_name = "DENSITY_BASELINE_PARITY_FAILED"
        elif not repeat_ok: verdict_name = "DENSITY_CALIBRATION_NONDETERMINISTIC"
        elif selected: verdict_name = "PF_BENCHMARK_DENSITY_QUALIFIED"
        else: verdict_name = "NO_PF_ELIGIBLE_DENSITY_IN_PREREGISTERED_LADDER"
    assert verdict_name in VALID_VERDICTS
    hnf_rows = []
    for row in rows:
        for backend, count_key, sha_key in (("H2S", "H2S_HNF_count", "H2S_HNF_set_sha"), ("CELF", "CELF_HNF_count", "CELF_HNF_set_sha")):
            if backend == "CELF" and not row["CELF_attempted"]: continue
            hnf_rows.append({"scenario": row["scenario"], "density_tag": row["density_tag"], "rho_num": row["rho_num"], "rho_den": row["rho_den"],
                             "repeat": row["repeat"], "backend": backend, "HNF_count": row[count_key], "HNF_set_sha": row[sha_key]})
    write_csv(output / "hnf_density_trajectory.csv", hnf_rows)
    flow_rows = []
    for scenario_id in ORDER:
        scale, topology = scale_topology(scenario_id)
        for density in DENSITIES:
            child = refs[(scenario_id, density.tag)][1]; selected_ids = {flow["id"] for flow in child["tt_flows"]}; obs = observations.get((scenario_id, density.tag, 1)); signature = obs["formal"]["signature"] if obs else None
            hnf = set(signature["hnf_flow_ids"]) if signature else selected_ids
            for flow in scenarios[scenario_id]["tt_flows"]:
                state = "DROPPED_BY_CALIBRATION" if flow["id"] not in selected_ids else "RETAINED_HNF" if flow["id"] in hnf else "RETAINED_SCHEDULED"
                flow_rows.append({"scale": scale, "topology": topology, "scenario": scenario_id, "density_tag": density.tag,
                                  "flow_id": flow["id"], "flow_kind": flow_kind(flow["id"]), "state": state})
    write_csv(output / "flow_density_trajectory.csv", flow_rows)
    candidate_invariant = all(len({row["candidate_vector_sha"] for row in rows if row["scenario"] == scenario_id and row["density_tag"] == density.tag}) == 1 for scenario_id in ORDER for density in DENSITIES if any(row["scenario"] == scenario_id and row["density_tag"] == density.tag for row in rows))
    selected_manifest = None
    if selected:
        selected_rows = [row for row in rows if row["density_tag"] == selected.tag and row["repeat"] == 1]
        selected_manifest = {"selected_rho_num": selected.numerator, "selected_rho_den": selected.denominator, "selected_rho_decimal": selected.decimal,
                             "medium_retained_logical_flow_count": len(refs[("M_RING", selected.tag)][1]["tt_flows"]), "large_retained_logical_flow_count": len(refs[("L_RING", selected.tag)][1]["tt_flows"]),
                             "medium_instance_count": expected_instances(refs[("M_RING", selected.tag)][1]), "large_instance_count": expected_instances(refs[("L_RING", selected.tag)][1]),
                             "traffic_census": [row for row in traffic if row["density_tag"] == selected.tag],
                             "per_flow_kind_retained_counts": [row for row in class_counts if row["density_tag"] == selected.tag],
                             "selected_scenarios": [{"scenario": scenario_id, "path": recorded_path(refs[(scenario_id, selected.tag)][0]), "sha256": sha256_file(refs[(scenario_id, selected.tag)][0]),
                                                     "workload_sha": workload_sha(refs[(scenario_id, selected.tag)][1])} for scenario_id in ORDER],
                             "backend_config": backend_config(), "p0_formal_status": [{key: row[key] for key in ("scenario", "H2S_complete", "CELF_attempted", "CELF_complete", "formal_complete", "upstream_verifier", "static_checker")} for row in selected_rows],
                             "repeatability_pass": all((scenario_id, selected.tag) not in failures for scenario_id in ORDER)}
        write_json(output / "selected_pf_benchmark_manifest.json", selected_manifest)
    verdict = {"formal_verdict": verdict_name, "baseline_parity_passed": baseline_ok, "selection_nested": nested_ok,
               "cross_topology_selection_passed": cross_ok and source_identity_ok, "candidate_vector_invariant": candidate_invariant,
               "repeatability_passed": repeat_ok, "selected_rho": {"tag": selected.tag, "num": selected.numerator, "den": selected.denominator} if selected else None,
               "next_stage_recommendation": "REALISTIC_PF_COST_CAMPAIGN" if selected else "REVIEW_WORKLOAD_CONSTRUCTION_OR_BACKEND_MODEL"}
    write_json(output / "calibration_verdict.json", verdict); archive_logs(output)
    ladder_text = ", ".join(f"{density.tag}={density.label}" for density in DENSITIES)
    count_table = ["| density | M flows / instances | L flows / instances |", "|---|---:|---:|"]
    for density in DENSITIES:
        m, l = traffic_total(traffic, "M", density), traffic_total(traffic, "L", density)
        count_table.append(f"| {density.tag} | {m['logical_flow_count']} / {m['packet_instance_count']} | {l['logical_flow_count']} / {l['packet_instance_count']} |")
    scheduled = ["| density | M_RING | M_REDSTAR | M_ROR | L_RING | L_REDSTAR | L_ROR |", "|---|---:|---:|---:|---:|---:|---:|"]
    if rows:
        for density in DENSITIES:
            current = {row["scenario"]: row for row in rows if row["density_tag"] == density.tag and row["repeat"] == 1}
            scheduled.append("| " + density.tag + " | " + " | ".join(
                f"{current[sid]['formal_scheduled_count']}/{current[sid]['retained_TT_count']}" if sid in current else "not-run" for sid in ORDER) + " |")
    rho_text = selected.tag if selected else "none"
    selected_density_text = (next(row["actual_global_density"] for row in summary if row["scale"] == "M" and row["density_tag"] == selected.tag)
                             if selected else "not applicable")
    selected_classes_text = (", ".join(f"{row['scale']}:{row['flow_kind']}={row['retained_count']}" for row in class_counts if selected and row["density_tag"] == selected.tag)
                             if selected else "not applicable")
    fallback_scenarios = sorted(f"{row['scenario']}:{row['density_tag']}" for row in rows if row["CELF_complete"] and not row["H2S_complete"])
    monotonicity = any(row.get("heuristic_monotonicity_violated", False) for row in global_rows)
    content = ["# exp18g: Deterministic role-stratified TT workload density calibration", "", "## Research answers", "",
               "1. The full-density workload remained P0-incomplete after the preregistered backend checks, so this stage calibrates a transparent PF benchmark workload.", "",
               "2. H2S tuning stops because K, deadlines, tie-breaks, and all official built-in sorters had already been tested without cardinality improvement.", "",
               "3. Only complete logical TT flows are removed; packet instances are never individually thinned.", "",
               "4. Periods are unchanged, preserving each retained flow's packet-instance multiplicity and PF synthesis semantics.", "",
               "5. Original exp18 deadlines are used because exp18e deadline relaxation did not improve P0 construction.", "",
               f"6. The exact rational ladder is: {ladder_text}.", "",
               "7. Selection is outcome-independent and uses only frozen source-flow metadata, a density rational, and the fixed namespace.", "",
               "8. No HNF identity or any solver result was read to decide which flows to drop.", "",
               "9. Each scale × role ranks SHA256(namespace|scale|flow_kind|flow_id), then retains the floor(p*N_c/q) prefix.", "",
               "10. Role stratification preserves approximate proportional representation of every canonical traffic role rather than selecting globally.", "",
               f"11. Cross-topology retained-flow identity: {'PASS' if cross_ok else 'FAIL'}.", "",
               f"12. Per-role nestedness: {'PASS' if nested_ok else 'FAIL'}.", "",
               "13–15. Retained logical flows and exact recomputed packet instances are:", "", *count_table, "",
               "16. On-wire-byte census is in `density_traffic_census.csv`, calculated from retained instances and the unchanged frame-overhead accounting.", "",
               f"17. D100 baseline parity: {'PASS' if baseline_ok else 'FAIL'}.", "",
               "18. The per-density six-scenario P0 scheduled counts are:", "", *scheduled, "",
               "19. H2S-only completion is separately recorded by `H2S_complete` in `p0_density_results.csv`; CELF fallback is never relabeled as H2S success.", "",
               f"20. Scenarios completed only through CELF fallback: {', '.join(fallback_scenarios) if fallback_scenarios else 'none'}.", "",
               f"21. Density-level 6/6 formal-backend qualification is recorded in `density_global_qualification.csv`; selected point: {rho_text}.", "",
               f"22. Highest qualified preregistered density rho*: {rho_text}.", "",
               f"23. The selected Medium actual global density is {selected_density_text}.", "",
               f"24. Per-role selected retained counts: {selected_classes_text}.", "",
               f"25. Heuristic non-monotonicity was {'observed' if monotonicity else 'not observed'} on the preregistered qualification predicate; selection always uses the highest qualified ladder point.", "",
               "26. A selected workload changes only the TT logical-flow population; retained timing, payload, role/endpoints, topology, and backend are unchanged.", "",
               "27. It is not a real factory trace.", "",
               "28. It is a deterministically density-calibrated, literature-grounded, role-based synthetic industrial workload.", "",
               "29. This experiment does not prove the original full-density workload mathematically infeasible; it only reports the fixed qualified heuristic backend's construction outcome.", "",
               "30. A selected workload is a PF-cost benchmark because it retains the original topology, mixed-period timing, roles, and semantics while the fixed backend constructs complete healthy P0 at the highest qualified tested density.", "",
               f"31. Next stage: `{verdict['next_stage_recommendation']}` using the exact selected scenario bytes if qualified; this experiment does not start it.", "",
               "No PF, fault enumeration, Profile Store, parallel PF, OMNeT++, or INET run is performed."]
    (output / "summary.md").write_text("\n".join(content) + "\n", encoding="utf-8")
    artifacts = {str(path.relative_to(output)): sha256_file(path) for path in output.rglob("*") if path.is_file() and path.name != "analysis_manifest.json"}
    write_json(output / "analysis_manifest.json", {"experiment": "exp18g_tt_workload_density_calibration", "parent_commit": parent, "implementation_commit": implementation,
               "results_commit": "RECORDED_BY_SUBSEQUENT_GIT_HISTORY", "historical_tree_sha256": frozen, "exp18_source_scenario_sha256": source_shas,
               "exp18b_verdict_sha256": sha256_file(ROOT / "results/p0_hnf_diagnosis/mechanism_verdict.md"), "exp18c_verdict_sha256": sha256_file(ROOT / "results/candidate_k_sensitivity/research_direction_assessment.json"),
               "exp18d_verdict_sha256": sha256_file(ROOT / "results/h2s_multistart_sensitivity/verdict.json"), "exp18e_verdict_sha256": sha256_file(ROOT / "results/deadline_feasibility_calibration/calibration_verdict.json"),
               "exp18f_verdict_sha256": sha256_file(ROOT / "results/h2s_primary_policy_sensitivity/verdict.json"), "advanced_flow_scheduler_commit": shell(["git", "-C", str(ROOT / ".external/AdvancedFlowScheduler"), "rev-parse", "HEAD"]),
               "upstream_semantic_patch_sha256": sha256_file(ROOT / "third_party_patches/advanced_flow_scheduler/exp15_semantics.patch"), "exp18d_tiebreak_patch_state": "DISABLED",
               "density_ladder": [{"tag": density.tag, "num": density.numerator, "den": density.denominator} for density in DENSITIES], "selector_namespace": NAMESPACE,
               "ranking_algorithm": "SHA256(namespace|scale|flow_kind|flow_id), prefix floor(p*N_c/q)", "selected_rho": verdict["selected_rho"], "backend_config": backend_config(), "artifact_sha256": artifacts})
    assert_frozen()
    return 0 if quick or verdict_name in {"PF_BENCHMARK_DENSITY_QUALIFIED", "NO_PF_ELIGIBLE_DENSITY_IN_PREREGISTERED_LADDER"} else 2


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--quick", action="store_true"); parser.add_argument("--implementation-commit")
    args = parser.parse_args(); output = output_root(args.quick)
    if not EXECUTABLE.is_file(): raise SystemExit(f"missing H2S executable: {EXECUTABLE}")
    if not args.quick and not clean_tree(): raise SystemExit("full campaign requires a clean implementation commit")
    if output.exists(): raise SystemExit(f"refusing to overwrite evidence: {output}")
    parent = shell(["git", "rev-parse", "HEAD"]); implementation = args.implementation_commit or parent; frozen = assert_frozen(); config = backend_config(); assert_backend_config(config)
    scenarios = {scenario_id: load_scenario(scenario_id) for scenario_id in ORDER}; source_shas = {scenario_id: source_sha(scenario_id) for scenario_id in ORDER}
    source_identity, source_identity_ok = source_identity_rows(scenarios)
    if not source_identity_ok: raise SystemExit("SOURCE_WORKLOAD_IDENTITY_MISMATCH")
    output.mkdir(parents=True); write_json(output / "environment.json", {"platform": platform.platform(), "python": platform.python_version(), "os_uname": list(os.uname()), "mode": "quick" if args.quick else "full", "executable_sha256": sha256_file(EXECUTABLE), "backend_config": config})
    write_json(output / "source_scenario_manifest.json", {"input_source": "byte-frozen original exp18 source scenarios; generator not invoked", "source_scenario_sha256": source_shas, "source_workload_identity": source_identity, "historical_tree_sha256": frozen})
    census = []
    for scale in ("M", "L"):
        parent = scenarios[f"{scale}_RING"]
        for kind, values in sorted(ranked_flows(scale, parent).items()): census.append({"scale": scale, "flow_kind": kind, "original_count": len(values)})
    write_csv(output / "source_flow_kind_census.csv", census)
    write_json(output / "selection_provenance.json", {"selection_inputs": ["frozen_scenario_sha256", "density_rational", "selection_namespace", "flow_metadata"], "selection_namespace": NAMESPACE, "hnf_guided_selection": False, "topology_in_ranking_key": False, "result_history_read": False})
    refs, integrity, ranks, coverage = materialize(output, scenarios, source_shas); write_csv(output / "flow_retention_rank.csv", ranks)
    traffic = traffic_rows("M", scenarios["M_RING"]) + traffic_rows("L", scenarios["L_RING"]); nested_m, nested_m_ok = nestedness_rows("M", scenarios["M_RING"]); nested_l, nested_l_ok = nestedness_rows("L", scenarios["L_RING"]); nested = nested_m + nested_l; nested_ok = nested_m_ok and nested_l_ok
    cross, cross_ok = cross_topology_rows(refs)
    if not nested_ok or not cross_ok or not all(row["integrity_pass"] for row in integrity):
        return reports(output, quick=args.quick, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, source_shas=source_shas, refs=refs, integrity_rows=integrity, traffic=traffic, coverage=coverage, nested=nested, nested_ok=nested_ok, cross=cross, cross_ok=cross_ok, source_identity_ok=source_identity_ok, rows=[], observations={}, baseline=[], baseline_ok=False)
    planned = [("M_RING", DENSITIES[0], 1), ("M_RING", DENSITIES[4], 1)] if args.quick else [(scenario_id, density, repeat) for density in DENSITIES for scenario_id in ORDER for repeat in (1, 2)]
    rows = []; observations: dict[tuple[str, str, int], dict[str, Any]] = {}; integrity_by = {(row["scenario"], row["density_tag"]): row for row in integrity}
    for scenario_id, density, repeat in planned:
        path, scenario = refs[(scenario_id, density.tag)]; raw = output / "raw_backend_output" / scenario_id / density.tag / f"R{repeat}"; observed = observe(scenario, run_backend(path, raw)); observations[(scenario_id, density.tag, repeat)] = observed
        rows.append(p0_row(scenario_id, density, repeat, scenarios[scenario_id], scenario, observed, traffic, integrity_by))
        if not args.quick and density.tag == "D100" and scenario_id == ORDER[-1] and repeat == 2:
            baseline, baseline_ok = baseline_parity(rows, observations)
            if not baseline_ok:
                return reports(output, quick=False, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, source_shas=source_shas, refs=refs, integrity_rows=integrity, traffic=traffic, coverage=coverage, nested=nested, nested_ok=nested_ok, cross=cross, cross_ok=cross_ok, source_identity_ok=source_identity_ok, rows=rows, observations=observations, baseline=baseline, baseline_ok=False)
    if args.quick:
        observed = observations[("M_RING", "D100", 1)]; baseline = []; baseline_ok = True
        for algorithm in ("H2S", "CELF"):
            item = observed[algorithm.lower()]; source, candidate = source_signature("M_RING", algorithm); signature = item["signature"] if item else None
            ok = bool(signature and signature["scheduled_flow_count"] == source["scheduled_flow_count"] and signature["hnf_set_sha256"] == source["hnf_set_sha256"] and signature["instance_completion_sha256"] == source["instance_completion_sha256"] and (algorithm != "H2S" or item["candidate_vector_sha"] == candidate)); baseline.append({"scenario": "M_RING", "algorithm": algorithm, "parity_pass": ok}); baseline_ok &= ok
    else: baseline, baseline_ok = baseline_parity(rows, observations)
    return reports(output, quick=args.quick, parent=parent, implementation=implementation, frozen=frozen, scenarios=scenarios, source_shas=source_shas, refs=refs, integrity_rows=integrity, traffic=traffic, coverage=coverage, nested=nested, nested_ok=nested_ok, cross=cross, cross_ok=cross_ok, source_identity_ok=source_identity_ok, rows=rows, observations=observations, baseline=baseline, baseline_ok=baseline_ok)


if __name__ == "__main__": raise SystemExit(main())
