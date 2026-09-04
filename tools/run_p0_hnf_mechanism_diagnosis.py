"""Second-stage, observational exp18b diagnosis for frozen HNF P0 evidence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.h2s_jrs_backend import H2sJrsBackend
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_p0_hnf_diagnosis import OUT, SOURCE, raw

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
ORDER = ("M_RING", "M_REDSTAR", "M_ROR", "L_RING", "L_REDSTAR", "L_ROR")
REPLAY_SCENARIOS = ("M_RING", "M_ROR", "L_ROR")
FROZEN = ("source_p0_manifest.json", "unscheduled_flow_identity.csv", "instance_completion.csv",
          "flow_set_comparison.csv", "scenario_diagnosis.csv")
OUTPUT_MARKER = "H2S_SCHEDULE_JSON:"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def flow_kind(flow_id: str) -> str:
    labels = {"SF": "SensorFast", "SC": "SensorCyclic", "CMD": "ControlCommand",
              "STAT": "MachineStatus", "COORD": "MachineCoordination",
              "IC_PREV": "InterCellPrevious", "IC_NEXT": "InterCellNext"}
    prefix = next(prefix for prefix in ("IC_PREV", "IC_NEXT", "SF", "SC", "CMD", "STAT", "COORD")
                  if flow_id.startswith(prefix))
    return labels[prefix]


def completion_records(scenario: dict[str, Any], payload: dict[str, Any], backend: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use the frozen phase-one source-to-destination continuity rule for raw output."""
    flows = sorted(scenario["tt_flows"], key=lambda item: item["id"])
    by_rank = {rank: flow for rank, flow in enumerate(flows)}
    node_map = {node["id"]: index for index, node in enumerate(sorted(scenario["nodes"], key=lambda item: item["id"]))}
    slots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for slot in payload["slots"]:
        slots[by_rank[int(slot["flow_id"])]["id"]].append(slot)
    identities: list[dict[str, Any]] = []
    instances: list[dict[str, Any]] = []
    hypercycle_ns = 8_000_000
    for rank, flow in enumerate(flows):
        flow_id = flow["id"]
        period_ns, release_ns = round(flow["period_s"] * 1e9), round(flow["release_offset_s"] * 1e9)
        expected = hypercycle_ns // period_ns
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for slot in slots[flow_id]:
            grouped[(int(slot["start_tick"]) * 100 - release_ns) // period_ns].append(slot)
        complete = partial = missing = 0
        for index in range(expected):
            values = sorted(grouped.get(index, []), key=lambda item: item["start_tick"])
            status = "MISSING_INSTANCE" if not values else "PARTIAL_INSTANCE"
            if values:
                source, destination = node_map[flow["source"]], node_map[flow["destination"]]
                continuous = (values[0]["source"] == source and values[-1]["destination"] == destination
                              and all(left["destination"] == right["source"] for left, right in zip(values, values[1:])))
                status = "COMPLETE_INSTANCE" if continuous else "PARTIAL_INSTANCE"
            complete += status == "COMPLETE_INSTANCE"
            partial += status == "PARTIAL_INSTANCE"
            missing += status == "MISSING_INSTANCE"
            instances.append({"backend": backend, "flow_id": flow_id, "instance_index": index,
                              "completion_status": status, "observed_slot_count": len(values)})
        classification = ("FULLY_SCHEDULED" if complete == expected else "ZERO_SCHEDULED" if not complete and not partial
                          else "PARTIAL_INSTANCES" if complete else "PARTIALLY_PLACED_INSTANCE")
        identities.append({"backend": backend, "flow_id": flow_id, "flow_kind": flow_kind(flow_id),
                           "input_rank": rank, "input_rank_percentile": rank / max(len(flows) - 1, 1),
                           "flow_completion_class": classification, "expected_instances": expected,
                           "complete_instances": complete, "partial_instances": partial, "missing_instances": missing})
    return identities, instances


def signatures(scenario: dict[str, Any], payload: dict[str, Any], backend: str) -> dict[str, Any]:
    identities, instances = completion_records(scenario, payload, backend)
    hnf = sorted(row["flow_id"] for row in identities if row["flow_completion_class"] != "FULLY_SCHEDULED")
    normalized_instances = sorted(instances, key=lambda item: (item["flow_id"], item["instance_index"]))
    return {"scheduled_flow_count": int(payload["scheduled_flow_count"]),
            "requested_flow_count": int(payload["requested_flow_count"]),
            "hnf_set_sha256": sha256_bytes(canonical_json_bytes(hnf)),
            "instance_completion_sha256": sha256_bytes(canonical_json_bytes(normalized_instances)),
            "hnf_flow_count": len(hnf), "hnf_flow_ids": hnf, "identities": identities}


def source_rows(identifier: str, attempt: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    scenario, _, payload = raw(identifier, attempt)
    return scenario, payload, signatures(scenario, payload, attempt.upper())


def candidate_rank_and_business(identity: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidate: list[dict[str, Any]] = []
    ranks: list[dict[str, Any]] = []
    business: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for identifier in ORDER:
        scale, topology = identifier[0], identifier.split("_", 1)[1]
        for attempt in ("h2s", "celf"):
            _, payload, _ = source_rows(identifier, attempt)
            backend = attempt.upper()
            rows = [row for row in identity if row["scale"] == scale and row["topology"] == topology and row["backend"] == backend]
            hnf = [row for row in rows if row["flow_completion_class"] != "FULLY_SCHEDULED"]
            for row in hnf:
                candidate.append({"scenario": identifier, "backend": backend, "flow_id": row["flow_id"],
                                  "flow_kind": row["flow_kind"], "input_rank": row["input_rank"],
                                  "candidate_route_count_formal_export": payload.get("candidate_path_counts", {}).get(str(row["input_rank"]), ""),
                                  "candidate_route_introspection_status": "CANDIDATE_ROUTE_INTROSPECTION_UNAVAILABLE",
                                  "mean_pairwise_edge_jaccard": "", "forced_edge_count": "", "edge_disjoint_pair_count": "",
                                  "unavailability_reason": "Upstream P0 JSON exports candidate counts, not formal candidate node/link paths."})
            for row in rows:
                ranks.append({"scenario": identifier, "backend": backend, "flow_id": row["flow_id"],
                              "flow_kind": row["flow_kind"], "flow_completion_class": row["flow_completion_class"],
                              "input_rank": row["input_rank"], "input_rank_percentile": row["input_rank_percentile"],
                              "rank_interpretation": "CANONICAL_INPUT_ID_NOT_PROVEN_ADMISSION_ORDER"})
            total_hnf = len(hnf)
            for name in sorted({row["flow_kind"] for row in rows}):
                group = [row for row in rows if row["flow_kind"] == name]
                group_hnf = [row for row in group if row["flow_completion_class"] != "FULLY_SCHEDULED"]
                rate, aggregate = len(group_hnf) / len(group), total_hnf / len(rows)
                business.append({"scenario": identifier, "backend": backend, "flow_kind": name,
                                 "all_flow_count": len(group), "all_flow_fraction": len(group) / len(rows),
                                 "HNF_count": len(group_hnf), "HNF_rate": rate,
                                 "HNF_fraction": len(group_hnf) / total_hnf if total_hnf else 0,
                                 "HNF_rate_enrichment_vs_all_flows": rate / aggregate if aggregate else 0})
            hnf_ranks = [float(row["input_rank_percentile"]) for row in hnf]
            scheduled_ranks = [float(row["input_rank_percentile"]) for row in rows if row["flow_completion_class"] == "FULLY_SCHEDULED"]
            summary.append({"scenario": identifier, "backend": backend, "HNF_rank_min": min(hnf_ranks, default=""),
                            "HNF_rank_median": statistics.median(hnf_ranks) if hnf_ranks else "",
                            "HNF_rank_max": max(hnf_ranks, default=""),
                            "scheduled_rank_median": statistics.median(scheduled_ranks) if scheduled_ranks else "",
                            "rank_interpretation": "CANONICAL_INPUT_ID_NOT_PROVEN_ADMISSION_ORDER"})
    return candidate, ranks, business, summary


def link_contention() -> list[dict[str, Any]]:
    """Derive directional egress busy time from actual scheduled P0 slots only."""
    rows: list[dict[str, Any]] = []
    hypercycle_ns = 8_000_000
    for identifier in ORDER:
        for attempt in ("h2s", "celf"):
            scenario, payload, signature = source_rows(identifier, attempt)
            backend = attempt.upper()
            by_rank = {rank: flow["id"] for rank, flow in enumerate(sorted(scenario["tt_flows"], key=lambda item: item["id"]))}
            reverse_nodes = {index: node["id"] for index, node in enumerate(sorted(scenario["nodes"], key=lambda item: item["id"]))}
            physical: dict[tuple[str, str], str] = {}
            for link in scenario["links"]:
                physical[(link["endpoint_a"], link["endpoint_b"])] = link["id"]
                physical[(link["endpoint_b"], link["endpoint_a"])] = link["id"]
            hnf = {row["flow_id"] for row in signature["identities"] if row["flow_completion_class"] != "FULLY_SCHEDULED"}
            by_egress: dict[tuple[str, str, str], list[tuple[str, int]]] = defaultdict(list)
            for slot in payload["slots"]:
                source, destination = reverse_nodes[int(slot["source"])], reverse_nodes[int(slot["destination"])]
                by_egress[(physical[(source, destination)], source, destination)].append(
                    (by_rank[int(slot["flow_id"])], (int(slot["end_tick"]) - int(slot["start_tick"])) * 100))
            for (link_id, source, destination), values in sorted(by_egress.items()):
                all_busy = sum(duration for _, duration in values)
                hnf_busy = sum(duration for flow_id, duration in values if flow_id in hnf)
                all_flows = {flow_id for flow_id, _ in values}
                hnf_flows = all_flows & hnf
                rows.append({"scenario": identifier, "backend": backend, "physical_link_id": link_id,
                             "egress_source": source, "egress_destination": destination,
                             "scheduled_distinct_flow_count": len(all_flows), "HNF_distinct_flow_count": len(hnf_flows),
                             "scheduled_busy_ns": all_busy, "scheduled_busy_fraction_of_8ms": all_busy / hypercycle_ns,
                             "HNF_busy_ns": hnf_busy, "HNF_busy_share_of_scheduled": hnf_busy / all_busy if all_busy else 0,
                             "scope": "SCHEDULED_SLOTS_ONLY; ZERO_SCHEDULED_HNF_FLOWS_HAVE_NO_SLOT_OCCUPANCY"})
    return rows


def repeatability() -> list[dict[str, Any]]:
    if not EXECUTABLE.is_file():
        raise RuntimeError(f"missing backend executable: {EXECUTABLE}")
    rows: list[dict[str, Any]] = []
    for identifier in REPLAY_SCENARIOS:
        original = {attempt.upper(): source_rows(identifier, attempt)[2] for attempt in ("h2s", "celf")}
        scenario_path = SOURCE / "scenarios" / f"{identifier}.json"
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        for repeat in (1, 2):
            output = OUT / "diagnostic_replays" / identifier / f"repeat_{repeat}"
            request = RecoverySynthesisRequest(scenario_path, solver_timeout_s=30, route_scope="all-reroute",
                                               forwarding_model="stream-aware", output_directory=output)
            result = H2sJrsBackend(EXECUTABLE).synthesize(request)
            write_attempt_logs(output / "logs", result)
            attempts = result.statistics.get("attempts", [])
            if len(attempts) != 2:
                raise RuntimeError(f"{identifier} repeat {repeat}: expected H2S and CELF, got {len(attempts)}")
            for attempt in attempts:
                backend = str(attempt["algorithm"]).upper()
                marker = str(attempt.get("stdout", ""))
                encoded = next((line[len(OUTPUT_MARKER):] for line in marker.splitlines() if line.startswith(OUTPUT_MARKER)), None)
                if encoded is None:
                    raise RuntimeError(f"{identifier} repeat {repeat} {backend}: missing schedule marker")
                signature = signatures(scenario, json.loads(encoded), backend)
                source = original[backend]
                rows.append({"scenario": identifier, "repeat": repeat, "backend": backend,
                             "overall_backend_status": result.status.value,
                             "attempt_status": "PARTIAL_SCHEDULE" if signature["scheduled_flow_count"] < signature["requested_flow_count"] else "ALL_FLOWS_SCHEDULED",
                             "scheduled_flow_count": signature["scheduled_flow_count"], "requested_flow_count": signature["requested_flow_count"],
                             "hnf_flow_count": signature["hnf_flow_count"], "HNF_set_sha256": signature["hnf_set_sha256"],
                             "instance_completion_sha256": signature["instance_completion_sha256"],
                             "matches_source_scheduled_count": signature["scheduled_flow_count"] == source["scheduled_flow_count"],
                             "matches_source_HNF_set": signature["hnf_set_sha256"] == source["hnf_set_sha256"],
                             "matches_source_instance_completion": signature["instance_completion_sha256"] == source["instance_completion_sha256"]})
    pairs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pairs[(row["scenario"], row["backend"])].append(row)
    for values in pairs.values():
        values.sort(key=lambda row: row["repeat"])
        if len(values) != 2:
            raise RuntimeError("repeatability record is incomplete")
        same = all(values[0][key] == values[1][key] for key in ("scheduled_flow_count", "HNF_set_sha256", "instance_completion_sha256"))
        for row in values:
            row["matches_other_repeat"] = same
    return rows


def write_ordering_audit() -> None:
    (OUT / "ordering_audit.md").write_text(
        "# Pinned upstream ordering audit\n\n"
        "At `650a9665e7bafb70fcf19c9f0a247e1d7b885ffd`, `ProgramOptions.h` defaults H2S to `LOW_PERIOD_FLOWS_FIRST`. "
        "`HierarchicalHeuristicScheduling::scheduleSet()` builds a priority queue with that flow sorter. CELF "
        "(`CelfFlowQueuing::scheduleSet`) prioritizes configurations, dynamically re-rates entries, and is not governed "
        "by the same per-flow rank. `input_rank` in the P0 CSV is the canonical scenario-flow numeric ID; it is **not** "
        "a proven scheduler-admission rank.\n", encoding="utf-8")


def run(skip_replays: bool) -> None:
    frozen_hashes = {name: sha256_file(OUT / name) for name in FROZEN}
    identity = list(csv.DictReader((OUT / "unscheduled_flow_identity.csv").open(encoding="utf-8")))
    # The new parser must reproduce the phase-one HNF sets before its derived metrics are trusted.
    for identifier in ORDER:
        scale, topology = identifier[0], identifier.split("_", 1)[1]
        for attempt in ("h2s", "celf"):
            _, _, parsed = source_rows(identifier, attempt)
            frozen_hnf = sorted(row["flow_id"] for row in identity
                                if row["scale"] == scale and row["topology"] == topology
                                and row["backend"] == attempt.upper()
                                and row["flow_completion_class"] != "FULLY_SCHEDULED")
            if parsed["hnf_flow_ids"] != frozen_hnf:
                raise RuntimeError(f"{identifier} {attempt}: raw parser does not reproduce frozen phase-one HNF identity")
    candidate, ranks, business, rank_summary = candidate_rank_and_business(identity)
    write_csv(OUT / "candidate_route_diagnosis.csv", candidate)
    write_csv(OUT / "flow_rank_diagnosis.csv", ranks)
    write_csv(OUT / "business_kind_diagnosis.csv", business)
    write_csv(OUT / "rank_summary.csv", rank_summary)
    write_csv(OUT / "link_contention.csv", link_contention())
    write_ordering_audit()
    if not skip_replays:
        write_csv(OUT / "diagnostic_replay_repeatability.csv", repeatability())
    if frozen_hashes != {name: sha256_file(OUT / name) for name in FROZEN}:
        raise RuntimeError("a frozen phase-one artifact changed")
    (OUT / "mechanism_diagnostic_limits.json").write_bytes(canonical_json_bytes({
        "candidate_route_introspection": "CANDIDATE_ROUTE_INTROSPECTION_UNAVAILABLE",
        "reason": "The formal upstream P0 export carries candidate counts but not candidate paths. No upstream-core patch or approximate candidate generator was used.",
        "contention_scope": "Directional egress busy time uses scheduled slots only; it cannot quantify occupancy for ZERO_SCHEDULED HNF flows.",
        "replay_scope": "M_RING, M_ROR, and L_ROR; two serial repeats each; frozen workload and backend settings.",
        "frozen_artifact_sha256": frozen_hashes,
    }))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-replays", action="store_true")
    args = parser.parse_args()
    run(args.skip_replays)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
