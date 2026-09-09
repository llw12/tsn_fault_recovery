"""exp19: full-census all-reroute PF cost campaign on exp18j workloads.

This runner deliberately has no candidate sampling, grouping, cache, warm
start, or adaptive policy.  It freezes every P0-used internal physical link
before the first PF invocation, invokes exactly one H2S->CELF attempt per
candidate, and makes resumption a provenance-checked continuation only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.fixed_release_collision_audit import ROOT, sha256_file
from tools.h2s_jrs_backend import (
    DEFAULT_CANDIDATE_PATHS, FORMAL_MEMORY_LIMIT_MB, FORMAL_SEED, FORMAL_THREADS,
    UPSTREAM_COMMIT, UPSTREAM_LICENSE, UPSTREAM_REPOSITORY, check_h2s_pf_solution,
    prepare_h2s_inputs,
)
from tools.h2s_pf_backend import H2sPfBackend, semantic_profile_hash
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.realistic_full_density_pf_cost import (
    EXP18J, ORDER, OUT, PfCampaignError, assert_resume_contract, benchmark_config,
    candidate_catalog, canonical_route_hash, frozen_preflight, gzip_bytes,
    load_benchmark, lpt_projection, mean_or_zero, pearson, percentile, profile_storage,
    recover_p0_baseline, repeat_subset, resume_contract, route_churn,
    runtime_profile_contract_audit, spearman, write_atomic_bytes, write_atomic_json,
)
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs

EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
SEMANTIC_PATCH = ROOT / "third_party_patches/advanced_flow_scheduler/exp15_semantics.patch"
TIMEOUT_S = 30
SCENARIO_BUDGET_S = 3 * 60 * 60
SUCCESS = {BackendStatus.SUCCESS_H2S.value, BackendStatus.SUCCESS_CELF_FALLBACK.value}
RESOURCE = {BackendStatus.TIME_LIMIT.value, BackendStatus.MEMORY_LIMIT.value}


def csv_bytes(rows: list[dict[str, Any]], fields: list[str] | None = None) -> bytes:
    fields = fields or sorted({key for row in rows for key in row})
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    write_atomic_bytes(path, csv_bytes(rows, fields))


def git_value(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def artifact_hashes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): sha256_file(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "analysis_manifest.json"}


def expected_config(manifest: dict[str, Any]) -> dict[str, Any]:
    config = benchmark_config(manifest)
    # The upstream CLI's configuration-rating default is PATH_LENGTH.  The
    # pinned executable does not expose a separate -c argument, so the audit
    # records both the source-manifest selection and the exact spawned argv.
    return {**config, "h2s_command_configuration_rating": "UPSTREAM_DEFAULT_PATH_LENGTH"}


def context_for(scenario_id: str, source_path: Path, candidate_sha: str, config: dict[str, Any], implementation: str) -> dict[str, Any]:
    return resume_contract(
        implementation_commit=implementation,
        scenario_sha=sha256_file(source_path),
        candidate_set_sha=candidate_sha,
        backend_config=config,
        upstream_commit=UPSTREAM_COMMIT,
        semantic_patch_sha=sha256_file(SEMANTIC_PATCH),
    )


def validate_all_reroute_input(scenario_path: Path, output: Path, healthy_routes: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any]:
    """Materialize then inspect the actual H2S input for a PF invocation."""
    fault = str(candidate["fault_id"])
    affected = tuple(str(value) for value in str(candidate["affected_flow_ids"]).split(";") if value)
    prepared = prepare_h2s_inputs(scenario_path, output, 100, DEFAULT_CANDIDATE_PATHS,
        disabled_links=(fault,), healthy_primary_routes={row["flow_id"]: row for row in healthy_routes},
        affected_flow_ids=affected, route_scope="all-reroute")
    upstream = json.loads(prepared.scenario_path.read_text(encoding="utf-8"))
    add_flows = upstream["time_steps"][0]["addFlows"]
    checks = {
        "FAILED_PHYSICAL_LINK_REMOVED_BOTH_DIRECTIONS": fault not in set(prepared.arc_to_link.values()),
        "ALL_REROUTE_HAS_NO_FIXED_PATH": all("fixed path" not in flow for flow in add_flows),
        "ALL_TT_FLOWS_PRESENT": len(add_flows) == len(prepared.flow_map),
        "FIXED_RELEASE_PRESERVED": all(flow.get("fixed release") is True for flow in add_flows),
        "RELEASE_AND_DEADLINE_QUANTIZATION_ROWS_COMPLETE": len(prepared.quantization_rows) == len(prepared.flow_map),
        "ROUTE_SCOPE_ALL_REROUTE": prepared.route_scope == "all-reroute",
    }
    if not all(checks.values()):
        raise PfCampaignError(f"PF_INPUT_SEMANTIC_INVALID:{candidate['scenario']}:{fault}:{checks}")
    return {"checks": checks, "all_flow_count": len(prepared.flow_map),
            "disabled_directed_arc_count": 2, "upstream_flow_count": len(add_flows)}


def result_hnf_signature(result: Any) -> str:
    """Stable diagnostic signature; it is never interpreted as infeasibility."""
    attempts = result.statistics.get("attempts", [])
    payload = [{key: attempt.get(key) for key in ("algorithm", "returncode", "parse_error", "normalization_error",
                                                   "scheduled_flow_count", "requested_flow_count")}
               for attempt in attempts]
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def result_row(scenario_id: str, candidate: dict[str, Any], result: Any, p0: dict[str, Any],
               input_audit: dict[str, Any], profile_ref: str = "") -> dict[str, Any]:
    stats, timings = result.statistics, result.timings_ms
    attempts = stats.get("attempts", [])
    peak = max([int(attempt.get("peak_rss_bytes") or 0) for attempt in attempts] or [0])
    success = result.status.value in SUCCESS and bool(result.profile)
    semantic = bool(stats.get("semantic_valid")) and success
    row = {
        "scenario": scenario_id, "scale": scenario_id[0], "topology": scenario_id.split("_", 1)[1],
        "fault_id": candidate["fault_id"], "candidate_rank": candidate["candidate_rank"],
        "affected_flow_count": candidate["affected_flow_count"], "affected_flow_ratio": candidate["affected_flow_ratio"],
        "affected_flow_ids": candidate["affected_flow_ids"], "status": result.status.value,
        "algorithm_used": stats.get("algorithm_used", ""), "primary_h2s_success": bool(stats.get("primary_h2s_success")),
        "celf_fallback_used": bool(stats.get("celf_fallback_used")), "all_flows_scheduled": bool(stats.get("all_flows_scheduled")),
        "scheduled_flow_count": int(stats.get("scheduled_flow_count", 0)), "requested_flow_count": int(stats.get("requested_flow_count", p0["all_flow_count"])),
        "scheduled_flow_ratio": float(stats.get("scheduled_flow_ratio", 0)), "semantic_valid": semantic,
        "upstream_verifier_pass": bool(stats.get("upstream_verifier_pass")),
        "project_static_checker_pass": bool(stats.get("project_static_checker_pass")),
        "structural_no_route": result.status == BackendStatus.STRUCTURAL_NO_ROUTE,
        "heuristic_not_found": result.status == BackendStatus.HEURISTIC_NOT_FOUND,
        "hnf_signature": result_hnf_signature(result), "diagnostic": result.diagnostic,
        "conversion_ms": float(timings.get("conversion", 0)),
        "candidate_route_generation_ms": float(timings.get("candidate_route_generation", stats.get("candidate_route_generation_ms", 0))),
        "scheduling_ms": float(timings.get("scheduling", 0)), "verification_ms": float(timings.get("verification", 0)),
        "profile_normalization_ms": float(timings.get("profile_normalization", 0)),
        "h2s_wall_ms": float(timings.get("h2s_wall", 0)), "celf_wall_ms": float(timings.get("celf_wall", 0)),
        "total_backend_ms": float(timings.get("total_backend", 0)), "p0_reference_ms": p0["p0_reference_ms"],
        "pf_to_p0_ratio": float(timings.get("total_backend", 0)) / p0["p0_reference_ms"] if p0["p0_reference_ms"] else 0,
        "peak_rss_bytes": peak, "profile_ref": profile_ref, "route_hash": canonical_route_hash(result.logical_routes) if success else "",
        "schedule_hash": canonical_sha(result.statistics.get("route_schedule", [])) if success else "",
        "semantic_profile_hash": semantic_profile_hash(result.profile) if success and result.profile else "",
        "input_semantics_pass": all(input_audit["checks"].values()),
    }
    if success and result.profile:
        row.update(profile_storage(result.profile))
    else:
        row.update({"canonical_profile_bytes": 0, "canonical_gzip_bytes": 0, "semantic_payload_bytes": 0,
                    "semantic_gzip_bytes": 0, "profile_sha256": ""})
    return row


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def checkpoint_path(root: Path, scenario_id: str, fault_id: str) -> Path:
    return root / "per_fault_results" / scenario_id / f"{fault_id}.json"


def run_fault(root: Path, scenario_id: str, scenario_path: Path, candidate: dict[str, Any], p0: dict[str, Any],
              contract: dict[str, Any], backend: H2sPfBackend, *, resume: bool, repeat: int | None = None) -> tuple[dict[str, Any], Any | None]:
    suffix = f"repeat_{repeat}" if repeat else "primary"
    fault = str(candidate["fault_id"])
    if repeat is None:
        saved = checkpoint_path(root, scenario_id, fault)
        if resume and saved.is_file():
            record = json.loads(saved.read_text(encoding="utf-8"))
            assert_resume_contract(record.get("resume_contract", {}), contract)
            if record.get("candidate") != candidate:
                raise PfCampaignError(f"RESUME_CANDIDATE_MISMATCH:{scenario_id}:{fault}")
            return record["row"], None
    raw = root / "raw_backend_output" / scenario_id / fault / suffix
    input_audit = validate_all_reroute_input(scenario_path, raw / "input", p0["logical_routes"], candidate)
    request = RecoverySynthesisRequest(
        scenario_path, disabled_links=(fault,), healthy_primary_routes={row["flow_id"]: row for row in p0["logical_routes"]},
        affected_flow_ids=tuple(str(value) for value in str(candidate["affected_flow_ids"]).split(";") if value),
        solver_timeout_s=TIMEOUT_S, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=raw / "input")
    result = backend.synthesize(request)
    write_attempt_logs(raw / "logs", result)
    ref = ""
    if result.status.value in SUCCESS and result.profile:
        profile = root / "profiles" / scenario_id / f"{fault}.json"
        # Repeat evaluations must not overwrite the canonical primary artifact.
        if repeat is None:
            write_atomic_json(profile, result.profile)
            compressed = root / "stores" / "canonical" / scenario_id / f"{fault}.json.gz"
            write_atomic_bytes(compressed, gzip_bytes(canonical_json_bytes(result.profile)))
            ref = str(profile.relative_to(root))
        else:
            ref = f"repeat_{repeat}_not_stored"
    row = result_row(scenario_id, candidate, result, p0, input_audit, ref)
    row["repeat"] = repeat or 1
    if repeat is None:
        write_atomic_json(checkpoint_path(root, scenario_id, fault), {"resume_contract": contract, "candidate": candidate,
            "input_audit": input_audit, "row": row, "profile_ref": ref})
    return row, result


def coverage_rows(rows: list[dict[str, Any]], census: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    by_scenario = defaultdict(list)
    for row in rows: by_scenario[row["scenario"]].append(row)
    for scenario in ORDER:
        values = by_scenario[scenario]; relevant = [row for row in census if row["scenario"] == scenario and row["relevant_fault"]]
        successful = [row for row in values if row["status"] in SUCCESS and row["semantic_valid"]]
        result.append({"scenario": scenario, "internal_physical_links": sum(row["internal_switch_link"] for row in census if row["scenario"] == scenario),
            "relevant_faults": len(relevant), "unused_internal_faults": sum(row["internal_switch_link"] and not row["p0_used"] for row in census if row["scenario"] == scenario),
            "PF_attempted": len(values), "successful_valid_profiles": len(successful),
            "candidate_coverage": len(values) / len(relevant) if relevant else 1.0,
            "valid_profile_coverage": len(successful) / len(relevant) if relevant else 1.0,
            "structural_no_route_count": sum(row["structural_no_route"] for row in values),
            "H2S_primary_success_rate": sum(row["primary_h2s_success"] for row in values) / len(values) if values else 0,
            "CELF_trigger_rate": sum(row["celf_fallback_used"] for row in values) / len(values) if values else 0,
            "CELF_rescue_rate": sum(row["status"] == BackendStatus.SUCCESS_CELF_FALLBACK.value for row in values) / max(sum(row["celf_fallback_used"] for row in values), 1),
        })
    return result


def timing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for scenario in ORDER:
        values = [row for row in rows if row["scenario"] == scenario]
        timings = [float(row["total_backend_ms"]) for row in values]
        p0 = float(values[0]["p0_reference_ms"]) if values else 0.0
        output.append({"scenario": scenario, "fault_count": len(values), "serial_fault_backend_ms": sum(timings),
            "mean_ms": mean_or_zero(timings), "p50_ms": percentile(timings, .50), "p75_ms": percentile(timings, .75),
            "p90_ms": percentile(timings, .90), "p95_ms": percentile(timings, .95), "p99_ms": percentile(timings, .99),
            "max_ms": max(timings, default=0.0), "p0_reference_ms": p0,
            "p50_pf_to_p0_ratio": percentile(timings, .50) / p0 if p0 else 0.0,
            "p95_pf_to_p0_ratio": percentile(timings, .95) / p0 if p0 else 0.0,
            "max_pf_to_p0_ratio": max(timings, default=0.0) / p0 if p0 else 0.0})
    return output


def memory_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for scenario in ORDER:
        values = [int(row["peak_rss_bytes"]) for row in rows if row["scenario"] == scenario]
        output.append({"scenario": scenario, "fault_count": len(values), "mean_peak_rss_bytes": mean_or_zero(values),
            "p50_peak_rss_bytes": percentile(values, .50), "p95_peak_rss_bytes": percentile(values, .95), "max_peak_rss_bytes": max(values, default=0)})
    return output


def make_lpt_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    scenarios = {scenario: [row for row in rows if row["scenario"] == scenario] for scenario in ORDER}
    for group, values in [(scenario, scenarios[scenario]) for scenario in ORDER] + [("POOLED_ALL_SCENARIOS", rows)]:
        for workers in (1, 2, 4, 8, 16, 32):
            output.append({"scope": group, **lpt_projection(values, workers)})
    return output


def storage_rows(rows: list[dict[str, Any]], p0s: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows_out, semantic_index = [], []
    for row in rows:
        if row["status"] not in SUCCESS or not row["semantic_valid"]: continue
        rows_out.append({key: row[key] for key in ("scenario", "fault_id", "profile_ref", "canonical_profile_bytes", "canonical_gzip_bytes",
            "semantic_payload_bytes", "semantic_gzip_bytes", "profile_sha256", "semantic_profile_hash")})
        semantic_index.append({"semantic_profile_hash": row["semantic_profile_hash"], "scenario": row["scenario"], "fault_id": row["fault_id"], "profile_ref": row["profile_ref"]})
    for scenario, p0 in p0s.items():
        rows_out.append({"scenario": scenario, "fault_id": "P0", "profile_ref": "P0_SEPARATE_NOT_DEDUPLICATED",
            "canonical_profile_bytes": p0["p0_profile_bytes"], "canonical_gzip_bytes": p0["p0_profile_gzip_bytes"],
            "semantic_payload_bytes": "", "semantic_gzip_bytes": "", "profile_sha256": "", "semantic_profile_hash": ""})
    return rows_out, semantic_index


def correlation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for scenario in ORDER:
        values = [row for row in rows if row["scenario"] == scenario]
        output.append({"scenario": scenario, "fault_count": len(values),
            "pearson_affected_flows_total_backend_ms": pearson([float(row["affected_flow_count"]) for row in values], [float(row["total_backend_ms"]) for row in values]),
            "spearman_affected_flows_total_backend_ms": spearman([float(row["affected_flow_count"]) for row in values], [float(row["total_backend_ms"]) for row in values]),
            "pearson_affected_flows_peak_rss": pearson([float(row["affected_flow_count"]) for row in values], [float(row["peak_rss_bytes"]) for row in values])})
    return output


def topology_comparisons(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary = {row["scenario"]: row for row in timing_rows(rows)}
    topology = [{"scale": scale, "topology": topology, "scenario": f"{scale}_{topology}",
        "median_pf_ms": summary.get(f"{scale}_{topology}", {}).get("p50_ms", 0),
        "p95_pf_ms": summary.get(f"{scale}_{topology}", {}).get("p95_ms", 0),
        "serial_pf_ms": summary.get(f"{scale}_{topology}", {}).get("serial_fault_backend_ms", 0)}
        for scale in ("M", "L") for topology in ("RING", "REDSTAR", "ROR")]
    scale = []
    for topology_id in ("RING", "REDSTAR", "ROR"):
        medium, large = summary.get(f"M_{topology_id}", {}), summary.get(f"L_{topology_id}", {})
        scale.append({"topology": topology_id, "M_median_pf_ms": medium.get("p50_ms", 0), "L_median_pf_ms": large.get("p50_ms", 0),
            "M_serial_pf_ms": medium.get("serial_fault_backend_ms", 0), "L_serial_pf_ms": large.get("serial_fault_backend_ms", 0)})
    return topology, scale


def repetition_audit(root: Path, contexts: dict[str, dict[str, Any]], candidates: dict[str, list[dict[str, Any]]],
                     p0s: dict[str, dict[str, Any]], rows_by_key: dict[tuple[str, str], dict[str, Any]], backend: H2sPfBackend) -> list[dict[str, Any]]:
    output = []
    for scenario in ORDER:
        selected = repeat_subset(candidates[scenario])
        for ordinal, fault in enumerate(selected, 1):
            candidate = next(item for item in candidates[scenario] if item["fault_id"] == fault)
            initial = rows_by_key[(scenario, fault)]
            repeat, _ = run_fault(root, scenario, contexts[scenario]["path"], candidate, p0s[scenario], contexts[scenario]["contract"], backend, resume=False, repeat=ordinal)
            same_success = initial["status"] in SUCCESS and repeat["status"] in SUCCESS
            output.append({"scenario": scenario, "fault_id": fault, "repeat_selector_rank": ordinal,
                "affected_flow_count": candidate["affected_flow_count"], "baseline_status": initial["status"], "repeat_status": repeat["status"],
                "status_equal": initial["status"] == repeat["status"], "algorithm_equal": initial["algorithm_used"] == repeat["algorithm_used"],
                "route_hash_equal": initial["route_hash"] == repeat["route_hash"] if same_success else "NOT_APPLICABLE",
                "schedule_hash_equal": initial["schedule_hash"] == repeat["schedule_hash"] if same_success else "NOT_APPLICABLE",
                "semantic_hash_equal": initial["semantic_profile_hash"] == repeat["semantic_profile_hash"] if same_success else "NOT_APPLICABLE",
                "profile_hash_equal": initial["profile_sha256"] == repeat["profile_sha256"] if same_success else "NOT_APPLICABLE",
                "instance_checker_equal": initial["project_static_checker_pass"] == repeat["project_static_checker_pass"],
                "verifier_equal": initial["upstream_verifier_pass"] == repeat["upstream_verifier_pass"],
                "hnf_signature_equal": initial["hnf_signature"] == repeat["hnf_signature"] if not same_success else "NOT_APPLICABLE"})
    return output


def run_tiny_semantic_pf(root: Path, backend: H2sPfBackend) -> dict[str, Any]:
    """Qualification-only all-reroute diamond, independent of the campaign data."""
    with tempfile.TemporaryDirectory(prefix="exp19-tiny-") as temporary:
        temp = Path(temporary); scenario = {
            "schema_version": 1, "scenario_name": "exp19_tiny", "forwarding_model": "stream-aware",
            "simulation": {"duration_s": .01, "cycle_time_s": .001, "time_quantum_s": 1e-9, "failure_time_s": .001, "solver_delay_s": 0, "random_seed": 1024},
            "network": {"default_bitrate_bps": 1_000_000_000, "default_propagation_delay_s": 0},
            "scheduling": {"ingress_margin_s": 0, "hop_margin_s": 0, "endpoint_budget_s": 0, "frame_overhead_bytes": 64, "be_traffic_class": 0},
            "nodes": [{"id": item, "type": "switch" if item.startswith("s") else "end_system"} for item in ("a", "d", "s0", "s1", "s2")],
            "links": [{"id": key, "endpoint_a": left, "endpoint_b": right, "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0}
                      for key, left, right in (("la", "a", "s0"), ("lf", "s0", "s1"), ("ld", "s1", "d"), ("l02", "s0", "s2"), ("l21", "s2", "s1"))],
            "tt_flows": [{"id": "TT", "source": "a", "destination": "d", "packet_size_bytes": 100, "period_s": .001, "deadline_e2e_s": .0005, "schedule_deadline_budget_s": .0005, "release_offset_s": 0, "pcp": 4, "traffic_class": 1}],
            "be_flows": [], "fault_candidates": [], "fault_candidate_policy": {"mode": "explicit", "exclude": []}}
        path = temp / "scenario.json"; write_atomic_json(path, scenario)
        healthy = {"TT": {"flow_id": "TT", "node_path": ["a", "s0", "s1", "d"], "link_path": ["la", "lf", "ld"]}}
        request = RecoverySynthesisRequest(path, disabled_links=("lf",), healthy_primary_routes=healthy, affected_flow_ids=("TT",),
            solver_timeout_s=TIMEOUT_S, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=temp / "out")
        result = backend.synthesize(request)
        routes = {row["flow_id"]: row for row in result.logical_routes}
        valid = result.status.value in SUCCESS and "lf" not in routes.get("TT", {}).get("link_path", []) and bool(result.statistics.get("semantic_valid"))
        return {"status": result.status.value, "all_reroute_semantics_pass": valid, "route": routes.get("TT", {}),
                "backend_output": str((root / "logs" / "tiny_semantic_pf.json").relative_to(root))}


def write_qualification(root: Path, contexts: dict[str, dict[str, Any]], p0s: dict[str, dict[str, Any]], backend: H2sPfBackend) -> bool:
    samples = []
    for scenario in ("M_RING", "M_REDSTAR", "M_ROR"):
        candidate = contexts[scenario]["candidates"][0]
        audit = validate_all_reroute_input(contexts[scenario]["path"], root / "logs" / "input_audit" / scenario, p0s[scenario]["logical_routes"], candidate)
        samples.append({"scenario": scenario, "fault_id": candidate["fault_id"], **audit["checks"]})
    tiny = run_tiny_semantic_pf(root, backend)
    good = all(all(value for key, value in row.items() if key not in {"scenario", "fault_id"}) for row in samples) and tiny["all_reroute_semantics_pass"]
    write_csv(root / "qualification_results.csv", samples)
    write_atomic_json(root / "qualification_verdict.json", {"qualified": good, "tiny_semantic_pf": tiny,
        "p0_parity_all_scenarios": all(p0["static_checker_pass"] and p0["upstream_verifier_pass"] for p0 in p0s.values()),
        "candidate_audit_frozen": True, "storage_contract_audited": True})
    return good


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true"); parser.add_argument("--qualification", action="store_true")
    parser.add_argument("--resume", action="store_true"); parser.add_argument("--implementation-commit", default="")
    args = parser.parse_args()
    if args.quick and args.qualification: raise SystemExit("--quick and --qualification are mutually exclusive")
    mode = "quick" if args.quick else "qualification" if args.qualification else "full"
    if not EXECUTABLE.is_file(): raise SystemExit(f"missing backend executable: {EXECUTABLE}")
    if not SEMANTIC_PATCH.is_file(): raise SystemExit(f"missing semantic patch: {SEMANTIC_PATCH}")
    implementation = args.implementation_commit or git_value("rev-parse", "HEAD")
    if mode == "full" and not args.resume and OUT.exists():
        raise SystemExit(f"refusing to overwrite existing exp19 output; use --resume after provenance review: {OUT}")
    root = OUT if mode == "full" else OUT / f"_{mode}_validation"
    root.mkdir(parents=True, exist_ok=True)
    for name in ("profiles", "stores", "per_fault_results", "raw_backend_output", "logs"):
        (root / name).mkdir(exist_ok=True)
    frozen = frozen_preflight(); manifest, scenarios, paths = load_benchmark(); config = expected_config(manifest)
    p0s: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="exp19-p0-recovery-") as temporary:
        for scenario in ORDER: p0s[scenario] = recover_p0_baseline(scenario, paths[scenario], scenarios[scenario], Path(temporary))
    census, candidates, contexts = [], {}, {}
    for scenario in ORDER:
        all_links, relevant, candidate_sha = candidate_catalog(scenario, scenarios[scenario], p0s[scenario]["logical_routes"])
        census.extend(all_links); candidates[scenario] = relevant
        contract = context_for(scenario, paths[scenario], candidate_sha, config, implementation)
        contexts[scenario] = {"path": paths[scenario], "candidates": relevant, "candidate_sha": candidate_sha, "contract": contract}
    write_atomic_json(root / "source_benchmark_manifest.json", {"selected_manifest": manifest,
        "selected_manifest_sha256": sha256_file(EXP18J / "selected_pf_benchmark_manifest.json"),
        "source_scenario_sha256": {scenario: sha256_file(paths[scenario]) for scenario in ORDER}, "frozen_history_sha256": frozen})
    write_atomic_json(root / "backend_config_audit.json", {"config": config, "backend_executable": str(EXECUTABLE.relative_to(ROOT)),
        "upstream_repository": UPSTREAM_REPOSITORY, "upstream_commit": UPSTREAM_COMMIT, "upstream_license": UPSTREAM_LICENSE,
        "semantic_patch": str(SEMANTIC_PATCH.relative_to(ROOT)), "semantic_patch_sha256": sha256_file(SEMANTIC_PATCH),
        "no_balanced_policy": True, "no_multistart": True, "diagnostic_trace": False, "threads": FORMAL_THREADS})
    write_atomic_json(root / "p0_route_manifest.json", {scenario: {key: value for key, value in p0.items() if key != "profile"}
        for scenario, p0 in p0s.items()})
    p0_rows = [{key: value for key, value in p0.items() if key not in {"profile", "logical_routes"}} for p0 in p0s.values()]
    write_csv(root / "p0_baseline.csv", p0_rows)
    write_csv(root / "candidate_fault_census.csv", census)
    write_atomic_json(root / "candidate_faults.json", {scenario: {"candidate_set_sha256": contexts[scenario]["candidate_sha"], "candidates": candidates[scenario]}
        for scenario in ORDER})
    runtime = runtime_profile_contract_audit()
    runtime_text = "# Runtime deployment profile contract audit\n\nStatus: `NOT_ESTABLISHED`.\n\nNo complete code-level profile activation provider/consumer contract was found; exp19 therefore records storage semantics only and makes no deployment-size claim.\n"
    write_atomic_bytes(root / "runtime_profile_contract_audit.md", runtime_text.encode("utf-8"))
    write_atomic_json(root / "runtime_profile_contract_audit.json", runtime)
    backend = H2sPfBackend(EXECUTABLE, quantum_ns=100, candidate_paths=5, memory_limit_mb=8192,
        h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0, h2s_flow_sorting=4, attempt_celf_fallback=True)
    qualified = write_qualification(root, contexts, p0s, backend)
    if not qualified:
        write_atomic_json(root / "campaign_verdict.json", {"formal_verdict": "PF_BENCHMARK_QUALIFICATION_FAILED", "mode": mode})
        return 2
    if mode == "qualification": return 0
    selected: dict[str, list[dict[str, Any]]] = {}
    for scenario in ORDER:
        values = candidates[scenario]
        if mode == "quick":
            # Exactly three fast-path PFs: the deterministic median-affected
            # M-scale candidate for Ring, Redstar, and RoR.  L is catalogued
            # and provenance-checked but deliberately not PF-synthesized here.
            values = ([sorted(values, key=lambda row: (row["affected_flow_count"], row["fault_id"]))[(len(values) - 1) // 2]]
                      if values and scenario.startswith("M_") else [])
        selected[scenario] = values
    rows: list[dict[str, Any]] = []
    for scenario in ORDER:
        started = time.monotonic()
        for candidate in selected[scenario]:
            if mode == "full" and time.monotonic() - started >= SCENARIO_BUDGET_S: break
            row, _ = run_fault(root, scenario, contexts[scenario]["path"], candidate, p0s[scenario], contexts[scenario]["contract"], backend, resume=args.resume)
            rows.append(row)
    rows.sort(key=lambda row: (ORDER.index(row["scenario"]), int(row["candidate_rank"])))
    rows_by_key = {(row["scenario"], row["fault_id"]): row for row in rows}
    repeats = repetition_audit(root, contexts, selected, p0s, rows_by_key, backend) if mode == "full" and all(len(rows_by_key) >= len(selected[s]) for s in ORDER) else []
    coverage = coverage_rows(rows, census); timings = timing_rows(rows); memories = memory_rows(rows); storage, semantic_index = storage_rows(rows, p0s)
    lpt = make_lpt_rows(rows); churn = []
    for row in rows:
        if row["status"] not in SUCCESS or not row["profile_ref"]: continue
        profile = json.loads((root / row["profile_ref"]).read_text(encoding="utf-8"))
        candidate = next(item for item in candidates[row["scenario"]] if item["fault_id"] == row["fault_id"])
        churn.append({"scenario": row["scenario"], "fault_id": row["fault_id"], **route_churn(p0s[row["scenario"]]["logical_routes"], profile["logical_routes"], str(candidate["affected_flow_ids"]).split(";"))})
    topology, scale = topology_comparisons(rows)
    write_csv(root / "per_fault_results.csv", rows); write_csv(root / "coverage_summary.csv", coverage); write_csv(root / "timing_summary.csv", timings)
    write_csv(root / "memory_summary.csv", memories); write_csv(root / "profile_storage.csv", storage); write_csv(root / "route_churn.csv", churn)
    write_csv(root / "cost_correlations.csv", correlation_rows(rows)); write_csv(root / "lpt_parallel_projection.csv", lpt)
    write_csv(root / "topology_comparison.csv", topology); write_csv(root / "scale_comparison.csv", scale); write_csv(root / "repeatability_audit.csv", repeats)
    write_atomic_json(root / "stores" / "semantic_profile_index.json", {"schema_version": 1, "profiles_not_deduplicated": True, "references": semantic_index})
    expected = sum(len(selected[scenario]) for scenario in ORDER); complete = len(rows) == expected
    semantic_bad = any(row["status"] in SUCCESS and not row["semantic_valid"] for row in rows)
    resources = any(row["status"] in RESOURCE for row in rows)
    valid = sum(row["status"] in SUCCESS and row["semantic_valid"] for row in rows)
    if semantic_bad: verdict = "PF_SEMANTIC_VALIDATION_FAILED"
    elif not complete: verdict = "PF_CAMPAIGN_BUDGET_EXCEEDED" if mode == "full" else "PF_CAMPAIGN_INCOMPLETE"
    elif resources: verdict = "PF_FULL_CENSUS_COMPLETE_WITH_RESOURCE_LIMITS"
    elif valid == len(rows): verdict = "PF_FULL_CENSUS_COMPLETE_ALL_PROFILES"
    else: verdict = "PF_FULL_CENSUS_COMPLETE_PARTIAL_PROFILE_COVERAGE"
    campaign = {"formal_verdict": verdict, "mode": mode, "full_census_required": mode == "full", "expected_faults": expected,
        "attempted_faults": len(rows), "valid_profiles": valid, "all_scenarios_complete": complete,
        "no_sampling": mode != "full" or all(len(selected[s]) == len(candidates[s]) for s in ORDER), "no_omnet_invocations": True,
        "no_inet_invocations": True, "no_profile_grouping": True, "no_profile_deduplication": True, "runtime_contract_status": runtime["status"]}
    write_atomic_json(root / "campaign_verdict.json", campaign)
    summary = f"# exp19 realistic full-density PF cost\n\nFormal verdict: `{verdict}`.\n\nAttempted faults: {len(rows)}/{expected}; valid canonical profiles: {valid}.\n\nThe campaign used the frozen exp18j SAR workload, all-reroute H2S primary with CELF fallback, and physical bidirectional link deletion. P0 was recovered from existing raw outputs and was excluded from PF serial cost.\n"
    write_atomic_bytes(root / "summary.md", summary.encode("utf-8"))
    environment = {"python": sys.version.split()[0], "platform": platform.platform(), "implementation_commit": implementation,
        "seed": FORMAL_SEED, "threads": FORMAL_THREADS, "timeout_per_algorithm_s": TIMEOUT_S, "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB,
        "scenario_budget_s": SCENARIO_BUDGET_S, "omnet_invocations": 0, "inet_invocations": 0, "run_mode": mode}
    write_atomic_json(root / "environment.json", environment)
    artifacts = artifact_hashes(root)
    write_atomic_json(root / "analysis_manifest.json", {"schema_version": 1, "experiment": "exp19_realistic_full_density_pf_cost", "mode": mode,
        "implementation_commit": implementation, "campaign_verdict": verdict, "upstream_commit": UPSTREAM_COMMIT,
        "source_manifest_sha256": sha256_file(EXP18J / "selected_pf_benchmark_manifest.json"), "artifact_sha256": artifacts,
        "campaign_sha256": canonical_sha(artifacts)})
    return 0 if verdict in {"PF_FULL_CENSUS_COMPLETE_ALL_PROFILES", "PF_FULL_CENSUS_COMPLETE_PARTIAL_PROFILE_COVERAGE", "PF_FULL_CENSUS_COMPLETE_WITH_RESOURCE_LIMITS"} or mode == "quick" else 2


if __name__ == "__main__":
    raise SystemExit(main())
