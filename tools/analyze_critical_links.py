#!/usr/bin/env python3
"""Generate P0 and discover candidate critical links without recovery leakage."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.critical_link import CriticalLinkAnalyzer, candidate_ids, write_analysis
from tools.omnet_runner import run_omnet
from tools.scenario_compiler import compile_scenario
from tools.scenario_model import load_scenario
import json


def discover(source: Path, *, skip_build: bool = False) -> tuple[Path, dict]:
    model = load_scenario(source)
    generated = compile_scenario(source, ROOT / "generated")
    if not skip_build:
        subprocess.run(["make", "-j", str(os.cpu_count() or 2)], cwd=ROOT, check=True)
    run_omnet(generated, "ScenarioPrecompute", generated / "precompute-results",
              generated / "precompute.log")
    profile0 = json.loads((generated / "profiles/profile0.json").read_text(encoding="utf-8"))
    analysis = CriticalLinkAnalyzer.analyze(model, profile0)
    write_analysis(analysis, generated / "fault_analysis")
    compile_scenario(source, ROOT / "generated", candidate_ids(analysis))
    return generated, analysis


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--inside-environment", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-build", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.inside_environment:
        forwarded = ["python3", "tools/analyze_critical_links.py", "--scenario", args.scenario.as_posix(),
                     "--inside-environment"]
        if args.skip_build:
            forwarded.append("--skip-build")
        command = "cd /home/opp_env/tsn_fault_recovery && opp_env run inet-4.7.0 -q -c " + shlex.quote(
            " ".join(shlex.quote(item) for item in forwarded))
        executable = ["wsl", "-d", "opp_env", "--", "bash", "-lc", command] if os.name == "nt" else ["bash", "-lc", command]
        return subprocess.run(executable).returncode
    source = args.scenario if args.scenario.is_absolute() else ROOT / args.scenario
    generated, analysis = discover(source, skip_build=args.skip_build)
    print(generated / "fault_analysis/candidate_faults.json")
    print(f"candidate_faults={len(analysis['candidate_faults'])} hash={analysis['candidate_set_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
