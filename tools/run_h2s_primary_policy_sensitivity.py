"""exp18f: sensitivity of pre-existing H2S primary flow-sorting policies only."""
from __future__ import annotations

import argparse
import csv
import gzip
import itertools
import json
import os
import platform
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.h2s_jrs_backend import H2sJrsBackend, parse_backend_output
from tools.h2s_primary_policy_sensitivity import (
    FORMAL_POLICIES, ORDER, OUT, ROOT, SOURCE, UPSTREAM, Policy, assert_backend_config, assert_frozen,
    audit_pinned_upstream, backend_config, candidate_vector_sha, canonical_sha256, expected_instances,
    expected_qualification_order, flow_kind, h2s_policy_qualification_case, load_scenario, order_sha,
    parse_order, release_sha, scenario_path, sha256_file, timing_sha, topology_sha,
)
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_p0_hnf_diagnosis import raw as frozen_raw
from tools.run_p0_hnf_mechanism_diagnosis import signatures

EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
TIMEOUT_S = 30
VALID_VERDICTS = {"BUILTIN_POLICY_FULL_RECOVERY", "BUILTIN_POLICY_PARTIAL_RECOVERY",
                  "BUILTIN_POLICY_IDENTITY_ONLY_EFFECT", "BUILTIN_POLICY_NO_MEANINGFUL_EFFECT",
                  "BUILTIN_POLICY_BASELINE_PARITY_FAILED", "POLICY_PARAMETER_NOT_EFFECTIVE",
                  "POLICY_INTERVENTION_CONFOUNDED", "BUILTIN_POLICY_NONDETERMINISTIC", "INCONCLUSIVE"}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def shell(command: list[str]) -> str:
    return subprocess.run(command, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def clean_tree() -> bool:
    return not shell(["git", "status", "--porcelain"])


def scale_topology(scenario_id: str) -> tuple[str, str]:
    return tuple(scenario_id.split("_", 1))  # type: ignore[return-value]


def output_root(quick: bool) -> Path:
    return OUT / "quick_validation" if quick else OUT


def run_policy(path: Path, output: Path, policy: Policy, *, celf_fallback: bool) -> Any:
    backend = H2sJrsBackend(EXECUTABLE, h2s_flow_sorting=policy.cli_value,
                            h2s_tiebreak_mode="BASELINE", h2s_tiebreak_seed=0,
                            attempt_celf_fallback=celf_fallback)
    result = backend.synthesize(RecoverySynthesisRequest(path, solver_timeout_s=TIMEOUT_S, route_scope="all-reroute",
                                                         forwarding_model="stream-aware", output_directory=output))
    write_attempt_logs(output / "logs", result)
    return result


def parsed_attempt(scenario: dict[str, Any], attempt: dict[str, Any], algorithm: str, policy: Policy | None,
                   *, allow_noncanonical_signature: bool = False) -> dict[str, Any]:
    try:
        payload = parse_backend_output(str(attempt.get("stdout", "")))
        # The formal source scenarios have an 8 ms observation horizon and
        # therefore use the canonical HNF/instance signature.  The tiny
        # qualification scenario deliberately has a different horizon, so it
        # is only allowed to use a minimal schedule signature.  Keep that
        # exception explicit: it must never mask a formal-run parse failure.
        try:
            signature = signatures(scenario, payload, algorithm)
        except Exception:
            if not allow_noncanonical_signature:
                raise
            requested = int(payload["requested_flow_count"])
            scheduled = int(payload["scheduled_flow_count"])
            signature = {"requested_flow_count": requested, "scheduled_flow_count": scheduled,
                         "hnf_flow_count": requested - scheduled, "hnf_set_sha256": "",
                         "instance_completion_sha256": "", "hnf_flow_ids": [], "identities": []}
        complete = (signature["scheduled_flow_count"] == signature["requested_flow_count"] == len(scenario["tt_flows"])
                    and bool(payload.get("upstream_verifier_pass")) and bool(attempt.get("project_static_checker_pass")))
        order = parse_order(str(attempt.get("stdout", "")), scenario, policy) if algorithm == "H2S" and policy else []
        return {"algorithm": algorithm, "payload": payload, "signature": signature, "complete": complete,
                "status": "SUCCESS_H2S" if algorithm == "H2S" and complete else "SUCCESS_CELF_FALLBACK" if complete else "HEURISTIC_NOT_FOUND",
                "order": order, "order_sha": order_sha(order) if order else "", "candidate_vector_sha": candidate_vector_sha(payload),
                "upstream_verifier": bool(payload.get("upstream_verifier_pass")),
                "static_checker": bool(attempt.get("project_static_checker_pass")), "wall_ms": float(attempt.get("wall_ms") or 0),
                "peak_rss_bytes": int(attempt.get("peak_rss_bytes") or 0)}
    except Exception as error:
        return {"algorithm": algorithm, "payload": None, "signature": None, "complete": False,
                "status": "OUTPUT_INVALID", "order": [], "order_sha": "", "candidate_vector_sha": "",
                "upstream_verifier": False, "static_checker": False, "wall_ms": float(attempt.get("wall_ms") or 0),
                "peak_rss_bytes": int(attempt.get("peak_rss_bytes") or 0), "parse_error": str(error)}


def observe(scenario: dict[str, Any], result: Any, policy: Policy, output: Path,
            *, allow_noncanonical_signature: bool = False) -> dict[str, Any]:
    attempts = {str(item.get("algorithm", "")).upper(): item for item in result.statistics.get("attempts", [])}
    h2s = parsed_attempt(scenario, attempts["H2S"], "H2S", policy,
                         allow_noncanonical_signature=allow_noncanonical_signature) if "H2S" in attempts else None
    if h2s is None:
        raise RuntimeError("H2S primary attempt missing")
    celf = parsed_attempt(scenario, attempts["CELF"], "CELF", None,
                          allow_noncanonical_signature=allow_noncanonical_signature) if "CELF" in attempts else None
    formal = h2s if h2s["complete"] else (celf if celf and celf["complete"] else h2s)
    manifest_path = output / "input_manifest.json"
    input_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    command = attempts["H2S"].get("command", [])
    command_ok = ("--flow-sorting" in command and command[command.index("--flow-sorting") + 1] == str(policy.cli_value)
                  and "--h2s-tiebreak-mode" in command and command[command.index("--h2s-tiebreak-mode") + 1] == "BASELINE"
                  and "--h2s-tiebreak-seed" in command and command[command.index("--h2s-tiebreak-seed") + 1] == "0")
    return {"result_status": result.status.value, "h2s": h2s, "celf": celf, "formal": formal,
            "total_wall_ms": float(result.timings_ms.get("total_backend", 0)),
            "peak_rss_bytes": max([item["peak_rss_bytes"] for item in (h2s, celf) if item] or [0]),
            "input_manifest": input_manifest, "h2s_command": command, "policy_command_ok": command_ok}


def original_integrity(scenario_id: str, scenario: dict[str, Any]) -> dict[str, Any]:
    return {"scenario": scenario_id, "source_scenario_sha256": sha256_file(scenario_path(scenario_id)),
            "workload_sha256": canonical_sha256(scenario), "topology_sha256": topology_sha(scenario),
            "timing_sha256": timing_sha(scenario), "release_sha256": release_sha(scenario),
            "flow_count": len(scenario["tt_flows"]), "expected_instances": expected_instances(scenario)}


def source_signature(scenario_id: str, algorithm: str) -> tuple[dict[str, Any], str]:
    scenario, _, payload = frozen_raw(scenario_id, algorithm.lower())
    return signatures(scenario, payload, algorithm), candidate_vector_sha(payload)


def policy_run_row(scenario_id: str, policy: Policy, repeat: int, scenario: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    h2s, celf, formal = observed["h2s"], observed["celf"], observed["formal"]
    hs, cs = h2s["signature"], celf["signature"] if celf else None
    total = len(scenario["tt_flows"])
    return {"scenario": scenario_id, "scale": scale_topology(scenario_id)[0], "topology": scale_topology(scenario_id)[1],
            "policy_enum": policy.enum_name, "policy_cli_value": policy.cli_value, "policy_tag": policy.tag, "repeat": repeat,
            "H2S_status": h2s["status"], "H2S_scheduled_count": hs["scheduled_flow_count"] if hs else 0,
            "H2S_scheduled_ratio": (hs["scheduled_flow_count"] / total) if hs else 0, "H2S_HNF_count": hs["hnf_flow_count"] if hs else total,
            "H2S_HNF_set_sha": hs["hnf_set_sha256"] if hs else "", "H2S_instance_completion_sha": hs["instance_completion_sha256"] if hs else "",
            "H2S_order_sha": h2s["order_sha"], "candidate_vector_sha": h2s["candidate_vector_sha"], "H2S_complete": h2s["complete"],
            "upstream_verifier": h2s["upstream_verifier"], "static_checker": h2s["static_checker"], "H2S_ms": h2s["wall_ms"],
            "CELF_attempted": celf is not None, "CELF_status": celf["status"] if celf else "NOT_ATTEMPTED",
            "CELF_scheduled_count": cs["scheduled_flow_count"] if cs else 0, "CELF_HNF_count": cs["hnf_flow_count"] if cs else "",
            "CELF_HNF_set_sha": cs["hnf_set_sha256"] if cs else "", "CELF_complete": celf["complete"] if celf else False,
            "CELF_ms": celf["wall_ms"] if celf else 0.0, "formal_backend_complete": formal["complete"],
            "total_wall_ms": observed["total_wall_ms"], "peak_RSS": observed["peak_rss_bytes"],
            "input_topology_sha": observed["input_manifest"].get("topology_sha256", ""),
            "input_scenario_sha": observed["input_manifest"].get("scenario_sha256", ""), "policy_command_ok": observed["policy_command_ok"]}


def baseline_parity(rows: list[dict[str, Any]], observations: dict[tuple[str, str, int], dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    result = []; passed = True
    for scenario_id in ORDER:
        expected_count = 348 if scenario_id.startswith("M_") else 914
        for repeat in (1, 2):
            observed = observations[(scenario_id, "LOW_PERIOD", repeat)]
            for algorithm in ("H2S", "CELF"):
                entry = observed[algorithm.lower()]
                source, candidate_sha = source_signature(scenario_id, algorithm)
                signature = entry["signature"] if entry else None
                row = {"scenario": scenario_id, "repeat": repeat, "algorithm": algorithm, "attempted": entry is not None,
                       "source_scheduled_count": source["scheduled_flow_count"], "observed_scheduled_count": signature["scheduled_flow_count"] if signature else -1,
                       "expected_scale_scheduled_count": expected_count, "source_HNF_set_sha": source["hnf_set_sha256"],
                       "observed_HNF_set_sha": signature["hnf_set_sha256"] if signature else "", "source_instance_completion_sha": source["instance_completion_sha256"],
                       "observed_instance_completion_sha": signature["instance_completion_sha256"] if signature else "",
                       "source_candidate_vector_sha": candidate_sha, "observed_candidate_vector_sha": entry["candidate_vector_sha"] if entry else ""}
                row["count_exact"] = row["source_scheduled_count"] == row["observed_scheduled_count"] == expected_count
                row["hnf_exact"] = row["source_HNF_set_sha"] == row["observed_HNF_set_sha"]
                row["instance_exact"] = row["source_instance_completion_sha"] == row["observed_instance_completion_sha"]
                # Candidate-vector parity is an H2S intervention invariant.
                # CELF is retained only as the production fallback reference.
                row["candidate_exact"] = algorithm != "H2S" or row["source_candidate_vector_sha"] == row["observed_candidate_vector_sha"]
                row["parity_pass"] = all(row[name] for name in ("count_exact", "hnf_exact", "instance_exact", "candidate_exact"))
                result.append(row); passed &= row["parity_pass"]
    return result, passed


def qualification(output: Path, audit: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    root = output / "policy_qualification"; path = root / "scenario.json"; scenario = h2s_policy_qualification_case(); write_json(path, scenario)
    rows = []; hashes = []; candidates = []; all_valid = True
    for policy in FORMAL_POLICIES:
        observed = observe(scenario, run_policy(path, root / "raw_backend_output" / policy.tag, policy, celf_fallback=False), policy,
                           root / "raw_backend_output" / policy.tag, allow_noncanonical_signature=True)
        actual = [row["flow_id"] for row in observed["h2s"]["order"]]
        expected = expected_qualification_order(policy)
        row = {"policy_tag": policy.tag, "policy_enum": policy.enum_name, "policy_cli_value": policy.cli_value,
               "H2S_status": observed["h2s"]["status"], "order_sha256": observed["h2s"]["order_sha"],
               "candidate_vector_sha256": observed["h2s"]["candidate_vector_sha"], "order_count": len(actual),
               "unique_positions": len({row["position"] for row in observed["h2s"]["order"]}), "actual_order": ";".join(actual),
               "expected_order": ";".join(expected), "order_matches_comparator": actual == expected,
               "policy_reaches_upstream": observed["policy_command_ok"], "order_export_valid": len(actual) == len(scenario["tt_flows"])}
        rows.append(row); hashes.append(row["order_sha256"]); candidates.append(row["candidate_vector_sha256"])
        all_valid &= row["order_matches_comparator"] and row["policy_reaches_upstream"] and row["order_export_valid"]
    effective = len(set(hashes)) >= 2
    candidate_invariant = len(set(candidates)) == 1 and bool(candidates[0])
    qualified = all_valid and effective and candidate_invariant
    verdict = {"policy_parameter_qualified": qualified, "policy_propagation": all_valid, "at_least_two_orders_differ": effective,
               "candidate_vector_invariant": candidate_invariant, "exp18d_tiebreak_patch_state": "DISABLED",
               "formal_verdict": "QUALIFIED" if qualified else "POLICY_PARAMETER_NOT_EFFECTIVE", "audit_default": audit["default_enum"]}
    write_csv(output / "policy_qualification.csv", rows); write_json(output / "qualification_verdict.json", verdict)
    return rows, verdict, qualified


def repeatability(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool, set[tuple[str, str]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows: grouped[(row["scenario"], row["policy_tag"])].append(row)
    records = []; failures: set[tuple[str, str]] = set()
    for key, pair in sorted(grouped.items()):
        pair.sort(key=lambda row: row["repeat"])
        if len(pair) != 2: failures.add(key); continue
        left, right = pair
        exact = {"order_sha_exact": left["H2S_order_sha"] == right["H2S_order_sha"],
                 "candidate_sha_exact": left["candidate_vector_sha"] == right["candidate_vector_sha"],
                 "scheduled_count_exact": left["H2S_scheduled_count"] == right["H2S_scheduled_count"],
                 "HNF_set_exact": left["H2S_HNF_set_sha"] == right["H2S_HNF_set_sha"],
                 "instance_completion_exact": left["H2S_instance_completion_sha"] == right["H2S_instance_completion_sha"]}
        ok = all(exact.values())
        if not ok: failures.add(key)
        records.append({"scenario": key[0], "policy_tag": key[1], **exact, "repeatability_pass": ok})
    return records, not failures, failures


def policy_summary(rows: list[dict[str, Any]], failures: set[tuple[str, str]]) -> list[dict[str, Any]]:
    result = []
    for policy in FORMAL_POLICIES:
        for scenario_id in ORDER:
            values = sorted((row for row in rows if row["policy_tag"] == policy.tag and row["scenario"] == scenario_id), key=lambda row: row["repeat"])
            first = values[0]
            result.append({"policy_tag": policy.tag, "policy_enum": policy.enum_name, "scenario": scenario_id,
                           "scheduled_count": first["H2S_scheduled_count"], "total_flows": first["total_flows"] if "total_flows" in first else (352 if scenario_id.startswith("M_") else 928),
                           "HNF_count": first["H2S_HNF_count"], "H2S_complete": first["H2S_complete"],
                           "formal_backend_complete": first["formal_backend_complete"], "median_H2S_ms": statistics.median(row["H2S_ms"] for row in values),
                           "max_RSS": max(row["peak_RSS"] for row in values), "repeat_consistent": (scenario_id, policy.tag) not in failures,
                           "order_sha": first["H2S_order_sha"], "HNF_sha": first["H2S_HNF_set_sha"]})
    return result


def pairwise_hnf(rows: list[dict[str, Any]], observations: dict[tuple[str, str, int], dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    comparisons = []; identities = []
    for scenario_id in ORDER:
        sets = {}
        for policy in FORMAL_POLICIES:
            signature = observations[(scenario_id, policy.tag, 1)]["h2s"]["signature"]
            values = set(signature["hnf_flow_ids"]) if signature else set()
            sets[policy.tag] = values
            identities.extend({"scenario": scenario_id, "policy_tag": policy.tag, "policy_enum": policy.enum_name, "flow_id": flow_id,
                               "flow_kind": flow_kind(flow_id)} for flow_id in sorted(values))
        for left, right in itertools.combinations(FORMAL_POLICIES, 2):
            a, b = sets[left.tag], sets[right.tag]; union = a | b
            comparisons.append({"scenario": scenario_id, "policy_a": left.tag, "policy_b": right.tag, "HNF_count_a": len(a), "HNF_count_b": len(b),
                                "intersection_count": len(a & b), "union_count": len(union), "jaccard": len(a & b) / len(union) if union else 1.0,
                                "exact_equal": a == b})
    return identities, comparisons


def flow_trajectory(observations: dict[tuple[str, str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for scenario_id in ORDER:
        scenario = load_scenario(scenario_id); by_policy = {}
        for policy in FORMAL_POLICIES:
            signature = observations[(scenario_id, policy.tag, 1)]["h2s"]["signature"]
            by_policy[policy.tag] = {row["flow_id"]: row["flow_completion_class"] for row in signature["identities"]}
        for flow in sorted(scenario["tt_flows"], key=lambda row: row["id"]):
            values = {policy.tag: by_policy[policy.tag][flow["id"]] for policy in FORMAL_POLICIES}
            result.append({"scenario": scenario_id, "flow_id": flow["id"], "flow_kind": flow_kind(flow["id"]),
                           "period_ns": int(round(flow["period_s"] * 1e9)), "frame_size": flow["packet_size_bytes"], "source": flow["source"],
                           **{f"{tag}_completion": value for tag, value in values.items()},
                           "success_policy_count": sum(value == "FULLY_SCHEDULED" for value in values.values()),
                           "always_HNF": all(value != "FULLY_SCHEDULED" for value in values.values()),
                           "baseline_HNF_rescued_by_other_policy": values["LOW_PERIOD"] != "FULLY_SCHEDULED" and any(value == "FULLY_SCHEDULED" for tag, value in values.items() if tag != "LOW_PERIOD"),
                           "baseline_success_regressed_under_other_policy": values["LOW_PERIOD"] == "FULLY_SCHEDULED" and any(value != "FULLY_SCHEDULED" for tag, value in values.items() if tag != "LOW_PERIOD")})
    return result


def cross_topology(observations: dict[tuple[str, str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for scale in ("M", "L"):
        ids = [f"{scale}_{name}" for name in ("RING", "REDSTAR", "ROR")]
        for policy in FORMAL_POLICIES:
            sets = {scenario_id: set(observations[(scenario_id, policy.tag, 1)]["h2s"]["signature"]["hnf_flow_ids"]) for scenario_id in ids}
            for left, right in itertools.combinations(ids, 2):
                union = sets[left] | sets[right]
                result.append({"scale": scale, "policy_tag": policy.tag, "topology_a": left.split("_", 1)[1], "topology_b": right.split("_", 1)[1],
                               "HNF_count_a": len(sets[left]), "HNF_count_b": len(sets[right]), "intersection_count": len(sets[left] & sets[right]),
                               "union_count": len(union), "jaccard": len(sets[left] & sets[right]) / len(union) if union else 1.0,
                               "exact_equal": sets[left] == sets[right]})
    return result


def archive_logs(output: Path) -> None:
    for path in list(output.rglob("*_stdout.log")) + list(output.rglob("*_stderr.log")):
        with path.open("rb") as source, Path(f"{path}.gz").open("wb") as compressed:
            with gzip.GzipFile(filename=f"{path.name}.gz", mode="wb", fileobj=compressed, mtime=0) as target:
                target.write(source.read())
        path.unlink()


def write_audit(path: Path, audit: dict[str, Any]) -> None:
    lines = ["# Built-in H2S primary policy audit", "", f"Pinned upstream commit: `{audit['upstream_commit']}`.", "",
             "The upstream `-f,--flow-sorting` option is an integer enum. `LOW_PERIOD_FLOWS_FIRST` (4) is the historical default. `HIGHEST_TRAFFIC_FLOWS_FIRST` (0) was discovered but is intentionally recorded-only: it is not part of the preregistered formal matrix.", "",
             "| tag | enum | CLI | class | audited comparator semantics | ordering inputs read |", "|---|---|---:|---|---|---|"]
    lines += [f"| {row['tag']} | {row['enum_name']} | {row['cli_value']} | {row['class_name']} | {row['comparator_semantics']} | {', '.join(row['ordering_inputs'])} |" for row in audit["formal_policies"]]
    lines += ["", "Fixed but not swept: configuration rating 1, placement 0, offensive planning false, DIJKSTRA_OVERLAP routing, and candidate-path budget 5.", "",
             "The existing exp18d tie-break extension remains in the local upstream worktree solely for order export and its conditional seeded mode. Every exp18f H2S command explicitly supplies `--h2s-tiebreak-mode BASELINE --h2s-tiebreak-seed 0`; therefore no seeded comparator path is selected. No upstream source file is changed by this experiment."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def reports(output: Path, *, quick: bool, parent_commit: str, implementation_commit: str, frozen: dict[str, str], audit: dict[str, Any],
            qualification_verdict: dict[str, Any], scenarios: dict[str, dict[str, Any]], rows: list[dict[str, Any]],
            observations: dict[tuple[str, str, int], dict[str, Any]], baseline: list[dict[str, Any]], baseline_ok: bool) -> int:
    write_audit(output / "built_in_policy_audit.md", audit); write_csv(output / "baseline_parity.csv", baseline); write_csv(output / "policy_runs.csv", rows)
    if quick:
        verdict_name = "INCONCLUSIVE"; repeats = []; repeat_ok = False; failures: set[tuple[str, str]] = set(); summary = []
        identity_changed = False; cross_topology_invariant = False
    else:
        repeats, repeat_ok, failures = repeatability(rows); write_csv(output / "repeatability.csv", repeats)
        summary = policy_summary(rows, failures); write_csv(output / "policy_summary.csv", summary)
        complete_matrix = len(rows) == len(ORDER) * len(FORMAL_POLICIES) * 2
        comparison: list[dict[str, Any]] = []
        cross_rows: list[dict[str, Any]] = []
        if complete_matrix:
            hnf, comparison = pairwise_hnf(rows, observations); write_csv(output / "hnf_by_policy.csv", hnf); write_csv(output / "hnf_policy_comparison.csv", comparison)
            write_csv(output / "flow_policy_trajectory.csv", flow_trajectory(observations)); cross_rows = cross_topology(observations); write_csv(output / "cross_topology_policy_sets.csv", cross_rows)
            matrix = []
            for policy in FORMAL_POLICIES:
                values = {row["scenario"]: row for row in summary if row["policy_tag"] == policy.tag}
                matrix.append({"Policy": policy.tag, **{scenario_id: f"{values[scenario_id]['scheduled_count']}/{values[scenario_id]['total_flows']}; complete={values[scenario_id]['H2S_complete']}" for scenario_id in ORDER}})
            write_csv(output / "policy_matrix.csv", matrix, ["Policy", *ORDER])
        h2s_complete = {policy.tag: sum(row["H2S_complete"] for row in summary if row["policy_tag"] == policy.tag) for policy in FORMAL_POLICIES}
        formal_complete = {policy.tag: sum(row["formal_backend_complete"] for row in summary if row["policy_tag"] == policy.tag) for policy in FORMAL_POLICIES}
        eligible = [policy.tag for policy in FORMAL_POLICIES if h2s_complete[policy.tag] == 6 and all((scenario_id, policy.tag) not in failures for scenario_id in ORDER)]
        fallback = [policy.tag for policy in FORMAL_POLICIES if formal_complete[policy.tag] == 6 and policy.tag not in eligible]
        baseline_counts = {row["scenario"]: row["scheduled_count"] for row in summary if row["policy_tag"] == "LOW_PERIOD"}
        improved = any(row["scheduled_count"] > baseline_counts[row["scenario"]] for row in summary if row["policy_tag"] != "LOW_PERIOD")
        identity_changed = any(not row["exact_equal"] for row in comparison)
        cross_topology_invariant = bool(cross_rows) and all(row["exact_equal"] for row in cross_rows)
        if not qualification_verdict["policy_parameter_qualified"]:
            verdict_name = "POLICY_PARAMETER_NOT_EFFECTIVE"
        elif not baseline_ok:
            verdict_name = "BUILTIN_POLICY_BASELINE_PARITY_FAILED"
        elif not repeat_ok:
            verdict_name = "BUILTIN_POLICY_NONDETERMINISTIC"
        elif any(tag != "LOW_PERIOD" for tag in eligible):
            verdict_name = "BUILTIN_POLICY_FULL_RECOVERY"
        elif improved:
            verdict_name = "BUILTIN_POLICY_PARTIAL_RECOVERY"
        elif identity_changed:
            verdict_name = "BUILTIN_POLICY_IDENTITY_ONLY_EFFECT"
        else:
            verdict_name = "BUILTIN_POLICY_NO_MEANINGFUL_EFFECT"
    assert verdict_name in VALID_VERDICTS
    archive_logs(output)
    policy_orders = []
    for (scenario_id, tag, repeat), value in sorted(observations.items()):
        for row in value["h2s"]["order"]:
            policy_orders.append({"scenario": scenario_id, "policy_tag": tag, "repeat": repeat, **row})
    with gzip.GzipFile(filename="h2s_order_by_policy.jsonl.gz", mode="wb", fileobj=(output / "h2s_order_by_policy.jsonl.gz").open("wb"), mtime=0) as handle:
        for row in policy_orders: handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    h2s_complete = {policy.tag: sum(row.get("H2S_complete", False) for row in summary if row.get("policy_tag") == policy.tag) for policy in FORMAL_POLICIES}
    formal_complete = {policy.tag: sum(row.get("formal_backend_complete", False) for row in summary if row.get("policy_tag") == policy.tag) for policy in FORMAL_POLICIES}
    eligible = [policy.tag for policy in FORMAL_POLICIES if h2s_complete[policy.tag] == 6 and not quick]
    fallback = [policy.tag for policy in FORMAL_POLICIES if formal_complete[policy.tag] == 6 and policy.tag not in eligible and not quick]
    candidate_invariant = qualification_verdict["candidate_vector_invariant"] and all(len({row["candidate_vector_sha"] for row in rows if row["scenario"] == scenario_id}) == 1 for scenario_id in ORDER) if rows else qualification_verdict["candidate_vector_invariant"]
    verdict = {"formal_verdict": verdict_name, "baseline_parity_passed": baseline_ok, "policy_parameter_qualified": qualification_verdict["policy_parameter_qualified"],
               "candidate_vector_invariant": candidate_invariant, "repeatability_passed": repeat_ok, "H2S_complete_scenarios_by_policy": h2s_complete,
               "formal_backend_complete_scenarios_by_policy": formal_complete, "pf_eligible_h2s_policies": eligible,
               "pf_eligible_with_fallback_policies": fallback, "best_scheduled_count_by_policy": {policy.tag: max((row["scheduled_count"] for row in summary if row["policy_tag"] == policy.tag), default=0) for policy in FORMAL_POLICIES},
               "next_stage_recommendation": "REALISTIC_PF_COST_WITH_QUALIFIED_H2S_POLICY" if eligible else "REVIEW_POLICY_RESULT_BEFORE_WORKLOAD_CALIBRATION" if verdict_name == "BUILTIN_POLICY_PARTIAL_RECOVERY" else "TT_WORKLOAD_DENSITY_CALIBRATION"}
    write_json(output / "verdict.json", verdict)
    content = ["# exp18f: Built-in H2S primary policy sensitivity", "",
               "## Research answers", "",
               "1. This experiment tests only whether an already built-in upstream H2S sorter can construct a complete healthy P0 on the frozen realistic workload; it is not a new scheduling-algorithm design study.", "",
               "2. It returns to the byte-identical original exp18 scenarios and original deadlines so any success would apply directly to the original realistic workload.", "",
               "3. Deadline relaxation stops here because exp18e found no P0 improvement even through D=T.", "",
               "4. The preregistered built-in sorters are LOW_PERIOD_FLOWS_FIRST, LOWEST_TRAFFIC_FLOWS_FIRST, LOWEST_ID_FIRST, and SOURCE_NODE_SORTING.", "",
               "5. LOW_PERIOD is period-ascending, then larger-frame-first, then deterministic ID; LOWEST_TRAFFIC is ascending frame-size/period; LOWEST_ID is numeric-ID ascending; SOURCE_NODE prioritizes source fan-out, then destination, traffic, and ID. The full source audit and read fields are in `built_in_policy_audit.md`.", "",
               "6. The sole formal intervention is the upstream H2S `--flow-sorting` policy value.", "",
               "7. Topology, workload roles and population, periods, original deadlines and releases, payloads, route scope, DIJKSTRA_OVERLAP, K=5, 100 ns quantum, seed 1024, one thread, 30 s per heuristic, 8192 MB, CELF behavior, and all other H2S knobs are fixed.", "",
               f"8. LOW_PERIOD historical baseline parity is **{'PASS' if baseline_ok else 'FAIL'}**: counts, HNF identities, instance completion, and H2S candidate vectors match the frozen reference.", "",
               f"9. The policies {'did' if qualification_verdict['at_least_two_orders_differ'] else 'did not'} produce distinct actual H2S orders; qualification passed before the formal matrix.", "",
               f"10. Candidate vectors {'remained invariant' if candidate_invariant else 'did not remain invariant'} across policies for each scenario.", "",
               "11. The per-policy, per-scenario scheduled-flow results are:", ""]
    if summary:
        content += ["| policy | M_RING | M_REDSTAR | M_ROR | L_RING | L_REDSTAR | L_ROR |", "|---|---:|---:|---:|---:|---:|---:|"]
        for policy in FORMAL_POLICIES:
            values = {row["scenario"]: row for row in summary if row["policy_tag"] == policy.tag}
            content.append("| " + policy.tag + " | " + " | ".join(f"{values[sid]['scheduled_count']}/{values[sid]['total_flows']}" for sid in ORDER) + " |")
    eligible_text = ", ".join(eligible) if eligible else "none"
    fallback_text = ", ".join(fallback) if fallback else "none"
    complete_scenario_text = "At least one H2S policy reaches complete P0 in this matrix" if any(h2s_complete.values()) else "No H2S policy reaches complete P0 in this matrix"
    content += ["", f"12. {complete_scenario_text}; H2S complete-scenario counts are {h2s_complete}.", "",
               f"13. There {'is' if eligible else 'is no'} policy with 6/6 H2S-complete scenarios.", "",
               f"14. PF-eligible H2S policies: {eligible_text}.", "",
               f"15. Policies with only 6/6 CELF-fallback completeness: {fallback_text}; none is counted as an H2S success.", "",
               f"16. Different policies {'do' if identity_changed else 'do not'} change HNF identity in at least one same-scenario pair.", "",
               f"17. The matrix {'does' if verdict_name == 'BUILTIN_POLICY_PARTIAL_RECOVERY' else 'does not'} improve scheduled-flow cardinality over LOW_PERIOD.", "",
               f"18. Cross-topology HNF-set invariance {'remains' if cross_topology_invariant else 'does not remain'} for every tested scale-policy comparison.", "",
               f"19. Repeatability {'passes' if repeat_ok else 'does not pass'} for order SHA, candidate vector, scheduled count, HNF set, and instance completion.", "",
               "20. These heuristic results cannot prove that the original workload is infeasible.", "",
               "21. No policy constructed 6/6 complete P0 here; if one had done so with the upstream verifier and independent static checker passing, it would demonstrate that the original frozen workload has a complete P0.", "",
               "22. The result cannot establish LOW_PERIOD as the unique root cause of historical HNF.", "",
               f"23. Recommended next stage: `{verdict['next_stage_recommendation']}`; do not automatically start it.", "",
               "No PF, fault enumeration, Profile Store study, parallel campaign, OMNeT++, or INET simulation is run by exp18f. The experiment stops after this policy matrix and awaits manual direction."]
    (output / "summary.md").write_text("\n".join(content) + "\n", encoding="utf-8")
    artifacts = {str(path.relative_to(output)): sha256_file(path) for path in output.rglob("*") if path.is_file() and path.name != "analysis_manifest.json"}
    write_json(output / "analysis_manifest.json", {"experiment": "exp18f_h2s_primary_policy_sensitivity", "parent_commit": parent_commit,
               "implementation_commit": implementation_commit, "results_commit": "RECORDED_BY_SUBSEQUENT_GIT_HISTORY", "historical_tree_sha256": frozen,
               "exp18_source_scenario_sha256": {scenario_id: sha256_file(scenario_path(scenario_id)) for scenario_id in ORDER},
               "exp18b_verdict_sha256": sha256_file(ROOT / "results/p0_hnf_diagnosis/mechanism_verdict.md"), "exp18c_verdict_sha256": sha256_file(ROOT / "results/candidate_k_sensitivity/research_direction_assessment.json"),
               "exp18d_verdict_sha256": sha256_file(ROOT / "results/h2s_multistart_sensitivity/verdict.json"), "exp18e_verdict_sha256": sha256_file(ROOT / "results/deadline_feasibility_calibration/calibration_verdict.json"),
               "advanced_flow_scheduler_repository": "https://github.com/gepperho/AdvancedFlowScheduler.git", "advanced_flow_scheduler_commit": shell(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"]),
               "upstream_semantic_patch_sha256": sha256_file(ROOT / "third_party_patches/advanced_flow_scheduler/exp15_semantics.patch"), "exp18d_tiebreak_patch_state": "DISABLED",
               "formal_policy_enum_list": [{"tag": p.tag, "enum": p.enum_name, "cli": p.cli_value} for p in FORMAL_POLICIES], "backend_config": backend_config(), "artifact_sha256": artifacts})
    assert_frozen()
    return 0 if (quick and qualification_verdict["policy_parameter_qualified"] and baseline_ok) or (not quick and verdict_name in {"BUILTIN_POLICY_FULL_RECOVERY", "BUILTIN_POLICY_PARTIAL_RECOVERY", "BUILTIN_POLICY_IDENTITY_ONLY_EFFECT", "BUILTIN_POLICY_NO_MEANINGFUL_EFFECT"}) else 2


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--quick", action="store_true"); parser.add_argument("--implementation-commit")
    args = parser.parse_args(); quick = args.quick; output = output_root(quick)
    if not EXECUTABLE.is_file(): raise SystemExit(f"missing H2S executable: {EXECUTABLE}")
    if not quick and not clean_tree(): raise SystemExit("full campaign requires a clean implementation commit")
    if output.exists(): raise SystemExit(f"refusing to overwrite existing evidence: {output}")
    parent = shell(["git", "rev-parse", "HEAD"]); implementation = args.implementation_commit or parent; frozen = assert_frozen(); config = backend_config(); assert_backend_config(config)
    audit = audit_pinned_upstream(); scenarios = {scenario_id: load_scenario(scenario_id) for scenario_id in ORDER}
    source_rows = [original_integrity(scenario_id, scenario) for scenario_id, scenario in scenarios.items()]
    output.mkdir(parents=True); write_json(output / "environment.json", {"platform": platform.platform(), "python": platform.python_version(), "os_uname": list(os.uname()),
               "executable_sha256": sha256_file(EXECUTABLE), "backend_config": config, "mode": "quick" if quick else "full"})
    write_json(output / "source_scenario_manifest.json", {"input_source": "direct byte-identical frozen exp18 scenarios; generator not invoked", "historical_tree_sha256": frozen, "scenarios": source_rows})
    _, qualification_verdict, qualified = qualification(output, audit)
    if not qualified:
        return reports(output, quick=quick, parent_commit=parent, implementation_commit=implementation, frozen=frozen, audit=audit, qualification_verdict=qualification_verdict, scenarios=scenarios, rows=[], observations={}, baseline=[], baseline_ok=False)
    planned = [("M_RING", FORMAL_POLICIES[0], 1), ("M_RING", FORMAL_POLICIES[1], 1)] if quick else [(scenario_id, policy, repeat) for policy in FORMAL_POLICIES for scenario_id in ORDER for repeat in (1, 2)]
    rows = []; observations: dict[tuple[str, str, int], dict[str, Any]] = {}
    for scenario_id, policy, repeat in planned:
        path = scenario_path(scenario_id); run_out = output / "raw_backend_output" / scenario_id / policy.tag / f"R{repeat}"
        observed = observe(scenarios[scenario_id], run_policy(path, run_out, policy, celf_fallback=True), policy, run_out)
        observations[(scenario_id, policy.tag, repeat)] = observed; rows.append(policy_run_row(scenario_id, policy, repeat, scenarios[scenario_id], observed))
        if not quick and policy.tag == "LOW_PERIOD" and scenario_id == ORDER[-1] and repeat == 2:
            baseline, baseline_ok = baseline_parity(rows, observations)
            if not baseline_ok:
                return reports(output, quick=False, parent_commit=parent, implementation_commit=implementation, frozen=frozen, audit=audit, qualification_verdict=qualification_verdict, scenarios=scenarios, rows=rows, observations=observations, baseline=baseline, baseline_ok=False)
    if quick:
        observed = observations[("M_RING", "LOW_PERIOD", 1)]; baseline = []
        for algorithm in ("H2S", "CELF"):
            entry = observed[algorithm.lower()]; source, candidate = source_signature("M_RING", algorithm); signature = entry["signature"] if entry else None
            baseline.append({"scenario": "M_RING", "repeat": 1, "algorithm": algorithm, "attempted": entry is not None,
                             "source_scheduled_count": source["scheduled_flow_count"], "observed_scheduled_count": signature["scheduled_flow_count"] if signature else -1,
                             "source_candidate_vector_sha": candidate, "observed_candidate_vector_sha": entry["candidate_vector_sha"] if entry else "",
                             "parity_pass": bool(signature and signature["scheduled_flow_count"] == source["scheduled_flow_count"] and signature["hnf_set_sha256"] == source["hnf_set_sha256"] and signature["instance_completion_sha256"] == source["instance_completion_sha256"] and (algorithm != "H2S" or entry["candidate_vector_sha"] == candidate))})
        baseline_ok = all(row["parity_pass"] for row in baseline)
    else:
        baseline, baseline_ok = baseline_parity(rows, observations)
    return reports(output, quick=quick, parent_commit=parent, implementation_commit=implementation, frozen=frozen, audit=audit, qualification_verdict=qualification_verdict, scenarios=scenarios, rows=rows, observations=observations, baseline=baseline, baseline_ok=baseline_ok)


if __name__ == "__main__": raise SystemExit(main())
