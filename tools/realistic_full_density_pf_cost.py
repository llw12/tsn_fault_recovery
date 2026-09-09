"""Pure provenance, catalog, metric, and storage helpers for exp19.

The runner is intentionally the only layer that invokes PF synthesis.  These
helpers consume the immutable exp18j benchmark, recovered healthy-P0 routes,
and completed per-fault artifacts; they contain no policy that can select a
fault based on a PF outcome.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from tools.fixed_release_collision_audit import ROOT, canonical_sha256, sha256_file, tree_sha256
from tools.h2s_jrs_backend import check_h2s_solution, normalize_schedule, parse_backend_output, prepare_h2s_inputs
from tools.h2s_pf_backend import SEMANTIC_PROFILE_FIELDS, semantic_profile_hash, semantic_profile_projection
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.run_p0_hnf_mechanism_diagnosis import signatures

OUT = ROOT / "results" / "realistic_full_density_pf_cost"
EXP18J = ROOT / "results" / "source_egress_aware_release_assignment"
ORDER = ("M_RING", "M_REDSTAR", "M_ROR", "L_RING", "L_REDSTAR", "L_ROR")
EXPECTED_SOURCE_SHA = {
    "M_RING": "35917de7491e2c108745552a892dad59ee2a94c4b6bb18b9fc2fff34ee03db79",
    "M_REDSTAR": "40d4ef3c3b649a14dbac5b3de918db61123945f7d472d27a0116c19f05a77bdd",
    "M_ROR": "893befade56501d84a2d9ae1c443c053e9aa9adc1949471ef5e870e0865075b0",
    "L_RING": "3d215e4f84417b6ae05e9b43a41c391c6e34b8c9cb48ec605f7650514532d7b7",
    "L_REDSTAR": "40c28d8eb477072672f7a9fbb8f70c5acb274c86547c36468f30dc8f78066790",
    "L_ROR": "491d1b46cabdc367249a76c38325cc64ccd10d56e8a0108b3f1cea6977dec9e0",
}
EXPECTED_RELEASE_MAP_SHA = {
    "M": "b3a7e019981d1018f5f851643c4be582cb302e817d2ff3c143fdb52abd731236",
    "L": "7675954ce6928db03cd2d5837a85fd9c3e212b3bd5ed177d9eaa17975cf91a44",
}
EXPECTED_COUNTS = {"M": (352, 3616), "L": (928, 9632)}
FROZEN_TREE_SHA256 = {
    "results/realistic_tsn_pf_cost": "4626bf9853a735e95d949d8056600f8d076744cfea9ed0aaf743ce425449f175",
    "results/p0_hnf_diagnosis": "b15351bd19ef7ec6956eda42e8a48aba908940d6d9412cbfdeb5d2f967c49155",
    "results/candidate_k_sensitivity": "024586fbe2f9d4d9a816212996fad1d928d1f046b51b5273c6d2d01d6ce08408",
    "results/h2s_multistart_sensitivity": "beed316c5c9901c6ed9248d59d331b67d35721947ba4c7f2fa2023d91b87ede3",
    "results/deadline_feasibility_calibration": "fa621c7eec21e9ce702dff1b7375970ce6f795ccff0d87c7724318429c5a258a",
    "results/h2s_primary_policy_sensitivity": "2d53e699d01d3d91372db4c46f4a0c34f8ce385a76472534ef4e6f516ec8b7d7",
    "results/tt_workload_density_calibration": "2b2b3d2b592a4d736aa381c20be1e7f0fcaace56a56420d07d6a34f380297e8d",
    "results/h2s_admission_placement_diagnostic": "99ccba68e3e03c4203d64e2f8e6ae384fc62984b450272888aaf689ce58d6984",
    "results/fixed_release_collision_audit": "c2441071f6c87f659c126b5435ec9208bac72ea796dc1b0cafcda6a6a6526f2b",
    "results/source_egress_aware_release_assignment": "55329d2b1320e5197f3547f78719d2eb13bf29d81a00f9c9923e64b31c2d9439",
}


class PfCampaignError(RuntimeError):
    """A provenance, semantic, or resume contract failure."""


def percentile(values: Iterable[float], fraction: float) -> float:
    values = sorted(float(value) for value in values)
    return values[math.ceil(fraction * len(values)) - 1] if values else 0.0


def mean_or_zero(values: Iterable[float]) -> float:
    values = [float(value) for value in values]
    return statistics.mean(values) if values else 0.0


def gzip_bytes(data: bytes) -> bytes:
    target = io.BytesIO()
    with gzip.GzipFile(filename="profile.json", mode="wb", fileobj=target, mtime=0) as stream:
        stream.write(data)
    return target.getvalue()


def gzip_size(data: bytes) -> int:
    return len(gzip_bytes(data))


def write_atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def write_atomic_json(path: Path, value: Any) -> None:
    write_atomic_bytes(path, canonical_json_bytes(value))


def frozen_preflight() -> dict[str, str]:
    actual = {name: tree_sha256(ROOT / name) for name in FROZEN_TREE_SHA256}
    mismatch = {name: {"expected": FROZEN_TREE_SHA256[name], "actual": actual[name]}
                for name in actual if actual[name] != FROZEN_TREE_SHA256[name]}
    if mismatch: raise PfCampaignError(f"FROZEN_HISTORY_CHANGED:{mismatch}")
    return actual


def benchmark_config(manifest: dict[str, Any]) -> dict[str, Any]:
    config = dict(manifest.get("backend_config", {}))
    expected = {"route_scope": "ALL_REROUTE", "primary_algorithm": "H2S", "celf_fallback": True,
                "h2s_flow_sorting": 4, "h2s_sorter": "LOW_PERIOD_FLOWS_FIRST",
                "configuration_rating": "PATH_LENGTH", "placement": "ASAP", "placement_cli": 0,
                "routing": "DIJKSTRA_OVERLAP", "candidate_paths_k": 5, "quantum_ns": 100,
                "global_seed": 1024, "backend_seed": 1024, "threads": 1, "timeout_s_h2s": 30,
                "timeout_s_celf": 30, "memory_limit_mb": 8192, "h2s_tiebreak_mode": "BASELINE",
                "h2s_tiebreak_seed": 0, "multistart": False, "diagnostic_trace": False}
    mismatch = {key: {"expected": value, "actual": config.get(key)} for key, value in expected.items()
                if config.get(key) != value}
    if mismatch: raise PfCampaignError(f"BACKEND_CONFIGURATION_DRIFT:{mismatch}")
    return config


def load_benchmark() -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Path]]:
    selected_path = EXP18J / "selected_pf_benchmark_manifest.json"
    verdict_path = EXP18J / "qualification_verdict.json"
    if not selected_path.is_file() or not verdict_path.is_file():
        raise PfCampaignError("PF_BENCHMARK_QUALIFICATION_FAILED:MISSING_EXP18J_MANIFEST")
    manifest, verdict = json.loads(selected_path.read_text(encoding="utf-8")), json.loads(verdict_path.read_text(encoding="utf-8"))
    if verdict.get("formal_verdict") != "FULL_DENSITY_PF_BENCHMARK_QUALIFIED":
        raise PfCampaignError("PF_BENCHMARK_QUALIFICATION_FAILED:EXP18J_VERDICT")
    benchmark_config(manifest)
    if manifest.get("release_map_sha256") != EXPECTED_RELEASE_MAP_SHA:
        raise PfCampaignError("BENCHMARK_SOURCE_HASH_MISMATCH:RELEASE_MAP")
    paths: dict[str, Path] = {}
    scenarios: dict[str, dict[str, Any]] = {}
    listed = manifest.get("derived_scenario_paths", {})
    recorded_shas = manifest.get("derived_scenario_sha256", {})
    for scenario_id in ORDER:
        path = ROOT / str(listed.get(scenario_id, ""))
        if not path.is_file() or sha256_file(path) != EXPECTED_SOURCE_SHA[scenario_id] or recorded_shas.get(scenario_id) != EXPECTED_SOURCE_SHA[scenario_id]:
            raise PfCampaignError(f"BENCHMARK_SOURCE_HASH_MISMATCH:{scenario_id}")
        scenario = json.loads(path.read_text(encoding="utf-8")); scale = scenario_id[0]
        flow_count, instance_count = EXPECTED_COUNTS[scale]
        if len(scenario.get("tt_flows", [])) != flow_count:
            raise PfCampaignError(f"BENCHMARK_SOURCE_FLOW_COUNT_MISMATCH:{scenario_id}")
        paths[scenario_id], scenarios[scenario_id] = path, scenario
        # The expected count is independently verified in P0 recovery through
        # the raw schedule instance identities; do not mutate the SAR object.
    return manifest, scenarios, paths


def canonical_route_hash(routes: Iterable[dict[str, Any]]) -> str:
    return canonical_sha256(sorted(routes, key=lambda row: str(row["flow_id"])))


def recover_p0_baseline(scenario_id: str, scenario_path: Path, scenario: dict[str, Any], work: Path) -> dict[str, Any]:
    """Recover a normalized healthy P0 only from exp18j's persisted raw output."""
    raw_root = EXP18J / "raw_backend_output" / scenario_id / "repeat_1"
    raw_log = raw_root / "logs" / "0_h2s_stdout.log"
    if not raw_log.is_file(): raise PfCampaignError(f"P0_BASELINE_UNAVAILABLE:{scenario_id}")
    raw = parse_backend_output(raw_log.read_text(encoding="utf-8"))
    prepared = prepare_h2s_inputs(scenario_path, work / scenario_id)
    normalized = normalize_schedule(prepared, raw, 100)
    checker = check_h2s_solution(prepared, normalized)
    signature = signatures(scenario, raw, "H2S")
    with (EXP18J / "p0_qualification.csv").open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["scenario"] == scenario_id]
    if len(rows) != 2 or not all(row["formal_complete"] == "True" for row in rows):
        raise PfCampaignError(f"P0_BASELINE_PARITY_FAILED:FORMAL_ROWS:{scenario_id}")
    schedule_sha = canonical_sha256(normalized["route_schedule"])
    for row in rows:
        if (row["normalized_schedule_sha"] != schedule_sha or row["instance_completion_sha"] != signature["instance_completion_sha256"]
                or row["formal_scheduled_count"] != str(len(scenario["tt_flows"]))):
            raise PfCampaignError(f"P0_BASELINE_PARITY_FAILED:{scenario_id}")
    if not raw.get("upstream_verifier_pass") or not checker["valid"] or signature["hnf_flow_count"]:
        raise PfCampaignError(f"P0_BASELINE_SEMANTIC_INVALID:{scenario_id}")
    routes = sorted(normalized["logical_routes"], key=lambda row: row["flow_id"])
    profile = dict(normalized["profile"])
    return {"scenario": scenario_id, "profile": profile, "logical_routes": routes,
            "route_hash": canonical_route_hash(routes), "normalized_schedule_sha": schedule_sha,
            "instance_completion_sha": signature["instance_completion_sha256"],
            "p0_reference_ms": statistics.median(float(row["total_wall_ms"]) for row in rows),
            "p0_profile_bytes": len(canonical_json_bytes(profile)),
            "p0_profile_gzip_bytes": gzip_size(canonical_json_bytes(profile)),
            "upstream_verifier_pass": bool(raw["upstream_verifier_pass"]), "static_checker_pass": bool(checker["valid"]),
            "all_flow_count": len(scenario["tt_flows"]), "expected_instance_count": sum(int(row["expected_instances"]) for row in signature["identities"]),
            "complete_instance_count": sum(int(row["complete_instances"]) for row in signature["identities"])}


def route_index(routes: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(route["flow_id"]): route for route in routes}


def candidate_catalog(scenario_id: str, scenario: dict[str, Any], healthy_routes: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Catalog all internal physical links, then freeze the P0-used subset."""
    routes = route_index(healthy_routes); switches = {str(node["id"]) for node in scenario["nodes"] if node.get("type") == "switch"}
    used_by: dict[str, set[str]] = defaultdict(set)
    use_count: dict[str, int] = defaultdict(int)
    for flow_id, route in routes.items():
        for link_id in route.get("link_path", []): used_by[str(link_id)].add(flow_id); use_count[str(link_id)] += 1
    census: list[dict[str, Any]] = []
    for link in sorted(scenario["links"], key=lambda row: str(row["id"])):
        fault_id = str(link["id"]); internal = str(link["endpoint_a"]) in switches and str(link["endpoint_b"]) in switches
        affected = sorted(used_by[fault_id]) if internal else []
        census.append({"scenario": scenario_id, "scale": scenario_id[0], "topology": scenario_id.split("_", 1)[1],
                       "link_id": fault_id, "fault_id": fault_id, "endpoint_a": str(link["endpoint_a"]), "endpoint_b": str(link["endpoint_b"]),
                       "internal_switch_link": internal, "p0_used": bool(used_by[fault_id]), "relevant_fault": bool(internal and affected),
                       "affected_flow_count": len(affected), "affected_flow_ratio": len(affected) / len(routes),
                       "affected_flow_ids": ";".join(affected), "healthy_route_use_count": use_count[fault_id], "PF_attempted": False})
    relevant = [dict(row) for row in census if row["relevant_fault"]]
    for rank, row in enumerate(sorted(relevant, key=lambda item: item["fault_id"]), 1): row["candidate_rank"] = rank
    frozen = [{key: row[key] for key in ("scenario", "fault_id", "endpoint_a", "endpoint_b", "affected_flow_count", "affected_flow_ids", "healthy_route_use_count", "candidate_rank")}
              for row in sorted(relevant, key=lambda item: item["fault_id"])]
    candidate_sha = canonical_sha256(frozen)
    for row in relevant: row["candidate_set_sha"] = candidate_sha
    return census, sorted(relevant, key=lambda row: row["fault_id"]), candidate_sha


def repeat_subset(candidates: Iterable[dict[str, Any]]) -> list[str]:
    """Five deterministic affected-count quantiles, filled by nearest IDs."""
    values = sorted(candidates, key=lambda row: (int(row["affected_flow_count"]), str(row["fault_id"])))
    if not values: return []
    desired = [round((len(values) - 1) * fraction) for fraction in (0, .25, .5, .75, 1)]
    selected: list[int] = []
    for index in desired:
        if index not in selected: selected.append(index)
    for index in range(len(values)):
        if len(selected) == min(5, len(values)): break
        if index not in selected: selected.append(index)
    return [str(values[index]["fault_id"]) for index in sorted(selected)]


def profile_storage(profile: dict[str, Any]) -> dict[str, Any]:
    canonical = canonical_json_bytes(profile); semantic = canonical_json_bytes(semantic_profile_projection(profile))
    return {"canonical_profile_bytes": len(canonical), "canonical_gzip_bytes": gzip_size(canonical),
            "semantic_payload_bytes": len(semantic), "semantic_gzip_bytes": gzip_size(semantic),
            "profile_sha256": hashlib.sha256(canonical).hexdigest(), "semantic_profile_hash": semantic_profile_hash(profile)}


def route_churn(healthy_routes: Iterable[dict[str, Any]], recovered_routes: Iterable[dict[str, Any]], affected: Iterable[str]) -> dict[str, Any]:
    healthy, recovered, affected_set = route_index(healthy_routes), route_index(recovered_routes), set(affected)
    changed = [flow_id for flow_id in sorted(healthy) if healthy[flow_id].get("link_path") != recovered.get(flow_id, {}).get("link_path")]
    p0_hops = sum(len(row.get("link_path", [])) for row in healthy.values()); pf_hops = sum(len(row.get("link_path", [])) for row in recovered.values())
    hop_deltas = [abs(len(healthy[flow_id].get("link_path", [])) - len(recovered[flow_id].get("link_path", []))) for flow_id in changed]
    p0_links = {link for row in healthy.values() for link in row.get("link_path", [])}; pf_links = {link for row in recovered.values() for link in row.get("link_path", [])}
    return {"changed_route_flow_count": len(changed), "changed_route_flow_ratio": len(changed) / len(healthy),
            "changed_affected_flow_count": len(set(changed) & affected_set), "changed_unaffected_flow_count": len(set(changed) - affected_set),
            "changed_unaffected_flow_ratio": len(set(changed) - affected_set) / max(len(healthy) - len(affected_set), 1),
            "total_P0_hops": p0_hops, "total_PF_hops": pf_hops, "hop_delta_total": pf_hops - p0_hops,
            "mean_hop_delta_per_changed_flow": mean_or_zero(hop_deltas), "max_hop_delta": max(hop_deltas, default=0),
            "new_physical_links_used": ";".join(sorted(pf_links - p0_links)), "P0_route_links_abandoned": ";".join(sorted(p0_links - pf_links))}


def lpt_projection(jobs: Iterable[dict[str, Any]], workers: int) -> dict[str, Any]:
    values = sorted(({"fault_id": str(row["fault_id"]), "duration_ms": float(row["total_backend_ms"])} for row in jobs),
                    key=lambda row: (-row["duration_ms"], row["fault_id"]))
    loads = [0.0] * workers
    for row in values:
        index = min(range(workers), key=lambda item: (loads[item], item)); loads[index] += row["duration_ms"]
    serial = sum(row["duration_ms"] for row in values); maximum = max((row["duration_ms"] for row in values), default=0.0)
    makespan = max(loads, default=0.0); lower = max(serial / workers, maximum)
    return {"workers": workers, "serial_work_ms": serial, "lpt_makespan_ms": makespan,
            "speedup": serial / makespan if makespan else 0.0, "parallel_efficiency": serial / (workers * makespan) if makespan else 0.0,
            "max_worker_load": max(loads, default=0.0), "min_worker_load": min(loads, default=0.0),
            "load_imbalance_ratio": max(loads, default=0.0) / (serial / workers) if serial else 0.0,
            "lower_bound_ms": lower, "lpt_to_lower_bound": makespan / lower if lower else 0.0}


def pearson(xs: Iterable[float], ys: Iterable[float]) -> float | None:
    left, right = list(xs), list(ys)
    if len(left) < 2 or len(left) != len(right): return None
    lx, ly = statistics.mean(left), statistics.mean(right)
    divisor = math.sqrt(sum((value - lx) ** 2 for value in left) * sum((value - ly) ** 2 for value in right))
    return sum((a - lx) * (b - ly) for a, b in zip(left, right)) / divisor if divisor else None


def _ranks(values: list[float]) -> list[float]:
    output = [0.0] * len(values); ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0])); start = 0
    while start < len(ordered):
        end = start
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[start][1]: end += 1
        rank = 1 + (start + end) / 2
        for position in range(start, end + 1): output[ordered[position][0]] = rank
        start = end + 1
    return output


def spearman(xs: Iterable[float], ys: Iterable[float]) -> float | None:
    left, right = list(xs), list(ys)
    return pearson(_ranks(left), _ranks(right)) if len(left) == len(right) else None


def runtime_profile_contract_audit() -> dict[str, Any]:
    """Report only a code-proven deployment payload; do not invent one."""
    scanned = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "src").rglob("*") if path.is_file())
    consumer_tokens = ("logical_routes", "stream_forwarding", "gate_schedules", "release_offsets_ns")
    consumers = []
    for path in (ROOT / "src").rglob("*"):
        if not path.is_file(): continue
        try: text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError: continue
        if any(token in text for token in consumer_tokens): consumers.append(str(path.relative_to(ROOT)))
    return {"status": "NOT_ESTABLISHED", "reason": "No complete code-level runtime activation provider/consumer contract for H2S PF profiles is established in this repository.",
            "semantic_profile_fields": list(SEMANTIC_PROFILE_FIELDS), "scanned_source_file_count": len(scanned),
            "matching_source_files": consumers, "deployment_payload_fields": None}


def resume_contract(*, implementation_commit: str, scenario_sha: str, candidate_set_sha: str,
                    backend_config: dict[str, Any], upstream_commit: str, semantic_patch_sha: str) -> dict[str, Any]:
    return {"implementation_commit": implementation_commit, "scenario_sha256": scenario_sha,
            "candidate_set_sha256": candidate_set_sha, "backend_config_sha256": canonical_sha256(backend_config),
            "upstream_commit": upstream_commit, "semantic_patch_sha256": semantic_patch_sha}


def assert_resume_contract(record: dict[str, Any], expected: dict[str, Any]) -> None:
    mismatch = {key: {"recorded": record.get(key), "expected": value} for key, value in expected.items() if record.get(key) != value}
    if mismatch: raise PfCampaignError(f"RESUME_CONTRACT_MISMATCH:{mismatch}")
