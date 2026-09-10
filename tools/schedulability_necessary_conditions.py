"""Deterministic monotone necessary conditions for exp21 fault grouping.

The cut family is deliberately finite: surviving-graph bridges plus one
deterministic minimum physical-edge cut for every affected endpoint pair.
Passing these tests is not a schedulability proof; failing any test is a
sound certificate for every superset of the disabled physical links.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable

from tools.h2s_jrs_backend import quantize_flow
from tools.jrs_wa_adapter import canonical_json_bytes

QUANTUM_NS = 100
HYPERPERIOD_NS = 8_000_000
HYPERPERIOD_TICKS = HYPERPERIOD_NS // QUANTUM_NS


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _graph(scenario: dict[str, Any], disabled: Iterable[str] = ()) -> tuple[list[str], list[dict[str, Any]], dict[str, list[tuple[str, str]]]]:
    blocked = set(disabled)
    nodes = sorted(str(row["id"]) for row in scenario["nodes"])
    links = [row for row in sorted(scenario["links"], key=lambda x: str(x["id"])) if str(row["id"]) not in blocked]
    adjacency = {node: [] for node in nodes}
    for row in links:
        a, b, link = str(row["endpoint_a"]), str(row["endpoint_b"]), str(row["id"])
        adjacency[a].append((b, link)); adjacency[b].append((a, link))
    for node in adjacency:
        adjacency[node].sort()
    return nodes, links, adjacency


def _reached(adjacency: dict[str, list[tuple[str, str]]], source: str, allowed: set[str] | None = None,
             removed_link: str | None = None) -> set[str]:
    if allowed is not None and source not in allowed:
        return set()
    seen, pending = {source}, deque([source])
    while pending:
        node = pending.popleft()
        for neighbor, link in adjacency[node]:
            if link == removed_link or (allowed is not None and neighbor not in allowed) or neighbor in seen:
                continue
            seen.add(neighbor); pending.append(neighbor)
    return seen


def shortest_hops(adjacency: dict[str, list[tuple[str, str]]], source: str, destination: str,
                  allowed: set[str] | None = None) -> int | None:
    if source == destination:
        return 0
    if allowed is not None and (source not in allowed or destination not in allowed):
        return None
    distance, pending = {source: 0}, deque([source])
    while pending:
        node = pending.popleft()
        for neighbor, _ in adjacency[node]:
            if allowed is not None and neighbor not in allowed:
                continue
            if neighbor not in distance:
                distance[neighbor] = distance[node] + 1
                if neighbor == destination:
                    return distance[neighbor]
                pending.append(neighbor)
    return None


def quantized_flows(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    overhead = int(scenario.get("scheduling", {}).get("frame_overhead_bytes", 0))
    return {str(flow["id"]): {**quantize_flow(flow, overhead, 1_000_000_000, QUANTUM_NS),
                              "source": str(flow["source"]), "destination": str(flow["destination"])}
            for flow in scenario["tt_flows"]}


def connectivity_condition(scenario: dict[str, Any], disabled: Iterable[str]) -> dict[str, Any]:
    _, _, adjacency = _graph(scenario, disabled)
    for flow in sorted(scenario["tt_flows"], key=lambda x: str(x["id"])):
        source, destination = str(flow["source"]), str(flow["destination"])
        if destination not in _reached(adjacency, source):
            return {"passed": False, "stage": "F1_CONNECTIVITY", "margin": -1,
                    "witness": {"flow_id": str(flow["id"]), "source": source, "destination": destination}}
    return {"passed": True, "stage": "F1_CONNECTIVITY", "margin": 0, "witness": None}


def deadline_condition(scenario: dict[str, Any], disabled: Iterable[str]) -> dict[str, Any]:
    _, _, adjacency = _graph(scenario, disabled); quantized = quantized_flows(scenario)
    minimum: int | None = None; witness = None
    for flow_id in sorted(quantized):
        row = quantized[flow_id]
        hops = shortest_hops(adjacency, row["source"], row["destination"])
        slack = -1 if hops is None else int(row["deadline_ticks"]) - hops * int(row["tx_ticks"])
        if minimum is None or slack < minimum:
            minimum, witness = slack, {"flow_id": flow_id, "minimum_hops": hops,
                                       "tx_ticks": row["tx_ticks"], "deadline_ticks": row["deadline_ticks"], "slack_ticks": slack}
    return {"passed": minimum is not None and minimum >= 0, "stage": "F2_MINIMUM_DELAY",
            "margin": minimum, "witness": witness if minimum is not None and minimum < 0 else None}


@dataclass
class _Edge:
    to: str
    reverse: int
    capacity: int


def _minimum_cut(nodes: list[str], links: list[dict[str, Any]], source: str, sink: str) -> set[str]:
    """Return the deterministic source side of an undirected unit min cut."""
    residual: dict[str, list[_Edge]] = {node: [] for node in nodes}

    def directed(a: str, b: str) -> None:
        forward = _Edge(b, len(residual[b]), 1)
        reverse = _Edge(a, len(residual[a]), 0)
        residual[a].append(forward); residual[b].append(reverse)

    for row in links:
        a, b = str(row["endpoint_a"]), str(row["endpoint_b"])
        directed(a, b); directed(b, a)
    while True:
        parent: dict[str, tuple[str, int]] = {}; pending = deque([source]); seen = {source}
        while pending and sink not in seen:
            node = pending.popleft()
            for index, edge in sorted(enumerate(residual[node]), key=lambda pair: (pair[1].to, pair[0])):
                if edge.capacity > 0 and edge.to not in seen:
                    seen.add(edge.to); parent[edge.to] = (node, index); pending.append(edge.to)
        if sink not in seen:
            return seen
        node = sink
        while node != source:
            previous, index = parent[node]; edge = residual[previous][index]
            edge.capacity -= 1; residual[node][edge.reverse].capacity += 1; node = previous


def _canonical_partition(nodes: Iterable[str], side: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    universe, left = set(nodes), set(side); right = universe - left
    a, b = tuple(sorted(left)), tuple(sorted(right))
    return (a, b) if a <= b else (b, a)


def checked_cut_family(scenario: dict[str, Any], disabled: Iterable[str], affected_flow_ids: Iterable[str]) -> list[dict[str, Any]]:
    nodes, links, adjacency = _graph(scenario, disabled); cuts: dict[tuple[tuple[str, ...], tuple[str, ...]], set[str]] = {}
    for row in links:
        link = str(row["id"]); a = str(row["endpoint_a"])
        side = _reached(adjacency, a, removed_link=link)
        if len(side) != len(nodes):
            partition = _canonical_partition(nodes, side); cuts.setdefault(partition, set()).add("BRIDGE")
    flows = {str(row["id"]): row for row in scenario["tt_flows"]}
    endpoint_pairs = sorted({(str(flows[fid]["source"]), str(flows[fid]["destination"]))
                             for fid in affected_flow_ids if fid in flows and flows[fid]["source"] != flows[fid]["destination"]})
    for source, destination in endpoint_pairs:
        partition = _canonical_partition(nodes, _minimum_cut(nodes, links, source, destination))
        cuts.setdefault(partition, set()).add(f"MINCUT:{source}:{destination}")
    output = []
    for (left, right), reasons in sorted(cuts.items()):
        left_set, crossing = set(left), []
        for row in links:
            if (str(row["endpoint_a"]) in left_set) != (str(row["endpoint_b"]) in left_set):
                crossing.append(str(row["id"]))
        payload = {"side_a": list(left), "side_b": list(right), "crossing_links": sorted(crossing)}
        output.append({"cut_id": canonical_sha(payload), **payload, "reasons": sorted(reasons)})
    return output


def _instances(row: dict[str, Any]) -> int:
    return HYPERPERIOD_TICKS // int(row["period_ticks"])


def cut_capacity_condition(scenario: dict[str, Any], disabled: Iterable[str], affected_flow_ids: Iterable[str]) -> dict[str, Any]:
    quantized = quantized_flows(scenario); cuts = checked_cut_family(scenario, disabled, affected_flow_ids)
    rows, minimum, failure = [], None, None
    for cut in cuts:
        left = set(cut["side_a"]); capacity = len(cut["crossing_links"]) * HYPERPERIOD_TICKS
        for direction, source_side in (("A_TO_B", left), ("B_TO_A", set(cut["side_b"]))):
            demand = sum(_instances(row) * int(row["tx_ticks"]) for row in quantized.values()
                         if row["source"] in source_side and row["destination"] not in source_side)
            slack = capacity - demand
            record = {"cut_id": cut["cut_id"], "direction": direction, "crossing_link_count": len(cut["crossing_links"]),
                      "capacity_ticks": capacity, "demand_ticks": demand, "slack_ticks": slack,
                      "side_a": ";".join(cut["side_a"]), "side_b": ";".join(cut["side_b"]),
                      "reasons": ";".join(cut["reasons"])}
            rows.append(record)
            if minimum is None or slack < minimum:
                minimum = slack
            if slack < 0 and failure is None:
                failure = record
    return {"passed": failure is None, "stage": "F3_CHECKED_CUT_CAPACITY", "margin": minimum,
            "no_bottleneck_observed": minimum is None, "witness": failure, "cuts": cuts, "rows": rows}


class _RangeMax:
    def __init__(self, values: list[int]):
        self.size = 1
        while self.size < len(values): self.size *= 2
        self.value = [-(10 ** 30)] * (2 * self.size); self.index = [10 ** 30] * (2 * self.size); self.lazy = [0] * (2 * self.size)
        for index, value in enumerate(values): self.value[self.size + index], self.index[self.size + index] = value, index
        for node in range(self.size - 1, 0, -1): self._pull(node)

    def _pull(self, node: int) -> None:
        left, right = node * 2, node * 2 + 1
        if (self.value[left], -self.index[left]) >= (self.value[right], -self.index[right]):
            self.value[node], self.index[node] = self.value[left], self.index[left]
        else: self.value[node], self.index[node] = self.value[right], self.index[right]

    def _apply(self, node: int, delta: int) -> None:
        self.value[node] += delta; self.lazy[node] += delta

    def _push(self, node: int) -> None:
        if self.lazy[node]:
            self._apply(node * 2, self.lazy[node]); self._apply(node * 2 + 1, self.lazy[node]); self.lazy[node] = 0

    def add(self, start: int, end: int, delta: int, node: int = 1, left: int = 0, right: int | None = None) -> None:
        right = self.size if right is None else right
        if end <= left or right <= start: return
        if start <= left and right <= end: self._apply(node, delta); return
        self._push(node); middle = (left + right) // 2
        self.add(start, end, delta, node * 2, left, middle); self.add(start, end, delta, node * 2 + 1, middle, right); self._pull(node)

    def query(self, start: int, end: int, node: int = 1, left: int = 0, right: int | None = None) -> tuple[int, int]:
        right = self.size if right is None else right
        if end <= left or right <= start: return -(10 ** 30), 10 ** 30
        if start <= left and right <= end: return self.value[node], self.index[node]
        self._push(node); middle = (left + right) // 2
        a = self.query(start, end, node * 2, left, middle); b = self.query(start, end, node * 2 + 1, middle, right)
        return max((a, b), key=lambda item: (item[0], -item[1]))


def critical_window_sweep(jobs: list[dict[str, int]], machines: int, horizon: int = HYPERPERIOD_TICKS) -> dict[str, Any]:
    """Exact O(n log n) mandatory-work sweep over the exp21 critical windows."""
    if not jobs or machines <= 0:
        return {"passed": machines > 0 or not jobs, "max_overload_ticks": 0, "witness": None, "critical_windows": 0}
    expanded = [dict(job) for job in jobs] + [{**job, "E": job["E"] + horizon, "L": job["L"] + horizon} for job in jobs]
    coordinates = sorted({0, *[max(0, min(horizon - 1, int(job["E"]))) for job in expanded if 0 <= job["E"] < horizon]})
    tree = _RangeMax([machines * value for value in coordinates]); ordered = sorted(expanded, key=lambda row: (row["L"], row["E"], row["packet_id"]))
    activated = 0; best = (-(10 ** 30), None); windows = 0
    from bisect import bisect_left, bisect_right
    for b in sorted({int(row["L"]) for row in ordered if 0 < row["L"] <= 2 * horizon}):
        while activated < len(ordered) and ordered[activated]["L"] <= b:
            job = ordered[activated]; stop = bisect_right(coordinates, job["E"])
            if stop: tree.add(0, stop, int(job["p"]))
            activated += 1
        low, high = max(0, b - horizon), min(horizon, b)
        first, last = bisect_left(coordinates, low), bisect_left(coordinates, high)
        if first >= last: continue
        value, index = tree.query(first, last); overload = value - machines * b; windows += last - first
        if overload > best[0]: best = overload, {"a_ticks": coordinates[index], "b_ticks": b,
                                                   "demand_ticks": value - machines * coordinates[index],
                                                   "capacity_ticks": machines * (b - coordinates[index]),
                                                   "overload_ticks": overload}
    maximum = max(0, best[0])
    return {"passed": best[0] <= 0, "max_overload_ticks": maximum,
            "minimum_window_slack_ticks": -best[0], "witness": best[1] if best[0] > 0 else None,
            "critical_windows": windows}


def critical_window_exhaustive(jobs: list[dict[str, int]], machines: int, horizon: int = HYPERPERIOD_TICKS) -> dict[str, Any]:
    expanded = [dict(job) for job in jobs] + [{**job, "E": job["E"] + horizon, "L": job["L"] + horizon} for job in jobs]
    coordinates = sorted({0, *[max(0, min(horizon - 1, int(job["E"]))) for job in expanded if 0 <= job["E"] < horizon]})
    best = -(10 ** 30)
    for b in sorted({int(row["L"]) for row in expanded if 0 < row["L"] <= 2 * horizon}):
        for a in coordinates:
            if max(0, b - horizon) <= a < min(horizon, b):
                demand = sum(row["p"] for row in expanded if row["E"] >= a and row["L"] <= b)
                best = max(best, demand - machines * (b - a))
    return {"passed": best <= 0, "max_overload_ticks": max(0, best), "minimum_window_slack_ticks": -best}


def _jobs_for_direction(quantized: dict[str, dict[str, Any]], adjacency: dict[str, list[tuple[str, str]]],
                        links: list[dict[str, Any]], side: set[str]) -> tuple[list[dict[str, int]], dict[str, Any] | None]:
    arcs = []
    for row in links:
        a, b = str(row["endpoint_a"]), str(row["endpoint_b"])
        if a in side and b not in side: arcs.append((a, b))
        if b in side and a not in side: arcs.append((b, a))
    jobs = []
    for flow_id, row in sorted(quantized.items()):
        if row["source"] not in side or row["destination"] in side: continue
        alternatives = []
        for u, v in arcs:
            pre = shortest_hops(adjacency, row["source"], u, side)
            post = shortest_hops(adjacency, v, row["destination"])
            if pre is not None and post is not None:
                alternatives.append((pre, post))
        for instance in range(_instances(row)):
            base = int(row["release_ticks"]) + instance * int(row["period_ticks"])
            feasible = [(base + pre * int(row["tx_ticks"]),
                         base + int(row["deadline_ticks"]) - post * int(row["tx_ticks"])) for pre, post in alternatives]
            feasible = [(early, late) for early, late in feasible if early + int(row["tx_ticks"]) <= late]
            if not feasible:
                return jobs, {"flow_id": flow_id, "instance": instance, "reason": "NO_CROSSING_ARC_TIME_WINDOW"}
            jobs.append({"packet_id": f"{flow_id}#{instance}", "E": min(x[0] for x in feasible),
                         "L": max(x[1] for x in feasible), "p": int(row["tx_ticks"])})
    return jobs, None


def time_window_condition(scenario: dict[str, Any], disabled: Iterable[str], cuts: list[dict[str, Any]]) -> dict[str, Any]:
    _, links, adjacency = _graph(scenario, disabled); quantized = quantized_flows(scenario)
    rows, minimum, failure = [], None, None
    for cut in cuts:
        for direction, side in (("A_TO_B", set(cut["side_a"])), ("B_TO_A", set(cut["side_b"]))):
            jobs, direct = _jobs_for_direction(quantized, adjacency, links, side); machines = len(cut["crossing_links"])
            sweep = critical_window_sweep(jobs, machines)
            passed = direct is None and sweep["passed"]
            slack = sweep.get("minimum_window_slack_ticks")
            record = {"cut_id": cut["cut_id"], "direction": direction, "job_count": len(jobs), "machine_count": machines,
                      "passed": passed, "minimum_window_slack_ticks": slack,
                      "max_overload_ticks": sweep.get("max_overload_ticks", 0), "critical_windows": sweep.get("critical_windows", 0)}
            rows.append(record)
            if slack is not None and (minimum is None or slack < minimum): minimum = slack
            if not passed and failure is None:
                failure = {**record, "direct_witness": direct, "window_witness": sweep.get("witness")}
    return {"passed": failure is None, "stage": "F4_TIME_WINDOW", "margin": minimum,
            "no_bottleneck_observed": minimum is None, "witness": failure, "rows": rows}


def evaluate_necessary_conditions(scenario: dict[str, Any], disabled: Iterable[str], affected_flow_ids: Iterable[str]) -> dict[str, Any]:
    faults = tuple(sorted(set(disabled))); timings = {}; stages = []
    for name, function, args in (
        ("F1_CONNECTIVITY", connectivity_condition, (scenario, faults)),
        ("F2_MINIMUM_DELAY", deadline_condition, (scenario, faults)),
        ("F3_CHECKED_CUT_CAPACITY", cut_capacity_condition, (scenario, faults, affected_flow_ids)),
    ):
        start = time.perf_counter_ns(); result = function(*args); timings[name] = (time.perf_counter_ns() - start) / 1e6; stages.append(result)
        if not result["passed"]:
            return _pipeline_result(faults, stages, timings)
    start = time.perf_counter_ns(); window = time_window_condition(scenario, faults, stages[-1]["cuts"])
    timings["F4_TIME_WINDOW"] = (time.perf_counter_ns() - start) / 1e6; stages.append(window)
    return _pipeline_result(faults, stages, timings)


def _pipeline_result(faults: tuple[str, ...], stages: list[dict[str, Any]], timings: dict[str, float]) -> dict[str, Any]:
    failed = next((row for row in stages if not row["passed"]), None)
    certificate = None
    if failed:
        payload = {"fault_set": list(faults), "stage": failed["stage"], "witness": failed.get("witness")}
        certificate = {"certificate_id": canonical_sha(payload), **payload, "monotone_superset_prunable": True}
    by_stage = {row["stage"]: row for row in stages}
    return {"passed": failed is None, "fault_set": list(faults), "first_failure": failed["stage"] if failed else "",
            "failure_certificate": certificate, "stages": by_stage, "timings_ms": timings,
            "deadline_slack_ticks": by_stage.get("F2_MINIMUM_DELAY", {}).get("margin"),
            "cut_slack_ticks": by_stage.get("F3_CHECKED_CUT_CAPACITY", {}).get("margin"),
            "window_slack_ticks": by_stage.get("F4_TIME_WINDOW", {}).get("margin")}


def certificate_prunes(certificate: dict[str, Any], candidate_faults: Iterable[str]) -> bool:
    return bool(certificate.get("monotone_superset_prunable")) and set(certificate["fault_set"]).issubset(set(candidate_faults))
