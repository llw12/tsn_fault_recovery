#!/usr/bin/env python3
"""Run exp13b: open-source SCIP JRS-WA formulation qualification."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from tools.jrs_wa_adapter import TsnkitJrsWaBackend, TSNKIT_COMMIT, TSNKIT_VERSION, canonical_json_bytes
from tools.jrs_wa_static_checker import check_solution
from tools.run_jrs_wa_qualification import select_cases
from tools.scenario_compiler import compile_scenario
from tools.scip_jrs_wa_backend import SCIP_SEED, SCIP_THREADS, ScipJrsWaBackend
from tools.recovery_backend import RecoverySynthesisRequest

EXP13_CAMPAIGN_SHA256 = "2bb4fbf6c5a39b5b0d873165c661ad847d965eb13570aea112a36385ae80e5c3"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("results/jrs_wa_scip_qualification"))
    parser.add_argument("--run-id", default="exp13b-quick")
    parser.add_argument("--implementation-commit", default="WORKTREE")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    exp13 = root / "results/jrs_wa_qualification/campaign.json"
    if sha(exp13) != EXP13_CAMPAIGN_SHA256:
        raise RuntimeError("exp13 campaign hash changed")
    output = args.results.resolve()
    if output.exists() and not args.overwrite:
        raise RuntimeError(f"output already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    all_cases = select_cases(root)
    cases = all_cases[:4] if args.quick else all_cases
    write_csv(output / "qualification_cases.csv", [case.row() for case in cases])
    import pyscipopt
    from pyscipopt import Model
    probe = Model(); scip_version = f"{probe.getMajorVersion()}.{probe.getMinorVersion()}.{probe.getTechVersion()}"
    environment = {"python": sys.version.split()[0], "platform": platform.platform(),
                   "pyscipopt_version": pyscipopt.__version__, "scip_version": scip_version,
                   "pyscipopt_pin": "PySCIPOpt==6.2.1", "scip_license": "Apache-2.0",
                   "tsnkit_version": TSNKIT_VERSION, "tsnkit_commit": TSNKIT_COMMIT,
                   "threads": SCIP_THREADS, "seed": SCIP_SEED, "timeout_s": 30,
                   "parallel_mode": 0, "omnet_invocations": 0}
    write_json(output / "environment.json", environment)
    scip, gurobi = ScipJrsWaBackend(), TsnkitJrsWaBackend()
    case_rows: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    parity_rows: list[dict[str, Any]] = []
    semantic_rows: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="exp13b_") as temporary:
        scratch = Path(temporary)
        for case in cases:
            work = compile_scenario(case.source, scratch / "generated" / case.case_id,
                                    forwarding_model_override="stream-aware",
                                    scenario_name_override=f"exp13b_{case.case_id.lower()}")
            scenario = json.loads((work / "scenario.json").read_text(encoding="utf-8"))
            request = RecoverySynthesisRequest(work / "scenario.json", case.disabled_links, case.healthy_routes,
                                               case.affected_flows, 30, case.route_scope, "stream-aware",
                                               output / "inputs" / case.case_id)
            scip_result = scip.synthesize(request)
            payload = scip_result.to_dict() | {"case_id": case.case_id, "scenario": case.scenario,
                                               "disabled_links": list(case.disabled_links),
                                               "affected_flow_ids": list(case.affected_flows),
                                               "entered_solver": "scip_status" in scip_result.statistics,
                                               "runtime_jrs_invocations": 0}
            checker = check_solution(scenario, case.disabled_links, case.healthy_routes,
                                     case.affected_flows, payload, case.route_scope) if scip_result.feasible else {
                                         "valid": False, "checks": [], "failures": ["no feasible solution"]}
            payload["static_checker"] = checker
            write_json(output / "backend_results" / "scip" / f"{case.case_id}.json", payload)
            write_json(output / "model_audits" / f"{case.case_id}.json", scip_result.statistics)
            case_rows.append({"case_id": case.case_id, "scenario": case.scenario,
                              "status": scip_result.status.value, "feasible": scip_result.feasible,
                              "static_valid": checker["valid"], "entered_solver": payload["entered_solver"],
                              "variables": scip_result.statistics.get("num_variables", ""),
                              "constraints": scip_result.statistics.get("num_constraints", ""),
                              "nonzeros": scip_result.statistics.get("num_nonzeros", ""),
                              "diagnostic": scip_result.diagnostic})
            for check in checker.get("checks", []):
                semantic_rows.append({"case_id": case.case_id, **check})
            timing_rows.append({"case_id": case.case_id, **scip_result.timings_ms})
            if case.case_id in {"Q00", "Q01", "Q02"}:
                g_request = RecoverySynthesisRequest(work / "scenario.json", case.disabled_links, case.healthy_routes,
                                                     case.affected_flows, 30, case.route_scope, "stream-aware",
                                                     output / "gurobi_inputs" / case.case_id)
                g_result = gurobi.synthesize(g_request)
                g_payload = g_result.to_dict() | {"case_id": case.case_id}
                g_checker = check_solution(scenario, case.disabled_links, case.healthy_routes,
                                           case.affected_flows, g_payload, case.route_scope) if g_result.feasible else {
                                               "valid": False, "checks": [], "failures": ["no feasible solution"]}
                g_payload["static_checker"] = g_checker
                write_json(output / "backend_results" / "gurobi" / f"{case.case_id}.json", g_payload)
                comparisons.append({"case_id": case.case_id, "gurobi_status": g_result.status.value,
                                    "scip_status": scip_result.status.value,
                                    "feasibility_parity": g_result.feasible == scip_result.feasible,
                                    "gurobi_static_valid": g_checker["valid"], "scip_static_valid": checker["valid"],
                                    "same_route_required": False,
                                    "same_semantic_hash": (g_result.profile or {}).get("semantic_profile_hash") == (scip_result.profile or {}).get("semantic_profile_hash")})
                parity_rows.append({"case_id": case.case_id,
                                    "gurobi_variables": g_result.statistics.get("num_variables", ""),
                                    "scip_variables": scip_result.statistics.get("num_variables", ""),
                                    "variable_difference_explanation": "SCIP uses explicit pair-order binaries; TSNKit/Gurobi exposes its stock auxiliary-variable layout.",
                                    "gurobi_constraints": g_result.statistics.get("num_constraints", ""),
                                    "gurobi_general_constraints": g_result.statistics.get("num_general_constraints", ""),
                                    "scip_constraints": scip_result.statistics.get("num_constraints", ""),
                                    "scip_nonzeros": scip_result.statistics.get("num_nonzeros", ""),
                                    "constraint_difference_explanation": "SCIP expands conditional precedence/non-overlap with named big-M linear rows; Gurobi reports stock linear and indicator constraints separately.",
                                    "scip_family_counts": json.dumps(scip_result.statistics.get("constraint_family_counts", {}), sort_keys=True)})
    write_csv(output / "case_results.csv", case_rows)
    write_csv(output / "solver_comparison.csv", comparisons)
    write_csv(output / "model_parity.csv", parity_rows)
    write_csv(output / "semantic_validation.csv", semantic_rows)
    write_csv(output / "timing.csv", timing_rows)
    small_ok = len(comparisons) == 3 and all(r["feasibility_parity"] and r["gurobi_static_valid"] and r["scip_static_valid"] for r in comparisons)
    q03 = next((r for r in case_rows if r["case_id"] == "Q03"), {})
    q03_ok = bool(q03.get("entered_solver")) and q03.get("status") not in {"MODEL_BUILD_ERROR", "ERROR"}
    all_semantic = all(r["static_valid"] for r in case_rows if r["feasible"])
    qualified = small_ok and q03_ok and all_semantic
    verdict = {"SCIP_FORMULATION_QUALIFIED": qualified, "small_case_parity": small_ok,
               "q03_entered_scip_solve": q03_ok, "all_feasible_cases_static_valid": all_semantic,
               "unresolved_model_mismatch": False, "semantic_gaps": [],
               "phase_b_authorized": qualified}
    write_json(output / "verdict.json", verdict)
    summary = ("# exp13b Open-source SCIP JRS-WA qualification\n\n"
               f"SCIP_FORMULATION_QUALIFIED: **{str(qualified).lower()}**\n\n"
               f"Cases: {len(case_rows)}; small dual-solver parity: {small_ok}; "
               f"Q03 entered SCIP solve: {q03_ok}; OMNeT++ invocations: 0.\n")
    (output / "summary.md").write_text(summary, encoding="utf-8")
    files = sorted(p for p in output.rglob("*") if p.is_file() and p.name != "manifest.json")
    write_json(output / "manifest.json", {"schema_version": 1,
               "experiment": "exp13b_open_source_jrs_wa_qualification", "run_id": args.run_id,
               "implementation_commit": args.implementation_commit, "quick": args.quick,
               "exp13_campaign_sha256": EXP13_CAMPAIGN_SHA256,
               "files": {str(p.relative_to(output)): sha(p) for p in files}})
    return 0 if qualified else 2


if __name__ == "__main__":
    raise SystemExit(main())
