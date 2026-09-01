"""Accounting, sampling, serialization and projection helpers for exp14."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from typing import Any


def canonical_profile_bytes(profile: dict[str, Any]) -> bytes:
    return (json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def deterministic_gzip(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)


def discover_candidates(scenario: dict[str, Any], routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    switches = {n["id"] for n in scenario["nodes"] if n["type"] == "switch"}
    links = {l["id"]: l for l in scenario["links"]}
    users: dict[str, set[str]] = defaultdict(set)
    for route in routes:
        for link_id in route["link_path"]: users[link_id].add(route["flow_id"])
    rows = []
    for link_id, flow_ids in users.items():
        link = links[link_id]
        if link["endpoint_a"] in switches and link["endpoint_b"] in switches:
            rows.append({"fault_id": link_id, "affected_flow_count": len(flow_ids),
                         "affected_flow_ids": sorted(flow_ids)})
    return sorted(rows, key=lambda x: x["fault_id"])


def stratified_sample(candidates: list[dict[str, Any]], maximum: int = 40, bins: int = 5,
                      per_bin: int = 8) -> tuple[str, list[dict[str, Any]]]:
    if len(candidates) <= 128: return "FULL", list(candidates)
    ordered = sorted(candidates, key=lambda x: (x["affected_flow_count"], x["fault_id"]))
    selected = []
    for bin_index in range(bins):
        lo = len(ordered) * bin_index // bins
        hi = len(ordered) * (bin_index + 1) // bins
        bucket = ordered[lo:hi]
        if len(bucket) <= per_bin: chosen = bucket
        else:
            indices = sorted({round(i * (len(bucket) - 1) / (per_bin - 1)) for i in range(per_bin)})
            chosen = [bucket[i] for i in indices]
        selected.extend(chosen)
    unique = {row["fault_id"]: row for row in selected}
    return "SAMPLED", [unique[key] for key in sorted(unique)][:maximum]


def quantile_bin_map(candidates: list[dict[str, Any]], bins: int = 5) -> dict[str, int]:
    ordered = sorted(candidates, key=lambda x: (x["affected_flow_count"], x["fault_id"]))
    return {row["fault_id"]: min(bins - 1, index * bins // max(len(ordered), 1))
            for index, row in enumerate(ordered)}


def lpt_projection(durations_ms: list[float], workers: int) -> float:
    loads = [0.0] * workers
    for duration in sorted(durations_ms, reverse=True):
        index = min(range(workers), key=lambda i: (loads[i], i))
        loads[index] += duration
    return max(loads, default=0.0)


def sha256_bytes(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
