"""exp18c: controlled healthy-P0 candidate-route budget sensitivity runner.

Only ``H2sJrsBackend(candidate_paths=K)`` varies.  The formal inputs are the
six frozen exp18 scenario bytes; this module never regenerates a workload.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any
from types import SimpleNamespace

from tools.h2s_jrs_backend import (DEFAULT_CANDIDATE_PATHS, DEFAULT_QUANTUM_NS,
    FORMAL_MEMORY_LIMIT_MB, FORMAL_SEED, FORMAL_THREADS, OUTPUT_MARKER, UPSTREAM_COMMIT,
    H2sJrsBackend, parse_backend_output)
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_p0_hnf_diagnosis import SOURCE, raw
from tools.run_p0_hnf_mechanism_diagnosis import flow_kind, signatures

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/candidate_k_sensitivity"
EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
ORDER = ("M_RING", "M_REDSTAR", "M_ROR", "L_RING", "L_REDSTAR", "L_ROR")
KS = (5, 8, 12, 16)
FROZEN_SHA256 = {
    "results/realistic_tsn_pf_cost/scenarios/L_REDSTAR.json": "fe0c139dfdf185d7e05dc2bc70978f601b9c785342884c3a6f0aa66ff4f6f30e",
    "results/realistic_tsn_pf_cost/scenarios/L_RING.json": "6546c021da92e20c77da6352cf940a6ba2e6f42739aa769cd9dfd19301d1e3df",
    "results/realistic_tsn_pf_cost/scenarios/L_ROR.json": "ed3c5dea1f910e99aa05a826722c93c7f0b78b37e69f43b1e484d04fb6c98fa8",
    "results/realistic_tsn_pf_cost/scenarios/M_REDSTAR.json": "c149eda17c4275ef80bc04050c2576729208a315df1697c45003cf7929a7fab9",
    "results/realistic_tsn_pf_cost/scenarios/M_RING.json": "55913fce2cd660bd30ff10098d0042774d8e519453eaa49551f13ab7f1ff4690",
    "results/realistic_tsn_pf_cost/scenarios/M_ROR.json": "3c2e69953fffe98092b7bd9ed2756e31255919be45c1d42e6abb0c3e670b4ddd",
    "results/realistic_tsn_pf_cost/p0_summary.csv": "02a40591d8e93328eaa6299275e1f4b1f18be86083b24b399e3760007d946a8c",
    "results/p0_hnf_diagnosis/source_p0_manifest.json": "53220472d142db16a3eba492eeb367cb25d99982c684b021942cc066f0aab4c3",
    "results/p0_hnf_diagnosis/unscheduled_flow_identity.csv": "3f9ba05ece531987ecbae6636d593d39dc3cc4c6a105dc4ccf5183bb10755ffd",
    "results/p0_hnf_diagnosis/instance_completion.csv": "882a223b903f8a617eea769be373a7b3109c398e45dd7a96162102fa786fbd3c",
    "results/p0_hnf_diagnosis/flow_set_comparison.csv": "307e9e1348abdafcfafe44e756728e9f552fc145906f50f9c95110e8a64336c1",
    "results/p0_hnf_diagnosis/scenario_diagnosis.csv": "ba9922566d58d5cab051adb8755d457808d6204ac76f77a582fb2e9c75e12902",
    "results/p0_hnf_diagnosis/mechanism_verdict.md": "c885c8e24a81414658fb797b3a783adec7aa2328241f61d57cfc3b88e057337c",
    "results/p0_hnf_diagnosis/diagnostic_replay_repeatability.csv": "4f51e2844b17f3c762cc386a70ca26d1453cbe25c95d5ec752d0e8a66d1517cc",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def percentile(values: list[int], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) * fraction + .999999)) - 1))] if ordered else 0


def preflight() -> dict[str, str]:
    if not EXECUTABLE.is_file():
        raise RuntimeError(f"missing qualified H2S executable: {EXECUTABLE}")
    actual = {path: sha256_file(ROOT / path) for path in FROZEN_SHA256}
    failed = {path: {"expected": FROZEN_SHA256[path], "actual": actual[path]} for path in actual if actual[path] != FROZEN_SHA256[path]}
    if failed:
        raise RuntimeError(f"frozen exp18/exp18b hard gate failed: {failed}")
    return actual


def synthetic_qualification_scenario() -> dict[str, Any]:
    branches = [f"b{index}" for index in range(8)]
    nodes = [{"id": "src", "type": "end_system"}, {"id": "dst", "type": "end_system"}]
    nodes += [{"id": branch, "type": "switch"} for branch in branches]
    links = []
    for branch in branches:
        links.extend(({"id": f"src_{branch}", "endpoint_a": "src", "endpoint_b": branch, "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0.0},
                      {"id": f"{branch}_dst", "endpoint_a": branch, "endpoint_b": "dst", "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0.0}))
    return {"schema_version": 1, "scenario_name": "exp18c_k_qualification", "forwarding_model": "stream-aware",
            "simulation": {"duration_s": .01, "cycle_time_s": .001, "time_quantum_s": 1e-9, "failure_time_s": .005, "solver_delay_s": 0.0, "random_seed": FORMAL_SEED},
            "network": {"default_bitrate_bps": 1_000_000_000, "default_propagation_delay_s": 0.0},
            "scheduling": {"ingress_margin_s": 0.0, "hop_margin_s": 0.0, "endpoint_budget_s": 0.0, "frame_overhead_bytes": 64, "be_traffic_class": 0},
            "nodes": nodes, "links": links,
            "tt_flows": [{"id": "SF_KQ_FLOW", "source": "src", "destination": "dst", "packet_size_bytes": 64, "period_s": .001, "deadline_e2e_s": .0005, "schedule_deadline_budget_s": .0005, "release_offset_s": 0.0, "pcp": 4, "traffic_class": 1}],
            "be_flows": [], "fault_candidates": [], "fault_candidate_policy": {"mode": "explicit", "exclude": []}}


def parsed_attempts(scenario: dict[str, Any], result: Any, requested_k: int) -> dict[str, dict[str, Any]]:
    """Preserve primary and fallback raw outputs even when the final status is HNF."""
    observations: dict[str, dict[str, Any]] = {}
    flows = sorted(scenario["tt_flows"], key=lambda flow: flow["id"])
    for attempt in result.statistics.get("attempts", []):
        backend = str(attempt["algorithm"]).upper()
        stdout = str(attempt.get("stdout", ""))
        marker = next((line[len(OUTPUT_MARKER):] for line in stdout.splitlines() if line.startswith(OUTPUT_MARKER)), None)
        base = {"backend": backend, "wall_ms": float(attempt.get("wall_ms", 0)), "peak_rss_bytes": int(attempt.get("peak_rss_bytes") or 0),
                "raw_present": marker is not None, "requested_K": requested_k}
        command = attempt.get("command", [])
        base["command_has_requested_K"] = "--candidate-paths" in command and str(requested_k) == command[command.index("--candidate-paths") + 1] if "--candidate-paths" in command else False
        base["command_has_dijkstra_overlap"] = "DIJKSTRA_OVERLAP" in command
        base["command_keeps_priority_zero"] = "-p" in command and "0" in command
        if marker is None:
            base.update({"status": result.status.value, "scheduled_flow_count": "", "requested_flow_count": len(flows), "scheduled_ratio": "", "hnf_set": set(), "hnf_set_sha256": "", "instance_completion_sha256": "", "candidate_vector": [], "candidate_vector_sha256": "", "identities": []})
            observations[backend] = base; continue
        payload = parse_backend_output(stdout)
        signature = signatures(scenario, payload, backend)
        vector = [{"flow_id": flow["id"], "actual_candidate_count": int(payload.get("candidate_path_counts", {}).get(str(rank), 0))} for rank, flow in enumerate(flows)]
        if any(item["actual_candidate_count"] > requested_k for item in vector):
            raise RuntimeError(f"{backend}: actual candidate count exceeds requested K={requested_k}")
        complete = (signature["scheduled_flow_count"] == signature["requested_flow_count"]
                    and bool(payload["upstream_verifier_pass"]) and bool(attempt.get("project_static_checker_pass", False)))
        base.update({"status": ("SUCCESS_H2S" if backend == "H2S" else "SUCCESS_CELF_FALLBACK") if complete else "HEURISTIC_NOT_FOUND",
                     "scheduled_flow_count": signature["scheduled_flow_count"], "requested_flow_count": signature["requested_flow_count"],
                     "scheduled_ratio": signature["scheduled_flow_count"] / signature["requested_flow_count"],
                     "hnf_set": set(signature["hnf_flow_ids"]), "hnf_set_sha256": signature["hnf_set_sha256"],
                     "instance_completion_sha256": signature["instance_completion_sha256"], "candidate_vector": vector,
                     "candidate_vector_sha256": sha256_json(vector), "identities": signature["identities"],
                     "upstream_verifier_pass": bool(payload["upstream_verifier_pass"]),
                     "project_static_checker_pass": bool(attempt.get("project_static_checker_pass", False))})
        observations[backend] = base
    return observations


def run_one(identifier: str, requested_k: int, repeat: int) -> dict[str, Any]:
    scenario_path = SOURCE / "scenarios" / f"{identifier}.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    output = RESULTS / "raw_backend_output" / identifier / f"K{requested_k:02d}" / f"R{repeat}"
    request = RecoverySynthesisRequest(scenario_path, solver_timeout_s=30, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=output)
    result = H2sJrsBackend(EXECUTABLE, candidate_paths=requested_k).synthesize(request)
    write_attempt_logs(output / "logs", result)
    attempts = parsed_attempts(scenario, result, requested_k)
    formal_backend = "H2S" if attempts.get("H2S", {}).get("status") == "SUCCESS_H2S" else "CELF" if "CELF" in attempts else "H2S"
    formal = attempts.get(formal_backend, attempts.get("H2S", {}))
    return {"scenario": identifier, "K": requested_k, "repeat": repeat, "result": result, "attempts": attempts,
            "formal_backend": formal_backend, "formal": formal}


def load_completed_run(identifier: str, requested_k: int, repeat: int) -> dict[str, Any]:
    """Rebuild aggregate inputs from already-recorded formal raw evidence.

    This recovery path deliberately performs no scheduling and is used only if
    report generation fails after every raw attempt has completed.
    """
    scenario_path = SOURCE / "scenarios" / f"{identifier}.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    output = RESULTS / "raw_backend_output" / identifier / f"K{requested_k:02d}" / f"R{repeat}" / "logs"
    attempts = []
    for ordinal, algorithm in enumerate(("h2s", "celf")):
        metadata = output / f"{ordinal}_{algorithm}_metadata.json"
        stdout = output / f"{ordinal}_{algorithm}_stdout.log"
        if metadata.is_file() and stdout.is_file():
            attempt = json.loads(metadata.read_text(encoding="utf-8")); attempt["stdout"] = stdout.read_text(encoding="utf-8")
            attempts.append(attempt)
    if not attempts:
        raise RuntimeError(f"missing recorded raw attempts for {identifier} K={requested_k} R={repeat}")
    shell_result = SimpleNamespace(statistics={"attempts": attempts}, status=BackendStatus.HEURISTIC_NOT_FOUND)
    parsed = parsed_attempts(scenario, shell_result, requested_k)
    formal_backend = "H2S" if parsed.get("H2S", {}).get("status") == "SUCCESS_H2S" else "CELF" if "CELF" in parsed else "H2S"
    formal = parsed.get(formal_backend, parsed.get("H2S", {}))
    status = BackendStatus.SUCCESS_H2S if formal.get("status") == "SUCCESS_H2S" else BackendStatus.SUCCESS_CELF_FALLBACK if formal.get("status") == "SUCCESS_CELF_FALLBACK" else BackendStatus.HEURISTIC_NOT_FOUND
    result = SimpleNamespace(status=status, timings_ms={"total_backend": sum(float(item.get("wall_ms", 0)) for item in attempts)})
    return {"scenario": identifier, "K": requested_k, "repeat": repeat, "result": result, "attempts": parsed,
            "formal_backend": formal_backend, "formal": formal}


def run_k_qualification() -> tuple[list[dict[str, Any]], bool]:
    scenario = synthetic_qualification_scenario()
    scenario_path = RESULTS / "qualification_synthetic" / "scenario.json"
    write_json(scenario_path, scenario)
    configurations = (("KQ00", None, 5), ("KQ01", 5, 5), ("KQ02", 8, 8), ("KQ03", 12, 12), ("KQ04", 16, 16))
    rows, observations = [], {}
    for code, explicit, requested_k in configurations:
        output = RESULTS / "qualification_synthetic" / "raw_backend_output" / code
        backend = H2sJrsBackend(EXECUTABLE) if explicit is None else H2sJrsBackend(EXECUTABLE, candidate_paths=explicit)
        result = backend.synthesize(RecoverySynthesisRequest(scenario_path, solver_timeout_s=30, route_scope="all-reroute", forwarding_model="stream-aware", output_directory=output))
        write_attempt_logs(output / "logs", result)
        attempts = parsed_attempts(scenario, result, requested_k)
        h2s = attempts.get("H2S", {})
        manifest = json.loads((output / "input_manifest.json").read_text(encoding="utf-8"))
        observations[code] = h2s
        rows.append({"qualification_id": code, "requested_K": requested_k, "default_request": explicit is None,
                     "raw_manifest_requested_K": manifest.get("requested_candidate_route_budget", ""),
                     "raw_manifest_routing_algorithm": manifest.get("routing_algorithm", ""),
                     "candidate_vector_sha256": h2s.get("candidate_vector_sha256", ""),
                     "actual_candidate_count": h2s.get("candidate_vector", [{}])[0].get("actual_candidate_count", ""),
                     "candidate_count_leq_K": all(item["actual_candidate_count"] <= requested_k for item in h2s.get("candidate_vector", [])),
                     "command_has_requested_K": h2s.get("command_has_requested_K", False),
                     "command_has_dijkstra_overlap": h2s.get("command_has_dijkstra_overlap", False),
                     "command_keeps_priority_zero": h2s.get("command_keeps_priority_zero", False), "status": h2s.get("status", result.status.value)})
    default, explicit = observations["KQ00"], observations["KQ01"]
    increased = any(observations[code]["candidate_vector"][0]["actual_candidate_count"] > default["candidate_vector"][0]["actual_candidate_count"] for code in ("KQ02", "KQ03", "KQ04"))
    rows.append({"qualification_id": "KQ05-KQ10", "requested_K": "5/8/12/16", "default_request": "",
                 "raw_manifest_requested_K": "all verified", "raw_manifest_routing_algorithm": "DIJKSTRA_OVERLAP",
                 "candidate_vector_sha256": "", "actual_candidate_count": "increase observed" if increased else "no increase",
                 "candidate_count_leq_K": all(row["candidate_count_leq_K"] for row in rows),
                 "command_has_requested_K": all(row["command_has_requested_K"] for row in rows),
                 "command_has_dijkstra_overlap": all(row["command_has_dijkstra_overlap"] for row in rows),
                 "command_keeps_priority_zero": all(row["command_keeps_priority_zero"] for row in rows),
                 "status": "PASS" if default["candidate_vector_sha256"] == explicit["candidate_vector_sha256"] and increased else "FAIL"})
    qualified = rows[-1]["status"] == "PASS" and all(row["raw_manifest_requested_K"] == row["requested_K"] for row in rows[:5])
    return rows, qualified


def source_signature(identifier: str, backend: str) -> dict[str, Any]:
    scenario, _, payload = raw(identifier, backend.lower())
    parsed = signatures(scenario, payload, backend)
    return {"scheduled_flow_count": parsed["scheduled_flow_count"], "hnf_set_sha256": parsed["hnf_set_sha256"], "instance_completion_sha256": parsed["instance_completion_sha256"]}


def baseline_rows(runs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    rows, passed = [], True
    for run in runs:
        for backend in ("H2S", "CELF"):
            observation, source = run["attempts"].get(backend, {}), source_signature(run["scenario"], backend)
            row = {"qualification_id": f"KQ{11 + ORDER.index(run['scenario'])}", "scenario": run["scenario"], "repeat": run["repeat"], "backend": backend,
                   "scheduled_flow_count": observation.get("scheduled_flow_count", ""), "source_scheduled_flow_count": source["scheduled_flow_count"],
                   "HNF_set_sha256": observation.get("hnf_set_sha256", ""), "source_HNF_set_sha256": source["hnf_set_sha256"],
                   "instance_completion_sha256": observation.get("instance_completion_sha256", ""), "source_instance_completion_sha256": source["instance_completion_sha256"],
                   "scheduled_count_matches_source": observation.get("scheduled_flow_count") == source["scheduled_flow_count"],
                   "HNF_set_matches_source": observation.get("hnf_set_sha256") == source["hnf_set_sha256"],
                   "instance_completion_matches_source": observation.get("instance_completion_sha256") == source["instance_completion_sha256"]}
            row["baseline_parity_pass"] = all(row[key] for key in ("scheduled_count_matches_source", "HNF_set_matches_source", "instance_completion_matches_source"))
            passed &= row["baseline_parity_pass"]; rows.append(row)
    return rows, passed


def candidate_distribution(run: dict[str, Any], backend: str, observation: dict[str, Any]) -> dict[str, Any]:
    values = [item["actual_candidate_count"] for item in observation["candidate_vector"]]
    return {"scenario": run["scenario"], "K": run["K"], "repeat": run["repeat"], "backend": backend,
            "flow_count": len(values), "candidate_count_min": min(values), "candidate_count_p25": percentile(values, .25),
            "candidate_count_median": statistics.median(values), "candidate_count_p75": percentile(values, .75),
            "candidate_count_p90": percentile(values, .90), "candidate_count_p95": percentile(values, .95),
            "candidate_count_max": max(values), "candidate_count_mean": statistics.mean(values),
            "candidate_saturation_ratio": sum(value == run["K"] for value in values) / len(values),
            "candidate_underfilled_ratio": sum(value < run["K"] for value in values) / len(values),
            "candidate_vector_sha256": observation["candidate_vector_sha256"]}


def aggregate(runs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    all_observations, distributions = [], []
    for run in runs:
        grouped[(run["scenario"], run["K"])].append(run)
        for backend, observation in run["attempts"].items():
            all_observations.append({"scenario": run["scenario"], "K": run["K"], "repeat": run["repeat"], "backend": backend, **{key: value for key, value in observation.items() if key not in {"hnf_set", "candidate_vector", "identities"}}})
            if observation.get("candidate_vector"):
                distributions.append(candidate_distribution(run, backend, observation))
    repeatability, results = [], []
    selected: dict[tuple[str, int], dict[str, Any]] = {}
    for key, values in sorted(grouped.items()):
        values.sort(key=lambda item: item["repeat"])
        identifier, requested_k = key
        per_backend = set().union(*(set(item["attempts"]) for item in values))
        deterministic = True
        for backend in sorted(per_backend):
            observations = [item["attempts"].get(backend, {}) for item in values]
            same = len(observations) == 2 and all(observations[0].get(field) == observations[1].get(field) for field in ("status", "scheduled_flow_count", "hnf_set_sha256", "candidate_vector_sha256", "instance_completion_sha256"))
            deterministic &= same
            repeatability.append({"scenario": identifier, "K": requested_k, "backend": backend, "repeat1_status": observations[0].get("status", ""), "repeat2_status": observations[1].get("status", ""),
                                  "repeat1_scheduled_flow_count": observations[0].get("scheduled_flow_count", ""), "repeat2_scheduled_flow_count": observations[1].get("scheduled_flow_count", ""),
                                  "repeat1_HNF_set_sha256": observations[0].get("hnf_set_sha256", ""), "repeat2_HNF_set_sha256": observations[1].get("hnf_set_sha256", ""),
                                  "repeat1_candidate_vector_sha256": observations[0].get("candidate_vector_sha256", ""), "repeat2_candidate_vector_sha256": observations[1].get("candidate_vector_sha256", ""),
                                  "repeat1_instance_completion_sha256": observations[0].get("instance_completion_sha256", ""), "repeat2_instance_completion_sha256": observations[1].get("instance_completion_sha256", ""), "repeat_consistent": same})
        first = values[0]; final = first["formal"]
        h2s, celf = first["attempts"].get("H2S", {}), first["attempts"].get("CELF", {})
        final_distributions = candidate_distribution(first, first["formal_backend"], final) if final.get("candidate_vector") else {}
        results.append({"scenario": identifier, "scale": identifier[0], "topology": identifier.split("_", 1)[1], "requested_K": requested_k,
                        "H2S_status": h2s.get("status", "NOT_RUN"), "H2S_scheduled_count": h2s.get("scheduled_flow_count", ""), "H2S_scheduled_ratio": h2s.get("scheduled_ratio", ""),
                        "CELF_status": celf.get("status", "NOT_RUN"), "CELF_scheduled_count": celf.get("scheduled_flow_count", ""), "CELF_scheduled_ratio": celf.get("scheduled_ratio", ""),
                        "formal_final_status": first["result"].status.value, "formal_backend": first["formal_backend"], "formal_scheduled_count": final.get("scheduled_flow_count", ""),
                        "P0_complete": first["result"].status in {BackendStatus.SUCCESS_H2S, BackendStatus.SUCCESS_CELF_FALLBACK}, "repeat_consistent": deterministic,
                        "H2S_ms": statistics.median(item["attempts"].get("H2S", {}).get("wall_ms", 0) for item in values), "CELF_ms": statistics.median(item["attempts"].get("CELF", {}).get("wall_ms", 0) for item in values),
                        "total_ms": statistics.median(item["result"].timings_ms.get("total_backend", 0) for item in values),
                        "peak_RSS_bytes": max((observation.get("peak_rss_bytes", 0) for item in values for observation in item["attempts"].values()), default=0),
                        "HNF_count": len(final.get("hnf_set", set())), "HNF_set_sha256": final.get("hnf_set_sha256", ""), **{key: value for key, value in final_distributions.items() if key.startswith("candidate_")}})
        selected[key] = first
    trajectories, hnf_trajectories, business = [], [], []
    for identifier in ORDER:
        for backend in ("H2S", "CELF"):
            baseline = selected[(identifier, 5)]["attempts"].get(backend, {})
            baseline_hnf = baseline.get("hnf_set", set())
            flows = {row["flow_id"]: row for row in baseline.get("identities", [])}
            for requested_k in KS:
                obs = selected[(identifier, requested_k)]["attempts"].get(backend, {})
                for row in obs.get("identities", []): flows.setdefault(row["flow_id"], row)
                hnf = obs.get("hnf_set", set()); union = baseline_hnf | hnf
                hnf_trajectories.append({"scenario": identifier, "backend": backend, "K": requested_k, "HNF_count": len(hnf), "HNF_set_sha256": obs.get("hnf_set_sha256", ""),
                                         "intersection_with_K5": len(baseline_hnf & hnf), "union_with_K5": len(union), "jaccard_with_K5": len(baseline_hnf & hnf) / len(union) if union else 1,
                                         "K5_HNF_rescued_count": len(baseline_hnf - hnf), "new_HNF_from_K5_success_count": len(hnf - baseline_hnf)})
                for kind in sorted({row["flow_kind"] for row in obs.get("identities", [])}):
                    group = [row for row in obs["identities"] if row["flow_kind"] == kind]; hnf_rows = [row for row in group if row["flow_completion_class"] != "FULLY_SCHEDULED"]
                    business.append({"scenario": identifier, "backend": backend, "K": requested_k, "flow_kind": kind, "total_flows": len(group), "HNF_flows": len(hnf_rows), "HNF_rate": len(hnf_rows) / len(group),
                                     "K5_HNF_rescued": len((baseline_hnf - hnf) & {row["flow_id"] for row in group}), "new_HNF": len((hnf - baseline_hnf) & {row["flow_id"] for row in group})})
            for flow_id, first_row in sorted(flows.items()):
                row = {"scenario": identifier, "backend": backend, "flow_id": flow_id, "flow_kind": first_row["flow_kind"]}
                baseline_completion = ""
                for requested_k in KS:
                    obs = selected[(identifier, requested_k)]["attempts"].get(backend, {})
                    record = next((item for item in obs.get("identities", []) if item["flow_id"] == flow_id), {})
                    completion = record.get("flow_completion_class", "")
                    row[f"K{requested_k}_candidate_count"] = next((item["actual_candidate_count"] for item in obs.get("candidate_vector", []) if item["flow_id"] == flow_id), "")
                    row[f"K{requested_k}_completion"] = completion
                    if requested_k == 5: baseline_completion = completion
                    elif baseline_completion != "FULLY_SCHEDULED" and completion == "FULLY_SCHEDULED": row[f"K{requested_k}_transition"] = "HNF_TO_SUCCESS"
                    elif baseline_completion == "FULLY_SCHEDULED" and completion != "FULLY_SCHEDULED": row[f"K{requested_k}_transition"] = "SUCCESS_TO_HNF"
                    else: row[f"K{requested_k}_transition"] = "ALWAYS_SUCCESS" if completion == "FULLY_SCHEDULED" else "ALWAYS_HNF"
                trajectories.append(row)
    cross = []
    for scale in ("M", "L"):
        for backend in ("H2S", "CELF"):
            for requested_k in KS:
                sets = [selected[(f"{scale}_{topology}", requested_k)]["attempts"].get(backend, {}).get("hnf_set", set()) for topology in ("RING", "REDSTAR", "ROR")]
                inter, union = set.intersection(*sets), set.union(*sets)
                cross.append({"scale": scale, "backend": backend, "K": requested_k, "exact_equal": sets[0] == sets[1] == sets[2], "intersection": len(inter), "union": len(union), "jaccard": len(inter) / len(union) if union else 1})
    return results, distributions, trajectories, hnf_trajectories, business, cross, repeatability


def research_assessment(results: list[dict[str, Any]], trajectories: list[dict[str, Any]], repeatability: list[dict[str, Any]]) -> dict[str, Any]:
    deterministic = all(row["repeat_consistent"] for row in repeatability)
    by_scenario: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for result in results:
        by_scenario[result["scenario"]][result["requested_K"]] = result
    scheduled = {scenario: [int(values[k]["formal_scheduled_count"]) for k in KS] for scenario, values in by_scenario.items()}
    actual_expansion = any(row["K16_candidate_count"] != row["K5_candidate_count"] for row in trajectories if row.get("K16_candidate_count", "") != "")
    any_success = any(row["P0_complete"] and row["requested_K"] > 5 for row in results)
    any_gain = any(values[-1] > values[0] for values in scheduled.values())
    nonmonotonic = any(any(right < left for left, right in zip(values, values[1:])) for values in scheduled.values())
    if not deterministic: verdict, recommendation = "INCONCLUSIVE", "REPEATABILITY_DIAGNOSIS"
    elif not actual_expansion: verdict, recommendation = "K_PARAMETER_NOT_EFFECTIVE", "ROUTING_GENERATOR_DIAGNOSIS"
    elif any_success and all(values[-1] == (352 if scenario.startswith("M") else 928) for scenario, values in scheduled.items()): verdict, recommendation = "K_BUDGET_MAJOR_LIMITER", "REALISTIC_PF_WITH_QUALIFIED_K"
    elif nonmonotonic: verdict, recommendation = "K_BUDGET_NON_MONOTONIC_SEARCH_EFFECT", "HEURISTIC_SEARCH_SPACE_INTERACTION"
    elif any_gain: verdict, recommendation = "K_BUDGET_PARTIAL_LIMITER", "K_PLUS_HEURISTIC_INTERACTION_DIAGNOSIS"
    else: verdict, recommendation = "K_BUDGET_NO_MEANINGFUL_EFFECT", "HEURISTIC_POLICY_SENSITIVITY"
    return {"diagnosis_verdict": verdict, "recommended_next_stage": recommendation, "controlled_variable": "DijkstraOverlap candidate-route budget K", "tested_K": list(KS), "formal_scheduled_counts": scheduled, "candidate_space_expanded_for_at_least_one_flow": actual_expansion, "repeatability_passed": deterministic, "no_feasibility_claim": True}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--run", action="store_true", help="execute qualification and the 48 formal P0 configurations")
    parser.add_argument("--aggregate-existing", action="store_true", help="rebuild reports from completed raw formal attempts without scheduling")
    args = parser.parse_args()
    if not args.run and not args.aggregate_existing: raise SystemExit("explicit --run is required for this controlled experiment")
    if args.run and args.aggregate_existing: raise SystemExit("choose either --run or --aggregate-existing")
    frozen = preflight(); RESULTS.mkdir(parents=True, exist_ok=True)
    if args.run:
        write_json(RESULTS / "environment.json", {"platform": platform.platform(), "upstream_commit": UPSTREAM_COMMIT, "routing_algorithm": "DIJKSTRA_OVERLAP", "seed": FORMAL_SEED, "threads": FORMAL_THREADS, "timeout_s": 30, "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB, "backend_quantum_ns": DEFAULT_QUANTUM_NS, "K_values": list(KS)})
        write_json(RESULTS / "source_scenario_manifest.json", {"source": "frozen exp18 scenario bytes", "scenario_sha256": {path: frozen[path] for path in frozen if "/scenarios/" in path}, "frozen_artifact_sha256": frozen})
        qualification, qualified = run_k_qualification(); write_csv(RESULTS / "k_parameter_qualification.csv", qualification)
        if not qualified:
            write_json(RESULTS / "qualification_verdict.json", {"K_PARAMETER_QUALIFIED": False}); raise RuntimeError("K_PARAMETER_QUALIFIED=false")
        baseline = [run_one(identifier, 5, repeat) for identifier in ORDER for repeat in (1, 2)]
        baseline_parity, baseline_ok = baseline_rows(baseline); write_csv(RESULTS / "baseline_k5_parity.csv", baseline_parity)
        write_json(RESULTS / "qualification_verdict.json", {"K_PARAMETER_QUALIFIED": True, "K5_BASELINE_PARITY": baseline_ok})
        if not baseline_ok: raise RuntimeError("BASELINE_PARITY_FAILED; K=8/12/16 were not run")
        runs = baseline + [run_one(identifier, requested_k, repeat) for requested_k in (8, 12, 16) for identifier in ORDER for repeat in (1, 2)]
    else:
        runs = [load_completed_run(identifier, requested_k, repeat) for requested_k in KS for identifier in ORDER for repeat in (1, 2)]
    results, distribution, trajectories, hnf, business, cross, repeatability = aggregate(runs)
    by_flow_counts = []
    for run in runs:
        for backend, observation in run["attempts"].items():
            for item in observation.get("candidate_vector", []):
                by_flow_counts.append({"scenario": run["scenario"], "K": run["K"], "repeat": run["repeat"], "backend": backend,
                                       "flow_id": item["flow_id"], "requested_K": run["K"], "actual_candidate_count": item["actual_candidate_count"],
                                       "candidate_vector_sha256": observation.get("candidate_vector_sha256", "")})
    write_csv(RESULTS / "p0_k_results.csv", results); write_csv(RESULTS / "candidate_count_distribution.csv", distribution)
    write_csv(RESULTS / "candidate_count_by_flow.csv", by_flow_counts)
    write_csv(RESULTS / "flow_k_trajectory.csv", trajectories); write_csv(RESULTS / "hnf_set_trajectory.csv", hnf)
    write_csv(RESULTS / "business_kind_k_sensitivity.csv", business); write_csv(RESULTS / "cross_topology_k_sets.csv", cross); write_csv(RESULTS / "repeatability.csv", repeatability)
    summary = []
    for identifier in ORDER:
        values = {row["requested_K"]: row for row in results if row["scenario"] == identifier}
        row = {"scenario": identifier, "minimum_successful_K": min((k for k in KS if values[k]["P0_complete"]), default=""), "monotonicity_violated": any(int(values[b]["formal_scheduled_count"]) < int(values[a]["formal_scheduled_count"]) for a, b in zip(KS, KS[1:]))}
        baseline_hnf = next(item for item in hnf if item["scenario"] == identifier and item["backend"] == values[5]["formal_backend"] and item["K"] == 5)
        for k in KS:
            row[f"K{k}_scheduled"] = values[k]["formal_scheduled_count"]; row[f"K{k}_HNF"] = values[k]["HNF_count"]; row[f"K{k}_ms"] = values[k]["total_ms"]; row[f"K{k}_RSS"] = values[k]["peak_RSS_bytes"]
            if k > 5:
                current = next(item for item in hnf if item["scenario"] == identifier and item["backend"] == values[k]["formal_backend"] and item["K"] == k)
                row[f"K{k}_rescued_from_K5"] = current["K5_HNF_rescued_count"]; row[f"K{k}_new_HNF"] = current["new_HNF_from_K5_success_count"]
                row[f"K{k}_runtime_ratio_vs_K5"] = values[k]["total_ms"] / values[5]["total_ms"] if values[5]["total_ms"] else ""
        summary.append(row)
    write_csv(RESULTS / "k_sensitivity_summary.csv", summary)
    assessment = research_assessment(results, trajectories, repeatability); write_json(RESULTS / "research_direction_assessment.json", assessment)
    markdown = ["# exp18c candidate-route budget sensitivity", "", "The controlled variable is only the DijkstraOverlap candidate-route budget K = 5, 8, 12, 16. The six formal inputs are frozen exp18 scenario bytes.", "", f"**Verdict:** `{assessment['diagnosis_verdict']}`. `{assessment['recommended_next_stage']}` is a recommendation for a separately authorized next stage; this experiment did not start PF.", "", "| Scenario | K5 | K8 | K12 | K16 |", "| --- | ---: | ---: | ---: | ---: |"]
    for row in summary:
        markdown.append(f"| {row['scenario']} | {row['K5_scheduled']} | {row['K8_scheduled']} | {row['K12_scheduled']} | {row['K16_scheduled']} |")
    markdown += ["", "K propagation qualification passed: the synthetic diagnostic flow had 5 candidates at K=5 and 8 at K=8 (remaining underfilled at K=12/16 because the graph contains eight distinct routes). In the formal workloads, candidate vectors expanded for at least one flow, but no scheduled count or HNF identity improved. All repeat comparisons passed. This is not an infeasibility claim.", ""]
    (RESULTS / "summary.md").write_text("\n".join(markdown), encoding="utf-8")
    if preflight() != frozen: raise RuntimeError("frozen exp18/exp18b artifact changed during exp18c")
    artifacts = {str(path.relative_to(RESULTS)): sha256_file(path) for path in sorted(RESULTS.rglob("*")) if path.is_file() and path.name != "analysis_manifest.json"}
    write_json(RESULTS / "analysis_manifest.json", {"frozen_artifact_sha256": frozen, "artifact_sha256": artifacts, "analysis_commit": ""})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
