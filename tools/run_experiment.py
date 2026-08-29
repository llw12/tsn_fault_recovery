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
from tools.recovery_modes import MODES, require_implemented
from tools.scenario_compiler import compile_scenario
from tools.scenario_model import load_scenario

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
    command = f"cd {shlex.quote('/home/opp_env/tsn_fault_recovery')} && opp_env run inet-4.7.0 -q -c {shlex.quote(' '.join(shlex.quote(item) for item in forwarded))}"
    executable = ["wsl", "-d", "opp_env", "--", "bash", "-lc", command] if os.name == "nt" else ["bash", "-lc", command]
    return subprocess.run(executable).returncode


def run_omnet(generated: Path, config: str, result_dir: Path, log: Path) -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    command = [str(ROOT / "tsn_fault_recovery"), "-u", "Cmdenv", "-n",
               f"{generated.parent}:{ROOT / 'src'}:/home/opp_env/inet-4.7.0/src",
               "-l", "/home/opp_env/inet-4.7.0/src/INET", "-f", "omnetpp.ini", "-c", config,
               f"--result-dir={result_dir}"]
    completed = subprocess.run(command, cwd=generated, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"OMNeT++ config {config} failed; see {log}")


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


def make_manifest(run_dir: Path, scenario: dict, mode: str, fault: str, generated: Path, recovery: bool) -> None:
    manifest = {
        "schema_version": 1, "scenario_name": scenario["scenario_name"], "scenario_sha256": scenario["scenario_sha256"],
        "mode": mode, "fault_id": fault,
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stdout=subprocess.PIPE, check=True).stdout.strip(),
        "omnetpp_version": "6.4.0", "inet_version": "4.7.0", "z3_version": version(["z3", "--version"], "unknown"),
        "simulation_duration_s": scenario["simulation"]["duration_s"], "cycle_time_s": scenario["simulation"]["cycle_time_s"],
        "random_seed": scenario["simulation"]["random_seed"], "deterministic": True,
        "profile0_id": "P0", "recovery_profile_id": f"online_{fault}" if recovery else None,
        "generated_ned_sha256": digest(generated / "ScenarioNetwork.ned"), "generated_ini_sha256": digest(generated / "omnetpp.ini"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "artifacts": {"scenario": "scenario.json", "port_map": "port_map.json", "profile0": "profile0.json",
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
    if args.fault not in model.fault_candidates:
        raise SystemExit(f"fault {args.fault!r} is not a candidate in scenario {model.scenario_name}")
    generated = compile_scenario(source, ROOT / "generated")
    if not args.skip_build:
        subprocess.run(["make", "-j", str(os.cpu_count() or 2)], cwd=ROOT, check=True)
    precompute_dir = generated / "precompute-results"
    run_omnet(generated, "ScenarioPrecompute", precompute_dir, generated / "precompute.log")
    precompute_scalars = list(precompute_dir.glob("*.sca"))
    precompute_seconds = scalar_from_file(precompute_scalars[0], "scenario.precompute.totalWallTimeSeconds") if len(precompute_scalars) == 1 else None
    precompute_wall_ms = precompute_seconds * 1e3 if precompute_seconds is not None else None
    scenario = json.loads((generated / "scenario.json").read_text())
    modes = IMPLEMENTED if args.mode == "all" else (args.mode,)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    summaries = []
    for mode in modes:
        run_dir = ROOT / "results/scenarios" / model.scenario_name / mode / args.fault / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        recovery_generated = generated / "profiles" / f"{mode}_{args.fault}.json"
        if recovery_generated.exists(): recovery_generated.unlink()
        config = ("NoRecovery" if mode == "no-recovery" else "Online") + f"_{args.fault}"
        run_omnet(generated, config, run_dir / "raw", run_dir / "run.log")
        for source_path, name in ((generated/"scenario.json","scenario.json"),(generated/"port_map.json","port_map.json"),(generated/"profiles/profile0.json","profile0.json"),(generated/"fault_analysis.json","fault_analysis.json")):
            shutil.copy2(source_path, run_dir / name)
        if mode == "online":
            if not recovery_generated.exists(): raise RuntimeError("online run did not emit a recovery profile")
            shutil.copy2(recovery_generated, run_dir / "recovery_profile.json")
        make_manifest(run_dir, scenario, mode, args.fault, generated, mode == "online")
        summaries.append(analyze_run(run_dir, scenario, mode, args.fault, precompute_wall_ms))
        print(run_dir)
    if args.mode == "all":
        aggregate = ROOT / "results/scenarios" / model.scenario_name / f"all_{args.fault}_{run_id}.json"
        aggregate.write_text(json.dumps({"runs": summaries, "offline-per-failure": "NOT_IMPLEMENTED", "offline-cluster": "NOT_IMPLEMENTED"}, indent=2) + "\n")
        print(aggregate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
