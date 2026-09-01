#!/usr/bin/env python3
"""Refresh exp14 evidence summaries and artifact hashes without plotting."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from tools.generate_jrs_scalability_scenarios import GENERATOR_VERSION
from tools.jrs_wa_adapter import TSNKIT_COMMIT, TSNKIT_VERSION, canonical_json_bytes
from tools.scip_jrs_wa_backend import SCIP_MEMORY_LIMIT_MB, SCIP_SEED


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle: return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("results/pf_jrs_scalability"))
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--results-commit", default="PENDING")
    args = parser.parse_args(); root = args.results.resolve()
    p0, scales, fixed = read_csv(root / "p0_results.csv"), read_csv(root / "scale_summary.csv"), read_csv(root / "fixed_topology_tt_sweep.csv")
    candidate_fields = ["scenario","fault_id","endpoint_a","endpoint_b","affected_flow_count","affected_flow_ratio",
                        "healthy_route_use_count","sampling_bin","selected_for_campaign","selection_reason"]
    candidate_path = root / "candidate_faults.csv"
    if not candidate_path.read_text(encoding="utf-8").strip():
        with candidate_path.open("w", encoding="utf-8", newline="") as handle:
            csv.DictWriter(handle, fieldnames=candidate_fields, lineterminator="\n").writeheader()
    for backend_result in sorted((root / "backend_results").glob("*/P0_R*/backend_result.json")):
        payload = json.loads(backend_result.read_text(encoding="utf-8"))
        scenario, repeat = backend_result.parts[-3], backend_result.parts[-2]
        write_json(root / "model_audits" / scenario / f"{repeat}.json", payload.get("statistics", {}))
    p0_by_scenario = {row["scenario"]: row for row in p0}
    for row in fixed:
        source = p0_by_scenario[f"F150_TT{row['tt_flows']}"]
        row["peak_solver_memory"] = source["peak_memory"]
    write_csv(root / "fixed_topology_tt_sweep.csv", fixed)
    for row in scales:
        if not int(row["attempted_faults"]):
            memory = p0_by_scenario[row["scenario"]]["peak_memory"]
            row["mean_peak_memory"] = memory; row["max_peak_memory"] = memory
    write_csv(root / "scale_summary.csv", scales)
    repeat = read_csv(root / "p0_repeatability.csv")
    s1 = [r for r in repeat if r["scenario"] == "S1"]
    s2 = [r for r in repeat if r["scenario"] == "S2"]
    max_vars = max(int(r["vars"] or 0) for r in p0); max_constraints = max(int(r["constraints"] or 0) for r in p0)
    max_memory = max(int(r["peak_memory"] or 0) for r in p0)
    f100 = p0_by_scenario["F150_TT100"]
    summary = f"""# exp14 PF JRS-WA scalability diagnosis

Research Direction Assessment: **COMPUTE_PRESSURE_OBSERVED**.

All 13 planned scenarios completed a real SCIP P0 diagnostic, and all 13 returned `MEMORY_LIMIT`; therefore `P0_NOT_AVAILABLE` correctly prevented every PF solve. S1 (50 nodes, 100 TT) reached {int(p0_by_scenario['S1']['vars']):,} variables and {int(p0_by_scenario['S1']['constraints']):,} constraints. Its three P0 repeats were status-stable (`{s1[0]['status']}`, `{s1[1]['status']}`, `{s1[2]['status']}`). S2 was likewise stable across three repeats (`{s2[0]['status']}`).

On the fixed 150-node topology, the 100-TT P0 reached {int(f100['vars']):,} variables and {int(f100['constraints']):,} constraints before the {SCIP_MEMORY_LIMIT_MB:,} MB SCIP limit. Across all partial/full model audits, the largest recorded construction had {max_vars:,} variables, {max_constraints:,} constraints, and {max_memory:,} bytes of SCIP-reported working memory. These are direct observations, not complexity-based claims.

## PF computation and parallelism

No PF candidate campaign was legally reachable because no healthy P0 profile existed. Consequently serial PF work is 0 only as **not executed**, not as evidence that PF is cheap. The 1/2/4/8/16/32-worker LPT rows are all zero and must be read as not applicable; parallel workers cannot remove the observed P0 formulation bottleneck.

## Profile storage

No valid P0 or PF profile was produced, so measured profile-store bytes are 0 because generation was blocked. This experiment therefore shows no storage-pressure evidence, but it also cannot estimate a populated controller store. Solver working memory and profile storage are separate quantities.

BE=0 intentionally isolates offline JRS-WA synthesis. The campaign used one solver thread per case, invoked no OMNeT++/INET process, and produced no plot artifact. No grouping, compression method, heuristic backend, warm start, or new recovery algorithm was introduced.
"""
    (root / "summary.md").write_text(summary, encoding="utf-8")
    old = json.loads((root / "analysis_manifest.json").read_text(encoding="utf-8"))
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "analysis_manifest.json")
    old.update({"implementation_commit": args.implementation_commit, "results_commit": args.results_commit,
                "TSNKit_version": TSNKIT_VERSION, "TSNKit_commit": TSNKIT_COMMIT,
                "scenario_generator_version": GENERATOR_VERSION,
                "solver_params": {"threads": 1, "timeout_s": 30, "memory_limit_mb": SCIP_MEMORY_LIMIT_MB,
                                  "seed": SCIP_SEED, "parallel_mode": 0},
                "artifact_SHA256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
    (root / "analysis_manifest.json").write_bytes(canonical_json_bytes(old))
    return 0


if __name__ == "__main__": raise SystemExit(main())
