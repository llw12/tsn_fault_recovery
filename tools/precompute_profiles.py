#!/usr/bin/env python3
"""Build all declared single-fault joint recovery profiles in a zero-time phase."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_critical_links import discover
from tools.profile_store import build_store
from tools.omnet_runner import run_omnet


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--strategy", default="per-failure", choices=("per-failure",))
    parser.add_argument("--inside-environment", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-build", action="store_true")
    return parser.parse_args()


def enter_environment(args) -> int:
    source = args.scenario.as_posix()
    forwarded = ["python3", "tools/precompute_profiles.py", "--scenario", source,
                 "--strategy", args.strategy, "--inside-environment"]
    if args.skip_build: forwarded.append("--skip-build")
    command = f"cd /home/opp_env/tsn_fault_recovery && opp_env run inet-4.7.0 -q -c {shlex.quote(' '.join(shlex.quote(x) for x in forwarded))}"
    executable = ["wsl", "-d", "opp_env", "--", "bash", "-lc", command] if os.name == "nt" else ["bash", "-lc", command]
    return subprocess.run(executable).returncode


def main() -> int:
    args = parse_args()
    if not args.inside_environment: return enter_environment(args)
    source = args.scenario if args.scenario.is_absolute() else ROOT / args.scenario
    generated, analysis = discover(source, skip_build=args.skip_build)
    root = generated / "profiles/per_failure"
    (root / "raw").mkdir(parents=True, exist_ok=True)
    run_omnet(generated, "ScenarioPerFailurePrecompute", root / "precompute-results", root / "precompute.log")
    store = build_store(generated)
    if analysis["policy"]["mode"] == "auto" and any(
            entry["status"] == "NO_AFFECTED_TT" for entry in store["faults"].values()):
        raise RuntimeError("auto candidate discovery and AffectedFlowAnalyzer disagree: NO_AFFECTED_TT")
    print(root / "store.json")
    print(f"candidate_faults={len(store['faults'])} recovery_profiles={sum(x['status'] == 'SAT' for x in store['faults'].values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
