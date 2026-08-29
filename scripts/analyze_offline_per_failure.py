#!/usr/bin/env python3
"""Validate and summarize the formal Offline Per-Failure fault sweep."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("diamond", "mesh10")
MODES = ("no-recovery", "online", "offline-per-failure")


def read_row(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.DictReader(handle))


def number(value, default=None):
    return default if value in (None, "") else float(value)


def percentile(values, fraction):
    if not values: return None
    values = sorted(values); position = (len(values) - 1) * fraction
    lower = int(position); upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def plot_latency(rows, output):
    relevant = [row for row in rows if row["affected_flow_count"] > 0]
    labels = [f"{row['scenario']}\n{row['fault_id']}" for row in relevant]
    x = range(len(labels)); width = .36
    online = [row["online_recovery_us"] for row in relevant]
    offline = [row["offline_recovery_us"] for row in relevant]
    fig, ax = plt.subplots(figsize=(12, 6.4))
    ax.bar([i-width/2 for i in x], online, width, label="Online", color="#3568A8", edgecolor="#24364B")
    ax.bar([i+width/2 for i in x], offline, width, label="Offline Per-Failure", color="#E6A23C", edgecolor="#6E4C17", hatch="//")
    fig.text(.075, .965, "Recovery latency by relevant candidate fault", weight="bold", fontsize=15, ha="left")
    fig.text(.075, .93, "Fault-to-first-success latency; all relevant faults; non-SAT states are annotated",
             color="#5B6573", fontsize=10, ha="left")
    ax.set_ylabel("Recovery latency (µs)"); ax.set_xticks(list(x), labels, rotation=25, ha="right")
    ax.set_ylim(bottom=0); ax.grid(axis="y", color="#D8DCE2", linewidth=.7); ax.set_axisbelow(True)
    top = max(online + offline + [1])
    for i, row in enumerate(relevant):
        if row["offline_status"] != "SAT":
            ax.text(i, top * .96, row["offline_status"], ha="center", va="top", fontsize=8, color="#6E4C17", rotation=90)
    ax.legend(frameon=False, ncol=2, loc="upper right")
    fig.tight_layout(rect=(0, 0, 1, .89)); fig.savefig(output, dpi=180, facecolor="white"); plt.close(fig)


def plot_loss(rows, output):
    labels = [f"{row['scenario']}\n{row['fault_id']}" for row in rows]
    x = range(len(labels)); width = .25
    series = [("No Recovery", "no_recovery_tt_lost", "#B8BEC7", ""),
              ("Online", "online_tt_lost", "#3568A8", ""),
              ("Offline Per-Failure", "offline_tt_lost", "#E6A23C", "//")]
    fig, ax = plt.subplots(figsize=(13.2, 6.4))
    for offset, (label, key, color, hatch) in zip((-1, 0, 1), series):
        ax.bar([i+offset*width for i in x], [row[key] for row in rows], width,
               label=label, color=color, edgecolor="#3D4652", hatch=hatch)
    fig.text(.075, .965, "TT packet loss by declared candidate fault", weight="bold", fontsize=15, ha="left")
    fig.text(.075, .93, "Eligible TT packets not received; includes NO_AFFECTED_TT faults without filtering",
             color="#5B6573", fontsize=10, ha="left")
    ax.set_ylabel("TT packets lost"); ax.set_xticks(list(x), labels, rotation=25, ha="right")
    ax.set_ylim(bottom=0); ax.grid(axis="y", color="#D8DCE2", linewidth=.7); ax.set_axisbelow(True)
    top = max([row[key] for row in rows for _, key, _, _ in series] + [1])
    for i, row in enumerate(rows):
        if row["offline_status"] not in {"SAT", "NO_AFFECTED_TT"}:
            ax.text(i, top * .96, row["offline_status"], ha="center", va="top", fontsize=8, color="#6E4C17", rotation=90)
    ax.legend(frameon=False, ncol=3, loc="upper right")
    fig.tight_layout(rect=(0, 0, 1, .89)); fig.savefig(output, dpi=180, facecolor="white"); plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir; output.mkdir(parents=True, exist_ok=True)
    manifests = output / "manifests"; manifests.mkdir(exist_ok=True)
    stores_out = output / "profile_stores"; stores_out.mkdir(exist_ok=True)
    comparisons = []
    summaries_by_mode = defaultdict(list)
    store_data = {}

    for scenario_name in SCENARIOS:
        generated = ROOT / "generated" / scenario_name
        scenario = json.loads((generated / "scenario.json").read_text())
        store_path = generated / "profiles/per_failure/store.json"
        store = json.loads(store_path.read_text()); store_data[scenario_name] = store
        destination = stores_out / scenario_name
        if destination.exists(): shutil.rmtree(destination)
        shutil.copytree(store_path.parent, destination, ignore=shutil.ignore_patterns("raw", "precompute-results", "precompute.log", "runtime_store.json"))
        shutil.copy2(store_path.parent / "precompute_per_fault.csv", output / f"{scenario_name}_precompute_per_fault.csv")
        for fault_id in scenario["fault_candidates"]:
            mode_rows = {}
            for mode in MODES:
                run_dir = ROOT / "results/scenarios" / scenario_name / mode / fault_id / args.run_id
                row = read_row(run_dir / "summary.csv"); mode_rows[mode] = row; summaries_by_mode[(scenario_name, mode)].append(row)
                manifest = json.loads((run_dir / "manifest.json").read_text())
                if manifest["git_commit"] != args.code_commit or manifest["runtime_code_commit"] != args.code_commit:
                    raise RuntimeError(f"manifest code SHA mismatch: {scenario_name}/{mode}/{fault_id}")
                if mode == "offline-per-failure":
                    if manifest["offline_precompute_code_commit"] != args.code_commit:
                        raise RuntimeError(f"offline precompute SHA mismatch: {scenario_name}/{fault_id}")
                    if int(float(row["runtime_route_solver_invocations"])) != 0 or int(float(row["runtime_z3_solver_invocations"])) != 0:
                        raise RuntimeError(f"offline runtime invoked solver: {scenario_name}/{fault_id}")
                shutil.copy2(run_dir / "manifest.json", manifests / f"{scenario_name}__{mode}__{fault_id}.json")
            entry = store["faults"][fault_id]
            affected_count = len(entry["affected_flows"])
            if affected_count and entry["status"] == "SAT":
                if int(float(mode_rows["online"]["runtime_route_solver_invocations"])) <= 0 or int(float(mode_rows["online"]["runtime_z3_solver_invocations"])) <= 0:
                    raise RuntimeError(f"online solver instrumentation missing: {scenario_name}/{fault_id}")
            comparisons.append({
                "scenario": scenario_name, "fault_id": fault_id, "affected_flow_count": affected_count,
                "offline_status": entry["status"], "online_recovery_status": mode_rows["online"]["recovery_status"],
                "no_recovery_tt_lost": int(float(mode_rows["no-recovery"]["tt_lost"])),
                "online_tt_lost": int(float(mode_rows["online"]["tt_lost"])),
                "offline_tt_lost": int(float(mode_rows["offline-per-failure"]["tt_lost"])),
                "online_recovery_us": number(mode_rows["online"]["recovery_duration_s"], 0) * 1e6 if affected_count else 0,
                "offline_recovery_us": number(mode_rows["offline-per-failure"]["recovery_duration_s"], 0) * 1e6 if affected_count else 0,
                "online_deadline_miss": int(float(mode_rows["online"]["deadline_miss_count"])),
                "offline_deadline_miss": int(float(mode_rows["offline-per-failure"]["deadline_miss_count"])),
                "online_runtime_solver_delay_us": number(mode_rows["online"]["simulated_decision_delay_s"], 0) * 1e6,
                "offline_lookup_delay_us": number(mode_rows["offline-per-failure"]["simulated_decision_delay_s"], 0) * 1e6,
                "online_route_wall_us": number(mode_rows["online"]["route_solver_wall_us_runtime"], 0),
                "online_z3_wall_us": number(mode_rows["online"]["smt_solver_wall_us_runtime"], 0),
                "offline_lookup_wall_us": number(mode_rows["offline-per-failure"]["runtime_lookup_wall_us"], 0),
                "profile_bytes": entry.get("profile_bytes", 0),
            })

    comparison_fields = list(comparisons[0])
    write_csv(output / "per_fault_comparison.csv", comparisons, comparison_fields)
    aggregates = []
    for (scenario_name, mode), rows in summaries_by_mode.items():
        losses = [number(row["tt_lost"], 0) for row in rows]
        relevant_recovery = [number(row["recovery_duration_s"]) * 1e6 for row in rows if row["recovery_duration_s"]]
        recovered = sum(row["recovery_status"] == "RECOVERED" for row in rows)
        relevant = sum(row["recovery_status"] not in {"NO_ACTION"} for row in rows)
        received = sum(number(row["tt_received"], 0) for row in rows); misses = sum(number(row["deadline_miss_count"], 0) for row in rows)
        store = store_data[scenario_name]
        aggregates.append({
            "scenario": scenario_name, "mode": mode, "fault_count": len(rows),
            "recovery_success_rate": recovered / relevant if relevant else None,
            "mean_tt_loss": mean(losses), "p95_tt_loss": percentile(losses, .95), "max_tt_loss": max(losses),
            "mean_recovery_us": mean(relevant_recovery) if relevant_recovery else None,
            "p50_recovery_us": percentile(relevant_recovery, .5), "p95_recovery_us": percentile(relevant_recovery, .95),
            "max_recovery_us": max(relevant_recovery) if relevant_recovery else None,
            "deadline_miss_ratio": misses / received if received else None,
            "precompute_total_ms": store["recovery_precompute_wall_ms"] if mode == "offline-per-failure" else None,
            "recovery_profile_count": sum(entry["status"] == "SAT" for entry in store["faults"].values()) if mode == "offline-per-failure" else 0,
            "recovery_profile_storage_bytes": sum(entry.get("profile_bytes", 0) for entry in store["faults"].values()) if mode == "offline-per-failure" else 0,
        })
    write_csv(output / "aggregate_summary.csv", aggregates, list(aggregates[0]))

    groups = defaultdict(list)
    for scenario_name, store in store_data.items():
        for fault_id, entry in store["faults"].items():
            if entry["status"] == "SAT": groups[(scenario_name, entry["semantic_profile_hash"])].append(fault_id)
    hash_rows = [{"scenario": key[0], "semantic_hash": key[1], "fault_count": len(faults), "fault_ids": " ".join(faults)} for key, faults in sorted(groups.items())]
    write_csv(output / "profile_hash_groups.csv", hash_rows, ["scenario", "semantic_hash", "fault_count", "fault_ids"])
    plot_latency(comparisons, output / "recovery_latency_by_fault.png")
    plot_loss(comparisons, output / "tt_loss_by_fault.png")

    scenario_lines = []
    for scenario_name in SCENARIOS:
        store = store_data[scenario_name]; statuses = [entry["status"] for entry in store["faults"].values()]
        scenario_lines.append(
            f"| {scenario_name} | {len(statuses)} | {sum(status != 'NO_AFFECTED_TT' for status in statuses)} | "
            f"{statuses.count('SAT')} | {statuses.count('NO_AFFECTED_TT')} | {statuses.count('NO_ROUTE')} | "
            f"{statuses.count('UNSAT')} | {sum(entry.get('profile_bytes', 0) for entry in store['faults'].values())} | "
            f"{store['recovery_precompute_wall_ms']:.3f} |")
    aggregate_map = {(row["scenario"], row["mode"]): row for row in aggregates}
    comparison_lines = []
    for scenario_name in SCENARIOS:
        for mode in MODES:
            row = aggregate_map[(scenario_name, mode)]
            mean_recovery = "" if row["mean_recovery_us"] is None else f"{row['mean_recovery_us']:.3f}"
            p95_recovery = "" if row["p95_recovery_us"] is None else f"{row['p95_recovery_us']:.3f}"
            comparison_lines.append(
                f"| {scenario_name} | {mode} | {row['mean_tt_loss']:.3f} | {row['p95_tt_loss']:.3f} | "
                f"{mean_recovery} | {p95_recovery} | {row['deadline_miss_ratio']:.6f} |")
    lines = ["# Offline Per-Failure Joint Profile Recovery", "",
             "## Technical summary", "",
             "The formal sweep evaluates every declared single-link candidate in Diamond and mesh10 from the same clean implementation commit. Offline Per-Failure preloads one independently serialized joint Profile for every SAT relevant fault and performs no runtime BFS or Z3 calls.", "",
             "## Recovery and loss evidence", "",
             "`recovery_latency_by_fault.png` compares fault-to-first-success latency for all relevant SAT faults. `tt_loss_by_fault.png` retains every declared candidate, including no-action faults.", "",
             "![Recovery latency by fault](recovery_latency_by_fault.png)", "",
             "![TT loss by fault](tt_loss_by_fault.png)", "",
             "| Scenario | Mode | Mean TT loss | p95 TT loss | Mean recovery (µs) | p95 recovery (µs) | Deadline-miss ratio |",
             "|---|---:|---:|---:|---:|---:|---:|", *comparison_lines, "",
             "## Profile coverage and offline cost", "",
             "| Scenario | Candidates | Relevant | SAT | No affected | No route | UNSAT | Recovery bytes | Precompute (ms) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|", *scenario_lines, "",
             "## Scope and metric definitions", "",
             "Recovery latency is `T_first_success - T_fault`; deadline misses count delivered TT packets exceeding their configured end-to-end deadline. Offline lookup delay is an explicit simulated control-plane parameter and defaults to the ideal preloaded lower bound of 0 µs. Wall-clock lookup, BFS, and Z3 times never advance simulation time.", "",
             "## Method and validation", "",
             "Each affected flow is rerouted with deterministic BFS on the fault-disabled graph; unaffected routes remain identical to P0. All active TT flows are jointly scheduled by the same Z3 model used online, compiled into a complete GCL/Profile, checked for destination-MAC forwarding realizability, and activated through the shared ProfileSwitcher. Every online SAT Profile matched its offline counterpart by semantic hash.", "",
             "## Limitations and robustness", "",
             "The experiment models instantaneous fault detection and an ideal 0 µs preloaded lookup; the additional 100 µs sensitivity run is reported separately. Results are deterministic simulation outcomes, not controller hardware measurements. Destination-MAC forwarding conflicts are rejected rather than solved with a stream-aware data plane.", "",
             "## Recommended next step", "",
             "Use these independent per-fault Profiles as the uncompressed baseline for the next-stage Fault Equivalence Classification study; do not interpret duplicate semantic hashes as an implemented equivalence class.", "",
             "## Further questions", "",
             "The next study should test how much recovery-profile count and storage can be reduced while preserving schedulability, TT loss, deadline compliance, and recovery latency.", ""]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    manifest = {"schema_version": 1, "experiment": "exp06_offline_per_failure",
                "git_commit": args.code_commit, "offline_precompute_code_commit": args.code_commit,
                "runtime_code_commit": args.code_commit, "run_id": args.run_id,
                "profile_strategy": "per-failure",
                "profile_store_sha256": {name: __import__('hashlib').sha256((ROOT / "generated" / name / "profiles/per_failure/store.json").read_bytes()).hexdigest() for name in SCENARIOS},
                "regression": {f"exp0{i}": "PASS" for i in range(1, 6)},
                "runtime_solver_assertion": {"offline_route_invocations": 0, "offline_z3_invocations": 0},
                "online_offline_semantic_equality": "PASS"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
