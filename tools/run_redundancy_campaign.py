#!/usr/bin/env python3
"""Serial, checkpointed exp12 production campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.analyze_critical_links import discover
from tools.approximate_equivalence import Policy, agglomerate, build_pre_fault_features
from tools.generate_redundancy_scenarios import GENERATOR_VERSION, LEVELS, generate
from tools.omnet_runner import run_omnet
from tools.profile_store import build_store, canonical_bytes, semantic_profile_hash
from tools.run_approximate_campaign import run_policy
from tools.scenario_model import load_scenario
from tools.topology_redundancy_metrics import connectivity_margin, switch_graph

POLICIES = (Policy("J100", "JACCARD", 1.0), Policy("J040", "JACCARD", .4),
            Policy("J020", "JACCARD", .2))
SOLVER_TIMEOUT_MS = 30_000
EXP10_CAMPAIGN_SHA = "93ffb1fab5670075fe9d74899844b584481e4b4e68b2dbda9f5371beff31c278"
EXP11_CAMPAIGN_SHA = "c7b85e2258851bb6f65a4c55d23d07d0883348a723f38827b04dd34e8cce48d1"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_value(value: object) -> str:
    return sha_bytes(canonical_bytes(value))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE).stdout.strip()


def file_sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def read_checkpoint(path: Path, identity: dict) -> dict:
    if not path.exists():
        return {"schema_version": 1, "identity": identity, "completed": [], "updated_at": None}
    value = json.loads(path.read_text())
    if value.get("identity") != identity:
        raise RuntimeError("STALE_CHECKPOINT: campaign identity differs")
    return value


def save_checkpoint(path: Path, checkpoint: dict, stage: str) -> None:
    if stage not in checkpoint["completed"]: checkpoint["completed"].append(stage)
    checkpoint["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(checkpoint))


def validate_frozen(model, frozen: dict) -> None:
    routes = {row["flow_id"]: row for row in frozen["logical_routes"]}
    links = {link.id: {link.endpoint_a, link.endpoint_b} for link in model.links}
    expected = {flow.id for flow in model.tt_flows}
    if set(routes) != expected: raise RuntimeError("frozen route TT coverage mismatch")
    for flow in model.tt_flows:
        route = routes[flow.id]
        if route["node_path"][0] != flow.source or route["node_path"][-1] != flow.destination:
            raise RuntimeError(f"frozen route endpoint mismatch: {flow.id}")
        if len(route["node_path"]) != len(route["link_path"]) + 1:
            raise RuntimeError(f"frozen route length mismatch: {flow.id}")
        if len(route["node_path"]) != len(set(route["node_path"])):
            raise RuntimeError(f"frozen route cycle: {flow.id}")
        for left, right, link_id in zip(route["node_path"], route["node_path"][1:], route["link_path"]):
            if link_id not in links or links[link_id] != {left, right}:
                raise RuntimeError(f"frozen route discontinuity: {flow.id}/{link_id}")


def classify(status: str, connected: bool) -> str:
    if not connected: return "GRAPH_DISCONNECTED"
    return {"SAT": "SAT", "NO_ROUTE": "CONNECTED_NO_ROUTE",
            "UNSAT": "SCHEDULE_UNSAT_GIVEN_BFS_ROUTE", "TIMEOUT": "SOLVER_TIMEOUT",
            "FORWARDING_CONFLICT": "FORWARDING_CONFLICT",
            "ERROR": "SOLVER_UNKNOWN_OTHER"}.get(status, status)


def read_summary(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.DictReader(handle))


def run_pf_validation(source: Path, scenario_name: str, faults: list[str], run_id: str) -> list[dict]:
    rows = []
    for index, fault in enumerate(faults, 1):
        validation_id = f"{run_id}_pf_{index:03d}"
        command = [sys.executable, "tools/run_experiment.py", "--scenario", str(source),
                   "--mode", "offline-per-failure", "--fault", fault, "--inside-environment",
                   "--skip-build", "--run-id", validation_id]
        subprocess.run(command, cwd=ROOT, check=True)
        path = ROOT / "results/scenarios" / scenario_name / "offline-per-failure" / fault / validation_id
        summary = read_summary(path / "summary.csv")
        profile = json.loads((path / "recovery_profile.json").read_text())
        rows.append({"fault_id": fault, "summary": summary,
                     "recovery_route_hash": sha_value(profile["logical_routes"]),
                     "recovery_hop_count": sum(len(route["link_path"]) for route in profile["logical_routes"]),
                     "logical_routes": profile["logical_routes"]})
    return rows


def route_layers(model, manifest: dict, routes: list[dict]) -> dict[str, int]:
    endpoint_layer = {tuple(row["endpoints"]): row["layer"] for row in manifest["edges"]}
    links = {link.id: tuple(sorted((link.endpoint_a, link.endpoint_b))) for link in model.links}
    counts = {layer: 0 for layer in ("BASE_GRID", "CURRENT_CROSS", "D4_EXTRA", "D5_EXTRA", "D6_EXTRA")}
    for route in routes:
        for link_id in route["link_path"]:
            edge = links.get(link_id)
            if edge in endpoint_layer: counts[endpoint_layer[edge]] += 1
    return counts


def tool_version(command: list[str]) -> str:
    try:
        return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=10).stdout.splitlines()[0]
    except Exception as error:
        return f"unavailable: {error}"


def campaign(args: argparse.Namespace) -> dict:
    scenario_dir = ROOT / "configs/scenarios/exp12_redundancy"
    manifests = generate(scenario_dir)
    implementation = args.implementation_commit or git("rev-parse", "HEAD")
    if git("rev-parse", "HEAD") != implementation:
        raise RuntimeError("implementation commit must equal HEAD for formal campaign")
    identity = {
        "implementation_commit": implementation, "generator_version": GENERATOR_VERSION,
        "scenario_files": {level: file_sha(scenario_dir / f"{level}.yaml") for level, _ in LEVELS},
        "topology_hashes": {level: manifests[level]["topology_edge_set_sha256"] for level, _ in LEVELS},
        "workload_sha256": next(iter(manifests.values()))["workload_sha256"],
        "policies": [policy.as_dict() for policy in POLICIES],
        "solver_timeout_ms": SOLVER_TIMEOUT_MS, "model_version": "bfs-z3-joint-tas-v1",
    }
    scratch = ROOT / "scratch/exp12" / args.run_id
    checkpoint_path = scratch / "checkpoint.json"; raw_path = scratch / "raw_campaign.json"
    checkpoint = read_checkpoint(checkpoint_path, identity)
    raw = json.loads(raw_path.read_text()) if raw_path.exists() else {"levels": {}, "identity": identity}
    result_dir = ROOT / "results/topology_redundancy"; result_dir.mkdir(parents=True, exist_ok=True)

    # R0 production BFS is the sole source of the frozen healthy route set.
    r0_source = scenario_dir / "R0_GRID.yaml"
    old_frozen = os.environ.pop("TSN_FROZEN_PRIMARY_ROUTES", None)
    try:
        r0_generated, _ = discover(r0_source, skip_build=True)
    except Exception as error:
        raise RuntimeError(f"BASE_REDUNDANCY_P0_NOT_FEASIBLE: {error}") from error
    finally:
        if old_frozen is not None: os.environ["TSN_FROZEN_PRIMARY_ROUTES"] = old_frozen
    native = json.loads((r0_generated / "profiles/profile0.json").read_text())
    frozen = {"schema_version": 1, "generator_version": GENERATOR_VERSION,
              "source_level": "R0_GRID", "logical_routes": native["logical_routes"]}
    frozen["frozen_primary_routes_sha256"] = sha_value(frozen["logical_routes"])
    frozen_path = result_dir / "frozen_primary_routes.json"
    frozen_path.write_bytes(canonical_bytes(frozen)); save_checkpoint(checkpoint_path, checkpoint, "preflight")

    reference = None
    frozen_feature_pairs = None
    for level, _ in LEVELS:
        source = scenario_dir / f"{level}.yaml"; model = load_scenario(source)
        validate_frozen(model, frozen)
        os.environ["TSN_FROZEN_PRIMARY_ROUTES"] = str(frozen_path.resolve())
        generated, candidate = discover(source, skip_build=True)
        profile0 = json.loads((generated / "profiles/profile0.json").read_text())
        semantic = semantic_profile_hash(profile0)
        features = build_pre_fault_features(model, candidate)
        if frozen_feature_pairs is None:
            frozen_feature_pairs = features["pairs"]
        else:
            features["pairs"] = frozen_feature_pairs
            features["feature_sha256"] = sha_value({key: value for key, value in features.items()
                                                      if key != "feature_sha256"})
        (generated / "fault_analysis/pre_fault_pairwise_features.json").write_bytes(canonical_bytes(features))
        groups = {}
        for policy in POLICIES:
            grouping, _ = agglomerate(features, policy)
            groups[policy.policy_id] = {
                "members": sorted(sorted(group["member_faults"]) for group in grouping["groups"]),
                "membership_sha256": sha_value(sorted(sorted(group["member_faults"]) for group in grouping["groups"])),
                "group_count": len(grouping["groups"]),
            }
        invariant = {
            "p0_semantic_hash": semantic,
            "candidate_ids": [row["fault_id"] for row in candidate["candidate_faults"]],
            "candidate_ids_sha256": sha_value([row["fault_id"] for row in candidate["candidate_faults"]]),
            "affected": [(row["fault_id"], row["affected_flows"]) for row in candidate["candidate_faults"]],
            "affected_sha256": sha_value([(row["fault_id"], row["affected_flows"]) for row in candidate["candidate_faults"]]),
            "jaccard_sha256": sha_value([(r["fault_i"], r["fault_j"], r["affected_flow_jaccard"])
                                         for r in features["pairs"]]), "groups": groups,
        }
        if reference is None: reference = invariant
        elif invariant != reference: raise RuntimeError(f"frozen cross-level invariant drift at {level}")
        level_raw = raw["levels"].setdefault(level, {})
        level_raw.update({"scenario_name": model.scenario_name, "generated": str(generated.relative_to(ROOT)),
                          "scenario_sha256": model.sha256(), "candidate_set_sha256": candidate["candidate_set_sha256"],
                          "manifest": manifests[level], "invariant": invariant})
        raw_path.parent.mkdir(parents=True, exist_ok=True); raw_path.write_bytes(canonical_bytes(raw))

        # Exhaustive single-fault attachment-pair connectivity precheck.  The
        # nested grid family has no single-edge disconnections; fail closed if
        # that design invariant ever changes instead of invoking Z3 blindly.
        for item in candidate["candidate_faults"]:
            minimum, _ = connectivity_margin(model, item["affected_flows"], {item["fault_id"]})
            if minimum == 0:
                raise RuntimeError(f"unexpected GRAPH_DISCONNECTED PF case: {level}/{item['fault_id']}")
        pf_root = generated / "profiles/per_failure"; (pf_root / "raw").mkdir(parents=True, exist_ok=True)
        run_omnet(generated, "ScenarioPerFailurePrecompute", pf_root / "precompute-results",
                  pf_root / "precompute.log")
        pf = build_store(generated)
        level_raw["solver_config_hash"] = pf["solver_config_hash"]
        profile0_routes = {r["flow_id"]: r for r in frozen["logical_routes"]}
        pf_rows = []
        for fault, entry in pf["faults"].items():
            affected = entry["affected_flows"]
            minimum, mean = connectivity_margin(model, affected, {fault})
            connected = minimum > 0
            row = {"fault_id": fault, "raw_status": entry["status"],
                   "status": classify(entry["status"], connected), "graph_connected": connected,
                   "connectivity_margin_min": minimum, "connectivity_margin_mean": mean,
                   "affected_flows": affected, "smt_solver_wall_us": entry["smt_solver_wall_us"]}
            if entry["status"] == "SAT":
                profile = json.loads((pf_root / entry["profile_file"]).read_text())
                row.update({"semantic_profile_hash": entry["semantic_profile_hash"],
                            "profile_bytes": entry["profile_bytes"],
                            "recovery_route_hash": sha_value(profile["logical_routes"]),
                            "recovery_hop_count": sum(len(r["link_path"]) for r in profile["logical_routes"]),
                            "edge_layer_usage": route_layers(model, manifests[level], profile["logical_routes"])})
            pf_rows.append(row)
        sat_faults = [row["fault_id"] for row in pf_rows if row["raw_status"] == "SAT"]
        level_raw["pf"] = pf_rows
        if not args.skip_runtime_validation:
            level_raw["pf_validation"] = run_pf_validation(source, model.scenario_name, sat_faults, args.run_id)
            for row in level_raw["pf_validation"]:
                row["edge_layer_usage"] = route_layers(model, manifests[level], row.pop("logical_routes"))
        raw_path.write_bytes(canonical_bytes(raw)); save_checkpoint(checkpoint_path, checkpoint, f"pf:{level}")

        policy_raw = {}
        for policy in POLICIES:
            store = run_policy(source, generated, json.loads((generated / "scenario.json").read_text()),
                               candidate, pf, features, policy, args.run_id, SOLVER_TIMEOUT_MS,
                               topology_precheck=True)
            attempts = []
            for attempt in store["synthesis_attempts"]:
                minimum, mean = connectivity_margin(model, attempt["affected_flows"], set(attempt["members"]))
                connected = minimum > 0
                raw_status = attempt["status"]
                strict_status = ("SHARED_SAT" if raw_status == "SHARED_SAT" else classify(raw_status, connected))
                attempts.append({**attempt, "pipeline_status": raw_status, "status": strict_status,
                                 "graph_connected": connected,
                                 "connectivity_margin_min": minimum, "connectivity_margin_mean": mean})
            classes = []
            for class_id, entry in store["classes"].items():
                profile = json.loads((generated / "profiles/approximate_equivalence" / policy.policy_id /
                                      entry["profile_file"]).read_text())
                classes.append({"class_id": class_id, **entry,
                                "recovery_route_hash": sha_value(profile["logical_routes"]),
                                "recovery_hop_count": sum(len(r["link_path"]) for r in profile["logical_routes"]),
                                "edge_layer_usage": route_layers(model, manifests[level], profile["logical_routes"])})
            validations = list(csv.DictReader((generated / "profiles/approximate_equivalence" /
                                               policy.policy_id / "class_validation.csv").open()))
            policy_raw[policy.policy_id] = {"attempts": attempts, "classes": classes,
                                             "rejected_groups": store["rejected_groups"],
                                             "validations": validations}
            save_checkpoint(checkpoint_path, checkpoint, f"raw:{level}:{policy.policy_id}")
        level_raw["policies"] = policy_raw; raw_path.write_bytes(canonical_bytes(raw))
        save_checkpoint(checkpoint_path, checkpoint, f"validated:{level}")

    os.environ.pop("TSN_FROZEN_PRIMARY_ROUTES", None)
    controlled = {
        "generator_version": GENERATOR_VERSION, "workload_sha256": identity["workload_sha256"],
        "frozen_primary_routes_sha256": frozen["frozen_primary_routes_sha256"],
        "candidate_ids_sha256": reference["candidate_ids_sha256"],
        "affected_sha256": reference["affected_sha256"], "jaccard_sha256": reference["jaccard_sha256"],
        "policy_group_sha256": {p.policy_id: reference["groups"][p.policy_id]["membership_sha256"] for p in POLICIES},
        "solver_timeout_ms": SOLVER_TIMEOUT_MS, "old_exp10_campaign_sha256": EXP10_CAMPAIGN_SHA,
        "old_exp11_campaign_sha256": EXP11_CAMPAIGN_SHA,
    }
    (result_dir / "controlled_variables.json").write_bytes(canonical_bytes(controlled))
    campaign_value = {
        "schema_version": 1, "experiment": "exp12_topology_redundancy",
        "title": "Topology Redundancy Sensitivity of Recovery-Profile Equivalence",
        "run_id": args.run_id, "implementation_commit": implementation,
        "generator_version": GENERATOR_VERSION, "identity": identity, "controlled": controlled,
        "levels": {level: {key: raw["levels"][level][key] for key in
                            ("scenario_name", "scenario_sha256", "candidate_set_sha256", "solver_config_hash")}
                   for level, _ in LEVELS},
        "machine": {"platform": platform.platform(), "python": platform.python_version(),
                    "omnetpp": "6.4.0", "inet": "4.7.0",
                    "z3": tool_version(["z3", "--version"])},
        "raw_campaign_sha256": file_sha(raw_path), "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (result_dir / "campaign.json").write_bytes(canonical_bytes(campaign_value))
    save_checkpoint(checkpoint_path, checkpoint, "complete")
    return campaign_value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--implementation-commit")
    parser.add_argument("--inside-environment", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-runtime-validation", action="store_true", help="test-only")
    args = parser.parse_args()
    if not args.inside_environment:
        forwarded = ["python3", "tools/run_redundancy_campaign.py", "--run-id", args.run_id,
                     "--inside-environment"]
        if args.implementation_commit: forwarded += ["--implementation-commit", args.implementation_commit]
        if args.skip_runtime_validation: forwarded.append("--skip-runtime-validation")
        inner = " ".join(shlex.quote(item) for item in forwarded)
        command = f"cd /home/opp_env/tsn_fault_recovery && opp_env run inet-4.7.0 -q -c {shlex.quote(inner)}"
        executable = ["wsl", "-d", "opp_env", "--", "bash", "-lc", command] if os.name == "nt" else ["bash", "-lc", command]
        return subprocess.run(executable).returncode
    print(json.dumps(campaign(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
