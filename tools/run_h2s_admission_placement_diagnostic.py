"""exp18h runner: observational H2S admission/placement audit only."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import platform
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools.h2s_admission_placement_diagnostic import (
    FAILURE_CASES, OUT, ROOT, SCENARIOS, SUCCESS_CONTROLS, TIEBREAK_PATCH_SHA256,
    TRACE_SCHEMA_VERSION, UPSTREAM, audit_current_patched_source, canonical_sha256,
    enrich_trace, formal_backend_config, load_source_scenario, sha256_file,
    source_scenario_path, trace_event_counts, trace_records, tree_sha256,
)
from tools.h2s_jrs_backend import H2sJrsBackend, parse_backend_output
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_p0_hnf_mechanism_diagnosis import signatures

EXECUTABLE = ROOT / ".external" / "AdvancedFlowScheduler" / "build-release" / "AdvancedFlowSchedulerExec"
EXPECTED = {**{scenario: (243, 0) for scenario in SUCCESS_CONTROLS}, **{scenario: (461, 3) for scenario in FAILURE_CASES}}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text, encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = fields or sorted({name for row in rows for name in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, names, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def write_jsonl_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename=path.name, mode="wb", fileobj=raw, mtime=0) as archive:
            for row in rows: archive.write(canonical_json_bytes(row) + b"\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for row in rows: handle.write(canonical_json_bytes(row) + b"\n")


def gzip_file(path: Path) -> Path:
    target = Path(f"{path}.gz")
    with path.open("rb") as source, target.open("wb") as raw:
        with gzip.GzipFile(filename=target.name, mode="wb", fileobj=raw, mtime=0) as archive:
            shutil.copyfileobj(source, archive)
    path.unlink()
    return target


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, check=True, capture_output=True).stdout.strip()


def parse_order(stdout: str, reverse_flow_map: dict[int, str]) -> list[dict[str, Any]]:
    values = [line.removeprefix("H2S_ORDER_JSON:") for line in stdout.splitlines() if line.startswith("H2S_ORDER_JSON:")]
    if len(values) != 1: raise RuntimeError(f"EXPECTED_ONE_H2S_ORDER_MARKER_GOT_{len(values)}")
    rows = json.loads(values[0]); output = []
    for row in rows:
        output.append({"position": int(row["position"]), "flow_id": reverse_flow_map[int(row["flow_id"])],
                       "period_ticks": int(row["period"]), "frame_size": int(row["frame_size"]),
                       "tie_break_mode": row["tie_break_mode"], "tie_break_seed": int(row["tie_break_seed"])})
    if [row["position"] for row in output] != list(range(len(output))): raise RuntimeError("INVALID_H2S_ORDER_POSITIONS")
    return output


def queue_map(scenario: dict[str, Any]) -> dict[int, dict[str, str]]:
    nodes = {node["id"]: index for index, node in enumerate(sorted(scenario["nodes"], key=lambda item: item["id"]))}
    adjacency: dict[int, list[int]] = {value: [] for value in nodes.values()}
    link_by_arc: dict[tuple[int, int], str] = {}
    for link in sorted(scenario["links"], key=lambda item: item["id"]):
        a, b = nodes[link["endpoint_a"]], nodes[link["endpoint_b"]]
        adjacency[a].append(b); adjacency[b].append(a)
        link_by_arc[(a, b)] = link_by_arc[(b, a)] = link["id"]
    reverse_nodes = {value: key for key, value in nodes.items()}; result: dict[int, dict[str, str]] = {}; index = 0
    for source in sorted(adjacency):
        for destination in sorted(adjacency[source]):
            result[index] = {"link_id": link_by_arc[(source, destination)], "source": reverse_nodes[source], "destination": reverse_nodes[destination]}; index += 1
    return result


def observation(scenario_id: str, root: Path, *, trace_on: bool, tag: str) -> dict[str, Any]:
    scenario = load_source_scenario(scenario_id); run_dir = root / "raw_trace" / scenario_id / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace.jsonl" if trace_on else None
    backend = H2sJrsBackend(EXECUTABLE, h2s_flow_sorting=4, h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0,
                            attempt_celf_fallback=False, diagnostic_trace_path=trace_path)
    result = backend.synthesize(RecoverySynthesisRequest(source_scenario_path(scenario_id), solver_timeout_s=30,
        route_scope="all-reroute", forwarding_model="stream-aware", output_directory=run_dir / "backend_input"))
    write_attempt_logs(run_dir / "logs", result)
    attempts = result.statistics.get("attempts", [])
    if len(attempts) != 1 or attempts[0].get("algorithm") != "H2S": raise RuntimeError("H2S_ONLY_DIAGNOSTIC_MODE_VIOLATED")
    attempt = attempts[0]; payload = parse_backend_output(str(attempt.get("stdout", "")))
    manifest = json.loads((run_dir / "backend_input" / "input_manifest.json").read_text(encoding="utf-8"))
    reverse_flow_map = {int(value): key for key, value in manifest["flow_map"].items()}
    order = parse_order(str(attempt["stdout"]), reverse_flow_map); signature = signatures(scenario, payload, "H2S")
    count, hnf_count = EXPECTED[scenario_id]
    if signature["scheduled_flow_count"] != count or signature["hnf_flow_count"] != hnf_count:
        raise RuntimeError(f"HISTORICAL_BASELINE_PARITY_FAILED:{scenario_id}:{signature['scheduled_flow_count']}/{signature['hnf_flow_count']}")
    records: list[dict[str, Any]] = []
    if trace_on:
        if trace_path is None or not trace_path.is_file(): raise RuntimeError("TRACE_NOT_CREATED")
        records = enrich_trace(trace_records(trace_path), scenario, reverse_flow_map,
                               {(int(a), int(b)): link for (a, b), link in ()})
        # enrich_trace only needs the arc mapping for CONFIGURATION.  Build it from its authoritative queue rows instead.
        for row in records:
            if "path" in row:
                row["canonical_path"] = [queue_map(scenario)[int(item["queue_id"])]["link_id"] for item in row["path"]]
            if "queue_id" in row:
                row["queue"] = queue_map(scenario)[int(row["queue_id"])]
        required = {"FLOW_BEGIN", "FLOW_END", "CONFIGURATION", "PLACEMENT_BEGIN", "PLACEMENT_END", "PLACEMENT_INSTANCE"}
        counts = trace_event_counts(records)
        if not required <= set(counts): raise RuntimeError(f"TRACE_EVENT_MISSING:{scenario_id}:{counts}")
    return {"scenario": scenario_id, "tag": tag, "trace_on": trace_on, "result_status": result.status.value,
            "payload": payload, "signature": signature, "order": order, "order_sha256": canonical_sha256(order),
            "normalized_schedule_sha256": canonical_sha256({key: payload[key] for key in ("requested_flow_count", "scheduled_flow_count", "hyper_cycle_ticks", "slots")}), "slots_sha256": canonical_sha256(payload["slots"]),
            "candidate_vector_sha256": canonical_sha256(sorted((int(key), int(value)) for key, value in payload.get("candidate_path_counts", {}).items())),
            "upstream_verifier": bool(payload["upstream_verifier_pass"]),
            "static_checker": attempt.get("project_static_checker_pass"), "trace": records,
            "trace_event_counts": trace_event_counts(records), "run_dir": str(run_dir.relative_to(root)),
            "trace_path": str(trace_path.relative_to(root)) if trace_path else "", "command": attempt.get("command", []),
            "wall_ms": float(attempt.get("wall_ms", 0)), "peak_rss_bytes": int(attempt.get("peak_rss_bytes", 0))}


def parity_row(off: dict[str, Any], on: dict[str, Any]) -> dict[str, Any]:
    fields = ("scheduled_flow_count", "hnf_set_sha256", "instance_completion_sha256")
    row = {"scenario": off["scenario"], "trace_off_tag": off["tag"], "trace_on_tag": on["tag"],
           **{f"{name}_exact": off["signature"][name] == on["signature"][name] for name in fields},
           "actual_flow_order_exact": off["order"] == on["order"], "chosen_schedule_slots_exact": off["slots_sha256"] == on["slots_sha256"],
           "normalized_schedule_exact": off["normalized_schedule_sha256"] == on["normalized_schedule_sha256"],
           "candidate_vector_exact": off["candidate_vector_sha256"] == on["candidate_vector_sha256"],
           "upstream_verifier_exact": off["upstream_verifier"] == on["upstream_verifier"],
           "static_checker_exact": off["static_checker"] == on["static_checker"]}
    row["parity_pass"] = all(value for key, value in row.items() if key.endswith("_exact"))
    return row


def repeatable_trace_sha256(rows: list[dict[str, Any]]) -> str:
    """Hash decision/observer events, excluding their intentionally unique sink identity."""
    return canonical_sha256([{key: value for key, value in row.items() if key not in {"run_id", "scenario"}} for row in rows])


def repeatable_admission_sha256(rows: list[dict[str, Any]]) -> str:
    """The repeat tag identifies an artifact, not an admission decision."""
    return canonical_sha256([{key: value for key, value in row.items() if key != "run_tag"} for row in rows])


def admission_rows(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for observed in observations:
        if not observed["trace_on"]: continue
        begin = {row["flow_id"]: row for row in observed["trace"] if row["event_type"] == "FLOW_BEGIN"}
        end = {row["flow_id"]: row for row in observed["trace"] if row["event_type"] == "FLOW_END"}
        first_hnf = min((row["sequence_index"] for row in end.values() if not row["accepted"]), default=None)
        for internal, left in begin.items():
            right = end[internal]
            result.append({"scenario": observed["scenario"], "run_tag": observed["tag"], "sequence_index": left["sequence_index"],
                           "flow_id": left["canonical_flow_id"], "flow_kind": left["flow_kind"], "source": left["source"], "destination": left["destination"],
                           "period_ticks": left["period_ticks"], "release_offset_ticks": left["release_offset_ticks"], "deadline_ticks": left["deadline_ticks"],
                           "frame_size_bytes": left["frame_size_bytes"], "configuration_count": left["configuration_count"],
                           "accepted_count_before": left["accepted_count_before"], "accepted": right["accepted"],
                           "chosen_config_id": right["chosen_config_id"], "configs_attempted": right["attempted_configuration_count"],
                           "accepted_count_after": right["accepted_count_after"], "after_first_hnf": first_hnf is not None and left["sequence_index"] > first_hnf})
    return result


def configuration_rows(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for observed in observations:
        if not observed["trace_on"]: continue
        ends = {(row["flow_id"], row["config_id"]): row for row in observed["trace"] if row["event_type"] == "PLACEMENT_END"}
        for row in observed["trace"]:
            if row["event_type"] != "CONFIGURATION": continue
            finish = ends.get((row["flow_id"], row["config_id"]), {})
            output.append({"scenario": observed["scenario"], "run_tag": observed["tag"], "flow_id": row["canonical_flow_id"],
                           "flow_kind": row["flow_kind"], "config_id": row["config_id"], "rating": row["rating"], "rating_rank": row["rating_rank"],
                           "path": ";".join(row.get("canonical_path", [])), "path_sha256": canonical_sha256(row.get("canonical_path", [])),
                           "attempted": bool(finish), "placement_success": finish.get("success", "")})
    return output


def placement_rows(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    instances: list[dict[str, Any]] = []; failures: list[dict[str, Any]] = []; states: list[dict[str, Any]] = []
    for observed in observations:
        if not observed["trace_on"]: continue
        for row in observed["trace"]:
            common = {"scenario": observed["scenario"], "run_tag": observed["tag"], "flow_id": row.get("canonical_flow_id", ""),
                      "flow_kind": row.get("flow_kind", ""), "config_id": row.get("config_id", ""), "attempt_index": row.get("attempt_index", "")}
            if row["event_type"] == "PLACEMENT_INSTANCE": instances.append({**common, **{key: row.get(key, "") for key in (
                "sub_cycle_index", "frame_index", "release_window_ticks", "deadline_window_ticks", "flow_release_offset_ticks", "flow_deadline_ticks", "fixed_release", "search_success", "fixed_release_matches", "accepted_for_instance")}})
            elif row["event_type"] in {"SEARCH_FAILURE", "PLACEMENT_REJECTION"}: failures.append({**common, **{key: row.get(key, "") for key in (
                "sub_cycle_index", "frame_index", "hop_index", "queue_id", "arrival_ticks", "deadline_ticks", "effective_deadline_ticks", "transmission_ticks",
                "reason_flags", "free_slot_count", "slots_ending_before_arrival", "slots_starting_after_deadline", "slots_overlapping_legal_window",
                "max_contiguous_free_duration", "relevant_free_intervals", "observed_overlapping_reservations", "queue", "required_release_ticks", "observed_first_start_ticks")}})
            elif row["event_type"] == "PLACEMENT_END":
                before, after = row["utilization_before"], row["utilization_after"]
                states.append({**common, "success": row["success"], "state_unchanged": row["state_unchanged"],
                               "before_state_sha256": before["state_sha256"], "after_state_sha256": after["state_sha256"],
                               "before_reservation_count": before["reservation_count"], "after_reservation_count": after["reservation_count"],
                               "before_total_reserved_ticks": before["total_reserved_ticks"], "after_total_reserved_ticks": after["total_reserved_ticks"]})
    return instances, failures, states


def mechanism(observations: list[dict[str, Any]], configs: list[dict[str, Any]], failures: list[dict[str, Any]], states: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    summaries = []; cross = []; snapshots = []
    for observed in observations:
        if not observed["trace_on"] or observed["scenario"] not in FAILURE_CASES or not observed["tag"].endswith("repeat_1"): continue
        hnf = set(observed["signature"]["hnf_flow_ids"])
        for flow_id in sorted(hnf):
            rows = [row for row in configs if row["scenario"] == observed["scenario"] and row["run_tag"] == observed["tag"] and row["flow_id"] == flow_id]
            failed = [row for row in failures if row["scenario"] == observed["scenario"] and row["run_tag"] == observed["tag"] and row["flow_id"] == flow_id]
            attempt = [row for row in states if row["scenario"] == observed["scenario"] and row["run_tag"] == observed["tag"] and row["flow_id"] == flow_id]
            reasons = Counter(reason for row in failed for reason in row["reason_flags"])
            queues = Counter(str(row.get("queue", {}).get("link_id", row["queue_id"])) for row in failed)
            blockers = {item.get("canonical_flow_id", "") for row in failed for item in row["observed_overlapping_reservations"] if item.get("canonical_flow_id")}
            first = min(failed, key=lambda row: (row["attempt_index"], row["sub_cycle_index"], row["frame_index"], row["hop_index"]), default={})
            mutation = any(not row["state_unchanged"] for row in attempt if not row["success"])
            summaries.append({"scenario": observed["scenario"], "flow_id": flow_id, "flow_kind": rows[0]["flow_kind"] if rows else "",
                              "flow_order_position": next((row["position"] for row in observed["order"] if row["flow_id"] == flow_id), ""),
                              "configs_total": len(rows), "configs_attempted": sum(row["attempted"] for row in rows), "configuration_empty": not rows,
                              "all_configs_failed": bool(rows) and all(row["placement_success"] is False for row in rows),
                              "first_failure_instance": f"{first.get('sub_cycle_index','')}:{first.get('frame_index','')}", "dominant_failure_hop": first.get("hop_index", ""),
                              "dominant_failure_link": queues.most_common(1)[0][0] if queues else "", "dominant_reason": reasons.most_common(1)[0][0] if reasons else "",
                              "unique_blocker_flow_count": len(blockers), "failed_placement_state_mutation": mutation,
                              "mechanism_class": "PLACEMENT_SEARCH_FAILURE" if rows and failed else "TRACE_INSUFFICIENT"})
            snapshots.extend({"scenario": observed["scenario"], "flow_id": flow_id, **row} for row in failed[:1])
    by_flow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in summaries: by_flow[row["flow_id"]].append(row)
    for flow_id, rows in sorted(by_flow.items()):
        if len(rows) != 3: continue
        comparisons = []
        for row in rows:
            cfg = [item for item in configs if item["scenario"] == row["scenario"] and item["run_tag"].endswith("repeat_1") and item["flow_id"] == flow_id]
            comparisons.append({"scenario": row["scenario"], "config_count": len(cfg), "path_hashes": sorted(item["path_sha256"] for item in cfg), "reason": row["dominant_reason"], "link": row["dominant_failure_link"]})
        cross.append({"flow_id": flow_id, "topology_count": len(rows), "same_config_count": len({row["config_count"] for row in comparisons}) == 1,
                      "same_path_hashes": len({canonical_sha256(row["path_hashes"]) for row in comparisons}) == 1,
                      "same_dominant_reason": len({row["reason"] for row in comparisons}) == 1,
                      "same_dominant_link": len({row["link"] for row in comparisons}) == 1,
                      "comparison": json.dumps(comparisons, sort_keys=True)})
    return summaries, cross, snapshots


def audit_markdown(audit: dict[str, Any]) -> str:
    return "\n".join(("# Current patched-source audit", "", f"- Upstream: `{audit['upstream_base_commit']}`; current source tree SHA-256: `{audit['current_patched_source_tree_sha256']}`.",
        f"- H2S class: `{audit['scheduler_class']}`. `{audit['schedule_set']}`", f"- Formal sorter/rating/placement: {audit['flow_sorter']}; {audit['configuration_rating']}; {audit['placement_strategy']}.",
        f"- Candidate routing: {audit['candidate_route_generator']}, K={audit['candidate_k']}.", "- Fixed release/deadline are consumed by `placeConfigASAP`; the search consumes per-flow propagation and processing delays.",
        "- Mixed-period chain is `compute_frames_per_hc` → placement frame loop → `searchTransmissionOpportunities` → `reserveSlot`.",
        f"- BALANCED (not the qualified exp18h placement) uses release `{audit['balanced_actual_formula']['release']}` and deadline `{audit['balanced_actual_formula']['deadline']}`. It does not consume fixed release/deadline fields.",
        "- No backtracking/retry/repair is present. A failed flow remains absent, and the priority-queue loop continues with later flows.",
        "- No global flow/config/attempt hard limit or explicit fixed cardinality ceiling was found.",
        "", "## Semantic difference from pinned source", "", "The existing exp15 patch makes ASAP use `release_offset`, relative `deadline`, `fixed_release`, and per-flow delay fields. The BALANCED implementation retains the stock period-bound formulas above; this is a recorded semantic anomaly, not repaired or exercised by the qualified ASAP cohort.", ""))


def summary_markdown(audit: dict[str, Any], rows: list[dict[str, Any]], parity: list[dict[str, Any]], mechanisms: list[dict[str, Any]], verdict: str) -> str:
    controls = [row for row in rows if row["scenario"] in SUCCESS_CONTROLS and row["tag"].endswith("repeat_1")]
    first_hnf = min((row for row in admission_rows(rows) if not row["accepted"]), key=lambda row: row["sequence_index"], default={})
    later = sum(1 for row in admission_rows(rows) if first_hnf and row["scenario"] == first_hnf.get("scenario") and row["sequence_index"] > first_hnf["sequence_index"] and row["accepted"])
    return "\n".join(("# exp18h read-only H2S admission and placement audit", "", "M_D070 is the first preregistered Medium 3/3 complete point; L_D050 is the lowest preregistered Large point still at 461/464. Both are direct byte reuse from exp18g.",
        "", "## Decision and parity gates", "", "No solver decision was changed: all formal commands use H2S only, LOW_PERIOD (`-f 4`), PATH_LENGTH (`-c 1`), ASAP (`-p 0`), DIJKSTRA_OVERLAP K=5, baseline tie-break, 100 ns, seed 1024, one thread, 30 s, and no CELF fallback.",
        f"TRACE_OFF/TRACE_ON parity passed for all qualification comparisons: `{all(row['parity_pass'] for row in parity)}`. It compares count, HNF set, instance completion, order, slots, candidate vector, verifier, and checker state.",
        "", "## Source semantics", "", "The qualified ASAP path does consume fixed release and relative deadline. BALANCED is not selected by the historical qualified adapter; its source still ignores those fields, so the source-only anomaly is recorded without a repair.",
        "H2S is constructive and non-backtracking; it continues after a failed flow. There is no observed hard global attempt/cardinality limit.",
        "", "## Trace observations", "", f"All Medium controls completed at 243/243: `{all(item['signature']['scheduled_flow_count'] == 243 and item['signature']['hnf_flow_count'] == 0 for item in controls)}`.",
        f"The earliest observed HNF is scenario `{first_hnf.get('scenario','')}`, position `{first_hnf.get('sequence_index','')}`, flow `{first_hnf.get('flow_id','')}`; later successful admissions in that trace: `{later}`.",
        "For every reported Large HNF, the report distinguishes configuration count from original placement attempts, records actual ASAP frame windows, failed hop/egress snapshot, and observed overlapping reservations. These are observational overlaps, not causal counterfactual claims.",
        "", "## Limits and recommendation", "", "This does not prove the Large workload infeasible, and it does not prove H2S is the sole problem. It cannot be described as merely the final three flows not fitting unless a controlled intervention establishes that claim.",
        f"Verdict: `{verdict}`. Recommended next authorised stage: `FIX_BACKEND_SEMANTIC_ANOMALY` (review/repair the non-qualified BALANCED semantics separately; do not make that repair here).", ""))


def run(quick: bool) -> int:
    if not EXECUTABLE.is_file(): raise RuntimeError(f"MISSING_H2S_EXECUTABLE:{EXECUTABLE}")
    output = OUT / "quick_validation" if quick else OUT
    if output.exists(): raise RuntimeError(f"REFUSE_TO_OVERWRITE_EXISTING_OUTPUT:{output}")
    if not quick and git("status", "--porcelain"): raise RuntimeError("MAIN_WORKTREE_NOT_CLEAN_FOR_FORMAL_PHASE_B")
    audit = audit_current_patched_source()
    frozen_roots = {str(path.relative_to(ROOT)): tree_sha256(path) for path in sorted((ROOT / "results").iterdir()) if path.is_dir() and path != OUT}
    output.mkdir(parents=True)
    write_json(output / "environment.json", {"platform": platform.platform(), "python": platform.python_version(), "parent_commit": git("rev-parse", "HEAD"), "origin_main": git("rev-parse", "origin/main"), "backend": formal_backend_config()})
    write_json(output / "source_scenario_manifest.json", {sid: {"path": str(source_scenario_path(sid).relative_to(ROOT)), "sha256": sha256_file(source_scenario_path(sid)), "expected": EXPECTED[sid]} for sid in SCENARIOS})
    write_text(output / "patched_source_audit.md", audit_markdown(audit))
    write_text(output / "instrumentation_design.md", "# Instrumentation design\n\n`--diagnostic-trace` is default-off and is only passed to H2S. The observer returns void, copies JSON state only, never calls RNG, never invokes placement a second time, and writes actual scheduler/configuration/search events. Trace-off passes a null observer.\n")
    qualified: list[dict[str, Any]] = []
    for sid in ("M_RING_D070", "L_RING_D050"):
        off = observation(sid, output, trace_on=False, tag="qualification_trace_off")
        on = observation(sid, output, trace_on=True, tag="qualification_trace_on")
        qualified += [off, on]
    parity = [parity_row(qualified[index], qualified[index + 1]) for index in range(0, len(qualified), 2)]
    if not all(row["parity_pass"] for row in parity): raise RuntimeError("INSTRUMENTATION_PERTURBED_SOLVER_DECISION")
    if quick:
        write_csv(output / "instrumentation_parity.csv", parity); write_json(output / "quick_verdict.json", {"quick_pass": True, "parity": parity, "audit_semantic_anomaly": audit["semantic_anomaly_observed"]})
        return 0
    observed: list[dict[str, Any]] = []
    for sid in SCENARIOS:
        for repeat in (1, 2): observed.append(observation(sid, output, trace_on=True, tag=f"trace_on_repeat_{repeat}"))
    admission = admission_rows(observed); configs = configuration_rows(observed); instances, failures, states = placement_rows(observed); mechanisms, cross, snapshots = mechanism(observed, configs, failures, states)
    repeat_rows = []
    for sid in SCENARIOS:
        pair = [row for row in observed if row["scenario"] == sid]
        left_admission = [row for row in admission if row["scenario"] == sid and row["run_tag"] == pair[0]["tag"]]
        right_admission = [row for row in admission if row["scenario"] == sid and row["run_tag"] == pair[1]["tag"]]
        repeat_rows.append({"scenario": sid, "trace_order_sha_exact": repeatable_trace_sha256(pair[0]["trace"]) == repeatable_trace_sha256(pair[1]["trace"]),
                            "admission_sha_exact": repeatable_admission_sha256(left_admission) == repeatable_admission_sha256(right_admission),
                            "schedule_sha_exact": pair[0]["slots_sha256"] == pair[1]["slots_sha256"], "hnf_exact": pair[0]["signature"]["hnf_set_sha256"] == pair[1]["signature"]["hnf_set_sha256"]})
        repeat_rows[-1]["repeatability_pass"] = all(value for key, value in repeat_rows[-1].items() if key.endswith("_exact"))
    if not all(row["repeatability_pass"] for row in repeat_rows): raise RuntimeError("DIAGNOSTIC_NONDETERMINISTIC")
    write_csv(output / "instrumentation_parity.csv", parity); write_csv(output / "formal_runs.csv", [{"scenario": row["scenario"], "repeat": row["tag"].rsplit("_", 1)[-1], "scheduled_flow_count": row["signature"]["scheduled_flow_count"], "hnf_flow_count": row["signature"]["hnf_flow_count"], "hnf_set_sha256": row["signature"]["hnf_set_sha256"], "order_sha256": row["order_sha256"], "schedule_slots_sha256": row["slots_sha256"], "wall_ms": row["wall_ms"], "peak_rss_bytes": row["peak_rss_bytes"], "h2s_only": len(row["command"]) > 0 and "CELF" not in row["command"]} for row in observed])
    write_csv(output / "flow_admission_trace.csv", admission); write_csv(output / "configuration_summary.csv", configs); write_jsonl_gz(output / "configuration_paths.jsonl.gz", [row for row in configs]); write_csv(output / "placement_attempts.csv", instances); gzip_file(output / "placement_attempts.csv")
    write_jsonl_gz(output / "placement_failures.jsonl.gz", failures); write_jsonl(output / "first_hnf_snapshot.jsonl", snapshots); write_csv(output / "hnf_flow_summary.csv", mechanisms); write_csv(output / "mechanism_summary.csv", mechanisms); write_csv(output / "cross_topology_hnf_mechanism.csv", cross); write_csv(output / "failed_placement_atomicity.csv", [row for row in states if not row["success"]]); write_csv(output / "utilization_snapshots.csv", states); write_csv(output / "repeatability.csv", repeat_rows)
    control = [{"scenario": row["scenario"], "repeat": row["tag"], "scheduled": row["signature"]["scheduled_flow_count"], "hnf": row["signature"]["hnf_flow_count"], "trace_events": json.dumps(row["trace_event_counts"], sort_keys=True)} for row in observed if row["scenario"] in SUCCESS_CONTROLS]
    write_csv(output / "success_control_summary.csv", control)
    verdict = "BACKEND_SEMANTIC_ANOMALY_OBSERVED" if audit["semantic_anomaly_observed"] else ("PLACEMENT_FAILURE_MECHANISM_OBSERVED" if all(row["all_configs_failed"] and not row["failed_placement_state_mutation"] for row in mechanisms) else "TRACE_INSUFFICIENT_FOR_MECHANISM")
    write_json(output / "mechanism_summary.json", {"verdict": verdict, "hnf_mechanisms": mechanisms, "event_counts": {f"{row['scenario']}/{row['tag']}": row["trace_event_counts"] for row in observed}})
    write_json(output / "diagnostic_verdict.json", {"verdict": verdict, "semantic_anomaly": audit["semantic_anomaly_observed"], "formal_placement": formal_backend_config()["placement"], "statement": "No solver/workload/backend repair was performed."})
    current_frozen = {name: tree_sha256(ROOT / name) for name in frozen_roots}
    if current_frozen != frozen_roots: raise RuntimeError("FROZEN_HISTORICAL_ARTIFACT_CHANGED")
    for log in output.rglob("*.log"):
        gzip_file(log)
    for trace in output.rglob("trace.jsonl"):
        gzip_file(trace)
    write_text(output / "summary.md", summary_markdown(audit, observed, parity, mechanisms, verdict))
    manifest = {"parent_commit": git("rev-parse", "HEAD^"), "implementation_commit": git("rev-parse", "HEAD"), "results_commit": "PENDING_RESULTS_COMMIT", "source_scenarios": {sid: sha256_file(source_scenario_path(sid)) for sid in SCENARIOS}, "exp18g_verdict_sha256": sha256_file(ROOT / "results" / "tt_workload_density_calibration" / "calibration_verdict.json"), "audit": audit, "backend": formal_backend_config(), "trace_schema_version": TRACE_SCHEMA_VERSION, "frozen_tree_sha256": frozen_roots, "artifact_sha256": {str(path.relative_to(output)): sha256_file(path) for path in sorted(output.rglob("*")) if path.is_file()}}
    write_json(output / "analysis_manifest.json", manifest)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--quick", action="store_true")
    raise SystemExit(run(parser.parse_args().quick))
