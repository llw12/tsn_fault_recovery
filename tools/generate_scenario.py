#!/usr/bin/env python3
"""Validate and compile one scenario file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.scenario_compiler import compile_scenario


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("generated"))
    args = parser.parse_args()
    print(compile_scenario(args.scenario, args.output_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
