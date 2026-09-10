"""Deterministic scenario-local schedulability-pruned fault grouping search."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Callable, Iterable

from tools.jrs_wa_adapter import canonical_json_bytes
from tools.schedulability_necessary_conditions import certificate_prunes

ALGORITHM_VERSION = "SPFG-v1"


def group_id(faults: Iterable[str]) -> str:
    return "FG_" + hashlib.sha256(canonical_json_bytes(sorted(set(faults)))).hexdigest()


def affected_set_key(affected: Iterable[str]) -> str:
    return hashlib.sha256(canonical_json_bytes(sorted(set(affected)))).hexdigest()


def build_affected_set_pools(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    pools: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        affected = candidate.get("affected_flow_ids", [])
        if isinstance(affected, str): affected = [value for value in affected.split(";") if value]
        key = affected_set_key(affected)
        pool = pools.setdefault(key, {"affected_set_key": key, "affected_flow_ids": sorted(affected), "fault_ids": []})
        pool["fault_ids"].append(str(candidate["fault_id"]))
    for pool in pools.values(): pool["fault_ids"].sort()
    return sorted(pools.values(), key=lambda row: (row["affected_set_key"], row["fault_ids"]))


def ranking_key(row: dict[str, Any]) -> tuple[Any, ...]:
    def descending_margin(value: Any) -> tuple[int, float]:
        return (1, 0.0) if value is None else (0, -float(value))
    return (*descending_margin(row.get("window_slack_ticks")),
            *descending_margin(row.get("cut_slack_ticks")),
            *descending_margin(row.get("deadline_slack_ticks")),
            -int(row["group_size"]), str(row["candidate_group_id"]))


@dataclass(frozen=True)
class ActiveGroup:
    faults: tuple[str, ...]
    profile: Any = None

    @property
    def group_id(self) -> str: return group_id(self.faults)


def run_grouping_search(
    fault_ids: Iterable[str],
    evaluate: Callable[[tuple[str, ...]], dict[str, Any]],
    synthesize: Callable[[tuple[str, ...]], dict[str, Any]],
) -> dict[str, Any]:
    """Search one affected-set pool and return a merge-decision audit trail.

    ``evaluate`` performs only F1--F4. ``synthesize`` performs the actual
    H2S->CELF attempt and singleton replay; only its ``accepted`` flag can
    change the active partition.
    """
    active = [ActiveGroup((fault,)) for fault in sorted(set(fault_ids))]
    exact_filter_cache: dict[tuple[str, ...], dict[str, Any]] = {}
    synthesis_cache: dict[tuple[str, ...], dict[str, Any]] = {}
    certificates: list[dict[str, Any]] = []
    candidates, attempts, accepted, iteration = [], [], [], 0
    while len(active) > 1:
        iteration += 1; generated = []
        for left, right in combinations(sorted(active, key=lambda group: group.group_id), 2):
            faults = tuple(sorted(set(left.faults) | set(right.faults)))
            record = {"iteration": iteration, "left_group_id": left.group_id, "right_group_id": right.group_id,
                      "candidate_group_id": group_id(faults), "fault_ids": ";".join(faults), "group_size": len(faults)}
            certificate = next((item for item in certificates if certificate_prunes(item, faults)), None)
            if certificate:
                filtered = {"passed": False, "first_failure": "CERTIFICATE_SUPERSET_PRUNE",
                            "failure_certificate": certificate, "timings_ms": {}}
                record["cache_status"] = "FAILURE_CERTIFICATE_HIT"
            elif faults in exact_filter_cache:
                filtered = exact_filter_cache[faults]; record["cache_status"] = "EXACT_FILTER_CACHE_HIT"
            else:
                filtered = evaluate(faults); exact_filter_cache[faults] = filtered; record["cache_status"] = "MISS"
                if filtered.get("failure_certificate"): certificates.append(filtered["failure_certificate"])
            record.update({"filter_pass": bool(filtered["passed"]), "first_failure": filtered.get("first_failure", ""),
                           "window_slack_ticks": filtered.get("window_slack_ticks"),
                           "cut_slack_ticks": filtered.get("cut_slack_ticks"),
                           "deadline_slack_ticks": filtered.get("deadline_slack_ticks"),
                           "filter_total_ms": sum(filtered.get("timings_ms", {}).values()), "decision": "FILTERED"})
            candidates.append(record); generated.append((record, left, right, faults, filtered))
        ranked = sorted((item for item in generated if item[0]["filter_pass"]), key=lambda item: ranking_key(item[0]))
        winner = None
        for rank, (record, left, right, faults, filtered) in enumerate(ranked, 1):
            record["rank"] = rank
            if faults in synthesis_cache:
                result = synthesis_cache[faults]; record["synthesis_cache_status"] = "EXACT_SYNTHESIS_CACHE_HIT"
            else:
                result = synthesize(faults); synthesis_cache[faults] = result; record["synthesis_cache_status"] = "MISS"
            attempt = {**record, **result}; attempts.append(attempt)
            if result.get("accepted"):
                record["decision"] = "ACCEPTED"; winner = (left, right, faults, result)
                accepted.append({"iteration": iteration, "candidate_group_id": record["candidate_group_id"],
                                 "fault_ids": record["fault_ids"], "group_size": len(faults),
                                 "semantic_profile_hash": result.get("semantic_profile_hash", "")})
                break
            record["decision"] = "BACKEND_REJECTED"
        if winner is None: break
        left, right, faults, result = winner
        for record, a, b, _, _ in ranked:
            if record["decision"] == "FILTERED" and ({a.group_id, b.group_id} & {left.group_id, right.group_id}):
                record["decision"] = "STALE_AFTER_ACCEPT"
        active = [group for group in active if group is not left and group is not right]
        active.append(ActiveGroup(faults, result.get("profile")))
    return {"algorithm_version": ALGORITHM_VERSION, "final_groups": [
                {"group_id": group.group_id, "fault_ids": list(group.faults), "group_size": len(group.faults), "profile": group.profile}
                for group in sorted(active, key=lambda group: group.group_id)],
            "merge_candidates": candidates, "synthesis_attempts": attempts,
            "accepted_merge_history": accepted, "failure_certificates": certificates,
            "search_complete": True, "filter_cache_entries": len(exact_filter_cache),
            "synthesis_cache_entries": len(synthesis_cache)}
