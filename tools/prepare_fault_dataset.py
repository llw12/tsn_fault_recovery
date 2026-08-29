#!/usr/bin/env python3
"""Create all post-precompute fault similarity datasets for one scenario."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.fault_dataset import build_fault_dataset
from tools.scenario_model import load_scenario


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, type=Path)
    args = parser.parse_args()
    source = args.scenario if args.scenario.is_absolute() else ROOT / args.scenario
    model = load_scenario(source)
    output = ROOT / "generated" / model.scenario_name / "fault_analysis"
    result = build_fault_dataset(model, output.parent)
    print(output)
    print(f"faults={len(result['dataset'])} pairs={len(result['pairs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
