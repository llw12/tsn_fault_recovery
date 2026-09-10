"""exp21 formal runner: schedulability-pruned scenario-local fault grouping."""
from __future__ import annotations

import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import os
import platform
import random
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.fault_grouping_search import ALGORITHM_VERSION, build_affected_set_pools, group_id, run_grouping_search
from tools.fixed_release_collision_audit import ROOT, sha256_file, tree_sha256
from tools.h2s_group_recovery_backend import H2sGroupRecoveryBackend
from tools.h2s_jrs_backend import (
    UPSTREAM_COMMIT, UPSTREAM_LICENSE, UPSTREAM_REPOSITORY, check_h2s_pf_solution,
    normalize_schedule, parse_backend_output, prepare_h2s_inputs,
)
from tools.h2s_pf_backend import H2sPfBackend, semantic_profile_hash
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.realistic_full_density_pf_cost import (
    ORDER, benchmark_config, candidate_catalog, gzip_bytes, load_benchmark,
    profile_storage, recover_p0_baseline, write_atomic_bytes, write_atomic_json,
)
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.schedulability_necessary_conditions import (
    HYPERPERIOD_TICKS, certificate_prunes, critical_window_exhaustive,
    critical_window_sweep, evaluate_necessary_conditions,
)

OUT = ROOT / "results/schedulability_pruned_fault_grouping"
EXP19 = ROOT / "results/realistic_full_density_pf_cost"
EXP20 = ROOT / "results/pf_redundancy_opportunity_audit"
EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
SUCCESS = {BackendStatus.SUCCESS_H2S.value, BackendStatus.SUCCESS_CELF_FALLBACK.value}
TIMEOUT_S = 30
SCENARIO_BUDGET_S = 3 * 60 * 60
FORMAL_VERDICTS = (
    "SCHEDULABILITY_PRUNED_GROUPING_ESTABLISHED",
    "NO_PROFILE_REDUCTION_ESTABLISHED",
    "SEARCH_INCOMPLETE",
    "INPUT_PARITY_FAILED",
    "GROUP_SINGLETON_REPLAY_CONTRADICTION",
    "REPEATABILITY_FAILED",
)
REQUIRED_OUTPUTS = (
    "environment.json", "source_manifest.json", "algorithm_config.json", "necessary_condition_qualification.json",
    "input_parity.csv", "affected_set_pools.csv", "merge_candidates.csv.gz", "merge_funnel_summary.csv",
    "filter_timing_summary.csv", "failure_certificates.json", "checked_cut_summary.csv.gz",
    "time_window_witnesses.csv.gz", "group_synthesis_attempts.csv", "accepted_merge_history.csv",
    "final_fault_groups.csv", "fault_to_group_profile.json", "coverage_validation.csv",
    "singleton_replay_validation.csv", "repeatability.csv", "compute_cost_comparison.csv",
    "storage_comparison.csv", "posthoc_exp20_comparison.csv", "algorithm_verdict.json",
    "summary.md", "analysis_manifest.json",
)


class Exp21Error(RuntimeError): pass


def csv_bytes(rows: list[dict[str, Any]], fields: list[str] | None = None) -> bytes:
    fields = fields or sorted({key for row in rows for key in row})
    stream = io.StringIO(newline=""); writer = csv.DictWriter(stream, fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader(); writer.writerows(rows); return stream.getvalue().encode()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    write_atomic_bytes(path, csv_bytes(rows, fields))


def write_gzip_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = csv_bytes(rows); buffer = io.BytesIO()
    with gzip.GzipFile(filename=path.name.removesuffix(".gz"), mode="wb", fileobj=buffer, mtime=0) as stream: stream.write(payload)
    write_atomic_bytes(path, buffer.getvalue())


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _candidate_source() -> dict[str, Any]:
    return json.loads((EXP19 / "candidate_faults.json").read_text(encoding="utf-8"))


def input_parity(manifest: dict[str, Any], scenarios: dict[str, dict[str, Any]], paths: dict[str, Path], work: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    source, p0, candidates, rows = _candidate_source(), {}, {}, []
    for scenario_id in ORDER:
        baseline = recover_p0_baseline(scenario_id, paths[scenario_id], scenarios[scenario_id], work / scenario_id)
        _, recomputed, digest = candidate_catalog(scenario_id, scenarios[scenario_id], baseline["logical_routes"])
        recorded = source[scenario_id]
        pass_row = digest == recorded["candidate_set_sha256"] and recomputed == recorded["candidates"]
        rows.append({"scenario": scenario_id, "scenario_sha256": sha256_file(paths[scenario_id]),
                     "p0_route_hash": baseline["route_hash"], "candidate_set_sha256": digest,
                     "candidate_count": len(recomputed), "recorded_candidate_count": len(recorded["candidates"]),
                     "affected_set_exact_parity": pass_row})
        if not pass_row: raise Exp21Error(f"INPUT_PARITY_FAILED:{scenario_id}")
        p0[scenario_id], candidates[scenario_id] = baseline, recomputed
    return p0, candidates, rows


def _toy_scenario() -> dict[str, Any]:
    return {"nodes": [{"id": x, "type": "switch" if x in "AB" else "device"} for x in "SABT"],
            "links": [{"id": "e1", "endpoint_a": "S", "endpoint_b": "A", "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0},
                      {"id": "e2", "endpoint_a": "A", "endpoint_b": "B", "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0},
                      {"id": "e3", "endpoint_a": "B", "endpoint_b": "T", "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0}],
            "tt_flows": [{"id": "F", "source": "S", "destination": "T", "period_s": .001,
                          "release_offset_s": 0, "schedule_deadline_budget_s": .0001,
                          "packet_size_bytes": 64}], "scheduling": {"frame_overhead_bytes": 64}}


def pure_qualification() -> dict[str, Any]:
    toy = _toy_scenario(); connected = evaluate_necessary_conditions(toy, (), ("F",)); disconnected = evaluate_necessary_conditions(toy, ("e2",), ("F",))
    rng = random.Random(1024); parity = []
    for case in range(100):
        horizon = 20; jobs = []
        for index in range(rng.randrange(0, 12)):
            early = rng.randrange(horizon); late = rng.randrange(early + 1, early + horizon + 1)
            jobs.append({"packet_id": f"j{index}", "E": early, "L": late, "p": rng.randrange(1, 5)})
        fast, slow = critical_window_sweep(jobs, rng.randrange(1, 4), horizon), critical_window_exhaustive(jobs, rng.randrange(1, 4), horizon)
        # Recompute with one shared machine count: random calls above are deliberately not reused.
        machines = 1 + (case % 3); fast, slow = critical_window_sweep(jobs, machines, horizon), critical_window_exhaustive(jobs, machines, horizon)
        parity.append(fast["passed"] == slow["passed"] and fast["max_overload_ticks"] == slow["max_overload_ticks"])
    certificate = disconnected["failure_certificate"]
    return {"algorithm_version": ALGORITHM_VERSION, "toy_connected_pipeline_pass": connected["passed"],
            "toy_connectivity_failure": disconnected["first_failure"] == "F1_CONNECTIVITY",
            "certificate_exact_hit": certificate_prunes(certificate, ("e2",)),
            "certificate_superset_hit": certificate_prunes(certificate, ("e2", "e9")),
            "certificate_non_superset_miss": not certificate_prunes(certificate, ("e9",)),
            "segment_tree_parity_cases": len(parity), "segment_tree_parity_passed": sum(parity),
            "pure_qualification_pass": connected["passed"] and all(parity)}


def live_backend_qualification(work: Path) -> dict[str, Any]:
    """Exercise one real two-fault union and its two singleton replays."""
    manifest, scenarios, paths = load_benchmark(); scenario_id = "M_REDSTAR"
    p0 = recover_p0_baseline(scenario_id, paths[scenario_id], scenarios[scenario_id], work / "p0")
    _, candidates, _ = candidate_catalog(scenario_id, scenarios[scenario_id], p0["logical_routes"])
    pool = next(row for row in build_affected_set_pools(candidates) if len(row["fault_ids"]) > 1)
    faults, affected = tuple(pool["fault_ids"][:2]), tuple(pool["affected_flow_ids"])
    filtered = evaluate_necessary_conditions(scenarios[scenario_id], faults, affected)
    if not filtered["passed"]:
        return {"real_scenario": scenario_id, "real_fault_ids": list(faults), "real_filter_pass": False,
                "live_backend_qualification_pass": False, "first_failure": filtered["first_failure"]}
    backend = H2sGroupRecoveryBackend(EXECUTABLE, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0,
                                      h2s_flow_sorting=4, attempt_celf_fallback=True)
    result = backend.synthesize(RecoverySynthesisRequest(paths[scenario_id], disabled_links=faults,
        healthy_primary_routes={row["flow_id"]: row for row in p0["logical_routes"]}, affected_flow_ids=affected,
        solver_timeout_s=TIMEOUT_S, route_scope="all-reroute", forwarding_model="stream-aware",
        output_directory=work / "backend/input"))
    write_attempt_logs(work / "backend/logs", result)
    replay = singleton_replay(result, paths[scenario_id], p0, faults, affected, work / "replay") if result.status.value in SUCCESS else []
    passed = result.status.value in SUCCESS and bool(result.profile) and len(replay) == 2 and all(row["singleton_replay_valid"] for row in replay)
    return {"real_scenario": scenario_id, "real_fault_ids": list(faults), "real_affected_flow_count": len(affected),
            "real_filter_pass": True, "multi_disabled_backend_status": result.status.value,
            "multi_disabled_semantic_hash": semantic_profile_hash(result.profile) if result.profile else "",
            "singleton_replay_count": len(replay), "singleton_replay_pass": bool(replay) and all(row["singleton_replay_valid"] for row in replay),
            "live_backend_qualification_pass": passed}


def _rebind_raw(raw: dict[str, Any], prepared: Any) -> dict[str, Any]:
    rebound = copy.deepcopy(raw)
    for slot in rebound.get("slots", []):
        arc = (int(slot["source"]), int(slot["destination"]))
        if arc not in prepared.queue_by_arc: raise Exp21Error(f"GROUP_SINGLETON_REPLAY_CONTRADICTION:DISABLED_ARC:{arc}")
        slot["queue_id"] = prepared.queue_by_arc[arc]
    return rebound


def singleton_replay(result: Any, scenario_path: Path, p0: dict[str, Any], faults: tuple[str, ...], affected: tuple[str, ...], root: Path) -> list[dict[str, Any]]:
    attempts = result.statistics.get("attempts", []); used = result.statistics.get("algorithm_used")
    attempt = next(row for row in attempts if row.get("algorithm") == used)
    raw = parse_backend_output(attempt["stdout"]); rows = []
    for fault in faults:
        prepared = prepare_h2s_inputs(scenario_path, root / fault, 100, 5, disabled_links=(fault,),
            healthy_primary_routes={row["flow_id"]: row for row in p0["logical_routes"]},
            affected_flow_ids=affected, route_scope="all-reroute")
        normalized = normalize_schedule(prepared, _rebind_raw(raw, prepared), 100); checker = check_h2s_pf_solution(prepared, normalized)
        valid = bool(raw["upstream_verifier_pass"] and checker["valid"])
        rows.append({"fault_id": fault, "group_id": group_id(faults), "upstream_verifier_pass": raw["upstream_verifier_pass"],
                     "static_checker_pass": checker["valid"], "singleton_replay_valid": valid,
                     "semantic_profile_hash": semantic_profile_hash(normalized["profile"])})
        if not valid: raise Exp21Error(f"GROUP_SINGLETON_REPLAY_CONTRADICTION:{fault}")
    return rows


def backend_result_row(scenario: str, faults: tuple[str, ...], result: Any, replay: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = result.statistics.get("attempts", []); peak = max([int(row.get("peak_rss_bytes", 0)) for row in attempts] or [0])
    return {"scenario": scenario, "group_id": group_id(faults), "fault_ids": ";".join(faults), "group_size": len(faults),
            "status": result.status.value, "accepted": result.status.value in SUCCESS and bool(result.profile) and all(row["singleton_replay_valid"] for row in replay),
            "algorithm_used": result.statistics.get("algorithm_used", ""), "semantic_valid": result.statistics.get("semantic_valid", False),
            "singleton_replay_pass": all(row["singleton_replay_valid"] for row in replay),
            "semantic_profile_hash": semantic_profile_hash(result.profile) if result.profile else "",
            "total_backend_ms": result.timings_ms.get("total_backend", 0), "peak_rss_bytes": peak, "diagnostic": result.diagnostic}


def run_campaign(output: Path, quick: bool = False, resume: bool = False) -> dict[str, Any]:
    if output.resolve() == OUT.resolve() and not quick and not resume and git("status", "--porcelain"):
        raise Exp21Error("FORMAL_RUN_REQUIRES_CLEAN_IMPLEMENTATION_COMMIT")
    frozen_history_before = {"exp19": tree_sha256(EXP19), "exp20": tree_sha256(EXP20)}
    output.mkdir(parents=True, exist_ok=True)
    for directory in ("profiles", "checkpoints", "raw_backend_output", "logs"):
        (output / directory).mkdir(parents=True, exist_ok=True)
    implementation = git("rev-parse", "HEAD"); manifest, scenarios, paths = load_benchmark()
    config = benchmark_config(manifest); p0, candidates, parity_rows = input_parity(manifest, scenarios, paths, output / "checkpoints/p0")
    contract = {"algorithm_version": ALGORITHM_VERSION, "implementation_commit": implementation,
        "scenario_sha256": {scenario: sha256_file(paths[scenario]) for scenario in ORDER},
        "candidate_catalog_sha256": sha256_file(EXP19 / "candidate_faults.json"),
        "p0_route_hash": {scenario: p0[scenario]["route_hash"] for scenario in ORDER},
        "backend_config_sha256": canonical_sha(config),
        "filter_implementation_sha256": sha256_file(ROOT / "tools/schedulability_necessary_conditions.py")}
    contract_path = output / "checkpoints/resume_contract.json"
    if resume and contract_path.is_file():
        recorded = json.loads(contract_path.read_text(encoding="utf-8"))
        if recorded != contract: raise Exp21Error("RESUME_CONTRACT_MISMATCH")
    write_atomic_json(contract_path, contract)
    qualification = pure_qualification()
    qualification.update(live_backend_qualification(output / "checkpoints/qualification"))
    qualification["qualification_pass"] = qualification["pure_qualification_pass"] and qualification["live_backend_qualification_pass"]
    if not qualification["qualification_pass"]: raise Exp21Error("NECESSARY_CONDITION_QUALIFICATION_FAILED")
    group_backend = H2sGroupRecoveryBackend(EXECUTABLE, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0,
                                             h2s_flow_sorting=4, attempt_celf_fallback=True)
    singleton_backend = H2sPfBackend(EXECUTABLE, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0,
                                     h2s_flow_sorting=4, attempt_celf_fallback=True)
    pool_rows: list[dict[str, Any]] = []; candidate_rows: list[dict[str, Any]] = []; cut_rows = []; witness_rows = []
    synthesis_rows = []; replay_rows = []; history_rows = []; certificates = []; final_groups = []; final_profiles = {}
    scenario_ids = ("M_RING",) if quick else ORDER
    for scenario_id in scenario_ids:
        started = time.monotonic(); affected_by_fault = {str(row["fault_id"]): tuple(str(row["affected_flow_ids"]).split(";")) for row in candidates[scenario_id]}
        pools = build_affected_set_pools(candidates[scenario_id]); selected = pools
        if quick:
            selected = [pool for pool in pools if len(pool["fault_ids"]) > 1][:3]
        for pool in pools:
            pool_rows.append({"scenario": scenario_id, "affected_set_key": pool["affected_set_key"],
                              "affected_flow_count": len(pool["affected_flow_ids"]), "fault_count": len(pool["fault_ids"]),
                              "fault_ids": ";".join(pool["fault_ids"]), "selected_by_mode": pool in selected})
        for pool in selected:
            affected = tuple(pool["affected_flow_ids"])
            def evaluate(faults: tuple[str, ...]) -> dict[str, Any]:
                result = evaluate_necessary_conditions(scenarios[scenario_id], faults, affected)
                f3, f4 = result["stages"].get("F3_CHECKED_CUT_CAPACITY", {}), result["stages"].get("F4_TIME_WINDOW", {})
                cut_rows.extend({"scenario": scenario_id, "group_id": group_id(faults), **row} for row in f3.get("rows", []))
                witness_rows.extend({"scenario": scenario_id, "group_id": group_id(faults), **row} for row in f4.get("rows", []))
                return result
            def synthesize(faults: tuple[str, ...]) -> dict[str, Any]:
                raw_root = output / "raw_backend_output" / scenario_id / group_id(faults)
                request = RecoverySynthesisRequest(paths[scenario_id], disabled_links=faults,
                    healthy_primary_routes={row["flow_id"]: row for row in p0[scenario_id]["logical_routes"]},
                    affected_flow_ids=affected, solver_timeout_s=TIMEOUT_S, route_scope="all-reroute",
                    forwarding_model="stream-aware", output_directory=raw_root / "input")
                result = group_backend.synthesize(request); write_attempt_logs(raw_root / "logs", result)
                replay = singleton_replay(result, paths[scenario_id], p0[scenario_id], faults, affected,
                    output / "checkpoints/replay" / scenario_id / group_id(faults)) if result.status.value in SUCCESS else []
                replay_rows.extend({"scenario": scenario_id, **row} for row in replay)
                row = backend_result_row(scenario_id, faults, result, replay); synthesis_rows.append(row)
                return {**row, "profile": result.profile if row["accepted"] else None}
            search = run_grouping_search(pool["fault_ids"], evaluate, synthesize)
            candidate_rows.extend({"scenario": scenario_id, "affected_set_key": pool["affected_set_key"], **row} for row in search["merge_candidates"])
            history_rows.extend({"scenario": scenario_id, "affected_set_key": pool["affected_set_key"], **row} for row in search["accepted_merge_history"])
            certificates.extend({"scenario": scenario_id, **row} for row in search["failure_certificates"])
            for row in search["final_groups"]:
                final_groups.append({"scenario": scenario_id, "affected_set_key": pool["affected_set_key"], **row})
                if row["profile"]: final_profiles[(scenario_id, row["group_id"])] = row["profile"]
        if time.monotonic() - started > SCENARIO_BUDGET_S: raise Exp21Error(f"SEARCH_INCOMPLETE:{scenario_id}:BUDGET")
    # Quick mode intentionally records that M_RING contains no multi-fault affected-set pools.
    if quick and not final_groups:
        qualification["quick_multi_pool_count"] = 0; qualification["quick_note"] = "M_RING_HAS_NO_MULTI_FAULT_AFFECTED_SET_POOL"
    # In formal mode, every untouched singleton pool is also a final group.
    if not quick:
        present = {(row["scenario"], fault) for row in final_groups for fault in row["fault_ids"]}
        for scenario_id in ORDER:
            for candidate in candidates[scenario_id]:
                fault = str(candidate["fault_id"])
                if (scenario_id, fault) not in present:
                    final_groups.append({"scenario": scenario_id, "affected_set_key": build_affected_set_pools([candidate])[0]["affected_set_key"],
                                         "group_id": group_id((fault,)), "fault_ids": [fault], "group_size": 1, "profile": None})
    singleton_parity = []
    if not quick:
        exp19_rows = {(row["scenario"], row["fault_id"]): row for row in csv.DictReader((EXP19 / "per_fault_results.csv").open())}
        for group in sorted(final_groups, key=lambda row: (ORDER.index(row["scenario"]), row["group_id"])):
            if group["group_size"] != 1: continue
            scenario_id, fault = group["scenario"], group["fault_ids"][0]; affected = affected_by = next(
                tuple(str(row["affected_flow_ids"]).split(";")) for row in candidates[scenario_id] if row["fault_id"] == fault)
            raw_root = output / "raw_backend_output" / scenario_id / group["group_id"] / "final_singleton"
            result = singleton_backend.synthesize(RecoverySynthesisRequest(paths[scenario_id], disabled_links=(fault,),
                healthy_primary_routes={row["flow_id"]: row for row in p0[scenario_id]["logical_routes"]}, affected_flow_ids=affected,
                solver_timeout_s=TIMEOUT_S, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=raw_root / "input"))
            write_attempt_logs(raw_root / "logs", result); expected = exp19_rows[(scenario_id, fault)]
            semantic = semantic_profile_hash(result.profile) if result.profile else ""
            parity = result.status.value == expected["status"] and semantic == expected["semantic_profile_hash"]
            singleton_parity.append({"scenario": scenario_id, "fault_id": fault, "status": result.status.value,
                                     "exp19_status": expected["status"], "semantic_profile_hash": semantic,
                                     "exp19_semantic_profile_hash": expected["semantic_profile_hash"], "exact_parity": parity,
                                     "total_backend_ms": result.timings_ms.get("total_backend", 0)})
            if result.status.value not in SUCCESS or not result.profile: raise Exp21Error(f"FINAL_SINGLETON_SYNTHESIS_FAILED:{scenario_id}:{fault}")
            final_profiles[(scenario_id, group["group_id"])] = result.profile
    # Freeze algorithm result before reading exp20 hindsight artifacts.
    frozen_group_sha = canonical_sha([{k: v for k, v in row.items() if k != "profile"} for row in final_groups])
    mappings, coverage, storage_rows = {}, [], []
    for group in final_groups:
        scenario_id, gid = group["scenario"], group["group_id"]; profile = final_profiles.get((scenario_id, gid))
        if not profile: continue
        profile_path = output / "profiles" / scenario_id / f"{gid}.json"; write_atomic_json(profile_path, profile)
        write_atomic_bytes(output / "profiles" / scenario_id / f"{gid}.json.gz", gzip_bytes(canonical_json_bytes(profile)))
        stored = profile_storage(profile); storage_rows.append({"scenario": scenario_id, "group_id": gid, "group_size": group["group_size"], **stored})
        for fault in group["fault_ids"]: mappings[f"{scenario_id}:{fault}"] = {"scenario": scenario_id, "fault_id": fault, "group_id": gid,
            "profile_ref": str(profile_path.relative_to(output)), "semantic_profile_hash": semantic_profile_hash(profile)}
    expected_faults = sum(len(rows) for rows in candidates.values()) if not quick else 0
    mapped_faults = len(mappings); coverage.append({"scope": "FORMAL" if not quick else "QUICK", "expected_faults": expected_faults,
        "mapped_faults": mapped_faults, "valid_profile_groups": len(final_profiles), "coverage_complete": mapped_faults == expected_faults})
    repeat_rows = []
    # Replay every filter/search decision from scratch while replacing actual
    # synthesis with the already frozen exact-group backend cache.
    synthesis_cache = {row["group_id"]: row for row in synthesis_rows}
    for scenario_id in scenario_ids:
        scenario_match = True; pool_count = 0
        for pool in build_affected_set_pools(candidates[scenario_id]):
            if len(pool["fault_ids"]) < 2: continue
            pool_count += 1; affected = tuple(pool["affected_flow_ids"])
            replay_search = run_grouping_search(pool["fault_ids"],
                lambda faults, sid=scenario_id, affected_ids=affected: evaluate_necessary_conditions(scenarios[sid], faults, affected_ids),
                lambda faults: {"accepted": bool(synthesis_cache.get(group_id(faults), {}).get("accepted")),
                                "profile": {}, "semantic_profile_hash": synthesis_cache.get(group_id(faults), {}).get("semantic_profile_hash", "")})
            expected = {row["group_id"] for row in final_groups if row["scenario"] == scenario_id and row["affected_set_key"] == pool["affected_set_key"]}
            observed = {row["group_id"] for row in replay_search["final_groups"]}
            scenario_match &= expected == observed
        repeat_rows.append({"scenario": scenario_id, "check_type": "DECISION_REPLAY", "pool_count": pool_count,
                            "decision_replay_match": scenario_match, "backend_repeat_required": False,
                            "backend_repeat_match": True})
    for scenario_id in scenario_ids:
        choices = sorted([row for row in final_groups if row["scenario"] == scenario_id and row["group_size"] > 1], key=lambda row: (row["group_size"], row["group_id"]))
        subset = []
        for index in sorted({0, len(choices)//2, len(choices)-1}) if choices else []:
            if choices[index] not in subset: subset.append(choices[index])
        for row in subset:
            faults = tuple(row["fault_ids"]); candidate = next(item for item in candidates[scenario_id] if item["fault_id"] == faults[0])
            affected = tuple(str(candidate["affected_flow_ids"]).split(";")); raw_root = output / "raw_backend_output/repeatability" / scenario_id / row["group_id"]
            result = group_backend.synthesize(RecoverySynthesisRequest(paths[scenario_id], disabled_links=faults,
                healthy_primary_routes={item["flow_id"]: item for item in p0[scenario_id]["logical_routes"]}, affected_flow_ids=affected,
                solver_timeout_s=TIMEOUT_S, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=raw_root / "input"))
            write_attempt_logs(raw_root / "logs", result)
            replay = singleton_replay(result, paths[scenario_id], p0[scenario_id], faults, affected,
                output / "checkpoints/repeatability" / scenario_id / row["group_id"]) if result.status.value in SUCCESS else []
            expected_profile = final_profiles[(scenario_id, row["group_id"])]
            semantic_match = bool(result.profile) and semantic_profile_hash(result.profile) == semantic_profile_hash(expected_profile)
            replay_match = len(replay) == len(faults) and all(item["singleton_replay_valid"] for item in replay)
            repeat_rows.append({"scenario": scenario_id, "check_type": "BACKEND_RESYNTHESIS", "group_id": row["group_id"], "group_size": row["group_size"],
                "selection": "MIN_MEDIAN_MAX_GROUP_SIZE", "decision_replay_match": True, "backend_repeat_required": True,
                "status": result.status.value, "status_match": result.status.value in SUCCESS,
                "semantic_hash_match": semantic_match, "checker_and_singleton_replay_match": replay_match,
                "backend_repeat_match": result.status.value in SUCCESS and semantic_match and replay_match,
                "total_backend_ms": result.timings_ms.get("total_backend", 0)})
    repeatability_pass = all(row["decision_replay_match"] and row["backend_repeat_match"] for row in repeat_rows)
    profile_count = len(final_groups); exp19_count = 1099
    exp19_backend_ms = sum(float(row["total_backend_ms"]) for row in csv.DictReader((EXP19 / "per_fault_results.csv").open()))
    filter_and_candidate_ms = sum(float(row.get("filter_total_ms", 0)) for row in candidate_rows)
    current_backend_ms = sum(float(row.get("total_backend_ms", 0)) for row in synthesis_rows) + sum(float(row["total_backend_ms"]) for row in singleton_parity)
    current_total_ms = filter_and_candidate_ms + current_backend_ms
    compute_rows = [{"baseline": "exp19_per_fault", "baseline_profile_count": exp19_count, "exp21_profile_count": profile_count,
                     "baseline_backend_ms": exp19_backend_ms, "exp21_filter_candidate_certificate_ms": filter_and_candidate_ms,
                     "exp21_merge_and_singleton_backend_ms": current_backend_ms, "exp21_total_algorithm_ms": current_total_ms,
                     "compute_reduction_fraction": (exp19_backend_ms - current_total_ms) / exp19_backend_ms}]
    exp20_verdict = json.loads((EXP20 / "audit_verdict.json").read_text())
    posthoc = [{"comparison": "EXP19_PER_FAULT", "profile_count": 1099}, {"comparison": "EXP20_S1", "profile_count": 1008},
               {"comparison": "EXP20_S2", "profile_count": 986}, {"comparison": "EXP20_S3", "profile_count": 823},
               {"comparison": "EXP21_SPFG", "profile_count": profile_count, "frozen_group_sha256": frozen_group_sha,
                "exp20_verdict": exp20_verdict.get("formal_verdict", "")}]
    funnel = [{"stage": stage, "count": count} for stage, count in Counter(
        row.get("first_failure") or ("FILTER_PASS" if row.get("filter_pass") else "UNKNOWN") for row in candidate_rows).items()]
    timing = []
    for stage in ("F1_CONNECTIVITY", "F2_MINIMUM_DELAY", "F3_CHECKED_CUT_CAPACITY", "F4_TIME_WINDOW"):
        field = f"filter_{stage.lower()}_ms"
        values = [float(row[field]) for row in candidate_rows if row.get(field) is not None]
        timing.append({"stage": stage, "candidate_count": len(values), "aggregate_stage_ms": sum(values),
                       "mean_stage_ms": sum(values) / len(values) if values else 0,
                       "max_stage_ms": max(values, default=0)})
    verdict = "SEARCH_INCOMPLETE" if quick else ("SCHEDULABILITY_PRUNED_GROUPING_ESTABLISHED" if mapped_faults == 1099 and profile_count < 1099 and repeatability_pass else "NO_PROFILE_REDUCTION_ESTABLISHED")
    verdict_payload = {"formal_verdict": verdict, "verdict_enums": FORMAL_VERDICTS, "algorithm_version": ALGORITHM_VERSION,
        "expected_faults": 1099, "mapped_faults": mapped_faults, "final_profile_count": profile_count,
        "profile_reduction": 1099 - profile_count, "search_complete": not quick, "input_parity_pass": all(row["affected_set_exact_parity"] for row in parity_rows),
        "all_final_profiles_valid": len(final_profiles) == profile_count, "singleton_replay_pass": all(row["singleton_replay_valid"] for row in replay_rows),
        "repeatability_pass": repeatability_pass, "runtime_profile_contract": "NOT_ESTABLISHED", "frozen_group_sha256": frozen_group_sha}
    frozen_history_after = {"exp19": tree_sha256(EXP19), "exp20": tree_sha256(EXP20)}
    if frozen_history_after != frozen_history_before: raise Exp21Error("FROZEN_HISTORY_CHANGED")
    write_atomic_json(output / "environment.json", {"platform": platform.platform(), "python": platform.python_version(), "implementation_commit": implementation,
        "upstream_repository": UPSTREAM_REPOSITORY, "upstream_commit": UPSTREAM_COMMIT, "upstream_license": UPSTREAM_LICENSE})
    write_atomic_json(output / "source_manifest.json", {"scenario_sha256": {s: sha256_file(paths[s]) for s in ORDER},
        "candidate_catalog_sha256": sha256_file(EXP19 / "candidate_faults.json"), "implementation_commit": implementation,
        "frozen_history_tree_sha256": frozen_history_before, "resume_contract": contract})
    write_atomic_json(output / "algorithm_config.json", {**config, "algorithm_version": ALGORITHM_VERSION, "scenario_budget_s": SCENARIO_BUDGET_S,
        "ranking": ["window_slack_DESC", "cut_slack_DESC", "deadline_slack_DESC", "group_size_DESC", "group_id_ASC"],
        "checked_cut_scope": "BRIDGES_PLUS_ONE_DETERMINISTIC_MINCUT_PER_AFFECTED_ENDPOINT_PAIR", "runtime_profile_contract": "NOT_ESTABLISHED"})
    write_atomic_json(output / "necessary_condition_qualification.json", qualification); write_csv(output / "input_parity.csv", parity_rows)
    write_csv(output / "affected_set_pools.csv", pool_rows); write_gzip_csv(output / "merge_candidates.csv.gz", candidate_rows)
    write_csv(output / "merge_funnel_summary.csv", funnel); write_csv(output / "filter_timing_summary.csv", timing)
    write_atomic_json(output / "failure_certificates.json", certificates); write_gzip_csv(output / "checked_cut_summary.csv.gz", cut_rows)
    write_gzip_csv(output / "time_window_witnesses.csv.gz", witness_rows); write_csv(output / "group_synthesis_attempts.csv", synthesis_rows)
    write_csv(output / "accepted_merge_history.csv", history_rows)
    write_csv(output / "final_fault_groups.csv", [{**row, "fault_ids": ";".join(row["fault_ids"]), "profile": bool(row.get("profile"))} for row in final_groups])
    write_atomic_json(output / "fault_to_group_profile.json", mappings); write_csv(output / "coverage_validation.csv", coverage)
    write_csv(output / "singleton_replay_validation.csv", replay_rows + singleton_parity); write_csv(output / "repeatability.csv", repeat_rows)
    write_csv(output / "compute_cost_comparison.csv", compute_rows); write_csv(output / "storage_comparison.csv", storage_rows)
    write_csv(output / "posthoc_exp20_comparison.csv", posthoc); write_atomic_json(output / "algorithm_verdict.json", verdict_payload)
    answers = [
        f"{index}. " + text for index, text in enumerate([
        f"Formal verdict: {verdict}.", f"Algorithm version: {ALGORITHM_VERSION}.", "Formal search is scenario-local.",
        "Only identical affected-flow sets are pooled.", "The candidate catalog has exact exp19 parity.", "P0 routes are reconstructed from exp18j raw output.",
        "F1 checks all TT endpoint connectivity.", "F2 checks minimum-hop serialization delay.", "F3 checks only the declared finite cut family.",
        "F3 is a necessary condition, not a sufficiency claim.", "F4 uses exact critical-window range-add/range-max sweeps.",
        "Circular jobs retain deadlines beyond one hyperperiod.", "Failure certificates prune only fault supersets.", "Backend failures do not become infeasibility certificates.",
        "H2S is primary and CELF is fallback.", "Routing is DIJKSTRA_OVERLAP with K=5.", "Flow sorting is LOW_PERIOD (4).",
        "Placement is ASAP and the quantum is 100 ns.", "Tie breaking is BASELINE.", "Seed is 1024 and execution is single-threaded.",
        "Each backend algorithm has a 30 s limit.", "Memory limit is 8192 MiB.", "Diagnostic tracing is disabled.",
        "Accepted multi-fault profiles pass the upstream verifier.", "Accepted multi-fault profiles pass the project static checker.",
        "Every accepted group is replayed against each singleton topology.", "Queue identifiers alone are rebound during singleton replay.",
        "Final singleton groups are synthesized anew with the exp19 contract.", "Final singleton results are compared with exp19 status and semantic hash.",
        "The active partition starts with singleton groups.", "Only pairwise active-group unions are proposed.", "The first successful ranked proposal is accepted.",
        "Candidates overlapping an accepted merge become stale.", "Search terminates at a deterministic local optimum.",
        f"Final profile count: {profile_count}.", f"Profile reduction from 1099: {1099-profile_count}.",
        f"Mapped fault count: {mapped_faults}.", f"Accepted merge count: {len(history_rows)}.",
        f"F1--F4 evaluated proposal count: {len(candidate_rows)}.", f"Actual group synthesis attempts: {len(synthesis_rows)}.",
        f"Singleton replay rows: {len(replay_rows)}.", f"Final singleton reruns: {len(singleton_parity)}.",
        f"Checked directed-cut rows: {len(cut_rows)}.", f"Time-window audit rows: {len(witness_rows)}.",
        f"Failure certificate count: {len(certificates)}.", f"Frozen final-group SHA-256: {frozen_group_sha}.",
        "Exp20 is read only after the final group set is frozen.", "Exp20 comparison counts are 1099/1008/986/823 plus exp21.",
        "The online runtime activation contract remains NOT_ESTABLISHED.", "No OMNeT++ or INET simulation was invoked.",
        ], 1)]
    write_atomic_bytes(output / "summary.md", ("# exp21 schedulability-pruned fault grouping\n\n" + "\n".join(answers) + "\n").encode())
    hashes = {str(path.relative_to(output)): sha256_file(path) for path in sorted(output.rglob("*")) if path.is_file() and path.name != "analysis_manifest.json"}
    write_atomic_json(output / "analysis_manifest.json", {"algorithm_version": ALGORITHM_VERSION, "implementation_commit": implementation,
        "artifact_sha256": hashes, "required_outputs": REQUIRED_OUTPUTS, "formal_verdict": verdict})
    return verdict_payload


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--quick", action="store_true"); parser.add_argument("--qualification", action="store_true")
    parser.add_argument("--resume", action="store_true"); parser.add_argument("--output", type=Path, default=OUT); args = parser.parse_args()
    if args.qualification:
        result = pure_qualification()
        with tempfile.TemporaryDirectory(prefix="exp21-qualification-") as directory:
            result.update(live_backend_qualification(Path(directory)))
        result["qualification_pass"] = result["pure_qualification_pass"] and result["live_backend_qualification_pass"]
        print(json.dumps(result, indent=2, sort_keys=True)); return 0 if result["qualification_pass"] else 1
    result = run_campaign(args.output, args.quick, args.resume); print(json.dumps(result, indent=2, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
