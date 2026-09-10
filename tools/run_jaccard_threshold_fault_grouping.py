"""exp22 preregistered Jaccard-threshold grouping sensitivity experiment."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import platform
import statistics
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.fixed_release_collision_audit import ROOT, sha256_file, tree_sha256
from tools.h2s_group_recovery_backend import H2sGroupRecoveryBackend
from tools.h2s_pf_backend import H2sPfBackend, semantic_profile_hash
from tools.jaccard_fault_grouping import (
    ALGORITHM_VERSION, THRESHOLDS, Threshold, affected_union, canonical_affected,
    jaccard_counts, jaccard_decimal, overlap_decimal, run_jaccard_grouping_search,
)
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.realistic_full_density_pf_cost import (
    ORDER, benchmark_config, candidate_catalog, gzip_bytes, load_benchmark,
    profile_storage, recover_p0_baseline, write_atomic_bytes, write_atomic_json,
)
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_schedulability_pruned_fault_grouping import (
    EXECUTABLE, EXP19, EXP20, OUT as EXP21, SCENARIO_BUDGET_S, SUCCESS, TIMEOUT_S,
    backend_result_row, canonical_sha, csv_bytes, singleton_replay, write_csv, write_gzip_csv,
)
from tools.schedulability_necessary_conditions import evaluate_necessary_conditions

OUT = ROOT / "results/jaccard_threshold_fault_grouping"
PARENT_COMMIT = "9962fef15eb3261e9cf958162da874f232593379"
FORMAL_VERDICTS = (
    "JACCARD_THRESHOLD_SWEEP_COMPLETE", "JACCARD_THRESHOLD_SWEEP_PARTIAL_BUDGET",
    "TAU1_EXP21_PARITY_FAILED", "INPUT_PARITY_FAILED", "GROUP_SINGLETON_REPLAY_CONTRADICTION",
    "SINGLETON_BASELINE_PARITY_FAILED", "THRESHOLD_CORRECTNESS_FAILED", "REPEATABILITY_FAILED", "INCONCLUSIVE",
)
ROOT_OUTPUTS = (
    "environment.json", "source_manifest.json", "algorithm_config.json", "input_parity.csv",
    "initial_pair_similarity.csv.gz", "initial_threshold_landscape.csv", "similarity_distribution.csv",
    "threshold_summary.csv", "threshold_comparison.csv", "threshold_funnel.csv",
    "necessary_condition_effectiveness.csv", "similarity_backend_outcome.csv", "jaccard_success_analysis.csv",
    "group_size_distribution.csv", "accepted_merge_history.csv", "failed_group_synthesis.csv",
    "failure_certificates.json", "filter_timing_summary.csv", "compute_cost_comparison.csv",
    "storage_comparison.csv", "pareto_frontier.csv", "repeatability.csv", "backend_repeatability.csv",
    "posthoc_baseline_comparison.csv", "experiment_verdict.json", "summary.md", "analysis_manifest.json", "thresholds", "logs",
)
CANDIDATE_FIELDS = (
    "scenario", "threshold_id", "threshold_num", "threshold_den", "iteration", "left_group_id", "right_group_id",
    "left_group_size", "right_group_size", "candidate_group_id", "fault_ids", "union_group_size",
    "left_affected_count", "right_affected_count", "union_affected_count", "intersection_count", "union_count",
    "jaccard_num", "jaccard_den", "jaccard_decimal", "overlap_coefficient", "threshold_pass", "decision",
    "first_failure", "filter_pass", "filter_total_ms", "cache_status", "rank", "synthesis_cache_status",
    "window_slack_ticks", "cut_slack_ticks", "deadline_slack_ticks", "filter_f1_connectivity_ms",
    "filter_f2_minimum_delay_ms", "filter_f3_checked_cut_capacity_ms", "filter_f4_time_window_ms",
)


class Exp22Error(RuntimeError): pass


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def percentile(values: Iterable[float], fraction: float) -> float:
    data = sorted(float(value) for value in values)
    return data[max(0, int((len(data) * fraction + .999999999) // 1) - 1)] if data else 0.0


class GzipCandidateWriter:
    """Streaming deterministic gzip CSV sink; avoids retaining every active pair."""
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True); self.path, self.tmp = path, path.with_name(f".{path.name}.tmp")
        self.raw = self.tmp.open("wb"); self.gz = gzip.GzipFile(filename=path.name.removesuffix(".gz"), mode="wb", fileobj=self.raw, mtime=0)
        self.text = io.TextIOWrapper(self.gz, encoding="utf-8", newline="")
        self.writer = csv.DictWriter(self.text, CANDIDATE_FIELDS, extrasaction="ignore", lineterminator="\n"); self.writer.writeheader(); self.count = 0

    def write(self, row: dict[str, Any]) -> None:
        self.writer.writerow(row); self.count += 1

    def close(self) -> None:
        self.text.flush(); self.text.detach(); self.gz.close(); self.raw.flush(); os.fsync(self.raw.fileno()); self.raw.close(); os.replace(self.tmp, self.path)


def frozen_manifest() -> dict[str, str]:
    paths = {
        "exp18j": ROOT / "results/source_egress_aware_release_assignment",
        "exp19": EXP19, "exp20": EXP20, "exp21": EXP21,
    }
    return {name: tree_sha256(path) for name, path in paths.items()}


def recover_inputs(work: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Path], dict[str, Any], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    manifest, scenarios, paths = load_benchmark(); catalog = json.loads((EXP19 / "candidate_faults.json").read_text(encoding="utf-8"))
    p0, candidates, parity = {}, {}, []
    for scenario_id in ORDER:
        baseline = recover_p0_baseline(scenario_id, paths[scenario_id], scenarios[scenario_id], work / scenario_id)
        _, recomputed, digest = candidate_catalog(scenario_id, scenarios[scenario_id], baseline["logical_routes"])
        recorded = catalog[scenario_id]
        passed = digest == recorded["candidate_set_sha256"] and recomputed == recorded["candidates"]
        parity.append({"scenario": scenario_id, "scenario_sha256": sha256_file(paths[scenario_id]), "p0_route_hash": baseline["route_hash"],
                       "candidate_set_sha256": digest, "candidate_count": len(recomputed), "recorded_candidate_count": len(recorded["candidates"]),
                       "affected_set_exact_parity": passed})
        if not passed: raise Exp22Error(f"INPUT_PARITY_FAILED:{scenario_id}")
        p0[scenario_id], candidates[scenario_id] = baseline, recomputed
    return manifest, scenarios, paths, p0, candidates, parity


def threshold_contract(implementation: str, threshold: Threshold, scenario_id: str, source_path: Path, p0: dict[str, Any], candidates: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    return {"algorithm_version": ALGORITHM_VERSION, "implementation_commit": implementation,
            "threshold_id": threshold.identifier, "threshold_num": threshold.numerator, "threshold_den": threshold.denominator,
            "positive_overlap": threshold.positive_overlap, "scenario": scenario_id, "scenario_sha256": sha256_file(source_path),
            "candidate_fault_sha256": canonical_sha(candidates), "p0_route_sha256": p0["route_hash"],
            "backend_config_sha256": canonical_sha(config),
            "f1_f4_implementation_sha256": sha256_file(ROOT / "tools/schedulability_necessary_conditions.py"),
            "similarity_implementation_sha256": sha256_file(ROOT / "tools/jaccard_fault_grouping.py"),
            "search_implementation_sha256": sha256_file(ROOT / "tools/jaccard_fault_grouping.py")}


def initial_similarity(candidates: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pairs, landscape, distribution = [], [], []
    for scenario_id in ORDER:
        rows = candidates[scenario_id]; values: list[tuple[int, int]] = []
        per_threshold = {threshold.identifier: 0 for threshold in THRESHOLDS}; positive = 0
        for index, left in enumerate(rows):
            left_affected = canonical_affected(str(left["affected_flow_ids"]).split(";"))
            for right in rows[index + 1:]:
                right_affected = canonical_affected(str(right["affected_flow_ids"]).split(";")); inter, union = jaccard_counts(left_affected, right_affected)
                values.append((inter, union)); positive += int(inter > 0)
                for threshold in THRESHOLDS: per_threshold[threshold.identifier] += int(threshold.eligible(inter, union))
                pairs.append({"scenario": scenario_id, "fault_a": left["fault_id"], "fault_b": right["fault_id"],
                              "affected_a": ";".join(left_affected), "affected_b": ";".join(right_affected),
                              "intersection_count": inter, "union_count": union, "jaccard_num": inter, "jaccard_den": union,
                              "jaccard_decimal": jaccard_decimal(inter, union)})
        total = len(values)
        for threshold in THRESHOLDS:
            landscape.append({"scenario": scenario_id, "threshold_id": threshold.identifier, "threshold_num": threshold.numerator,
                              "threshold_den": threshold.denominator, "positive_overlap": threshold.positive_overlap,
                              "singleton_pair_count": total, "positive_overlap_pair_count": positive,
                              "eligible_pair_count": per_threshold[threshold.identifier],
                              "eligible_fraction": per_threshold[threshold.identifier] / total if total else 0})
        ratios = sorted(inter / union for inter, union in values)
        distribution.append({"scenario": scenario_id, "singleton_pair_count": total,
                             "unique_jaccard_values": len(set(values)), "p50": percentile(ratios, .5), "p75": percentile(ratios, .75),
                             "p90": percentile(ratios, .9), "p95": percentile(ratios, .95),
                             "max_below_1": max((inter / union for inter, union in values if inter < union), default=0),
                             "fraction_zero": sum(inter == 0 for inter, _ in values) / total if total else 0,
                             "fraction_positive": positive / total if total else 0,
                             "fraction_one": sum(inter == union for inter, union in values) / total if total else 0})
    return pairs, landscape, distribution


def _profile_path(root: Path, scenario: str, group: str) -> Path:
    return root / "profiles" / scenario / f"{group}.json"


def _write_profile(root: Path, scenario: str, group: str, profile: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    path = _profile_path(root, scenario, group); write_atomic_json(path, profile)
    write_atomic_bytes(path.with_suffix(".json.gz"), gzip_bytes(canonical_json_bytes(profile)))
    return str(path.relative_to(root)), profile_storage(profile)


def _profile_partition_sha(groups: Iterable[dict[str, Any]]) -> str:
    return canonical_sha(sorted(({"scenario": row["scenario"], "group_id": row["group_id"], "fault_ids": sorted(row["fault_ids"])} for row in groups),
                               key=lambda row: (row["scenario"], row["group_id"])))


def storage_totals(root: Path, *, reference: str, threshold_id: str = "") -> dict[str, Any]:
    """Measure actual profile and fault-map artifacts without inference."""
    profiles = root / "profiles"
    json_paths = sorted(profiles.rglob("*.json")) if profiles.exists() else []
    gzip_paths = sorted(profiles.rglob("*.json.gz")) if profiles.exists() else []
    mapping = root / "fault_to_group_profile.json"
    return {"reference": reference, "threshold_id": threshold_id, "profile_count": len(json_paths),
            "canonical_json_bytes": sum(path.stat().st_size for path in json_paths),
            "canonical_gzip_bytes": sum(path.stat().st_size for path in gzip_paths),
            "fault_to_profile_map_bytes": mapping.stat().st_size if mapping.exists() else 0}


def run_threshold(root: Path, threshold: Threshold, implementation: str, manifest: dict[str, Any], scenarios: dict[str, dict[str, Any]],
                  paths: dict[str, Path], p0: dict[str, Any], candidates: dict[str, list[dict[str, Any]]], *, quick: bool = False) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    for directory in ("profiles", "raw_backend_output", "checkpoints", "logs"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    config = benchmark_config(manifest); scenario_ids = ("M_RING",) if quick else ORDER
    group_backend = H2sGroupRecoveryBackend(EXECUTABLE, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0, h2s_flow_sorting=4, attempt_celf_fallback=True)
    singleton_backend = H2sPfBackend(EXECUTABLE, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0, h2s_flow_sorting=4, attempt_celf_fallback=True)
    candidate_writer = GzipCandidateWriter(root / "merge_candidates.csv.gz")
    final_groups: list[dict[str, Any]] = []; synthesis_rows = []; replay_rows = []; singleton_rows = []; accepted_rows = []; failures = []; certificates = []
    storage_rows = []; filter_samples: dict[str, list[float]] = defaultdict(list); funnel_rows = []; scenario_costs = []
    for scenario_id in scenario_ids:
        started = time.monotonic(); scenario_candidates = candidates[scenario_id]
        fault_affected = {str(row["fault_id"]): canonical_affected(str(row["affected_flow_ids"]).split(";")) for row in scenario_candidates}
        contract = threshold_contract(implementation, threshold, scenario_id, paths[scenario_id], p0[scenario_id], scenario_candidates, config)
        write_atomic_json(root / "checkpoints" / scenario_id / "resume_contract.json", contract)

        def evaluate(faults: tuple[str, ...], affected: tuple[str, ...]) -> dict[str, Any]:
            return evaluate_necessary_conditions(scenarios[scenario_id], faults, affected)

        def synthesize(faults: tuple[str, ...], affected: tuple[str, ...], record: dict[str, Any]) -> dict[str, Any]:
            raw = root / "raw_backend_output" / scenario_id / record["candidate_group_id"]
            result = group_backend.synthesize(RecoverySynthesisRequest(paths[scenario_id], disabled_links=faults,
                healthy_primary_routes={item["flow_id"]: item for item in p0[scenario_id]["logical_routes"]}, affected_flow_ids=affected,
                solver_timeout_s=TIMEOUT_S, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=raw / "input"))
            write_attempt_logs(raw / "logs", result)
            replay = singleton_replay(result, paths[scenario_id], p0[scenario_id], faults, affected,
                                      root / "checkpoints/replay" / scenario_id / record["candidate_group_id"]) if result.status.value in SUCCESS else []
            replay_rows.extend({"threshold_id": threshold.identifier, "scenario": scenario_id, **row} for row in replay)
            row = backend_result_row(scenario_id, faults, result, replay)
            row.update({"threshold_id": threshold.identifier, "jaccard_num": record["jaccard_num"], "jaccard_den": record["jaccard_den"],
                        "jaccard_decimal": record["jaccard_decimal"], "overlap_coefficient": record["overlap_coefficient"]})
            synthesis_rows.append(row)
            if not row["accepted"]: failures.append(row)
            return {**row, "profile": result.profile if row["accepted"] else None}

        search = run_jaccard_grouping_search(fault_affected, threshold, evaluate, synthesize,
            candidate_sink=lambda row, sid=scenario_id: candidate_writer.write({"scenario": sid, **row}))
        for stage, values in search["filter_stage_samples_ms"].items(): filter_samples[stage].extend(values)
        certificates.extend({"threshold_id": threshold.identifier, "scenario": scenario_id, **row} for row in search["failure_certificates"])
        for row in search["accepted_merge_history"]:
            accepted_rows.append({"threshold_id": threshold.identifier, "scenario": scenario_id, **row})
        # Search for this scenario is frozen: materialize only its final shared profiles.
        for row in search["final_groups"]:
            group = {"threshold_id": threshold.identifier, "scenario": scenario_id, "group_id": row["group_id"],
                     "fault_ids": row["fault_ids"], "affected_flow_ids": row["affected_flow_ids"], "group_size": row["group_size"],
                     "profile_ref": "", "semantic_profile_hash": ""}
            if row["group_size"] > 1:
                if not row["profile"]: raise Exp22Error(f"MISSING_FINAL_SHARED_PROFILE:{scenario_id}:{row['group_id']}")
                ref, stored = _write_profile(root, scenario_id, row["group_id"], row["profile"])
                group["profile_ref"], group["semantic_profile_hash"] = ref, semantic_profile_hash(row["profile"])
                storage_rows.append({"threshold_id": threshold.identifier, "scenario": scenario_id, "group_id": row["group_id"], "group_size": row["group_size"], **stored})
            final_groups.append(group)
        # The actual final-singleton solve is part of this threshold's measured cost.
        for group in [row for row in final_groups if row["scenario"] == scenario_id and row["group_size"] == 1]:
            fault = group["fault_ids"][0]; affected = fault_affected[fault]; raw = root / "raw_backend_output" / scenario_id / group["group_id"] / "final_singleton"
            result = singleton_backend.synthesize(RecoverySynthesisRequest(paths[scenario_id], disabled_links=(fault,),
                healthy_primary_routes={item["flow_id"]: item for item in p0[scenario_id]["logical_routes"]}, affected_flow_ids=affected,
                solver_timeout_s=TIMEOUT_S, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=raw / "input"))
            write_attempt_logs(raw / "logs", result)
            if result.status.value not in SUCCESS or not result.profile: raise Exp22Error(f"FINAL_SINGLETON_SYNTHESIS_FAILED:{scenario_id}:{fault}")
            ref, stored = _write_profile(root, scenario_id, group["group_id"], result.profile)
            group["profile_ref"], group["semantic_profile_hash"] = ref, semantic_profile_hash(result.profile)
            storage_rows.append({"threshold_id": threshold.identifier, "scenario": scenario_id, "group_id": group["group_id"], "group_size": 1, **stored})
            singleton_rows.append({"threshold_id": threshold.identifier, "scenario": scenario_id, "fault_id": fault, "status": result.status.value,
                                   "semantic_profile_hash": group["semantic_profile_hash"], "total_backend_ms": result.timings_ms.get("total_backend", 0)})
        elapsed = time.monotonic() - started
        scenario_costs.append({"scenario": scenario_id, "wall_ms": elapsed * 1000, "similarity_ms": search["similarity_ms"], "filter_ms": search["filter_ms"],
                               "group_backend_ms": sum(float(row["total_backend_ms"]) for row in search["synthesis_attempts"]),
                               "final_singleton_ms": sum(float(row["total_backend_ms"]) for row in singleton_rows if row["scenario"] == scenario_id),
                               **search["funnel"]})
        if elapsed > SCENARIO_BUDGET_S: raise Exp22Error(f"THRESHOLD_SCENARIO_BUDGET_EXCEEDED:{threshold.identifier}:{scenario_id}")
    candidate_writer.close()
    # Search is now frozen, so exp19 per-fault status/hash parity may be read.
    if not quick:
        with (EXP19 / "per_fault_results.csv").open(encoding="utf-8", newline="") as handle:
            exp19_rows = {(row["scenario"], row["fault_id"]): row for row in csv.DictReader(handle)}
        for row in singleton_rows:
            expected = exp19_rows[(row["scenario"], row["fault_id"])]
            row["exp19_status"], row["exp19_semantic_profile_hash"] = expected["status"], expected["semantic_profile_hash"]
            row["exact_parity"] = row["status"] == expected["status"] and row["semantic_profile_hash"] == expected["semantic_profile_hash"]
            if not row["exact_parity"]: raise Exp22Error(f"SINGLETON_BASELINE_PARITY_FAILED:{threshold.identifier}:{row['scenario']}:{row['fault_id']}")
    mappings = {}
    for group in final_groups:
        for fault in group["fault_ids"]:
            mappings[f"{group['scenario']}:{fault}"] = {"scenario": group["scenario"], "fault_id": fault, "group_id": group["group_id"],
                "profile_ref": group["profile_ref"], "semantic_profile_hash": group["semantic_profile_hash"]}
    expected_faults = sum(len(candidates[s]) for s in scenario_ids) if not quick else len(candidates["M_RING"])
    coverage = [{"threshold_id": threshold.identifier, "expected_faults": expected_faults, "mapped_faults": len(mappings),
                 "final_profile_count": len(final_groups), "coverage_complete": len(mappings) == expected_faults}]
    write_csv(root / "final_fault_groups.csv", [{**row, "fault_ids": ";".join(row["fault_ids"]), "affected_flow_ids": ";".join(row["affected_flow_ids"])} for row in final_groups])
    write_atomic_json(root / "fault_to_group_profile.json", mappings); write_csv(root / "coverage_validation.csv", coverage)
    write_csv(root / "singleton_replay_validation.csv", replay_rows + singleton_rows); write_csv(root / "group_synthesis_attempts.csv", synthesis_rows)
    write_atomic_json(root / "failure_certificates.json", certificates)
    total = {key: sum(float(row.get(key, 0)) for row in scenario_costs) for key in ("similarity_ms", "filter_ms", "group_backend_ms", "final_singleton_ms")}
    return {"threshold": threshold, "root": root, "final_groups": final_groups, "mappings": mappings, "coverage": coverage[0],
            "synthesis_rows": synthesis_rows, "replay_rows": replay_rows, "singleton_rows": singleton_rows, "accepted_rows": accepted_rows,
            "failure_certificates": certificates, "storage_rows": storage_rows, "filter_samples": filter_samples, "scenario_costs": scenario_costs,
            "cost": {**total, "total_ms": sum(total.values())}, "partition_sha": _profile_partition_sha(final_groups),
            "candidate_count": candidate_writer.count, "complete": len(mappings) == expected_faults}


def tau1_parity(result: dict[str, Any]) -> dict[str, Any]:
    """The sole permitted exp21 read before lower-threshold post-hoc analysis."""
    exp21_groups = list(csv.DictReader((EXP21 / "final_fault_groups.csv").open()))
    exp21_mapping = json.loads((EXP21 / "fault_to_group_profile.json").read_text(encoding="utf-8"))
    actual_groups = {(row["scenario"], row["group_id"], tuple(sorted(row["fault_ids"]))) for row in result["final_groups"]}
    expected_groups = {(row["scenario"], row["group_id"], tuple(sorted(row["fault_ids"].split(";")))) for row in exp21_groups}
    actual_mapping = result["mappings"]
    hashes = all(actual_mapping.get(key, {}).get("semantic_profile_hash") == value.get("semantic_profile_hash") for key, value in exp21_mapping.items())
    accepted = len(result["accepted_rows"])
    passed = len(result["final_groups"]) == 986 and accepted == 113 and sum(len(row["fault_ids"]) for row in result["final_groups"] if row["group_size"] > 1) == 226 and len(actual_mapping) == 1099 and actual_groups == expected_groups and hashes
    return {"tau1_exp21_parity_pass": passed, "final_profile_count": len(result["final_groups"]), "accepted_merges": accepted,
            "shared_fault_count": sum(len(row["fault_ids"]) for row in result["final_groups"] if row["group_size"] > 1),
            "mapped_faults": len(actual_mapping), "partition_sha": result["partition_sha"],
            "exp21_partition_sha": canonical_sha(sorted(({"scenario": row["scenario"], "group_id": row["group_id"], "fault_ids": sorted(row["fault_ids"].split(";"))} for row in exp21_groups), key=lambda row: (row["scenario"], row["group_id"]))),
            "semantic_hash_parity": hashes}


def full_tau1_parity_qualification() -> dict[str, Any]:
    """Perform the actual six-scenario tau=1 exp21 compatibility gate."""
    with tempfile.TemporaryDirectory(prefix="exp22-tau1-parity-") as directory:
        root = Path(directory)
        implementation, before = git("rev-parse", "HEAD"), frozen_manifest()
        manifest, scenarios, paths, p0, candidates, _ = recover_inputs(root / "preflight/p0")
        result = run_threshold(root / "tau_100", THRESHOLDS[0], implementation, manifest, scenarios, paths, p0, candidates)
        gate = tau1_parity(result)
        if frozen_manifest() != before:
            raise Exp22Error("FROZEN_HISTORY_CHANGED_DURING_TAU1_QUALIFICATION")
        return gate


def _similarity_bin(row: dict[str, Any]) -> str:
    n, d = int(row["jaccard_num"]), int(row["jaccard_den"])
    if n == d: return "{1.0}"
    if 5 * n < d: return "(0,0.2)"
    if 5 * n < 2 * d: return "[0.2,0.4)"
    if 5 * n < 3 * d: return "[0.4,0.6)"
    if 5 * n < 4 * d: return "[0.6,0.8)"
    return "[0.8,1.0)"


def pareto(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    complete = [row for row in rows if row["complete"]]
    output = []
    for row in complete:
        count = int(row.get("final_profile_count", row.get("final_profiles", 0)))
        cost = float(row.get("T_total_ms", row.get("total_algorithm_ms", 0)))
        dominated_by = [other["threshold_id"] for other in complete if other is not row and int(other.get("final_profile_count", other.get("final_profiles", 0))) <= count and float(other.get("T_total_ms", other.get("total_algorithm_ms", 0))) <= cost and (int(other.get("final_profile_count", other.get("final_profiles", 0))) < count or float(other.get("T_total_ms", other.get("total_algorithm_ms", 0))) < cost)]
        output.append({"threshold_id": row["threshold_id"], "final_profile_count": count, "T_total_ms": cost, "pareto_optimal": not dominated_by, "dominated_by": ";".join(sorted(dominated_by))})
    return output


def qualification(*, live: bool = False) -> dict[str, Any]:
    from tools.jaccard_fault_grouping import Threshold
    values = {"same_set": jaccard_counts(("a", "b"), ("a", "b")) == (2, 2), "disjoint": jaccard_counts(("a",), ("b",)) == (0, 2),
              "partial": jaccard_counts(("a", "b"), ("b", "c")) == (1, 3), "subset": jaccard_counts(("a",), ("a", "b")) == (1, 2),
              "boundary_060": Threshold("x", 3, 5).eligible(3, 5), "below_060": not Threshold("x", 3, 5).eligible(2, 4),
              "positive": Threshold("p", 0, 1, True).eligible(1, 100) and not Threshold("p", 0, 1, True).eligible(0, 1),
              "union": affected_union(("a", "b"), ("b", "c")) == ("a", "b", "c")}
    with (EXP21 / "final_fault_groups.csv").open(encoding="utf-8", newline="") as handle:
        exp21 = list(csv.DictReader(handle))
    values["tau1_exp21_artifact_shape"] = len(exp21) == 986 and sum(int(row["group_size"]) == 2 for row in exp21) == 113
    # Deterministic toy proves active-group similarity is recomputed after a merge.
    toy = {"a": ("1", "2", "3"), "b": ("2", "3", "4"), "c": ("3", "4", "5")}
    calls = []
    result = run_jaccard_grouping_search(toy, Threshold("toy", 1, 5), lambda f, a: {"passed": True, "timings_ms": {}, "first_failure": ""},
        lambda f, a, r: calls.append((f, a)) or {"accepted": True, "profile": {}, "semantic_profile_hash": "toy"})
    values["size3_toy_merge"] = any(row["group_size"] == 3 for row in result["final_groups"])
    values["checkpoint_isolation"] = THRESHOLDS[0].identifier != THRESHOLDS[1].identifier
    values["f1_f4_exp21_static_parity"] = (ROOT / "tools/schedulability_necessary_conditions.py").exists()
    if not live:
        values["qualification_pass"] = all(values.values())
        return values
    # One real non-tau1 candidate: choose the first F1--F4-pass pair below 1,
    # then require a true multi-disabled synthesis and singleton replay.
    with tempfile.TemporaryDirectory(prefix="exp22-live-qualification-") as directory:
        manifest, scenarios, paths, p0, candidates, _ = recover_inputs(Path(directory) / "p0")
        chosen = None
        threshold = THRESHOLDS[2]
        for scenario_id in ORDER:
            rows = candidates[scenario_id]
            for index, left in enumerate(rows):
                left_set = canonical_affected(str(left["affected_flow_ids"]).split(";"))
                for right in rows[index + 1:]:
                    right_set = canonical_affected(str(right["affected_flow_ids"]).split(";")); inter, union = jaccard_counts(left_set, right_set)
                    if not threshold.eligible(inter, union) or inter == union: continue
                    faults = tuple(sorted((str(left["fault_id"]), str(right["fault_id"])))); affected = affected_union(left_set, right_set)
                    if evaluate_necessary_conditions(scenarios[scenario_id], faults, affected)["passed"]:
                        chosen = scenario_id, faults, affected; break
                if chosen: break
            if chosen: break
        if not chosen: raise Exp22Error("QUALIFICATION_NO_LOW_SIMILARITY_PASSING_PAIR")
        scenario_id, faults, affected = chosen
        backend = H2sGroupRecoveryBackend(EXECUTABLE, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0, h2s_flow_sorting=4, attempt_celf_fallback=True)
        result = backend.synthesize(RecoverySynthesisRequest(paths[scenario_id], disabled_links=faults,
            healthy_primary_routes={row["flow_id"]: row for row in p0[scenario_id]["logical_routes"]}, affected_flow_ids=affected,
            solver_timeout_s=TIMEOUT_S, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=Path(directory) / "input"))
        replay = singleton_replay(result, paths[scenario_id], p0[scenario_id], faults, affected, Path(directory) / "replay") if result.status.value in SUCCESS else []
        values["real_low_similarity_shared_synthesis"] = result.status.value in SUCCESS and len(replay) == 2 and all(row["singleton_replay_valid"] for row in replay)
        values["real_low_similarity_scenario"] = scenario_id
        values["real_low_similarity_faults"] = list(faults)
    tau1 = full_tau1_parity_qualification()
    values["tau1_exp21_exact_parity"] = tau1["tau1_exp21_parity_pass"]
    values["tau1_exp21_exact_parity_detail"] = tau1
    values["qualification_pass"] = all(values.values())
    return values


def _candidate_trace(rows: Iterable[dict[str, Any]]) -> str:
    fields = ("iteration", "left_group_id", "right_group_id", "candidate_group_id", "fault_ids", "jaccard_num", "jaccard_den",
              "threshold_pass", "first_failure", "filter_pass", "decision", "rank")
    digest = hashlib.sha256()
    for row in rows:
        # Candidate CSV is deliberately the frozen replay source.  Canonical
        # text makes an in-memory integer/bool and its CSV representation
        # compare identically without weakening any decision comparison.
        normalized = {}
        for field in fields:
            value = row.get(field, "")
            normalized[field] = "" if value is None else ("true" if value is True else "false" if value is False else str(value).lower() if str(value).lower() in {"true", "false"} else str(value))
        digest.update(canonical_json_bytes(normalized))
    return digest.hexdigest()


def decision_replay(result: dict[str, Any], scenarios: dict[str, dict[str, Any]], candidates: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Rerun deterministic Jaccard/F1--F4 decisions using frozen backend outcomes only."""
    threshold, root = result["threshold"], result["root"]; output = []
    outcome = {(row["scenario"], row["group_id"]): row for row in result["synthesis_rows"]}
    for scenario_id in ORDER:
        if not any(row["scenario"] == scenario_id for row in result["final_groups"]): continue
        fault_affected = {str(row["fault_id"]): canonical_affected(str(row["affected_flow_ids"]).split(";")) for row in candidates[scenario_id]}
        trace = []
        replay = run_jaccard_grouping_search(fault_affected, threshold,
            lambda faults, affected, sid=scenario_id: evaluate_necessary_conditions(scenarios[sid], faults, affected),
            lambda faults, affected, record, sid=scenario_id: {"accepted": bool(outcome.get((sid, record["candidate_group_id"]), {}).get("accepted")), "profile": {},
                                               "semantic_profile_hash": outcome.get((sid, record["candidate_group_id"]), {}).get("semantic_profile_hash", "")},
            candidate_sink=lambda row: trace.append(row))
        with gzip.open(root / "merge_candidates.csv.gz", "rt", encoding="utf-8") as handle:
            baseline = [row for row in csv.DictReader(handle) if row["scenario"] == scenario_id]
        expected_groups = {(row["group_id"], tuple(sorted(row["fault_ids"]))) for row in result["final_groups"] if row["scenario"] == scenario_id}
        observed_groups = {(row["group_id"], tuple(sorted(row["fault_ids"]))) for row in replay["final_groups"]}
        trace_match = _candidate_trace(trace) == _candidate_trace(baseline)
        accepted_match = [row["new_group_id"] for row in replay["accepted_merge_history"]] == [row["new_group_id"] for row in result["accepted_rows"] if row["scenario"] == scenario_id]
        partition_match = expected_groups == observed_groups
        output.append({"threshold_id": threshold.identifier, "scenario": scenario_id, "candidate_trace_match": trace_match,
                       "accepted_sequence_match": accepted_match,
                       "final_partition_match": partition_match, "backend_cache_only": True,
                       "repeat_pass": trace_match and accepted_match and partition_match})
    return output


def backend_replay(result: dict[str, Any], scenarios: dict[str, dict[str, Any]], paths: dict[str, Path], p0: dict[str, Any], candidates: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Resynthesize deterministic min/median/max attempted groups per scenario."""
    threshold, root = result["threshold"], result["root"]; output = []
    backend = H2sGroupRecoveryBackend(EXECUTABLE, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0, h2s_flow_sorting=4, attempt_celf_fallback=True)
    for scenario_id in ORDER:
        groups = sorted([row for row in result["synthesis_rows"] if row["scenario"] == scenario_id], key=lambda row: (int(row["group_size"]), row["group_id"]))
        if not groups:
            output.append({"threshold_id": threshold.identifier, "scenario": scenario_id, "selection": "NO_BACKEND_CANDIDATES",
                           "repeat_pass": True, "reason": "ALL_ELIGIBLE_PAIRS_PRUNED_BEFORE_BACKEND"})
            continue
        selected = []
        for index in sorted({0, len(groups)//2, len(groups)-1}) if groups else []:
            if groups[index] not in selected: selected.append(groups[index])
        for group in selected:
            faults = tuple(str(group["fault_ids"]).split(";")); affected = affected_union(*(canonical_affected(str(next(row for row in candidates[scenario_id] if row["fault_id"] == fault)["affected_flow_ids"]).split(";")) for fault in faults))
            raw = root / "raw_backend_output" / "repeatability" / scenario_id / group["group_id"]
            response = backend.synthesize(RecoverySynthesisRequest(paths[scenario_id], disabled_links=faults,
                healthy_primary_routes={row["flow_id"]: row for row in p0[scenario_id]["logical_routes"]}, affected_flow_ids=affected,
                solver_timeout_s=TIMEOUT_S, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=raw / "input"))
            write_attempt_logs(raw / "logs", response)
            replay = singleton_replay(response, paths[scenario_id], p0[scenario_id], faults, affected, root / "checkpoints/repeatability" / scenario_id / group["group_id"]) if response.status.value in SUCCESS else []
            expected_hash = group.get("semantic_profile_hash", "")
            semantic_match = (not expected_hash and not response.profile) or (bool(response.profile) and semantic_profile_hash(response.profile) == expected_hash)
            status_match = response.status.value == group["status"]
            replay_ok = (len(replay) == len(faults) and all(row["singleton_replay_valid"] for row in replay)) if response.status.value in SUCCESS else True
            output.append({"threshold_id": threshold.identifier, "scenario": scenario_id, "group_id": group["group_id"], "group_size": group["group_size"],
                           "selection": "MIN_MEDIAN_MAX_GROUP_SIZE_THEN_GID", "status": response.status.value, "expected_status": group["status"], "status_match": status_match,
                           "semantic_hash_match": semantic_match, "singleton_replay_match": replay_ok,
                           "repeat_pass": status_match and semantic_match and replay_ok,
                           "total_backend_ms": response.timings_ms.get("total_backend", 0)})
    return output


def run_formal(output: Path, *, quick: bool = False) -> dict[str, Any]:
    if output.resolve() == OUT.resolve() and git("status", "--porcelain"):
        raise Exp22Error("FORMAL_RUN_REQUIRES_CLEAN_IMPLEMENTATION_COMMIT")
    implementation, frozen_before = git("rev-parse", "HEAD"), frozen_manifest()
    if implementation != PARENT_COMMIT and git("merge-base", "--is-ancestor", PARENT_COMMIT, implementation) is None:
        raise Exp22Error("PARENT_COMMIT_NOT_ANCESTOR")
    output.mkdir(parents=True, exist_ok=True); (output / "logs").mkdir(parents=True, exist_ok=True)
    manifest, scenarios, paths, p0, candidates, parity_rows = recover_inputs(output / "preflight/p0")
    pair_rows, landscape, distribution = initial_similarity(candidates)
    write_gzip_csv(output / "initial_pair_similarity.csv.gz", pair_rows); write_csv(output / "initial_threshold_landscape.csv", landscape); write_csv(output / "similarity_distribution.csv", distribution)
    selected = (THRESHOLDS[0], THRESHOLDS[2], THRESHOLDS[-1]) if quick else THRESHOLDS
    results = []
    for threshold in selected:
        result = run_threshold(output / "thresholds" / threshold.identifier, threshold, implementation, manifest, scenarios, paths, p0, candidates, quick=quick)
        if threshold.identifier == "tau_100" and not quick:
            gate = tau1_parity(result)
            write_atomic_json(output / "tau1_exp21_parity.json", gate)
            if not gate["tau1_exp21_parity_pass"]: raise Exp22Error("TAU1_EXP21_PARITY_FAILED")
        results.append(result)
    # All threshold search results are frozen; post-hoc exp20/exp21 reads begin here.
    repeatability = [row for result in results for row in decision_replay(result, scenarios, candidates)]
    backend_repeatability = [row for result in results for row in backend_replay(result, scenarios, paths, p0, candidates)]
    if not all(row["repeat_pass"] for row in repeatability + backend_repeatability):
        raise Exp22Error("REPEATABILITY_FAILED")
    # All dynamic-search and replay artifacts are now frozen; post-hoc reads begin here.
    with (EXP19 / "per_fault_results.csv").open(encoding="utf-8", newline="") as handle:
        exp19_total = sum(float(row["total_backend_ms"]) for row in csv.DictReader(handle))
    with (EXP21 / "compute_cost_comparison.csv").open(encoding="utf-8", newline="") as handle:
        exp21_cost = float(list(csv.DictReader(handle))[0]["exp21_total_algorithm_ms"])
    summaries = []; funnels = []; effectiveness = []; outcomes = []; accepted = []; failed = []; certs = []; storage = []; groups = []
    for result in results:
        threshold, cost = result["threshold"], result["cost"]; rows = result["synthesis_rows"]
        funnel = Counter()
        for scenario in result["scenario_costs"]:
            for key in ("generated_active_pairs", "jaccard_rejected", "jaccard_eligible", "certificate_pruned", "F1_pruned", "F2_pruned", "F3_pruned", "F4_pruned", "filter_pass", "stale_after_accept"):
                funnel[key] += int(scenario[key])
            for stage, count in (("ALL_ACTIVE_PAIR_PROPOSALS", scenario["generated_active_pairs"]), ("JACCARD_ELIGIBLE", scenario["jaccard_eligible"]), ("FILTER_PASS", scenario["filter_pass"]), ("BACKEND_ATTEMPTED", sum(1 for row in rows if row["scenario"] == scenario["scenario"])), ("MERGE_SUCCESS", sum(1 for row in rows if row["scenario"] == scenario["scenario"] and row["accepted"]))):
                funnels.append({"threshold_id": threshold.identifier, "scenario": scenario["scenario"], "stage": stage, "count": count,
                                "fraction_of_generated_pairs": count / scenario["generated_active_pairs"] if scenario["generated_active_pairs"] else 0})
        nc_pruned = sum(funnel[key] for key in ("certificate_pruned", "F1_pruned", "F2_pruned", "F3_pruned", "F4_pruned")); backend_count = len(rows)
        multi = [row for row in result["final_groups"] if row["group_size"] > 1]
        summary = {"threshold_id": threshold.identifier, "threshold_num": threshold.numerator, "threshold_den": threshold.denominator, "positive_overlap": threshold.positive_overlap,
                   "scenario_count_complete": len(result["scenario_costs"]), "mapped_faults": len(result["mappings"]), "generated_active_pairs": funnel["generated_active_pairs"],
                   "jaccard_rejected": funnel["jaccard_rejected"], "jaccard_eligible": funnel["jaccard_eligible"], "certificate_pruned": funnel["certificate_pruned"],
                   "F1_pruned": funnel["F1_pruned"], "F2_pruned": funnel["F2_pruned"], "F3_pruned": funnel["F3_pruned"], "F4_pruned": funnel["F4_pruned"],
                   "filter_pass": funnel["filter_pass"], "group_backend_attempts": backend_count, "H2S_success": sum(row["status"] == "SUCCESS_H2S" for row in rows),
                   "CELF_trigger": sum(row.get("algorithm_used") == "CELF" for row in rows), "CELF_rescue": sum(row["status"] == "SUCCESS_CELF_FALLBACK" for row in rows),
                   "HNF": sum(row["status"] == "HEURISTIC_NOT_FOUND" for row in rows), "accepted_merges": len(result["accepted_rows"]),
                   "final_profile_count": len(result["final_groups"]), "final_singleton_count": sum(row["group_size"] == 1 for row in result["final_groups"]),
                   "final_shared_group_count": len(multi), "shared_fault_count": sum(len(row["fault_ids"]) for row in multi),
                   "max_group_size": max((row["group_size"] for row in result["final_groups"]), default=0),
                   "T_similarity_ms": cost["similarity_ms"], "T_filter_ms": cost["filter_ms"], "T_group_backend_ms": cost["group_backend_ms"],
                   "T_final_singleton_ms": cost["final_singleton_ms"], "T_total_ms": cost["total_ms"], "complete": result["complete"],
                   "necessary_condition_avoided_fraction": nc_pruned / (nc_pruned + backend_count) if nc_pruned + backend_count else 0,
                   "jaccard_rejection_fraction": funnel["jaccard_rejected"] / funnel["generated_active_pairs"] if funnel["generated_active_pairs"] else 0}
        summaries.append(summary); accepted.extend(result["accepted_rows"]); failed.extend([row for row in rows if not row["accepted"]]); certs.extend(result["failure_certificates"]); storage.extend(result["storage_rows"])
        for row in result["final_groups"]: groups.append({"threshold_id": threshold.identifier, "scenario": row["scenario"], "group_size": row["group_size"], "group_id": row["group_id"]})
        for stage, values in result["filter_samples"].items():
            effectiveness.append({"threshold_id": threshold.identifier, "stage": stage, "direct_checks": len(values), "direct_failures": funnel.get(stage, 0),
                                  "certificate_reuses": funnel["certificate_pruned"], "total_stage_ms": sum(values), "mean_stage_us": statistics.mean(values) * 1000 if values else 0,
                                  "p95_stage_us": percentile(values, .95) * 1000 if values else 0,
                                  "saved_backend_attempts_per_filter_second": nc_pruned / (cost["filter_ms"] / 1000) if cost["filter_ms"] else 0})
        buckets = defaultdict(list)
        for row in rows: buckets[(row["scenario"], _similarity_bin(row))].append(row)
        for (scenario, bucket), values in sorted(buckets.items()):
            outcomes.append({"threshold_id": threshold.identifier, "scenario": scenario, "jaccard_bin": bucket, "attempt_count": len(values),
                             "H2S_primary_success": sum(row["status"] == "SUCCESS_H2S" for row in values), "CELF_trigger": sum(row.get("algorithm_used") == "CELF" for row in values),
                             "CELF_rescue": sum(row["status"] == "SUCCESS_CELF_FALLBACK" for row in values), "HNF": sum(row["status"] == "HEURISTIC_NOT_FOUND" for row in values),
                             "accepted_merge": sum(row["accepted"] for row in values)})
    comparison = []
    for row in summaries:
        comparison.append({"threshold_id": row["threshold_id"], "final_profiles": row["final_profile_count"], "profile_reduction_vs_exp19": 1 - row["final_profile_count"] / 1099,
                           "profile_reduction_vs_exp21": 986 - row["final_profile_count"], "shared_groups": row["final_shared_group_count"], "shared_faults": row["shared_fault_count"],
                           "max_group_size": row["max_group_size"], "generated_pairs": row["generated_active_pairs"], "eligible_pairs": row["jaccard_eligible"],
                           "NC_pruned": sum(row[key] for key in ("certificate_pruned", "F1_pruned", "F2_pruned", "F3_pruned", "F4_pruned")), "backend_attempts": row["group_backend_attempts"],
                           "accepted_merges": row["accepted_merges"], "total_algorithm_ms": row["T_total_ms"], "compute_reduction_vs_exp19": 1 - row["T_total_ms"] / exp19_total,
                           "compute_change_vs_exp21": row["T_total_ms"] / exp21_cost - 1, "complete": row["complete"]})
    success = []
    for (threshold, bucket), values in defaultdict(list, {(row["threshold_id"], _similarity_bin(row)): [] for result in results for row in result["synthesis_rows"]}).items(): pass
    bucketed = defaultdict(list)
    for result in results:
        for row in result["synthesis_rows"]: bucketed[(row["threshold_id"], _similarity_bin(row))].append(row)
    for (threshold_id, bucket), values in sorted(bucketed.items()):
        success.append({"threshold_id": threshold_id, "jaccard_bin": bucket, "attempt_count": len(values), "success_count": sum(row["accepted"] for row in values),
                        "success_rate": sum(row["accepted"] for row in values) / len(values), "median_backend_ms": statistics.median(float(row["total_backend_ms"]) for row in values),
                        "p95_backend_ms": percentile([float(row["total_backend_ms"]) for row in values], .95), "median_group_size": statistics.median(int(row["group_size"]) for row in values)})
    group_distribution = [{"threshold_id": threshold, "group_size": size, "group_count": count} for (threshold, size), count in sorted(Counter((row["threshold_id"], row["group_size"]) for row in groups).items())]
    write_csv(output / "input_parity.csv", parity_rows); write_csv(output / "threshold_summary.csv", summaries); write_csv(output / "threshold_comparison.csv", comparison)
    write_csv(output / "threshold_funnel.csv", funnels); write_csv(output / "necessary_condition_effectiveness.csv", effectiveness); write_csv(output / "similarity_backend_outcome.csv", outcomes)
    write_csv(output / "jaccard_success_analysis.csv", success); write_csv(output / "group_size_distribution.csv", group_distribution); write_csv(output / "accepted_merge_history.csv", accepted)
    write_csv(output / "failed_group_synthesis.csv", failed); write_atomic_json(output / "failure_certificates.json", certs)
    write_csv(output / "filter_timing_summary.csv", effectiveness)
    costs = [{"threshold_id": row["threshold_id"], "T_similarity_ms": row["T_similarity_ms"], "T_filter_ms": row["T_filter_ms"], "T_group_backend_ms": row["T_group_backend_ms"], "T_final_singleton_ms": row["T_final_singleton_ms"], "T_total_ms": row["T_total_ms"], "exp19_serial_backend_ms": exp19_total, "exp21_total_algorithm_ms": exp21_cost} for row in summaries]
    storage_summary = [storage_totals(EXP19, reference="exp19"), storage_totals(EXP21, reference="exp21")]
    storage_summary.extend(storage_totals(result["root"], reference=result["threshold"].identifier, threshold_id=result["threshold"].identifier) for result in results)
    write_csv(output / "compute_cost_comparison.csv", costs); write_csv(output / "storage_comparison.csv", storage_summary); write_csv(output / "pareto_frontier.csv", pareto(comparison))
    write_csv(output / "repeatability.csv", repeatability)
    write_csv(output / "backend_repeatability.csv", backend_repeatability)
    write_csv(output / "posthoc_baseline_comparison.csv", [{"reference": "exp19", "profile_count": 1099}, {"reference": "exp20_s1", "profile_count": 1008}, {"reference": "exp20_s2", "profile_count": 986}, {"reference": "exp20_existing_profile_exact_cover", "profile_count": 823, "note": "post-hoc exact cover of existing exp19 profiles"}, {"reference": "exp21", "profile_count": 986, "total_algorithm_ms": exp21_cost}, *[{"reference": row["threshold_id"], "profile_count": row["final_profile_count"], "total_algorithm_ms": row["T_total_ms"]} for row in summaries]])
    frozen_after = frozen_manifest()
    if frozen_after != frozen_before: raise Exp22Error("FROZEN_HISTORY_CHANGED")
    complete = len(results) == len(THRESHOLDS) and all(row["complete"] for row in summaries) and all(row["repeat_pass"] for row in repeatability + backend_repeatability)
    verdict = "JACCARD_THRESHOLD_SWEEP_COMPLETE" if complete else "JACCARD_THRESHOLD_SWEEP_PARTIAL_BUDGET"
    verdict_payload = {"formal_verdict": verdict, "verdict_enums": FORMAL_VERDICTS, "algorithm_version": ALGORITHM_VERSION, "threshold_count": len(results), "complete": complete, "runtime_profile_contract": "NOT_ESTABLISHED"}
    write_atomic_json(output / "environment.json", {"platform": platform.platform(), "python": platform.python_version(), "parent_commit": PARENT_COMMIT, "implementation_commit": implementation})
    write_atomic_json(output / "source_manifest.json", {"parent_commit": PARENT_COMMIT, "implementation_commit": implementation, "frozen_history_sha256": frozen_before, "scenario_sha256": {s: sha256_file(paths[s]) for s in ORDER}, "candidate_fault_sha256": sha256_file(EXP19 / "candidate_faults.json"), "p0_route_sha256": {s: p0[s]["route_hash"] for s in ORDER}})
    write_atomic_json(output / "algorithm_config.json", {**benchmark_config(manifest), "algorithm_version": ALGORITHM_VERSION, "affected_feature": "HEALTHY_P0_AFFECTED_FLOW_SET", "group_feature": "UNION_OF_MEMBER_AFFECTED_SETS", "similarity": "JACCARD", "thresholds": [threshold.__dict__ for threshold in THRESHOLDS], "lowest_policy": "POSITIVE_OVERLAP", "ranking": ["JACCARD_DESC", "WINDOW_SLACK_DESC", "CUT_SLACK_DESC", "DEADLINE_SLACK_DESC", "UNION_GROUP_SIZE_DESC", "GROUP_ID_ASC"], "runtime_profile_contract": "NOT_ESTABLISHED"})
    write_atomic_json(output / "experiment_verdict.json", verdict_payload)
    lines = ["# exp22 Jaccard-threshold fault grouping", "", f"Formal verdict: {verdict}.", "", "## Protocol answers", "", "- JSPFG-v1 uses exact integer Jaccard comparisons over healthy-P0 affected-flow unions.", "- The six independent searches are tau100, tau080, tau060, tau040, tau020, and positive-overlap.", "- Each threshold starts from all 1,099 singleton faults; no decision, certificate, partition, profile, or checkpoint cache crosses a threshold or scenario boundary.", "- Jaccard is an a-priori candidate-space restriction and ranking feature, never a schedulability necessary condition.", "- A Jaccard reject skips F1--F4 and cannot produce a certificate; only monotone F1--F4 failures can do so. H2S/CELF failure is not infeasibility.", "- Every active pair is audited before filtering. Passed candidates are ranked by exact Jaccard, window/cut/deadline slack, group size, then group id.", "- Every final singleton was re-solved with H2sPf and compared exactly with exp19 status and semantic hash.", "- Replays rerun decisions against frozen backend outcomes and rerun selected min/median/max backend groups. Runtime activation remains NOT_ESTABLISHED.", "", "## Threshold sensitivity results:"]
    lines.extend(f"- {row['threshold_id']}: profiles={row['final_profile_count']}, total_ms={row['T_total_ms']:.3f}, complete={row['complete']}" for row in summaries)
    lines.extend(["", "Runtime activation remains NOT_ESTABLISHED.", "exp20's 823 reference is a post-hoc exact cover over existing exp19 profiles, not an exp22 lower bound."])
    write_atomic_bytes(output / "summary.md", ("\n".join(lines) + "\n").encode())
    hashes = {str(path.relative_to(output)): sha256_file(path) for path in sorted(output.rglob("*")) if path.is_file() and path.name != "analysis_manifest.json"}
    write_atomic_json(output / "analysis_manifest.json", {"parent_commit": PARENT_COMMIT, "implementation_commit": implementation, "algorithm_version": ALGORITHM_VERSION, "artifact_sha256": hashes, "required_outputs": ROOT_OUTPUTS, "thresholds": [row["threshold_id"] for row in summaries], "formal_verdict": verdict})
    return verdict_payload


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--qualification", action="store_true"); parser.add_argument("--quick", action="store_true"); parser.add_argument("--formal", action="store_true"); parser.add_argument("--output", type=Path, default=OUT); args = parser.parse_args()
    if args.qualification:
        result = qualification(live=True); print(json.dumps(result, indent=2, sort_keys=True)); return 0 if result["qualification_pass"] else 1
    if not args.quick and not args.formal: parser.error("select --qualification, --quick, or --formal")
    result = run_formal(args.output, quick=args.quick); print(json.dumps(result, indent=2, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
