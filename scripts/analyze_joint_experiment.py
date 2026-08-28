#!/usr/bin/env python3
"""Analyze Phase A joint-profile and Phase B online-recovery experiments."""

from __future__ import annotations

import argparse
import csv
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_recovery import (  # noqa: E402
    build_summary,
    find_one,
    match_packets,
    read_vectors,
    write_packet_csv,
    write_summary_csv,
)


JOINT_SCENARIOS = (
    ("baseline", "Baseline", False, False),
    ("failure", "Failure", True, False),
    ("recovery", "ManualRecovery {T1,P0}", True, True),
    ("joint", "JointProfileRecovery {T1,P1}", True, True),
)
ONLINE_SCENARIOS = (
    ("baseline", "Baseline", False, False),
    ("failure", "Failure", True, False),
    ("online", "OnlineJointRecovery", True, True),
)


def read_named_scalar(path: Path, module_suffix: str, name: str) -> float:
    matches: list[float] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("scalar "):
                continue
            fields = shlex.split(line)
            if len(fields) == 4 and fields[1].endswith(module_suffix) and fields[2] == name:
                matches.append(float(fields[3]))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one scalar {module_suffix}/{name}, found {len(matches)} in {path}")
    return matches[0]


def make_plot(path: Path, records_by_scenario: dict, specs: tuple, fault: float, activation: float) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(specs), 1, figsize=(11.5, 2.35 * len(specs) + 0.8), sharex=True, sharey=True)
    if len(specs) == 1:
        axes = [axes]
    for axis, (key, label, _, _) in zip(axes, specs):
        records = records_by_scenario[key]
        delivered = [r for r in records if r.received and r.eligible_for_loss]
        lost = [r for r in records if not r.received and r.eligible_for_loss]
        axis.scatter([r.send_time_s * 1e3 for r in delivered], [r.delay_s * 1e6 for r in delivered],
                     s=31, color="#326891", edgecolor="#173A52", linewidth=0.5, label="delivered")
        axis.scatter([r.send_time_s * 1e3 for r in lost], [18] * len(lost),
                     s=42, color="#D97706", marker="x", label="lost")
        axis.axvline(fault * 1e3, color="#30343B", linestyle="--", linewidth=1)
        axis.axvline(activation * 1e3, color="#30343B", linestyle=":", linewidth=1.2)
        axis.set_title(label, loc="left", fontsize=10.5, fontweight="bold")
        axis.set_ylabel("Delay (µs)")
        axis.set_ylim(0, 375)
        axis.grid(axis="y", color="#D9DDE2", linewidth=0.7)
        axis.spines[["top", "right"]].set_visible(False)
    axes[-1].set_xlabel("TT send time (ms)")
    axes[-1].set_xlim(-0.3, 20.3)
    fig.suptitle("TT delivery around failure and profile activation", fontsize=14)
    fig.tight_layout()
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)


GCL_PATTERN = re.compile(
    r"PROFILE_GCL module=(\S+) trafficClass=(\d+) "
    r"oldInitiallyOpen=(\d+) oldOffset=(\S+) oldDurations=(\S+) "
    r"newInitiallyOpen=(\d+) newOffset=(\S+) newDurations=(\S+) readback=OK"
)


def write_gcl_log(raw_log: Path, destination: Path, switch_time: float) -> None:
    rows = []
    text = raw_log.read_text(encoding="utf-8", errors="replace")
    for match in GCL_PATTERN.finditer(text):
        rows.append({
            "switch_time_s": f"{switch_time:.9f}", "module": match.group(1),
            "traffic_class": match.group(2), "old_initially_open": match.group(3),
            "old_offset": match.group(4), "old_durations": match.group(5),
            "new_initially_open": match.group(6), "new_offset": match.group(7),
            "new_durations": match.group(8), "readback": "OK",
        })
    if len(rows) != 6:
        raise RuntimeError(f"Expected six GCL readback records in {raw_log}, found {len(rows)}")
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def write_joint_report(path: Path, summaries: list[dict], raw_root: Path) -> None:
    by_key = {row["scenario"]: row for row in summaries}
    joint = by_key["joint"]
    manual = by_key["recovery"]
    lines = [
        "# Joint profile activation — Phase A",
        "",
        "This experiment validates a runtime data-plane mechanism, not the final thesis algorithm. At 6 ms one activation changes S1 forwarding and six TT/BE PeriodicGate parameter sets for the three backup-path egresses; all gate values pass immediate readback.",
        "",
        "## Exact P1 gate parameters",
        "",
        "| Egress | Class | initiallyOpen | offset | durations | Effective open interval |",
        "|---|---|---:|---:|---|---|",
        "| s1.eth[2] → s3 | TT (1) | true | 0 µs | [100 µs, 900 µs] | 0–100 µs |",
        "| s1.eth[2] → s3 | BE (0) | false | 0 µs | [100 µs, 900 µs] | 100–1000 µs |",
        "| s3.eth[1] → s4 | TT (1) | true | 900 µs | [100 µs, 900 µs] | 100–200 µs |",
        "| s3.eth[1] → s4 | BE (0) | false | 900 µs | [100 µs, 900 µs] | 0–100 and 200–1000 µs |",
        "| s4.eth[2] → destination | TT (1) | true | 800 µs | [100 µs, 900 µs] | 200–300 µs |",
        "| s4.eth[2] → destination | BE (0) | false | 800 µs | [100 µs, 900 µs] | 0–200 and 300–1000 µs |",
        "",
        "INET 4.7.0 re-runs `initializeGating()` on each mutable parameter change. Its offset is the elapsed position in the duration cycle at reinitialization, so offsets 900 µs and 800 µs produce TT openings 100 µs and 200 µs after the 6 ms activation. Intermediate reinitializations cannot interleave with another simulation event; final values are read back before the activation returns.",
        "",
        "## Delivery result",
        "",
        "| Scenario | TT received / eligible | TT lost | BE received | First post-fault success | Recovery (µs) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label, _, _ in JOINT_SCENARIOS:
        row = by_key[key]
        first = row["first_success_after_fault_sequence"]
        recovery = row["recovery_duration_us"]
        first_text = str(first) if first is not None else "N/A"
        recovery_text = f"{recovery:.3f}" if recovery is not None else "N/A"
        lines.append(f"| {label} | {row['tt_received']} / {row['tt_eligible_sent']} | {row['tt_lost']} | {row['be_received_raw']} | {first_text} | {recovery_text} |")
    lines += [
        "", "![TT timeline](tt_timeline.png)", "",
        "The manually configured P1 is a 1 ms pipeline: s1.eth[2] opens TT at 0–100 µs, s3.eth[1] at 100–200 µs, and s4.eth[2] at 200–300 µs; BE is complementary at each egress.",
        "",
        f"Joint recovery first succeeds at {joint['first_success_after_fault_s'] * 1e3:.6f} ms (sequence {joint['first_success_after_fault_sequence']}), {joint['switch_to_first_success_us']:.3f} µs after activation. ManualRecovery remains the routing-only {{T1,P0}} regression/ablation and delivered TT/BE {manual['tt_received_raw']}/{manual['be_received_raw']}.",
        "",
        "Activation has scheduling priority -100. OMNeT++ orders equal-time events by lower numeric scheduling priority before insertion order, so activation precedes the 6 ms source production event (default priority 0).",
        "",
        f"Raw input: `{raw_root}`.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_online_timing(path: Path, sca: Path, summary: dict) -> dict[str, float]:
    suffix = ".onlineJointRecoveryController"
    values = {
        "failure_time_s": read_named_scalar(sca, suffix, "online.failureTime"),
        "solver_start_s": read_named_scalar(sca, suffix, "online.solverStart"),
        "solver_end_s": read_named_scalar(sca, suffix, "online.solverEnd"),
        "solver_wall_time_s": read_named_scalar(sca, suffix, "online.solverWallTimeSeconds"),
        "simulated_solver_delay_s": read_named_scalar(sca, suffix, "online.simulatedSolverDelay"),
        "activation_start_s": read_named_scalar(sca, suffix, "online.activationStart"),
        "activation_end_s": read_named_scalar(sca, suffix, "online.activationEnd"),
        "activation_wall_time_s": read_named_scalar(sca, suffix, "online.activationWallTimeSeconds"),
        "first_success_s": summary["first_success_after_fault_s"],
    }
    values["solve_simulation_time_s"] = values["solver_end_s"] - values["solver_start_s"]
    values["activation_simulation_time_s"] = values["activation_end_s"] - values["activation_start_s"]
    values["switch_to_first_success_s"] = values["first_success_s"] - values["activation_end_s"]
    values["total_recovery_s"] = values["first_success_s"] - values["failure_time_s"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=values.keys())
        writer.writeheader()
        writer.writerow({key: f"{value:.12g}" for key, value in values.items()})
    return values


def write_online_report(path: Path, summary: dict, timing: dict, raw_root: Path) -> None:
    path.write_text(
        "\n".join([
            "# Online joint recovery — Phase B",
            "",
            "After the 5 ms disconnect, the online solver reads the live topology, runs deterministic BFS, obtains `s1 -> s3 -> s4 -> destination`, derives all forwarding entries and route-length-dependent pipeline gates, and returns the same ProfileDefinition consumed by Phase A's activator.",
            "The current single configured TT flow is the affected-flow set. The abstraction keeps fault data, affected-flow/scheduling input, solver output, and activation separate; replacing only the solver backend does not change data-plane activation.",
            "",
            f"TT/BE received: **{summary['tt_received_raw']}/{summary['be_received_raw']}**. First successful post-fault TT is sequence {summary['first_success_after_fault_sequence']} at {summary['first_success_after_fault_s'] * 1e3:.6f} ms; total recovery is {timing['total_recovery_s'] * 1e6:.3f} µs.",
            "",
            "| Timing component | Value | Clock domain |",
            "|---|---:|---|",
            f"| Solver execution | {timing['solver_wall_time_s'] * 1e6:.3f} µs | host wall clock |",
            f"| Configured solverDelay | {timing['simulated_solver_delay_s'] * 1e3:.3f} ms | simulation time |",
            f"| Synchronous activation event | {timing['activation_simulation_time_s'] * 1e6:.3f} µs | simulation time |",
            f"| Activator execution | {timing['activation_wall_time_s'] * 1e6:.3f} µs | host wall clock |",
            f"| Activation to first success | {timing['switch_to_first_success_s'] * 1e6:.3f} µs | simulation time |",
            "",
            "The measured C++ wall-clock duration does not advance OMNeT++ time. `solverDelay=1 ms` is the explicit control-plane latency model; it is configured independently from the observed host runtime.",
            "",
            "![TT timeline](tt_timeline.png)",
            "",
            f"Raw input: `{raw_root}`.",
        ]) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("joint", "online"), required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    specs = JOINT_SCENARIOS if args.mode == "joint" else ONLINE_SCENARIOS
    records_by_scenario = {}
    summaries = []
    for key, label, has_fault, has_switch in specs:
        directory = args.raw_root / key
        source, lifetimes = read_vectors(find_one(directory, ".vec"))
        records = match_packets(key, source, lifetimes, 0.019, 0.005, 0.006, 0.007, 1e-9)
        records_by_scenario[key] = records
        summary = build_summary(key, label, has_fault, has_switch, records, find_one(directory, ".sca"), 0.005, 0.006, 0.007)
        summaries.append(summary)
    baseline_mean = summaries[0]["post_delay_mean_us"]
    for row in summaries:
        row["post_vs_baseline_same_window_mean_us"] = row["post_delay_mean_us"] - baseline_mean if row["post_delay_mean_us"] is not None else None
    write_summary_csv(args.output_dir / "summary.csv", summaries)
    make_plot(args.output_dir / "tt_timeline.png", records_by_scenario, specs, 0.005, 0.006)
    if args.mode == "joint":
        write_packet_csv(args.output_dir / "joint_profile_tt_packets.csv", records_by_scenario["joint"])
        write_gcl_log(args.raw_root / "joint" / "run.log", args.output_dir / "gcl_activation_log.csv", 0.006)
        write_joint_report(args.output_dir / "summary.md", summaries, args.raw_root)
    else:
        online = next(row for row in summaries if row["scenario"] == "online")
        write_packet_csv(args.output_dir / "online_tt_packets.csv", records_by_scenario["online"])
        timing = write_online_timing(args.output_dir / "timing.csv", find_one(args.raw_root / "online", ".sca"), online)
        write_gcl_log(args.raw_root / "online" / "run.log", args.output_dir / "gcl_activation_log.csv", 0.006)
        write_online_report(args.output_dir / "summary.md", online, timing, args.raw_root)
    print(f"Wrote {args.mode} analysis to {args.output_dir}")


if __name__ == "__main__":
    main()
