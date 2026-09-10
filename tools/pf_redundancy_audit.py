"""Read-only exact redundancy and static-replay primitives for exp20.

Nothing in this module invokes a recovery synthesis backend or an upstream
solver.  It consumes the immutable exp19 profile/raw artifacts and either
replays exact saved slots or proves the target-fault part of the independent
checker by factoring its scenario-invariant and disabled-link predicates.
"""
from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import io
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.h2s_jrs_backend import H2sAdapterError, H2sPreparedInputs, check_h2s_pf_solution, normalize_schedule, parse_backend_output
from tools.h2s_pf_backend import SEMANTIC_PROFILE_FIELDS, semantic_profile_hash, semantic_profile_projection
from tools.jrs_wa_adapter import canonical_json_bytes


class RedundancyAuditError(RuntimeError):
    """Raised when a frozen input or exact replay contract is violated."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_sha(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def deterministic_gzip(value: bytes) -> bytes:
    buffer = io.BytesIO()
    # Match exp19's per-profile compression representation exactly. The
    # filename is stored in the gzip header, so it matters for byte parity.
    with gzip.GzipFile(filename="profile.json", mode="wb", fileobj=buffer, mtime=0) as handle:
        handle.write(value)
    return buffer.getvalue()


def affected_ids(value: str | Iterable[str]) -> tuple[str, ...]:
    raw = value.split(";") if isinstance(value, str) else value
    ids = tuple(sorted(str(item) for item in raw if str(item)))
    if len(ids) != len(set(ids)):
        raise RedundancyAuditError(f"AFFECTED_FLOW_ID_DUPLICATE:{ids}")
    return ids


def affected_set_sha(value: str | Iterable[str]) -> str:
    return canonical_sha(list(affected_ids(value)))


def profile_components(profile: dict[str, Any]) -> dict[str, str]:
    """Exact, canonical component fingerprints used only for observation."""
    route_forwarding = {key: profile[key] for key in ("forwarding_model", "logical_routes", "stream_forwarding")}
    schedule = {key: profile[key] for key in ("release_offsets_ns", "gate_schedules", "schedule_windows")}
    semantic = semantic_profile_projection(profile)
    return {
        "SEMANTIC_FULL": canonical_sha(semantic),
        "ROUTE_FORWARDING": canonical_sha(route_forwarding),
        "LOGICAL_ROUTE_ONLY": canonical_sha(profile["logical_routes"]),
        "SCHEDULE": canonical_sha(schedule),
    }


def route_hash(routes: Iterable[dict[str, Any]]) -> str:
    return canonical_sha(sorted(routes, key=lambda row: str(row["flow_id"])))


def schedule_hash(normalized: dict[str, Any]) -> str:
    return canonical_sha(normalized["route_schedule"])


def raw_schedule_marker_count(text: str) -> int:
    return sum(line.startswith("H2S_SCHEDULE_JSON:") for line in text.splitlines())


def load_raw_h2s(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse one saved H2S output and return data plus its auditable facts."""
    text = path.read_text(encoding="utf-8")
    markers = raw_schedule_marker_count(text)
    if markers != 1:
        raise RedundancyAuditError(f"RAW_SCHEDULE_MARKER_COUNT:{path}:{markers}")
    try:
        raw = parse_backend_output(text)
    except (H2sAdapterError, json.JSONDecodeError) as error:
        raise RedundancyAuditError(f"RAW_PARSE_FAILED:{path}:{error}") from error
    return raw, {"raw_H2S_output_found": True, "exactly_one_schedule_marker": True,
                 "raw_slot_count": len(raw.get("slots", [])), "raw_parse_pass": True}


def rebind_queue_ids(raw: dict[str, Any], target: H2sPreparedInputs) -> dict[str, Any]:
    """Copy an exact raw schedule and rebind only target-local queue IDs.

    ``queue_id`` is assigned by a target topology's enumeration; every other
    slot field is checked byte-for-value unchanged.  A missing target arc is
    an exact static replay rejection (usually because the target fault is on
    the saved route).
    """
    rebound = copy.deepcopy(raw)
    for original, slot in zip(raw.get("slots", []), rebound.get("slots", [])):
        source, destination = int(original["source"]), int(original["destination"])
        try:
            queue = target.queue_by_arc[(source, destination)] if target.queue_by_arc else None
        except KeyError as error:
            raise RedundancyAuditError(f"TARGET_DISABLED_OR_UNKNOWN_ARC:{source}->{destination}") from error
        if queue is None:
            raise RedundancyAuditError(f"TARGET_QUEUE_MAPPING_MISSING:{source}->{destination}")
        slot["queue_id"] = queue
        for field in ("flow_id", "source", "destination", "start_tick", "end_tick", "config_id"):
            if slot[field] != original[field]:
                raise RedundancyAuditError(f"REPLAY_MUTATED_{field}")
    return rebound


def replay_exact_raw(raw: dict[str, Any], target: H2sPreparedInputs) -> tuple[dict[str, Any], dict[str, Any]]:
    rebound = rebind_queue_ids(raw, target)
    normalized = normalize_schedule(target, rebound, 100)
    return normalized, check_h2s_pf_solution(target, normalized)


def profile_link_set(profile: dict[str, Any]) -> frozenset[str]:
    return frozenset(str(link) for route in profile["logical_routes"] for link in route["link_path"])


def profile_arcs(profile: dict[str, Any], prepared: H2sPreparedInputs) -> frozenset[tuple[int, int]]:
    result = set()
    for route in profile["logical_routes"]:
        for source, destination in zip(route["node_path"], route["node_path"][1:]):
            result.add((prepared.node_map[source], prepared.node_map[destination]))
    return frozenset(result)


def checker_factor_replay_valid(*, source_base_checker_pass: bool, source_arcs: frozenset[tuple[int, int]],
                                target: H2sPreparedInputs) -> tuple[bool, str]:
    """Exact factor of ``check_h2s_pf_solution`` for an all-reroute replay.

    In a single SAR scenario the base checker sees the same flows, timing
    quantization, releases and topology except for ``disabled_links``.  Once
    a source raw schedule passed its diagonal full check, the only target
    dependent predicate is that every directed saved route arc remains in
    ``target.queue_by_arc`` (and, equivalently, no disabled physical link is
    used).  This function is deliberately narrow and is regression-tested
    against full raw replay; it is not a new scheduling or routing policy.
    """
    if not source_base_checker_pass:
        return False, "SOURCE_BASE_CHECKER_FAILED"
    target_arcs = frozenset((target.queue_by_arc or {}).keys())
    if not source_arcs <= target_arcs:
        return False, "TARGET_DISABLED_LINK_USED"
    return True, "CROSS_FAULT_STATIC_VALID"


def static_profile_from_normalized(normalized: dict[str, Any]) -> dict[str, Any]:
    """Projection-only profile used to compare saved replay semantics."""
    return normalized["profile"]


def validate_diagonal(*, normalized: dict[str, Any], checker: dict[str, Any], expected: dict[str, str]) -> dict[str, bool]:
    profile = static_profile_from_normalized(normalized)
    return {
        "normalize_pass": True,
        "project_static_checker_pass": bool(checker["valid"]),
        "route_hash_parity": route_hash(normalized["logical_routes"]) == expected["route_hash"],
        "schedule_hash_parity": schedule_hash(normalized) == expected["schedule_hash"],
        "semantic_hash_parity": semantic_profile_hash(profile) == expected["semantic_profile_hash"],
    }


def group_rows(records: Iterable[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records: grouped[str(record[key])].append(record)
    return {value: sorted(rows, key=lambda row: str(row["fault_id"])) for value, rows in sorted(grouped.items())}


def exact_set_cover(coverage: dict[str, set[str]], targets: Iterable[str], *, timeout_s: int = 60) -> dict[str, Any]:
    """Exact deterministic boolean set cover, never a greedy substitute."""
    started = time.perf_counter()
    try:
        import z3  # type: ignore
    except ImportError as error:
        return {"exact": False, "reason": "Z3_UNAVAILABLE", "solve_ms": (time.perf_counter() - started) * 1000}
    profiles, target_ids = sorted(coverage), sorted(set(targets))
    if not target_ids:
        return {"exact": True, "minimum_profile_count": 0, "selected_profile_fault_ids": [], "solve_ms": 0.0, "z3_version": z3.get_version_string()}
    covers = {target: [profile for profile in profiles if target in coverage[profile]] for target in target_ids}
    if any(not values for values in covers.values()):
        return {"exact": False, "reason": "UNCOVERED_TARGET", "solve_ms": (time.perf_counter() - started) * 1000, "z3_version": z3.get_version_string()}
    variables = {profile: z3.Bool(f"p_{index}") for index, profile in enumerate(profiles)}
    base = [z3.Or(*(variables[profile] for profile in covers[target])) for target in target_ids]
    low, high = 1, len(profiles); optimum: int | None = None
    def sat(extra: list[Any]) -> tuple[bool, Any]:
        solver = z3.Solver(); solver.set(timeout=max(1, int(timeout_s * 1000 - (time.perf_counter() - started) * 1000)))
        solver.add(*base, *extra)
        result = solver.check()
        return result == z3.sat, solver.model() if result == z3.sat else None
    while low <= high:
        midpoint = (low + high) // 2
        okay, _ = sat([z3.PbLe([(variables[profile], 1) for profile in profiles], midpoint)])
        if time.perf_counter() - started > timeout_s:
            return {"exact": False, "reason": "SET_COVER_TIMEOUT", "solve_ms": (time.perf_counter() - started) * 1000, "z3_version": z3.get_version_string()}
        if okay: optimum, high = midpoint, midpoint - 1
        else: low = midpoint + 1
    if optimum is None:
        return {"exact": False, "reason": "SET_COVER_UNSAT", "solve_ms": (time.perf_counter() - started) * 1000, "z3_version": z3.get_version_string()}
    # Canonical tie-break: include an earlier fault/profile exactly when an
    # optimum-size completion remains feasible; otherwise exclude it.
    fixed: list[Any] = [z3.PbLe([(variables[profile], 1) for profile in profiles], optimum)]
    selected: list[str] = []
    for profile in profiles:
        okay, _ = sat([*fixed, variables[profile]])
        if time.perf_counter() - started > timeout_s:
            return {"exact": False, "reason": "SET_COVER_TIEBREAK_TIMEOUT", "solve_ms": (time.perf_counter() - started) * 1000, "z3_version": z3.get_version_string()}
        if okay:
            fixed.append(variables[profile]); selected.append(profile)
        else:
            fixed.append(z3.Not(variables[profile]))
    return {"exact": True, "minimum_profile_count": optimum, "selected_profile_fault_ids": selected,
            "solve_ms": (time.perf_counter() - started) * 1000, "z3_version": z3.get_version_string()}


def csv_gzip_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fields, lineterminator="\n", extrasaction="ignore")
    writer.writeheader(); writer.writerows(rows)
    return deterministic_gzip(text.getvalue().encode("utf-8"))
