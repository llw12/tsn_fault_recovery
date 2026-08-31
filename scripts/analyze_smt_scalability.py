#!/usr/bin/env python3
"""Deterministic analysis for the exp11 solver-only campaign."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

MODES = ("PRODUCTION_OPTIMIZE", "BENCHMARK_FEASIBILITY_ONLY")
CASE_TYPES = ("P0", "PF", "SHARED")
POLICIES = ("J100", "J040", "J020")
COMPLETED = {"SAT", "UNSAT"}
NA = "NOT_AVAILABLE"
BLUE, ORANGE, GOLD, OLIVE, PINK = "#1769aa", "#d4661f", "#c49a00", "#657a2e", "#ad4773"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values: return None
    ordered = sorted(values); position = (len(ordered) - 1) * fraction
    lo, hi = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def stats(values: list[float]) -> dict:
    return {"mean": sum(values) / len(values) if values else None,
            "p50": percentile(values, .5), "p95": percentile(values, .95),
            "max": max(values) if values else None, "min": min(values) if values else None}


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__); result = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and values[order[end]] == values[order[index]]: end += 1
        rank = (index + end - 1) / 2 + 1
        for item in order[index:end]: result[item] = rank
        index = end
    return result


def spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right): return None
    x, y = ranks(left), ranks(right); mx, my = statistics.mean(x), statistics.mean(y)
    numerator = sum((a - mx) * (b - my) for a, b in zip(x, y))
    denominator = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return numerator / denominator if denominator else None


def aggregate_status(rows: list[dict]) -> str:
    statuses = {row["status"] for row in rows}
    for status in ("TIMEOUT", "UNKNOWN_OTHER", "UNSAT", "NO_ROUTE", "SAT"):
        if status in statuses: return status
    return "UNKNOWN_OTHER"


def representative_rows(rows: list[dict], mode: str = "PRODUCTION_OPTIMIZE") -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        if row["mode"] == mode: groups[row["case_id"]].append(row)
    result = []
    for case_id in sorted(groups):
        members = groups[case_id]; base = dict(members[0]); base["status"] = aggregate_status(members)
        completed = [r for r in members if r["status"] in COMPLETED]
        if completed:
            base["z3_check_wall_ms"] = percentile([r["z3_check_wall_ms"] for r in completed], .5)
        base["repeat_count"] = len(members); result.append(base)
    return result


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: ("" if row.get(key) is None else
                             json.dumps(row[key], sort_keys=True, separators=(",", ":")) if isinstance(row.get(key), (list, dict))
                             else row.get(key, "")) for key in fields})


def scenario_summary(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows: grouped[(row["scenario"], row["switch_count"], row["case_type"], row["mode"])].append(row)
    output = []
    for key in sorted(grouped, key=lambda x: (x[1], CASE_TYPES.index(x[2]), MODES.index(x[3]))):
        scenario, switches, case_type, mode = key; members = grouped[key]
        logical = {row["case_id"] for row in members}; completed = [r for r in members if r["status"] in COMPLETED]
        timing = [r["z3_check_wall_ms"] for r in completed]
        build = [r["model_build_wall_ms"] for r in completed]
        row = {"scenario": scenario, "switch_count": switches, "case_type": case_type, "mode": mode,
               "case_count": len(logical), "measurement_count": len(members),
               "sat_count": len({r["case_id"] for r in members if r["status"] == "SAT"}),
               "unsat_count": len({r["case_id"] for r in members if r["status"] == "UNSAT"}),
               "timeout_count": len({r["case_id"] for r in members if r["status"] == "TIMEOUT"}),
               "unknown_other_count": len({r["case_id"] for r in members if r["status"] == "UNKNOWN_OTHER"}),
               "no_route_count": len({r["case_id"] for r in members if r["status"] == "NO_ROUTE"}),
               "completed_solve_count": len(completed),
               "timeout_rate": len({r["case_id"] for r in members if r["status"] == "TIMEOUT"}) / len(logical) if logical else 0,
               "z3_mean_ms": stats(timing)["mean"], "z3_p50_ms": stats(timing)["p50"],
               "z3_p95_ms": stats(timing)["p95"], "z3_max_ms": stats(timing)["max"],
               "z3_min_ms": stats(timing)["min"], "build_mean_ms": stats(build)["mean"],
               "build_p95_ms": stats(build)["p95"]}
        for field, prefix in (("total_symbolic_var_count", "variables"),
                              ("total_hard_constraint_count", "constraints"),
                              ("contention_pair_count", "contention_pairs")):
            values = [r[field] for r in members if r["status"] != "NO_ROUTE"]
            row[f"mean_{prefix}"] = stats(values)["mean"]; row[f"max_{prefix}"] = stats(values)["max"]
        output.append(row)
    return output


def optimize_comparison(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(lambda: defaultdict(list))
    for row in rows: grouped[row["case_id"]][row["mode"]].append(row)
    output = []
    for case_id in sorted(grouped):
        modes = grouped[case_id]; opt, feas = modes.get(MODES[0], []), modes.get(MODES[1], [])
        if not opt or not feas: continue
        first = opt[0]
        def aggregate(items):
            status = aggregate_status(items)
            values = [r["z3_check_wall_ms"] for r in items if r["status"] in COMPLETED]
            return status, percentile(values, .5)
        opt_status, opt_ms = aggregate(opt); feas_status, feas_ms = aggregate(feas)
        ratio = opt_ms / feas_ms if opt_ms is not None and feas_ms is not None and feas_ms > 0 else None
        consistent = not (opt_status == "SAT" and feas_status != "SAT") and not (feas_status == "UNSAT" and opt_status == "SAT")
        output.append({"case_id": case_id, "scenario": first["scenario"], "switch_count": first["switch_count"],
                       "case_type": first["case_type"], "policy_id": first["policy_id"],
                       "optimize_status": opt_status, "feasibility_status": feas_status,
                       "optimize_z3_ms": opt_ms, "feasibility_z3_ms": feas_ms,
                       "overhead_ratio": ratio, "consistent": consistent})
    return output


def pf_shared(rows: list[dict]) -> list[dict]:
    reps = representative_rows(rows); output = []
    scenarios = sorted({(r["switch_count"], r["scenario"]) for r in reps})
    for switches, scenario in scenarios:
        for label in ("PF",) + POLICIES:
            members = [r for r in reps if r["scenario"] == scenario and
                       (r["case_type"] == "PF" if label == "PF" else r["case_type"] == "SHARED" and r["policy_id"] == label)]
            completed = [r for r in members if r["status"] in COMPLETED]
            timing = [r["z3_check_wall_ms"] for r in completed]
            row = {"scenario": scenario, "switch_count": switches, "case_family": label,
                   "case_count": len(members), "completed_count": len(completed),
                   "z3_mean_ms": stats(timing)["mean"], "z3_p95_ms": stats(timing)["p95"], "z3_max_ms": stats(timing)["max"]}
            for field, name in (("total_symbolic_var_count", "mean_variables"),
                                ("total_hard_constraint_count", "mean_constraints"),
                                ("contention_pair_count", "mean_contention_pairs")):
                values = [r[field] for r in members if r["status"] != "NO_ROUTE"]
                row[name] = stats(values)["mean"]
            output.append(row)
    return output


def status_frontier(rows: list[dict]) -> list[dict]:
    reps = representative_rows(rows); output = []
    scales = sorted({(r["switch_count"], r["scenario"]) for r in reps})
    first_timeout = next((s for s, _ in scales if any(r["switch_count"] == s and r["status"] == "TIMEOUT" for r in reps)), None)
    first_p0 = next((s for s, _ in scales if any(r["switch_count"] == s and r["case_type"] == "P0" and r["status"] != "SAT" for r in reps)), None)
    zero_timeout = [s for s, _ in scales if not any(r["switch_count"] == s and r["status"] == "TIMEOUT" for r in reps)]
    for switches, scenario in scales:
        members = [r for r in reps if r["scenario"] == scenario]
        row = {"scenario": scenario, "switch_count": switches,
               "p0_status": next((r["status"] for r in members if r["case_type"] == "P0"), "SKIPPED"),
               "largest_scale_with_zero_timeout": max(zero_timeout) if zero_timeout else None,
               "first_scale_with_timeout": first_timeout, "first_scale_with_p0_failure": first_p0}
        for family in ("PF", "SHARED"):
            selected = [r for r in members if r["case_type"] == family]
            for status in ("SAT", "UNSAT", "TIMEOUT", "UNKNOWN_OTHER", "NO_ROUTE"):
                row[f"{family.lower()}_{status.lower()}_count"] = sum(r["status"] == status for r in selected)
        output.append(row)
    return output


def complexity(rows: list[dict]) -> list[dict]:
    fields = ["case_id", "scenario", "switch_count", "case_type", "policy_id", "tt_flow_count",
              "controlled_hop_count", "total_symbolic_var_count", "total_hard_constraint_count",
              "non_overlap_constraint_count", "contention_pair_count", "max_flows_per_egress",
              "z3_check_wall_ms", "status", "repeat_count"]
    return [{key: row.get(key) for key in fields} for row in representative_rows(rows)]


def relationships(complexity_rows: list[dict]) -> list[dict]:
    features = ("tt_flow_count", "controlled_hop_count", "total_symbolic_var_count",
                "total_hard_constraint_count", "non_overlap_constraint_count",
                "contention_pair_count", "max_flows_per_egress")
    output = []
    for scope in ("ALL", "PF", "SHARED"):
        members = [r for r in complexity_rows if r["status"] in COMPLETED and (scope == "ALL" or r["case_type"] == scope)]
        for feature in features:
            output.append({"scope": scope, "feature": feature, "completed_case_count": len(members),
                           "spearman_rho": spearman([float(r[feature]) for r in members],
                                                    [float(r["z3_check_wall_ms"]) for r in members])})
    return output


def scale_summary(rows: list[dict], comparisons: list[dict]) -> list[dict]:
    reps = representative_rows(rows); output = []
    for switches, scenario in sorted({(r["switch_count"], r["scenario"]) for r in reps}):
        members = [r for r in reps if r["scenario"] == scenario]; p0 = next(r for r in members if r["case_type"] == "P0")
        pf = [r for r in members if r["case_type"] == "PF" and r["status"] in COMPLETED]
        ratios = [r["overhead_ratio"] for r in comparisons if r["scenario"] == scenario and r["overhead_ratio"] is not None]
        output.append({"scenario": scenario, "switch_count": switches,
                       "tt_flow_count": p0["tt_flow_count"], "candidate_fault_count": len([r for r in members if r["case_type"] == "PF"]),
                       "p0_status": p0["status"], "p0_z3_median_ms": p0["z3_check_wall_ms"],
                       "pf_z3_mean_ms": stats([r["z3_check_wall_ms"] for r in pf])["mean"],
                       "pf_z3_p95_ms": stats([r["z3_check_wall_ms"] for r in pf])["p95"],
                       "pf_z3_max_ms": stats([r["z3_check_wall_ms"] for r in pf])["max"],
                       "median_overhead_ratio": percentile(ratios, .5), "p95_overhead_ratio": percentile(ratios, .95),
                       "timeout_count": sum(r["status"] == "TIMEOUT" for r in members),
                       "unknown_other_count": sum(r["status"] == "UNKNOWN_OTHER" for r in members),
                       "unsat_count": sum(r["status"] == "UNSAT" for r in members),
                       "no_route_count": sum(r["status"] == "NO_ROUTE" for r in members)})
    return output


def setup_plot(title: str, subtitle: str, xlabel: str, ylabel: str):
    fig, ax = plt.subplots(figsize=(9, 5.5)); fig.patch.set_facecolor("white"); ax.set_facecolor("white")
    ax.set_title(title, loc="left", fontsize=14, color="#202124", pad=22)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9, color="#5f6368", va="bottom")
    ax.set_xlabel(xlabel); ax.set_ylabel(ylabel); ax.grid(axis="y", color="#dfe3e8", linewidth=.7)
    ax.spines[["top", "right"]].set_visible(False)
    return fig, ax


def save(fig, path: Path):
    fig.tight_layout(); fig.savefig(path, dpi=160, metadata={"Software": "exp11 deterministic analyzer"}); plt.close(fig)


def figures(output: Path, rows: list[dict], summaries: list[dict], pfshared: list[dict], comparisons: list[dict]):
    prod = [r for r in summaries if r["mode"] == MODES[0] and r["case_type"] == "PF"]
    fig, ax = setup_plot("PF Z3 check time vs network scale", "Completed production Optimize cases; timeouts excluded", "Switch count", "Z3 check time (ms)")
    for field, label, color, marker in (("z3_mean_ms", "Mean", BLUE, "o"), ("z3_p95_ms", "P95", ORANGE, "s"), ("z3_max_ms", "Max", PINK, "^")):
        ax.plot([r["switch_count"] for r in prod], [r[field] for r in prod], marker=marker, label=label, color=color)
    ax.legend(frameon=False); save(fig, output / "z3_check_time_vs_scale.png")
    p0 = [r for r in summaries if r["mode"] == MODES[0] and r["case_type"] == "P0"]
    fig, ax = setup_plot("P0 Z3 check time vs network scale", "Median with min-max range across three production repeats", "Switch count", "Z3 check time (ms)")
    x=[r["switch_count"] for r in p0]; y=[r["z3_p50_ms"] for r in p0]
    ax.errorbar(x, y, yerr=[[a-b for a,b in zip(y,[r["z3_min_ms"] for r in p0])], [b-a for a,b in zip(y,[r["z3_max_ms"] for r in p0])]], color=BLUE, marker="o", capsize=4)
    save(fig, output / "p0_z3_vs_scale.png")
    pfcomp = [r for r in comparisons if r["case_type"] == "PF" and r["overhead_ratio"] is not None]
    byscale=defaultdict(list)
    for r in pfcomp: byscale[r["switch_count"]].append(r)
    fig, ax = setup_plot("PF Optimize vs feasibility-only", "Median completed-case Z3 check time by scale", "Switch count", "Z3 check time (ms)")
    scales=sorted(byscale)
    ax.plot(scales,[percentile([r["optimize_z3_ms"] for r in byscale[s]],.5) for s in scales],marker="o",label="Production Optimize",color=BLUE)
    ax.plot(scales,[percentile([r["feasibility_z3_ms"] for r in byscale[s]],.5) for s in scales],marker="s",label="Feasibility only",color=ORANGE)
    ax.legend(frameon=False); save(fig, output / "optimize_vs_feasibility.png")
    fig, ax = setup_plot("Hard-constraint growth vs network scale", "PF production cases; mean and P95", "Switch count", "Constraint count")
    reps=representative_rows(rows); byscale=defaultdict(list)
    for r in reps:
        if r["case_type"]=="PF" and r["status"]!= "NO_ROUTE": byscale[r["switch_count"]].append(r)
    scales=sorted(byscale)
    for field,label,color,style in (("total_hard_constraint_count","Total mean",BLUE,"-"),("non_overlap_constraint_count","Non-overlap mean",ORANGE,"-")):
        ax.plot(scales,[statistics.mean(r[field] for r in byscale[s]) for s in scales],label=label,color=color,linestyle=style,marker="o")
        ax.plot(scales,[percentile([r[field] for r in byscale[s]],.95) for s in scales],label=label.replace("mean","P95"),color=color,linestyle="--")
    ax.legend(frameon=False,ncol=2); save(fig, output / "model_constraints_vs_scale.png")
    completed=[r for r in reps if r["status"] in COMPLETED]
    for xfield, name, xlabel in (("total_hard_constraint_count","z3_vs_constraints.png","Total hard constraints"),("contention_pair_count","z3_vs_contention.png","Contention pairs")):
        fig, ax=setup_plot("Z3 check time vs " + xlabel.lower(), "Completed production cases; descriptive scatter, no causal fit", xlabel, "Z3 check time (ms)")
        for family,color,marker in (("PF",BLUE,"o"),("SHARED",ORANGE,"^"),("P0",GOLD,"s")):
            chosen=[r for r in completed if r["case_type"]==family]
            ax.scatter([r[xfield] for r in chosen],[r["z3_check_wall_ms"] for r in chosen],label=family,color=color,marker=marker,alpha=.72,edgecolors="#333333",linewidths=.35)
        ax.legend(frameon=False); save(fig,output/name)
    fig, ax=setup_plot("PF vs raw shared synthesis Z3 time", "Production Optimize mean among completed cases", "Switch count", "Mean Z3 check time (ms)")
    for family,color,marker in (("PF",BLUE,"o"),("J100",GOLD,"s"),("J040",OLIVE,"^"),("J020",PINK,"D")):
        selected=[r for r in pfshared if r["case_family"]==family]
        ax.plot([r["switch_count"] for r in selected],[r["z3_mean_ms"] for r in selected],label=family,color=color,marker=marker)
    ax.legend(frameon=False,ncol=4); save(fig,output/"pf_vs_shared_z3.png")
    fig, ax=setup_plot("Solver and routing status vs network scale", "Logical production cases; NO_ROUTE is separated from SMT outcomes", "Switch count", "Case count")
    scales=sorted({r["switch_count"] for r in reps}); bottoms=[0]*len(scales)
    for status,color in (("SAT",BLUE),("UNSAT",ORANGE),("TIMEOUT",PINK),("UNKNOWN_OTHER",GOLD),("NO_ROUTE","#777777")):
        values=[sum(r["switch_count"]==s and r["status"]==status for r in reps) for s in scales]
        ax.bar(scales,values,bottom=bottoms,label=status,color=color,width=4,edgecolor="#333333",linewidth=.4)
        bottoms=[a+b for a,b in zip(bottoms,values)]
    ax.legend(frameon=False,ncol=3); save(fig,output/"solver_status_vs_scale.png")


def fmt(value, digits=2): return "NA" if value is None else f"{value:.{digits}f}"


def summary_markdown(campaign: dict, scale: list[dict], front: list[dict], relationships_rows: list[dict],
                     pfshared: list[dict], comparisons: list[dict]) -> str:
    max_scale = max(scale, key=lambda r:r["switch_count"]); min_scale=min(scale,key=lambda r:r["switch_count"])
    completed_ratios=[r["overhead_ratio"] for r in comparisons if r["overhead_ratio"] is not None]
    all_timeouts=sum(r["timeout_count"] for r in scale); all_unknown=sum(r["unknown_other_count"] for r in scale); all_unsat=sum(r["unsat_count"] for r in scale)
    relation={r["feature"]:r["spearman_rho"] for r in relationships_rows if r["scope"]=="ALL"}
    shared_by=defaultdict(dict)
    for r in pfshared: shared_by[r["scenario"]][r["case_family"]]=r
    lines=["# SMT Solver Scalability and Model Complexity Characterization", "",
           "## Technical summary", "",
           f"Under the fixed {campaign['solver_timeout_ms']/1000:.0f}-second bound, the campaign covered {len(scale)} structured scales from {min_scale['switch_count']} to {max_scale['switch_count']} switches. P0 median Z3 time changed from {fmt(min_scale['p0_z3_median_ms'])} ms to {fmt(max_scale['p0_z3_median_ms'])} ms; the largest-scale PF mean/P95/max were {fmt(max_scale['pf_z3_mean_ms'])}/{fmt(max_scale['pf_z3_p95_ms'])}/{fmt(max_scale['pf_z3_max_ms'])} ms.", "",
           f"Observed production outcomes included {all_timeouts} TIMEOUT, {all_unknown} UNKNOWN_OTHER, and {all_unsat} UNSAT logical cases. These are empirical outcomes under one machine and fixed timeout, not a theoretical complexity bound. The evidence {'does not justify' if all_timeouts == 0 else 'may justify targeted investigation of'} solver optimization; it does not support introducing GA.", "",
           "## Key findings", "",
           "- P0 scaling: " + "; ".join(f"{r['switch_count']}={fmt(r['p0_z3_median_ms'])} ms" for r in scale) + ".",
           "- PF scaling (mean/P95/max ms): " + "; ".join(f"{r['switch_count']}={fmt(r['pf_z3_mean_ms'])}/{fmt(r['pf_z3_p95_ms'])}/{fmt(r['pf_z3_max_ms'])}" for r in scale) + ".",
           f"- Optimize overhead ratio across comparable completed cases: median {fmt(percentile(completed_ratios,.5))}, P95 {fmt(percentile(completed_ratios,.95))}, max {fmt(max(completed_ratios) if completed_ratios else None)}.",
           f"- Descriptive Spearman association with Z3 time: total constraints ρ={fmt(relation.get('total_hard_constraint_count'),3)}, non-overlap constraints ρ={fmt(relation.get('non_overlap_constraint_count'),3)}, contention pairs ρ={fmt(relation.get('contention_pair_count'),3)}. Association is not causation.", "",
           "![PF Z3 scaling](z3_check_time_vs_scale.png)", "",
           "![P0 Z3 scaling](p0_z3_vs_scale.png)", "",
           "## PF versus raw shared synthesis", ""]
    for r in scale:
        families=shared_by[r["scenario"]]
        lines.append(f"- {r['switch_count']} switches — PF/J100/J040/J020 mean Z3 ms: " + "/".join(fmt(families.get(x,{}).get("z3_mean_ms")) for x in ("PF","J100","J040","J020")) + ".")
    lines += ["", "Raw SHARED groups are synthesis cases only; no member validation or recursive split was performed. A harder J020 instance therefore remains visible instead of being replaced by easier descendants.", "", "![PF versus shared](pf_vs_shared_z3.png)", "",
              "## Model structure and observed relationships", "",
              "The model uses one integer start-time variable per controlled TT hop plus one `maxCompletion` auxiliary integer; explicit ordering Bool count is zero. Hard constraints are counted at insertion time, including cycle bounds, release, hop precedence, deadline, max-completion support, and pairwise non-overlap constraints.", "",
              "![Constraint growth](model_constraints_vs_scale.png)", "", "![Z3 versus constraints](z3_vs_constraints.png)", "", "![Z3 versus contention](z3_vs_contention.png)", "",
              "## Optimize versus feasibility-only", "",
              "Both modes use the same hard-constraint builder. Feasibility-only adds no objectives, never writes a Profile Store, and its schedule is not used as a production profile. Timing ratios are defined only where both modes completed and the feasibility denominator was positive.", "", "![Optimize versus feasibility](optimize_vs_feasibility.png)", "",
              "## Status frontier and regressions", "", "![Status frontier](solver_status_vs_scale.png)", ""]
    for r in front:
        lines.append(f"- {r['switch_count']} switches: P0={r['p0_status']}; PF SAT/UNSAT/TIMEOUT/UNKNOWN/NO_ROUTE={r['pf_sat_count']}/{r['pf_unsat_count']}/{r['pf_timeout_count']}/{r['pf_unknown_other_count']}/{r['pf_no_route_count']}; SHARED={r['shared_sat_count']}/{r['shared_unsat_count']}/{r['shared_timeout_count']}/{r['shared_unknown_other_count']}/{r['shared_no_route_count']}.")
    lines += ["", "The structured20/J020 raw union-disabled routing failure is retained as `NO_ROUTE`, not counted as UNSAT. Production-SAT implies feasibility-SAT for every comparable case in the dataset.", "",
              "## Scope and methodology", "", f"Source campaign: run `{campaign['run_id']}`, implementation `{campaign['implementation_commit']}`, serial parallelism={campaign['parallelism']}, Z3 {campaign['machine']['z3_version']}, timeout={campaign['solver_timeout_ms']} ms. P0 production timing uses three repeats and reports the median/range; PF and SHARED use one measurement per logical case. Ordinary timing summaries exclude TIMEOUT/UNKNOWN/NO_ROUTE and include completed SAT/UNSAT only.", "",
              "## Limitations and robustness", "", "- Wall time is machine- and load-dependent; the dataset supports empirical characterization, not asymptotic complexity claims.", "- Native Z3 statistics are version/mode dependent; missing keys are reported as `NOT_AVAILABLE`, never zero.", "- No full OMNeT++ member-validation campaign, RSS measurement, incremental solving, decomposition, parallel Z3, GA, or alternate routing was performed.", "- Correlations are descriptive and may reflect shared scale drivers or model interactions.", "",
              "## Evidence-based next recommendation", ""]
    if all_timeouts == 0 and max_scale["pf_z3_p95_ms"] is not None and max_scale["pf_z3_p95_ms"] < campaign["solver_timeout_ms"] * .1:
        lines.append("The fixed-timeout evidence shows substantial headroom through the largest tested scale. Do not add solver complexity solely for thesis novelty; proceed to final ablation/sensitivity work and thesis writing. Incremental reuse remains a future opportunity, not an implemented result.")
    elif completed_ratios and percentile(completed_ratios,.5) > 3:
        lines.append("Optimize is materially slower than feasibility-only in the observed dataset. The next bounded study should test objective simplification or a two-stage feasibility/optimization design; do not implement it without a separate decision.")
    else:
        lines.append("The first observed fixed-bound failures or feasibility cost warrant a targeted follow-up on hard-model reduction/decomposition. Incremental solving is only a reuse hypothesis until separately measured.")
    lines += ["", "There remains no evidence here supporting GA or k-shortest-path changes.", "", "## Further questions", "", "- How much model structure is identical across adjacent PF cases, measured independently of an incremental implementation?", "- Would an objective-ablation study preserve production schedule semantics while reducing Optimize cost?", "- Are the observed associations stable across a second machine or controlled repeated campaign?", ""]
    return "\n".join(lines)


def analyze(campaign_path: Path, output: Path, analysis_code_commit: str) -> dict:
    campaign=json.loads(campaign_path.read_text()); rows=campaign["solver_cases"]
    output.mkdir(parents=True,exist_ok=True)
    solver_fields=[key for key in rows[0] if key != "z3_statistics"]
    write_csv(output/"solver_cases.csv",rows,solver_fields)
    known=("conflicts","decisions","propagations","binary propagations","mk bool var","num allocs","memory","rlimit count","time")
    with (output/"z3_statistics.jsonl").open("w",encoding="utf-8") as handle:
        for row in rows:
            raw=row.get("z3_statistics",{}); canonical={key:raw.get(key,NA) for key in known}
            handle.write(json.dumps({"case_id":row["case_id"],"mode":row["mode"],"repeat_index":row["repeat_index"],"status":row["status"],"canonical":canonical,"statistics":raw},sort_keys=True,separators=(",",":"))+"\n")
    summaries=scenario_summary(rows); write_csv(output/"scenario_solver_summary.csv",summaries)
    comp=complexity(rows); write_csv(output/"model_complexity.csv",comp)
    pfs=pf_shared(rows); write_csv(output/"pf_vs_shared_scaling.csv",pfs)
    comparisons=optimize_comparison(rows); write_csv(output/"optimize_vs_feasibility.csv",comparisons)
    rel=relationships(comp); write_csv(output/"feature_relationships.csv",rel)
    front=status_frontier(rows); write_csv(output/"status_frontier.csv",front)
    scales=scale_summary(rows,comparisons); write_csv(output/"scale_summary.csv",scales)
    figures(output,rows,summaries,pfs,comparisons)
    (output/"summary.md").write_text(summary_markdown(campaign,scales,front,rel,pfs,comparisons),encoding="utf-8")
    artifacts=sorted(path.name for path in output.iterdir() if path.is_file() and path.name not in {"campaign.json","analysis_manifest.json"})
    manifest={"schema_version":1,"experiment":"exp11_smt_scalability","source_campaign":campaign_path.name,
              "source_campaign_sha256":hashlib.sha256(campaign_path.read_bytes()).hexdigest(),
              "implementation_commit":campaign["implementation_commit"],"analysis_code_commit":analysis_code_commit,
              "generated_artifact_paths":artifacts,"artifact_sha256":{name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in artifacts},
              "completed_case_timing_rule":"SAT/UNSAT only; TIMEOUT/UNKNOWN/NO_ROUTE excluded",
              "report_surface":"repository-native summary.md with static PNG evidence"}
    (output/"analysis_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    return manifest


def validate(output: Path) -> None:
    required=("campaign.json","solver_cases.csv","z3_statistics.jsonl","scenario_solver_summary.csv","model_complexity.csv","pf_vs_shared_scaling.csv","optimize_vs_feasibility.csv","feature_relationships.csv","status_frontier.csv","scale_summary.csv","summary.md","analysis_manifest.json")
    missing=[name for name in required if not (output/name).is_file()]
    if missing: raise RuntimeError("missing exp11 artifacts: "+", ".join(missing))
    manifest=json.loads((output/"analysis_manifest.json").read_text())
    for name,digest in manifest["artifact_sha256"].items():
        if hashlib.sha256((output/name).read_bytes()).hexdigest()!=digest: raise RuntimeError("artifact hash mismatch: "+name)
    rows=list(csv.DictReader((output/"solver_cases.csv").open()))
    if any(float(r[field])<0 for r in rows for field in ("route_wall_ms","model_build_wall_ms","model_extract_wall_ms","total_solver_pipeline_wall_ms") if r[field]): raise RuntimeError("negative timing")
    comparisons=list(csv.DictReader((output/"optimize_vs_feasibility.csv").open()))
    if any(r["consistent"]!="True" for r in comparisons): raise RuntimeError("Optimize/feasibility consistency failure")


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--campaign",type=Path); parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--analysis-code-commit",default="UNCOMMITTED"); parser.add_argument("--validate-only",action="store_true")
    args=parser.parse_args()
    if args.validate_only: validate(args.output)
    else:
        analyze(args.campaign,args.output,args.analysis_code_commit); validate(args.output)
    return 0


if __name__=="__main__": raise SystemExit(main())
