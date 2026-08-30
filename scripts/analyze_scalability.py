#!/usr/bin/env python3
"""Deterministic, simulation-free post-processing for the formal exp10 artifact."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path
from statistics import mean

SCHEMA_VERSION = 1
POLICIES = ("J100", "J060", "J040", "J020")
REQUIRED_SWITCHES = (20, 30, 40, 50)
NOT_CAPTURED = "NOT_CAPTURED"
CSV_NAMES = (
    "scale_summary.csv", "scenario_summary.csv", "candidate_fault_scaling.csv",
    "pf_precompute_scaling.csv", "z3_scaling.csv", "bfs_scaling.csv",
    "profile_scaling.csv", "class_scaling.csv", "compression_scaling.csv",
    "coverage_scaling.csv", "failure_status_scaling.csv", "bottleneck_breakdown.csv",
    "representative_online_audit.csv", "memory_scaling.csv", "campaign_execution.csv",
)
PNG_NAMES = (
    "candidate_faults_vs_scale.png", "pf_precompute_time_vs_scale.png",
    "z3_time_vs_scale.png", "profile_count_vs_scale.png",
    "compression_ratio_vs_scale.png", "profile_storage_vs_scale.png",
    "offline_cost_breakdown_vs_scale.png", "failure_status_vs_scale.png",
    "class_size_vs_scale.png",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires non-empty values")
    position = (len(ordered) - 1) * fraction
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def load_campaign(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if tuple(data.get("policies", [])) != POLICIES:
        raise ValueError(f"formal policy order must be {POLICIES}")
    scenarios = sorted(data.get("scenarios", []), key=lambda row: row["switch_count"])
    if tuple(row["switch_count"] for row in scenarios) != REQUIRED_SWITCHES:
        raise ValueError("formal campaign must contain exactly 20/30/40/50 switches")
    for scenario in scenarios:
        if tuple(policy for policy in POLICIES if policy in scenario["policy"]) != POLICIES:
            raise ValueError(f"missing formal policy in {scenario['scenario']}")
        if scenario["candidate_fault_count"] < scenario["recoverable_fault_count"]:
            raise ValueError("candidate faults must be >= recoverable faults")
        for key in ("initial_profile_total_wall_ms", "initial_route_wall_ms", "initial_z3_wall_ms",
                    "pf_total_precompute_wall_ms"):
            if scenario[key] < 0:
                raise ValueError(f"negative wall time: {scenario['scenario']} {key}")
        for policy in POLICIES:
            item = scenario["policy"][policy]
            for key in ("compression", "candidate_compression", "storage_compression", "shared_fault_coverage"):
                if not 0 <= item[key] <= 1:
                    raise ValueError(f"out-of-range {key}: {scenario['scenario']} {policy}")
            if item["profile_count"] <= 0:
                raise ValueError("profile count must be positive")
    return {**data, "scenarios": scenarios}


def pf_profile_bytes(scenario: dict) -> int:
    estimates = []
    for policy in POLICIES:
        item = scenario["policy"][policy]
        denominator = 1 - item["storage_compression"]
        if denominator <= 0:
            raise ValueError("cannot recover PF storage denominator")
        estimates.append(item["profile_bytes"] / denominator)
    rounded = [round(value) for value in estimates]
    if max(rounded) - min(rounded) > 1:
        raise ValueError(f"inconsistent PF storage derivation for {scenario['scenario']}")
    return rounded[0]


def format_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite CSV value")
        return f"{value:.12g}"
    return str(value)


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: format_value(row.get(field, "")) for field in fields})


def build_tables(campaign: dict) -> dict[str, tuple[list[dict], list[str]]]:
    scenarios = campaign["scenarios"]
    scale, scenario_rows, candidate, pf_rows, z3, bfs = [], [], [], [], [], []
    profile, classes, compression, coverage, failures = [], [], [], [], []
    bottleneck, online, memory, execution = [], [], [], []
    previous_candidates = None
    for scenario in scenarios:
        policies = scenario["policy"]
        pf_count, candidate_count = scenario["recoverable_fault_count"], scenario["candidate_fault_count"]
        scale_row = {key: scenario[key] for key in (
            "scenario", "switch_count", "end_system_count", "physical_link_count", "internal_link_count",
            "tt_flow_count", "be_flow_count", "candidate_fault_count", "recoverable_fault_count",
            "initial_profile_total_wall_ms", "initial_route_wall_ms", "initial_z3_wall_ms",
            "pf_total_precompute_wall_ms")}
        for stage, prefix in ((scenario["pf_route"], "pf_route"), (scenario["pf_z3"], "pf_z3"),
                              (scenario["pf_total"], "pf_total")):
            for stat in ("mean", "p50", "p95", "max"):
                scale_row[f"{prefix}_{stat}_ms"] = stage[stat]
        scale_row["pf_profile_count"] = pf_count
        for policy in POLICIES:
            key = policy.lower(); item = policies[policy]
            scale_row[f"{key}_profile_count"] = item["profile_count"]
            scale_row[f"{key}_compression"] = item["compression"]
            scale_row[f"{key}_storage_compression"] = item["storage_compression"]
            scale_row[f"{key}_shared_coverage"] = item["shared_fault_coverage"]
            scale_row[f"{key}_largest_class"] = item["largest_class"]
        scale_row["no_route_count"] = sum(policies[p]["no_route"] for p in POLICIES)
        scale_row["unsat_count"] = sum(policies[p]["unsat"] for p in POLICIES)
        scale_row["timeout_count"] = sum(policies[p]["timeout"] for p in POLICIES)
        scale.append(scale_row)

        scenario_rows.append({
            "scenario": scenario["scenario"], "switch_count": scenario["switch_count"],
            "ES_count": scenario["end_system_count"], "TT_count": scenario["tt_flow_count"],
            "BE_count": scenario["be_flow_count"], "physical_links": scenario["physical_link_count"],
            "internal_links": scenario["internal_link_count"], "candidate_faults": candidate_count,
            "recoverable_faults": pf_count,
            "candidate_fault_ratio": candidate_count / scenario["internal_link_count"],
            "P0_profile_bytes": scenario["initial_profile_bytes"],
            "P0_total_wall_ms": scenario["initial_profile_total_wall_ms"],
            "P0_route_wall_ms": scenario["initial_route_wall_ms"], "P0_z3_wall_ms": scenario["initial_z3_wall_ms"],
        })
        candidate.append({
            "scenario": scenario["scenario"], "switch_count": scenario["switch_count"],
            "internal_link_count": scenario["internal_link_count"], "candidate_fault_count": candidate_count,
            "recoverable_fault_count": pf_count, "candidate_per_switch": candidate_count / scenario["switch_count"],
            "candidate_per_internal_link": candidate_count / scenario["internal_link_count"],
            "candidate_growth_from_previous": "" if previous_candidates is None else candidate_count / previous_candidates,
        })
        previous_candidates = candidate_count
        pf_row = {"scenario": scenario["scenario"], "switch_count": scenario["switch_count"],
                  "pf_total_precompute_wall_ms": scenario["pf_total_precompute_wall_ms"]}
        for source, prefix in (("pf_total", "per_fault"), ("pf_route", "route"),
                               ("pf_z3", "z3"), ("pf_profile", "profile_compile")):
            for stat in ("mean", "p50", "p95", "max"):
                pf_row[f"{prefix}_{stat}_ms"] = scenario[source][stat]
        pf_rows.append(pf_row)
        z3.append({"scenario": scenario["scenario"], "switch_count": scenario["switch_count"],
                   **{f"pf_z3_{stat}_ms": scenario["pf_z3"][stat] for stat in ("mean", "p50", "p95", "max")},
                   "shared_z3_detail_status": NOT_CAPTURED})
        bfs.append({"scenario": scenario["scenario"], "switch_count": scenario["switch_count"],
                    **{f"pf_route_{stat}_ms": scenario["pf_route"][stat] for stat in ("mean", "p50", "p95", "max")},
                    "bfs_call_count": NOT_CAPTURED, "measurement_boundary": "algorithm_wall_clock"})

        pf_bytes = pf_profile_bytes(scenario)
        profile.append({"scenario": scenario["scenario"], "switch_count": scenario["switch_count"], "mode": "PF",
                        "profile_count": pf_count, "profile_bytes": pf_bytes,
                        "profile_count_compression": 0, "storage_compression": 0, "relative_to_pf_count": 1})
        for policy in POLICIES:
            item = policies[policy]; sizes = item["class_sizes"]
            profile.append({"scenario": scenario["scenario"], "switch_count": scenario["switch_count"], "mode": policy,
                            "profile_count": item["profile_count"], "profile_bytes": item["profile_bytes"],
                            "profile_count_compression": item["compression"], "storage_compression": item["storage_compression"],
                            "relative_to_pf_count": item["profile_count"] / pf_count})
            classes.append({
                "scenario": scenario["scenario"], "switch_count": scenario["switch_count"], "policy_id": policy,
                "candidate_group_count": item["candidate_group_count"],
                "multi_fault_candidate_group_count": item["multi_fault_candidate_group_count"],
                "profile_count": item["profile_count"], "largest_class": item["largest_class"],
                "class_size_mean": mean(sizes), "class_size_p50": percentile(sizes, .5),
                "class_size_p95": percentile(sizes, .95), "class_size_max": max(sizes),
                "shared_fault_coverage": item["shared_fault_coverage"],
                "recursive_split_count": item["recursive_split_count"], "max_split_depth": item["max_split_depth"],
                "accepted_attempt_count": item["accepted_attempt_count"], "rejected_attempt_count": item["rejected_attempt_count"],
            })
            compression.append({
                "scenario": scenario["scenario"], "switch_count": scenario["switch_count"], "policy_id": policy,
                "candidate_compression": item["candidate_compression"], "realized_compression": item["compression"],
                "compression_gap": item["candidate_compression"] - item["compression"],
                "storage_compression": item["storage_compression"], "profile_count": item["profile_count"],
                "PF_profile_count": pf_count,
            })
            coverage.append({
                "scenario": scenario["scenario"], "switch_count": scenario["switch_count"], "policy_id": policy,
                "candidate_fault_count": candidate_count, "recoverable_fault_count": pf_count,
                "PF_recovery_coverage": pf_count / candidate_count, "shared_fault_coverage": item["shared_fault_coverage"],
                "equivalence_profile_coverage": pf_count / candidate_count,
            })
            failures.append({
                "scenario": scenario["scenario"], "switch_count": scenario["switch_count"], "policy_id": policy,
                "NO_ROUTE": item["no_route"], "UNSAT": item["unsat"], "TIMEOUT": item["timeout"],
                "FORWARDING_CONFLICT": NOT_CAPTURED, "VALIDATION_FAILED": NOT_CAPTURED,
                "rejected_attempt_count": item["rejected_attempt_count"], "recursive_split_count": item["recursive_split_count"],
            })
            execution.append({
                "scenario": scenario["scenario"], "switch_count": scenario["switch_count"], "policy": policy,
                "validation_logical_count": item["validation_logical_count"], "validation_wall_ms": item["validation_wall_ms"],
                "actual_execution_wall_ms": NOT_CAPTURED, "cold_cost_status": "logical_validation_sum",
            })
        known = [("P0_ROUTE", scenario["initial_route_wall_ms"]), ("P0_Z3", scenario["initial_z3_wall_ms"]),
                 ("PF_TOTAL", scenario["pf_total_precompute_wall_ms"])]
        known += [(f"VALIDATION_{policy}", policies[policy]["validation_wall_ms"]) for policy in POLICIES]
        known_total = sum(value for _, value in known)
        for stage_name, wall in known:
            bottleneck.append({"scenario": scenario["scenario"], "switch_count": scenario["switch_count"],
                               "stage": stage_name, "wall_ms": wall,
                               "percentage_of_known_total": wall / known_total,
                               "measurement_boundary": "captured logical/cold costs; not campaign elapsed"})
        online.append({"scenario": scenario["scenario"], "status": NOT_CAPTURED,
                       "reason": "representative Online audit absent from formal campaign artifact"})
        memory.append({"scenario": scenario["scenario"], "switch_count": scenario["switch_count"],
                       "peak_rss_mb": NOT_CAPTURED, "status": NOT_CAPTURED,
                       "reason": "peak RSS absent from formal campaign artifact"})

    def fields(rows): return list(rows[0].keys())
    return {
        "scale_summary.csv": (scale, fields(scale)), "scenario_summary.csv": (scenario_rows, fields(scenario_rows)),
        "candidate_fault_scaling.csv": (candidate, fields(candidate)), "pf_precompute_scaling.csv": (pf_rows, fields(pf_rows)),
        "z3_scaling.csv": (z3, fields(z3)), "bfs_scaling.csv": (bfs, fields(bfs)),
        "profile_scaling.csv": (profile, fields(profile)), "class_scaling.csv": (classes, fields(classes)),
        "compression_scaling.csv": (compression, fields(compression)), "coverage_scaling.csv": (coverage, fields(coverage)),
        "failure_status_scaling.csv": (failures, fields(failures)), "bottleneck_breakdown.csv": (bottleneck, fields(bottleneck)),
        "representative_online_audit.csv": (online, fields(online)), "memory_scaling.csv": (memory, fields(memory)),
        "campaign_execution.csv": (execution, fields(execution)),
    }


def render_summary(campaign: dict, analysis_commit: str = NOT_CAPTURED) -> str:
    scenarios = campaign["scenarios"]
    first, last = scenarios[0], scenarios[-1]
    labels = ", ".join(f"{s['switch_count']}→{s['candidate_fault_count']}" for s in scenarios)
    j020 = ", ".join(f"{s['switch_count']}→{s['policy']['J020']['compression']:.1%}" for s in scenarios)
    counts = "; ".join(
        f"{s['switch_count']}: PF {s['recoverable_fault_count']}, " + ", ".join(f"{p} {s['policy'][p]['profile_count']}" for p in POLICIES)
        for s in scenarios)
    failures = sum(s["policy"][p]["no_route"] + s["policy"][p]["unsat"] + s["policy"][p]["timeout"]
                   for s in scenarios for p in POLICIES)
    validation_last = sum(last["policy"][p]["validation_wall_ms"] for p in POLICIES)
    largest = ", ".join(f"{s['switch_count']}→{s['policy']['J020']['largest_class']}" for s in scenarios)
    gaps = ", ".join(f"{s['switch_count']}→{s['policy']['J020']['candidate_compression']-s['policy']['J020']['compression']:.1%}" for s in scenarios)
    return f"""# exp10 — Large-scale scalability analysis

## Technical summary

Candidate faults grew {labels} as the structured mesh increased from 20 to 50 switches. PF precompute rose from {first['pf_total_precompute_wall_ms']:.1f} ms to {last['pf_total_precompute_wall_ms']:.1f} ms, while P0 Z3 rose from {first['initial_z3_wall_ms']:.1f} ms to {last['initial_z3_wall_ms']:.1f} ms. J020 retained {last['policy']['J020']['compression']:.1%} profile-count compression at 50 switches, with {last['policy']['J020']['storage_compression']:.1%} storage compression.

The experimental diagnosis is **VALIDATION_CAMPAIGN_BOTTLENECK**: at 50 switches the four logical validation sums total {validation_last/1000:.1f} s, versus {last['pf_total_precompute_wall_ms']/1000:.1f} s for PF precompute. This is campaign-engineering cost, not online recovery computation. The formal artifact does not justify GA or k-shortest routing: only one NO_ROUTE occurred across all recorded policy attempts, at structured20/J020; no UNSAT or TIMEOUT was recorded.

## Profile compression remains useful but scale-dependent

Profile counts were: {counts}. J020 compression by scale was {j020}. Count and storage compression track closely at every scale because serialized recovery Profiles have similar sizes within each scenario. The J020 largest validated class changed {largest}; it did not grow monotonically. Candidate-to-realized J020 compression gaps were {gaps}; only structured20 had a non-zero gap, caused by its recorded NO_ROUTE and recursive split.

## Solver and routing costs

PF per-fault Z3 mean/p95/max increased from {first['pf_z3']['mean']:.1f}/{first['pf_z3']['p95']:.1f}/{first['pf_z3']['max']:.1f} ms at 20 switches to {last['pf_z3']['mean']:.1f}/{last['pf_z3']['p95']:.1f}/{last['pf_z3']['max']:.1f} ms at 50 switches. PF route mean remained {last['pf_route']['mean']:.3f} ms at 50 switches, far below Z3. Z3 scaling is therefore the main measured algorithmic growth signal, but the absence of solver timeout/UNSAT means the formal evidence does not yet establish a scheduling feasibility bottleneck.

## Scope, definitions, and measurement boundaries

Compression uses PF-SAT recovery Profiles as the denominator and excludes P0. PF recovery coverage uses all candidate faults; shared fault coverage is the fraction of all candidate faults in multi-fault classes and is not total recovery coverage. Storage excludes metadata; PF storage is deterministically recovered from each policy's recorded Profile bytes and storage-compression definition, with cross-policy consistency checks.

Wall-clock values are empirical measurements on the formal experiment host. `bottleneck_breakdown.csv` reports only captured P0 route/Z3, PF total, and logical validation sums; it is not campaign elapsed time and must not be interpreted as mutually exclusive process-stage accounting.

## Missing formal metrics

- **NOT_CAPTURED:** shared-synthesis Z3 mean/p50/p95/max and shared-stage detailed timing.
- **NOT_CAPTURED:** representative Online audit; no audit figure is generated.
- **NOT_CAPTURED:** peak RSS / memory.
- **NOT_CAPTURED:** actual campaign/stage timestamps, cache counts, and orchestration elapsed time.
- **NOT_CAPTURED:** explicit forwarding-conflict and validation-failed counters. Their absence is not converted to zero.

The missing metrics cannot be reconstructed from `campaign.json` or the retained checkpoint metadata without rerunning simulation/solver stages, which this post-processing patch intentionally does not do.

## Method and provenance

This report is deterministic post-processing of the immutable formal artifact only. No OMNeT++, Z3, route solver, grouping, Profile synthesis, or member validation is invoked.

- Simulation implementation commit: `ed97466cf46d79a74171833af0ea69114d3fdb48`
- Simulation run_id: `{campaign['run_id']}`
- Raw formal artifact: `results/scalability/campaign.json`
- Analysis code commit: `{analysis_commit}`

## Recommendation

Do not introduce GA/k-shortest routing based on exp10. The next evidence-driven step is to keep the recovery algorithm frozen and either optimize validation infrastructure for experiment throughput or study SMT scalability separately. Any new solver work should preserve the distinction between offline algorithm wall-clock and OMNeT++ validation cost.

## Further questions

The existing artifact cannot answer whether shared synthesis Z3 scales differently from PF Z3, how peak memory grows, or whether representative Online behavior remains identical at every scale. Those questions require a future explicitly instrumented campaign, not retroactive inference.
"""


def make_plots(campaign: dict, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 11,
                         "axes.labelsize": 9, "legend.fontsize": 8, "figure.facecolor": "white",
                         "axes.facecolor": "white", "savefig.facecolor": "white"})
    scenarios = campaign["scenarios"]; x = [s["switch_count"] for s in scenarios]
    colors = {"PF": "#374151", "J100": "#2563EB", "J060": "#D97706", "J040": "#7C3AED", "J020": "#0F766E"}
    def finish(name, title, ylabel):
        plt.title(title); plt.xlabel("Switch count"); plt.ylabel(ylabel); plt.xticks(x); plt.grid(axis="y", color="#D1D5DB", linewidth=.6, alpha=.7)
        plt.tight_layout(); plt.savefig(output/name, dpi=150, metadata={"Software": "Matplotlib"}); plt.close()
    plt.figure(figsize=(6.4,4.0)); plt.plot(x,[s["candidate_fault_count"] for s in scenarios],marker="o",color="#2563EB",label="Candidate faults")
    plt.plot(x,[s["internal_link_count"] for s in scenarios],marker="s",linestyle="--",color="#6B7280",label="Internal links"); plt.legend(); finish("candidate_faults_vs_scale.png","Candidate faults and internal links by scale","Count")
    plt.figure(figsize=(6.4,4.0)); plt.plot(x,[s["pf_total_precompute_wall_ms"]/1000 for s in scenarios],marker="o",color="#2563EB")
    finish("pf_precompute_time_vs_scale.png","PF precompute wall time by scale","Wall time (s)")
    plt.figure(figsize=(6.4,4.0))
    for stat, style in (("mean","-"),("p95","--"),("max",":")): plt.plot(x,[s["pf_z3"][stat] for s in scenarios],marker="o",linestyle=style,label=stat.upper())
    plt.legend(); finish("z3_time_vs_scale.png","PF Z3 per-fault wall time by scale","Wall time (ms)")
    plt.figure(figsize=(6.4,4.0)); plt.plot(x,[s["recoverable_fault_count"] for s in scenarios],marker="o",color=colors["PF"],label="PF")
    for p in POLICIES: plt.plot(x,[s["policy"][p]["profile_count"] for s in scenarios],marker="o",color=colors[p],label=p)
    plt.legend(ncol=3); finish("profile_count_vs_scale.png","Recovery Profile count by scale","Profile count")
    plt.figure(figsize=(6.4,4.0))
    for p in POLICIES: plt.plot(x,[100*s["policy"][p]["compression"] for s in scenarios],marker="o",color=colors[p],label=p)
    plt.ylim(bottom=0); plt.legend(ncol=2); finish("compression_ratio_vs_scale.png","Profile-count compression by scale","Compression (%)")
    plt.figure(figsize=(6.4,4.0)); plt.plot(x,[pf_profile_bytes(s)/1024 for s in scenarios],marker="o",color=colors["PF"],label="PF")
    for p in ("J100","J040","J020"): plt.plot(x,[s["policy"][p]["profile_bytes"]/1024 for s in scenarios],marker="o",color=colors[p],label=p)
    plt.legend(); finish("profile_storage_vs_scale.png","Serialized recovery Profile storage by scale","Storage (KiB)")
    plt.figure(figsize=(7.2,4.2)); bottom=[0.0]*len(x)
    components=[("P0",[s["initial_profile_total_wall_ms"]/1000 for s in scenarios],"#9CA3AF"),
                ("PF total",[s["pf_total_precompute_wall_ms"]/1000 for s in scenarios],"#2563EB"),
                ("Logical validation",[sum(s["policy"][p]["validation_wall_ms"] for p in POLICIES)/1000 for s in scenarios],"#D97706")]
    for label,values,color in components: plt.bar(x,values,bottom=bottom,width=5.5,label=label,color=color,edgecolor="#374151",linewidth=.5); bottom=[a+b for a,b in zip(bottom,values)]
    plt.legend(); finish("offline_cost_breakdown_vs_scale.png","Known offline logical/cold cost composition","Wall time (s)")
    plt.figure(figsize=(6.4,4.0)); width=1.8
    for index,(label,key,color) in enumerate((("NO_ROUTE","no_route","#D97706"),("UNSAT","unsat","#6B7280"),("TIMEOUT","timeout","#7C3AED"))):
        plt.bar([v+(index-1)*width for v in x],[sum(s["policy"][p][key] for p in POLICIES) for s in scenarios],width=width,label=label,color=color,edgecolor="#374151",linewidth=.5)
    plt.legend(); finish("failure_status_vs_scale.png","Recorded synthesis failure status by scale","Attempt count")
    plt.figure(figsize=(6.4,4.0))
    for p in ("J100","J040","J020"): plt.plot(x,[s["policy"][p]["largest_class"] for s in scenarios],marker="o",color=colors[p],label=f"{p} max")
    plt.legend(); finish("class_size_vs_scale.png","Largest validated class by scale","Faults per class")


def strict_validate(output: Path, campaign: dict, campaign_hash: str) -> None:
    required = set(CSV_NAMES) | set(PNG_NAMES) | {"summary.md", "analysis_manifest.json"}
    missing = sorted(name for name in required if not (output/name).exists())
    if missing: raise ValueError(f"missing exp10 analysis artifacts: {missing}")
    manifest = json.loads((output/"analysis_manifest.json").read_text())
    if manifest["source_campaign_sha256"] != campaign_hash: raise ValueError("source campaign SHA mismatch")
    if tuple(campaign["policies"]) != POLICIES or len(campaign["scenarios"]) != 4: raise ValueError("formal campaign shape mismatch")
    for name, expected in manifest["generated_artifact_sha256"].items():
        if sha256(output/name) != expected: raise ValueError(f"analysis artifact hash mismatch: {name}")
    with (output/"compression_scaling.csv").open(encoding="utf-8") as handle:
        rows=list(csv.DictReader(handle))
    for row in rows:
        if not 0 <= float(row["realized_compression"]) <= 1: raise ValueError("compression range failure")
        if int(row["profile_count"]) <= 0: raise ValueError("profile count failure")


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--campaign",required=True,type=Path); parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--analysis-code-commit",help="committed analyzer SHA; defaults to current HEAD")
    parser.add_argument("--validate-only",action="store_true")
    args=parser.parse_args(); source=args.campaign.resolve(); output=args.output.resolve()
    before=sha256(source); campaign=load_campaign(source)
    if args.validate_only:
        strict_validate(output,campaign,before); print("EXP10 ANALYSIS PASS"); return 0
    output.mkdir(parents=True,exist_ok=True)
    analysis_commit=args.analysis_code_commit or subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    tables=build_tables(campaign)
    for name,(rows,fields) in tables.items(): write_csv(output/name,rows,fields)
    (output/"summary.md").write_text(render_summary(campaign,analysis_commit),encoding="utf-8")
    make_plots(campaign,output)
    if sha256(source) != before: raise RuntimeError("immutable campaign.json changed during analysis")
    generated=sorted(list(CSV_NAMES)+list(PNG_NAMES)+["summary.md"])
    manifest={"analysis_schema_version":SCHEMA_VERSION,"source_campaign_sha256":before,
              "source_campaign_implementation_commit":campaign["implementation_commit"],
              "simulation_run_id":campaign["run_id"],"analysis_code_commit":analysis_commit,
              "analysis_timestamp":datetime.strptime(campaign["run_id"],"%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ"),
              "generated_artifact_paths":generated,
              "generated_artifact_sha256":{name:sha256(output/name) for name in generated}}
    (output/"analysis_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    strict_validate(output,campaign,before)
    print("EXP10 ANALYSIS PASS")
    return 0

if __name__ == "__main__": raise SystemExit(main())
