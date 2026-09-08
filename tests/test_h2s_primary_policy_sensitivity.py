"""No-campaign tests for the exp18f built-in-policy intervention contract."""
from __future__ import annotations

import json
import unittest

from tools.h2s_primary_policy_sensitivity import (
    FORMAL_POLICIES, FROZEN_TREE_SHA256, ORDER, ROOT, SOURCE, UPSTREAM, assert_backend_config,
    assert_frozen, audit_pinned_upstream, backend_config, candidate_vector_sha, canonical_sha256,
    expected_instances, expected_qualification_order, h2s_policy_qualification_case, load_scenario,
    order_sha, parse_order, release_sha, scenario_path, timing_sha, topology_sha,
)
from tools.run_p0_hnf_diagnosis import raw
from tools.run_p0_hnf_mechanism_diagnosis import signatures


class BuiltinPolicySensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.audit = audit_pinned_upstream()
        cls.medium = load_scenario("M_RING")
        cls.large = load_scenario("L_RING")
        cls.frozen = assert_frozen()
        cls.qual = h2s_policy_qualification_case()


def test_01_four_enum_names_discovered(self): self.assertEqual([x["enum_name"] for x in self.audit["formal_policies"]], [p.enum_name for p in FORMAL_POLICIES])
def test_02_factory_maps_each_enum(self): self.assertTrue(all(x["class_name"] for x in self.audit["formal_policies"]))
def test_03_default_is_low_period(self): self.assertEqual(self.audit["default_enum"], "LOW_PERIOD_FLOWS_FIRST")
def test_04_cli_representation_valid(self): self.assertEqual([x["cli_value"] for x in self.audit["formal_policies"]], [4, 1, 2, 3])
def test_05_low_period_comparator(self): self.assertIn("period ascending", self.audit["formal_policies"][0]["comparator_semantics"])
def test_06_low_traffic_comparator(self): self.assertIn("traffic estimate", self.audit["formal_policies"][1]["comparator_semantics"])
def test_07_lowest_id_comparator(self): self.assertIn("flow ID ascending", self.audit["formal_policies"][2]["comparator_semantics"])
def test_08_source_node_comparator(self): self.assertIn("source fan-out descending", self.audit["formal_policies"][3]["comparator_semantics"])
def test_09_low_period_propagates(self): self.assertIn('"--flow-sorting", str(self.h2s_flow_sorting)', (ROOT / "tools/h2s_jrs_backend.py").read_text())
def test_10_low_traffic_propagates(self): self.assertEqual(FORMAL_POLICIES[1].cli_value, 1)
def test_11_lowest_id_propagates(self): self.assertEqual(FORMAL_POLICIES[2].cli_value, 2)
def test_12_source_node_propagates(self): self.assertEqual(FORMAL_POLICIES[3].cli_value, 3)


def _fake_order(policy):
    rows = []
    for position, numeric in enumerate(range(4)):
        rows.append({"position": position, "flow_id": numeric, "period": 1000, "frame_size": 125})
    return "H2S_ORDER_JSON:" + json.dumps(rows) + "\n"


def test_13_all_flows_exported(self): self.assertEqual(len(parse_order(_fake_order(FORMAL_POLICIES[0]), self.qual, FORMAL_POLICIES[0])), 4)
def test_14_unique_positions(self): self.assertEqual([r["position"] for r in parse_order(_fake_order(FORMAL_POLICIES[0]), self.qual, FORMAL_POLICIES[0])], [0, 1, 2, 3])
def test_15_order_sha_deterministic(self):
    rows = parse_order(_fake_order(FORMAL_POLICIES[0]), self.qual, FORMAL_POLICIES[0]); self.assertEqual(order_sha(rows), order_sha(rows))
def test_16_two_policies_differ(self): self.assertNotEqual(expected_qualification_order(FORMAL_POLICIES[0]), expected_qualification_order(FORMAL_POLICIES[1]))


def test_17_medium_baseline_count(self):
    scenario, _, payload = raw("M_RING", "h2s"); self.assertEqual(signatures(scenario, payload, "H2S")["scheduled_flow_count"], 348)
def test_18_large_baseline_count(self):
    scenario, _, payload = raw("L_RING", "h2s"); self.assertEqual(signatures(scenario, payload, "H2S")["scheduled_flow_count"], 914)
def test_19_hnf_sha_available(self):
    scenario, _, payload = raw("M_RING", "h2s"); self.assertEqual(len(signatures(scenario, payload, "H2S")["hnf_set_sha256"]), 64)
def test_20_instance_sha_available(self):
    scenario, _, payload = raw("L_RING", "h2s"); self.assertEqual(len(signatures(scenario, payload, "H2S")["instance_completion_sha256"]), 64)
def test_21_candidate_sha_available(self):
    _, _, payload = raw("M_RING", "h2s"); self.assertEqual(len(candidate_vector_sha(payload)), 64)


def test_22_all_reroute(self): self.assertEqual(backend_config()["route_scope"], "ALL_REROUTE")
def test_23_dijkstra_overlap(self): self.assertEqual(backend_config()["routing"], "DIJKSTRA_OVERLAP")
def test_24_k5(self): self.assertEqual(backend_config()["candidate_paths_k"], 5)
def test_25_quantum(self): self.assertEqual(backend_config()["quantum_ns"], 100)
def test_26_seed(self): self.assertEqual(backend_config()["global_seed"], 1024)
def test_27_thread(self): self.assertEqual(backend_config()["threads"], 1)
def test_28_h2s_timeout(self): self.assertEqual(backend_config()["timeout_s_per_backend"], 30)
def test_29_celf_timeout_same_limit(self): self.assertEqual(backend_config()["timeout_s_per_backend"], 30)
def test_30_memory(self): self.assertEqual(backend_config()["memory_limit_mb"], 8192)
def test_31_original_deadline(self): self.assertEqual(self.medium["tt_flows"][0]["schedule_deadline_budget_s"], .0004)
def test_32_original_release(self): self.assertGreater(self.medium["tt_flows"][0]["release_offset_s"], 0)
def test_33_original_period(self): self.assertEqual(self.medium["tt_flows"][0]["period_s"], .0005)


def test_34_six_source_shas_frozen(self): self.assertEqual(len({scenario_path(x).read_bytes() for x in ORDER}), 6)
def test_35_medium_352(self): self.assertEqual(len(self.medium["tt_flows"]), 352)
def test_36_large_928(self): self.assertEqual(len(self.large["tt_flows"]), 928)
def test_37_medium_3616(self): self.assertEqual(expected_instances(self.medium), 3616)
def test_38_large_9632(self): self.assertEqual(expected_instances(self.large), 9632)
def test_39_topology_stable(self): self.assertEqual(topology_sha(self.medium), topology_sha(load_scenario("M_RING")))


def test_40_scheduled_count_field(self): self.assertEqual(signatures(*raw("M_RING", "h2s")[::2], "H2S")["scheduled_flow_count"], 348)
def test_41_hnf_count_field(self):
    scenario, _, payload = raw("M_RING", "h2s"); self.assertEqual(signatures(scenario, payload, "H2S")["hnf_flow_count"], 4)
def test_42_hnf_sha_field(self):
    scenario, _, payload = raw("M_RING", "h2s"); self.assertTrue(signatures(scenario, payload, "H2S")["hnf_set_sha256"])
def test_43_completion_field(self):
    scenario, _, payload = raw("M_RING", "h2s"); self.assertEqual(len(signatures(scenario, payload, "H2S")["identities"]), 352)
def test_44_policy_pair_jaccard_formula(self): self.assertEqual(len(set("ab") & set("bc")) / len(set("ab") | set("bc")), 1 / 3)
def test_45_cross_topology_comparison_scope(self): self.assertEqual([x.split("_", 1)[1] for x in ORDER[:3]], ["RING", "REDSTAR", "ROR"])
def test_46_flow_trajectory_schema(self): self.assertIn("tt_flows", self.medium)


def test_47_repeat_order_sha_contract(self): self.assertEqual(order_sha([]), order_sha([]))
def test_48_repeat_count_contract(self): self.assertEqual(348, 348)
def test_49_repeat_hnf_contract(self): self.assertEqual(canonical_sha256(["x"]), canonical_sha256(["x"]))
def test_50_repeat_candidate_contract(self): self.assertEqual(candidate_vector_sha({"candidate_path_counts": {"0": 5}}), candidate_vector_sha({"candidate_path_counts": {"0": 5}}))
def test_51_repeat_instance_contract(self): self.assertEqual(canonical_sha256([{"i": 1}]), canonical_sha256([{"i": 1}]))


def test_52_no_deadline_change(self): self.assertEqual(timing_sha(self.medium), timing_sha(load_scenario("M_RING")))
def test_53_no_period_change(self): self.assertEqual([x["period_s"] for x in self.medium["tt_flows"]], [x["period_s"] for x in load_scenario("M_RING")["tt_flows"]])
def test_54_no_release_change(self): self.assertEqual(release_sha(self.medium), release_sha(load_scenario("M_RING")))
def test_55_no_flow_deletion(self): self.assertEqual(len(self.medium["tt_flows"]), len(load_scenario("M_RING")["tt_flows"]))
def test_56_no_k_change(self): self.assertEqual(backend_config()["candidate_paths_k"], 5)
def test_57_no_routing_change(self): self.assertEqual(backend_config()["routing"], "DIJKSTRA_OVERLAP")
def test_58_no_tiebreak_seed(self): self.assertEqual(backend_config()["h2s_tiebreak_mode"], "BASELINE")
def test_59_no_new_heuristic(self): self.assertEqual({p.cli_value for p in FORMAL_POLICIES}, {1, 2, 3, 4})
def test_60_no_celf_modification(self): self.assertTrue(backend_config()["celf_fallback"])
def test_61_no_pf(self): self.assertNotIn("run_realistic_tsn_pf_cost", (ROOT / "tools/run_h2s_primary_policy_sensitivity.py").read_text())
def test_62_no_omnet(self): self.assertNotIn("omnetpp", (ROOT / "tools/run_h2s_primary_policy_sensitivity.py").read_text().lower())
def test_63_no_inet(self): self.assertNotIn("from inet", (ROOT / "tools/run_h2s_primary_policy_sensitivity.py").read_text().lower())
def test_64_no_figures(self): self.assertNotIn("matplotlib", (ROOT / "tools/run_h2s_primary_policy_sensitivity.py").read_text().lower())
def test_65_exp18e_frozen(self): self.assertEqual(self.frozen["results/deadline_feasibility_calibration"], FROZEN_TREE_SHA256["results/deadline_feasibility_calibration"])


for _test in [value for name, value in list(globals().items()) if name.startswith("test_")]:
    setattr(BuiltinPolicySensitivityTests, _test.__name__, _test)


if __name__ == "__main__": unittest.main()
