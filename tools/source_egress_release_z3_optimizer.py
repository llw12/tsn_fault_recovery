"""Exact Z3 sub-process used by exp18j's source-egress release assignment.

Run this file with the repository's existing ``.venv-jrs`` interpreter.  It
is intentionally separate from the pure workload module so normal project
tools and tests do not gain a hidden solver dependency.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import z3


class OptimalityUnproven(RuntimeError):
    pass


def _remaining_ms(deadline: float) -> int:
    remaining = int((deadline - time.monotonic()) * 1000)
    if remaining <= 0:
        raise OptimalityUnproven("SOURCE_RELEASE_OPTIMALITY_UNPROVEN:GROUP_TIMEOUT")
    return remaining


def solve_group(group: dict[str, Any], hypercycle_ticks: int, timeout_s: int = 60) -> dict[str, Any]:
    """Solve one independent source egress with exact lexicographic stages.

    A normal ``Solver`` is used rather than ``Optimize``.  Each minimum is
    established by satisfiability-preserving integer tightening, so a timeout
    never turns an incumbent model into an incorrectly claimed optimum.
    """
    started = time.monotonic(); deadline = started + timeout_s
    flows = sorted(group["flows"], key=lambda row: str(row["flow_id"]))
    if not flows:
        raise ValueError("empty source group")
    solver = z3.Solver()
    variables = [z3.Int(f"release_{index}") for index in range(len(flows))]
    original = [int(row["original_release_ticks"]) for row in flows]
    upper = [int(row["upper_release_ticks"]) for row in flows]
    periods = [int(row["period_ticks"]) for row in flows]
    durations = [int(row["tx_ticks"]) for row in flows]
    for index, variable in enumerate(variables):
        if not 0 <= original[index] <= upper[index] or periods[index] <= 0 or durations[index] <= 0:
            raise ValueError(f"invalid source group timing: {flows[index]['flow_id']}")
        if hypercycle_ticks % periods[index] or durations[index] >= hypercycle_ticks:
            raise ValueError(f"invalid periodic interval: {flows[index]['flow_id']}")
        solver.add(variable >= 0, variable <= upper[index])
    interval_constraints = 0
    # The three shifted copies encode circular half-open intervals without a
    # branch on whether an interval crosses the hypercycle boundary.
    for left in range(len(flows)):
        for right in range(left + 1, len(flows)):
            for left_instance in range(hypercycle_ticks // periods[left]):
                left_start = variables[left] + left_instance * periods[left]
                for right_instance in range(hypercycle_ticks // periods[right]):
                    right_start = variables[right] + right_instance * periods[right]
                    for cycle_shift in (-1, 0, 1):
                        shifted_right = right_start + cycle_shift * hypercycle_ticks
                        solver.add(z3.Or(left_start + durations[left] <= shifted_right,
                                         shifted_right + durations[right] <= left_start))
                        interval_constraints += 1
    changed = [z3.If(variable != old, 1, 0) for variable, old in zip(variables, original)]
    absolute = [z3.If(variable >= old, variable - old, old - variable)
                for variable, old in zip(variables, original)]
    changed_count = z3.Sum(changed)
    total_shift = z3.Sum(absolute)
    maximum_shift = z3.Int("maximum_shift")
    solver.add(maximum_shift >= 0)
    for value in absolute:
        solver.add(maximum_shift >= value)

    def check(extra: list[Any] = ()) -> z3.CheckSatResult:
        solver.push(); solver.add(*extra); solver.set(timeout=_remaining_ms(deadline))
        result = solver.check(); solver.pop()
        if result == z3.unknown:
            raise OptimalityUnproven(f"SOURCE_RELEASE_OPTIMALITY_UNPROVEN:{solver.reason_unknown()}")
        return result

    initial = check()
    if initial == z3.unsat:
        return {"scale": group["scale"], "source": group["source"], "source_egress": group["source_egress"],
                "solver_status": "UNSAT", "optimal": False, "assignments": {},
                "constraint_count": interval_constraints + 3 * len(flows),
                "solve_ms": round((time.monotonic() - started) * 1000, 3)}

    # Stage 1: minimum changed logical-flow count.
    best_changed = None
    for candidate in range(len(flows) + 1):
        if check([changed_count <= candidate]) == z3.sat:
            best_changed = candidate; break
    if best_changed is None:
        raise OptimalityUnproven("SOURCE_RELEASE_OPTIMALITY_UNPROVEN:CHANGED_TIGHTENING")

    # Stage 2: exact binary minimum of total L1 shift.
    max_total = sum(max(old, high - old) for old, high in zip(original, upper))
    low, high = 0, max_total
    stage_two = [changed_count == best_changed]
    while low < high:
        middle = (low + high) // 2
        if check(stage_two + [total_shift <= middle]) == z3.sat: high = middle
        else: low = middle + 1
    best_total = low

    # Stage 3: exact binary minimum of the largest individual change.
    low, high = 0, max(max(old, high - old) for old, high in zip(original, upper))
    stage_three = stage_two + [total_shift == best_total]
    while low < high:
        middle = (low + high) // 2
        if check(stage_three + [maximum_shift <= middle]) == z3.sat: high = middle
        else: low = middle + 1
    best_maximum = low

    # Stage 4: stable canonical-flow-ID lexicographic minimization of R'.
    fixed = stage_three + [maximum_shift == best_maximum]
    chosen: list[int] = []
    for index, variable in enumerate(variables):
        low, high = 0, upper[index]
        while low < high:
            middle = (low + high) // 2
            if check(fixed + [variable <= middle]) == z3.sat: high = middle
            else: low = middle + 1
        chosen.append(low); fixed.append(variable == low)
    if check(fixed) != z3.sat:
        raise OptimalityUnproven("SOURCE_RELEASE_OPTIMALITY_UNPROVEN:FINAL_MODEL")
    assignments = {str(row["flow_id"]): chosen[index] for index, row in enumerate(flows)}
    return {"scale": group["scale"], "source": group["source"], "source_egress": group["source_egress"],
            "solver_status": "SAT", "optimal": True, "assignments": assignments,
            "changed_flow_count": best_changed, "total_shift_ticks": best_total,
            "max_shift_ticks": best_maximum, "constraint_count": interval_constraints + 3 * len(flows),
            "solve_ms": round((time.monotonic() - started) * 1000, 3)}


def solve_problem(problem: dict[str, Any]) -> dict[str, Any]:
    hypercycle = int(problem["hypercycle_ticks"])
    timeout = int(problem.get("group_timeout_s", 60))
    output: list[dict[str, Any]] = []
    for group in sorted(problem["groups"], key=lambda row: (row["scale"], row["source_egress"], row["source"])):
        try:
            output.append(solve_group(group, hypercycle, timeout))
        except OptimalityUnproven as error:
            output.append({"scale": group["scale"], "source": group["source"], "source_egress": group["source_egress"],
                           "solver_status": "UNKNOWN", "optimal": False, "assignments": {}, "error": str(error),
                           "solve_ms": ""})
    return {"solver": "z3.Solver iterative exact tightening", "z3_version": z3.get_version_string(),
            "hypercycle_ticks": hypercycle, "group_timeout_s": timeout, "groups": output}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", required=True); parser.add_argument("--output", required=True)
    args = parser.parse_args()
    problem = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = solve_problem(problem)
    Path(args.output).write_text(json.dumps(result, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
