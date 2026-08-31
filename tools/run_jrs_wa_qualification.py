#!/usr/bin/env python3
"""Run the deterministic exp13 JRS-WA backend qualification campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.jrs_wa_adapter import GUROBI_SEED, TSNKIT_COMMIT, TSNKIT_VERSION, TsnkitJrsWaBackend
from tools.recovery_backend import RecoverySynthesisRequest
from tools.scenario_compiler import compile_scenario

EXP12_CAMPAIGN_SHA256 = "c306a4d5de34761aba96dead957bdcda27cbaed7e3614bd573effd8515333274"
EXP12_RAW_CAMPAIGN_SHA256 = "34c5e2218ffe7494c987f3c36c1942ba555125e27b344c2c4be9ae4c56d9bf93"
SOLVER_TIMEOUT_S = 30


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@dataclass
class QualificationCase:
    case_id: str
    source: Path
    scenario: str
    level: str
    case_type: str
    disabled_links: tuple[str, ...]
    affected_flows: tuple[str, ...]
    healthy_routes: dict[str, dict[str, Any]]
    legacy_status: str
    selection_rule: str
    route_scope: str = "affected-only"

    def row(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "scenario": self.scenario, "level": self.level,
            "case_type": self.case_type, "fault_or_group": ";".join(self.disabled_links),
            "member_faults": ";".join(self.disabled_links), "affected_flows": ";".join(self.affected_flows),
            "legacy_status": self.legacy_status, "selection_rule": self.selection_rule,
            "route_scope": self.route_scope, "forwarding_model": "stream-aware",
        }


def routes_by_flow(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {route["flow_id"]: route for route in profile["logical_routes"]}


def affected_flows(routes: dict[str, dict[str, Any]], disabled: tuple[str, ...]) -> tuple[str, ...]:
    failed = set(disabled)
    return tuple(sorted(flow for flow, route in routes.items() if failed.intersection(route["link_path"])))


def _representative(root: Path, case_id: str, config: str, generated_name: str) -> QualificationCase:
    source = root / "configs/scenarios" / config
    profile_path = root / "generated" / generated_name / "profiles/profile0.json"
    scenario_path = root / "generated" / generated_name / "scenario.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    routes = routes_by_flow(profile)
    candidates = sorted(scenario["fault_candidates"])
    fault = next((item for item in candidates if affected_flows(routes, (item,))), None)
    if fault is None:
        raise RuntimeError(f"no route-affecting representative fault in {generated_name}")
    legacy = "UNKNOWN"
    report = root / "generated" / generated_name / "profiles/per_failure/precompute_report.json"
    if report.exists():
        legacy = json.loads(report.read_text(encoding="utf-8"))["faults"].get(fault, {}).get("status", "UNKNOWN")
    return QualificationCase(case_id, source, generated_name, "", "representative-pf", (fault,),
                             affected_flows(routes, (fault,)), routes, legacy,
                             "lexicographically first route-affecting configured candidate")


def select_cases(root: Path) -> list[QualificationCase]:
    exp12 = root / "results/topology_redundancy"
    campaign = json.loads((exp12 / "campaign.json").read_text(encoding="utf-8"))
    frozen = routes_by_flow(json.loads((exp12 / "frozen_primary_routes.json").read_text(encoding="utf-8")))
    level_source = {level: root / "configs/scenarios/exp12_redundancy" / f"{level}.yaml"
                    for level in campaign["levels"]}
    level_scenario = {level: data["scenario_name"] for level, data in campaign["levels"].items()}
    cases = [
        QualificationCase("Q00", root / "configs/scenarios/stream_aware_micro.yaml", "stream_aware_micro", "",
                          "stream-aware-micro", (), ("TT1", "TT2"), {}, "LEGACY_FORWARDING_CONFLICT",
                          "fixed qualification micro-case", "all-reroute"),
        _representative(root, "Q01", "diamond.yaml", "diamond"),
        _representative(root, "Q02", "mesh10.yaml", "mesh10"),
        _representative(root, "Q03", "structured20_auto.yaml", "structured20_auto"),
    ]
    pf = sorted(read_csv(exp12 / "pf_summary.csv"), key=lambda r: (r["level"], r["fault_id"]))
    for case_id, row in zip(("Q04", "Q05"), (r for r in pf if r["raw_status"] == "SAT")):
        disabled = (row["fault_id"],)
        cases.append(QualificationCase(case_id, level_source[row["level"]], level_scenario[row["level"]], row["level"],
                                       "legacy-sat-pf", disabled, affected_flows(frozen, disabled), frozen,
                                       row["raw_status"], "lexicographically first two legacy BFS SAT PF rows"))
    for case_id, row in zip(("Q06", "Q07"), (r for r in pf if r["raw_status"] == "FORWARDING_CONFLICT")):
        disabled = (row["fault_id"],)
        cases.append(QualificationCase(case_id, level_source[row["level"]], level_scenario[row["level"]], row["level"],
                                       "legacy-forwarding-conflict-pf", disabled, affected_flows(frozen, disabled), frozen,
                                       row["raw_status"], "lexicographically first two legacy BFS FORWARDING_CONFLICT PF rows"))
    raw = sorted(read_csv(exp12 / "raw_group_connectivity.csv"),
                 key=lambda r: (r["level"], r["policy_id"], r["group_key"]))
    shared = next((r for r in raw if int(r["member_count"]) > 1 and r["raw_status"] == "FORWARDING_CONFLICT"), None)
    if shared:
        disabled = tuple(shared["group_key"].split(";"))
        cases.append(QualificationCase("Q08", level_source[shared["level"]], level_scenario[shared["level"]], shared["level"],
                                       "legacy-shared-forwarding-conflict", disabled, affected_flows(frozen, disabled), frozen,
                                       shared["raw_status"], "lexicographically first shared raw FORWARDING_CONFLICT group"))
    transitions = sorted(read_csv(exp12 / "group_connectivity_transition.csv"),
                         key=lambda r: (r["group_key"], r["level"]))
    rescued = next((r for r in transitions if r["policy_id"] == "J020" and r["transition"] == "RESCUED"), None)
    if rescued:
        disabled = tuple(rescued["group_key"].split(";"))
        cases.append(QualificationCase("Q09", level_source[rescued["level"]], level_scenario[rescued["level"]], rescued["level"],
                                       "j020-topology-rescued-group", disabled, affected_flows(frozen, disabled), frozen,
                                       rescued["raw_status"], "lexicographically first J020 RESCUED transition from R0 disconnected"))
    expected = [f"Q{i:02d}" for i in range(10)]
    actual = [case.case_id for case in cases]
    if actual != expected:
        raise RuntimeError(f"qualification case class unavailable: expected {expected}, selected {actual}")
    return cases


def gurobi_smoke() -> dict[str, Any]:
    import gurobipy as gp
    started = time.perf_counter_ns()
    model = gp.Model("exp13_license_smoke")
    model.Params.LogToConsole = 0
    model.Params.Threads = 1
    model.Params.Seed = GUROBI_SEED
    x = model.addVar(vtype=gp.GRB.BINARY, name="x")
    model.setObjective(x, gp.GRB.MAXIMIZE)
    model.optimize()
    if model.Status != gp.GRB.OPTIMAL or round(x.X) != 1:
        raise RuntimeError(f"Gurobi smoke failed with status {model.Status}")
    return {"status": "PASS", "gurobipy_version": gp.gurobi.version(),
            "gurobi_version": ".".join(map(str, gp.gurobi.version())),
            "wall_ms": (time.perf_counter_ns() - started) / 1e6, "threads": 1, "seed": GUROBI_SEED}


def parse_vectors(vec_path: Path) -> dict[str, list[tuple[float, int]]]:
    ids: dict[str, str] = {}
    values: dict[str, list[tuple[float, int]]] = {}
    with vec_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "vector" and len(parts) >= 4:
                name = parts[3].strip('"')
                ids[parts[1]] = name
                values.setdefault(name, [])
            elif parts[0] in ids and len(parts) >= 4:
                values[ids[parts[0]]].append((float(parts[2]), int(float(parts[3]))))
    return values


def validation_ini(work: Path, case: QualificationCase, profile: Path, evidence: Path) -> Path:
    port_map = json.loads((work / "port_map.json").read_text(encoding="utf-8"))
    commands = []
    for link_id in case.disabled_links:
        link = port_map["links"][link_id]
        for side in ("a", "b"):
            item = link[side]
            commands.append(f"<set-channel-param t='0s' src-module='{item['node']}' src-gate='ethg$o[{item['interface'][3:]}]' par='disabled' value='true'/>")
    script = "<scenario>" + "".join(commands) + "</scenario>"
    profile_value = json.loads(profile.read_text(encoding="utf-8"))
    gate_lines = []
    for gate in profile_value.get("gate_schedules", []):
        prefix = f"*.{gate['gate_path']}"
        durations = ", ".join(f"{value:.12g}s" for value in gate["durations_s"])
        gate_lines.extend([
            f"{prefix}.initiallyOpen = {str(gate['initially_open']).lower()}",
            f"{prefix}.offset = {gate['offset_s']:.12g}s",
            f"{prefix}.durations = [{durations}]",
        ])
    path = work / "exp13_validation.ini"
    path.write_text(
        "[Config Exp13Validation]\n"
        "sim-time-limit = 3ms\n"
        f"*.scenarioManager.script = xml(\"{script}\")\n"
        "*.scenarioRecoveryController.mode = \"no-recovery\"\n"
        f"*.scenarioRecoveryController.profile0 = readJSON(\"{profile.as_posix()}\")\n"
        f"*.streamForwardingRecorder.outputPath = \"{evidence.as_posix()}\"\n"
        + "\n".join(gate_lines) + "\n",
        encoding="utf-8")
    return path


def validate_omnet(root: Path, work: Path, case: QualificationCase, result: dict[str, Any], scratch: Path) -> dict[str, Any]:
    profile_path = scratch / f"{case.case_id}_profile.json"
    write_json(profile_path, result["profile"])
    evidence = scratch / f"{case.case_id}_forwarding.csv"
    ini = validation_ini(work, case, profile_path.resolve(), evidence.resolve())
    result_dir = scratch / f"{case.case_id}_omnet"
    result_dir.mkdir(parents=True, exist_ok=True)
    workspace = root.parent
    command = [str(root / "tsn_fault_recovery"), "-u", "Cmdenv", "-n", f"{work.parent}:{root / 'src'}:{workspace / 'inet-4.7.0/src'}",
               "-l", str(workspace / "inet-4.7.0/src/INET"), "-f", "base.ini", "-f", ini.name,
               "-c", "Exp13Validation", "--cmdenv-express-mode=true", f"--result-dir={result_dir}"]
    process = subprocess.run(command, cwd=work, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    validation: dict[str, Any] = {"case_id": case.case_id, "exit_code": process.returncode,
                                  "omnet_valid": False, "diagnostic": process.stdout[-2000:]}
    if process.returncode != 0 or not evidence.exists():
        return validation
    observations = read_csv(evidence)
    observed = {(r["flow_id"], r["switch"]): r["observed_egress"] for r in observations
                if r["flow_id"] != "BE_OR_UNKNOWN" and int(r["packet_count"]) > 0}
    expected = {(r["flow_id"], r["switch"]): r["interface"] for r in result["profile"]["routes"]}
    route_match = all(observed.get(key) == value for key, value in expected.items())
    be_forwarded = any(r["flow_id"] == "BE_OR_UNKNOWN" and int(r["packet_count"]) > 0 for r in observations)
    vec_files = list(result_dir.glob("*.vec"))
    if os.environ.get("EXP13_DEBUG_COPY") and vec_files:
        shutil.copy2(vec_files[0], Path(os.environ["EXP13_DEBUG_COPY"]) / f"{case.case_id}.vec")
    vectors = parse_vectors(vec_files[0]) if vec_files else {}
    scenario = json.loads((work / "scenario.json").read_text(encoding="utf-8"))
    flow_defs = {f["id"]: f for f in scenario["tt_flows"]}
    delivery_ok = True
    max_latency_ns = 0
    sent_count = received_count = 0
    for flow_id, flow in flow_defs.items():
        sent = {sequence: at for at, sequence in vectors.get(f"{flow_id}.sentSequence", []) if at >= 0.001}
        received = {sequence: at for at, sequence in vectors.get(f"{flow_id}.receivedSequence", [])}
        sent_count += len(sent); received_count += len(received)
        eligible = {seq: at for seq, at in sent.items() if at + flow["deadline_e2e_s"] <= 0.003}
        if not eligible or any(seq not in received for seq in eligible):
            delivery_ok = False
            continue
        for seq, at in eligible.items():
            latency = received[seq] - at
            max_latency_ns = max(max_latency_ns, round(latency * 1e9))
            if latency > flow["deadline_e2e_s"] + 1e-15:
                delivery_ok = False
    validation.update({"omnet_valid": route_match and delivery_ok,
                       "route_match": route_match, "deadline_and_delivery_pass": delivery_ok,
                       "be_forwarded": be_forwarded, "max_tt_latency_ns": max_latency_ns,
                       "tt_sent_after_stabilization": sent_count, "tt_received_total": received_count,
                       "stream_observations": observations, "diagnostic": "PASS" if route_match and delivery_ok else "VALIDATION_MISMATCH"})
    return validation


def preflight(root: Path) -> None:
    campaign = root / "results/topology_redundancy/campaign.json"
    if sha256_file(campaign) != EXP12_CAMPAIGN_SHA256:
        raise RuntimeError("exp12 campaign hash changed")
    raw = json.loads(campaign.read_text(encoding="utf-8"))["raw_campaign_sha256"]
    if raw != EXP12_RAW_CAMPAIGN_SHA256:
        raise RuntimeError("exp12 raw campaign hash changed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("results/jrs_wa_qualification"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--selection-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    preflight(root)
    cases = select_cases(root)
    case_fields = list(cases[0].row())
    if args.selection_only:
        write_csv(args.results, case_fields, [case.row() for case in cases])
        return 0
    output = args.results.resolve()
    if output.exists():
        raise RuntimeError(f"formal output already exists: {output}")
    output.mkdir(parents=True)
    write_csv(output / "qualification_cases.csv", case_fields, [case.row() for case in cases])
    smoke = gurobi_smoke()
    environment = {"python": sys.version.split()[0], "platform": platform.platform(), "tsnkit_version": TSNKIT_VERSION,
                   "tsnkit_commit": TSNKIT_COMMIT, "solver": smoke, "omnetpp": "6.4.0", "inet": "4.7.0"}
    write_json(output / "environment.json", environment)
    backend = TsnkitJrsWaBackend()
    solver_rows: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    repeats: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="exp13_jrs_wa_") as temporary:
        scratch = Path(temporary)
        for case in cases:
            work = compile_scenario(case.source, scratch / "generated", forwarding_model_override="stream-aware",
                                    scenario_name_override=f"exp13_{case.case_id.lower()}")
            input_dir = output / "inputs" / case.case_id
            request = RecoverySynthesisRequest(work / "scenario.json", case.disabled_links, case.healthy_routes,
                                               case.affected_flows, SOLVER_TIMEOUT_S, case.route_scope,
                                               "stream-aware", input_dir)
            synthesis = backend.synthesize(request)
            payload = synthesis.to_dict()
            payload.update({"case_id": case.case_id, "scenario": case.scenario, "disabled_links": list(case.disabled_links),
                            "affected_flow_ids": list(case.affected_flows), "runtime_jrs_invocations": 0})
            write_json(output / "backend_results" / f"{case.case_id}.json", payload)
            validation = {"case_id": case.case_id, "omnet_valid": False, "diagnostic": "NOT_FEASIBLE"}
            if synthesis.feasible:
                validation = validate_omnet(root, work, case, payload, scratch)
            validations.append(validation)
            stats, timings = synthesis.statistics, synthesis.timings_ms
            solver_rows.append({
                "case_id": case.case_id, "scenario": case.scenario, "case_type": case.case_type,
                "disabled_link_count": len(case.disabled_links), "affected_flow_count": len(case.affected_flows),
                "total_tt_count": len(json.loads((work / "scenario.json").read_text())["tt_flows"]),
                "route_scope": case.route_scope, "forwarding_model": "stream-aware", "legacy_status": case.legacy_status,
                "jrs_status": synthesis.status.value, "feasible": synthesis.feasible, "optimal_proven": synthesis.optimal_proven,
                "solver_timeout_s": SOLVER_TIMEOUT_S, "input_conversion_ms": timings.get("input_conversion", ""),
                "model_build_ms": timings.get("model_build", ""), "solver_wall_ms": timings.get("solver_wall", ""),
                "output_extract_ms": timings.get("output_extract", ""), "profile_convert_ms": timings.get("profile_convert", ""),
                "total_backend_ms": timings.get("total_backend", ""), "num_variables": stats.get("num_variables", ""),
                "num_constraints": stats.get("num_constraints", ""), "mip_gap": stats.get("mip_gap", ""),
                "objective": synthesis.objective if synthesis.objective is not None else "",
                "semantic_profile_hash": synthesis.profile.get("semantic_profile_hash", "") if synthesis.profile else "",
                "omnet_valid": validation["omnet_valid"], "diagnostic": synthesis.diagnostic,
            })
            if case.case_id in {"Q01", "Q06", "Q09"}:
                for repeat in range(1, 4):
                    repeat_request = RecoverySynthesisRequest(work / "scenario.json", case.disabled_links, case.healthy_routes,
                                                              case.affected_flows, SOLVER_TIMEOUT_S, case.route_scope,
                                                              "stream-aware", scratch / "repeat" / case.case_id / str(repeat))
                    repeated = backend.synthesize(repeat_request)
                    repeats.append({"case_id": case.case_id, "repeat": repeat, "status": repeated.status.value,
                                    "objective": repeated.objective if repeated.objective is not None else "",
                                    "semantic_profile_hash": repeated.profile.get("semantic_profile_hash", "") if repeated.profile else "",
                                    "solver_wall_ms": repeated.timings_ms.get("solver_wall", "")})
    write_csv(output / "jrs_solver_results.csv", list(solver_rows[0]), solver_rows)
    omnet_rows = [{k: v for k, v in row.items() if k != "stream_observations"} for row in validations]
    write_csv(output / "omnet_validation.csv", sorted({k for row in omnet_rows for k in row}), omnet_rows)
    forwarding_rows = []
    for validation in validations:
        backend_payload = json.loads((output / "backend_results" / f"{validation['case_id']}.json").read_text())
        installed = {(r["flow_id"], r["switch"]): r for r in (backend_payload.get("profile") or {}).get("routes", [])}
        for row in validation.get("stream_observations", []):
            rule = installed.get((row["flow_id"], row["switch"]), {})
            forwarding_rows.append({"case_id": validation["case_id"], "flow_id": row["flow_id"],
                                    "switch": row["switch"], "expected_egress": rule.get("interface", "BE_FALLBACK"),
                                    "installed_stream_handle": rule.get("stream_handle", 0),
                                    "installed_egress": rule.get("interface", "BE_FALLBACK"),
                                    "observed_egress": row["observed_egress"],
                                    "packet_count": row["packet_count"],
                                    "match": not rule or rule.get("interface") == row["observed_egress"]})
    write_csv(output / "stream_forwarding_validation.csv",
              ["case_id", "flow_id", "switch", "expected_egress", "installed_stream_handle",
               "installed_egress", "observed_egress", "packet_count", "match"], forwarding_rows)
    write_csv(output / "route_repeatability.csv",
              ["case_id", "repeat", "status", "objective", "semantic_profile_hash", "solver_wall_ms"], repeats)
    write_json(output / "campaign.json", {"schema_version": 1, "experiment": "exp13_jrs_wa_qualification",
               "run_id": args.run_id, "implementation_commit": args.implementation_commit,
               "solver_timeout_s": SOLVER_TIMEOUT_S, "threads": 1, "seed": GUROBI_SEED,
               "exp12_campaign_sha256": EXP12_CAMPAIGN_SHA256, "cases": [case.row() for case in cases]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
