#!/usr/bin/env python3
"""Aggregate exp09 policy frontiers, diagnostics, quality deltas, and figures."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
from collections import Counter
from itertools import combinations
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("diamond_auto", "mesh10_auto", "structured20_auto")
LABELS = {"diamond_auto": "Diamond", "mesh10_auto": "mesh10", "structured20_auto": "structured20"}
POLICIES = ("J100", "J080", "J060", "J040", "J020",
            "JE080_D0", "JE080_D1", "JE080_D2", "JE060_D0", "JE060_D1", "JE060_D2",
            "JE040_D0", "JE040_D1", "JE040_D2")
BLUE, GOLD, RED, GREY, INK = "#2878B5", "#E59F28", "#C94C4C", "#8A98A6", "#26323D"


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values); position = (len(ordered) - 1) * fraction
    lower = int(position); upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pair_lookup(features: dict) -> dict[tuple[str, str], dict]:
    return {(row["fault_i"], row["fault_j"]): row for row in features["pairs"]}


def pair_row(lookup: dict, left: str, right: str) -> dict:
    return lookup[tuple(sorted((left, right)))]


def diagnostics(generated: Path, store: dict, features: dict) -> list[dict]:
    candidate = json.loads((generated / "fault_analysis/candidate_faults.json").read_text())
    scenario = json.loads((generated / "scenario.json").read_text())
    pf_root = generated / "profiles/per_failure"
    pf = json.loads((pf_root / "store.json").read_text())
    profile0 = json.loads((generated / "profiles/profile0.json").read_text())
    positions = {route["flow_id"]: {link: index for index, link in enumerate(route["link_path"])}
                 for route in profile0["logical_routes"]}
    affected = {row["fault_id"]: set(row["affected_flows"]) for row in candidate["candidate_faults"]}
    flow_load = {flow["id"]: flow["packet_size_bytes"] * 8 / flow["period_s"]
                 for flow in scenario["tt_flows"]}
    pf_profiles = {fault: json.loads((pf_root / entry["profile_file"]).read_text())
                   for fault, entry in pf["faults"].items() if entry["status"] == "SAT"}
    lookup = pair_lookup(features)
    rows = []
    for attempt in store["synthesis_attempts"]:
        members = attempt["members"]; pairs = list(combinations(members, 2))
        jaccards = [float(pair_row(lookup, a, b)["affected_flow_jaccard"]) for a, b in pairs]
        distances = [int(pair_row(lookup, a, b)["fault_edge_distance"]) for a, b in pairs]
        path_differences, recovery_jaccards = [], []
        for left, right in pairs:
            common = affected[left] & affected[right]
            if common:
                path_differences.append(mean([abs(positions[flow][left] - positions[flow][right]) for flow in common]))
            left_links = {link for route in pf_profiles[left]["logical_routes"] for link in route["link_path"]}
            right_links = {link for route in pf_profiles[right]["logical_routes"] for link in route["link_path"]}
            recovery_jaccards.append(len(left_links & right_links) / len(left_links | right_links))
        union = set().union(*(affected[fault] for fault in members))
        accepted = attempt["status"] == "SHARED_SAT" and attempt.get("validation_pass") is True
        rows.append({
            "scenario": store["scenario_name"], "policy_id": store["policy_id"],
            "candidate_group_id": attempt["candidate_group_id"], "member_count": len(members),
            "member_faults": ";".join(members), "min_jaccard": min(jaccards),
            "mean_jaccard": mean(jaccards), "max_edge_distance": max(distances),
            "mean_path_position_difference": mean(path_differences),
            "affected_flow_union_count": len(union),
            "affected_load_union_bps": sum(flow_load[flow] for flow in union),
            "shared_synthesis_status": attempt["raw_status"],
            "validation_status": attempt.get("validation_status", "NOT_RUN"), "accepted": accepted,
            "posthoc_mean_recovery_route_jaccard": mean(recovery_jaccards),
            "posthoc_pf_semantic_hash_count": len({pf["faults"][fault]["semantic_profile_hash"] for fault in members}),
        })
    return rows


def collect():
    policy_rows, traces, candidates, rejected, classes, validations, comparisons, diagnostic_rows, cache_rows = ([] for _ in range(9))
    baseline = {(row["scenario"], row["fault_id"]): row
                for row in read_csv(ROOT / "results/exact_equivalence/per_fault_equivalence_comparison.csv")}
    for scenario_name in SCENARIOS:
        generated = ROOT / "generated" / scenario_name
        pf_root = generated / "profiles/per_failure"
        pf = json.loads((pf_root / "store.json").read_text())
        features = json.loads((generated / "fault_analysis/pre_fault_pairwise_features.json").read_text())
        pf_entries = [entry for entry in pf["faults"].values() if entry["status"] == "SAT"]
        m = len(pf_entries); pf_bytes = sum(entry["profile_bytes"] for entry in pf_entries)
        for policy_id in POLICIES:
            root = generated / "profiles/approximate_equivalence" / policy_id
            store = json.loads((root / "store.json").read_text())
            grouping = json.loads((root / "candidate_groups.json").read_text())
            trace = read_csv(root / "grouping_merge_trace.csv"); traces.extend(trace)
            class_rows = read_csv(root / "validated_classes.csv"); classes.extend(class_rows)
            validation_rows = read_csv(root / "class_validation.csv"); validations.extend(validation_rows)
            for group in grouping["groups"]:
                candidates.append({
                    "scenario": scenario_name, "policy_id": policy_id, "candidate_group_id": group["group_id"],
                    "member_faults": ";".join(group["member_faults"]), "member_count": group["member_count"],
                    "min_pairwise_jaccard": group["min_pairwise_jaccard"],
                    "mean_pairwise_jaccard": group["mean_pairwise_jaccard"],
                    "max_edge_distance": group["max_edge_distance"],
                    "predicted_profile_count": group["predicted_profile_count"], "pre_fault_only": True,
                })
            lookup = pair_lookup(features)
            for item in store["rejected_groups"]:
                members = item["members"]; metrics = ([pair_row(lookup, a, b) for a, b in combinations(members, 2)])
                status = item["status"].replace("SHARED_", "")
                rejected.append({
                    "scenario": scenario_name, "policy_id": policy_id,
                    "candidate_group_id": item.get("source_candidate_group", ""),
                    "member_count": len(members), "member_faults": ";".join(members),
                    "min_jaccard": min(float(row["affected_flow_jaccard"]) for row in metrics),
                    "max_edge_distance": max(int(row["fault_edge_distance"]) for row in metrics),
                    "failure_stage": "VALIDATION" if status == "VALIDATION_FAILED" else "SYNTHESIS",
                    "status": status, "diagnostic": item.get("diagnostic", ""),
                    "split_left": ";".join(item["split_left"]), "split_right": ";".join(item["split_right"]),
                    "synthesis_wall_us": item.get("synthesis_wall_us", 0),
                    "validation_wall_us": item.get("validation_wall_us", 0),
                })
            entries = list(store["classes"].values())
            result_bytes = sum(entry["profile_bytes"] for entry in entries)
            attempt_count = len(store["synthesis_attempts"])
            accepted = sum(row["status"] == "SHARED_SAT" and row.get("validation_pass") is True
                           for row in store["synthesis_attempts"])
            reasons = Counter(row["status"] for row in rejected if row["scenario"] == scenario_name and row["policy_id"] == policy_id)
            per_fault = []
            validation_by_fault = {row["fault_id"]: row for row in validation_rows}
            for fault in sorted(store["fault_to_class"]):
                row = validation_by_fault[fault]; base = baseline[(scenario_name, fault)]
                delta_recovery = float(row["recovery_duration_us"]) - float(base["per_failure_recovery_us"])
                comparison = {
                    "scenario": scenario_name, "policy_id": policy_id, "fault_id": fault,
                    "class_id": row["class_id"], "class_type": row["class_type"],
                    "class_size": row["class_size"], "per_failure_tt_lost": base["per_failure_tt_lost"],
                    "approximate_tt_lost": row["tt_lost"],
                    "tt_loss_delta": int(row["tt_lost"]) - int(base["per_failure_tt_lost"]),
                    "per_failure_deadline_miss": base["per_failure_deadline_miss"],
                    "approximate_deadline_miss": row["deadline_miss_count"],
                    "deadline_miss_delta": int(row["deadline_miss_count"]) - int(base["per_failure_deadline_miss"]),
                    "per_failure_recovery_us": base["per_failure_recovery_us"],
                    "approximate_recovery_us": row["recovery_duration_us"],
                    "recovery_delta_us": delta_recovery, "validation_pass": row["validation_pass"],
                    "validation_reused": row["validation_reused"], "source_validation_hash": row["source_validation_hash"],
                }
                comparisons.append(comparison); per_fault.append(comparison)
            deltas = [float(row["recovery_delta_us"]) for row in per_fault]
            raw_groups = len(grouping["groups"])
            candidate_compression = 1 - raw_groups / m if m else 0
            realized_compression = 1 - len(entries) / m if m else 0
            synthesis_wall = sum(float(row.get("synthesis_wall_us", 0)) for row in store["synthesis_attempts"])
            logical_validations = [item for attempt in store["synthesis_attempts"]
                                   for item in attempt.get("validation_rows", [])] + validation_rows
            unique_validations = {}
            for item in logical_validations:
                unique_validations.setdefault(item["source_validation_hash"], item)
            validation_wall = sum(float(item.get("validation_wall_us", 0)) for item in unique_validations.values())
            shared = [entry for entry in entries if entry["class_type"] == "SHARED"]
            row = {
                "scenario": scenario_name, "policy_id": policy_id, "policy_type": store["policy_type"],
                "theta": store["theta"], "dmax": "" if store["dmax"] is None else store["dmax"],
                "recoverable_fault_count": m, "raw_candidate_group_count": raw_groups,
                "raw_multi_fault_group_count": sum(group["member_count"] > 1 for group in grouping["groups"]),
                "candidate_compression_ratio": candidate_compression,
                "synthesis_attempt_count": attempt_count, "accepted_attempt_count": accepted,
                "rejected_attempt_count": attempt_count - accepted,
                "recursive_split_count": len(store["rejected_groups"]),
                "max_split_depth": max((int(item["split_depth"]) for item in store["rejected_groups"]), default=0),
                "final_profile_count": len(entries), "realized_profile_compression_ratio": realized_compression,
                "storage_compression_ratio": 1 - result_bytes / pf_bytes if pf_bytes else 0,
                "compression_gap": candidate_compression - realized_compression,
                "shared_fault_coverage": sum(len(entry["members"]) for entry in shared) / m if m else 0,
                "mean_class_size": m / len(entries) if entries else 0,
                "max_class_size": max((len(entry["members"]) for entry in entries), default=0),
                "group_acceptance_rate": accepted / attempt_count if attempt_count else 1.0,
                "no_route_count": reasons["NO_ROUTE"], "unsat_count": reasons["UNSAT"],
                "forwarding_conflict_count": reasons["FORWARDING_CONFLICT"],
                "timeout_count": reasons["TIMEOUT"], "validation_failed_count": reasons["VALIDATION_FAILED"],
                "error_count": reasons["ERROR"], "cold_synthesis_wall_ms": synthesis_wall / 1e3,
                "cold_validation_wall_ms": validation_wall / 1e3,
                "mean_recovery_delta_us": mean(deltas), "p95_recovery_delta_us": percentile(deltas, .95),
                "max_recovery_delta_us": max(deltas, default=0),
                "tt_loss_delta": sum(int(item["tt_loss_delta"]) for item in per_fault),
                "deadline_miss_delta": sum(int(item["deadline_miss_delta"]) for item in per_fault),
                "stable_validation_pass": all(str(item["validation_pass"]).lower() == "true" for item in per_fault),
                "multi_fault_class_count": len(shared),
                "singleton_class_count": len(entries) - len(shared),
            }
            policy_rows.append(row)
            cache_rows.append({
                "scenario": scenario_name, "policy_id": policy_id,
                "synthesis_request_count": attempt_count,
                "synthesis_cache_hit_count": sum(bool(item.get("cache_reused")) for item in store["synthesis_attempts"]),
                "validation_logical_count": len(validation_rows),
                "validation_cache_hit_count": sum(str(item["validation_reused"]).lower() == "true" for item in validation_rows),
                "cold_synthesis_wall_ms": synthesis_wall / 1e3, "cold_validation_wall_ms": validation_wall / 1e3,
            })
            diagnostic_rows.extend(diagnostics(generated, store, features))
    return policy_rows, traces, candidates, rejected, classes, validations, comparisons, diagnostic_rows, cache_rows


def pareto(rows: list[dict]) -> list[dict]:
    result = []
    for scenario in SCENARIOS:
        eligible = [row for row in rows if row["scenario"] == scenario and row["deadline_miss_delta"] == 0 and row["stable_validation_pass"]]
        for row in eligible:
            dominated = any(
                other["realized_profile_compression_ratio"] >= row["realized_profile_compression_ratio"] and
                other["cold_synthesis_wall_ms"] <= row["cold_synthesis_wall_ms"] and
                other["max_recovery_delta_us"] <= row["max_recovery_delta_us"] and
                (other["realized_profile_compression_ratio"] > row["realized_profile_compression_ratio"] or
                 other["cold_synthesis_wall_ms"] < row["cold_synthesis_wall_ms"] or
                 other["max_recovery_delta_us"] < row["max_recovery_delta_us"])
                for other in eligible if other is not row)
            if not dominated:
                result.append({**row, "frontier_type": "observed_non_dominated"})
    return result


def style(ax):
    ax.spines[["top", "right"]].set_visible(False); ax.grid(color="#E5E9ED", linewidth=.7)
    ax.set_axisbelow(True)


def plots(output: Path, rows: list[dict], rejected: list[dict], classes: list[dict]) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), sharey=True)
    for ax, scenario in zip(axes, SCENARIOS):
        selected = [row for row in rows if row["scenario"] == scenario and row["policy_type"] == "JACCARD"]
        labels = [row["policy_id"] for row in selected]
        ax.plot(labels, [row["candidate_compression_ratio"] for row in selected], marker="o", label="Candidate", color=GREY)
        ax.plot(labels, [row["realized_profile_compression_ratio"] for row in selected], marker="o", label="Realized", color=BLUE)
        ax.set_title(LABELS[scenario]); ax.set_xlabel("Jaccard policy (more aggressive →)"); style(ax)
    axes[0].set_ylabel("Profile compression ratio"); axes[0].legend(frameon=False)
    fig.suptitle("Candidate and validated compression frontier", x=.06, ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, .92)); fig.savefig(output / "compression_frontier.png", dpi=180); plt.close(fig)

    def scatter(name, xfield, yfield, xlabel, ylabel, title):
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
        for ax, scenario in zip(axes, SCENARIOS):
            selected = [row for row in rows if row["scenario"] == scenario]
            for row in selected:
                marker = "o" if row["policy_type"] == "JACCARD" else "s"
                ax.scatter(row[xfield], row[yfield], marker=marker, s=38, color=BLUE if marker == "o" else GOLD)
            ax.set_title(LABELS[scenario]); ax.set_xlabel(xlabel); ax.set_ylabel(ylabel); style(ax)
        fig.suptitle(title, x=.06, ha="left", fontweight="bold")
        fig.tight_layout(rect=(0, 0, 1, .92)); fig.savefig(output / name, dpi=180); plt.close(fig)
    scatter("compression_vs_acceptance.png", "realized_profile_compression_ratio", "group_acceptance_rate",
            "Realized compression", "Shared-group acceptance", "Compression versus validation acceptance")
    for row in rows: row["cold_total_wall_ms"] = row["cold_synthesis_wall_ms"] + row["cold_validation_wall_ms"]
    scatter("compression_vs_offline_cost.png", "cold_total_wall_ms", "realized_profile_compression_ratio",
            "Estimated cold synthesis + validation (ms)", "Realized compression", "Compression versus offline cost")

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=True)
    for ax, scenario in zip(axes, SCENARIOS):
        selected = [row for row in rows if row["scenario"] == scenario]
        x = range(len(selected)); ax.plot(x, [row["mean_recovery_delta_us"] for row in selected], marker="o", label="mean", color=BLUE)
        ax.plot(x, [row["p95_recovery_delta_us"] for row in selected], marker=".", label="p95", color=GOLD)
        ax.plot(x, [row["max_recovery_delta_us"] for row in selected], marker=".", label="max", color=RED)
        ax.axhline(0, color=GREY, linewidth=.8); ax.set_xticks(list(x), [row["policy_id"] for row in selected], rotation=70, fontsize=7)
        ax.set_title(LABELS[scenario]); style(ax)
    axes[0].set_ylabel("Recovery latency delta vs PF (µs)"); axes[0].legend(frameon=False)
    fig.suptitle("Recovery quality by policy", x=.06, ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, .92)); fig.savefig(output / "recovery_delta_by_policy.png", dpi=180); plt.close(fig)

    reasons = ("NO_ROUTE", "UNSAT", "FORWARDING_CONFLICT", "TIMEOUT", "VALIDATION_FAILED", "ERROR")
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    for ax, scenario in zip(axes, SCENARIOS):
        bottom = [0] * len(POLICIES)
        for reason, color in zip(reasons, (BLUE, GOLD, RED, GREY, "#7A5EA8", INK)):
            values = [sum(row["scenario"] == scenario and row["policy_id"] == policy and row["status"] == reason for row in rejected) for policy in POLICIES]
            ax.bar(POLICIES, values, bottom=bottom, label=reason, color=color); bottom = [a+b for a,b in zip(bottom, values)]
        ax.set_title(LABELS[scenario]); ax.set_ylabel("Rejected nodes"); style(ax)
    axes[-1].tick_params(axis="x", rotation=60); axes[0].legend(frameon=False, ncol=3)
    fig.suptitle("Recursive-split rejection reasons", x=.06, ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, .94)); fig.savefig(output / "rejected_group_reasons.png", dpi=180); plt.close(fig)

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    buckets = ("singleton", "size2", "size3", "size4+")
    for ax, scenario in zip(axes, SCENARIOS):
        bottom = [0] * len(POLICIES)
        for bucket, color in zip(buckets, (GREY, BLUE, GOLD, RED)):
            values = []
            for policy in POLICIES:
                sizes = [int(row["member_count"]) for row in classes if row["scenario"] == scenario and row["policy_id"] == policy]
                values.append(sum((size == 1 if bucket == "singleton" else size == 2 if bucket == "size2" else size == 3 if bucket == "size3" else size >= 4) for size in sizes))
            ax.bar(POLICIES, values, bottom=bottom, label=bucket, color=color); bottom = [a+b for a,b in zip(bottom, values)]
        ax.set_title(LABELS[scenario]); ax.set_ylabel("Final classes"); style(ax)
    axes[-1].tick_params(axis="x", rotation=60); axes[0].legend(frameon=False, ncol=4)
    fig.suptitle("Validated class-size distribution", x=.06, ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, .94)); fig.savefig(output / "class_size_by_policy.png", dpi=180); plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results/approximate_equivalence")
    parser.add_argument("--run-id", required=True); parser.add_argument("--code-commit", required=True)
    args = parser.parse_args(); output = args.output_dir
    if output.exists(): shutil.rmtree(output)
    output.mkdir(parents=True)
    rows, traces, candidates, rejected, classes, validations, comparisons, diagnostics_rows, cache_rows = collect()
    policy_fields = list(rows[0]); write_csv(output / "policy_summary.csv", rows, policy_fields)
    write_csv(output / "grouping_merge_trace.csv", traces, list(traces[0]) if traces else ["scenario", "policy_id"])
    write_csv(output / "candidate_groups.csv", candidates, list(candidates[0]))
    rejected_fields = ["scenario", "policy_id", "candidate_group_id", "member_count", "member_faults", "min_jaccard", "max_edge_distance", "failure_stage", "status", "diagnostic", "split_left", "split_right", "synthesis_wall_us", "validation_wall_us"]
    write_csv(output / "rejected_groups.csv", rejected, rejected_fields)
    reason_rows = [{"scenario": scenario, "policy_id": policy, "status": status,
                    "count": sum(row["scenario"] == scenario and row["policy_id"] == policy and row["status"] == status for row in rejected)}
                   for scenario in SCENARIOS for policy in POLICIES
                   for status in ("NO_ROUTE", "UNSAT", "FORWARDING_CONFLICT", "TIMEOUT", "VALIDATION_FAILED", "ERROR")]
    write_csv(output / "rejection_reason_summary.csv", reason_rows, ["scenario", "policy_id", "status", "count"])
    write_csv(output / "validated_classes.csv", classes, list(classes[0]))
    write_csv(output / "class_validation.csv", validations, list(validations[0]))
    write_csv(output / "per_fault_policy_comparison.csv", comparisons, list(comparisons[0]))
    write_csv(output / "group_acceptance_diagnostic.csv", diagnostics_rows, list(diagnostics_rows[0]) if diagnostics_rows else ["scenario", "policy_id"])
    frontier_fields = ["scenario", "policy_id", "policy_type", "theta", "dmax", "candidate_compression_ratio", "realized_profile_compression_ratio", "compression_gap", "group_acceptance_rate", "cold_synthesis_wall_ms", "cold_validation_wall_ms", "max_recovery_delta_us", "deadline_miss_delta"]
    write_csv(output / "compression_frontier.csv", rows, frontier_fields)
    frontier = pareto(rows); write_csv(output / "pareto_frontier.csv", frontier, list(frontier[0]) if frontier else policy_fields + ["frontier_type"])
    write_csv(output / "cache_summary.csv", cache_rows, list(cache_rows[0]))
    plots(output, rows, rejected, classes)
    by_scenario = {scenario: [row for row in rows if row["scenario"] == scenario] for scenario in SCENARIOS}
    lines = [
        "# exp09 — Approximate Fault Equivalence Frontier", "",
        "## Executive summary", "",
        "Jaccard and topology rules proposed candidate fault groups; no group was treated as equivalent until union-disabled robust Profile synthesis and every-member single-fault OMNeT++ validation passed. The final stores therefore preserve deterministic, zero-runtime-computation recovery while exposing the gap between proposed and validated compression.", "",
        "Thresholds are experimental policy points on the evaluated grid, not learned universal constants.", "",
        "## Results", "",
    ]
    for scenario, selected in by_scenario.items():
        j100 = next(row for row in selected if row["policy_id"] == "J100")
        j020 = next(row for row in selected if row["policy_id"] == "J020")
        best = max(selected, key=lambda row: row["realized_profile_compression_ratio"])
        lines += [
            f"### {LABELS[scenario]}", "",
            f"Lowering the J-only threshold from 1.0 to 0.2 changed candidate compression from {j100['candidate_compression_ratio']:.1%} to {j020['candidate_compression_ratio']:.1%}, and realized compression from {j100['realized_profile_compression_ratio']:.1%} to {j020['realized_profile_compression_ratio']:.1%}. The J020 proposal-to-validation gap was {j020['compression_gap']:.1%}.", "",
            f"The largest observed realized compression was {best['realized_profile_compression_ratio']:.1%} at {best['policy_id']}; this is an observed frontier result, not a generally optimal threshold. Across all 14 policies, aggregate TT-loss delta was {sum(row['tt_loss_delta'] for row in selected)} and aggregate delivered-TT deadline-miss delta was {sum(row['deadline_miss_delta'] for row in selected)}.", "",
        ]
    total_attempts = sum(row["synthesis_attempt_count"] for row in rows)
    total_rejected = sum(row["rejected_attempt_count"] for row in rows)
    lines += [
        "## Feasibility, edge constraints, and cost", "",
        f"The grid made {total_attempts} logical shared-synthesis attempts and rejected {total_rejected}; rejection causes are separated in `rejection_reason_summary.csv`. JE policies show whether restricting fault-edge distance improves acceptance at the cost of candidate compression, without changing the acceptance gate.", "",
        f"Summed per-policy cold estimates were {sum(row['cold_synthesis_wall_ms'] for row in rows):.3f} ms for synthesis and {sum(row['cold_validation_wall_ms'] for row in rows):.3f} ms for validation. These estimates charge cached work at its original measured cost; `cache_summary.csv` separately records campaign reuse.", "",
        "## Method and limitations", "",
        "Grouping used only healthy P0 affected-flow sets, Jaccard, edge distance, affected counts/load, and topology metadata. Recovery status, recovery routes, semantic Profile hashes, Z3 objectives, latency, serialized bytes, and packet outcomes were excluded from grouping. Recovery-route similarity and PF semantic-hash counts appear only as labeled post-hoc diagnostics.", "",
        "The observed Pareto frontier maximizes realized compression while minimizing cold synthesis cost and maximum recovery-latency delta, subject to zero deadline-miss delta and stable validation. It characterizes only these three frozen scenarios and 14 policy points.", "",
        "## Reproducibility", "",
        f"Run ID: `{args.run_id}`. Implementation commit: `{args.code_commit}`. Solver timeout: 30000 ms. Runtime BFS, Z3, Profile synthesis, and grouping counters were asserted to be zero for every validation row.", "",
    ]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    artifact_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in sorted(output.iterdir()) if path.name != "manifest.json"}
    manifest = {"schema_version": 1, "experiment": "exp09_approximate_equivalence",
                "run_id": args.run_id, "git_commit": args.code_commit,
                "omnetpp_version": "6.4.0", "inet_version": "4.7.0",
                "policy_count_per_scenario": 14, "scenario_count": 3,
                "solver_timeout_ms": 30000, "artifacts": artifact_hashes}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
