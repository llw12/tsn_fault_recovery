#!/usr/bin/env python3
"""Analyze exp04 using explicit per-flow packet sequence vectors."""

from __future__ import annotations

import argparse
import csv
import math
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


FLOW_CONFIG = {
    "single": {"TT": (0.000300, 0.010)},
    "multi_sat": {"TT1": (0.000450, 0.010), "TT2": (0.000500, 0.010), "TT3": (0.000550, 0.010)},
    "online_delay_0_1": {"TT": (0.001000, 0.020)},
    "online_delay_1": {"TT": (0.001000, 0.020)},
    "online_delay_5": {"TT": (0.001000, 0.020)},
    "online_delay_10": {"TT": (0.001000, 0.020)},
}

SCENARIOS = (
    "unit",
    "single",
    "multi_sat",
    "multi_unsat",
    "online_delay_0_1",
    "online_delay_1",
    "online_delay_5",
    "online_delay_10",
)

DELAY_SECONDS = {
    "online_delay_0_1": 0.0001,
    "online_delay_1": 0.001,
    "online_delay_5": 0.005,
    "online_delay_10": 0.010,
}


@dataclass
class Sample:
    time: float
    sequence: int


def find_one(directory: Path, suffix: str) -> Path:
    matches = sorted(directory.glob(f"*{suffix}"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {suffix} in {directory}, found {len(matches)}")
    return matches[0]


def read_identity_vectors(path: Path) -> dict[str, dict[str, list[Sample]]]:
    metadata: dict[int, tuple[str, str, str]] = {}
    samples: dict[int, list[Sample]] = {}
    pattern = re.compile(r"^(.+)\.(sent|received)Sequence$")
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith("vector "):
                fields = shlex.split(line)
                match = pattern.match(fields[3])
                if match:
                    vector_id = int(fields[1])
                    metadata[vector_id] = (match.group(1), match.group(2), fields[4])
                    samples[vector_id] = []
                continue
            if not line or not line[0].isdigit():
                continue
            fields = line.split()
            vector_id = int(fields[0])
            if vector_id not in metadata:
                continue
            _, _, columns = metadata[vector_id]
            values = dict(zip(columns, fields[1:]))
            samples[vector_id].append(Sample(float(values["T"]), int(float(values["V"]))))
    result: dict[str, dict[str, list[Sample]]] = {}
    for vector_id, (flow, direction, _) in metadata.items():
        if direction in result.setdefault(flow, {}):
            raise RuntimeError(f"Duplicate explicit identity vector {flow}.{direction} in {path}")
        result[flow][direction] = samples[vector_id]
    return result


def read_scalars(path: Path) -> list[tuple[str, str, float]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            if raw.startswith("scalar "):
                fields = shlex.split(raw)
                if len(fields) == 4:
                    rows.append((fields[1], fields[2], float(fields[3])))
    return rows


def scalar(rows: list[tuple[str, str, float]], suffix: str, name: str, required: bool = True) -> float | None:
    values = [value for module, scalar_name, value in rows if module.endswith(suffix) and scalar_name == name]
    if len(values) == 1:
        return values[0]
    if not required and not values:
        return None
    raise RuntimeError(f"Expected one scalar {suffix}/{name}, found {len(values)}")


def packet_rows(scenario: str, flow_config: dict[str, tuple[float, float]], vectors: dict) -> list[dict]:
    rows = []
    for flow, (deadline, sim_end) in flow_config.items():
        if flow not in vectors or set(vectors[flow]) != {"sent", "received"}:
            raise RuntimeError(f"Missing explicit sent/received vectors for {scenario}/{flow}")
        sends = {sample.sequence: sample.time for sample in vectors[flow]["sent"]}
        receives = {sample.sequence: sample.time for sample in vectors[flow]["received"]}
        if len(sends) != len(vectors[flow]["sent"]) or len(receives) != len(vectors[flow]["received"]):
            raise RuntimeError(f"Duplicate explicit packet sequence in {scenario}/{flow}")
        unknown = set(receives) - set(sends)
        if unknown:
            raise RuntimeError(f"Received unknown sequences in {scenario}/{flow}: {sorted(unknown)}")
        cutoff = sim_end - 0.001
        for sequence, send_time in sorted(sends.items()):
            receive_time = receives.get(sequence)
            delivered = receive_time is not None
            delay = receive_time - send_time if delivered else None
            eligible = send_time <= cutoff + 1e-12
            deadline_met = delivered and delay <= deadline + 1e-12
            phase = "pre_fault" if send_time < 0.005 else "post_fault"
            rows.append({
                "scenario": scenario, "flow_id": flow, "sequence": sequence,
                "send_time_s": send_time, "receive_time_s": receive_time, "delay_s": delay,
                "deadline_s": deadline, "delivered": delivered, "eligible_for_loss": eligible,
                "deadline_met": deadline_met if delivered else None, "phase": phase,
            })
    return rows


def summarize_packets(rows: list[dict]) -> list[dict]:
    summaries = []
    keys = sorted({(row["scenario"], row["flow_id"]) for row in rows})
    for scenario, flow in keys:
        selected = [row for row in rows if row["scenario"] == scenario and row["flow_id"] == flow and row["eligible_for_loss"]]
        delivered = [row for row in selected if row["delivered"]]
        misses = [row for row in delivered if not row["deadline_met"]]
        summaries.append({
            "scenario": scenario, "flow_id": flow, "period_s": 0.001,
            "deadline_s": selected[0]["deadline_s"],
            "packet_size_B": {"TT": 200, "TT1": 200, "TT2": 300, "TT3": 400}[flow],
            "route": "s1->s2->s4->destination" if scenario in {"single", "multi_sat"} else "s1->s3->s4->destination",
            "eligible_sent": len(selected), "received": len(delivered), "lost": len(selected) - len(delivered),
            "deadline_miss_count": len(misses),
            "deadline_miss_ratio": len(misses) / len(delivered) if delivered else None,
            "deadline_success_count": sum(row["deadline_met"] for row in delivered),
            "mean_delay_us": sum(row["delay_s"] for row in delivered) / len(delivered) * 1e6 if delivered else None,
            "max_delay_us": max((row["delay_s"] for row in delivered), default=None) * 1e6 if delivered else None,
        })
    return summaries


WINDOW_RE = re.compile(r"SMT_WINDOW flow=(\S+) egress=(\S+) class=(\d+) startTick=(\d+) endTick=(\d+)")
GCL_RE = re.compile(
    r"PROFILE_GCL module=(\S+) trafficClass=(\d+) .*?newInitiallyOpen=(\d+) "
    r"newOffset=(\S+) newDurations=(\S+) readback=OK"
)


def parse_log(path: Path, scenario: str) -> tuple[list[dict], list[dict], str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    windows = [{
        "scenario": scenario, "row_type": "logical_window", "flow_id": match.group(1),
        "egress": match.group(2), "traffic_class": int(match.group(3)),
        "start_tick": int(match.group(4)), "end_tick": int(match.group(5)),
        "gate_module": "", "initially_open": "", "offset": "", "durations": "",
    } for match in WINDOW_RE.finditer(text)]
    gates = [{
        "scenario": scenario, "row_type": "compiled_gate", "flow_id": "", "egress": "",
        "traffic_class": int(match.group(2)), "start_tick": "", "end_tick": "",
        "gate_module": match.group(1), "initially_open": match.group(3),
        "offset": match.group(4), "durations": match.group(5),
    } for match in GCL_RE.finditer(text)]
    diagnostic_match = re.search(r"(?:SMT_VALIDATION|ONLINE_SOLVER).*?(?:diagnostic=([^\r\n]+))", text)
    diagnostic = diagnostic_match.group(1).strip() if diagnostic_match else ""
    return windows, gates, diagnostic


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if not rows:
        raise RuntimeError(f"Refusing to write empty CSV {path}")
    fields = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: format_value(row.get(field)) for field in fields})


def format_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.12g}"
    return value


def make_gcl_plot(path: Path, windows: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    selected = [row for row in windows if row["scenario"] == "multi_sat"]
    egresses = sorted({row["egress"] for row in selected})
    colors = {"TT1": "#326891", "TT2": "#D99B2B", "TT3": "#D66B3D"}
    fig, axis = plt.subplots(figsize=(11.5, 4.8))
    for y, egress in enumerate(egresses):
        for row in [item for item in selected if item["egress"] == egress]:
            axis.broken_barh([(row["start_tick"], row["end_tick"] - row["start_tick"])],
                             (y - 0.32, 0.64), facecolors=colors[row["flow_id"]],
                             edgecolors="#30343B", linewidth=0.7)
            axis.text((row["start_tick"] + row["end_tick"]) / 2, y, row["flow_id"],
                      ha="center", va="center", fontsize=8, color="#111827")
    axis.set_yticks(range(len(egresses)), egresses)
    axis.set_xlim(0, 1000)
    axis.set_xlabel("Cycle offset (1 µs ticks)")
    fig.suptitle("SMT logical TT windows by egress", y=0.98, fontsize=15)
    axis.set_title("Three 1 ms flows; intervals are half-open and non-overlapping on each egress",
                   loc="left", fontsize=9, color="#4C535C", pad=10)
    axis.grid(axis="x", color="#D9DDE2", linewidth=0.7)
    axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)


def make_sensitivity_plot(path: Path, rows: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = [row["solver_delay_s"] * 1e3 for row in rows]
    recovery = [row["total_recovery_s"] * 1e3 for row in rows]
    lost = [row["tt_lost"] for row in rows]
    misses = [row["deadline_miss_count"] for row in rows]
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(9.5, 7.5), sharex=True,
                                     gridspec_kw={"height_ratios": [1.15, 1]})
    top.plot(x, recovery, color="#326891", marker="o", linewidth=2)
    for px, py in zip(x, recovery):
        top.annotate(f"{py:.3f}", (px, py), xytext=(0, 7), textcoords="offset points", ha="center", fontsize=8)
    top.set_ylabel("Total recovery (ms)")
    fig.suptitle("Recovery time versus configured solver delay", y=0.99, fontsize=15)
    top.set_title("Simulation-time delay model; not measured controller latency",
                  loc="left", fontsize=9, color="#4C535C", pad=10)
    top.grid(axis="y", color="#D9DDE2", linewidth=0.7)
    width = 0.34
    positions = list(range(len(x)))
    bottom.bar([p - width / 2 for p in positions], lost, width, color="#D66B3D", label="TT lost")
    bottom.bar([p + width / 2 for p in positions], misses, width, facecolor="none",
               edgecolor="#326891", hatch="//", label="Delivered deadline misses")
    bottom.set_xticks(positions, [f"{value:g}" for value in x])
    bottom.set_xlabel("Configured solverDelay (ms)")
    bottom.set_ylabel("Packet count")
    bottom.set_title("Delivery loss and delivered deadline misses")
    bottom.legend(frameon=False, ncol=2)
    bottom.grid(axis="y", color="#D9DDE2", linewidth=0.7)
    for axis in (top, bottom):
        axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)


def report(path: Path, solver_rows: list[dict], flow_rows: list[dict], sensitivity: list[dict], raw_root: Path) -> None:
    sat = next(row for row in solver_rows if row["scenario"] == "multi_sat")
    unsat = next(row for row in solver_rows if row["scenario"] == "multi_unsat")
    multi = [row for row in flow_rows if row["scenario"] == "multi_sat"]
    lines = [
        "# BFS rerouting with SMT-based TAS rescheduling",
        "",
        "## Technical summary",
        "",
        f"Z3 produced a deterministic SAT schedule for three contending TT flows with maximum completion {sat['objective_ticks']:.0f} µs, while the 50 µs deadline case returned UNSAT with the expected lower-bound diagnostic. All delivered packets in the feasible multi-flow case met their configured deadlines. Routing remains BFS-based; Z3 schedules only the fixed route.",
        "",
        "## Three contending flows remain non-overlapping and deadline-feasible",
        "",
        "The plot shows logical transmission windows, not packet traces. Same-egress windows never overlap; different egresses overlap where pipeline forwarding permits it.",
        "",
        "![SMT GCL timeline](gcl_timeline.png)",
        "",
        "| Flow | Packet | Deadline | Route | Received / eligible | Deadline misses | Mean / max delay |",
        "|---|---:|---:|---|---:|---:|---:|",
    ]
    for row in multi:
        lines.append(f"| {row['flow_id']} | {row['packet_size_B']} B | {row['deadline_s'] * 1e6:.0f} µs | {row['route']} | {row['received']} / {row['eligible_sent']} | {row['deadline_miss_count']} | {row['mean_delay_us']:.3f} / {row['max_delay_us']:.3f} µs |")
    lines += [
        "",
        "## The negative case fails for a specific deadline lower bound",
        "",
        f"`SmtMultiFlowUnsat` returned **{unsat['status']}**. Diagnostic: `{unsat['diagnostic']}`. This distinguishes an infeasible model from a simulation or activation failure.",
        "",
        "## Recovery tracks configured delay after short-delay cycle-phase effects",
        "",
        "The 0.1 and 1 ms cases land at different points in the 1 ms traffic/GCL cycle, so 1 ms is slightly faster in first-success time. At 5 and 10 ms, recovery and loss scale with configured delay. The top panel uses simulation time; the lower panel keeps packet loss and deadline misses on their own count scale. Host Z3 runtime is measured separately and is not added to `simTime()`.",
        "",
        "![Recovery versus solver delay](recovery_vs_solver_delay.png)",
        "",
        "| solverDelay | Total recovery | TT lost | Delivered deadline misses |",
        "|---:|---:|---:|---:|",
    ]
    for row in sensitivity:
        lines.append(f"| {row['solver_delay_s'] * 1e3:g} ms | {row['total_recovery_s'] * 1e3:.3f} ms | {row['tt_lost']} | {row['deadline_miss_count']} |")
    lines += [
        "",
        "## Scope, metrics, and method",
        "",
        "- Time is modeled with 1 µs integer ticks and a 1 ms single-period hyperperiod.",
        "- Serialization is `ceil((payload + 64 B) × 8 / 100 Mbit/s / 1 µs)`.",
        "- A configurable 40 µs ingress margin covers Source→S1 readiness in the scheduling abstraction; a 5 µs margin separates successive controlled egress hops.",
        "- The SMT deadline bound ends at completion on the last controlled egress. The separately measured end-to-end deadline includes INET endpoint and final-link latency; the 450/500/550 µs validation deadlines were selected above the calculated 216 µs controlled-egress makespan and then checked against packet traces.",
        "- Packet identity is explicit: each source generates `flowId-sequence` names and the recorder writes sent/received sequence vectors. No lifetime-based inference is used in exp04.",
        "- Loss and deadline miss are separate. A deadline miss is counted only for a delivered packet whose measured delay exceeds its flow deadline.",
        "- Z3 Optimize minimizes maximum completion, then total completion, then every start variable in stable flow/hop order.",
        "",
        "## Robustness and limitations",
        "",
        "Ten solver/compiler self-tests cover SAT, shared-link non-overlap, precedence, deadline, capacity/deadline UNSAT, GCL invariants, complement, and repeatability. The model still assumes one common period, fixed routes, one TT class, conservative margins, a last-controlled-egress deadline boundary, and no explicit industrial guard-band optimization.",
        "",
        "## Recommended next step",
        "",
        "Build Offline Per-Failure profiles by running the same route computation and SMT scheduler before simulation/runtime, then store the resulting ProfileDefinition for the existing activator.",
        "",
        "## Further questions",
        "",
        "Before scaling, calibrate ingress/hop margins against a defined switch processing model and decide whether later candidate-route generation optimizes worst-case deadline slack, gate occupancy, or recovery robustness.",
        "",
        f"Run input was generated under `{raw_root}`. Raw OMNeT++ vectors are intentionally excluded from Git and can be regenerated with the exp04 runner; committed CSV/Markdown/PNG files preserve the analyzed evidence.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    all_packets: list[dict] = []
    all_gcl: list[dict] = []
    logical_windows: list[dict] = []
    solver_rows: list[dict] = []
    scenario_scalars = {}
    diagnostics = {}
    for scenario in SCENARIOS:
        directory = args.raw_root / scenario
        sca = find_one(directory, ".sca")
        scalars = read_scalars(sca)
        scenario_scalars[scenario] = scalars
        windows, gates, diagnostic = parse_log(directory / "run.log", scenario)
        logical_windows.extend(windows)
        all_gcl.extend(windows + gates)
        diagnostics[scenario] = diagnostic
        if scenario in FLOW_CONFIG:
            all_packets.extend(packet_rows(scenario, FLOW_CONFIG[scenario], read_identity_vectors(find_one(directory, ".vec"))))
        if scenario == "unit":
            status, objective, wall = "PASS", "", ""
            if scalar(scalars, ".test", "testsPassed") != 10:
                raise RuntimeError("SMT unit tests did not report ten passes")
        elif scenario.startswith("multi_") or scenario == "single":
            status_value = scalar(scalars, ".smtValidationController", "smt.status")
            status = {1.0: "SAT", 0.0: "UNSAT", -1.0: "UNKNOWN"}[status_value]
            objective = scalar(scalars, ".smtValidationController", "smt.objectiveTicks")
            wall = scalar(scalars, ".smtValidationController", "smt.solverWallTimeSeconds")
        else:
            status_value = scalar(scalars, ".onlineJointRecoveryController", "online.scheduleStatus")
            status = {1.0: "SAT", 0.0: "UNSAT", -1.0: "UNKNOWN"}[status_value]
            objective = scalar(scalars, ".onlineJointRecoveryController", "online.scheduleObjectiveTicks")
            wall = scalar(scalars, ".onlineJointRecoveryController", "online.scheduleSolverWallTimeSeconds")
        solver_rows.append({
            "scenario": scenario, "status": status, "objective_ticks": objective,
            "route_solver_wall_us": (scalar(scalars, ".onlineJointRecoveryController", "online.routeSolverWallTimeSeconds", False) or 0) * 1e6 if scenario.startswith("online_") else None,
            "smt_solver_wall_us": wall * 1e6 if isinstance(wall, float) else None,
            "total_solver_wall_us": (scalar(scalars, ".onlineJointRecoveryController", "online.solverWallTimeSeconds", False) or 0) * 1e6 if scenario.startswith("online_") else wall * 1e6 if isinstance(wall, float) else None,
            "diagnostic": diagnostic,
        })

    flow_rows = summarize_packets(all_packets)
    sensitivity = []
    for scenario, delay in DELAY_SECONDS.items():
        packet_subset = [row for row in all_packets if row["scenario"] == scenario and row["eligible_for_loss"]]
        post_fault_successes = [row for row in packet_subset if row["delivered"] and row["send_time_s"] >= 0.005]
        first_success = min(row["receive_time_s"] for row in post_fault_successes)
        scalars = scenario_scalars[scenario]
        sensitivity.append({
            "scenario": scenario, "solver_delay_s": delay, "activation_time_s": scalar(scalars, ".onlineJointRecoveryController", "online.activationEnd"),
            "first_success_s": first_success, "total_recovery_s": first_success - 0.005,
            "tt_lost": sum(not row["delivered"] for row in packet_subset),
            "deadline_miss_count": sum(row["delivered"] and not row["deadline_met"] for row in packet_subset),
            "route_solver_wall_us": scalar(scalars, ".onlineJointRecoveryController", "online.routeSolverWallTimeSeconds") * 1e6,
            "smt_solver_wall_us": scalar(scalars, ".onlineJointRecoveryController", "online.scheduleSolverWallTimeSeconds") * 1e6,
            "total_solver_wall_us": scalar(scalars, ".onlineJointRecoveryController", "online.solverWallTimeSeconds") * 1e6,
        })

    expected = {"unit": "PASS", "single": "SAT", "multi_sat": "SAT", "multi_unsat": "UNSAT"}
    expected.update({key: "SAT" for key in DELAY_SECONDS})
    for row in solver_rows:
        if row["status"] != expected[row["scenario"]]:
            raise RuntimeError(f"Unexpected solver status for {row['scenario']}: {row['status']}")
    multi_rows = [row for row in flow_rows if row["scenario"] == "multi_sat"]
    if any(row["lost"] or row["deadline_miss_count"] for row in multi_rows):
        raise RuntimeError(f"Feasible multi-flow validation has loss/deadline miss: {multi_rows}")

    write_csv(args.output_dir / "solver_summary.csv", solver_rows)
    write_csv(args.output_dir / "flow_summary.csv", flow_rows)
    write_csv(args.output_dir / "packet_results.csv", all_packets)
    write_csv(args.output_dir / "gcl.csv", all_gcl)
    write_csv(args.output_dir / "solver_delay_sensitivity.csv", sensitivity)
    make_gcl_plot(args.output_dir / "gcl_timeline.png", logical_windows)
    make_sensitivity_plot(args.output_dir / "recovery_vs_solver_delay.png", sensitivity)
    report(args.output_dir / "summary.md", solver_rows, flow_rows, sensitivity, args.raw_root)
    (args.output_dir / "chart_map.md").write_text(
        "# Chart map\n\n"
        "- `gcl_timeline.png`: schedule section; comparison across egresses; broken-bar timeline; flow, egress, start/end tick; proves non-overlap and pipeline precedence; three direct-labeled colors plus outlines.\n"
        "- `recovery_vs_solver_delay.png`: sensitivity section; ordered delay response; line plus separate grouped-count panel; solverDelay, recovery, loss, deadline misses; proves configured delay drives interruption without a dual axis.\n",
        encoding="utf-8",
    )
    print(f"Wrote SMT schedule analysis to {args.output_dir}")


if __name__ == "__main__":
    main()
