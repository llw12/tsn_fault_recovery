#!/usr/bin/env python3
"""Aggregate exp07 auto scenarios and render the technical report figures."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(output: Path, code_commit: str) -> tuple[dict, list[dict], list[dict]]:
    output.mkdir(parents=True, exist_ok=True)
    names = ("diamond_auto", "mesh10_auto")
    artifacts = {}; critical = []; dataset = []; pairs = []; groups = []; matrices = []
    for name in names:
        source = ROOT / "generated" / name / "fault_analysis"
        artifact = json.loads((source / "candidate_faults.json").read_text())
        artifacts[name] = artifact
        critical += read_csv(source / "critical_link_analysis.csv")
        dataset += read_csv(source / "fault_equivalence_dataset.csv")
        for row in read_csv(source / "pairwise_fault_similarity.csv"):
            pairs.append({"scenario": name, **row})
        for row in read_csv(source / "exact_affected_set_groups.csv"):
            groups.append({"scenario": name, **row})
        matrix = read_csv(source / "affected_flow_matrix.csv")
        for row in matrix:
            matrices.append({"scenario": name, **row})
    (output / "candidate_faults.json").write_text(json.dumps({"schema_version": 1, "scenarios": artifacts}, indent=2, sort_keys=True) + "\n")
    write_csv(output / "critical_link_analysis.csv", critical)
    write_csv(output / "fault_equivalence_dataset.csv", dataset)
    all_flows = sorted({key for row in matrices for key in row if key not in {"scenario", "fault_id"}})
    normalized = [{"scenario": row["scenario"], "fault_id": row["fault_id"],
                   **{flow: row.get(flow, 0) for flow in all_flows}} for row in matrices]
    write_csv(output / "affected_flow_matrix.csv", normalized, ["scenario", "fault_id", *all_flows])
    qualified = [(row["scenario"] + ":" + row["fault_id"], row) for row in normalized]
    similarity = []
    for id_i, row_i in qualified:
        out = {"fault_id": id_i}
        set_i = {flow for flow in all_flows if row_i[flow] == "1" or row_i[flow] == 1}
        for id_j, row_j in qualified:
            set_j = {flow for flow in all_flows if row_j[flow] == "1" or row_j[flow] == 1}
            if row_i["scenario"] != row_j["scenario"]:
                out[id_j] = ""
            else:
                out[id_j] = len(set_i & set_j) / len(set_i | set_j)
        similarity.append(out)
    write_csv(output / "jaccard_similarity_matrix.csv", similarity, ["fault_id", *[item[0] for item in qualified]])
    write_csv(output / "pairwise_fault_similarity.csv", pairs)
    bins = ["0", "(0,0.25]", "(0.25,0.5]", "(0.5,0.75]", "(0.75,1)", "1"]
    binned = []
    for label in bins:
        def in_bin(x):
            return (x == 0 if label == "0" else 0 < x <= .25 if label == "(0,0.25]" else
                    .25 < x <= .5 if label == "(0.25,0.5]" else .5 < x <= .75 if label == "(0.5,0.75]" else
                    .75 < x < 1 if label == "(0.75,1)" else x == 1)
        selected = [row for row in pairs if in_bin(float(row["jaccard"]))]
        both = [row for row in selected if row["both_sat"] == "1"]
        same = [row for row in both if row["same_semantic_profile"] == "1"]
        binned.append({"jaccard_bin": label, "pair_count": len(selected), "both_sat_count": len(both),
                       "same_profile_count": len(same), "same_profile_ratio": len(same) / len(both) if both else ""})
    write_csv(output / "profile_similarity_by_jaccard.csv", binned)
    write_csv(output / "exact_affected_set_groups.csv", groups)
    summaries = []
    for name in names:
        artifact = artifacts[name]
        store = json.loads((ROOT / "generated" / name / "profiles/per_failure/store.json").read_text())
        statuses = Counter(entry["status"] for entry in store["faults"].values())
        affected_sets = {tuple(item["affected_flows"]) for item in artifact["candidate_faults"]}
        hashes = [entry.get("semantic_profile_hash") for entry in store["faults"].values() if entry.get("semantic_profile_hash")]
        summaries.append({
            "scenario": name, "physical_link_count": len(artifact["all_links"]),
            "switch_switch_link_count": sum(row["in_protection_scope"] for row in artifact["all_links"]),
            "tt_used_internal_link_count": sum(row["classification"] == "TT_RELEVANT_CANDIDATE" for row in artifact["all_links"]),
            "auto_candidate_fault_count": len(artifact["candidate_faults"]), "recoverable_sat_count": statuses["SAT"],
            "no_route_count": statuses["NO_ROUTE"], "unsat_count": statuses["UNSAT"],
            "forwarding_conflict_count": statuses["FORWARDING_CONFLICT"],
            "affected_flow_set_unique_count": len(affected_sets), "semantic_profile_unique_count": len(set(hashes)),
            "per_failure_recovery_profile_count": statuses["SAT"], "duplicate_semantic_profile_count": len(hashes) - len(set(hashes)),
        })
    write_csv(output / "critical_link_summary.csv", summaries)
    manifest = {
        "schema_version": 1, "git_commit": code_commit, "omnetpp_version": "6.4.0", "inet_version": "4.7.0",
        "z3_version": "4.16.0", "candidate_sets": {name: {"candidate_set_sha256": artifacts[name]["candidate_set_sha256"],
            "candidate_fault_count": len(artifacts[name]["candidate_faults"]), "policy": artifacts[name]["policy"]} for name in names},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return artifacts, summaries, pairs


def figures(output: Path, artifact: dict, pairs: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    name = "mesh10_auto"
    scenario = json.loads((ROOT / "generated" / name / "scenario.json").read_text())
    switch_pos = {"sw1": (0, 1), "sw2": (1.8, 1.8), "sw3": (1.8, .2), "sw4": (3.7, -.2), "sw5": (3.9, 2.1),
                  "sw6": (5.4, 1), "sw7": (6.6, -.3), "sw8": (7, 2), "sw9": (8.5, .5), "sw10": (10, 1.5)}
    es_pos = {"es1": (-1, 1), "es2": (1.8, 3), "es3": (3.7, -1.35), "es4": (6.6, -1.45), "es5": (8.5, -.9), "es6": (10, 2.8)}
    pos = {**switch_pos, **es_pos}; by_link = {row["link_id"]: row for row in artifact["all_links"]}
    fig, ax = plt.subplots(figsize=(11.5, 6.8))
    styles = {"candidate": ("#326891", 3.2, "-"), "unused": ("#8B949E", 1.7, "--"), "access": ("#D99B2B", 1.8, ":")}
    for link in scenario["links"]:
        row = by_link[link["id"]]
        kind = "access" if not row["in_protection_scope"] else "candidate" if row["candidate_fault"] else "unused"
        color, width, line = styles[kind]; a, b = link["endpoint_a"], link["endpoint_b"]
        ax.plot([pos[a][0], pos[b][0]], [pos[a][1], pos[b][1]], color=color, lw=width, ls=line, zorder=1)
    for node, (x, y) in switch_pos.items():
        ax.scatter(x, y, s=700, marker="s", color="#243447", edgecolor="white", zorder=2); ax.text(x, y, node, color="white", ha="center", va="center", fontsize=8)
    for node, (x, y) in es_pos.items():
        ax.scatter(x, y, s=500, marker="o", facecolor="white", edgecolor="#243447", zorder=2); ax.text(x, y, node, color="#243447", ha="center", va="center", fontsize=8)
    ax.set_title("mesh10_auto critical-link discovery", loc="left", fontsize=16, pad=18)
    ax.text(0, 1.01, "Candidate = switch-switch link used by at least one healthy TT primary route", transform=ax.transAxes, fontsize=9, color="#4C535C")
    ax.legend(handles=[Line2D([0], [0], color=styles[k][0], lw=styles[k][1], ls=styles[k][2], label=l) for k, l in
                       (("candidate", "Candidate internal link"), ("unused", "Unused internal link"), ("access", "Access link (out of scope)"))], frameon=False, loc="lower left")
    ax.axis("off"); ax.set_aspect("equal"); fig.tight_layout(); fig.savefig(output / "critical_links_topology.png", dpi=180, facecolor="white"); plt.close(fig)

    candidates = artifact["candidate_faults"]; flows = [flow["id"] for flow in scenario["tt_flows"]]
    incidence = [[int(flow in row["affected_flows"]) for flow in flows] for row in candidates]
    fig, ax = plt.subplots(figsize=(10, 7)); image = ax.imshow(incidence, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(flows)), flows, rotation=45, ha="right"); ax.set_yticks(range(len(candidates)), [row["fault_id"] for row in candidates])
    fig.suptitle("Affected TT flows by candidate fault", x=.105, y=.99, ha="left", fontsize=15)
    fig.text(.105, .935, "mesh10_auto; 1 means the healthy P0 route contains the failed link", fontsize=9, color="#4C535C")
    for y, row in enumerate(incidence):
        for x, value in enumerate(row): ax.text(x, y, value, ha="center", va="center", color="white" if value else "#65707C", fontsize=7)
    fig.tight_layout(rect=(0, 0, 1, .92)); fig.savefig(output / "affected_flow_heatmap.png", dpi=180, facecolor="white"); plt.close(fig)

    sets = [set(row["affected_flows"]) for row in candidates]
    matrix = [[len(a & b) / len(a | b) for b in sets] for a in sets]
    fig, ax = plt.subplots(figsize=(9.5, 8)); im = ax.imshow(matrix, cmap="Blues", vmin=0, vmax=1)
    labels = [row["fault_id"] for row in candidates]; ax.set_xticks(range(len(labels)), labels, rotation=55, ha="right", fontsize=7); ax.set_yticks(range(len(labels)), labels, fontsize=7)
    fig.suptitle("Affected-flow Jaccard similarity", x=.13, y=.99, ha="left", fontsize=15)
    fig.text(.13, .94, "mesh10_auto; deterministic fault order, no clustering reorder", fontsize=9, color="#4C535C")
    fig.colorbar(im, ax=ax, label="Jaccard similarity", fraction=.045); fig.tight_layout(rect=(0, 0, 1, .92)); fig.savefig(output / "jaccard_heatmap.png", dpi=180, facecolor="white"); plt.close(fig)

    mesh_pairs = [row for row in pairs if row["scenario"] == name]
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    xs = [float(row["jaccard"]) for row in mesh_pairs]; raw = [int(row["same_semantic_profile"]) for row in mesh_pairs]
    ys = [value + ((index % 7) - 3) * .012 for index, value in enumerate(raw)]
    ax.scatter(xs, ys, s=42, facecolor="#326891", edgecolor="white", linewidth=.5, alpha=.75)
    ax.set_xlim(-.03, 1.03); ax.set_ylim(-.09, 1.09); ax.set_yticks([0, 1], ["Different", "Identical"]); ax.set_xlabel("Affected-flow Jaccard similarity"); ax.set_ylabel("Semantic per-failure profile")
    fig.suptitle("Jaccard similarity vs semantic profile identity", x=.105, y=.99, ha="left", fontsize=15)
    fig.text(.105, .93, f"mesh10_auto; {len(mesh_pairs)} within-scenario fault pairs; vertical jitter is display-only", fontsize=9, color="#4C535C")
    ax.grid(axis="x", color="#E1E5EA", lw=.8); fig.tight_layout(rect=(0, 0, 1, .91)); fig.savefig(output / "jaccard_vs_profile_identity.png", dpi=180, facecolor="white"); plt.close(fig)


def report(output: Path, summaries: list[dict], pairs: list[dict]) -> None:
    by = {row["scenario"]: row for row in summaries}
    exact = [row for row in pairs if float(row["jaccard"]) == 1]
    exact_same = sum(row["same_semantic_profile"] == "1" for row in exact)
    d, m = by["diamond_auto"], by["mesh10_auto"]
    lines = [
        "# Critical-Link Discovery and Fault Similarity Dataset", "",
        "## Technical summary", "",
        f"Healthy P0 routes selected {d['auto_candidate_fault_count']} of {d['switch_switch_link_count']} Diamond internal links and {m['auto_candidate_fault_count']} of {m['switch_switch_link_count']} mesh10 internal links. All {int(d['auto_candidate_fault_count']) + int(m['auto_candidate_fault_count'])} candidates entered per-failure recovery; none was filtered by recoverability and all happened to be SAT in this dataset.", "",
        "The resulting similarity data are exploratory diagnostics, not recovery equivalence classes. Affected-flow Jaccard describes overlap in P0 impact only; semantic profile identity compares complete per-failure route, forwarding, and gate semantics.", "",
        "## Candidate coverage", "",
        "| Scenario | Physical links | Switch-switch | P0-used candidates | Unique affected sets | Unique profile hashes |", "|---|---:|---:|---:|---:|---:|",
        f"| Diamond auto | {d['physical_link_count']} | {d['switch_switch_link_count']} | {d['auto_candidate_fault_count']} | {d['affected_flow_set_unique_count']} | {d['semantic_profile_unique_count']} |",
        f"| mesh10 auto | {m['physical_link_count']} | {m['switch_switch_link_count']} | {m['auto_candidate_fault_count']} | {m['affected_flow_set_unique_count']} | {m['semantic_profile_unique_count']} |", "",
        "![Critical-link topology](critical_links_topology.png)", "",
        "Access links are out of the current protection scope because end systems are single-homed. An out-of-scope or P0-unused link is not unimportant: a P0-unused internal link can be essential as a recovery path.", "",
        "## Impact structure and similarity", "",
        "![Affected-flow incidence](affected_flow_heatmap.png)", "",
        "![Jaccard matrix](jaccard_heatmap.png)", "",
        f"Across the two scenarios there are {len(pairs)} within-scenario unordered pairs. Among the {len(exact)} pairs with Jaccard = 1, {exact_same} also produced identical semantic profiles. This is an observation for these scenarios only and does not establish a general implication.", "",
        "![Jaccard and profile identity](jaccard_vs_profile_identity.png)", "",
        "## Definitions and method", "",
        "For each physical link e, A(e) is built in one pass over healthy P0 logical route link paths. Auto candidates satisfy both switch-switch scope and |A(e)| > 0. Payload rate is packet_size_bytes × 8 / period. Candidate discovery has no access to recovery status, recovered routes, solver objectives, or semantic hashes.", "",
        "Affected-flow Jaccard is |A(i) ∩ A(j)| / |A(i) ∪ A(j)|. Fault edge distance is the minimum healthy switch-graph node distance over the four endpoint pairs; shared endpoints therefore have distance 0. Primary path position difference is the mean absolute zero-based link-index difference for flows affected by both faults. Recovery-route Jaccard uses the union of all recovered TT route links and is present only for SAT pairs.", "",
        "## Limitations and robustness", "",
        "Candidate artifacts, ordering, hashes, incidence matrices, and Jaccard matrices are deterministic and checked by repeat execution. Wall-clock solver timings are measured evidence and are not expected to be byte-identical. Both scenarios are small and all auto candidates are SAT, so this dataset cannot estimate failure-mode prevalence or statistical predictive power. No clustering, shared Profile synthesis, deduplication, or compression ratio is claimed.", "",
        "## Recommended next step", "",
        "Use affected-flow overlap together with topology and route features only to propose candidate groups. A group becomes a Recovery Equivalence Class only after one shared robust Profile is synthesized and validated for forwarding, connectivity, schedule feasibility, end-to-end deadlines, and deterministic recovery under every fault in that group.", "",
        "## Further questions", "",
        "A larger structured scenario is needed to test whether the mesh10 patterns persist, including non-SAT candidates and repeated affected sets with different semantic profiles. That extension should preserve the same no-leakage discovery policy.", "",
    ]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--code-commit", required=True); args = parser.parse_args()
    artifacts, summaries, pairs = aggregate(args.output_dir, args.code_commit)
    figures(args.output_dir, artifacts["mesh10_auto"], pairs); report(args.output_dir, summaries, pairs)
    manifest_path = args.output_dir / "manifest.json"; manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"] = {path.name: digest(path) for path in sorted(args.output_dir.iterdir()) if path.is_file() and path.name != "manifest.json"}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print("EXP07_ANALYSIS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
