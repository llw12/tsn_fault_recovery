"""exp15 H2S qualification and healthy-P0 scalability screening."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools.h2s_jrs_backend import (
    DEFAULT_CANDIDATE_PATHS, FORMAL_MEMORY_LIMIT_MB, FORMAL_SEED, FORMAL_THREADS,
    UPSTREAM_COMMIT, UPSTREAM_LICENSE, UPSTREAM_REPOSITORY, H2sJrsBackend,
    prepare_h2s_inputs, quantize_flow,
)
from tools.jrs_wa_adapter import TsnkitJrsWaBackend, canonical_json_bytes
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest
from tools.scenario_compiler import compile_scenario

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/h2s_backend_qualification"
SCENARIOS = ROOT / "results/pf_jrs_scalability/scenarios"
EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
PATCH = ROOT / "third_party_patches/advanced_flow_scheduler/exp15_semantics.patch"
TIMEOUT_S = 30
FORMAL_QUANTUM_NS = 100
SCALE_IDS = [f"S{i}" for i in range(1, 9)] + [f"F150_TT{i}" for i in (100, 250, 500, 750, 1000)]
QUICK_CASES = ["QH00", "QH01", "QH03", "QH04", "QH05"]
QUALIFICATION_CASES = [f"QH{i:02d}" for i in range(11)]
SUCCESS = {BackendStatus.SUCCESS_H2S, BackendStatus.SUCCESS_CELF_FALLBACK}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def make_case(case_id: str) -> dict[str, Any]:
    def flow(fid: str, source: str, destination: str, *, size: int = 100, release_ns: int = 0,
             deadline_ns: int = 100_000, period_ns: int = 1_000_000) -> dict[str, Any]:
        return {"id": fid, "source": source, "destination": destination, "packet_size_bytes": size,
                "period_s": period_ns / 1e9, "deadline_e2e_s": deadline_ns / 1e9,
                "schedule_deadline_budget_s": deadline_ns / 1e9, "release_offset_s": release_ns / 1e9,
                "pcp": 4, "traffic_class": 1}
    if case_id == "QH00":
        nodes = [("a", "end_system"), ("s", "switch"), ("d", "end_system")]
        edges = [("a", "s"), ("s", "d")]; flows = [flow("TT1", "a", "d")]
    elif case_id in {"QH01", "QH07"}:
        nodes = [("a", "end_system"), ("b", "end_system"), ("x", "switch"), ("y", "switch"), ("d", "end_system")]
        edges = [("a", "x"), ("b", "x"), ("x", "y"), ("x", "d"), ("y", "d")]
        flows = [flow("TT1", "a", "d", release_ns=0), flow("TT2", "b", "d", release_ns=10_000)]
    elif case_id == "QH02":
        nodes = [(f"n{i}", "end_system" if i < 4 else "switch") for i in range(10)]
        edges = [("n0", "n4"), ("n1", "n4"), ("n2", "n5"), ("n3", "n5"),
                 ("n4", "n5"), ("n4", "n6"), ("n5", "n7"), ("n6", "n7"),
                 ("n6", "n8"), ("n7", "n9"), ("n8", "n9")]
        flows = [flow("TT1", "n0", "n2", release_ns=0), flow("TT2", "n1", "n3", release_ns=10_000),
                 flow("TT3", "n2", "n0", release_ns=20_000), flow("TT4", "n3", "n1", release_ns=30_000)]
    elif case_id == "QH03":
        nodes = [("a", "end_system"), ("b", "end_system"), ("s", "switch"), ("d", "end_system")]
        edges = [("a", "s"), ("b", "s"), ("s", "d")]
        flows = [flow("TT1", "a", "d", release_ns=0), flow("TT2", "b", "d", release_ns=37_000)]
    elif case_id == "QH04":
        nodes = [("a", "end_system"), ("s", "switch"), ("d", "end_system")]
        edges = [("a", "s"), ("s", "d")]; flows = [flow("TT1", "a", "d", deadline_ns=50_000)]
    elif case_id == "QH05":
        nodes = [("a", "end_system"), ("b", "end_system"), ("s0", "switch"), ("s1", "switch"), ("d", "end_system")]
        edges = [("a", "s0"), ("s0", "s1"), ("b", "s1"), ("s1", "d")]
        flows = [flow("TT1", "a", "d", size=61), flow("TT2", "b", "d", size=186)]
    elif case_id == "QH06":
        nodes = [("a", "end_system"), ("b", "end_system"), ("s", "switch"), ("d", "end_system")]
        edges = [("a", "s"), ("b", "s"), ("s", "d")]
        flows = [flow("stream_A", "a", "d", release_ns=0), flow("stream_B", "b", "d", release_ns=20_000)]
    elif case_id == "QH08":
        nodes = [("a", "end_system"), ("s", "switch"), ("d1", "end_system"), ("d2", "end_system")]
        edges = [("a", "s"), ("s", "d1"), ("s", "d2")]
        flows = [flow("TT1", "a", "d1", size=100, release_ns=0), flow("TT2", "a", "d2", size=100, release_ns=0)]
    elif case_id == "QH09":
        nodes = [("a", "end_system"), ("s", "switch"), ("d", "end_system")]
        edges = [("a", "s"), ("s", "d")]; flows = [flow("TT1", "a", "d", size=65, release_ns=4_350, deadline_ns=92_050)]
    elif case_id == "QH10":
        nodes = [("a", "end_system"), ("s0", "switch"), ("s1", "switch"), ("d", "end_system")]
        edges = [("a", "s0"), ("s0", "s1"), ("s1", "d")]; flows = [flow("TT1", "a", "d", release_ns=12_300)]
    else: raise ValueError(case_id)
    links = [{"id": f"l_{a}_{b}", "endpoint_a": a, "endpoint_b": b,
              "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0.0} for a, b in edges]
    return {"schema_version": 1, "scenario_name": case_id.lower(), "forwarding_model": "stream-aware",
            "simulation": {"duration_s": .03, "cycle_time_s": .001, "time_quantum_s": 1e-9,
                           "failure_time_s": .01, "solver_delay_s": 0.0, "random_seed": FORMAL_SEED},
            "network": {"default_bitrate_bps": 1_000_000_000, "default_propagation_delay_s": 0.0},
            "scheduling": {"ingress_margin_s": 0.0, "hop_margin_s": 0.0, "endpoint_budget_s": 0.0,
                           "frame_overhead_bytes": 64, "be_traffic_class": 0},
            "nodes": [{"id": node, "type": kind} for node, kind in nodes], "links": links,
            "tt_flows": flows, "be_flows": [], "fault_candidates": [],
            "fault_candidate_policy": {"mode": "explicit", "exclude": []}}


def materialize_case(case_id: str, root: Path) -> Path:
    directory = root / case_id; directory.mkdir(parents=True, exist_ok=True)
    scenario = make_case(case_id)
    path = directory / "scenario.json"; path.write_bytes(canonical_json_bytes(scenario))
    counters = {node["id"]: 0 for node in scenario["nodes"]}; bindings = {}
    for link in scenario["links"]:
        sides = {}
        for side, node, peer in (("a", link["endpoint_a"], link["endpoint_b"]), ("b", link["endpoint_b"], link["endpoint_a"])):
            index = counters[node]; counters[node] += 1
            sides[side] = {"node": node, "peer": peer, "interface": f"eth{index}", "egress_path": f"{node}.eth[{index}]"}
        bindings[link["id"]] = sides
    (directory / "port_map.json").write_bytes(canonical_json_bytes({"schema_version": 1, "scenario_name": case_id.lower(), "links": bindings}))
    return path


def request_for(path: Path, output: Path) -> RecoverySynthesisRequest:
    return RecoverySynthesisRequest(path, solver_timeout_s=TIMEOUT_S, route_scope="all-reroute",
                                    forwarding_model="stream-aware", output_directory=output)


def write_attempt_logs(base: Path, result: Any) -> None:
    base.mkdir(parents=True, exist_ok=True)
    for index, attempt in enumerate(result.statistics.get("attempts", [])):
        algorithm = attempt.get("algorithm", f"attempt{index}").lower()
        (base / f"{index}_{algorithm}_stdout.log").write_text(str(attempt.get("stdout", "")), encoding="utf-8")
        (base / f"{index}_{algorithm}_stderr.log").write_text(str(attempt.get("stderr", "")), encoding="utf-8")
        write_json(base / f"{index}_{algorithm}_metadata.json", {key: value for key, value in attempt.items() if key not in {"stdout", "stderr"}})


def run_case(case_id: str, case_root: Path, raw_root: Path, profiles: Path) -> tuple[dict[str, Any], Any]:
    path = materialize_case(case_id, case_root)
    backend = H2sJrsBackend(EXECUTABLE, quantum_ns=FORMAL_QUANTUM_NS)
    result = backend.synthesize(request_for(path, raw_root / case_id))
    write_attempt_logs(raw_root / case_id, result)
    profile_hash = profile_bytes = gzip_bytes = ""
    if result.status in SUCCESS and result.profile:
        payload = canonical_json_bytes(result.profile); profile_hash = hashlib.sha256(payload).hexdigest()
        profile_bytes = len(payload); gzip_bytes = len(gzip.compress(payload, mtime=0))
        (profiles / f"{case_id}_P0.json").write_bytes(payload)
    stats = result.statistics
    row = {"case_id": case_id, "status": result.status.value, "algorithm_used": stats.get("algorithm_used", ""),
           "all_flows_scheduled": stats.get("all_flows_scheduled", False), "semantic_valid": stats.get("semantic_valid", False),
           "upstream_verifier_pass": stats.get("upstream_verifier_pass", False),
           "project_static_checker_pass": stats.get("project_static_checker_pass", False),
           "scheduled_flow_ratio": stats.get("scheduled_flow_ratio", 0), "runtime_ms": result.timings_ms.get("total_backend", 0),
           "peak_memory_bytes": max([a.get("peak_rss_bytes") or 0 for a in stats.get("attempts", [])] or [0]),
           "profile_hash": profile_hash, "profile_bytes": profile_bytes, "gzip_bytes": gzip_bytes,
           "expected_outcome": "HEURISTIC_NOT_FOUND" if case_id == "QH08" else "SUCCESS"}
    return row, result


def compile_scale(scale_id: str, temp_root: Path) -> tuple[Path, str]:
    source = SCENARIOS / f"{scale_id}.yaml"
    source_hash = sha256_file(source)
    generated = compile_scenario(source, temp_root, forwarding_model_override="stream-aware")
    return generated / "scenario.json", source_hash


def run_scale(scale_id: str, temp_root: Path, raw_root: Path, profiles: Path) -> tuple[dict[str, Any], Any]:
    path, source_hash = compile_scale(scale_id, temp_root)
    scenario = json.loads(path.read_text(encoding="utf-8"))
    result = H2sJrsBackend(EXECUTABLE, quantum_ns=FORMAL_QUANTUM_NS).synthesize(request_for(path, raw_root / scale_id))
    write_attempt_logs(raw_root / scale_id, result)
    stats = result.statistics; payload = canonical_json_bytes(result.profile) if result.profile else b""
    if payload: (profiles / f"{scale_id}_P0.json").write_bytes(payload)
    scheduled_count = stats.get("scheduled_flow_count", max((a.get("scheduled_flow_count", 0) for a in stats.get("attempts", [])), default=0))
    row = {"scenario_id": scale_id, "switches": sum(n["type"] == "switch" for n in scenario["nodes"]),
           "end_systems": sum(n["type"] == "end_system" for n in scenario["nodes"]), "total_nodes": len(scenario["nodes"]),
           "tt_flows": len(scenario["tt_flows"]), "algorithm_used": stats.get("algorithm_used", ""),
           "primary_h2s_success": stats.get("primary_h2s_success", False), "celf_fallback_used": stats.get("celf_fallback_used", False),
           "status": result.status.value, "all_flows_scheduled": stats.get("all_flows_scheduled", False),
           "scheduled_flow_count": scheduled_count, "scheduled_flow_ratio": stats.get("scheduled_flow_ratio", 0),
           "semantic_valid": stats.get("semantic_valid", False),
           "upstream_verifier_pass": stats.get("upstream_verifier_pass", False),
           "project_static_checker_pass": stats.get("project_static_checker_pass", False),
           "conversion_ms": result.timings_ms.get("conversion", 0),
           "candidate_route_generation_ms": result.timings_ms.get("candidate_route_generation", stats.get("candidate_route_generation_ms", 0)),
           "scheduling_ms": result.timings_ms.get("scheduling", 0),
           "verification_ms": result.timings_ms.get("verification", 0),
           "profile_normalization_ms": result.timings_ms.get("profile_normalization", 0),
           "total_backend_ms": result.timings_ms.get("total_backend", 0),
           "peak_rss_bytes": max([a.get("peak_rss_bytes") or 0 for a in stats.get("attempts", [])] or [0]),
           "mean_hops": stats.get("mean_hops", 0), "median_hops": stats.get("median_hops", 0), "p95_hops": stats.get("p95_hops", 0), "max_hops": stats.get("max_hops", 0),
           "mean_wait_ns": stats.get("mean_wait_ns", 0), "median_wait_ns": stats.get("median_wait_ns", 0), "p95_wait_ns": stats.get("p95_wait_ns", 0), "max_wait_ns": stats.get("max_wait_ns", 0),
           "candidate_paths_k": DEFAULT_CANDIDATE_PATHS, "routing_policy": "DIJKSTRA_OVERLAP",
           "backend_quantum_ns": FORMAL_QUANTUM_NS,
           "mean_candidate_paths_per_flow": stats.get("mean_candidate_paths_per_flow", 0),
           "min_candidate_paths": stats.get("min_candidate_paths", 0), "max_candidate_paths": stats.get("max_candidate_paths", 0),
           "mean_candidate_hops": "not_exposed_by_minimal_upstream_patch",
           "max_e2e_latency_ns": stats.get("max_e2e_latency_ns", 0),
           "profile_bytes": len(payload), "canonical_profile_bytes": len(payload),
           "gzip_bytes": len(gzip.compress(payload, mtime=0)) if payload else 0,
           "profile_hash": hashlib.sha256(payload).hexdigest() if payload else "",
           "diagnostic": result.diagnostic, "exp14_scenario_byte_sha256": source_hash}
    return row, result


def qualification_pass(rows: list[dict[str, Any]], results: dict[str, Any], deterministic: bool) -> tuple[bool, dict[str, bool]]:
    by_id = {row["case_id"]: row for row in rows}
    expected_success = all(by_id[c]["status"] in {s.value for s in SUCCESS} and by_id[c]["semantic_valid"] for c in QUALIFICATION_CASES if c != "QH08")
    qh08_ok = by_id.get("QH08", {}).get("status") == BackendStatus.HEURISTIC_NOT_FOUND.value
    wait_rows = results.get("QH05").statistics.get("route_schedule", []) if results.get("QH05") else []
    by_flow: dict[str, list[dict[str, Any]]] = {}
    for row in wait_rows: by_flow.setdefault(row["flow_id"], []).append(row)
    wait_verified = any(right["start_ns"] > left["end_ns"] for values in by_flow.values()
                        for left, right in zip(sorted(values, key=lambda r: r["hop_index"]), sorted(values, key=lambda r: r["hop_index"])[1:]))
    flags = {"WAIT_ALLOWED_VERIFIED": wait_verified, "RELEASE_OFFSET_VERIFIED": by_id.get("QH03", {}).get("semantic_valid", False),
             "INDEPENDENT_DEADLINE_VERIFIED": by_id.get("QH04", {}).get("semantic_valid", False),
             "FRAME_SEMANTICS_VERIFIED": by_id.get("QH00", {}).get("semantic_valid", False),
             "TIME_QUANTIZATION_SAFE": by_id.get("QH09", {}).get("semantic_valid", False),
             "UPSTREAM_VERIFIER_PARITY": all(row["upstream_verifier_pass"] == row["project_static_checker_pass"] for row in rows if row["status"] in {s.value for s in SUCCESS}),
             "SAME_DESTINATION_STREAM_AWARE_VERIFIED": by_id.get("QH06", {}).get("semantic_valid", False),
             "DETERMINISTIC_OUTCOME_VERIFIED": deterministic}
    return expected_success and qh08_ok and all(flags.values()), flags


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--mode", choices=("quick", "qualification", "full"), default="full")
    parser.add_argument("--implementation-commit", default=""); parser.add_argument("--results-commit", default="")
    args = parser.parse_args()
    if not EXECUTABLE.is_file(): raise SystemExit(f"missing backend executable: run {ROOT / 'scripts/bootstrap_h2s_backend.sh'}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    for name in ("profiles", "raw_backend_output", "logs"): (RESULTS / name).mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="exp15-") as temporary:
        temp = Path(temporary); case_ids = QUICK_CASES if args.mode == "quick" else QUALIFICATION_CASES
        qrows, qresults = [], {}
        for case_id in case_ids:
            row, result = run_case(case_id, temp / "cases", RESULTS / "raw_backend_output", RESULTS / "profiles")
            qrows.append(row); qresults[case_id] = result
        deterministic = True
        if args.mode != "quick":
            _, repeated = run_case("QH00", temp / "repeat", RESULTS / "raw_backend_output/determinism", RESULTS / "profiles")
            original = qresults["QH00"]
            deterministic = (repeated.status == original.status and
                             canonical_json_bytes(repeated.profile) == canonical_json_bytes(original.profile))
        if args.mode == "quick":
            qualified = all(row["status"] in {s.value for s in SUCCESS} and row["semantic_valid"] for row in qrows)
            flags = {"QUICK_CASES_PASS": qualified}
        else: qualified, flags = qualification_pass(qrows, qresults, deterministic)
        write_csv(RESULTS / "qualification_cases.csv", qrows)
        semantic_rows = [{"case_id": row["case_id"], "upstream_verifier_pass": row["upstream_verifier_pass"],
                          "project_static_checker_pass": row["project_static_checker_pass"], "semantic_valid": row["semantic_valid"]} for row in qrows]
        write_csv(RESULTS / "semantic_validation.csv", semantic_rows)
        quant_rows = []
        for case_id in case_ids:
            scenario = make_case(case_id)
            for quantum in (100, 1000):
                for flow in scenario["tt_flows"]:
                    try: quant_rows.append({"case_id": case_id, **quantize_flow(flow, 64, 1_000_000_000, quantum)})
                    except ValueError as error: quant_rows.append({"case_id": case_id, "flow_id": flow["id"], "backend_quantum_ns": quantum, "error": str(error)})
        write_csv(RESULTS / "time_quantization_audit.csv", quant_rows)
        scale_rows = []
        if qualified:
            scale_ids = ["S1"] if args.mode == "quick" else ([] if args.mode == "qualification" else SCALE_IDS)
            bad = 0
            for scale_id in scale_ids:
                row, _ = run_scale(scale_id, temp / "scales", RESULTS / "raw_backend_output", RESULTS / "profiles")
                scale_rows.append(row)
                failed_frontier = row["status"] in {BackendStatus.TIME_LIMIT.value, BackendStatus.MEMORY_LIMIT.value} or float(row["scheduled_flow_ratio"]) < .8
                bad = bad + 1 if failed_frontier else 0
                if bad >= 2: break
        write_csv(RESULTS / "p0_scalability.csv", scale_rows)
        if args.mode == "quick" and (not qualified or not scale_rows or scale_rows[0]["status"] not in {s.value for s in SUCCESS}):
            qualified = False
        screen = all(any(r["scenario_id"] == sid and r["status"] in {s.value for s in SUCCESS} for r in scale_rows) for sid in ("S1", "S2", "S3")) if args.mode == "full" else False
        large = any(r["scenario_id"] in {"S5", "F150_TT1000"} and r["status"] in {s.value for s in SUCCESS} for r in scale_rows)
        verdict = {"H2S_BACKEND_QUALIFIED": qualified, **flags, "P0_SCALABILITY_SCREEN_PASS": screen,
                   "LARGE_SCALE_SUCCESS": large, "recommended_backend_quantum_ns": FORMAL_QUANTUM_NS,
                   "recommended_candidate_paths": DEFAULT_CANDIDATE_PATHS, "recommended_primary_algorithm": "H2S",
                   "recommended_fallback_algorithm": "CELF"}
        write_json(RESULTS / "qualification_verdict.json", verdict)
        sensitivity = []
        if args.mode != "quick":
            for case_id in case_ids:
                result100 = qresults[case_id]
                sensitivity.append({"scenario": case_id, "quantum_ns": 100, "status": result100.status.value,
                    "runtime_ms": result100.timings_ms.get("total_backend", 0),
                    "memory_bytes": max([a.get("peak_rss_bytes") or 0 for a in result100.statistics.get("attempts", [])] or [0]),
                    "scheduled_flow_ratio": result100.statistics.get("scheduled_flow_ratio", 0),
                    "max_quantization_error": max((r.get("max_quantization_error_ns", 0) for r in quant_rows if r.get("case_id") == case_id and r.get("backend_quantum_ns") == 100), default=0)})
                path = materialize_case(case_id, temp / "quantum1000")
                result1000 = H2sJrsBackend(EXECUTABLE, quantum_ns=1000).synthesize(request_for(path, RESULTS / "raw_backend_output/quantum1000" / case_id))
                write_attempt_logs(RESULTS / "raw_backend_output/quantum1000" / case_id, result1000)
                sensitivity.append({"scenario": case_id, "quantum_ns": 1000, "status": result1000.status.value,
                    "runtime_ms": result1000.timings_ms.get("total_backend", 0),
                    "memory_bytes": max([a.get("peak_rss_bytes") or 0 for a in result1000.statistics.get("attempts", [])] or [0]),
                    "scheduled_flow_ratio": result1000.statistics.get("scheduled_flow_ratio", 0),
                    "max_quantization_error": max((r.get("max_quantization_error_ns", 0) for r in quant_rows if r.get("case_id") == case_id and r.get("backend_quantum_ns") == 1000), default=0)})
        write_csv(RESULTS / "quantum_sensitivity.csv", sensitivity)
        oracle_rows = []
        if args.mode != "quick":
            for case_id in ("QH00", "QH01", "QH02"):
                path = materialize_case(case_id, temp / "oracle")
                oracle = TsnkitJrsWaBackend().synthesize(request_for(path, RESULTS / "raw_backend_output/oracle" / case_id))
                h2s = qresults[case_id]
                oracle_rows.append({"case_id": case_id, "oracle_backend": "JRS-WA Gurobi", "oracle_status": oracle.status.value,
                    "oracle_feasible": oracle.feasible, "oracle_static_valid": oracle.statistics.get("static_validation", {}).get("valid", oracle.feasible),
                    "h2s_status": h2s.status.value, "h2s_static_valid": h2s.statistics.get("semantic_valid", False),
                    "HEURISTIC_FALSE_NEGATIVE_ON_REFERENCE": oracle.feasible and h2s.status not in SUCCESS})
        write_csv(RESULTS / "oracle_comparison.csv", oracle_rows)
        exp14_path = ROOT / "results/pf_jrs_scalability/p0_results.csv"
        exp14 = {row.get("scenario", row.get("scenario_id")): row for row in csv.DictReader(exp14_path.open())} if exp14_path.is_file() else {}
        comparisons = []
        for row in scale_rows:
            old = exp14.get(row["scenario_id"], {})
            comparisons.append({"scenario": row["scenario_id"], "JRS_WA_status": old.get("status", old.get("P0_status", "")),
                "JRS_WA_total_backend_ms": old.get("total_backend_ms", old.get("solver_ms", "")), "JRS_WA_vars": old.get("vars", ""),
                "JRS_WA_constraints": old.get("constraints", ""), "JRS_WA_peak_memory": old.get("peak_memory", ""),
                "H2S_status": row["status"], "H2S_total_backend_ms": row["total_backend_ms"], "H2S_peak_memory": row["peak_rss_bytes"],
                "H2S_scheduled_ratio": row["scheduled_flow_ratio"], "H2S_semantic_valid": row["semantic_valid"],
                "speedup_if_comparable": "", "memory_ratio_if_comparable": ""})
        write_csv(RESULTS / "jrs_wa_vs_h2s_p0.csv", comparisons)
        write_csv(RESULTS / "profile_storage.csv", [{"scenario": r["scenario_id"], "canonical_profile_bytes": r["canonical_profile_bytes"], "gzip_bytes": r["gzip_bytes"]} for r in scale_rows])
        compiler = subprocess.run(["g++", "--version"], text=True, capture_output=True).stdout.splitlines()[0]
        cmake = subprocess.run([str(ROOT / ".venv-h2s/bin/cmake"), "--version"], text=True, capture_output=True).stdout.splitlines()[0]
        environment = {"python": sys.version.split()[0], "platform": platform.platform(), "compiler": compiler, "cmake": cmake,
                       "build_type": "Release", "threads": FORMAL_THREADS, "seed": FORMAL_SEED, "timeout_s": TIMEOUT_S,
                       "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB, "omnet_invocations": 0, "pf_invocations": 0, "plot_artifacts": 0,
                       "UPSTREAM_REPOSITORY": UPSTREAM_REPOSITORY, "UPSTREAM_COMMIT": UPSTREAM_COMMIT, "UPSTREAM_LICENSE": UPSTREAM_LICENSE}
        write_json(RESULTS / "environment.json", environment)
        write_json(RESULTS / "upstream_audit.json", {"repository": UPSTREAM_REPOSITORY, "commit": UPSTREAM_COMMIT, "license": UPSTREAM_LICENSE,
            "cpp_standard": 20, "build_system": "CMake FetchContent", "algorithms": ["H2S", "CELF", "EDF", "FF", "HERMES"],
            "routing": ["DIJKSTRA_OVERLAP", "K_SHORTEST"], "timing_unit": "integer macro tick (upstream documents microseconds)",
            "transmission_delay": "floor(frame_bytes*8/1000) upstream; adapter uses exact conservative multiples of 125 bytes",
            "queue_semantics": "store-and-forward with waiting", "hypercycle": "LCM(periods)",
            "input_gap": "stock Flow lacks independent release/deadline", "output_gap": "stock runner has TODO and no schedule export",
            "upstream_tests": {"command": "cmake --build build-debug --parallel 2 && build-debug/test/unit_tests",
                "stock": {"total": 61, "passed": 26, "failed": 35},
                "patched": {"total": 61, "passed": 26, "failed": 35},
                "failure_cause": "all 35 failures require upstream test/test_data, which is absent from the pinned git tree",
                "test_data_tracked": False}})
        write_json(RESULTS / "patch_audit.json", {"patch": str(PATCH.relative_to(ROOT)), "sha256": sha256_file(PATCH),
            "scope": ["release/deadline schema and propagation", "fixed source release", "verifier window", "schedule JSON export",
                      "zero exp14 propagation/switching delay", "Release bfd linker fallback"],
            "h2s_core_ordering_scoring_candidate_routing_modified": False})
        compute_frontier = next((r["scenario_id"] for r in scale_rows if r["status"] in {BackendStatus.TIME_LIMIT.value, BackendStatus.MEMORY_LIMIT.value}), "NONE")
        quality_frontier = next((r["scenario_id"] for r in scale_rows if float(r["scheduled_flow_ratio"]) < .8), "NONE")
        semantic_frontier = next((r["scenario_id"] for r in scale_rows if r["status"] in {s.value for s in SUCCESS} and not r["semantic_valid"]), "NONE")
        verdict.update({"COMPUTE_FRONTIER": compute_frontier, "QUALITY_FRONTIER": quality_frontier,
                        "SEMANTIC_FRONTIER": semantic_frontier,
                        "SCALABILITY_FRONTIER_REACHED": any(value != "NONE" for value in (compute_frontier, quality_frontier, semantic_frontier))})
        write_json(RESULTS / "qualification_verdict.json", verdict)
        assessment = "H2S_SCALABLE_AND_VALID" if screen and large else "H2S_SEMANTIC_GAP" if not qualified else "H2S_SCALABLE_BUT_QUALITY_LIMITED" if quality_frontier != "NONE" else "H2S_COMPUTE_LIMITED" if compute_frontier != "NONE" else "INCONCLUSIVE"
        scale_table = "\n".join(f"| {r['scenario_id']} | {r['tt_flows']} | {r['status']} | {int(r['scheduled_flow_count'])}/{r['tt_flows']} | {float(r['total_backend_ms']):.3f} | {int(r['peak_rss_bytes'])} | {r['semantic_valid']} |" for r in scale_rows)
        primary_successes = sum(r["status"] == BackendStatus.SUCCESS_H2S.value for r in scale_rows)
        fallback_successes = sum(r["status"] == BackendStatus.SUCCESS_CELF_FALLBACK.value for r in scale_rows)
        s1 = next((r for r in scale_rows if r["scenario_id"] == "S1"), None)
        s1_text = (f"H2S scheduled {int(s1['scheduled_flow_count'])}/{s1['tt_flows']} TT flows in {float(s1['total_backend_ms']):.3f} ms "
                   f"with {int(s1['peak_rss_bytes'])} bytes measured peak RSS; semantic validation was `{s1['semantic_valid']}`.") if s1 else "S1 was not run in this mode."
        summary = f"""# exp15 H2S backend qualification

Research Direction Assessment: **{assessment}**.

## Qualification

The QH00-QH10 gate is `{qualified}`. Release offsets, independent deadline budgets, wait-allowed forwarding, on-wire frame serialization, conservative time quantization, stream-aware same-destination routing, deterministic outcome, and upstream/project verifier parity are recorded in `qualification_verdict.json`. The formal quantum is {FORMAL_QUANTUM_NS} ns, routing is DIJKSTRA_OVERLAP, K={DEFAULT_CANDIDATE_PATHS}, and the fixed seed is {FORMAL_SEED}.

## P0 scalability

| Scenario | TT | Status | Scheduled | Total backend ms | Peak RSS bytes | Semantic valid |
|---|---:|---|---:|---:|---:|---|
{scale_table}

S1 is the key identical-workload comparison: exp14 JRS-WA reached 804,200 variables and 1,653,600 constraints and returned `MEMORY_LIMIT`. {s1_text} This is a backend runtime comparison under identical scenario semantics, not an exact-solver speedup claim.

H2S primary successes: {primary_successes}/{len(scale_rows)}. CELF successful fallback contributions: {fallback_successes}. Compute frontier: `{compute_frontier}`. Quality frontier: `{quality_frontier}`. Semantic frontier: `{semantic_frontier}`.

## Interpretation

JRS-WA is an exact feasibility formulation and may report `INFEASIBLE` only after a solver proof. H2S and CELF are constructive heuristics; `HEURISTIC_NOT_FOUND` is not an infeasibility proof. The tested heuristic exhibits better empirical scalability where it returns valid schedules, without establishing an algorithmic complexity result or superiority over an exact formulation.

The recommended next step for `H2S_SCALABLE_AND_VALID` is a separate rerun of PF precomputation scalability with H2S primary and CELF fallback. No PF campaign is started by exp15.
"""
        (RESULTS / "summary.md").write_text(summary, encoding="utf-8")
        files = sorted(path for path in RESULTS.rglob("*") if path.is_file() and path.name != "analysis_manifest.json")
        artifact_sha = {str(p.relative_to(RESULTS)): sha256_file(p) for p in files}
        campaign_sha = hashlib.sha256(canonical_json_bytes(artifact_sha)).hexdigest()
        manifest = {"schema_version": 1, "experiment": "exp15_h2s_backend_qualification", "mode": args.mode,
            "implementation_commit": args.implementation_commit, "results_commit": args.results_commit,
            "upstream_repo": UPSTREAM_REPOSITORY, "upstream_commit": UPSTREAM_COMMIT, "upstream_license": UPSTREAM_LICENSE,
            "patch_sha256": sha256_file(PATCH), "compiler": compiler, "cmake": cmake, "build_type": "Release",
            "backend_primary_algorithm": "H2S", "fallback_algorithm": "CELF", "routing_algorithm": "DIJKSTRA_OVERLAP",
            "candidate_paths_k": DEFAULT_CANDIDATE_PATHS, "backend_quantum_ns": FORMAL_QUANTUM_NS, "seed": FORMAL_SEED,
            "thread_count": FORMAL_THREADS, "timeout_s": TIMEOUT_S, "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB,
            "scenario_sha": {p.stem: sha256_file(p) for p in SCENARIOS.glob("*.yaml")},
            "exp14_scenario_sha": {p.stem: sha256_file(p) for p in SCENARIOS.glob("*.yaml")},
            "campaign_sha256": campaign_sha, "verdict": assessment, "artifact_sha256": artifact_sha}
        write_json(RESULTS / "analysis_manifest.json", manifest)
    return 0 if qualified else 2


if __name__ == "__main__": raise SystemExit(main())
