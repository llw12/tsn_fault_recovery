#!/usr/bin/env python3
"""Compare deterministic simulation metrics from two experiment runs."""

import argparse
import csv
from pathlib import Path


def keyed(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return {row["scenario"]: row for row in csv.DictReader(handle)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timing-output", type=Path)
    args = parser.parse_args()
    first, second = keyed(args.first), keyed(args.second)
    fields = ["tt_eligible_sent", "tt_received", "tt_lost", "be_received_raw", "first_success_after_fault_s", "recovery_duration_us", "switch_to_first_success_us"]
    rows = []
    for scenario in sorted(first):
        for field in fields:
            left, right = first[scenario][field], second[scenario][field]
            rows.append({"scenario": scenario, "metric": field, "run1": left, "run2": right, "identical": str(left == right).lower()})
    if not all(row["identical"] == "true" for row in rows):
        raise RuntimeError("Deterministic simulation metrics differ between runs")
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    if args.timing_output:
        timing_rows = []
        for label, summary_path in (("run1", args.first), ("run2", args.second)):
            timing_path = summary_path.with_name("timing.csv")
            with timing_path.open(encoding="utf-8") as handle:
                timing = next(csv.DictReader(handle))
            timing_rows.append({
                "run": label,
                "solver_wall_time_s": timing["solver_wall_time_s"],
                "activation_wall_time_s": timing["activation_wall_time_s"],
                "simulated_solver_delay_s": timing["simulated_solver_delay_s"],
            })
        with args.timing_output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=timing_rows[0].keys())
            writer.writeheader()
            writer.writerows(timing_rows)


if __name__ == "__main__":
    main()
