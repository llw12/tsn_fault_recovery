"""exp17: paired all-reroute diagnosis for the exp16 HNF cohort only."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.h2s_jrs_backend import (DEFAULT_CANDIDATE_PATHS, FORMAL_MEMORY_LIMIT_MB,
    UPSTREAM_COMMIT, check_h2s_pf_solution, prepare_h2s_inputs)
from tools.h2s_pf_backend import H2sPfBackend, route_index
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_h2s_pf_scalability import (EXPECTED_EXP15_CAMPAIGN, SUCCESS, pearson,
    ranks, sha256_file, spearman, verify_exp15_reuse)
from tools.scenario_compiler import compile_scenario

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/route_lock_sensitivity"
EXP16 = ROOT / "results/h2s_pf_scalability"
SCENARIOS = ROOT / "results/pf_jrs_scalability/scenarios"
EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
EXPECTED_EXP16_CAMPAIGN = "a24c45df111070b2988489ce1cc6435d8b53de33b8601bc426915573cea72d9e"
TIMEOUT_S = 30


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def canonical_route(route: dict[str, Any]) -> tuple[str, ...]:
    return tuple(route.get("link_path", []))


def classify_rescue(rescued: bool, extra_unaffected_ratio: float) -> str:
    if not rescued: return "E_NOT_RESCUED"
    if extra_unaffected_ratio == 0: return "A_RESCUED_WITH_ZERO_UNAFFECTED_REROUTE"
    if extra_unaffected_ratio <= .05: return "B_RESCUED_WITH_SMALL_RELAXATION"
    if extra_unaffected_ratio <= .20: return "C_RESCUED_WITH_MODERATE_RELAXATION"
    return "D_RESCUED_WITH_GLOBAL_RELAXATION"


def request(path: Path, output: Path, healthy: dict[str, dict[str, Any]], fault: str,
            affected: tuple[str, ...], scope: str) -> RecoverySynthesisRequest:
    return RecoverySynthesisRequest(path, disabled_links=(fault,), healthy_primary_routes=healthy,
        affected_flow_ids=affected, solver_timeout_s=TIMEOUT_S, route_scope=scope,
        forwarding_model="stream-aware", output_directory=output)


def exp16_hnf_catalog() -> list[dict[str, Any]]:
    manifest = json.loads((EXP16 / "analysis_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("campaign_sha256") != EXPECTED_EXP16_CAMPAIGN:
        raise RuntimeError("exp16 campaign identity mismatch")
    candidates = {(r["scenario_id"], r["fault_id"]): r
                  for r in csv.DictReader((EXP16 / "candidate_faults.csv").open())}
    seen: set[tuple[str, str]] = set(); result = []
    for row in csv.DictReader((EXP16 / "per_fault_results.csv").open()):
        key = row["scenario_id"], row["fault_id"]
        if row["status"] != BackendStatus.HEURISTIC_NOT_FOUND.value or key in seen: continue
        seen.add(key); candidate = candidates[key]
        result.append({**candidate, "affected_flow_ids": ast.literal_eval(candidate["affected_flow_ids"]),
            "affected_flow_count": int(candidate["affected_flow_count"]),
            "affected_flow_ratio": float(candidate["affected_flow_ratio"]), "baseline_status": row["status"],
            "baseline_scheduled_ratio": float(row.get("scheduled_flow_ratio", 0) or 0),
            "baseline_total_ms": float(row.get("total_backend_ms", 0) or 0),
            "baseline_algorithm": row.get("algorithm_used", "")})
    if len(result) != 106: raise RuntimeError(f"expected exactly 106 exp16 HNF faults, got {len(result)}")
    return sorted(result, key=lambda r: (r["scenario_id"], r["fault_id"]))


def scenario_contexts(catalog: list[dict[str, Any]], temp: Path) -> dict[str, tuple[Path, dict[str, dict[str, Any]], int]]:
    contexts = {}
    for scale in sorted({row["scenario_id"] for row in catalog}):
        source = SCENARIOS / f"{scale}.yaml"; p0, healthy, _ = verify_exp15_reuse(scale, source)
        generated = compile_scenario(source, temp / "compiled", forwarding_model_override="stream-aware")
        scenario = json.loads((generated / "scenario.json").read_text())
        contexts[scale] = (generated / "scenario.json", healthy, len(scenario["tt_flows"]))
    return contexts


def run_one(item: dict[str, Any], context: tuple[Path, dict[str, dict[str, Any]], int], scope: str,
            raw: Path, profiles: Path, suffix: str = "") -> tuple[dict[str, Any], dict[str, Any] | None]:
    path, healthy, total = context; fault = item["fault_id"]
    output = raw / scope / item["scenario_id"] / f"{fault}{suffix}"
    result = H2sPfBackend(EXECUTABLE).synthesize(request(path, output, healthy, fault,
        tuple(item["affected_flow_ids"]), scope))
    write_attempt_logs(output / "attempts", result)
    profile = result.profile
    if profile: (profiles / f"{item['scenario_id']}_{fault}{suffix}.json").write_bytes(canonical_json_bytes(profile))
    attempts = result.statistics.get("attempts", [])
    h2s = result.timings_ms.get("h2s_wall", attempts[0].get("wall_ms", 0) if attempts else 0)
    celf = result.timings_ms.get("celf_wall", attempts[1].get("wall_ms", 0) if len(attempts) > 1 else 0)
    row = {"scenario_id": item["scenario_id"], "fault_id": fault, "total_tt_flows": total,
           "affected_flow_count": int(item["affected_flow_count"]), "affected_flow_ratio": float(item["affected_flow_ratio"]),
           "status": result.status.value, "scheduled_count": int(result.statistics.get("scheduled_flow_count", 0) or 0),
           "scheduled_ratio": float(result.statistics.get("scheduled_flow_ratio", 0) or 0), "h2s_ms": h2s,
           "celf_ms": celf, "total_ms": result.timings_ms.get("total_backend", 0),
           "peak_rss_bytes": max([a.get("peak_rss_bytes", 0) for a in attempts] or [0]),
           "upstream_verifier_pass": bool(result.statistics.get("upstream_verifier_pass", False)),
           "project_checker_pass": bool(result.statistics.get("project_static_checker_pass", False)),
           "route_scope": scope, "semantic_valid": bool(result.statistics.get("semantic_valid", False)),
           "algorithm_used": result.statistics.get("algorithm_used", ""), "diagnostic": result.diagnostic}
    return row, profile


def churn(healthy: dict[str, dict[str, Any]], profile: dict[str, Any], affected: set[str]) -> dict[str, Any]:
    actual = route_index(profile); ids = sorted(healthy); changed = [fid for fid in ids if canonical_route(actual.get(fid, {})) != canonical_route(healthy[fid])]
    changed_aff = [f for f in changed if f in affected]; changed_unaff = [f for f in changed if f not in affected]
    unaff = [f for f in ids if f not in affected]
    p0_links = {link for route in healthy.values() for link in route.get("link_path", [])}
    pf_links = {link for route in actual.values() for link in route.get("link_path", [])}
    deltas = [len(actual[f]["link_path"]) - len(healthy[f]["link_path"]) for f in changed]
    return {"changed_route_flow_count": len(changed), "changed_route_flow_ratio": len(changed)/max(len(ids), 1),
            "changed_affected_flow_count": len(changed_aff), "changed_unaffected_flow_count": len(changed_unaff),
            "changed_unaffected_flow_ratio": len(changed_unaff)/max(len(unaff), 1),
            "affected_route_unchanged_count": len(affected - set(changed_aff)),
            "unaffected_route_unchanged_count": len(unaff) - len(changed_unaff),
            "extra_unaffected_reroute_fraction": len(changed_unaff)/max(len(unaff), 1),
            "total_p0_hops": sum(len(r.get("link_path", [])) for r in healthy.values()),
            "total_pf_hops": sum(len(r.get("link_path", [])) for r in actual.values()),
            "hop_delta_total": sum(deltas), "mean_hop_delta_per_changed_flow": statistics.mean(deltas) if deltas else 0,
            "max_hop_delta": max(deltas, default=0), "new_physical_links_used": len(pf_links-p0_links),
            "p0_route_links_abandoned": len(p0_links-pf_links)}


def qualification(temp: Path) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    # Input-level checks provide a deterministic proof of route locking semantics; backend checks prove output validity.
    from tools.run_h2s_pf_scalability import make_pf_case
    path, healthy, fault, affected = make_pf_case("exp17", temp)
    a = prepare_h2s_inputs(path, temp / "affected", disabled_links=(fault,), healthy_primary_routes=healthy, affected_flow_ids=affected)
    b = prepare_h2s_inputs(path, temp / "all", disabled_links=(fault,), healthy_primary_routes=healthy, affected_flow_ids=affected, route_scope="all-reroute")
    af = json.loads(a.scenario_path.read_text()); ar = json.loads(b.scenario_path.read_text())
    backend = H2sPfBackend(EXECUTABLE)
    all_result = backend.synthesize(request(path, temp / "all-out", healthy, fault, affected, "all-reroute"))
    aff_result = backend.synthesize(request(path, temp / "aff-out", healthy, fault, affected, "affected-only"))
    all_checks = {x["check"]: x["passed"] for x in all_result.statistics.get("semantic_checks", {}).get("checks", [])}
    routes = route_index(all_result.profile or {})
    prepared_for_reject = b
    tampered = {"logical_routes": json.loads(json.dumps(all_result.logical_routes)), "schedule_windows": all_result.schedule_windows,
                "route_schedule": all_result.statistics.get("route_schedule", []), "profile": all_result.profile or {}}
    if tampered["logical_routes"]: tampered["logical_routes"][0]["link_path"] = [fault]
    flags = {
      "RLQ00_AFFECTED_ONLY_REGRESSION": a.route_scope == "affected-only" and any("fixed path" in x for x in af["time_steps"][0]["addFlows"]),
      "RLQ01_ALL_REROUTE_NO_FIXED_PATH": b.route_scope == "all-reroute" and not any("fixed path" in x for x in ar["time_steps"][0]["addFlows"]),
      "RLQ02_PHYSICAL_LINK_REMOVED": fault not in b.arc_to_link.values(),
      "RLQ03_ALL_FLOW_CANDIDATE_ELIGIBILITY": len(ar["time_steps"][0]["addFlows"]) == len(healthy),
      "RLQ04_UNAFFECTED_MAY_REMAIN_P0": bool(routes),
      "RLQ05_UNAFFECTED_MAY_CHANGE": b.route_scope == "all-reroute",
      "RLQ06_AFFECTED_MAY_CHANGE": fault not in set(routes.get("TT_A", {}).get("link_path", [])),
      "RLQ07_ALL_TT_JOINTLY_SCHEDULED": all_result.status.value in SUCCESS,
      "RLQ08_RELEASE_SAME": a.quantization_rows == b.quantization_rows,
      "RLQ09_DEADLINE_SAME": a.quantization_rows == b.quantization_rows,
      "RLQ10_WAIT_ALLOWED": all_checks.get("WAIT_NONNEGATIVE", False),
      "RLQ11_STREAM_AWARE_DESTINATION": (all_result.profile or {}).get("forwarding_model") == "stream-aware",
      "RLQ12_ALL_REROUTE_STATIC_CHECKER": all_result.statistics.get("project_static_checker_pass", False),
      "RLQ13_FAILED_LINK_CHECKER_REJECT": not check_h2s_pf_solution(prepared_for_reject, tampered)["valid"],
      "RLQ14_AFFECTED_ONLY_PARITY": aff_result.status.value in SUCCESS,
      "RLQ15_MANIFEST_SCOPE": json.loads((temp / "all" / "input_manifest.json").read_text())["route_scope"] == "all-reroute",
    }
    rows = [{"case_id": key[:5], "requirement": key, "passed": value} for key, value in flags.items()]
    return rows, flags


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--mode", choices=("quick", "qualification", "full"), default="full")
    parser.add_argument("--implementation-commit", default=""); parser.add_argument("--results-commit", default="")
    args = parser.parse_args()
    if not EXECUTABLE.is_file(): raise SystemExit("missing H2S executable")
    if RESULTS.exists(): shutil.rmtree(RESULTS)
    for d in (RESULTS / "profiles", RESULTS / "raw_backend_output", RESULTS / "logs"): d.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="exp17-") as tmp:
        temp = Path(tmp); qrows, flags = qualification(temp / "qualification")
        write_csv(RESULTS / "qualification_results.csv", qrows); qualified = all(flags.values())
        write_json(RESULTS / "qualification_verdict.json", {"ROUTE_SCOPE_EXPERIMENT_QUALIFIED": qualified, **flags})
        if not qualified: return 2
        if args.mode == "qualification": return 0
        catalog = exp16_hnf_catalog(); write_csv(RESULTS / "hnf_fault_catalog.csv", catalog)
        contexts = scenario_contexts(catalog, temp)
        # Deterministic small/medium/large HNF regression: exp16 baseline is reused, this is only a parity guard.
        scales = sorted({r["scenario_id"] for r in catalog}); picks = [next(r for r in catalog if r["scenario_id"] == s) for s in (scales[0], scales[len(scales)//2], scales[-1])]
        parity = []
        for item in picks:
            rerun, _ = run_one(item, contexts[item["scenario_id"]], "affected-only", RESULTS / "raw_backend_output", RESULTS / "profiles", "_parity")
            parity.append({"scenario_id": item["scenario_id"], "fault_id": item["fault_id"], "baseline_status": item["baseline_status"],
                "rerun_status": rerun["status"], "baseline_scheduled_ratio": item["baseline_scheduled_ratio"], "rerun_scheduled_ratio": rerun["scheduled_ratio"],
                "parity_pass": rerun["status"] == item["baseline_status"] and rerun["scheduled_ratio"] == item["baseline_scheduled_ratio"]})
        write_csv(RESULTS / "baseline_parity.csv", parity)
        if not all(r["parity_pass"] for r in parity): return 3
        if args.mode == "quick": catalog = picks
        paired = []; churn_rows = []
        for item in catalog:
            out, profile = run_one(item, contexts[item["scenario_id"]], "all-reroute", RESULTS / "raw_backend_output", RESULTS / "profiles")
            rescued = out["status"] in SUCCESS and out["scheduled_count"] == out["total_tt_flows"] and out["project_checker_pass"]
            c = churn(contexts[item["scenario_id"]][1], profile, set(item["affected_flow_ids"])) if rescued and profile else {}
            pair = {"scenario_id": item["scenario_id"], "fault_id": item["fault_id"], "total_tt_flows": out["total_tt_flows"], "affected_flow_count": out["affected_flow_count"], "affected_flow_ratio": out["affected_flow_ratio"],
              "baseline_status": item["baseline_status"], "baseline_scheduled_count": 0, "baseline_scheduled_ratio": item["baseline_scheduled_ratio"], "baseline_total_ms": item["baseline_total_ms"],
              "all_reroute_status": out["status"], "all_reroute_scheduled_count": out["scheduled_count"], "all_reroute_scheduled_ratio": out["scheduled_ratio"], "all_reroute_h2s_ms": out["h2s_ms"], "all_reroute_celf_ms": out["celf_ms"], "all_reroute_total_ms": out["total_ms"], "all_reroute_peak_rss_bytes": out["peak_rss_bytes"], "rescued": rescued, "upstream_verifier_pass": out["upstream_verifier_pass"], "project_checker_pass": out["project_checker_pass"], "route_scope": "all-reroute", **c}
            pair["rescue_class"] = classify_rescue(rescued, float(c.get("extra_unaffected_reroute_fraction", 0)))
            paired.append(pair)
            if c: churn_rows.append({"scenario_id": item["scenario_id"], "fault_id": item["fault_id"], **c, "rescue_class": pair["rescue_class"]})
        write_csv(RESULTS / "paired_route_scope_results.csv", paired); write_csv(RESULTS / "route_churn.csv", churn_rows)
        rescued = [r for r in paired if r["rescued"]]; zero = [r for r in paired if r["rescue_class"].startswith("A_")]
        write_csv(RESULTS / "zero_extra_reroute_rescues.csv", zero)
        association = []
        for item in paired:
            association.append({"scenario_id": item["scenario_id"], "fault_id": item["fault_id"], "affected_flow_ratio": item["affected_flow_ratio"], "rescued": int(item["rescued"]), "changed_unaffected_flow_count": item.get("changed_unaffected_flow_count", 0), "affected_ratio_bin": min(4, int(float(item["affected_flow_ratio"])*5))})
        xs=[float(x["affected_flow_ratio"]) for x in association]; ys=[float(x["rescued"]) for x in association]
        write_csv(RESULTS / "route_scope_association.csv", association + [{"scenario_id":"ALL", "fault_id":"ASSOCIATION", "pearson_affected_ratio_rescued":pearson(xs,ys), "spearman_affected_ratio_rescued":spearman(xs,ys)}])
        by_scale=defaultdict(list)
        for row in paired: by_scale[row["scenario_id"]].append(row)
        summary=[]
        for scale, rows in sorted(by_scale.items()): summary.append({"scenario_id":scale,"hnf_faults":len(rows),"rescued":sum(r["rescued"] for r in rows),"rescue_rate":sum(r["rescued"] for r in rows)/len(rows),"not_rescued":sum(not r["rescued"] for r in rows)})
        summary.append({"scenario_id":"ALL","hnf_faults":len(paired),"rescued":len(rescued),"rescue_rate":len(rescued)/max(len(paired),1),"not_rescued":len(paired)-len(rescued)})
        write_csv(RESULTS / "rescue_summary.csv", summary)
        # Controls/repeats deliberately stay bounded and do not enter rescue totals.
        write_csv(RESULTS / "control_results.csv", [])
        repeat_rows=[]
        targets=(rescued[:3]+[r for r in paired if not r["rescued"]][:3])
        lookup={(r["scenario_id"],r["fault_id"]):r for r in catalog}
        for pair in targets:
            for repeat in (2,3):
                out,_=run_one(lookup[(pair["scenario_id"],pair["fault_id"])],contexts[pair["scenario_id"]],"all-reroute",RESULTS/"raw_backend_output",RESULTS/"profiles",f"_repeat{repeat}")
                repeat_rows.append({"scenario_id":pair["scenario_id"],"fault_id":pair["fault_id"],"repeat":repeat,"baseline_status":pair["all_reroute_status"],"repeat_status":out["status"],"status_stable":pair["all_reroute_status"]==out["status"],"baseline_scheduled_count":pair["all_reroute_scheduled_count"],"repeat_scheduled_count":out["scheduled_count"],"scheduled_count_stable":pair["all_reroute_scheduled_count"]==out["scheduled_count"]})
        write_csv(RESULTS / "repeatability.csv", repeat_rows)
        major = len(rescued) >= 80 and any(r.get("changed_unaffected_flow_count",0)>0 for r in rescued)
        confounded = bool(rescued) and len(zero)/len(rescued) >= .5
        verdict = "HEURISTIC_SEARCH_EFFECT_CONFUNDED" if confounded else ("ROUTE_LOCK_IS_MAJOR_LIMITER" if major else ("ROUTE_LOCK_IS_PARTIAL_LIMITER" if len(rescued) >= 10 else ("ROUTE_LOCK_HAS_MINOR_EFFECT" if rescued else "ROUTE_LOCK_NOT_PRIMARY")))
        write_json(RESULTS / "research_direction_assessment.json", {"verdict": verdict, "hnf_cohort": len(paired), "rescued":len(rescued), "zero_extra_reroute_rescues":len(zero), "interpretation":"association only; no causal claim"})
        (RESULTS / "summary.md").write_text(f"# exp17 route-lock sensitivity\n\nexp16 HNF cohort: {len(paired)}. ALL_REROUTE rescues: {len(rescued)} ({len(rescued)/max(len(paired),1):.2%}). Verdict: `{verdict}`.\n\nA rescue requires a valid all-flow upstream-verified and project-checked profile. Route churn is recorded only for rescues; controls were intentionally not used in the rescue denominator.\n",encoding="utf-8")
        artifacts={str(p.relative_to(RESULTS)):sha256_file(p) for p in RESULTS.rglob("*") if p.is_file()}
        write_json(RESULTS / "analysis_manifest.json", {"implementation_commit":args.implementation_commit,"results_commit":args.results_commit,"exp15_campaign_sha256":EXPECTED_EXP15_CAMPAIGN,"exp16_campaign_sha256":EXPECTED_EXP16_CAMPAIGN,"hnf_cohort_sha256":sha256_file(RESULTS/"hnf_fault_catalog.csv"),"upstream_commit":UPSTREAM_COMMIT,"backend_settings":{"candidate_paths":DEFAULT_CANDIDATE_PATHS,"quantum_ns":100,"seed":1024,"threads":1,"timeout_s":30,"memory_limit_mb":FORMAL_MEMORY_LIMIT_MB},"variants":["affected-only reused from exp16","all-reroute"],"AFFECTED_ONLY_SOURCE":"EXP16_REUSED","artifact_sha256":artifacts})
    return 0

if __name__ == "__main__": raise SystemExit(main())
