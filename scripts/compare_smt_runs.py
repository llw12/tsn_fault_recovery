#!/usr/bin/env python3
"""Compare deterministic exp04 outputs while retaining wall-clock variability."""

import argparse
import csv
from pathlib import Path


DETERMINISTIC_FILES = (
    "flow_summary.csv",
    "packet_results.csv",
    "gcl.csv",
    "solver_delay_sensitivity.csv",
)


def rows_without_wall(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key in list(row):
            if "wall" in key:
                row.pop(key)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wall-output", type=Path, required=True)
    args = parser.parse_args()
    comparisons = []
    for filename in ("solver_summary.csv",) + DETERMINISTIC_FILES:
        identical = rows_without_wall(args.first / filename) == rows_without_wall(args.second / filename)
        comparisons.append({"artifact": filename, "deterministic_fields_identical": str(identical).lower()})
    if not all(row["deterministic_fields_identical"] == "true" for row in comparisons):
        raise RuntimeError(f"exp04 deterministic outputs differ: {comparisons}")
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=comparisons[0], lineterminator="\n")
        writer.writeheader(); writer.writerows(comparisons)

    wall_rows = []
    for run, directory in (("run1", args.first), ("run2", args.second)):
        with (directory / "solver_summary.csv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                wall_rows.append({"run": run, "scenario": row["scenario"],
                                  "route_solver_wall_us": row["route_solver_wall_us"],
                                  "smt_solver_wall_us": row["smt_solver_wall_us"],
                                  "total_solver_wall_us": row["total_solver_wall_us"]})
    with args.wall_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=wall_rows[0], lineterminator="\n")
        writer.writeheader(); writer.writerows(wall_rows)


if __name__ == "__main__":
    main()
