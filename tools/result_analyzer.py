"""Unified packet-, flow-, timing-, and run-level result writer."""

from __future__ import annotations

import csv
import json
import re
import shlex
from pathlib import Path
from statistics import mean


SUMMARY_FIELDS = [
    "scenario", "mode", "fault_id", "recovery_status", "recovery_action", "num_nodes", "num_switches",
    "num_end_systems", "num_links", "num_tt_flows", "num_be_flows",
    "candidate_fault_count", "relevant_fault_count", "no_action_fault_count",
    "recoverable_fault_count", "unrecoverable_fault_count",
    "initial_profile_precompute_wall_ms", "recovery_precompute_wall_ms",
    "initial_profile_count", "recovery_profile_count", "total_profile_count",
    "initial_profile_storage_bytes", "recovery_profile_storage_bytes",
    "profile_store_metadata_bytes", "total_profile_storage_bytes",
    "runtime_lookup_wall_us", "simulated_decision_delay_s",
    "route_solver_wall_us_runtime", "smt_solver_wall_us_runtime",
    "runtime_route_solver_invocations", "runtime_z3_solver_invocations",
    "activation_wall_us", "tt_sent", "tt_received", "tt_lost", "deadline_miss_count",
    "deadline_miss_ratio", "failure_time_s", "decision_ready_time_s", "activation_time_s",
    "first_success_after_fault_s", "decision_delay_s", "activation_to_first_success_s",
    "recovery_duration_s",
]


def _vectors(path: Path) -> dict[str, dict[str, list[tuple[float, int]]]]:
    metadata, samples = {}, {}
    pattern = re.compile(r"^(.+)\.(sent|received)Sequence$")
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("vector "):
            fields = shlex.split(raw)
            match = pattern.match(fields[3])
            if match:
                identifier = int(fields[1]); metadata[identifier] = (match.group(1), match.group(2), fields[4]); samples[identifier] = []
        elif raw and raw[0].isdigit():
            fields = raw.split(); identifier = int(fields[0])
            if identifier in metadata:
                columns = metadata[identifier][2]; values = dict(zip(columns, fields[1:])); samples[identifier].append((float(values["T"]), int(float(values["V"]))))
    result = {}
    for identifier, (flow, direction, _) in metadata.items():
        result.setdefault(flow, {})[direction] = samples[identifier]
    return result


def _scalars(path: Path) -> list[tuple[str, str, float]]:
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("scalar "):
            fields = shlex.split(raw)
            if len(fields) == 4:
                rows.append((fields[1], fields[2], float(fields[3])))
    return rows


def _scalar(rows, name, default=None):
    values = [value for _, scalar_name, value in rows if scalar_name == name]
    return values[0] if len(values) == 1 else default


def _percentile(values: list[float], fraction: float):
    if not values:
        return None
    ordered = sorted(values); position = (len(ordered) - 1) * fraction; lower = int(position); upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n"); writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else (f"{row[key]:.12g}" if isinstance(row.get(key), float) else row.get(key, "")) for key in fields})


def analyze_run(run_dir: Path, scenario: dict, mode: str, fault_id: str,
                precompute_wall_ms: float | None = None, *, store: dict | None = None,
                profile_metrics: dict | None = None,
                offline_lookup_delay_s: float = 0.0) -> dict:
    raw = run_dir / "raw"
    vec_files, sca_files = list(raw.glob("*.vec")), list(raw.glob("*.sca"))
    if len(vec_files) != 1 or len(sca_files) != 1:
        raise RuntimeError(f"Expected one .vec and one .sca under {raw}")
    vectors, scalars = _vectors(vec_files[0]), _scalars(sca_files[0])
    tt = {flow["id"]: flow for flow in scenario["tt_flows"]}; be = {flow["id"]: flow for flow in scenario["be_flows"]}
    profile0 = json.loads((run_dir / "profile0.json").read_text()); recovery_path = run_dir / "recovery_profile.json"
    recovery = json.loads(recovery_path.read_text()) if recovery_path.exists() else None
    before = {route["flow_id"]: "->".join(route["node_path"]) for route in profile0["logical_routes"]}
    after = {route["flow_id"]: "->".join(route["node_path"]) for route in (recovery or profile0)["logical_routes"]}
    fault_analysis = json.loads((run_dir / "fault_analysis.json").read_text())["faults"]
    affected = set(fault_analysis[fault_id])
    failure = scenario["simulation"]["failure_time_s"]
    activation = _scalar(scalars, "scenario.activationTime")
    if activation is None: activation = _scalar(scalars, "scenario.online.activationTime")
    packet_rows, flow_rows = [], []
    for flow_id, flow in {**tt, **be}.items():
        directions = vectors.get(flow_id, {}); sends = {seq: time for time, seq in directions.get("sent", [])}; receives = {seq: time for time, seq in directions.get("received", [])}
        deadline = flow.get("deadline_e2e_s"); interval = flow.get("period_s", flow.get("interval_s")); cutoff = scenario["simulation"]["duration_s"] - interval
        eligible_rows = []
        for sequence, send in sorted(sends.items()):
            receive = receives.get(sequence); delay = receive - send if receive is not None else None
            deadline_met = receive is not None and (deadline is None or delay <= deadline + 1e-12)
            phase = "pre_fault" if send < failure else ("post_recovery" if activation is not None and send >= activation else "outage")
            row = {"scenario": scenario["scenario_name"], "mode": mode, "fault_id": fault_id, "flow_id": flow_id,
                   "sequence": sequence, "send_time_s": send, "receive_time_s": receive, "delay_s": delay,
                   "deadline_e2e_s": deadline, "received": receive is not None, "deadline_met": deadline_met if deadline is not None else None, "phase": phase}
            packet_rows.append(row)
            if send <= cutoff + 1e-12: eligible_rows.append(row)
        delivered = [row for row in eligible_rows if row["received"]]; delays = [row["delay_s"] for row in delivered]
        misses = [row for row in delivered if deadline is not None and not row["deadline_met"]]
        flow_rows.append({"scenario": scenario["scenario_name"], "mode": mode, "fault_id": fault_id, "flow_id": flow_id,
            "source": flow["source"], "destination": flow["destination"], "period_s": interval, "deadline_e2e_s": deadline,
            "route_before": before.get(flow_id, ""), "route_after": after.get(flow_id, ""), "sent": len(eligible_rows),
            "received": len(delivered), "lost": len(eligible_rows)-len(delivered), "deadline_miss_count": len(misses),
            "deadline_miss_ratio": len(misses)/len(delivered) if delivered and deadline is not None else None,
            "mean_delay_us": mean(delays)*1e6 if delays else None, "p50_delay_us": _percentile(delays,.5)*1e6 if delays else None,
            "p95_delay_us": _percentile(delays,.95)*1e6 if delays else None, "max_delay_us": max(delays)*1e6 if delays else None,
            "affected_by_fault": flow_id in affected})
    packet_fields = ["scenario","mode","fault_id","flow_id","sequence","send_time_s","receive_time_s","delay_s","deadline_e2e_s","received","deadline_met","phase"]
    flow_fields = ["scenario","mode","fault_id","flow_id","source","destination","period_s","deadline_e2e_s","route_before","route_after","sent","received","lost","deadline_miss_count","deadline_miss_ratio","mean_delay_us","p50_delay_us","p95_delay_us","max_delay_us","affected_by_fault"]
    _write_csv(run_dir / "packets.csv", packet_rows, packet_fields); _write_csv(run_dir / "flows.csv", flow_rows, flow_fields)
    tt_rows = [row for row in flow_rows if row["flow_id"] in tt]; tt_sent=sum(row["sent"] for row in tt_rows); tt_received=sum(row["received"] for row in tt_rows); misses=sum(row["deadline_miss_count"] for row in tt_rows)
    first_success = min((row["receive_time_s"] for row in packet_rows if row["flow_id"] in affected and row["send_time_s"] >= failure and row["received"]), default=None)
    nodes=scenario["nodes"]
    affected_count = len(affected)
    store_entry = store["faults"][fault_id] if store else None
    if mode == "offline-per-failure":
        recovery_status = "RECOVERED" if store_entry["status"] == "SAT" and activation is not None else ("NO_ACTION" if store_entry["status"] == "NO_AFFECTED_TT" else "UNRECOVERABLE")
        recovery_action = "ACTIVATE_PROFILE" if store_entry["status"] == "SAT" else "NO_ACTION"
        simulated_delay = offline_lookup_delay_s
    elif mode == "online":
        recovery_status = "NO_ACTION" if not affected else ("RECOVERED" if activation is not None else "UNRECOVERABLE")
        recovery_action = "NO_ACTION" if not affected else ("ACTIVATE_PROFILE" if activation is not None else "NO_ACTION")
        simulated_delay = scenario["simulation"]["solver_delay_s"] if affected else 0.0
    else:
        recovery_status = "NO_ACTION" if not affected else "NOT_ATTEMPTED"
        recovery_action = "NO_ACTION"
        simulated_delay = 0.0
    decision_ready = failure + simulated_delay
    base_counts = profile_metrics or {
        "candidate_fault_count": len(scenario["fault_candidates"]),
        "relevant_fault_count": sum(bool(value) for value in fault_analysis.values()),
        "no_action_fault_count": sum(not value for value in fault_analysis.values()),
        "recoverable_fault_count": None, "unrecoverable_fault_count": None,
    }
    initial_bytes = (run_dir/"profile0.json").stat().st_size
    offline_only = mode == "offline-per-failure"
    summary={key:None for key in SUMMARY_FIELDS}; summary.update({
        "scenario":scenario["scenario_name"], "mode":mode, "fault_id":fault_id,
        "recovery_status":recovery_status, "recovery_action":recovery_action, "num_nodes":len(nodes),
        "num_switches":sum(n["type"]=="switch" for n in nodes),
        "num_end_systems":sum(n["type"]=="end_system" for n in nodes), "num_links":len(scenario["links"]),
        "num_tt_flows":len(tt), "num_be_flows":len(be),
        "candidate_fault_count":base_counts["candidate_fault_count"], "relevant_fault_count":base_counts["relevant_fault_count"],
        "no_action_fault_count":base_counts["no_action_fault_count"], "recoverable_fault_count":base_counts["recoverable_fault_count"],
        "unrecoverable_fault_count":base_counts["unrecoverable_fault_count"],
        "initial_profile_precompute_wall_ms":precompute_wall_ms,
        "recovery_precompute_wall_ms":base_counts.get("recovery_precompute_wall_ms") if offline_only else None,
        "initial_profile_count":1, "recovery_profile_count":base_counts.get("recovery_profile_count", 0) if offline_only else 0,
        "total_profile_count":base_counts.get("total_profile_count", 1) if offline_only else 1,
        "initial_profile_storage_bytes":initial_bytes,
        "recovery_profile_storage_bytes":base_counts.get("recovery_profile_storage_bytes", 0) if offline_only else 0,
        "profile_store_metadata_bytes":base_counts.get("profile_store_metadata_bytes", 0) if offline_only else 0,
        "total_profile_storage_bytes":base_counts.get("total_profile_storage_bytes", initial_bytes) if offline_only else initial_bytes,
        "runtime_lookup_wall_us":(_scalar(scalars,"scenario.offline.lookupWallTimeSeconds") or 0)*1e6 if offline_only else None,
        "simulated_decision_delay_s":simulated_delay,
        "route_solver_wall_us_runtime":(_scalar(scalars,"scenario.online.routeWallTimeSeconds") or 0)*1e6 if mode=="online" else 0,
        "smt_solver_wall_us_runtime":(_scalar(scalars,"scenario.online.scheduleWallTimeSeconds") or 0)*1e6 if mode=="online" else 0,
        "runtime_route_solver_invocations":int(_scalar(scalars,"scenario.runtime.routeSolverInvocations",0)),
        "runtime_z3_solver_invocations":int(_scalar(scalars,"scenario.runtime.z3SolverInvocations",0)),
        "activation_wall_us":(_scalar(scalars,"scenario.activationWallTimeSeconds") or 0)*1e6 if activation is not None else None,
        "tt_sent":tt_sent, "tt_received":tt_received, "tt_lost":tt_sent-tt_received,
        "deadline_miss_count":misses, "deadline_miss_ratio":misses/tt_received if tt_received else None,
        "failure_time_s":failure, "decision_ready_time_s":decision_ready, "activation_time_s":activation,
        "first_success_after_fault_s":first_success, "decision_delay_s":simulated_delay,
        "activation_to_first_success_s":first_success-activation if first_success is not None and activation is not None else None,
        "recovery_duration_s":first_success-failure if first_success is not None else None,
    })
    _write_csv(run_dir/"summary.csv",[summary],SUMMARY_FIELDS)
    timing_fields=["scenario","mode","fault_id","failure_time_s","decision_ready_time_s","activation_time_s","route_solver_wall_us_runtime","smt_solver_wall_us_runtime","profile_compilation_wall_us","simulated_decision_delay_s","runtime_lookup_wall_us","activation_wall_us","initial_profile_precompute_wall_ms","recovery_precompute_wall_ms"]
    timing={key:summary.get(key) for key in timing_fields}; timing["activation_time_s"]=activation; timing["profile_compilation_wall_us"]=(_scalar(scalars,"scenario.online.profileCompilationWallTimeSeconds") or 0)*1e6 if mode=="online" else None; _write_csv(run_dir/"timing.csv",[timing],timing_fields)
    lines=[f"# {scenario['scenario_name']} / {mode} / {fault_id}","",f"TT delivery: {tt_received}/{tt_sent}; loss: {tt_sent-tt_received}; delivered deadline misses: {misses}.","",f"Affected TT flows: {', '.join(sorted(affected)) or 'none'}."]
    if first_success is not None: lines += ["",f"First successful TT reception after fault: {first_success:.9g} s; recovery duration: {summary['recovery_duration_s']:.9g} s."]
    (run_dir/"summary.md").write_text("\n".join(lines)+"\n")
    return summary
