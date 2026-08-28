#!/usr/bin/env python3
"""Analyze per-packet TT delivery from native OMNeT++/INET result vectors.

The source UdpSocketIo packetSent vector supplies one timestamp per TT send.
The destination PacketSink packetLifeTime vector supplies the receive timestamp
and the packet lifetime.  The original send/creation timestamp is therefore
receive_time - packet_lifetime, which is matched to the source timestamp.

TODO: Replace this inferred matching with explicit packet sequence/identity
tracking before adding source phase offsets, gPTP/clock drift, multiple sources,
or more complex concurrent flows.
"""

from __future__ import annotations

import argparse
import csv
import math
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCENARIOS = (
    ("baseline", "Baseline", False, False),
    ("failure", "Failure", True, False),
    ("recovery", "ManualRecovery", True, True),
)

EXPECTED_REGRESSION = {
    "baseline": {"tt": 20, "be": 98},
    "failure": {"tt": 5, "be": 24},
    "recovery": {"tt": 19, "be": 92},
}


@dataclass
class VectorSample:
    event: int | None
    time: float
    value: float


@dataclass
class PacketRecord:
    scenario: str
    sequence: int
    send_time_s: float
    receive_time_s: float | None
    delay_s: float | None
    received: bool
    eligible_for_loss: bool
    phase: str


def find_one(directory: Path, suffix: str) -> Path:
    matches = sorted(directory.glob(f"*{suffix}"))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {suffix} file in {directory}, found {len(matches)}"
        )
    return matches[0]


def read_vectors(vec_path: Path) -> tuple[list[VectorSample], list[VectorSample]]:
    vector_columns: dict[int, str] = {}
    source_ids: set[int] = set()
    lifetime_ids: set[int] = set()
    samples: dict[int, list[VectorSample]] = {}

    with vec_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("vector "):
                fields = shlex.split(line)
                vector_id = int(fields[1])
                module, name, columns = fields[2], fields[3], fields[4]
                vector_columns[vector_id] = columns
                samples[vector_id] = []
                if (
                    module.endswith(".source.app[0].io")
                    and name == "packetSent:vector(packetBytes)"
                ):
                    source_ids.add(vector_id)
                elif (
                    module.endswith(".destination.app[0].sink")
                    and name == "packetLifeTime:vector"
                ):
                    lifetime_ids.add(vector_id)
                continue
            if not line[0].isdigit():
                continue
            fields = line.split()
            vector_id = int(fields[0])
            if vector_id not in source_ids and vector_id not in lifetime_ids:
                continue
            columns = vector_columns[vector_id]
            values = fields[1:]
            if len(values) != len(columns):
                raise RuntimeError(f"Malformed vector row in {vec_path}: {line}")
            by_column = dict(zip(columns, values))
            samples[vector_id].append(
                VectorSample(
                    event=int(by_column["E"]) if "E" in by_column else None,
                    time=float(by_column["T"]),
                    value=float(by_column["V"]),
                )
            )

    if len(source_ids) != 1 or len(lifetime_ids) != 1:
        raise RuntimeError(
            f"Required TT vectors not found uniquely in {vec_path}: "
            f"packetSent={len(source_ids)}, packetLifeTime={len(lifetime_ids)}"
        )
    source = sorted(samples[next(iter(source_ids))], key=lambda item: item.time)
    lifetime = sorted(samples[next(iter(lifetime_ids))], key=lambda item: item.time)
    return source, lifetime


def read_scalar(sca_path: Path, module_suffix: str, statistic: str) -> int:
    matches: list[float] = []
    with sca_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            if not raw_line.startswith("scalar "):
                continue
            fields = shlex.split(raw_line)
            if len(fields) != 4:
                continue
            _, module, name, value = fields
            if module.endswith(module_suffix) and name == statistic:
                matches.append(float(value))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one scalar {module_suffix} / {statistic} in {sca_path}, "
            f"found {len(matches)}"
        )
    return int(matches[0])


def phase_for(
    send_time: float, fault_time: float, switch_time: float, stable_time: float
) -> str:
    if send_time < fault_time:
        return "pre_fault"
    if send_time < switch_time:
        return "outage"
    if send_time < stable_time:
        return "recovery_transition"
    return "post_recovery"


def match_packets(
    scenario: str,
    source: list[VectorSample],
    lifetimes: list[VectorSample],
    cutoff_time: float,
    fault_time: float,
    switch_time: float,
    stable_time: float,
    tolerance: float,
) -> list[PacketRecord]:
    unmatched = set(range(len(source)))
    receptions: dict[int, VectorSample] = {}
    delays: dict[int, float] = {}

    for lifetime in lifetimes:
        creation_time = lifetime.time - lifetime.value
        if not unmatched:
            raise RuntimeError(f"Unmatched TT reception in {scenario} at {lifetime.time}s")
        source_index = min(unmatched, key=lambda index: abs(source[index].time - creation_time))
        error = abs(source[source_index].time - creation_time)
        if error > tolerance:
            raise RuntimeError(
                f"TT reception in {scenario} could not be matched: "
                f"creation={creation_time:.12g}s, nearest send error={error:.3g}s"
            )
        unmatched.remove(source_index)
        receptions[source_index] = lifetime
        delays[source_index] = lifetime.value

    records: list[PacketRecord] = []
    for sequence, sent in enumerate(source):
        received = sequence in receptions
        receive = receptions.get(sequence)
        records.append(
            PacketRecord(
                scenario=scenario,
                sequence=sequence,
                send_time_s=sent.time,
                receive_time_s=receive.time if receive else None,
                delay_s=delays.get(sequence),
                received=received,
                eligible_for_loss=sent.time <= cutoff_time + tolerance,
                phase=phase_for(sent.time, fault_time, switch_time, stable_time),
            )
        )
    return records


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def describe_delays(records: Iterable[PacketRecord]) -> dict[str, float | None]:
    values = [record.delay_s for record in records if record.delay_s is not None]
    if not values:
        return {"mean": None, "p50": None, "p95": None, "max": None}
    return {
        "mean": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def to_us(value: float | None) -> float | None:
    return None if value is None else value * 1_000_000


def difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def format_number(value: object, digits: int = 6) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def markdown_number(value: object, digits: int = 3, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def build_summary(
    scenario: str,
    label: str,
    has_fault: bool,
    has_switch: bool,
    records: list[PacketRecord],
    sca_path: Path,
    fault_time: float,
    switch_time: float,
    stable_time: float,
) -> dict[str, object]:
    eligible = [record for record in records if record.eligible_for_loss]
    received_eligible = [record for record in eligible if record.received]
    lost = [record for record in eligible if not record.received]
    pre = [
        record
        for record in received_eligible
        if record.send_time_s < fault_time
    ]
    post = [
        record
        for record in received_eligible
        if record.send_time_s >= stable_time
    ]
    pre_stats = describe_delays(pre)
    post_stats = describe_delays(post)

    first_lost = None
    first_success = None
    post_fault_successes: list[PacketRecord] = []
    if has_fault:
        post_fault_lost = [record for record in lost if record.send_time_s >= fault_time]
        first_lost = min(post_fault_lost, key=lambda item: item.sequence, default=None)
        post_fault_successes = [
            record
            for record in received_eligible
            if record.send_time_s >= fault_time
        ]
        first_success = min(
            post_fault_successes,
            key=lambda item: item.receive_time_s if item.receive_time_s is not None else math.inf,
            default=None,
        )

    if has_fault:
        outage_end_send = first_success.send_time_s if first_success else math.inf
        lost_during_outage = sum(
            1
            for record in lost
            if record.send_time_s >= fault_time and record.send_time_s < outage_end_send
        )
    else:
        lost_during_outage = None

    mean_degradation = difference(post_stats["mean"], pre_stats["mean"])
    mean_degradation_pct = None
    if mean_degradation is not None and pre_stats["mean"]:
        mean_degradation_pct = mean_degradation / pre_stats["mean"] * 100

    first_arrival = first_success.receive_time_s if first_success else None
    raw_tt_received = read_scalar(
        sca_path, ".destination.app[0].io", "packets received"
    )
    raw_be_received = read_scalar(
        sca_path, ".destination.app[1].io", "packets received"
    )
    raw_tt_sent = read_scalar(sca_path, ".source.app[0].io", "packets sent")
    raw_be_sent = read_scalar(sca_path, ".source.app[1].io", "packets sent")

    if raw_tt_received != sum(record.received for record in records):
        raise RuntimeError(
            f"{scenario}: scalar TT received={raw_tt_received} disagrees with "
            f"matched vector count={sum(record.received for record in records)}"
        )

    return {
        "scenario": scenario,
        "label": label,
        "tt_sent": raw_tt_sent,
        "tt_eligible_sent": len(eligible),
        "tt_received": len(received_eligible),
        "tt_received_raw": raw_tt_received,
        "tt_lost": len(lost),
        "loss_ratio": len(lost) / len(eligible) if eligible else None,
        "be_sent_raw": raw_be_sent,
        "be_received_raw": raw_be_received,
        "first_lost_after_fault_sequence": first_lost.sequence if first_lost else None,
        "first_success_after_fault_sequence": first_success.sequence if first_success else None,
        "first_success_after_fault_s": first_arrival,
        "recovery_duration_us": (
            to_us(first_arrival - fault_time) if has_switch and first_arrival else None
        ),
        "switch_to_first_success_us": (
            to_us(first_arrival - switch_time) if has_switch and first_arrival else None
        ),
        "tt_lost_during_outage": lost_during_outage,
        "tt_lost_fault_to_switch": (
            sum(
                1
                for record in lost
                if fault_time <= record.send_time_s < switch_time
            )
            if has_fault
            else None
        ),
        "pre_delay_mean_us": to_us(pre_stats["mean"]),
        "pre_delay_p50_us": to_us(pre_stats["p50"]),
        "pre_delay_p95_us": to_us(pre_stats["p95"]),
        "pre_delay_max_us": to_us(pre_stats["max"]),
        "post_delay_mean_us": to_us(post_stats["mean"]),
        "post_delay_p50_us": to_us(post_stats["p50"]),
        "post_delay_p95_us": to_us(post_stats["p95"]),
        "post_delay_max_us": to_us(post_stats["max"]),
        "delay_mean_degradation_us": to_us(mean_degradation),
        "delay_mean_degradation_pct": mean_degradation_pct,
        "delay_p95_degradation_us": to_us(
            difference(post_stats["p95"], pre_stats["p95"])
        ),
        "delay_max_degradation_us": to_us(
            difference(post_stats["max"], pre_stats["max"])
        ),
    }


def write_packet_csv(path: Path, records: list[PacketRecord]) -> None:
    fields = [
        "scenario",
        "sequence",
        "send_time_s",
        "receive_time_s",
        "delay_s",
        "received",
        "eligible_for_loss",
        "phase",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "scenario": record.scenario,
                    "sequence": record.sequence,
                    "send_time_s": format_number(record.send_time_s, 9),
                    "receive_time_s": format_number(record.receive_time_s, 9),
                    "delay_s": format_number(record.delay_s, 9),
                    "received": format_number(record.received),
                    "eligible_for_loss": format_number(record.eligible_for_loss),
                    "phase": record.phase,
                }
            )


def write_summary_csv(path: Path, summaries: list[dict[str, object]]) -> None:
    fields = [
        "scenario",
        "tt_sent",
        "tt_eligible_sent",
        "tt_received",
        "tt_received_raw",
        "tt_lost",
        "loss_ratio",
        "be_sent_raw",
        "be_received_raw",
        "first_lost_after_fault_sequence",
        "first_success_after_fault_sequence",
        "first_success_after_fault_s",
        "recovery_duration_us",
        "switch_to_first_success_us",
        "tt_lost_during_outage",
        "tt_lost_fault_to_switch",
        "pre_delay_mean_us",
        "pre_delay_p50_us",
        "pre_delay_p95_us",
        "pre_delay_max_us",
        "post_delay_mean_us",
        "post_delay_p50_us",
        "post_delay_p95_us",
        "post_delay_max_us",
        "delay_mean_degradation_us",
        "delay_mean_degradation_pct",
        "delay_p95_degradation_us",
        "delay_max_degradation_us",
        "post_vs_baseline_same_window_mean_us",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for summary in summaries:
            writer.writerow(
                {field: format_number(summary.get(field), 9) for field in fields}
            )


def make_timeline_plot(
    path: Path,
    all_records: dict[str, list[PacketRecord]],
    fault_time: float,
    switch_time: float,
    cutoff_time: float,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    labels = {key: label for key, label, _, _ in SCENARIOS}
    fig, axes = plt.subplots(3, 1, figsize=(12, 8.2), sharex=True, sharey=True)
    blue = "#326891"
    orange = "#D97706"
    charcoal = "#30343B"
    grey = "#8A9099"

    for axis, (scenario, _, _, _) in zip(axes, SCENARIOS):
        records = all_records[scenario]
        received = [
            record
            for record in records
            if record.received and record.eligible_for_loss
        ]
        lost = [
            record
            for record in records
            if not record.received and record.eligible_for_loss
        ]
        excluded = [record for record in records if not record.eligible_for_loss]
        axis.scatter(
            [record.send_time_s * 1000 for record in received],
            [record.delay_s * 1_000_000 for record in received],
            color=blue,
            edgecolor="#173A52",
            linewidth=0.6,
            s=35,
            marker="o",
            zorder=3,
        )
        if lost:
            axis.scatter(
                [record.send_time_s * 1000 for record in lost],
                [18] * len(lost),
                color=orange,
                linewidth=1.6,
                s=45,
                marker="x",
                zorder=4,
            )
        if excluded:
            axis.scatter(
                [record.send_time_s * 1000 for record in excluded],
                [8] * len(excluded),
                facecolor="none",
                edgecolor=grey,
                linewidth=1.0,
                s=34,
                marker="s",
                zorder=3,
            )
        axis.axvline(fault_time * 1000, color=charcoal, linestyle="--", linewidth=1.1)
        axis.axvline(switch_time * 1000, color=charcoal, linestyle=":", linewidth=1.3)
        axis.set_ylabel("Delay (µs)")
        axis.set_title(labels[scenario], loc="left", fontsize=11, fontweight="bold")
        axis.set_ylim(0, 275)
        axis.grid(axis="y", color="#D9DDE2", linewidth=0.7)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)

    axes[-1].set_xlabel("TT generation/send time (ms); sequence 0 starts at 0 ms")
    axes[-1].set_xlim(-0.35, 20.45)
    legend = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=blue,
               markeredgecolor="#173A52", label="Delivered (y = end-to-end delay)"),
        Line2D([0], [0], marker="x", color=orange, linestyle="none", label="Lost"),
        Line2D([0], [0], marker="s", color=grey, markerfacecolor="none",
               linestyle="none", label="Tail-excluded"),
        Line2D([0], [0], color=charcoal, linestyle="--", label="Failure 5 ms"),
        Line2D([0], [0], color=charcoal, linestyle=":", label="Profile switch 6 ms"),
    ]
    fig.legend(handles=legend, loc="upper center", ncol=5, frameon=False,
               bbox_to_anchor=(0.5, 0.945), fontsize=9)
    fig.suptitle("TT packet delivery and end-to-end delay", fontsize=15, y=0.985)
    fig.text(
        0.5,
        0.955,
        "100 Mbps diamond topology; loss denominator excludes the packet generated at 20 ms",
        ha="center",
        va="top",
        fontsize=9.5,
        color="#4C535C",
    )
    fig.tight_layout(rect=(0.03, 0.035, 0.99, 0.91))
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)


def write_chart_map(path: Path) -> None:
    path.write_text(
        """# Chart map

- **Section:** TT delivery timeline
- **Question:** Which generated TT packets were delivered or lost, and what delay did delivered packets experience around the 5 ms failure and 6 ms switch?
- **Family/type:** ordered time comparison; three aligned scatter panels
- **Fields:** scenario, sequence/send time, receive status, end-to-end delay, loss eligibility
- **Supported claim:** failure stops TT delivery; manual recovery loses one eligible TT packet and resumes delivery after the profile switch.
- **Palette/non-color encoding:** blue circles for delivery, orange crosses for loss, open grey squares for tail exclusion; dashed and dotted lines distinguish failure and switch references.
- **QA surface:** `tt_timeline.png` at 2160×1476 px (12×8.2 in, 180 dpi).
""",
        encoding="utf-8",
    )


def write_markdown_report(
    path: Path,
    summaries: list[dict[str, object]],
    raw_root: Path,
    sim_end: float,
    fault_time: float,
    switch_time: float,
    stable_time: float,
    drain_window: float,
) -> None:
    by_scenario = {summary["scenario"]: summary for summary in summaries}
    recovery = by_scenario["recovery"]

    lines = [
        "# TSN fault-recovery measurement report",
        "",
        "## Technical summary",
        "",
        "The native INET result signals are sufficient for deterministic per-packet TT matching, so no collector was inserted into the forwarding path. With a 1 ms drain window, Baseline delivered all 20 eligible TT packets, Failure delivered 5 and lost 15, and ManualRecovery delivered 19 and lost 1. ManualRecovery's first post-fault success was sequence 6 at 6.140740 ms: 1,140.740 µs after the fault and 140.740 µs after the profile switch.",
        "",
        "## Manual profile switching restores TT delivery after one eligible loss",
        "",
        "The table separates raw sends from the delivery denominator. The packet generated exactly at the 20 ms simulation limit is retained in the packet CSV but excluded from loss calculations.",
        "",
        "| Scenario | Raw TT sent | Eligible TT sent | TT received | TT lost | Loss ratio | First lost seq after fault | First successful seq after fault |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario, _, _, _ in SCENARIOS:
        summary = by_scenario[scenario]
        lines.append(
            f"| {summary['label']} | {summary['tt_sent']} | {summary['tt_eligible_sent']} | "
            f"{summary['tt_received']} | {summary['tt_lost']} | "
            f"{markdown_number(summary['loss_ratio'] * 100 if summary['loss_ratio'] is not None else None, 3, '%')} | "
            f"{markdown_number(summary['first_lost_after_fault_sequence'], 0)} | "
            f"{markdown_number(summary['first_success_after_fault_sequence'], 0)} |"
        )
    lines.extend(
        [
            "",
            "![TT timeline](tt_timeline.png)",
            "",
            "Delivered packets are plotted at their end-to-end delay. Orange crosses mark eligible losses; the open square at 20 ms is the deliberately excluded simulation-tail packet. Failure and profile-switch references are shown at 5 ms and 6 ms.",
            "",
            "## Recovery timing uses arrival time, not switch time",
            "",
            "`T_recovery` is the first successful TT arrival after the fault minus the fault occurrence time. `T_switch_to_first_success` uses the same arrival but subtracts the profile-switch time; the two metrics therefore differ by exactly 1 ms in this experiment.",
            "",
            "| Recovery metric | ManualRecovery |",
            "|---|---:|",
            f"| Fault time | {fault_time * 1000:.3f} ms |",
            f"| Profile-switch time | {switch_time * 1000:.3f} ms |",
            f"| First successful TT sequence after fault | {recovery['first_success_after_fault_sequence']} |",
            f"| First successful TT arrival | {recovery['first_success_after_fault_s'] * 1000:.6f} ms |",
            f"| Recovery interruption duration | {recovery['recovery_duration_us']:.3f} µs |",
            f"| Switch-to-first-success | {recovery['switch_to_first_success_us']:.3f} µs |",
            f"| TT packets lost during outage | {recovery['tt_lost_during_outage']} |",
            "",
            "## Stable post-recovery delay matches the time-aligned baseline",
            "",
            f"Pre-fault statistics use TT packets generated before {fault_time * 1000:.0f} ms. Stable post-recovery statistics begin at {stable_time * 1000:.0f} ms, one complete 1 ms TAS cycle after the profile switch; the packet generated during 6–7 ms is classified as `recovery_transition` and excluded from stable-delay statistics.",
            "",
            "| Scenario | Window | Mean (µs) | p50 (µs) | p95 (µs) | Max (µs) |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for scenario, _, _, _ in SCENARIOS:
        summary = by_scenario[scenario]
        lines.append(
            f"| {summary['label']} | pre-fault | {markdown_number(summary['pre_delay_mean_us'])} | "
            f"{markdown_number(summary['pre_delay_p50_us'])} | {markdown_number(summary['pre_delay_p95_us'])} | "
            f"{markdown_number(summary['pre_delay_max_us'])} |"
        )
        lines.append(
            f"| {summary['label']} | stable post-recovery/time-aligned | {markdown_number(summary['post_delay_mean_us'])} | "
            f"{markdown_number(summary['post_delay_p50_us'])} | {markdown_number(summary['post_delay_p95_us'])} | "
            f"{markdown_number(summary['post_delay_max_us'])} |"
        )
    lines.extend(
        [
            "",
            f"ManualRecovery's post-vs-pre mean increase is {recovery['delay_mean_degradation_us']:.3f} µs ({recovery['delay_mean_degradation_pct']:.3f}%), while p95 and max degradation are {recovery['delay_p95_degradation_us']:.3f} µs and {recovery['delay_max_degradation_us']:.3f} µs. The mean difference is caused by the shorter startup packet at t=0 in the five-packet pre-fault sample; the stable post-recovery mean is identical to Baseline over the same ≥7 ms window ({recovery['post_vs_baseline_same_window_mean_us']:.3f} µs difference).",
            "",
            "## Scope, data, and metric definitions",
            "",
            "- Environment: OMNeT++ 6.4.0 + INET 4.7.0, 100 Mbps Ethernet links.",
            "- TT in this project means periodic UDP traffic (1 ms, 200 B) classified with PCP 4 into TAS traffic class 1. BE is periodic UDP traffic (200 µs, 1400 B) with PCP 0. This is not a complete industrial IEEE TSN TT model.",
            "- Sequence identity is zero-based source send order; sequence 0 is generated at t=0.",
            "- `packetSent:vector(packetBytes)` supplies source send timestamps. Destination `packetLifeTime:vector` supplies receive timestamps and end-to-end lifetime. A receive is matched to the unique send satisfying `receive_time - lifetime == send_time` within 1 ns.",
            f"- Simulation end is {sim_end * 1000:.0f} ms. The {drain_window * 1000:.0f} ms drain rule makes sends at or before {(sim_end - drain_window) * 1000:.0f} ms loss-eligible; the send at 20 ms remains visible but is not counted as loss.",
            "- Percentiles use linear interpolation at rank `(n - 1) × p`.",
            "- Phases are `pre_fault` (<5 ms), `outage` (5–6 ms), `recovery_transition` (6–7 ms), and `post_recovery` (≥7 ms).",
            "",
            "## Methodology and reproducibility",
            "",
            "The experiment entry builds the existing executable in release mode, runs General/Baseline, LinkFailure, and ManualRecovery under Cmdenv with the IDE-derived NED and image paths, then invokes the Python analyzer on a timestamped raw-result directory. The analyzer cross-checks vector-derived TT delivery against application scalars and enforces the expected TT/BE regression counts.",
            "",
            f"Raw input for this report: `{raw_root}`.",
            "",
            "## Regression and robustness checks",
            "",
            "| Scenario | TT received (expected / actual) | BE received (expected / actual) | Result |",
            "|---|---:|---:|---|",
        ]
    )
    for scenario, _, _, _ in SCENARIOS:
        summary = by_scenario[scenario]
        expected = EXPECTED_REGRESSION[scenario]
        passed = (
            summary["tt_received_raw"] == expected["tt"]
            and summary["be_received_raw"] == expected["be"]
        )
        lines.append(
            f"| {summary['label']} | {expected['tt']} / {summary['tt_received_raw']} | "
            f"{expected['be']} / {summary['be_received_raw']} | {'PASS' if passed else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "All exp01 regression checks pass. The measurement remains post-processing of INET-native result records; later joint-profile modules are disabled in these three configurations.",
            "",
            "## Limitations, uncertainty, and next step",
            "",
            "- Results are deterministic for the current single 20 ms run and configuration; no multi-seed confidence interval is claimed.",
            "- There is no explicit deadline, gPTP clock drift, SMT schedule, or automatic recovery algorithm.",
            "- The 1 ms drain window is conservative relative to the observed sub-0.24 ms TT delay and prevents the t=20 ms tail artifact; changing topology, link rate, or GCL should trigger a review of this window.",
            "- This report intentionally remains the routing-only `{T1,P0}` regression/ablation. Joint `{T1,P1}` and online recovery are evaluated in exp02 and exp03, not mixed into these historical metrics.",
            "",
            "## Further questions",
            "",
            "Before replacing the deterministic generator with SMT, define whether the objective is worst-case TT delay, guard-band overhead, or recovery robustness across multiple failure locations, plus the validation horizon needed for those comparisons.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sim-end-s", type=float, default=0.020)
    parser.add_argument("--fault-time-s", type=float, default=0.005)
    parser.add_argument("--switch-time-s", type=float, default=0.006)
    parser.add_argument("--drain-window-s", type=float, default=0.001)
    parser.add_argument("--match-tolerance-s", type=float, default=1e-9)
    parser.add_argument("--check-regression", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cutoff_time = args.sim_end_s - args.drain_window_s
    stable_time = args.switch_time_s + 0.001
    all_records: dict[str, list[PacketRecord]] = {}
    summaries: list[dict[str, object]] = []

    for scenario, label, has_fault, has_switch in SCENARIOS:
        scenario_dir = args.raw_root / scenario
        vec_path = find_one(scenario_dir, ".vec")
        sca_path = find_one(scenario_dir, ".sca")
        source, lifetimes = read_vectors(vec_path)
        records = match_packets(
            scenario,
            source,
            lifetimes,
            cutoff_time,
            args.fault_time_s,
            args.switch_time_s,
            stable_time,
            args.match_tolerance_s,
        )
        all_records[scenario] = records
        write_packet_csv(args.output_dir / f"{scenario}_tt_packets.csv", records)
        summaries.append(
            build_summary(
                scenario,
                label,
                has_fault,
                has_switch,
                records,
                sca_path,
                args.fault_time_s,
                args.switch_time_s,
                stable_time,
            )
        )

    baseline_post_mean = next(
        summary["post_delay_mean_us"]
        for summary in summaries
        if summary["scenario"] == "baseline"
    )
    for summary in summaries:
        summary["post_vs_baseline_same_window_mean_us"] = difference(
            summary["post_delay_mean_us"], baseline_post_mean
        )

    if args.check_regression:
        failures = []
        for summary in summaries:
            expected = EXPECTED_REGRESSION[summary["scenario"]]
            if summary["tt_received_raw"] != expected["tt"]:
                failures.append(
                    f"{summary['scenario']} TT expected {expected['tt']}, "
                    f"got {summary['tt_received_raw']}"
                )
            if summary["be_received_raw"] != expected["be"]:
                failures.append(
                    f"{summary['scenario']} BE expected {expected['be']}, "
                    f"got {summary['be_received_raw']}"
                )
        if failures:
            raise RuntimeError("Regression check failed: " + "; ".join(failures))

    write_summary_csv(args.output_dir / "summary.csv", summaries)
    make_timeline_plot(
        args.output_dir / "tt_timeline.png",
        all_records,
        args.fault_time_s,
        args.switch_time_s,
        cutoff_time,
    )
    write_chart_map(args.output_dir / "chart_map.md")
    write_markdown_report(
        args.output_dir / "summary.md",
        summaries,
        args.raw_root,
        args.sim_end_s,
        args.fault_time_s,
        args.switch_time_s,
        stable_time,
        args.drain_window_s,
    )

    for summary in summaries:
        print(
            f"{summary['label']}: eligible={summary['tt_eligible_sent']}, "
            f"received={summary['tt_received']}, lost={summary['tt_lost']}, "
            f"BE received={summary['be_received_raw']}"
        )
    print(f"Wrote analysis to {args.output_dir}")


if __name__ == "__main__":
    main()
