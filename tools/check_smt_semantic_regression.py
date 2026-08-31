#!/usr/bin/env python3
"""Verify that solver instrumentation preserves frozen production P0 profiles."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.omnet_runner import run_omnet
from tools.scenario_compiler import compile_scenario


SCENARIOS = ("diamond_auto", "mesh10_auto", "structured20_auto")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="exp11-semantic-") as temporary:
        scratch = Path(temporary)
        for scenario in SCENARIOS:
            generated = compile_scenario(ROOT / "configs/scenarios" / f"{scenario}.yaml", scratch / "generated")
            result_dir = scratch / "results" / scenario
            run_omnet(generated, "ScenarioPrecompute", result_dir, result_dir / "precompute.log")
            actual = (generated / "profiles/profile0.json").read_bytes()
            expected_path = ROOT / "generated" / scenario / "profiles/profile0.json"
            expected = expected_path.read_bytes()
            if actual != expected:
                raise AssertionError(f"production P0 profile changed: {scenario}")
            print(f"PASS {scenario}: byte-identical production P0 profile")
        print(f"SMT semantic regression PASS scenarios={len(SCENARIOS)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
