import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from tools.critical_link import CriticalLinkAnalyzer, candidate_ids, canonical_bytes
from tools.fault_dataset import (
    affected_flow_matrix, edge_distance, exact_affected_groups, jaccard,
    jaccard_matrix, pairwise_rows, profile_similarity_bins, recovery_route_union,
    switch_distances,
)
from tools.scenario_compiler import compile_scenario
from tools.scenario_model import load_scenario


ROOT = Path(__file__).resolve().parents[1]


def diamond_p0(model=None):
    model = model or load_scenario(ROOT / "configs/scenarios/diamond_auto.yaml")
    return {"scenario_sha256": model.sha256(), "profile_id": "P0", "logical_routes": [{
        "flow_id": "TT", "node_path": ["source", "s1", "s2", "s4", "destination"],
        "link_path": ["l_source_s1", "l_s1_s2", "l_s2_s4", "l_s4_destination"],
    }], "routes": [], "gate_schedules": []}


class CriticalLinkTests(unittest.TestCase):
    def setUp(self):
        self.auto = load_scenario(ROOT / "configs/scenarios/diamond_auto.yaml")
        self.p0 = diamond_p0(self.auto)
        self.analysis = CriticalLinkAnalyzer.analyze(self.auto, self.p0)
        self.by_link = {row["link_id"]: row for row in self.analysis["all_links"]}

    def test_01_inverted_index(self):
        index = CriticalLinkAnalyzer.inverted_index(self.p0)
        self.assertEqual(index["l_s1_s2"], ("TT",))

    def test_02_candidate_iff_scope_and_used(self):
        self.assertEqual({row["fault_id"] for row in self.analysis["candidate_faults"]}, {"l_s1_s2", "l_s2_s4"})

    def test_03_access_excluded(self):
        self.assertFalse(self.by_link["l_source_s1"]["candidate_fault"])
        self.assertEqual(self.by_link["l_source_s1"]["classification"], "OUT_OF_SCOPE")

    def test_04_unused_internal_excluded(self):
        self.assertEqual(self.by_link["l_s1_s3"]["classification"], "UNUSED_BY_TT")

    def test_05_explicit_supported(self):
        model = load_scenario(ROOT / "configs/scenarios/diamond.yaml")
        analysis = CriticalLinkAnalyzer.analyze(model, diamond_p0(model))
        self.assertEqual(candidate_ids(analysis), model.fault_candidates)

    def test_06_explicit_no_affected_allowed(self):
        model = load_scenario(ROOT / "configs/scenarios/diamond.yaml")
        analysis = CriticalLinkAnalyzer.analyze(model, diamond_p0(model))
        row = next(item for item in analysis["candidate_faults"] if item["fault_id"] == "l_s1_s3")
        self.assertEqual(row["affected_flows"], [])

    def test_07_auto_never_no_affected(self):
        self.assertTrue(all(row["affected_flows"] for row in self.analysis["candidate_faults"]))

    def test_08_order_deterministic(self):
        self.assertEqual(candidate_ids(self.analysis), tuple(sorted(candidate_ids(self.analysis))))

    def test_09_json_deterministic(self):
        self.assertEqual(canonical_bytes(self.analysis), canonical_bytes(CriticalLinkAnalyzer.analyze(self.auto, self.p0)))

    def test_10_hash_deterministic(self):
        self.assertEqual(self.analysis["candidate_set_sha256"], CriticalLinkAnalyzer.analyze(self.auto, self.p0)["candidate_set_sha256"])

    def test_11_changed_p0_changes_hash(self):
        changed = deepcopy(self.p0)
        changed["logical_routes"][0]["node_path"] = ["source", "s1", "s3", "s4", "destination"]
        changed["logical_routes"][0]["link_path"] = ["l_source_s1", "l_s1_s3", "l_s3_s4", "l_s4_destination"]
        self.assertNotEqual(self.analysis["candidate_set_sha256"], CriticalLinkAnalyzer.analyze(self.auto, changed)["candidate_set_sha256"])

    def test_12_same_p0_same_candidates(self):
        self.assertEqual(candidate_ids(self.analysis), candidate_ids(CriticalLinkAnalyzer.analyze(self.auto, deepcopy(self.p0))))

    def test_13_incidence_matrix(self):
        matrix = affected_flow_matrix(self.analysis["candidate_faults"], ["TT"])
        self.assertEqual(matrix, [{"fault_id": "l_s1_s2", "TT": 1}, {"fault_id": "l_s2_s4", "TT": 1}])

    def test_14_jaccard_identical(self): self.assertEqual(jaccard({"a"}, {"a"}), 1)
    def test_15_jaccard_disjoint(self): self.assertEqual(jaccard({"a"}, {"b"}), 0)
    def test_16_jaccard_partial(self): self.assertEqual(jaccard({"a", "b"}, {"b", "c"}), 1 / 3)

    def test_17_matrix_symmetric(self):
        _, matrix = jaccard_matrix(self.analysis["candidate_faults"])
        self.assertEqual(matrix[0][1], matrix[1][0])

    def test_18_matrix_diagonal(self):
        _, matrix = jaccard_matrix(self.analysis["candidate_faults"])
        self.assertTrue(all(matrix[i][i] == 1 for i in range(len(matrix))))

    def pair_fixture(self, same_hash=True):
        candidates = self.analysis["candidate_faults"]
        store = {"faults": {fault["fault_id"]: {"status": "SAT", "semantic_profile_hash": "same" if same_hash else fault["fault_id"]}
                            for fault in candidates}}
        recovery = deepcopy(self.p0); recovery["logical_routes"][0]["link_path"] = ["l_source_s1", "l_s1_s3", "l_s3_s4", "l_s4_destination"]
        profiles = {fault["fault_id"]: recovery for fault in candidates}
        return pairwise_rows(self.auto, candidates, store, self.p0, profiles), store

    def test_19_profile_identity(self):
        rows, _ = self.pair_fixture(); self.assertEqual(rows[0]["same_semantic_profile"], 1)

    def test_20_exact_groups(self):
        _, store = self.pair_fixture(); groups = exact_affected_groups(self.analysis["candidate_faults"], store)
        self.assertEqual((groups[0]["fault_count"], groups[0]["semantic_profile_hash_count"]), (2, 1))

    def test_21_edge_distance(self):
        distances = switch_distances(self.auto)
        self.assertEqual(edge_distance(("s1", "s2"), ("s2", "s4"), distances), 0)

    def test_22_recovery_route_jaccard(self):
        rows, _ = self.pair_fixture(); self.assertEqual(rows[0]["recovery_route_link_jaccard"], 1)

    def test_23_recovery_union(self):
        self.assertEqual(recovery_route_union(self.p0), {"l_source_s1", "l_s1_s2", "l_s2_s4", "l_s4_destination"})

    def test_24_resolved_compile_exact_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            generated = compile_scenario(ROOT / "configs/scenarios/diamond_auto.yaml", tmp, candidate_ids(self.analysis))
            scenario = json.loads((generated / "scenario.json").read_text())
            self.assertEqual(tuple(scenario["fault_candidates"]), candidate_ids(self.analysis))

    def test_25_selection_has_no_recovery_input(self):
        first = CriticalLinkAnalyzer.analyze(self.auto, self.p0)
        fake_recovery_statuses = {fault: "ERROR" for fault in candidate_ids(first)}
        self.assertTrue(fake_recovery_statuses)
        self.assertEqual(candidate_ids(first), candidate_ids(CriticalLinkAnalyzer.analyze(self.auto, self.p0)))

    def test_26_profile_bins(self):
        rows, _ = self.pair_fixture(); bins = profile_similarity_bins(rows)
        self.assertEqual(next(row for row in bins if row["jaccard_bin"] == "1")["same_profile_ratio"], 1)

    def test_27_node_type_not_name(self):
        self.assertTrue(self.by_link["l_s1_s2"]["in_protection_scope"])

    def test_28_auto_scenarios_only_change_name_and_fault_policy(self):
        for base in ("diamond", "mesh10"):
            legacy = load_scenario(ROOT / f"configs/scenarios/{base}.yaml")
            auto = load_scenario(ROOT / f"configs/scenarios/{base}_auto.yaml")
            for attribute in ("simulation", "network", "scheduling", "nodes", "links", "tt_flows", "be_flows"):
                self.assertEqual(getattr(legacy, attribute), getattr(auto, attribute))

    def test_29_explicit_auto_exclude_recorded(self):
        selection = replace(self.auto.candidate_selection, exclude=("l_s1_s2",))
        model = replace(self.auto, candidate_selection=selection)
        analysis = CriticalLinkAnalyzer.analyze(model, diamond_p0(model))
        self.assertNotIn("l_s1_s2", candidate_ids(analysis))
        reason = next(row["reason"] for row in analysis["excluded_links"] if row["fault_id"] == "l_s1_s2")
        self.assertEqual(reason, "EXPLICIT_EXCLUDE")


if __name__ == "__main__":
    unittest.main()
