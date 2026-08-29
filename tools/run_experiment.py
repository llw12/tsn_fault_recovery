#!/usr/bin/env python3
"""One-command Scenario Framework v1 experiment runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.result_analyzer import SUMMARY_FIELDS, analyze_run
from tools.omnet_runner import run_omnet
from tools.profile_store import ProfileStoreError, file_sha256, semantic_profile_hash, store_metrics, validate_store
from tools.recovery_modes import MODES, require_implemented
from tools.scenario_model import load_scenario
from tools.analyze_critical_links import discover
from tools.critical_link import affected_flow_set_hash

IMPLEMENTED = tuple(name for name, mode in MODES.items() if mode.implemented)
NOT_IMPLEMENTED = tuple(name for name, mode in MODES.items() if not mode.implemented)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args():
    parser = argparse.ArgumentParser(description="Compile, run, and analyze a scenario-driven TSN experiment")
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=(*IMPLEMENTED, *NOT_IMPLEMENTED, "all"))
    parser.add_argument("--fault", required=True)
    parser.add_argument("--inside-environment", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--offline-lookup-delay-us", type=float, default=0.0,
                        help="simulated preloaded lookup delay; 0 is the ideal lower bound")
    parser.add_argument("--run-id", help="explicit run identifier for orchestrated experiments")
    return parser.parse_args()


def enter_environment(args) -> int:
    if args.scenario.is_absolute():
        try:
            scenario_argument = args.scenario.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            raise SystemExit("When launched from Windows, --scenario must be inside the repository")
    else:
        scenario_argument = args.scenario.as_posix()
    forwarded = ["python3", "tools/run_experiment.py", "--scenario", scenario_argument, "--mode", args.mode,
                 "--fault", args.fault, "--inside-environment"]
    if args.skip_build: forwarded.append("--skip-build")
    forwarded += ["--offline-lookup-delay-us", str(args.offline_lookup_delay_us)]
    if args.run_id: forwarded += ["--run-id", args.run_id]
    command = f"cd {shlex.quote('/home/opp_env/tsn_fault_recovery')} && opp_env run inet-4.7.0 -q -c {shlex.quote(' '.join(shlex.quote(item) for item in forwarded))}"
    executable = ["wsl", "-d", "opp_env", "--", "bash", "-lc", command] if os.name == "nt" else ["bash", "-lc", command]
    return subprocess.run(executable).returncode


def version(command: list[str], fallback: str) -> str:
    try:
        output = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10).stdout.strip()
        return output.splitlines()[0] if output else fallback
    except Exception:
        return fallback


def scalar_from_file(path: Path, name: str) -> float | None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("scalar "):
            fields = shlex.split(raw)
            if len(fields) == 4 and fields[2] == name:
                return float(fields[3])
    return None


def make_manifest(run_dir: Path, scenario: dict, mode: str, fault: str, generated: Path,
                  recovery: bool, store: dict | None = None, store_path: Path | None = None,
                  offline_lookup_delay_s: float | None = None) -> None:
    code_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                 stdout=subprocess.PIPE, check=True).stdout.strip()
    entry = store["faults"][fault] if store else None
    candidate_artifact = json.loads((generated / "fault_analysis/candidate_faults.json").read_text())
    candidate_by_id = {item["fault_id"]: item for item in candidate_artifact["candidate_faults"]}
    candidate = candidate_by_id.get(fault)
    manifest = {
        "schema_version": 1, "scenario_name": scenario["scenario_name"], "scenario_sha256": scenario["scenario_sha256"],
        "mode": mode, "fault_id": fault,
        "fault_candidate_mode": candidate_artifact["policy"]["mode"],
        "fault_candidate_scope": candidate_artifact["policy"].get("scope"),
        "fault_candidate_criterion": candidate_artifact["policy"].get("criterion"),
        "candidate_set_sha256": candidate_artifact["candidate_set_sha256"],
        "candidate_fault_count": len(candidate_artifact["candidate_faults"]),
        "fault_is_candidate": candidate is not None,
        "affected_flow_set_sha256": candidate["affected_flow_set_sha256"] if candidate else affected_flow_set_hash([]),
        "git_commit": code_commit,
        "omnetpp_version": "6.4.0", "inet_version": "4.7.0", "z3_version": version(["z3", "--version"], "unknown"),
        "simulation_duration_s": scenario["simulation"]["duration_s"], "cycle_time_s": scenario["simulation"]["cycle_time_s"],
        "random_seed": scenario["simulation"]["random_seed"], "deterministic": True,
        "profile0_id": "P0", "recovery_profile_id": (entry.get("profile_id") if entry and entry["status"] == "SAT" else (f"online_{fault}" if recovery else None)),
        "generated_ned_sha256": digest(generated / "ScenarioNetwork.ned"), "generated_ini_sha256": digest(generated / "omnetpp.ini"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "profile_strategy": "per-failure" if store else None,
        "profile_store_sha256": file_sha256(store_path) if store_path else None,
        "profile_store_scenario_sha256": store.get("scenario_sha256") if store else None,
        "offline_precompute_code_commit": store.get("precompute_code_commit") if store else None,
        "runtime_code_commit": code_commit,
        "recovery_profile_sha256": entry.get("profile_sha256") if entry else None,
        "offline_lookup_delay_s": offline_lookup_delay_s,
        "runtime_solver_invocations": {"route": 0, "z3": 0} if mode == "offline-per-failure" else None,
        "artifacts": {"scenario": "scenario.json", "port_map": "port_map.json", "profile0": "profile0.json",
                      "candidate_faults": "candidate_faults.json",
                      "recovery_profile": "recovery_profile.json" if recovery else None},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.mode in NOT_IMPLEMENTED:
        try: require_implemented(args.mode)
        except NotImplementedError as error: print(error, file=sys.stderr)
        return 2
    if not args.inside_environment:
        return enter_environment(args)
    source = args.scenario if args.scenario.is_absolute() else ROOT / args.scenario
    model = load_scenario(source)
    generated, candidate_artifact = discover(source, skip_build=args.skip_build)
    candidate_faults = tuple(item["fault_id"] for item in candidate_artifact["candidate_faults"])
    if args.fault not in candidate_faults:
        raise SystemExit(f"fault {args.fault!r} is not a discovered candidate in scenario {model.scenario_name}")
    precompute_dir = generated / "precompute-results"
    precompute_scalars = list(precompute_dir.glob("*.sca"))
    precompute_seconds = scalar_from_file(precompute_scalars[0], "scenario.precompute.totalWallTimeSeconds") if len(precompute_scalars) == 1 else None
    precompute_wall_ms = precompute_seconds * 1e3 if precompute_seconds is not None else None
    scenario = json.loads((generated / "scenario.json").read_text())
    modes = IMPLEMENTED if args.mode == "all" else (args.mode,)
    store_path = generated / "profiles/per_failure/store.json"
    port_map = json.loads((generated / "port_map.json").read_text())
    store = None
    if "offline-per-failure" in modes:
        try:
            store = validate_store(store_path, scenario, port_map)
        except ProfileStoreError as error:
            raise SystemExit(str(error)) from error
        runtime_path = generated / "profiles/per_failure/runtime_store.json"
        if not runtime_path.exists():
            raise SystemExit(f"missing runtime ProfileStore: {runtime_path}; run precompute_profiles.py first")
    metrics = store_metrics(store_path, generated / "profiles/profile0.json") if store else None
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    summaries = []
    for mode in modes:
        run_dir = ROOT / "results/scenarios" / model.scenario_name / mode / args.fault / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        copied_recovery = run_dir / "recovery_profile.json"
        if copied_recovery.exists(): copied_recovery.unlink()
        recovery_generated = generated / "profiles" / f"{mode}_{args.fault}.json"
        if recovery_generated.exists(): recovery_generated.unlink()
        label = {"no-recovery": "NoRecovery", "online": "Online", "offline-per-failure": "Offline"}[mode]
        config = label + f"_{args.fault}"
        overrides = ([f"--*.scenarioRecoveryController.offlineLookupDelay={args.offline_lookup_delay_us}us"]
                     if mode == "offline-per-failure" else None)
        run_omnet(generated, config, run_dir / "raw", run_dir / "run.log", overrides)
        for source_path, name in ((generated/"scenario.json","scenario.json"),(generated/"port_map.json","port_map.json"),(generated/"profiles/profile0.json","profile0.json"),(generated/"fault_analysis/candidate_faults.json","candidate_faults.json")):
            shutil.copy2(source_path, run_dir / name)
        candidate_document = json.loads((run_dir / "candidate_faults.json").read_text())
        legacy_fault_analysis = {"scenario_sha256": scenario["scenario_sha256"],
                                 "faults": {item["fault_id"]: item["affected_flows"]
                                            for item in candidate_document["candidate_faults"]}}
        (run_dir / "fault_analysis.json").write_text(
            json.dumps(legacy_fault_analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if mode == "online":
            candidate_rows = candidate_document["candidate_faults"]
            affected_now = next(item["affected_flows"] for item in candidate_rows if item["fault_id"] == args.fault)
            if affected_now:
                if not recovery_generated.exists(): raise RuntimeError("online run did not emit a recovery profile")
                shutil.copy2(recovery_generated, run_dir / "recovery_profile.json")
                if store and store["faults"][args.fault]["status"] == "SAT":
                    online_profile = json.loads(recovery_generated.read_text())
                    expected = store["faults"][args.fault]["semantic_profile_hash"]
                    if semantic_profile_hash(online_profile) != expected:
                        raise RuntimeError(f"online/offline semantic profile mismatch for {args.fault}")
        elif mode == "offline-per-failure" and store["faults"][args.fault]["status"] == "SAT":
            shutil.copy2(store_path.parent / store["faults"][args.fault]["profile_file"], run_dir / "recovery_profile.json")
        if store:
            shutil.copy2(store_path, run_dir / "profile_store.json")
        make_manifest(run_dir, scenario, mode, args.fault, generated, (run_dir / "recovery_profile.json").exists(),
                      store if mode == "offline-per-failure" else None,
                      store_path if mode == "offline-per-failure" else None,
                      args.offline_lookup_delay_us * 1e-6 if mode == "offline-per-failure" else None)
        summaries.append(analyze_run(run_dir, scenario, mode, args.fault, precompute_wall_ms,
                                     store=store, profile_metrics=metrics,
                                     offline_lookup_delay_s=args.offline_lookup_delay_us * 1e-6))
        print(run_dir)
    if args.mode == "all":
        aggregate = ROOT / "results/scenarios" / model.scenario_name / f"all_{args.fault}_{run_id}.json"
        aggregate.write_text(json.dumps({"runs": summaries, "offline-cluster": "NOT_IMPLEMENTED"}, indent=2) + "\n")
        print(aggregate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
