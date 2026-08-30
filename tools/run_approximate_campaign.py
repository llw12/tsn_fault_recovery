#!/usr/bin/env python3
"""Build validation-guided approximate equivalence stores for one frozen scenario."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.analyze_critical_links import discover
from tools.approximate_equivalence import (
    Policy, agglomerate, build_pre_fault_features, canonical_bytes, cluster_metrics,
    make_shared_profile, policy_grid, prune_tree, read_cache, resolve_tree,
    synthesis_cache_key, validation_cache_key, write_approx_store, write_cache,
)
from tools.omnet_runner import run_omnet
from tools.profile_store import semantic_profile_hash, solver_config_hash, validate_store, write_json
from tools.scenario_model import load_scenario

SOLVER_TIMEOUT_MS = 30_000

VALIDATION_FIELDS = [
    "scenario", "policy_id", "class_id", "class_type", "fault_id", "class_size",
    "profile_id", "profile_sha256", "profile_semantic_hash", "same_profile_for_all_members",
    "activation_ok", "failed_link_avoided", "forwarding_valid", "tt_sent", "tt_received",
    "tt_lost", "deadline_miss_count", "deadline_miss_ratio", "first_success_after_fault_s",
    "recovery_duration_us", "stable_post_recovery_start_s", "post_recovery_delivery_ok",
    "post_recovery_deadline_ok", "runtime_route_solver_invocations",
    "runtime_z3_solver_invocations", "runtime_profile_synthesis_invocations",
    "runtime_grouping_invocations", "validation_pass", "validation_reused",
    "source_validation_hash", "validation_wall_us", "diagnostic",
]


def truth(value) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def runtime_config_hash(scenario: dict) -> str:
    payload = {
        "mode": "offline-approx-equivalence", "lookup_delay_us": 0,
        "duration_s": scenario["simulation"]["duration_s"],
        "failure_time_s": scenario["simulation"]["failure_time_s"],
        "cycle_time_s": scenario["simulation"]["cycle_time_s"],
        "stable_window": "activation-plus-one-cycle-v1",
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def write_rows(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def synthesize(generated: Path, scenario: dict, candidate: dict, members: list[str],
               timeout_ms: int) -> dict:
    port_map = json.loads((generated / "port_map.json").read_text())
    key = synthesis_cache_key(scenario["scenario_sha256"], members,
                              solver_config_hash(scenario, port_map), candidate["candidate_set_sha256"])
    cache_dir = generated / "profiles/approximate_equivalence/cache/synthesis" / key
    cache_path = cache_dir / "cache.json"
    cached = read_cache(cache_path, key, "synthesis")
    if cached is not None:
        return {**cached, "cache_reused": True, "source_synthesis_hash": key}
    affected_by_fault = {row["fault_id"]: set(row["affected_flows"])
                         for row in candidate["candidate_faults"]}
    affected = sorted(set().union(*(affected_by_fault[fault] for fault in members)))
    cache_dir.mkdir(parents=True, exist_ok=True)
    relative = cache_dir.relative_to(generated).as_posix()
    profile_rel = f"{relative}/profile.raw.json"
    report_rel = f"{relative}/report.json"
    overrides = [
        f'--*.scenarioRecoveryController.exactClassId="SYN_{key[:16]}"',
        f'--*.scenarioRecoveryController.exactDisabledLinks="{" ".join(sorted(members))}"',
        f'--*.scenarioRecoveryController.exactAffectedFlows="{" ".join(affected)}"',
        f'--*.scenarioRecoveryController.exactProfileOutputPath="{profile_rel}"',
        f'--*.scenarioRecoveryController.exactReportOutputPath="{report_rel}"',
        f"--*.scenarioRecoveryController.solverTimeoutMs={timeout_ms}",
    ]
    started = time.perf_counter_ns()
    try:
        run_omnet(generated, "ScenarioExactGroupPrecompute", cache_dir / "results",
                  cache_dir / "precompute.log", overrides)
        report = json.loads((generated / report_rel).read_text())
        raw_status = report["status"]
        status = "SHARED_SAT" if raw_status == "SAT" else raw_status
        raw_profile = json.loads((generated / profile_rel).read_text()) if raw_status == "SAT" else None
        payload = {
            "status": status, "raw_status": raw_status, "members": sorted(members),
            "affected_flows": affected, "raw_profile": raw_profile,
            "diagnostic": report.get("diagnostic", ""), "solver_timeout_ms": timeout_ms,
            "route_solver_wall_us": report.get("route_solver_wall_us", 0),
            "smt_solver_wall_us": report.get("smt_solver_wall_us", 0),
            "synthesis_wall_us": report.get("total_class_synthesis_wall_us", 0),
        }
    except Exception as error:
        payload = {
            "status": "ERROR", "raw_status": "ERROR", "members": sorted(members),
            "affected_flows": affected, "raw_profile": None, "diagnostic": str(error),
            "solver_timeout_ms": timeout_ms, "route_solver_wall_us": 0,
            "smt_solver_wall_us": 0,
            "synthesis_wall_us": (time.perf_counter_ns() - started) / 1e3,
        }
    write_cache(cache_path, key, "synthesis", payload)
    return {**payload, "cache_reused": False, "source_synthesis_hash": key}


def validate_current_store(source: Path, generated: Path, scenario: dict, policy: Policy,
                           fault: str, semantic_hash: str, run_id: str) -> dict:
    config_hash = runtime_config_hash(scenario)
    key = validation_cache_key(scenario["scenario_sha256"], semantic_hash, fault, config_hash)
    cache_path = generated / "profiles/approximate_equivalence/cache/validation" / key / "cache.json"
    cached = read_cache(cache_path, key, "validation")
    if cached is not None:
        return {**cached, "validation_reused": True, "source_validation_hash": key}
    validation_run_id = f"{run_id}_val_{key[:12]}"
    command = [
        sys.executable, "tools/run_experiment.py", "--scenario", str(source),
        "--mode", "offline-approx-equivalence", "--equivalence-policy", policy.policy_id,
        "--fault", fault, "--inside-environment", "--skip-build", "--run-id", validation_run_id,
    ]
    started = time.perf_counter_ns()
    subprocess.run(command, cwd=ROOT, check=True)
    wall_us = (time.perf_counter_ns() - started) / 1e3
    run = (ROOT / "results/scenarios" / scenario["scenario_name"] /
           "offline-approx-equivalence" / policy.policy_id / fault / validation_run_id)
    summary = next(csv.DictReader((run / "summary.csv").open()))
    profile = json.loads((run / "recovery_profile.json").read_text())
    avoids = all(fault not in route["link_path"] for route in profile["logical_routes"])
    activation = summary["activation_time_s"] != ""
    zero_runtime = all(int(summary[field]) == 0 for field in (
        "runtime_route_solver_invocations", "runtime_z3_solver_invocations",
        "runtime_profile_synthesis_invocations", "runtime_grouping_invocations"))
    delivery = truth(summary["post_recovery_delivery_ok"])
    deadlines = truth(summary["post_recovery_deadline_ok"])
    passed = (activation and avoids and zero_runtime and delivery and deadlines and
              summary["first_success_after_fault_s"] != "")
    failed = [name for name, ok in (
        ("activation", activation), ("failed-link-avoidance", avoids),
        ("zero-runtime-computation", zero_runtime), ("stable-delivery", delivery),
        ("stable-deadlines", deadlines),
    ) if not ok]
    payload = {
        "fault_id": fault, "profile_semantic_hash": semantic_hash,
        "activation_ok": activation, "failed_link_avoided": avoids,
        "forwarding_valid": activation, "tt_sent": int(summary["tt_sent"]),
        "tt_received": int(summary["tt_received"]), "tt_lost": int(summary["tt_lost"]),
        "deadline_miss_count": int(summary["deadline_miss_count"]),
        "deadline_miss_ratio": float(summary["deadline_miss_ratio"]),
        "first_success_after_fault_s": summary["first_success_after_fault_s"],
        "recovery_duration_us": float(summary["recovery_duration_s"]) * 1e6,
        "stable_post_recovery_start_s": summary["stable_post_recovery_start_s"],
        "post_recovery_delivery_ok": delivery, "post_recovery_deadline_ok": deadlines,
        "runtime_route_solver_invocations": int(summary["runtime_route_solver_invocations"]),
        "runtime_z3_solver_invocations": int(summary["runtime_z3_solver_invocations"]),
        "runtime_profile_synthesis_invocations": int(summary["runtime_profile_synthesis_invocations"]),
        "runtime_grouping_invocations": int(summary["runtime_grouping_invocations"]),
        "validation_pass": passed, "validation_wall_us": wall_us,
        "runtime_config_hash": config_hash, "diagnostic": ";".join(failed),
        "source_run": str(run.relative_to(ROOT)),
    }
    write_cache(cache_path, key, "validation", payload)
    return {**payload, "validation_reused": False, "source_validation_hash": key}


def provisional_specs(sat_faults: list[str], shared: dict, candidate_group: str) -> list[dict]:
    members = set(shared["members"])
    specs = [{"class_type": "SHARED", "members": shared["members"],
              "raw_profile": shared["raw_profile"], "affected_flows": shared["affected_flows"],
              "source_candidate_group": candidate_group}]
    specs.extend({"class_type": "SINGLETON", "members": [fault],
                  "source_candidate_group": candidate_group}
                 for fault in sat_faults if fault not in members)
    return specs


def run_policy(source: Path, generated: Path, scenario: dict, candidate: dict, pf: dict,
               features: dict, policy: Policy, run_id: str, timeout_ms: int) -> dict:
    grouping, trace = agglomerate(features, policy)
    root = generated / "profiles/approximate_equivalence" / policy.policy_id
    root.mkdir(parents=True, exist_ok=True)
    grouping_path = root / "candidate_groups.json"
    write_json(grouping_path, grouping)
    write_rows(root / "grouping_merge_trace.csv",
               [{"scenario": scenario["scenario_name"], "policy_id": policy.policy_id, **row} for row in trace],
               ["scenario", "policy_id", "step", "left_cluster", "right_cluster", "merged_members",
                "min_pairwise_jaccard", "max_edge_distance", "merge_priority", "result_candidate_group_id"])
    sat_faults = sorted(fault for fault, entry in pf["faults"].items() if entry["status"] == "SAT")
    resolved, rejected, attempts = [], [], []
    for group in grouping["groups"]:
        tree = prune_tree(group["merge_tree"], set(sat_faults))
        if tree is None:
            continue
        if len(tree["members"]) == 1:
            resolved.append({"class_type": "SINGLETON", "members": tree["members"],
                             "split_depth": 0, "source_candidate_group": group["group_id"]})
            continue

        def attempt(members: list[str], depth: int) -> dict:
            synthesis = synthesize(generated, scenario, candidate, members, timeout_ms)
            logical = {key: value for key, value in synthesis.items() if key != "raw_profile"}
            logical.update({"candidate_group_id": group["group_id"], "split_depth": depth})
            attempts.append(logical)
            if synthesis["status"] != "SHARED_SAT":
                return {**synthesis, "validation_pass": False,
                        "validation_status": "NOT_RUN", "validation_wall_us": 0,
                        "source_candidate_group": group["group_id"]}
            provisional = provisional_specs(sat_faults, synthesis, group["group_id"])
            write_approx_store(generated, policy, grouping_path, provisional, attempts, [], timeout_ms)
            profile = make_shared_profile(synthesis["raw_profile"], scenario, candidate, members,
                                          synthesis["affected_flows"], "VALIDATION")
            semantic_hash = semantic_profile_hash(profile)
            validations = [validate_current_store(source, generated, scenario, policy, fault,
                                                  semantic_hash, run_id) for fault in members]
            passed = all(row["validation_pass"] for row in validations)
            logical["validation_wall_us"] = sum(row["validation_wall_us"] for row in validations)
            logical["validation_status"] = "PASS" if passed else "VALIDATION_FAILED"
            logical["validation_pass"] = passed
            logical["validation_rows"] = validations
            attempts[-1] = logical
            return {**synthesis, "status": "SHARED_SAT" if passed else "VALIDATION_FAILED",
                    "validation_pass": passed, "validation_status": logical["validation_status"],
                    "validation_wall_us": logical["validation_wall_us"],
                    "validation_rows": validations, "source_candidate_group": group["group_id"]}

        group_classes, group_rejected = resolve_tree(tree, attempt)
        for item in group_classes:
            item.setdefault("source_candidate_group", group["group_id"])
        resolved.extend(group_classes)
        rejected.extend(group_rejected)
    clean_rejected = [{key: value for key, value in row.items()
                       if key not in {"raw_profile", "validation_rows"}} for row in rejected]
    store = write_approx_store(generated, policy, grouping_path, resolved, attempts,
                               clean_rejected, timeout_ms)
    final_rows = []
    for class_id, entry in store["classes"].items():
        for fault in entry["members"]:
            validation = validate_current_store(source, generated, scenario, policy, fault,
                                                entry["semantic_profile_hash"], run_id)
            final_rows.append({
                "scenario": scenario["scenario_name"], "policy_id": policy.policy_id,
                "class_id": class_id, "class_type": entry["class_type"], "fault_id": fault,
                "class_size": len(entry["members"]), "profile_id": entry["profile_id"],
                "profile_sha256": entry["profile_sha256"],
                "profile_semantic_hash": entry["semantic_profile_hash"],
                "same_profile_for_all_members": True, **validation,
            })
    write_rows(root / "class_validation.csv", final_rows, VALIDATION_FIELDS)
    write_rows(root / "validated_classes.csv", [{
        "scenario": scenario["scenario_name"], "policy_id": policy.policy_id, "class_id": class_id,
        "class_type": entry["class_type"], "members": ";".join(entry["members"]),
        "member_count": len(entry["members"]), "profile_id": entry["profile_id"],
        "profile_semantic_hash": entry["semantic_profile_hash"],
        "min_pairwise_jaccard": entry["min_pairwise_jaccard"],
        "mean_pairwise_jaccard": entry["mean_pairwise_jaccard"],
        "max_edge_distance": entry["max_edge_distance"],
        "source_candidate_group": entry["source_candidate_group"],
        "split_depth": entry["split_depth"], "validation_pass": True,
    } for class_id, entry in store["classes"].items()],
        ["scenario", "policy_id", "class_id", "class_type", "members", "member_count",
         "profile_id", "profile_semantic_hash", "min_pairwise_jaccard", "mean_pairwise_jaccard",
         "max_edge_distance", "source_candidate_group", "split_depth", "validation_pass"])
    return store


def assert_j100(generated: Path, store: dict) -> None:
    exact = json.loads((generated / "profiles/exact_equivalence/store.json").read_text())
    grouping = json.loads((generated / "profiles/approximate_equivalence/J100/candidate_groups.json").read_text())
    approximate_groups = sorted(sorted(group["member_faults"]) for group in grouping["groups"])
    exact_groups = sorted(sorted(group["members"]) for group in exact["candidate_groups"])
    if approximate_groups != exact_groups:
        raise RuntimeError("J100 candidate groups differ from exp08 exact affected-set groups")
    approximate_members = sorted(sorted(entry["members"]) for entry in store["classes"].values())
    exact_members = sorted(sorted(entry["members"]) for entry in exact["classes"].values())
    if approximate_members != exact_members:
        raise RuntimeError("J100 final memberships differ from exp08 exact equivalence")
    approximate_semantics = {tuple(sorted(entry["members"])): entry["semantic_profile_hash"]
                             for entry in store["classes"].values()}
    exact_semantics = {tuple(sorted(entry["members"])): entry["semantic_profile_hash"]
                       for entry in exact["classes"].values()}
    if approximate_semantics != exact_semantics:
        raise RuntimeError("J100 semantic profiles differ from exp08 exact equivalence")
    if sum(entry["profile_bytes"] for entry in store["classes"].values()) != sum(
            entry["profile_bytes"] for entry in exact["classes"].values()):
        raise RuntimeError("J100 recovery Profile storage differs from exp08 exact equivalence")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--policy", action="append", help="run only selected policy id(s)")
    parser.add_argument("--solver-timeout-ms", type=int, default=SOLVER_TIMEOUT_MS)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    source = args.scenario if args.scenario.is_absolute() else ROOT / args.scenario
    model = load_scenario(source)
    generated, candidate = discover(source, skip_build=args.skip_build)
    scenario = json.loads((generated / "scenario.json").read_text())
    port_map = json.loads((generated / "port_map.json").read_text())
    pf = validate_store(generated / "profiles/per_failure/store.json", scenario, port_map)
    features = build_pre_fault_features(model, candidate)
    feature_path = generated / "fault_analysis/pre_fault_pairwise_features.json"
    write_json(feature_path, features)
    selected = set(args.policy or [])
    policies = [policy for policy in policy_grid() if not selected or policy.policy_id in selected]
    if selected != {policy.policy_id for policy in policies} and selected:
        raise SystemExit(f"unknown policies: {sorted(selected - {policy.policy_id for policy in policies})}")
    for index, policy in enumerate(policies, 1):
        print(f"APPROX_POLICY {index}/{len(policies)} {model.scenario_name} {policy.policy_id}", flush=True)
        store = run_policy(source, generated, scenario, candidate, pf, features, policy,
                           args.run_id, args.solver_timeout_ms)
        if policy.policy_id == "J100":
            assert_j100(generated, store)
    print(f"scenario={model.scenario_name} policies={len(policies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
