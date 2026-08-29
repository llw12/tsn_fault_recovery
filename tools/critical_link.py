"""Deterministic healthy-route critical-link discovery.

This module deliberately consumes only topology, flow definitions, and P0 routes.
Per-failure recovery results are not part of its interface.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from tools.scenario_model import ScenarioModel


class CriticalLinkError(RuntimeError):
    pass


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_value(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def affected_flow_set_hash(flow_ids: list[str]) -> str:
    return sha256_value(flow_ids)


class CriticalLinkAnalyzer:
    """Build one link-to-flow inverted index and apply the declared policy."""

    @staticmethod
    def inverted_index(profile0: dict) -> dict[str, tuple[str, ...]]:
        index: dict[str, set[str]] = defaultdict(set)
        for route in sorted(profile0.get("logical_routes", []), key=lambda item: item["flow_id"]):
            for link_id in route["link_path"]:
                index[link_id].add(route["flow_id"])
        return {link_id: tuple(sorted(flow_ids)) for link_id, flow_ids in sorted(index.items())}

    @staticmethod
    def analyze(model: ScenarioModel, profile0: dict) -> dict:
        if profile0.get("scenario_sha256") != model.sha256():
            raise CriticalLinkError("P0 scenario hash does not match scenario input")
        routes = profile0.get("logical_routes")
        if not isinstance(routes, list):
            raise CriticalLinkError("P0 logical_routes are missing")
        expected_flows = {flow.id for flow in model.tt_flows}
        actual_flows = {route.get("flow_id") for route in routes}
        if actual_flows != expected_flows:
            raise CriticalLinkError("P0 logical routes do not cover the canonical TT flow set")

        index = CriticalLinkAnalyzer.inverted_index(profile0)
        node_type = {node.id: node.type for node in model.nodes}
        flow_by_id = {flow.id: flow for flow in model.tt_flows}
        explicit = set(model.fault_candidates)
        excluded = set(model.candidate_selection.exclude)
        all_links = []
        candidates = []
        excluded_links = []
        for link in model.links:
            affected = list(index.get(link.id, ()))
            in_scope = node_type[link.endpoint_a] == "switch" and node_type[link.endpoint_b] == "switch"
            used = bool(affected)
            classification = "OUT_OF_SCOPE" if not in_scope else ("UNUSED_BY_TT" if not used else "TT_RELEVANT_CANDIDATE")
            if model.candidate_selection.mode == "auto":
                candidate = in_scope and used and link.id not in excluded
            else:
                candidate = link.id in explicit
            flows = [flow_by_id[item] for item in affected]
            deadlines = [flow.deadline_e2e_s * 1e6 for flow in flows]
            record = {
                "link_id": link.id,
                "endpoint_a": link.endpoint_a,
                "endpoint_a_type": node_type[link.endpoint_a],
                "endpoint_b": link.endpoint_b,
                "endpoint_b_type": node_type[link.endpoint_b],
                "in_protection_scope": in_scope,
                "used_by_tt_primary_route": used,
                "candidate_fault": candidate,
                "affected_flows": affected,
                "affected_flow_count": len(affected),
                "affected_flow_set_sha256": affected_flow_set_hash(affected),
                "affected_load_bps": sum(flow.packet_size_bytes * 8 / flow.period_s for flow in flows),
                "affected_deadline_us": deadlines,
                "affected_release_offset_us": [flow.release_offset_s * 1e6 for flow in flows],
                "affected_packet_size_bytes": [flow.packet_size_bytes for flow in flows],
                "min_affected_deadline_us": min(deadlines) if deadlines else None,
                "mean_affected_deadline_us": sum(deadlines) / len(deadlines) if deadlines else None,
                "max_affected_deadline_us": max(deadlines) if deadlines else None,
                "primary_route_usage_count": len(affected),
                "classification": classification,
            }
            all_links.append(record)
            if candidate:
                candidates.append({
                    "fault_id": link.id,
                    "affected_flows": affected,
                    "affected_flow_count": len(affected),
                    "affected_flow_set_sha256": record["affected_flow_set_sha256"],
                    "affected_load_bps": record["affected_load_bps"],
                })
            else:
                reason = "EXPLICIT_EXCLUDE" if link.id in excluded and classification == "TT_RELEVANT_CANDIDATE" else classification
                excluded_links.append({"fault_id": link.id, "reason": reason})

        if model.candidate_selection.mode == "auto" and any(not row["affected_flows"] for row in candidates):
            raise CriticalLinkError("auto candidate has no affected TT flow")
        policy = {"mode": model.candidate_selection.mode,
                  "exclude": list(model.candidate_selection.exclude)}
        if model.candidate_selection.scope is not None:
            policy["scope"] = model.candidate_selection.scope
        if model.candidate_selection.criterion is not None:
            policy["criterion"] = model.candidate_selection.criterion
        primary_routes = sorted(({
            "flow_id": route["flow_id"], "node_path": route["node_path"], "link_path": route["link_path"]
        } for route in routes), key=lambda route: route["flow_id"])
        primary_routes_sha256 = sha256_value(primary_routes)
        hash_payload = {
            "scenario_sha256": model.sha256(),
            "policy": policy,
            "healthy_primary_routes_sha256": primary_routes_sha256,
            "candidate_faults": candidates,
        }
        return {
            "schema_version": 1,
            "scenario_name": model.scenario_name,
            "scenario_sha256": model.sha256(),
            "policy": policy,
            "healthy_primary_routes_sha256": primary_routes_sha256,
            "candidate_set_sha256": sha256_value(hash_payload),
            "all_links": all_links,
            "candidate_faults": candidates,
            "excluded_links": excluded_links,
        }


CRITICAL_LINK_FIELDS = [
    "scenario", "link_id", "endpoint_a", "endpoint_a_type", "endpoint_b", "endpoint_b_type",
    "in_protection_scope", "used_by_tt_primary_route", "candidate_fault", "affected_flow_count",
    "affected_flow_ids", "affected_tt_payload_rate_bps", "min_affected_deadline_us",
    "mean_affected_deadline_us", "max_affected_deadline_us", "primary_route_usage_count", "classification",
]


def write_analysis(analysis: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "candidate_faults.json").write_bytes(canonical_bytes(analysis))
    with (output_dir / "critical_link_analysis.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CRITICAL_LINK_FIELDS, lineterminator="\n")
        writer.writeheader()
        for item in analysis["all_links"]:
            writer.writerow({
                "scenario": analysis["scenario_name"], "link_id": item["link_id"],
                "endpoint_a": item["endpoint_a"], "endpoint_a_type": item["endpoint_a_type"],
                "endpoint_b": item["endpoint_b"], "endpoint_b_type": item["endpoint_b_type"],
                "in_protection_scope": int(item["in_protection_scope"]),
                "used_by_tt_primary_route": int(item["used_by_tt_primary_route"]),
                "candidate_fault": int(item["candidate_fault"]),
                "affected_flow_count": item["affected_flow_count"],
                "affected_flow_ids": ";".join(item["affected_flows"]),
                "affected_tt_payload_rate_bps": item["affected_load_bps"],
                "min_affected_deadline_us": item["min_affected_deadline_us"],
                "mean_affected_deadline_us": item["mean_affected_deadline_us"],
                "max_affected_deadline_us": item["max_affected_deadline_us"],
                "primary_route_usage_count": item["primary_route_usage_count"],
                "classification": item["classification"],
            })


def candidate_ids(analysis: dict) -> tuple[str, ...]:
    values = tuple(item["fault_id"] for item in analysis["candidate_faults"])
    if values != tuple(sorted(values)):
        raise CriticalLinkError("candidate artifact is not deterministically ordered")
    return values
