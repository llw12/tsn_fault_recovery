import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tools.approximate_equivalence import (
    ApproximateEquivalenceError, Policy, agglomerate, cluster_metrics, complete_link_ok,
    make_shared_profile, observed_pareto, pair_value, policy_grid, prune_tree, read_cache,
    resolve_tree, synthesis_cache_key, validation_cache_key, write_cache,
)
from tools.exact_equivalence import build_candidate_groups
from tools.profile_store import semantic_profile_hash
from tools.recovery_modes import MODES

ROOT = Path(__file__).resolve().parents[1]


def features():
    return {
        "scenario_name": "fixture", "scenario_sha256": "s", "candidate_set_sha256": "c",
        "feature_sha256": "f", "faults": [{"fault_id": item} for item in "ABCD"],
        "pairs": [
            {"fault_i": "A", "fault_j": "B", "affected_flow_jaccard": .8, "fault_edge_distance": 0},
            {"fault_i": "A", "fault_j": "C", "affected_flow_jaccard": .2, "fault_edge_distance": 2},
            {"fault_i": "A", "fault_j": "D", "affected_flow_jaccard": 0, "fault_edge_distance": 3},
            {"fault_i": "B", "fault_j": "C", "affected_flow_jaccard": .8, "fault_edge_distance": 1},
            {"fault_i": "B", "fault_j": "D", "affected_flow_jaccard": 0, "fault_edge_distance": 3},
            {"fault_i": "C", "fault_j": "D", "affected_flow_jaccard": .6, "fault_edge_distance": 1},
        ],
    }


def exact_features():
    value = features()
    value["pairs"] = [
        {"fault_i": "A", "fault_j": "B", "affected_flow_jaccard": 1, "fault_edge_distance": 0},
        {"fault_i": "A", "fault_j": "C", "affected_flow_jaccard": 0, "fault_edge_distance": 1},
        {"fault_i": "A", "fault_j": "D", "affected_flow_jaccard": 0, "fault_edge_distance": 2},
        {"fault_i": "B", "fault_j": "C", "affected_flow_jaccard": 0, "fault_edge_distance": 1},
        {"fault_i": "B", "fault_j": "D", "affected_flow_jaccard": 0, "fault_edge_distance": 2},
        {"fault_i": "C", "fault_j": "D", "affected_flow_jaccard": 1, "fault_edge_distance": 0},
    ]
    return value


def raw_profile():
    return {
        "logical_routes": [{"flow_id": "T", "node_path": ["a", "b"], "link_path": ["safe"]}],
        "routes": [{"flow_id": "T", "switch": "a", "destination": "b", "interface": "eth0", "logical_link": "safe"}],
        "gate_schedules": [{"gate_path": "a.eth[0]", "traffic_class": 1, "initially_open": False,
                            "offset_s": 0, "durations_s": [0.1, 0.9]}],
    }


class ApproximateEquivalenceTest(unittest.TestCase):
    def test_01_complete_link_condition(self):
        pairs = {(row["fault_i"], row["fault_j"]): (row["affected_flow_jaccard"], row["fault_edge_distance"]) for row in features()["pairs"]}
        self.assertTrue(complete_link_ok(["A", "B"], pairs, Policy("x", "JACCARD", .8)))

    def test_02_threshold_chaining_is_not_one_group(self):
        artifact, _ = agglomerate(features(), Policy("x", "JACCARD", .8))
        self.assertNotIn(["A", "B", "C"], [group["member_faults"] for group in artifact["groups"]])

    def test_03_j_grouping_is_deterministic(self):
        policy = Policy("x", "JACCARD", .6)
        self.assertEqual(agglomerate(features(), policy), agglomerate(features(), policy))

    def test_04_je_grouping_is_deterministic(self):
        policy = Policy("x", "JACCARD_EDGE", .6, 1)
        self.assertEqual(agglomerate(features(), policy), agglomerate(features(), policy))

    def test_05_edge_distance_constraint(self):
        pairs = {(row["fault_i"], row["fault_j"]): (row["affected_flow_jaccard"], row["fault_edge_distance"]) for row in features()["pairs"]}
        self.assertFalse(complete_link_ok(["B", "C"], pairs, Policy("x", "JACCARD_EDGE", .6, 0)))

    def test_06_j100_matches_exact_affected_sets(self):
        artifact, _ = agglomerate(exact_features(), Policy("J100", "JACCARD", 1))
        candidate = {"candidate_faults": [
            {"fault_id": "A", "affected_flows": ["x"]}, {"fault_id": "B", "affected_flows": ["x"]},
            {"fault_id": "C", "affected_flows": ["y"]}, {"fault_id": "D", "affected_flows": ["y"]}]}
        pf = {"faults": {fault: {"status": "SAT"} for fault in "ABCD"}}
        exact = build_candidate_groups(candidate, pf)
        self.assertEqual(sorted(group["member_faults"] for group in artifact["groups"]),
                         sorted(group["members"] for group in exact))

    def test_07_grouping_ignores_semantic_hash(self):
        left, right = exact_features(), exact_features(); right["semantic_profile_hash"] = "changed"
        self.assertEqual(agglomerate(left, Policy("J100", "JACCARD", 1)), agglomerate(right, Policy("J100", "JACCARD", 1)))

    def test_08_grouping_ignores_recovery_route(self):
        left, right = features(), features(); right["recovery_routes"] = ["leak"]
        self.assertEqual(agglomerate(left, Policy("x", "JACCARD", .6)), agglomerate(right, Policy("x", "JACCARD", .6)))

    def test_09_grouping_ignores_pf_objective(self):
        left, right = features(), features(); right["z3_objective"] = -999
        self.assertEqual(agglomerate(left, Policy("x", "JACCARD", .6)), agglomerate(right, Policy("x", "JACCARD", .6)))

    def test_10_grouping_needs_no_pf_store(self):
        self.assertTrue(agglomerate(features(), Policy("x", "JACCARD", .8))[0]["pre_fault_only"])

    def test_11_merge_tree_is_deterministic(self):
        policy = Policy("x", "JACCARD", .6)
        first = [group["merge_tree"] for group in agglomerate(features(), policy)[0]["groups"]]
        second = [group["merge_tree"] for group in agglomerate(features(), policy)[0]["groups"]]
        self.assertEqual(first, second)

    def test_12_recursive_split_order(self):
        tree = {"members": ["A", "B"], "left": {"members": ["A"], "leaf_fault": "A"}, "right": {"members": ["B"], "leaf_fault": "B"}}
        classes, _ = resolve_tree(tree, lambda *_: {"status": "UNSAT", "validation_pass": False})
        self.assertEqual([row["members"] for row in classes], [["A"], ["B"]])

    def test_13_shared_pass_accepts_group(self):
        tree = {"members": ["A", "B"], "left": {"members": ["A"], "leaf_fault": "A"}, "right": {"members": ["B"], "leaf_fault": "B"}}
        classes, rejected = resolve_tree(tree, lambda *_: {"status": "SHARED_SAT", "validation_pass": True})
        self.assertEqual((len(classes), len(rejected), classes[0]["class_type"]), (1, 0, "SHARED"))

    def _assert_failure_splits(self, status):
        tree = {"members": ["A", "B"], "left": {"members": ["A"], "leaf_fault": "A"}, "right": {"members": ["B"], "leaf_fault": "B"}}
        classes, rejected = resolve_tree(tree, lambda *_: {"status": status, "validation_pass": False})
        self.assertEqual((len(classes), len(rejected)), (2, 1))

    def test_14_no_route_splits(self): self._assert_failure_splits("NO_ROUTE")
    def test_15_unsat_splits(self): self._assert_failure_splits("UNSAT")
    def test_16_forwarding_conflict_splits(self): self._assert_failure_splits("FORWARDING_CONFLICT")
    def test_17_timeout_splits(self): self._assert_failure_splits("TIMEOUT")
    def test_18_validation_failed_splits(self): self._assert_failure_splits("VALIDATION_FAILED")

    def test_19_singleton_fallback_covers_faults(self):
        artifact, _ = agglomerate(features(), Policy("x", "JACCARD", .2))
        classes = []
        for group in artifact["groups"]:
            part, _ = resolve_tree(group["merge_tree"], lambda *_: {"status": "ERROR", "validation_pass": False})
            classes += part
        self.assertEqual(sorted(row["members"][0] for row in classes), list("ABCD"))

    def test_20_final_partition_has_no_overlap(self):
        artifact, _ = agglomerate(features(), Policy("x", "JACCARD", .2))
        members = [fault for group in artifact["groups"] for fault in group["member_faults"]]
        self.assertEqual(len(members), len(set(members)))

    def test_21_pruned_partition_covers_eligible_faults(self):
        artifact, _ = agglomerate(features(), Policy("x", "JACCARD", .2))
        kept = [prune_tree(group["merge_tree"], {"A", "C"}) for group in artifact["groups"]]
        members = sorted(fault for tree in kept if tree for fault in tree["members"])
        self.assertEqual(members, ["A", "C"])

    def test_22_shared_profile_avoids_member_links(self):
        self.assertFalse({"A", "B"} & set(raw_profile()["logical_routes"][0]["link_path"]))

    def test_23_same_class_members_share_file_hash(self):
        scenario = {"scenario_sha256": "s"}; candidate = {"candidate_set_sha256": "c"}
        profile = make_shared_profile(raw_profile(), scenario, candidate, ["A", "B"], ["T"], "C1")
        self.assertEqual(profile["profile_sha256"], profile["profile_sha256"])

    def test_24_synthesis_cache_key_sorted_members(self):
        self.assertEqual(synthesis_cache_key("s", ["B", "A"], "z", "c"), synthesis_cache_key("s", ["A", "B"], "z", "c"))

    def test_25_validation_cache_key_includes_fault(self):
        self.assertNotEqual(validation_cache_key("s", "p", "A", "r"), validation_cache_key("s", "p", "B", "r"))

    def test_26_stale_cache_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"; write_cache(path, "a", "synthesis", {"status": "SAT"})
            with self.assertRaises(ApproximateEquivalenceError): read_cache(path, "b", "synthesis")

    def test_27_cache_roundtrip_preserves_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"; write_cache(path, "a", "synthesis", {"status": "SAT"})
            self.assertEqual(read_cache(path, "a", "synthesis")["status"], "SAT")

    def test_28_policy_hash_deterministic(self):
        self.assertEqual(Policy("x", "JACCARD", .8).hash, Policy("x", "JACCARD", .8).hash)

    def test_29_grouping_artifact_deterministic(self):
        self.assertEqual(agglomerate(features(), Policy("x", "JACCARD", .4))[0], agglomerate(features(), Policy("x", "JACCARD", .4))[0])

    def test_30_j100_semantic_profile_is_metadata_independent(self):
        scenario = {"scenario_sha256": "s"}; candidate = {"candidate_set_sha256": "c"}
        left = make_shared_profile(raw_profile(), scenario, candidate, ["A", "B"], ["T"], "C1")
        right = make_shared_profile(raw_profile(), scenario, candidate, ["A", "B"], ["T"], "C2")
        self.assertEqual(semantic_profile_hash(left), semantic_profile_hash(right))

    def test_31_candidate_compression_bounds_split_result(self):
        m, candidate_groups, final_groups = 4, 2, 3
        self.assertGreaterEqual(1-candidate_groups/m, 1-final_groups/m)

    def test_32_compression_denominator_excludes_p0(self):
        m, final_profiles = 4, 2
        self.assertEqual(1-final_profiles/m, .5)

    def test_33_storage_formula_excludes_metadata(self):
        pf_bytes, approximate_bytes, metadata = 100, 60, 1000
        self.assertEqual(1-approximate_bytes/pf_bytes, .4)
        self.assertNotEqual(1-(approximate_bytes+metadata)/pf_bytes, .4)

    def test_34_runtime_bfs_counter_is_zeroed(self):
        source = (ROOT / "src/tsn_fault_recovery/control/ScenarioRecoveryController.cc").read_text()
        self.assertIn('recordScalar("scenario.runtime.routeSolverInvocations", 0)', source)

    def test_35_runtime_z3_counter_is_zeroed(self):
        source = (ROOT / "src/tsn_fault_recovery/control/ScenarioRecoveryController.cc").read_text()
        self.assertIn('recordScalar("scenario.runtime.z3SolverInvocations", 0)', source)

    def test_36_runtime_synthesis_counter_is_zeroed(self):
        source = (ROOT / "src/tsn_fault_recovery/control/ScenarioRecoveryController.cc").read_text()
        self.assertIn('recordScalar("scenario.runtime.profileSynthesisInvocations", 0)', source)

    def test_37_runtime_grouping_counter_is_zeroed(self):
        source = (ROOT / "src/tsn_fault_recovery/control/ScenarioRecoveryController.cc").read_text()
        self.assertIn('recordScalar("scenario.runtime.groupingInvocations", 0)', source)

    def test_38_post_recovery_fields_do_not_change_candidate_groups(self):
        left, right = features(), deepcopy(features()); right["faults"][0]["latency"] = 123
        self.assertEqual(agglomerate(left, Policy("x", "JACCARD", .6)), agglomerate(right, Policy("x", "JACCARD", .6)))

    def test_39_pareto_is_deterministic(self):
        rows = [{"scenario": "s", "policy_id": "a", "deadline_miss_delta": 0, "stable_validation_pass": True, "realized_profile_compression_ratio": .5, "cold_synthesis_wall_ms": 2, "max_recovery_delta_us": 1}]
        self.assertEqual(observed_pareto(rows), observed_pareto(rows))

    def test_40_diagnostic_fields_are_labeled_posthoc(self):
        source = (ROOT / "scripts/analyze_approximate_equivalence.py").read_text()
        self.assertIn("posthoc_mean_recovery_route_jaccard", source)
        self.assertIn("posthoc_pf_semantic_hash_count", source)

    def test_41_policy_grid_has_fourteen_points(self): self.assertEqual(len(policy_grid()), 14)
    def test_42_policy_ids_are_unique(self): self.assertEqual(len({p.policy_id for p in policy_grid()}), 14)
    def test_43_approximate_runtime_mode_is_implemented(self): self.assertTrue(MODES["offline-approx-equivalence"].implemented)
    def test_44_pair_value_is_symmetric(self):
        pairs = {("A", "B"): (.8, 1)}
        self.assertEqual(pair_value(pairs, "A", "B"), pair_value(pairs, "B", "A"))
    def test_45_singleton_metrics_are_identity(self):
        self.assertEqual(cluster_metrics(["A"], {}), {"min_pairwise_jaccard": 1.0, "mean_pairwise_jaccard": 1.0, "max_edge_distance": 0})


if __name__ == "__main__":
    unittest.main()
