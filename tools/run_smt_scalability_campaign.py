#!/usr/bin/env python3
"""Serial, resumable solver-only campaign for exp11."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.approximate_equivalence import Policy, agglomerate, build_pre_fault_features
from tools.critical_link import CriticalLinkAnalyzer, candidate_ids, write_analysis
from tools.scenario_compiler import compile_scenario
from tools.scenario_model import load_scenario

TIMEOUT_MS = 30_000
MODES = ("PRODUCTION_OPTIMIZE", "BENCHMARK_FEASIBILITY_ONLY")
POLICIES = (Policy("J100", "JACCARD", 1.0), Policy("J040", "JACCARD", .4),
            Policy("J020", "JACCARD", .2))
HARD_MODEL_VERSION = "fixed-route-single-cycle-v2-instrumented"


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


@dataclass(frozen=True)
class Case:
    scenario: str
    case_id: str
    case_type: str
    policy_id: str
    fault_id: str
    group_id: str
    members: tuple[str, ...]
    affected: tuple[str, ...]


def shared_case_id(scenario: str, policy: str, members: list[str]) -> str:
    return f"{scenario}:SHARED:{policy}:{sha(sorted(members))[:16]}"


def build_cases(model, candidate: dict) -> list[Case]:
    scenario = model.scenario_name
    cases = [Case(scenario, f"{scenario}:P0", "P0", "", "", "", (), ())]
    for item in sorted(candidate["candidate_faults"], key=lambda row: row["fault_id"]):
        fault = item["fault_id"]
        cases.append(Case(scenario, f"{scenario}:PF:{fault}", "PF", "", fault, "",
                          (fault,), tuple(sorted(item["affected_flows"]))))
    features = build_pre_fault_features(model, candidate)
    fault_map = {row["fault_id"]: set(row["affected_flows"]) for row in candidate["candidate_faults"]}
    for policy in POLICIES:
        grouping, _ = agglomerate(features, policy)
        for group in grouping["groups"]:
            members = sorted(group["member_faults"])
            if len(members) < 2:
                continue
            affected = sorted(set().union(*(fault_map[item] for item in members)))
            cases.append(Case(scenario, shared_case_id(scenario, policy.policy_id, members),
                              "SHARED", policy.policy_id, "", group["group_id"],
                              tuple(members), tuple(affected)))
    return cases


def solver_config_hash() -> str:
    return sha({"timeout_ms": TIMEOUT_MS, "time_quantum_policy": "scenario",
                "objective_policy": "lexicographic-production-v1",
                "hard_model_version": HARD_MODEL_VERSION})


def checkpoint_identity(source: Path) -> dict:
    return {"implementation_commit": git_commit(), "scenario_sha": file_sha(source),
            "solver_config_hash": solver_config_hash()}


def load_checkpoint(path: Path, identity: dict, resume: bool) -> dict:
    if not path.exists():
        return {"identity": identity, "completed": {}}
    state = json.loads(path.read_text())
    if state.get("identity") != identity:
        raise RuntimeError("stale exp11 checkpoint: implementation/scenario/solver identity changed")
    if not resume:
        raise RuntimeError("exp11 checkpoint exists; use --resume")
    return state


def save_checkpoint(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def model_fingerprint(report: dict, scenario_json: dict) -> str:
    timing = [{key: flow[key] for key in ("id", "packet_size_bytes", "period_s",
               "schedule_deadline_budget_s", "release_offset_s", "traffic_class")}
              for flow in scenario_json["tt_flows"]]
    hard_structure = {key: report.get(key, 0) for key in (
        "startTimeVarCount", "orderingBoolVarCount", "otherAuxVarCount",
        "cycleBoundConstraintCount", "releaseConstraintCount", "hopPrecedenceConstraintCount",
        "deadlineConstraintCount", "nonOverlapConstraintCount", "otherHardConstraintCount")}
    return sha({"scenario_sha256": report["scenario_sha256"],
                "disabled_links": sorted(report["disabled_links"]),
                "complete_routes": report["complete_routes"],
                "complete_node_routes": report.get("complete_node_routes", {}), "flow_timing": timing,
                "solver_config_hash": solver_config_hash(), "hard_model_version": HARD_MODEL_VERSION,
                "hard_constraint_structure": hard_structure})


def run_solver(generated: Path, case: Case, mode: str, report_path: Path) -> dict:
    command = [str(ROOT / "tsn_fault_recovery"), "-u", "Cmdenv", "-n",
               f"{ROOT / 'src'}:/home/opp_env/inet-4.7.0/src", "-l",
               "/home/opp_env/inet-4.7.0/src/INET", "-f",
               str(ROOT / "simulations/smt_scalability/omnetpp.ini"),
               f'--*.benchmark.scenario=readJSON("{generated / "scenario.json"}")',
               f'--*.benchmark.portMap=readJSON("{generated / "port_map.json"}")',
               f'--*.benchmark.caseId="{case.case_id}"',
               f'--*.benchmark.solverMode="{mode}"',
               f'--*.benchmark.disabledLinks="{" ".join(case.members)}"',
               f'--*.benchmark.affectedFlowIds="{" ".join(case.affected)}"',
               f'--*.benchmark.solverTimeoutMs={TIMEOUT_MS}',
               f'--*.benchmark.reportOutputPath="{report_path}"']
    completed = subprocess.run(command, cwd=generated, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    if completed.returncode:
        raise RuntimeError(f"SMT benchmark failed for {case.case_id}/{mode}:\n{completed.stdout[-4000:]}")
    return json.loads(report_path.read_text())


def normalize(case: Case, mode: str, repeat: int, report: dict, scenario_json: dict,
              switch_count: int, tt_count: int) -> dict:
    mapping = {
        "activeTtFlowCount": "active_tt_flow_count", "controlledHopCount": "controlled_hop_count",
        "egressCount": "egress_count", "contentedEgressCount": "contented_egress_count",
        "sharedEgressCount": "shared_egress_count", "maxFlowsPerEgress": "max_flows_per_egress",
        "meanFlowsPerUsedEgress": "mean_flows_per_used_egress", "contentionPairCount": "contention_pair_count",
        "startTimeVarCount": "start_time_var_count", "orderingBoolVarCount": "ordering_bool_var_count",
        "otherAuxVarCount": "other_aux_var_count", "totalSymbolicVarCount": "total_symbolic_var_count",
        "cycleBoundConstraintCount": "cycle_bound_constraint_count", "releaseConstraintCount": "release_constraint_count",
        "hopPrecedenceConstraintCount": "hop_precedence_constraint_count", "deadlineConstraintCount": "deadline_constraint_count",
        "nonOverlapConstraintCount": "non_overlap_constraint_count", "otherHardConstraintCount": "other_hard_constraint_count",
        "totalHardConstraintCount": "total_hard_constraint_count", "objectiveCount": "objective_count",
    }
    row = {"scenario": case.scenario, "switch_count": switch_count, "tt_flow_count": tt_count,
           "case_id": case.case_id, "case_type": case.case_type, "policy_id": case.policy_id,
           "fault_id": case.fault_id, "group_id": case.group_id,
           "member_faults": ";".join(case.members), "member_count": len(case.members),
           "mode": mode, "repeat_index": repeat, "status": report["status"],
           "reason_unknown": report.get("reason_unknown", ""),
           "affected_tt_flow_count": len(case.affected), "disabled_link_count": len(case.members),
           "route_total_hops": report.get("route_total_hops", 0),
           "route_mean_hops": report.get("route_mean_hops", 0), "route_max_hops": report.get("route_max_hops", 0),
           "route_wall_ms": report.get("route_wall_ms", 0),
           "model_build_wall_ms": report.get("model_build_wall_ms", 0),
           "z3_check_wall_ms": report.get("z3_check_wall_ms", 0) if report["status"] != "NO_ROUTE" else None,
           "model_extract_wall_ms": report.get("model_extract_wall_ms", 0),
           "schedule_compile_wall_ms": report.get("schedule_compile_wall_ms", 0),
           "total_solver_pipeline_wall_ms": report.get("total_solver_pipeline_wall_ms", report.get("route_wall_ms", 0)),
           "objective_values": report.get("objective_values", []),
           "z3_statistics": report.get("z3_statistics", {}),
           "model_fingerprint": model_fingerprint(report, scenario_json) if report["status"] != "NO_ROUTE" else "",
           "diagnostic": report.get("diagnostic", "")}
    for source, target in mapping.items(): row[target] = report.get(source, 0)
    return row


def machine_metadata() -> dict:
    cpu = "NOT_AVAILABLE"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                cpu = line.split(":", 1)[1].strip(); break
    except OSError: pass
    ram = "NOT_AVAILABLE"
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"): ram = line.split(":", 1)[1].strip(); break
    except OSError: pass
    try: z3_version = subprocess.check_output(["z3", "--version"], text=True).strip()
    except (OSError, subprocess.CalledProcessError): z3_version = "NOT_AVAILABLE"
    return {"cpu_model": cpu, "logical_cpu_count": os.cpu_count(), "ram_total": ram,
            "platform": platform.platform(), "wsl": "microsoft" in platform.release().lower(),
            "python_version": platform.python_version(), "z3_version": z3_version}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", action="append", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scratch-root", type=Path, default=ROOT / "scratch/exp11")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    all_rows, scenario_records = [], []
    for relative in args.scenario:
        source = relative if relative.is_absolute() else ROOT / relative
        model = load_scenario(source)
        # Compile first with no resolved auto candidates.  This permits the P0
        # fixed-timeout result to be recorded even when candidate discovery must
        # be skipped because P0 is UNSAT/UNKNOWN/TIMEOUT.
        generated = compile_scenario(source, ROOT / "generated", ())
        scenario_json = json.loads((generated / "scenario.json").read_text())
        checkpoint_path = args.scratch_root / args.run_id / model.scenario_name / "checkpoint.json"
        state = load_checkpoint(checkpoint_path, checkpoint_identity(source), args.resume)
        p0_case = Case(model.scenario_name, f"{model.scenario_name}:P0", "P0", "", "", "", (), ())

        def execute(cases: list[Case]) -> None:
            for case in cases:
                for mode in MODES:
                    repeat_count = 3 if case.case_type == "P0" and mode == "PRODUCTION_OPTIMIZE" else 1
                    for repeat in range(1, repeat_count + 1):
                        key = f"{case.case_id}|{mode}|{repeat}"
                        if key not in state["completed"]:
                            report_path = checkpoint_path.parent / "reports" / f"{sha(key)[:20]}.json"
                            report_path.parent.mkdir(parents=True, exist_ok=True)
                            report = run_solver(generated, case, mode, report_path)
                            row = normalize(case, mode, repeat, report, scenario_json,
                                            sum(node.type == "switch" for node in model.nodes), len(model.tt_flows))
                            state["completed"][key] = row
                            if case.case_type == "P0" and mode == "PRODUCTION_OPTIMIZE" and repeat == 1:
                                state["healthy_routes"] = report.get("complete_routes", {})
                                state["healthy_node_routes"] = report.get("complete_node_routes", {})
                            save_checkpoint(checkpoint_path, state)
                        row = state["completed"][key]
                        all_rows.append(row)

        execute([p0_case])
        p0_rows = [row for row in all_rows if row["case_id"] == p0_case.case_id and row["mode"] == MODES[0]]
        p0_failed = any(row["status"] != "SAT" for row in p0_rows)
        candidate = {"candidate_faults": []}
        cases = [p0_case]
        if not p0_failed:
            profile0 = {"scenario_sha256": model.sha256(), "logical_routes": [
                {"flow_id": flow.id, "node_path": state["healthy_node_routes"][flow.id],
                 "link_path": state["healthy_routes"][flow.id]} for flow in model.tt_flows]}
            candidate = CriticalLinkAnalyzer.analyze(model, profile0)
            write_analysis(candidate, generated / "fault_analysis")
            generated = compile_scenario(source, ROOT / "generated", candidate_ids(candidate))
            scenario_json = json.loads((generated / "scenario.json").read_text())
            cases = build_cases(model, candidate)
            execute(cases[1:])
        scenario_records.append({"scenario": model.scenario_name,
            "scenario_sha256": scenario_json["scenario_sha256"], "source_sha256": file_sha(source),
            "candidate_fault_count": len(candidate["candidate_faults"]), "case_count": len(cases),
            "time_quantum_s": scenario_json["simulation"]["time_quantum_s"],
            "cycle_time_s": scenario_json["simulation"]["cycle_time_s"],
            "p0_failed": p0_failed})
    payload = {"schema_version": 1, "experiment": "exp11_smt_scalability",
               "run_id": args.run_id, "implementation_commit": git_commit(),
               "parallelism": 1, "solver_timeout_ms": TIMEOUT_MS, "solver_modes": list(MODES),
               "policies": [policy.policy_id for policy in POLICIES],
               "objective_policy": "lexicographic: max_completion, total_completion, stable_start_times",
               "hard_model_version": HARD_MODEL_VERSION, "solver_config_hash": solver_config_hash(),
               "machine": machine_metadata(), "scenarios": scenario_records, "solver_cases": all_rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
