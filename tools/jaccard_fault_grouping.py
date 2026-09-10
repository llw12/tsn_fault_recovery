"""Exact Jaccard eligibility and deterministic JSPFG-v1 search primitives.

Similarity is an a-priori search-space restriction.  It is intentionally
separate from the monotone F1--F4 necessary-condition pipeline.
"""
from __future__ import annotations

import functools
import hashlib
import time
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Callable, Iterable

from tools.fault_grouping_search import group_id
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.schedulability_necessary_conditions import certificate_prunes

ALGORITHM_VERSION = "JSPFG-v1"


@dataclass(frozen=True)
class Threshold:
    identifier: str
    numerator: int
    denominator: int
    positive_overlap: bool = False

    def eligible(self, intersection_count: int, union_count: int) -> bool:
        if self.positive_overlap:
            return intersection_count > 0
        return self.denominator * intersection_count >= self.numerator * union_count

    @property
    def label(self) -> str:
        return "POSITIVE_OVERLAP" if self.positive_overlap else f"{self.numerator}/{self.denominator}"


THRESHOLDS = (
    Threshold("tau_100", 1, 1), Threshold("tau_080", 4, 5),
    Threshold("tau_060", 3, 5), Threshold("tau_040", 2, 5),
    Threshold("tau_020", 1, 5), Threshold("tau_positive", 0, 1, True),
)


def canonical_affected(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(str(value) for value in values)))


def affected_union(*sets: Iterable[str]) -> tuple[str, ...]:
    return canonical_affected(value for values in sets for value in values)


def jaccard_counts(left: Iterable[str], right: Iterable[str]) -> tuple[int, int]:
    a, b = set(left), set(right)
    return len(a & b), len(a | b)


def jaccard_decimal(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.000000000000"
    # This presentation value is never used for an eligibility or sort decision.
    return f"{numerator / denominator:.12f}"


def overlap_decimal(intersection_count: int, left_count: int, right_count: int) -> str:
    denominator = min(left_count, right_count)
    return f"{intersection_count / denominator:.12f}" if denominator else "0.000000000000"


def compare_fraction_desc(left_num: int, left_den: int, right_num: int, right_den: int) -> int:
    """Comparator: negative means the left exact fraction sorts first."""
    product_left, product_right = left_num * right_den, right_num * left_den
    return -1 if product_left > product_right else (1 if product_left < product_right else 0)


def same_affected_set(left: Iterable[str], right: Iterable[str]) -> bool:
    return canonical_affected(left) == canonical_affected(right)


@dataclass(frozen=True)
class ActiveGroup:
    faults: tuple[str, ...]
    affected: tuple[str, ...]
    profile: Any = None

    @property
    def group_id(self) -> str:
        return group_id(self.faults)


def _margin_key(value: Any) -> tuple[int, float]:
    return (1, 0.0) if value is None else (0, -float(value))


def candidate_compare(left: dict[str, Any], right: dict[str, Any]) -> int:
    fraction = compare_fraction_desc(int(left["jaccard_num"]), int(left["jaccard_den"]),
                                     int(right["jaccard_num"]), int(right["jaccard_den"]))
    if fraction:
        return fraction
    left_tail = (*_margin_key(left.get("window_slack_ticks")), *_margin_key(left.get("cut_slack_ticks")),
                 *_margin_key(left.get("deadline_slack_ticks")), -int(left["union_group_size"]), str(left["candidate_group_id"]))
    right_tail = (*_margin_key(right.get("window_slack_ticks")), *_margin_key(right.get("cut_slack_ticks")),
                  *_margin_key(right.get("deadline_slack_ticks")), -int(right["union_group_size"]), str(right["candidate_group_id"]))
    return -1 if left_tail < right_tail else (1 if left_tail > right_tail else 0)


def run_jaccard_grouping_search(
    fault_affected: dict[str, Iterable[str]], threshold: Threshold,
    evaluate: Callable[[tuple[str, ...], tuple[str, ...]], dict[str, Any]],
    synthesize: Callable[[tuple[str, ...], tuple[str, ...], dict[str, Any]], dict[str, Any]],
    candidate_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one independent scenario/threshold JSPFG-v1 search.

    All active pairs are audited.  Jaccard rejection cannot create a
    certificate; only F1--F4 certificates may prune a fault-set superset.
    """
    active = [ActiveGroup((fault,), canonical_affected(affected))
              for fault, affected in sorted(fault_affected.items())]
    exact_filter_cache: dict[tuple[str, ...], dict[str, Any]] = {}
    synthesis_cache: dict[tuple[str, ...], dict[str, Any]] = {}
    certificates: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []; attempts: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []; iteration = 0
    similarity_ms = filter_ms = 0.0
    funnel = {"generated_active_pairs": 0, "jaccard_rejected": 0, "jaccard_eligible": 0,
              "certificate_pruned": 0, "F1_pruned": 0, "F2_pruned": 0,
              "F3_pruned": 0, "F4_pruned": 0, "filter_pass": 0, "stale_after_accept": 0}
    stage_samples: dict[str, list[float]] = {}
    while len(active) > 1:
        iteration += 1; generated: list[tuple[dict[str, Any], ActiveGroup, ActiveGroup, tuple[str, ...], tuple[str, ...], dict[str, Any] | None]] = []
        iteration_rows: list[dict[str, Any]] = []
        started = time.perf_counter_ns()
        pairs = list(combinations(sorted(active, key=lambda row: row.group_id), 2))
        similarity_ms += (time.perf_counter_ns() - started) / 1e6
        for left, right in pairs:
            started = time.perf_counter_ns()
            intersection_count, union_count = jaccard_counts(left.affected, right.affected)
            faults, affected = tuple(sorted(set(left.faults) | set(right.faults))), affected_union(left.affected, right.affected)
            similarity_ms += (time.perf_counter_ns() - started) / 1e6
            eligible = threshold.eligible(intersection_count, union_count)
            record: dict[str, Any] = {
                "iteration": iteration, "left_group_id": left.group_id, "right_group_id": right.group_id,
                "left_group_size": len(left.faults), "right_group_size": len(right.faults),
                "candidate_group_id": group_id(faults), "fault_ids": ";".join(faults),
                "union_group_size": len(faults), "left_affected_count": len(left.affected),
                "right_affected_count": len(right.affected), "union_affected_count": len(affected),
                "intersection_count": intersection_count, "union_count": union_count,
                "jaccard_num": intersection_count, "jaccard_den": union_count,
                "jaccard_decimal": jaccard_decimal(intersection_count, union_count),
                "overlap_coefficient": overlap_decimal(intersection_count, len(left.affected), len(right.affected)),
                "threshold_id": threshold.identifier, "threshold_num": threshold.numerator,
                "threshold_den": threshold.denominator, "threshold_pass": eligible,
            }
            funnel["generated_active_pairs"] += 1
            if not eligible:
                record.update({"decision": "JACCARD_REJECT", "first_failure": "", "filter_pass": False,
                               "filter_total_ms": 0.0, "cache_status": "NOT_APPLICABLE"})
                funnel["jaccard_rejected"] += 1; iteration_rows.append(record); continue
            funnel["jaccard_eligible"] += 1
            certificate = next((item for item in certificates if certificate_prunes(item, faults)), None)
            if certificate:
                filtered = {"passed": False, "first_failure": "CERTIFICATE_SUPERSET_PRUNE",
                            "failure_certificate": certificate, "timings_ms": {}}
                record["cache_status"] = "FAILURE_CERTIFICATE_HIT"; funnel["certificate_pruned"] += 1
            elif faults in exact_filter_cache:
                filtered = exact_filter_cache[faults]; record["cache_status"] = "EXACT_FILTER_CACHE_HIT"
            else:
                started = time.perf_counter_ns(); filtered = evaluate(faults, affected)
                filter_ms += (time.perf_counter_ns() - started) / 1e6
                exact_filter_cache[faults] = filtered; record["cache_status"] = "MISS"
                if filtered.get("failure_certificate"): certificates.append(filtered["failure_certificate"])
            failure = filtered.get("first_failure", "")
            if failure == "F1_CONNECTIVITY": funnel["F1_pruned"] += 1
            elif failure == "F2_MINIMUM_DELAY": funnel["F2_pruned"] += 1
            elif failure == "F3_CHECKED_CUT_CAPACITY": funnel["F3_pruned"] += 1
            elif failure == "F4_TIME_WINDOW": funnel["F4_pruned"] += 1
            if filtered["passed"]: funnel["filter_pass"] += 1
            record.update({"decision": "FILTER_PASS" if filtered["passed"] else "NECESSARY_CONDITION_PRUNE",
                           "first_failure": failure, "filter_pass": bool(filtered["passed"]),
                           "window_slack_ticks": filtered.get("window_slack_ticks"),
                           "cut_slack_ticks": filtered.get("cut_slack_ticks"),
                           "deadline_slack_ticks": filtered.get("deadline_slack_ticks"),
                           "filter_total_ms": sum(filtered.get("timings_ms", {}).values())})
            for stage, elapsed_ms in filtered.get("timings_ms", {}).items(): record[f"filter_{stage.lower()}_ms"] = elapsed_ms
            for stage, elapsed_ms in filtered.get("timings_ms", {}).items(): stage_samples.setdefault(stage, []).append(float(elapsed_ms))
            iteration_rows.append(record); generated.append((record, left, right, faults, affected, filtered))
        ranked = sorted((item for item in generated if item[0]["filter_pass"]),
                        key=functools.cmp_to_key(lambda a, b: candidate_compare(a[0], b[0])))
        winner = None
        for rank, (record, left, right, faults, affected, filtered) in enumerate(ranked, 1):
            record["rank"] = rank
            if faults in synthesis_cache:
                result = synthesis_cache[faults]; record["synthesis_cache_status"] = "EXACT_SYNTHESIS_CACHE_HIT"
            else:
                result = synthesize(faults, affected, record); synthesis_cache[faults] = result; record["synthesis_cache_status"] = "MISS"
            attempts.append({**record, **{key: value for key, value in result.items() if key != "profile"}})
            if result.get("accepted"):
                record["decision"] = "ACCEPTED"; winner = (left, right, faults, affected, result, record)
                accepted.append({"iteration": iteration, "left_group_id": left.group_id, "right_group_id": right.group_id,
                                 "new_group_id": record["candidate_group_id"], "fault_ids": record["fault_ids"],
                                 "group_size": len(faults), "jaccard_num": record["jaccard_num"],
                                 "jaccard_den": record["jaccard_den"], "jaccard_decimal": record["jaccard_decimal"],
                                 "window_slack_ticks": record.get("window_slack_ticks"),
                                 "cut_slack_ticks": record.get("cut_slack_ticks"),
                                 "deadline_slack_ticks": record.get("deadline_slack_ticks"),
                                 "algorithm_used": result.get("algorithm_used", ""),
                                 "backend_ms": result.get("total_backend_ms", 0),
                                 "semantic_profile_hash": result.get("semantic_profile_hash", "")})
                break
            record["decision"] = "BACKEND_REJECTED"
        if winner is None:
            if candidate_sink:
                for record in iteration_rows: candidate_sink(record)
            else: candidate_rows.extend(iteration_rows)
            break
        left, right, faults, affected, result, accepted_record = winner
        for record, a, b, _, _, _ in ranked:
            if record["decision"] == "FILTER_PASS" and ({a.group_id, b.group_id} & {left.group_id, right.group_id}):
                record["decision"] = "STALE_AFTER_ACCEPT"; funnel["stale_after_accept"] += 1
        if candidate_sink:
            for record in iteration_rows: candidate_sink(record)
        else:
            candidate_rows.extend(iteration_rows)
        active = [row for row in active if row is not left and row is not right]
        active.append(ActiveGroup(faults, affected, result.get("profile")))
        # The active group owns the only profile copy needed by later merges.
        # Retaining every successful intermediate profile in the exact-result
        # cache makes a long threshold sweep needlessly memory-proportional to
        # backend attempts.  The cache still preserves the immutable outcome.
        if faults in synthesis_cache:
            synthesis_cache[faults] = {key: value for key, value in synthesis_cache[faults].items()
                                       if key != "profile"}
    return {"algorithm_version": ALGORITHM_VERSION, "threshold": threshold, "final_groups": [
                {"group_id": row.group_id, "fault_ids": list(row.faults), "affected_flow_ids": list(row.affected),
                 "group_size": len(row.faults), "profile": row.profile}
                for row in sorted(active, key=lambda row: row.group_id)],
            "merge_candidates": candidate_rows, "synthesis_attempts": attempts,
            "accepted_merge_history": accepted, "failure_certificates": certificates,
            "funnel": funnel, "similarity_ms": similarity_ms, "filter_ms": filter_ms,
            "filter_stage_samples_ms": stage_samples,
            "search_complete": True, "filter_cache_entries": len(exact_filter_cache),
            "synthesis_cache_entries": len(synthesis_cache)}
