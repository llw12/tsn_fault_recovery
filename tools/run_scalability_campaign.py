#!/usr/bin/env python3
"""Serial, resumable exp10 campaign using the frozen exp09 algorithms."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_critical_links import discover
from tools.profile_store import validate_store
from tools.scenario_model import load_scenario

POLICIES = ("J100", "J060", "J040", "J020")
SOLVER_TIMEOUT_MS = 30_000


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def percentile(values: list[float], fraction: float) -> float:
    values = sorted(values); position = (len(values) - 1) * fraction
    lo, hi = int(position), min(int(position) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def checkpoint_payload(source: Path) -> dict:
    return {"implementation_commit": git_commit(), "scenario_sha256": digest(source),
            "policy_hash": hashlib.sha256("|".join(POLICIES).encode()).hexdigest(),
            "solver_config_hash": hashlib.sha256(str(SOLVER_TIMEOUT_MS).encode()).hexdigest()}


def load_checkpoint(path: Path, expected: dict, resume: bool) -> dict:
    if path.exists():
        state = json.loads(path.read_text())
        if state["identity"] != expected:
            raise RuntimeError("stale exp10 checkpoint: commit/scenario/policy/solver hash changed")
        if not resume:
            raise RuntimeError("checkpoint exists; use --resume or remove only the exp10 scratch directory")
        return state
    return {"identity": expected, "completed": []}


def save_checkpoint(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def stage(state: dict, path: Path, name: str, action) -> None:
    if name in state["completed"]:
        return
    action(); state["completed"].append(name); save_checkpoint(path, state)


def scalar_map(generated: Path) -> dict[str, float]:
    sca = next((generated / "precompute-results").glob("*.sca"))
    values = {}
    for line in sca.read_text(encoding="utf-8").splitlines():
        if line.startswith("scalar "):
            fields = line.split()
            if len(fields) == 4:
                values[fields[2]] = float(fields[3])
    return values


def scenario_metrics(source: Path) -> dict:
    model = load_scenario(source); generated = ROOT / "generated" / model.scenario_name
    scenario = json.loads((generated / "scenario.json").read_text())
    candidate = json.loads((generated / "fault_analysis/candidate_faults.json").read_text())
    pf = validate_store(generated / "profiles/per_failure/store.json", scenario,
                        json.loads((generated / "port_map.json").read_text()))
    rows = list(csv.DictReader((generated / "profiles/per_failure/precompute_per_fault.csv").open()))
    sat = [row for row in rows if row["status"] == "SAT"]
    def stats(field: str) -> dict:
        values = [float(row[field]) / 1000 for row in sat]
        return {"mean": sum(values)/len(values), "p50": percentile(values,.5), "p95": percentile(values,.95), "max": max(values)}
    scalars = scalar_map(generated)
    policy = {}
    for name in POLICIES:
        store = json.loads((generated / "profiles/approximate_equivalence" / name / "store.json").read_text())
        classes = list(store["classes"].values()); sizes = [len(item["members"]) for item in classes]
        attempts = store["synthesis_attempts"]
        validation = list(csv.DictReader((generated / "profiles/approximate_equivalence" / name / "class_validation.csv").open()))
        profile_bytes = sum(int(item["profile_bytes"]) for item in classes)
        pf_bytes = sum(int(item.get("profile_bytes",0)) for item in pf["faults"].values() if item["status"] == "SAT")
        policy[name] = {
            "profile_count": len(classes), "profile_bytes": profile_bytes,
            "candidate_group_count": len(json.loads((generated / "profiles/approximate_equivalence" / name / "candidate_groups.json").read_text())["groups"]),
            "multi_fault_candidate_group_count": sum(len(g["member_faults"]) > 1 for g in json.loads((generated / "profiles/approximate_equivalence" / name / "candidate_groups.json").read_text())["groups"]),
            "synthesis_attempt_count": len(attempts), "accepted_attempt_count": sum(a["status"] == "SHARED_SAT" for a in attempts),
            "rejected_attempt_count": sum(a["status"] != "SHARED_SAT" for a in attempts),
            "recursive_split_count": len(store["rejected_groups"]), "max_split_depth": max((int(c.get("split_depth",0)) for c in classes), default=0),
            "largest_class": max(sizes, default=0), "shared_fault_coverage": sum(size for size in sizes if size > 1) / len(rows),
            "compression": 1 - len(classes) / len(sat), "storage_compression": 1 - profile_bytes / pf_bytes if pf_bytes else 0,
            "candidate_compression": 1 - len(json.loads((generated / "profiles/approximate_equivalence" / name / "candidate_groups.json").read_text())["groups"]) / len(sat),
            "class_sizes": sizes, "validation_logical_count": len(validation),
            "validation_wall_ms": sum(float(row["validation_wall_us"]) for row in validation) / 1000,
            "no_route": sum(a["status"] == "NO_ROUTE" for a in attempts), "unsat": sum(a["status"] == "UNSAT" for a in attempts),
            "timeout": sum(a["status"] == "TIMEOUT" for a in attempts),
        }
    internal = [link for link in scenario["links"] if all(node.startswith("sw") for node in (link["endpoint_a"], link["endpoint_b"]))]
    return {"scenario": model.scenario_name, "switch_count": sum(n.type == "switch" for n in model.nodes),
            "end_system_count": sum(n.type == "end_system" for n in model.nodes), "physical_link_count": len(model.links),
            "internal_link_count": len(internal), "tt_flow_count": len(model.tt_flows), "be_flow_count": len(model.be_flows),
            "candidate_fault_count": len(rows), "recoverable_fault_count": len(sat),
            "initial_route_wall_ms": scalars.get("scenario.precompute.routeWallTimeSeconds", 0)*1000,
            "initial_z3_wall_ms": scalars.get("scenario.precompute.scheduleWallTimeSeconds", 0)*1000,
            "initial_profile_compile_wall_ms": scalars.get("scenario.precompute.profileCompilationWallTimeSeconds", 0)*1000,
            "initial_profile_total_wall_ms": scalars.get("scenario.precompute.totalWallTimeSeconds", 0)*1000,
            "initial_profile_bytes": (generated / "profiles/profile0.json").stat().st_size,
            "pf_total_precompute_wall_ms": pf["recovery_precompute_wall_ms"],
            "pf_route": stats("route_solver_wall_us"), "pf_z3": stats("smt_solver_wall_us"),
            "pf_profile": stats("profile_compile_wall_us"), "pf_total": stats("total_precompute_wall_us"),
            "policy": policy}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", action="append", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scratch-root", type=Path, default=ROOT / "scratch/exp10")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--min-free-gb", type=float, default=float(os.environ.get("EXP10_MIN_FREE_GB", "2")))
    args = parser.parse_args()
    if shutil.disk_usage(ROOT).free < args.min_free_gb * 1024**3:
        raise SystemExit("EXP10 disk guard: insufficient free filesystem space before campaign")
    results = []
    for relative in args.scenario:
        source = relative if relative.is_absolute() else ROOT / relative
        scenario_name = load_scenario(source).scenario_name
        checkpoint = args.scratch_root / args.run_id / scenario_name / "checkpoint.json"
        state = load_checkpoint(checkpoint, checkpoint_payload(source), args.resume)
        stage(state, checkpoint, "P0_AND_CANDIDATES", lambda: discover(source, skip_build=True))
        stage(state, checkpoint, "PF", lambda: run([sys.executable, "tools/precompute_profiles.py", "--scenario", str(source), "--inside-environment", "--skip-build"]))
        stage(state, checkpoint, "J100_REFERENCE", lambda: run([sys.executable, "tools/precompute_exact_equivalence.py", "--scenario", str(source), "--inside-environment", "--skip-build"]))
        for policy in POLICIES:
            stage(state, checkpoint, policy, lambda policy=policy: run([sys.executable, "tools/run_approximate_campaign.py", "--scenario", str(source), "--run-id", args.run_id, "--policy", policy, "--skip-build"]))
        results.append(scenario_metrics(source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"run_id": args.run_id, "implementation_commit": git_commit(), "policies": POLICIES, "scenarios": results}, indent=2, sort_keys=True) + "\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
