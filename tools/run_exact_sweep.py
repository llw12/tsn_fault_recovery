#!/usr/bin/env python3
"""Run matched Offline Per-Failure and Offline Exact-Equivalence sweeps."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--skip-per-failure", action="store_true")
    args = parser.parse_args()
    scenario_path = args.scenario if args.scenario.is_absolute() else ROOT / args.scenario
    scenario_name = scenario_path.stem
    store = json.loads((ROOT / "generated" / scenario_name / "profiles/per_failure/store.json").read_text())
    faults = [fault for fault, entry in store["faults"].items() if entry["status"] == "SAT"]
    modes = ["offline-exact-equivalence"] if args.skip_per_failure else ["offline-per-failure", "offline-exact-equivalence"]
    for fault in faults:
        for mode in modes:
            command = [sys.executable, "tools/run_experiment.py", "--scenario", str(scenario_path),
                       "--mode", mode, "--fault", fault, "--inside-environment", "--skip-build",
                       "--run-id", args.run_id]
            subprocess.run(command, cwd=ROOT, check=True)
    subprocess.run([sys.executable, "tools/validate_exact_equivalence.py", "--scenario-name", scenario_name,
                    "--run-id", args.run_id], cwd=ROOT, check=True)
    print(f"scenario={scenario_name} recoverable_faults={len(faults)} modes={','.join(modes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
