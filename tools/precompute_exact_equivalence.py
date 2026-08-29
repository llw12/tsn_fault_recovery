#!/usr/bin/env python3
"""Synthesize exact-affected-set robust profiles and build the Class Store."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.analyze_critical_links import discover
from tools.exact_equivalence import build_candidate_groups, build_class_store, synthesis_plan
from tools.omnet_runner import run_omnet
from tools.profile_store import ProfileStoreError, validate_store


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--inside-environment", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-build", action="store_true")
    return parser.parse_args()


def enter_environment(args) -> int:
    forwarded = ["python3", "tools/precompute_exact_equivalence.py", "--scenario",
                 args.scenario.as_posix(), "--inside-environment"]
    if args.skip_build: forwarded.append("--skip-build")
    command = f"cd /home/opp_env/tsn_fault_recovery && opp_env run inet-4.7.0 -q -c {shlex.quote(' '.join(shlex.quote(x) for x in forwarded))}"
    executable = ["wsl", "-d", "opp_env", "--", "bash", "-lc", command] if os.name == "nt" else ["bash", "-lc", command]
    return subprocess.run(executable).returncode


def main() -> int:
    args = parse_args()
    if not args.inside_environment: return enter_environment(args)
    source = args.scenario if args.scenario.is_absolute() else ROOT / args.scenario
    generated, _ = discover(source, skip_build=args.skip_build)
    scenario = json.loads((generated / "scenario.json").read_text())
    port_map = json.loads((generated / "port_map.json").read_text())
    pf_path = generated / "profiles/per_failure/store.json"
    try:
        pf_store = validate_store(pf_path, scenario, port_map)
    except ProfileStoreError as error:
        raise SystemExit(f"{error}; run precompute_profiles.py first") from error
    candidate = json.loads((generated / "fault_analysis/candidate_faults.json").read_text())
    groups = build_candidate_groups(candidate, pf_store)
    root = generated / "profiles/exact_equivalence"
    reports = {}
    for item in synthesis_plan(groups):
        group_id = item["candidate_group_id"]
        raw = root / "raw" / group_id; raw.mkdir(parents=True, exist_ok=True)
        profile_rel = f"profiles/exact_equivalence/raw/{group_id}/profile.raw.json"
        report_rel = f"profiles/exact_equivalence/raw/{group_id}/report.json"
        overrides = [
            f"--*.scenarioRecoveryController.exactClassId=\"{group_id}\"",
            f"--*.scenarioRecoveryController.exactDisabledLinks=\"{' '.join(item['disabled_links'])}\"",
            f"--*.scenarioRecoveryController.exactAffectedFlows=\"{' '.join(item['affected_flows'])}\"",
            f"--*.scenarioRecoveryController.exactProfileOutputPath=\"{profile_rel}\"",
            f"--*.scenarioRecoveryController.exactReportOutputPath=\"{report_rel}\"",
        ]
        run_omnet(generated, "ScenarioExactGroupPrecompute", raw / "results", raw / "precompute.log", overrides)
        reports[group_id] = json.loads((generated / report_rel).read_text())
    store = build_class_store(generated, reports)
    print(root / "store.json")
    print(f"candidate_groups={len(groups)} classes={len(store['classes'])} shared={sum(x['class_type'] == 'MULTI_FAULT_SHARED' for x in store['classes'].values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
