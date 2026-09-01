"""Solver-independent semantic checks for generated JRS-WA solutions."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tools.jrs_wa_adapter import seconds_to_ns


def check_solution(scenario: dict[str, Any], disabled_links: tuple[str, ...],
                   healthy_routes: dict[str, dict[str, Any]], affected_flow_ids: tuple[str, ...],
                   result: dict[str, Any], route_scope: str) -> dict[str, Any]:
    failures: list[str] = []
    details: list[dict[str, Any]] = []

    def record(check: str, passed: bool, detail: str) -> None:
        details.append({"check": check, "passed": passed, "detail": detail})
        if not passed: failures.append(f"{check}: {detail}")

    flows = {f["id"]: f for f in scenario["tt_flows"]}
    links = {l["id"]: l for l in scenario["links"]}
    routes = {r["flow_id"]: r for r in result.get("logical_routes", [])}
    schedule_rows = result.get("statistics", {}).get("route_schedule", [])
    schedules: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in schedule_rows: schedules[row["flow_id"]].append(row)
    record("ALL_TT_ROUTED", set(routes) == set(flows), f"routes={len(routes)} flows={len(flows)}")
    record("ALL_TT_SCHEDULED", set(schedules) == set(flows), f"scheduled={len(schedules)} flows={len(flows)}")
    disabled = set(disabled_links)
    affected = set(affected_flow_ids)
    intervals: dict[tuple[str, str], list[tuple[int, int, str]]] = defaultdict(list)
    switch_ids = {n["id"] for n in scenario["nodes"] if n["type"] == "switch"}
    expected_windows = 0
    for fid, flow in sorted(flows.items()):
        route = routes.get(fid, {})
        node_path, link_path = route.get("node_path", []), route.get("link_path", [])
        coherent = len(node_path) == len(link_path) + 1 and bool(node_path)
        coherent = coherent and node_path[0] == flow["source"] and node_path[-1] == flow["destination"]
        coherent = coherent and len(node_path) == len(set(node_path))
        for index, link_id in enumerate(link_path):
            link = links.get(link_id)
            coherent = coherent and link is not None and {node_path[index], node_path[index + 1]} == {link["endpoint_a"], link["endpoint_b"]}
        record("ROUTE_CONNECTIVITY_CYCLE", bool(coherent), fid)
        record("DISABLED_LINK_EXCLUSION", not disabled.intersection(link_path), fid)
        if route_scope == "affected-only" and fid not in affected:
            locked = healthy_routes.get(fid)
            record("UNAFFECTED_ROUTE_LOCK", locked is not None and
                   node_path == locked.get("node_path") and link_path == locked.get("link_path"), fid)
        rows = sorted(schedules.get(fid, []), key=lambda x: x["start_ns"])
        release = seconds_to_ns(flow["release_offset_s"], f"{fid}.release")
        deadline = seconds_to_ns(flow["schedule_deadline_budget_s"], f"{fid}.deadline")
        record("FIXED_RELEASE_OFFSET", bool(rows) and rows[0]["start_ns"] == release, fid)
        precedence = len(rows) == len(link_path)
        for left, right in zip(rows, rows[1:]):
            link = links[left["logical_link"]]
            propagation = seconds_to_ns(link["propagation_delay_s"], f"{link['id']}.propagation")
            precedence = precedence and right["start_ns"] >= left["end_ns"] + propagation
        record("HOP_PRECEDENCE", precedence, fid)
        record("END_TO_END_DEADLINE", bool(rows) and rows[-1]["end_ns"] <= release + deadline, fid)
        for row in rows:
            intervals[(row["logical_link"], row["source"])].append((row["start_ns"], row["end_ns"], fid))
            if row["source"] in switch_ids: expected_windows += 1
    overlap_ok = True
    for key, values in intervals.items():
        ordered = sorted(values)
        for left, right in zip(ordered, ordered[1:]):
            if right[0] < left[1]:
                overlap_ok = False
                failures.append(f"LINK_NON_OVERLAP: {key} {left} vs {right}")
    record("LINK_NON_OVERLAP", overlap_ok, f"directed_egresses={len(intervals)}")
    windows = result.get("schedule_windows", [])
    record("ALL_SWITCH_HOPS_HAVE_WINDOWS", len(windows) == expected_windows,
           f"windows={len(windows)} expected={expected_windows}")
    profile = result.get("profile") or {}
    profile_routes = {r["flow_id"] for r in profile.get("logical_routes", [])}
    record("PROFILE_ALL_TT", profile_routes == set(flows), f"profile_routes={len(profile_routes)}")
    cycle = seconds_to_ns(scenario["simulation"]["cycle_time_s"], "cycle")
    gates_ok = True
    for gate in profile.get("gate_schedules", []):
        durations_ns = round(sum(gate["durations_s"]) * 1_000_000_000)
        gates_ok = gates_ok and durations_ns == cycle and all(v > 0 for v in gate["durations_s"])
    window_paths = {w["egress_path"] for w in windows}
    gate_paths = {g["gate_path"].split(".macLayer.queue")[0] for g in profile.get("gate_schedules", [])}
    gates_ok = gates_ok and window_paths == gate_paths
    record("GCL_CYCLE_AND_COVERAGE", gates_ok, f"paths={len(gate_paths)} cycle_ns={cycle}")
    return {"valid": not failures, "failure_count": len(failures),
            "failures": failures, "checks": details}
