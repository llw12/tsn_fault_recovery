"""exp20 read-only audit of redundancy among exp19 PF profiles.

The runner has no recovery backend and no scheduler executable.  Its only
network operation is conversion/normalization of exp19's saved raw H2S slots
and the project-independent static checker.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.fixed_release_collision_audit import ROOT, tree_sha256
from tools.h2s_jrs_backend import prepare_h2s_inputs
from tools.h2s_pf_backend import SEMANTIC_PROFILE_FIELDS, semantic_profile_hash, semantic_profile_projection
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.pf_redundancy_audit import (
    RedundancyAuditError, affected_ids, affected_set_sha, canonical_sha, checker_factor_replay_valid,
    csv_gzip_bytes, deterministic_gzip, exact_set_cover, load_raw_h2s, profile_components,
    profile_link_set, replay_exact_raw, route_hash, schedule_hash, sha256_file, validate_diagonal,
)

ORDER = ("M_RING", "M_REDSTAR", "M_ROR", "L_RING", "L_REDSTAR", "L_ROR")
EXP19 = ROOT / "results" / "realistic_full_density_pf_cost"
OUT = ROOT / "results" / "pf_redundancy_opportunity_audit"
SCENARIO_BUDGET = 60


class AuditStop(RuntimeError):
    pass


def write_atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with temporary.open("wb") as handle:
            handle.write(value); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def write_json(path: Path, value: Any) -> None:
    write_atomic_bytes(path, canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    fields = fields or sorted({key for row in rows for key in row})
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader(); writer.writerows(rows)
    write_atomic_bytes(path, text.getvalue().encode("utf-8"))


def write_gzip_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    write_atomic_bytes(path, csv_gzip_bytes(rows, fields))


def git_value(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def artifact_sha(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): sha256_file(path) for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "analysis_manifest.json"}


def frozen_result_trees() -> dict[str, str]:
    """Hash every pre-exp20 result tree, including the full exp19 evidence."""
    roots = [path for path in sorted((ROOT / "results").iterdir()) if path.is_dir() and path != OUT]
    return {str(path.relative_to(ROOT)): tree_sha256(path) for path in roots}


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def bool_value(value: str | bool) -> bool:
    return value is True or value == "True" or value == "true"


def load_exp19() -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]], dict[str, Path]]:
    required = ("campaign_verdict.json", "candidate_fault_census.csv", "candidate_faults.json", "per_fault_results.csv",
                "profile_storage.csv", "p0_baseline.csv", "p0_route_manifest.json", "analysis_manifest.json")
    missing = [name for name in required if not (EXP19 / name).is_file()]
    if missing: raise AuditStop(f"EXP19_INPUT_MISSING:{missing}")
    verdict = json.loads((EXP19 / "campaign_verdict.json").read_text(encoding="utf-8"))
    gates = {"formal_verdict": "PF_FULL_CENSUS_COMPLETE_ALL_PROFILES", "attempted_faults": 1099,
             "expected_faults": 1099, "valid_profiles": 1099, "no_sampling": True,
             "no_profile_grouping": True, "no_profile_deduplication": True}
    mismatch = {key: {"expected": expected, "actual": verdict.get(key)} for key, expected in gates.items() if verdict.get(key) != expected}
    if mismatch: raise AuditStop(f"EXP19_BASELINE_NOT_QUALIFIED:{mismatch}")
    candidates = json.loads((EXP19 / "candidate_faults.json").read_text(encoding="utf-8"))
    p0 = json.loads((EXP19 / "p0_route_manifest.json").read_text(encoding="utf-8"))
    source = json.loads((EXP19 / "source_benchmark_manifest.json").read_text(encoding="utf-8"))
    listed = source["selected_manifest"]["derived_scenario_paths"]
    paths = {scenario: ROOT / listed[scenario] for scenario in ORDER}
    if any(not path.is_file() for path in paths.values()): raise AuditStop("EXP19_SOURCE_SCENARIO_MISSING")
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for scenario in ORDER:
        values = candidates.get(scenario, {}).get("candidates", [])
        by_scenario[scenario] = sorted(values, key=lambda row: str(row["fault_id"]))
    if sum(len(values) for values in by_scenario.values()) != 1099: raise AuditStop("EXP19_CANDIDATE_COUNT_MISMATCH")
    return verdict, by_scenario, p0, paths


def source_manifest(pre_trees: dict[str, str]) -> dict[str, Any]:
    files = ("campaign_verdict.json", "candidate_faults.json", "per_fault_results.csv", "profile_storage.csv",
             "stores/semantic_profile_index.json", "analysis_manifest.json")
    return {"exp19_commit": git_value("log", "-1", "--format=%H", "--", str(EXP19.relative_to(ROOT))),
            "exp19_artifact_sha256": {name: sha256_file(EXP19 / name) for name in files},
            "frozen_result_tree_sha256": pre_trees}


def integrity_audit(candidates: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    results = {(row["scenario"], row["fault_id"]): row for row in load_csv(EXP19 / "per_fault_results.csv")}
    storage = {(row["scenario"], row["fault_id"]): row for row in load_csv(EXP19 / "profile_storage.csv") if row["fault_id"] != "P0"}
    semantic_index = json.loads((EXP19 / "stores" / "semantic_profile_index.json").read_text(encoding="utf-8"))["references"]
    index = {(row["scenario"], row["fault_id"]): row for row in semantic_index}
    output, records = [], {}
    for scenario in ORDER:
        for candidate in candidates[scenario]:
            fault = str(candidate["fault_id"]); key = (scenario, fault); row, store = results.get(key), storage.get(key)
            profile_path = EXP19 / "profiles" / scenario / f"{fault}.json"
            checks = {"candidate_present": row is not None and store is not None and key in index, "profile_present": profile_path.is_file()}
            profile: dict[str, Any] | None = None
            if profile_path.is_file():
                payload = profile_path.read_bytes(); profile = json.loads(payload)
                canonical = hashlib.sha256(payload).hexdigest(); semantic = semantic_profile_hash(profile)
                components = profile_components(profile); semantic_bytes = canonical_json_bytes(semantic_profile_projection(profile))
                checks.update({"profile_sha_parity": bool(row and store and canonical == row["profile_sha256"] == store["profile_sha256"]),
                    "semantic_sha_parity": bool(row and store and index.get(key) and semantic == row["semantic_profile_hash"] == store["semantic_profile_hash"] == index[key]["semantic_profile_hash"]),
                    "semantic_bytes_parity": bool(row and int(row["semantic_payload_bytes"]) == len(semantic_bytes) and int(row["semantic_gzip_bytes"]) == len(deterministic_gzip(semantic_bytes))),
                    "scenario_local": bool(row and row["scenario"] == scenario and profile.get("fault_id") == fault),
                    "success_h2s": bool(row and row["status"] == "SUCCESS_H2S"), "semantic_valid": bool(row and bool_value(row["semantic_valid"]))})
                records[key] = {"scenario": scenario, "fault_id": fault, "candidate": candidate, "profile_path": profile_path,
                    "canonical_profile_sha": canonical, "semantic_hash": semantic, "components": components,
                    "semantic_payload_bytes": len(semantic_bytes), "semantic_gzip_bytes": len(deterministic_gzip(semantic_bytes)),
                    "canonical_profile_bytes": len(payload), "canonical_gzip_bytes": len(deterministic_gzip(payload)),
                    # Do not retain all complete JSON profiles in memory.
                    # Static arc membership is exactly reconstructed later
                    # from this set and the frozen scenario arc mapping.
                    "link_set": profile_link_set(profile), "row": row}
            output.append({"scenario": scenario, "fault_id": fault, "profile_ref": row.get("profile_ref", "") if row else "",
                "canonical_profile_sha256": records.get(key, {}).get("canonical_profile_sha", ""),
                "semantic_profile_hash": records.get(key, {}).get("semantic_hash", ""), **checks,
                "integrity_pass": all(checks.values())})
    if len(records) != 1099 or not all(row["integrity_pass"] for row in output): raise AuditStop("INPUT_INTEGRITY_FAILED")
    return output, records


def affected_groups(candidates: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    groups, members, summary, lookup = [], [], [], {}
    for scenario in ORDER:
        by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for candidate in candidates[scenario]: by_hash[affected_set_sha(candidate["affected_flow_ids"])].append(candidate)
        sizes = []
        for index, (digest, values) in enumerate(sorted(by_hash.items()), 1):
            values = sorted(values, key=lambda row: str(row["fault_id"])); fault_ids = [str(row["fault_id"]) for row in values]
            group_id = f"ASG_{scenario}_{index:04d}"; ids = affected_ids(values[0]["affected_flow_ids"])
            group = {"scenario": scenario, "group_id": group_id, "affected_set_sha": digest, "affected_flow_count": len(ids),
                     "fault_count": len(values), "fault_ids": ";".join(fault_ids), "min_fault_id": fault_ids[0], "group_size": len(values),
                     "singleton": len(values) == 1, "fraction_of_scenario_faults": len(values) / len(candidates[scenario])}
            groups.append(group); sizes.append(len(values)); lookup[(scenario, group_id)] = {**group, "members": values}
            members.extend({"scenario": scenario, "group_id": group_id, "fault_id": fault, "affected_set_sha": digest} for fault in fault_ids)
        summary.append({"scenario": scenario, "fault_count": len(candidates[scenario]), "affected_set_group_count": len(sizes),
            "singleton_group_count": sum(size == 1 for size in sizes), "multi_fault_group_count": sum(size > 1 for size in sizes),
            "largest_group": max(sizes, default=0), "mean_group_size": sum(sizes) / len(sizes) if sizes else 0,
            "median_group_size": sorted(sizes)[(len(sizes) - 1) // 2] if sizes else 0,
            "faults_in_multi_member_groups": sum(size for size in sizes if size > 1),
            "fraction_faults_in_multi_groups": sum(size for size in sizes if size > 1) / len(candidates[scenario]) if candidates[scenario] else 0,
            "affected_set_candidate_solve_reduction": 1 - len(sizes) / len(candidates[scenario]) if candidates[scenario] else 0})
    return groups, members, summary, lookup


def component_outputs(records: dict[tuple[str, str], dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    semantic_groups, canonical_groups, component_rows, semantic_summary = [], [], [], []
    for scenario in ORDER:
        values = [record for (sid, _), record in records.items() if sid == scenario]
        by_semantic: dict[str, list[dict[str, Any]]] = defaultdict(list); by_canonical: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for value in values: by_semantic[value["semantic_hash"]].append(value); by_canonical[value["canonical_profile_sha"]].append(value)
        for digest, rows in sorted(by_semantic.items()):
            rows = sorted(rows, key=lambda row: row["fault_id"]); representative = rows[0]
            semantic_groups.append({"scenario": scenario, "semantic_hash": digest, "profile_count": len(rows),
                "fault_ids": ";".join(row["fault_id"] for row in rows), "representative_fault": representative["fault_id"],
                "semantic_payload_bytes": representative["semantic_payload_bytes"], "semantic_gzip_bytes": representative["semantic_gzip_bytes"]})
        for digest, rows in sorted(by_canonical.items()):
            if len(rows) >= 2:
                canonical_groups.append({"scenario": scenario, "canonical_profile_sha256": digest, "group_size": len(rows),
                    "fault_ids": ";".join(sorted(row["fault_id"] for row in rows)), "representative_fault": min(row["fault_id"] for row in rows)})
        semantic_summary.append({"scenario": scenario, "fault_profiles": len(values), "unique_semantic_profiles": len(by_semantic),
            "duplicate_profile_count": len(values) - len(by_semantic), "largest_semantic_group": max((len(rows) for rows in by_semantic.values()), default=0),
            "semantic_duplicate_fraction": 1 - len(by_semantic) / len(values) if values else 0,
            "unique_canonical_profiles": len(by_canonical), "canonical_duplicate_profile_count": len(values) - len(by_canonical)})
        for component in ("SEMANTIC_FULL", "ROUTE_FORWARDING", "LOGICAL_ROUTE_ONLY", "SCHEDULE"):
            hashes = Counter(record["components"][component] for record in values)
            component_rows.append({"scenario": scenario, "component": component, "fault_count": len(values), "unique_hashes": len(hashes),
                "duplicate_count": len(values) - len(hashes), "duplicate_fraction": 1 - len(hashes) / len(values) if values else 0,
                "largest_group": max(hashes.values(), default=0)})
    return semantic_groups, canonical_groups, component_rows, semantic_summary


def prepare_targets(temp: Path, candidates: dict[str, list[dict[str, Any]]], p0: dict[str, Any], paths: dict[str, Path]) -> tuple[dict[tuple[str, str], Any], dict[str, Any]]:
    prepared, bases = {}, {}
    for scenario in ORDER:
        if not candidates[scenario]:
            continue
        healthy = {row["flow_id"]: row for row in p0[scenario]["logical_routes"]}
        bases[scenario] = prepare_h2s_inputs(paths[scenario], temp / scenario / "base", 100, 5)
        for candidate in candidates[scenario]:
            fault = str(candidate["fault_id"])
            prepared[(scenario, fault)] = prepare_h2s_inputs(paths[scenario], temp / scenario / fault, 100, 5,
                disabled_links=(fault,), healthy_primary_routes=healthy, affected_flow_ids=affected_ids(candidate["affected_flow_ids"]), route_scope="all-reroute")
            if (prepared[(scenario, fault)].node_map != bases[scenario].node_map or
                    prepared[(scenario, fault)].flow_map != bases[scenario].flow_map):
                raise AuditStop(f"TARGET_NODE_OR_FLOW_MAP_CHANGED:{scenario}:{fault}")
    return prepared, bases


def raw_and_diagonal(temp: Path, candidates: dict[str, list[dict[str, Any]]], records: dict[tuple[str, str], dict[str, Any]],
                     prepared: dict[tuple[str, str], Any], bases: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], frozenset[tuple[int, int]]]]:
    raw_rows, diagonal, arcs = [], [], {}
    for scenario in ORDER:
        for candidate in candidates[scenario]:
            fault = str(candidate["fault_id"]); key = (scenario, fault)
            raw_path = EXP19 / "raw_backend_output" / scenario / fault / "primary" / "logs" / "0_h2s_stdout.log"
            raw_fact = {"scenario": scenario, "fault_id": fault, "raw_H2S_output_found": raw_path.is_file(), "exactly_one_schedule_marker": False,
                        "raw_slot_count": 0, "raw_parse_pass": False}
            try:
                raw, facts = load_raw_h2s(raw_path); raw_fact.update(facts)
                normalized, checker = replay_exact_raw(raw, prepared[key])
                expected = {name: records[key]["row"][name] for name in ("route_hash", "schedule_hash", "semantic_profile_hash")}
                checks = validate_diagonal(normalized=normalized, checker=checker, expected=expected)
                diagonal.append({"scenario": scenario, "fault_id": fault, **checks, "diagonal_pass": all(checks.values())})
                arcs[key] = frozenset(arc for arc, link in bases[scenario].arc_to_link.items()
                                      if link in records[key]["link_set"])
            except (OSError, RedundancyAuditError, ValueError, KeyError) as error:
                raw_fact["failure_reason"] = str(error); diagonal.append({"scenario": scenario, "fault_id": fault, "normalize_pass": False,
                    "project_static_checker_pass": False, "route_hash_parity": False, "schedule_hash_parity": False,
                    "semantic_hash_parity": False, "diagonal_pass": False, "failure_reason": str(error)})
            raw_rows.append(raw_fact)
    return raw_rows, diagonal, arcs


def cross_replay(candidates: dict[str, list[dict[str, Any]]], records: dict[tuple[str, str], dict[str, Any]], prepared: dict[tuple[str, str], Any],
                 arcs: dict[tuple[str, str], frozenset[tuple[int, int]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], set[str]]]:
    edges, coverage, failures = [], defaultdict(set), Counter()
    for scenario in ORDER:
        values = candidates[scenario]
        for source in values:
            source_fault = str(source["fault_id"]); key = (scenario, source_fault)
            for target in values:
                target_fault = str(target["fault_id"]); valid, reason = checker_factor_replay_valid(
                    source_base_checker_pass=True, source_arcs=arcs[key], target=prepared[(scenario, target_fault)])
                if valid:
                    coverage[key].add(target_fault)
                    edges.append({"scenario": scenario, "source_profile_fault": source_fault, "target_fault": target_fault,
                        "source_semantic_hash": records[key]["semantic_hash"],
                        "same_affected_set": affected_set_sha(source["affected_flow_ids"]) == affected_set_sha(target["affected_flow_ids"]),
                        "same_semantic_hash": records[key]["semantic_hash"] == records[(scenario, target_fault)]["semantic_hash"], "target_covered": True})
                else: failures[(scenario, reason)] += 1
    summary = [{"scenario": scenario, "failure_reason": reason, "invalid_count": count}
               for (scenario, reason), count in sorted(failures.items())]
    return edges, summary, coverage


def union_and_group_outputs(temp: Path, groups_lookup: dict[tuple[str, str], dict[str, Any]], records: dict[tuple[str, str], dict[str, Any]],
                            coverage: dict[tuple[str, str], set[str]], paths: dict[str, Path], p0: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], str]]:
    reuse_rows, union_rows, diversity_rows, mapping = [], [], [], {}
    for (scenario, group_id), group in sorted(groups_lookup.items()):
        members = group["members"]; fault_ids = [str(row["fault_id"]) for row in members]
        covering = [fault for fault in fault_ids if set(fault_ids) <= coverage[(scenario, fault)]]
        representative = min(covering) if covering else ""; all_singleton = bool(covering)
        reuse_rows.append({"scenario": scenario, "group_id": group_id, "fault_count": len(fault_ids), "affected_flow_count": group["affected_flow_count"],
            "representative_exists": bool(representative), "representative_fault": representative, "member_profiles_covering_all_count": len(covering),
            "all_members_singleton_valid": all_singleton, "reduction_if_one_profile": len(fault_ids) - 1 if representative else 0,
            "validation_strength": "EXISTING_MEMBER_PROFILE_CROSS_FAULT_STATIC_VALID" if representative else "NO_EXISTING_MEMBER_COVER"})
        semantic = {records[(scenario, fault)]["components"]["SEMANTIC_FULL"] for fault in fault_ids}
        routes = {records[(scenario, fault)]["components"]["ROUTE_FORWARDING"] for fault in fault_ids}
        schedules = {records[(scenario, fault)]["components"]["SCHEDULE"] for fault in fault_ids}
        diversity_rows.append({"scenario": scenario, "group_id": group_id, "fault_count": len(fault_ids), "affected_flow_count": group["affected_flow_count"],
            "unique_semantic_profiles": len(semantic), "unique_route_profiles": len(routes), "unique_schedule_profiles": len(schedules),
            "semantic_all_equal": len(semantic) == 1, "route_all_equal": len(routes) == 1, "schedule_all_equal": len(schedules) == 1,
            "union_shared_profile_exists": False})
        union_valid = False; reason = "NO_MEMBER_PROFILE_COVERS_SINGLETON_GROUP"; checker_pass = False; absent = False
        if representative:
            if len(fault_ids) == 1:
                # The singleton union topology is exactly the qualified
                # diagonal target.  Avoid a duplicate conversion while still
                # recording the full-check implication honestly.
                absent = records[(scenario, representative)]["link_set"].isdisjoint(fault_ids)
                checker_pass = True
                union_valid = absent
                reason = "SINGLETON_DIAGONAL_REPLAY" if union_valid else "SINGLETON_ROUTE_USES_DISABLED_LINK"
            else:
                disabled = tuple(sorted(fault_ids)); healthy = {row["flow_id"]: row for row in p0[scenario]["logical_routes"]}
                union_affected = tuple(sorted({flow_id for member in members for flow_id in affected_ids(member["affected_flow_ids"])}))
                target = prepare_h2s_inputs(paths[scenario], temp / scenario / group_id, 100, 5, disabled_links=disabled, healthy_primary_routes=healthy,
                    affected_flow_ids=union_affected, route_scope="all-reroute")
                absent = not (set(disabled) & set(records[(scenario, representative)]["link_set"]))
                try:
                    raw, _ = load_raw_h2s(EXP19 / "raw_backend_output" / scenario / representative / "primary" / "logs" / "0_h2s_stdout.log")
                    _, report = replay_exact_raw(raw, target); checker_pass = bool(report["valid"]); union_valid = absent and checker_pass
                    reason = "UNION_SHARED_PROFILE_OBSERVED" if union_valid else "UNION_STATIC_CHECKER_FAILED"
                except (OSError, RedundancyAuditError, ValueError, KeyError) as error:
                    reason = f"UNION_REPLAY_ERROR:{error}"
        union_rows.append({"scenario": scenario, "group_id": group_id, "fault_count": len(fault_ids), "union_disabled_count": len(fault_ids),
            "representative_fault": representative, "union_static_valid": union_valid, "all_member_links_absent_from_profile": absent,
            "checker_pass": checker_pass, "failure_reason": reason})
        diversity_rows[-1]["union_shared_profile_exists"] = union_valid
        for fault in fault_ids: mapping[(scenario, fault)] = representative if union_valid else fault
    return reuse_rows, union_rows, diversity_rows, mapping


def make_storage_and_compute(records: dict[tuple[str, str], dict[str, Any]], group_summary: list[dict[str, Any]], union_rows: list[dict[str, Any]],
                             union_mapping: dict[tuple[str, str], str], coverage: dict[tuple[str, str], set[str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    storage, compute, semantic_index_maps, affected_maps, setcover_maps, setcover_rows = [], [], {}, {}, {}, []
    per_results = {(row["scenario"], row["fault_id"]): row for row in load_csv(EXP19 / "per_fault_results.csv")}
    groups_by_scenario = defaultdict(list)
    for row in group_summary: groups_by_scenario[row["scenario"]] = row
    for scenario in ORDER:
        values = sorted([record for (sid, _), record in records.items() if sid == scenario], key=lambda row: row["fault_id"])
        faults = [row["fault_id"] for row in values]
        by_semantic: dict[str, list[str]] = defaultdict(list)
        for value in values: by_semantic[value["semantic_hash"]].append(value["fault_id"])
        semantic_reps = {fault: min(by_semantic[records[(scenario, fault)]["semantic_hash"]]) for fault in faults}
        semantic_index_maps[scenario] = {fault: records[(scenario, fault)]["semantic_hash"] for fault in faults}
        representatives = sorted(set(semantic_reps.values()))
        reduced_coverage = {fault: coverage[(scenario, fault)] for fault in representatives}
        cover = exact_set_cover(reduced_coverage, faults, timeout_s=SCENARIO_BUDGET)
        selected = cover.get("selected_profile_fault_ids", []) if cover.get("exact") else []
        target_mapping = {target: min(source for source in selected if target in coverage[(scenario, source)]) for target in faults} if selected else {}
        setcover_maps[scenario] = target_mapping
        setcover_rows.append({"scenario": scenario, "fault_count": len(faults), "candidate_profile_count": len(representatives), "exact": bool(cover.get("exact")),
            "minimum_profile_count": cover.get("minimum_profile_count", ""), "reduction_count": len(faults) - int(cover.get("minimum_profile_count", len(faults))),
            "reduction_fraction": 1 - int(cover.get("minimum_profile_count", len(faults))) / len(faults), "selected_profile_fault_ids": ";".join(selected),
            "solve_ms": cover.get("solve_ms", 0), "solver": f"z3 {cover.get('z3_version', 'unavailable')}", "reason": cover.get("reason", "")})
        affected_maps[scenario] = {fault: union_mapping[(scenario, fault)] for fault in faults}
        schemes = {
            "EXACT_HASH_DEDUP": (semantic_reps, semantic_index_maps[scenario], "fault_to_semantic_hash", "semantic_hash"),
            "AFFECTED_GROUP_REUSE": (affected_maps[scenario], affected_maps[scenario], "fault_to_representative_profile", "representative_profile_fault"),
            "EX_POST_SET_COVER": (target_mapping or {fault: fault for fault in faults}, target_mapping or {fault: fault for fault in faults}, "fault_to_representative_profile", "representative_profile_fault"),
        }
        for scheme, (payload_mapping, index_mapping, index_field, value_field) in schemes.items():
            selected_ids = sorted(set(payload_mapping.values())); selected_records = [records[(scenario, fault)] for fault in selected_ids]
            index_payload = {"schema_version": 1, "analysis_only": True, "scheme": scheme, "scenario": scenario,
                             index_field: [{"fault_id": fault, value_field: index_mapping[fault]} for fault in faults]}
            index_bytes = len(canonical_json_bytes(index_payload))
            raw = sum(row["semantic_payload_bytes"] for row in selected_records); gz = sum(row["semantic_gzip_bytes"] for row in selected_records)
            baseline_raw = sum(row["semantic_payload_bytes"] for row in values); baseline_gz = sum(row["semantic_gzip_bytes"] for row in values)
            storage.append({"scenario": scenario, "scheme": scheme, "baseline_profile_count": len(values), "stored_payload_count": len(selected_ids),
                "baseline_payload_raw_bytes": baseline_raw, "baseline_payload_gzip_bytes": baseline_gz,
                "mapping_entries": len(payload_mapping), "payload_raw_bytes": raw, "payload_gzip_bytes": gz, "index_bytes": index_bytes,
                "total_raw_plus_index": raw + index_bytes, "gzip_payload_plus_index": gz + index_bytes,
                "raw_reduction_fraction": 1 - (raw + index_bytes) / baseline_raw, "gzip_reduction_fraction": 1 - (gz + index_bytes) / baseline_gz})
        group = groups_by_scenario[scenario]; union_count = len(set(affected_maps[scenario].values()))
        baseline_ms = sum(float(per_results[(scenario, fault)]["total_backend_ms"]) for fault in faults)
        chosen_ms = sum(float(per_results[(scenario, fault)]["total_backend_ms"]) for fault in selected) if selected else 0.0
        compute.append({"scenario": scenario, "baseline_fault_solves": len(faults), "affected_set_groups": group["affected_set_group_count"],
            "affected_set_candidate_reduction": group["affected_set_candidate_solve_reduction"], "union_validated_profile_count": union_count,
            "union_validated_reduction": 1 - union_count / len(faults), "ex_post_set_cover_count": len(selected) if selected else "",
            "ex_post_set_cover_reduction": 1 - len(selected) / len(faults) if selected else "", "baseline_serial_ms": baseline_ms,
            "selected_representative_historical_ms": chosen_ms, "selected_representative_runtime_label": "EX_POST_REPRESENTATIVE_RUNTIME_SUM_DIAGNOSTIC_ONLY"})
    return storage, compute, semantic_index_maps, affected_maps, setcover_maps, setcover_rows


def verdict_for(*, exact: bool, raw_ok: bool, diagonal_ok: bool, integrity_ok: bool, union_rows: list[dict[str, Any]], setcover: list[dict[str, Any]], semantic_summary: list[dict[str, Any]]) -> str:
    if not integrity_ok: return "INPUT_INTEGRITY_FAILED"
    if not raw_ok: return "RAW_REPLAY_ARTIFACT_INCOMPLETE"
    if not diagonal_ok: return "CROSS_FAULT_REPLAY_NOT_QUALIFIED"
    if not exact: return "SET_COVER_EXACT_UNAVAILABLE"
    if any(row["union_static_valid"] for row in union_rows) or any(int(row["minimum_profile_count"]) < int(row["fault_count"]) for row in setcover): return "PF_REDUNDANCY_OPPORTUNITY_ESTABLISHED"
    if any(int(row["duplicate_profile_count"]) for row in semantic_summary): return "PF_EXACT_SEMANTIC_DEDUP_ONLY"
    return "PF_REDUNDANCY_LIMITED"


def summary_markdown(verdict: str, affected: list[dict[str, Any]], semantic: list[dict[str, Any]], components: list[dict[str, Any]],
                     edges: list[dict[str, Any]], reuse: list[dict[str, Any]], union: list[dict[str, Any]], setcover: list[dict[str, Any]],
                     storage: list[dict[str, Any]], compute: list[dict[str, Any]]) -> str:
    by_scenario = {row["scenario"]: row for row in affected}
    semantic_by_scenario = {row["scenario"]: row for row in semantic}
    cover_by_scenario = {row["scenario"]: row for row in setcover}
    component = {(row["scenario"], row["component"]): row for row in components}
    compute_by_scenario = {row["scenario"]: row for row in compute}
    storage_by_scenario = {(row["scenario"], row["scheme"]): row for row in storage}
    edge_coverage = Counter(row["source_profile_fault"] for row in edges)
    average_coverage = len(edges) / 1099
    max_coverage = max(edge_coverage.values(), default=0)
    full_group_reuse = sum(bool(row["representative_exists"]) and row["fault_count"] > 1 for row in reuse)
    union_multi = [row for row in union if row["fault_count"] > 1 and row["union_static_valid"]]
    union_faults = sum(row["fault_count"] for row in union_multi)
    scheme_totals = {}
    for scheme in ("EXACT_HASH_DEDUP", "AFFECTED_GROUP_REUSE", "EX_POST_SET_COVER"):
        rows = [row for row in storage if row["scheme"] == scheme]
        scheme_totals[scheme] = {"payloads": sum(row["stored_payload_count"] for row in rows),
            "raw": sum(row["payload_raw_bytes"] for row in rows), "gzip": sum(row["payload_gzip_bytes"] for row in rows),
            "index": sum(row["index_bytes"] for row in rows), "total": sum(row["total_raw_plus_index"] for row in rows),
            "gzip_total": sum(row["gzip_payload_plus_index"] for row in rows)}
    baseline_raw = sum(storage_by_scenario[(scenario, "EXACT_HASH_DEDUP")]["baseline_payload_raw_bytes"] for scenario in ORDER)
    baseline_gzip = sum(storage_by_scenario[(scenario, "EXACT_HASH_DEDUP")]["baseline_payload_gzip_bytes"] for scenario in ORDER)
    route_delta = sum(component[(scenario, "ROUTE_FORWARDING")]["duplicate_count"] for scenario in ORDER)
    schedule_delta = sum(component[(scenario, "SCHEDULE")]["duplicate_count"] for scenario in ORDER)
    lines = ["# exp20 PF redundancy and shared-profile opportunity audit", "", f"Formal verdict: `{verdict}`.", "",
             "## Direct answers", "",
             "1. PF was not rerun: this is a read-only, post-hoc audit; no H2S/CELF synthesis, upstream scheduler, OMNeT++, or INET ran.",
             "2. Input is the complete exp19 census: 1,099 canonical `SUCCESS_H2S` profiles with recomputed byte and semantic-hash parity.",
             "3–7. Per-scenario structural census (the reduction is only a hypothetical candidate grouping opportunity):"]
    for scenario in ORDER:
        row = by_scenario[scenario]
        lines.append(f"   - {scenario}: faults={row['fault_count']}; distinct affected sets={row['affected_set_group_count']}; "
                     f"faults in multi-member groups={row['faults_in_multi_member_groups']}; largest group={row['largest_group']}; "
                     f"candidate reduction={row['affected_set_candidate_solve_reduction']:.2%}.")
    lines.extend(["8–12. Exact profile redundancy is shown per scenario below; semantic equality includes forwarding, routes, releases, GCL/windows. "
                  f"Across the census, route-forwarding exact duplicates={route_delta}; schedule exact duplicates={schedule_delta}. "
                  "These component counts describe equality, not fuzzy similarity or a deployment contract."])
    for scenario in ORDER:
        semantic_row = semantic_by_scenario[scenario]
        lines.append(f"   - {scenario}: semantic unique={semantic_row['unique_semantic_profiles']}/{semantic_row['fault_profiles']}; "
                     f"semantic duplicates={semantic_row['duplicate_profile_count']}; route-only unique={component[(scenario, 'LOGICAL_ROUTE_ONLY')]['unique_hashes']}; "
                     f"route-forwarding unique={component[(scenario, 'ROUTE_FORWARDING')]['unique_hashes']}; schedule-only unique={component[(scenario, 'SCHEDULE')]['unique_hashes']}.")
    lines.extend([f"13. S1 exact semantic dedup is analysis-only: baseline semantic bytes raw/gzip={baseline_raw}/{baseline_gzip}; "
                  f"S1 raw+index/gzip+index={scheme_totals['EXACT_HASH_DEDUP']['total']}/{scheme_totals['EXACT_HASH_DEDUP']['gzip_total']}.",
                  "14. Runtime deployment contract remains `NOT_ESTABLISHED`; these are semantic-representation storage opportunities, not device storage measurements.",
                  "15. All 1,099 diagonal raw-slot replays were qualified before cross-fault analysis.",
                  f"16–18. Valid same-scenario existing-profile→fault relations={len(edges)}; average source coverage={average_coverage:.3f}; maximum source coverage={max_coverage}.",
                  f"19–21. Multi-fault affected-set groups with a member profile covering all singleton members={full_group_reuse}; "
                  f"union-disabled static-valid multi-groups={len(union_multi)}, covering {union_faults} faults.",
                  "22–23. Conservative validated affected-group profile counts/reductions are in `compute_opportunity.csv`; they are ex-post evidence, not measured shared-synthesis solve savings.",
                  "24–25. The exact per-scenario ex-post existing-profile cover is an oracle computed from all observed outcomes, not an algorithm that could choose K profiles in advance:"])
    for scenario in ORDER:
        cover = cover_by_scenario[scenario]
        lines.append(f"   - {scenario}: minimum={cover['minimum_profile_count'] or 'unavailable'} of {cover['fault_count']}; exact={cover['exact']}; solver={cover['solver']}.")
    lines.extend([f"26–29. Stored semantic payload counts S1/S2/S3 are {scheme_totals['EXACT_HASH_DEDUP']['payloads']}/"
                  f"{scheme_totals['AFFECTED_GROUP_REUSE']['payloads']}/{scheme_totals['EX_POST_SET_COVER']['payloads']}; "
                  f"raw+index bytes are {scheme_totals['EXACT_HASH_DEDUP']['total']}/{scheme_totals['AFFECTED_GROUP_REUSE']['total']}/{scheme_totals['EX_POST_SET_COVER']['total']}; "
                  f"gzip-payload+index bytes are {scheme_totals['EXACT_HASH_DEDUP']['gzip_total']}/{scheme_totals['AFFECTED_GROUP_REUSE']['gzip_total']}/{scheme_totals['EX_POST_SET_COVER']['gzip_total']}.",
                  "30–31. `affected_group_profile_diversity.csv` distinguishes equal affected sets with differing semantic, route, or schedule components; equal affected flow IDs do not imply an equal PF profile.",
                  "32–33. Topology and M→L comparisons are intentionally reported as exact per-scenario counts above, rather than a cross-scenario merge: scenario namespaces remain isolated.",
                  "34. No: equal affected sets cannot prove one profile per group.",
                  "35–36. A stronger claim needs a fixed existing profile to pass the full static checker on the group union-disabled graph. Then E\\C ⊆ E\\{e} proves validity for every singleton member; the converse is not assumed.",
                  "37–39. Exp20 did not reduce PF computation or modify ProfileStore. It supplies an optimization-opportunity census and future-experiment design evidence."])
    if union_multi:
        recommendation = "EXP21_UNION_FAULT_SHARED_SYNTHESIS is worth an explicit human decision because union-valid groups were observed; exp20 itself did not run that synthesis."
    elif scheme_totals["EXACT_HASH_DEDUP"]["payloads"] < 1099:
        recommendation = "Consider a semantic-profile-store dedup experiment; runtime activation remains unestablished."
    else:
        recommendation = "Stop grouping optimization and prioritize runtime activation validation or thesis consolidation."
    lines.extend([f"40. Next-stage recommendation: {recommendation}", "",
                  "All cross-fault claims are same-scenario project-independent static-checker replay claims. A union-disabled static-valid profile establishes existing-profile reuse, not that a future union-fault heuristic will discover it.", ""])
    return "\n".join(lines)


def quick_union_probe(temp: Path, group: dict[str, Any], records: dict[tuple[str, str], dict[str, Any]],
                      paths: dict[str, Path], p0: dict[str, Any]) -> dict[str, Any]:
    """Run exactly one required multi-fault union replay for ``--quick``.

    This is deliberately not used for a formal reuse claim: the quick mode
    does not build the group's full N×N singleton-coverage relation.
    """
    scenario = group["scenario"]; members = group["members"]
    faults = tuple(sorted(str(member["fault_id"]) for member in members))
    representative = faults[0]
    healthy = {row["flow_id"]: row for row in p0[scenario]["logical_routes"]}
    union_affected = tuple(sorted({flow_id for member in members for flow_id in affected_ids(member["affected_flow_ids"])}))
    target = prepare_h2s_inputs(paths[scenario], temp / scenario / group["group_id"], 100, 5,
        disabled_links=faults, healthy_primary_routes=healthy, affected_flow_ids=union_affected, route_scope="all-reroute")
    absent = records[(scenario, representative)]["link_set"].isdisjoint(faults)
    try:
        raw, _ = load_raw_h2s(EXP19 / "raw_backend_output" / scenario / representative / "primary" / "logs" / "0_h2s_stdout.log")
        _, report = replay_exact_raw(raw, target)
        valid = absent and bool(report["valid"])
        reason = "QUICK_UNION_SHARED_PROFILE_OBSERVED" if valid else "QUICK_UNION_STATIC_CHECKER_FAILED"
    except (OSError, RedundancyAuditError, ValueError, KeyError) as error:
        valid = False; absent = False; report = {"valid": False}; reason = f"QUICK_UNION_REPLAY_ERROR:{error}"
    return {"scenario": scenario, "group_id": group["group_id"], "fault_count": len(faults), "union_disabled_count": len(faults),
            "representative_fault": representative, "union_static_valid": valid, "all_member_links_absent_from_profile": absent,
            "checker_pass": bool(report["valid"]), "failure_reason": reason,
            "qualification_scope": "QUICK_SINGLE_MEMBER_UNION_PROBE_NOT_FORMAL_GROUP_REUSE"}


def run_quick(root: Path, implementation: str, pre_trees: dict[str, str], source: dict[str, Any],
              candidates: dict[str, list[dict[str, Any]]], p0: dict[str, Any], paths: dict[str, Path]) -> int:
    """Fast, read-only qualification: all integrity plus M_RING diagonal replay."""
    root.mkdir(parents=True); (root / "logs").mkdir()
    integrity_rows, records = integrity_audit(candidates)
    groups, members, group_summary, group_lookup = affected_groups(candidates)
    semantic_groups, canonical_groups, component_rows, semantic_summary = component_outputs(records)
    write_json(root / "exp19_source_manifest.json", source); write_csv(root / "input_integrity.csv", integrity_rows)
    write_csv(root / "affected_set_groups.csv", groups); write_csv(root / "affected_set_group_members.csv", members)
    write_csv(root / "affected_set_summary.csv", group_summary); write_csv(root / "semantic_profile_groups.csv", semantic_groups)
    write_csv(root / "semantic_profile_summary.csv", semantic_summary); write_csv(root / "canonical_hash_groups.csv", canonical_groups)
    write_csv(root / "component_redundancy.csv", component_rows)
    quick_candidates = {scenario: candidates[scenario] if scenario == "M_RING" else [] for scenario in ORDER}
    with tempfile.TemporaryDirectory(prefix="exp20-quick-replay-") as temporary:
        temp = Path(temporary); prepared, bases = prepare_targets(temp / "targets", quick_candidates, p0, paths)
        raw_rows, diagonal_rows, _ = raw_and_diagonal(temp / "diagonal", quick_candidates, records, prepared, bases)
        multi_groups = [group for _, group in sorted(group_lookup.items()) if group["group_size"] > 1]
        union_rows = [quick_union_probe(temp / "union", multi_groups[0], records, paths, p0)] if multi_groups else []
    raw_ok = len(raw_rows) == len(candidates["M_RING"]) and all(row["raw_parse_pass"] and row["raw_slot_count"] > 0 for row in raw_rows)
    diagonal_ok = len(diagonal_rows) == len(candidates["M_RING"]) and all(row["diagonal_pass"] for row in diagonal_rows)
    write_csv(root / "raw_replay_artifact_audit.csv", raw_rows); write_csv(root / "diagonal_replay_validation.csv", diagonal_rows)
    write_csv(root / "affected_group_union_validation.csv", union_rows,
        ["scenario", "group_id", "fault_count", "union_disabled_count", "representative_fault", "union_static_valid", "all_member_links_absent_from_profile", "checker_pass", "failure_reason", "qualification_scope"])
    status = "QUICK_STATIC_VALIDATION_PASS" if raw_ok and diagonal_ok else "QUICK_STATIC_VALIDATION_FAILED"
    if not union_rows:
        status += "_NO_MULTI_FAULT_AFFECTED_SET_GROUP"
    write_json(root / "audit_verdict.json", {"formal_verdict": status, "mode": "quick", "input_integrity_complete": True,
        "m_ring_raw_replay_complete": raw_ok, "m_ring_diagonal_replay_complete": diagonal_ok,
        "multi_group_union_probe_count": len(union_rows), "pf_synthesis_invocations": 0, "upstream_scheduler_invocations": 0,
        "omnet_invocations": 0, "inet_invocations": 0})
    write_atomic_bytes(root / "summary.md", ("# exp20 quick validation\n\n"
        f"All 1,099 input profiles passed integrity; M_RING raw/diagonal replay qualification: `{status}`. "
        "This quick output is not a formal cross-fault reuse census. No PF synthesis, CELF, scheduler, OMNeT++, or INET was run.\n").encode("utf-8"))
    write_json(root / "environment.json", {"python": sys.version.split()[0], "platform": platform.platform(), "mode": "quick",
        "parent_commit": git_value("rev-parse", "HEAD"), "implementation_commit": implementation, "read_only_exp19": True,
        "pf_synthesis_invocations": 0, "upstream_scheduler_invocations": 0, "omnet_invocations": 0, "inet_invocations": 0})
    post_trees = frozen_result_trees()
    if pre_trees != post_trees:
        raise AuditStop("FROZEN_HISTORY_CHANGED")
    artifacts = artifact_sha(root)
    write_json(root / "analysis_manifest.json", {"schema_version": 1, "experiment": "exp20_pf_redundancy_opportunity_audit",
        "mode": "quick", "implementation_commit": implementation, "exp19_commit": source["exp19_commit"],
        "raw_replay_artifact_coverage": len(raw_rows), "diagonal_replay_status": status, "artifact_sha256": artifacts,
        "campaign_sha256": canonical_sha(artifacts)})
    return 0 if raw_ok and diagonal_ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--quick", action="store_true"); parser.add_argument("--implementation-commit", default="")
    args = parser.parse_args(); mode = "quick" if args.quick else "full"; root = OUT / "_quick_validation" if args.quick else OUT
    if root.exists(): raise SystemExit(f"refusing to overwrite existing output: {root}")
    implementation = args.implementation_commit or git_value("rev-parse", "HEAD")
    pre_trees = frozen_result_trees(); verdict, candidates, p0, paths = load_exp19(); source = source_manifest(pre_trees)
    if args.quick:
        return run_quick(root, implementation, pre_trees, source, candidates, p0, paths)
    root.mkdir(parents=True); (root / "logs").mkdir()
    integrity_rows, records = integrity_audit(candidates)
    groups, members, group_summary, group_lookup = affected_groups(candidates)
    semantic_groups, canonical_groups, component_rows, semantic_summary = component_outputs(records)
    write_json(root / "exp19_source_manifest.json", source); write_csv(root / "input_integrity.csv", integrity_rows)
    write_csv(root / "affected_set_groups.csv", groups); write_csv(root / "affected_set_group_members.csv", members); write_csv(root / "affected_set_summary.csv", group_summary)
    write_csv(root / "semantic_profile_groups.csv", semantic_groups); write_csv(root / "semantic_profile_summary.csv", semantic_summary); write_csv(root / "canonical_hash_groups.csv", canonical_groups)
    write_csv(root / "component_redundancy.csv", component_rows)
    with tempfile.TemporaryDirectory(prefix="exp20-replay-") as temporary:
        temp = Path(temporary); prepared, bases = prepare_targets(temp / "targets", candidates, p0, paths)
        raw_rows, diagonal_rows, arcs = raw_and_diagonal(temp / "diagonal", candidates, records, prepared, bases)
        raw_ok = len(raw_rows) == 1099 and all(row["raw_parse_pass"] and row["raw_slot_count"] > 0 for row in raw_rows)
        diagonal_ok = len(diagonal_rows) == 1099 and all(row["diagonal_pass"] for row in diagonal_rows)
        write_csv(root / "raw_replay_artifact_audit.csv", raw_rows); write_csv(root / "diagonal_replay_validation.csv", diagonal_rows)
        if not raw_ok or not diagonal_ok:
            formal = "RAW_REPLAY_ARTIFACT_INCOMPLETE" if not raw_ok else "CROSS_FAULT_REPLAY_NOT_QUALIFIED"
            write_json(root / "audit_verdict.json", {"formal_verdict": formal, "raw_replay_complete": raw_ok, "diagonal_replay_complete": diagonal_ok})
            return 2
        edges, failure_summary, coverage = cross_replay(candidates, records, prepared, arcs)
        reuse_rows, union_rows, diversity_rows, union_mapping = union_and_group_outputs(temp / "union", group_lookup, records, coverage, paths, p0)
    coverage_rows = []
    for scenario in ORDER:
        count = len(candidates[scenario])
        affected_by_fault = {str(candidate["fault_id"]): affected_set_sha(candidate["affected_flow_ids"]) for candidate in candidates[scenario]}
        semantic_size = Counter(record["semantic_hash"] for (sid, _), record in records.items() if sid == scenario)
        for candidate in candidates[scenario]:
            fault = str(candidate["fault_id"]); covers = coverage[(scenario, fault)]
            coverage_rows.append({"scenario": scenario, "source_fault": fault, "covers_fault_count": len(covers), "coverage_fraction": len(covers) / count,
                "covers_same_affected_set_count": sum(affected_by_fault[fault] == affected_by_fault[target] for target in covers),
                "covers_other_affected_set_count": sum(affected_by_fault[fault] != affected_by_fault[target] for target in covers),
                "semantic_hash_group_size": semantic_size[records[(scenario, fault)]["semantic_hash"]]})
    if any(row["covers_fault_count"] < 1 for row in coverage_rows): raise AuditStop("STATIC_REUSE_CONTRADICTS_BASELINE:SELF_COVERAGE")
    storage_rows, compute_rows, semantic_index_maps, affected_maps, setcover_maps, setcover_rows = make_storage_and_compute(records, group_summary, union_rows, union_mapping, coverage)
    write_gzip_csv(root / "cross_fault_reuse_edges.csv.gz", edges, ["scenario", "source_profile_fault", "target_fault", "source_semantic_hash", "same_affected_set", "same_semantic_hash", "target_covered"])
    write_csv(root / "cross_fault_reuse_failure_summary.csv", failure_summary); write_csv(root / "profile_reuse_coverage.csv", coverage_rows)
    write_csv(root / "affected_group_reuse_validation.csv", reuse_rows); write_csv(root / "affected_group_union_validation.csv", union_rows); write_csv(root / "affected_group_profile_diversity.csv", diversity_rows)
    write_csv(root / "existing_profile_set_cover.csv", setcover_rows); write_csv(root / "storage_opportunity.csv", storage_rows); write_csv(root / "compute_opportunity.csv", compute_rows)
    write_json(root / "semantic_dedup_index_analysis.json", {"schema_version": 1, "analysis_only": True, "runtime_contract": "NOT_ESTABLISHED", "fault_to_semantic_hash": semantic_index_maps})
    write_json(root / "reuse_mapping_affected_groups.json", {"schema_version": 1, "analysis_only": True, "fault_to_representative_profile": affected_maps})
    write_json(root / "reuse_mapping_set_cover.json", {"schema_version": 1, "analysis_only": True, "fault_to_representative_profile": setcover_maps})
    exact = all(row["exact"] for row in setcover_rows); formal = verdict_for(exact=exact, raw_ok=True, diagonal_ok=True, integrity_ok=True, union_rows=union_rows, setcover=setcover_rows, semantic_summary=semantic_summary)
    write_json(root / "audit_verdict.json", {"formal_verdict": formal, "mode": mode, "exp19_profile_count": 1099, "raw_replay_complete": True,
        "diagonal_replay_complete": True, "cross_fault_scope": "SAME_SCENARIO_ONLY", "pf_synthesis_invocations": 0,
        "upstream_scheduler_invocations": 0, "omnet_invocations": 0, "inet_invocations": 0, "runtime_contract": "NOT_ESTABLISHED"})
    write_atomic_bytes(root / "summary.md", summary_markdown(formal, group_summary, semantic_summary, component_rows, edges, reuse_rows, union_rows, setcover_rows, storage_rows, compute_rows).encode("utf-8"))
    write_json(root / "environment.json", {"python": sys.version.split()[0], "platform": platform.platform(), "mode": mode,
        "parent_commit": git_value("rev-parse", "HEAD"), "implementation_commit": implementation, "read_only_exp19": True,
        "pf_synthesis_invocations": 0, "upstream_scheduler_invocations": 0, "omnet_invocations": 0, "inet_invocations": 0})
    post_trees = frozen_result_trees()
    if pre_trees != post_trees: raise AuditStop("FROZEN_HISTORY_CHANGED")
    artifacts = artifact_sha(root)
    write_json(root / "analysis_manifest.json", {"schema_version": 1, "experiment": "exp20_pf_redundancy_opportunity_audit", "parent_commit": git_value("rev-parse", "HEAD"),
        "implementation_commit": implementation, "results_commit": "", "exp19_commit": source["exp19_commit"], "exp19_artifact_sha256": source["exp19_artifact_sha256"],
        "canonical_profile_hashes": {f"{scenario}/{fault}": records[(scenario, fault)]["canonical_profile_sha"] for scenario in ORDER for fault in sorted(fault for sid, fault in records if sid == scenario)},
        "scenario_sha256": {scenario: sha256_file(paths[scenario]) for scenario in ORDER}, "raw_replay_artifact_coverage": len(raw_rows),
        "diagonal_replay_status": "PASS", "checker_implementation_sha256": sha256_file(ROOT / "tools" / "h2s_jrs_backend.py"),
        "affected_set_grouping_algorithm": "sha256(canonical_json(sorted_unique_affected_flow_ids))", "semantic_projection_fields": list(SEMANTIC_PROFILE_FIELDS),
        "set_cover_solver": setcover_rows[0]["solver"] if setcover_rows else "unavailable", "formal_verdict": formal, "artifact_sha256": artifacts,
        "campaign_sha256": canonical_sha(artifacts)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
