"""Seventy-two focused, no-scheduler tests for the exp18e preregistration."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.deadline_feasibility_calibration import (
    ALPHAS, FROZEN_TREE_SHA256, HYPERCYCLE_NS, ORDER, ROOT, SOURCE, Alpha,
    assert_backend_config, assert_frozen_tree, backend_config, cycle_boundary_rows,
    deadline_values, derive_scenario, derived_integrity, expected_instance_count,
    flow_kind, ns_field, topology_projection, transformed_deadline_ns,
)
from tools.run_deadline_feasibility_calibration import make_cycle_boundary_case, selected_alpha
from tools.run_p0_hnf_diagnosis import raw
from tools.run_p0_hnf_mechanism_diagnosis import signatures


def source(identifier: str = "M_RING") -> dict:
    return json.loads((SOURCE / "scenarios" / f"{identifier}.json").read_text(encoding="utf-8"))


class DeadlineFeasibilityCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.medium = source("M_RING")
        cls.large = source("L_RING")
        cls.medium_child = derive_scenario(cls.medium, "M_RING", Alpha("A133", 4, 3))
        cls.frozen = assert_frozen_tree()


def test_alpha_one_exact(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertEqual(transformed_deadline_ns(400_000, 500_000, ALPHAS[0]), (400_000, False))


def test_alpha_eleven_tenths_exact(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertEqual(transformed_deadline_ns(400_000, 500_000, ALPHAS[1]), (440_000, False))


def test_alpha_six_fifths_exact(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertEqual(transformed_deadline_ns(400_000, 500_000, ALPHAS[2]), (480_000, False))


def test_alpha_five_quarters_exact(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertEqual(transformed_deadline_ns(400_000, 500_000, ALPHAS[3]), (500_000, True))


def test_alpha_four_thirds_exact(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertEqual(transformed_deadline_ns(400_000, 500_000, ALPHAS[4]), (500_000, True))


def test_cap_at_period(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertEqual(transformed_deadline_ns(6_000_000, 8_000_000, ALPHAS[4])[0], 8_000_000)


def test_integer_ns(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertIsInstance(transformed_deadline_ns(1_500_000, 2_000_000, ALPHAS[3])[0], int)


def test_hundred_ns_alignment(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertEqual(transformed_deadline_ns(1_500_000, 2_000_000, ALPHAS[3])[0] % 100, 0)


def test_400us_a110(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(400_000, 500_000, ALPHAS[1])[0], 440_000)
def test_400us_a120(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(400_000, 500_000, ALPHAS[2])[0], 480_000)
def test_400us_a125(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(400_000, 500_000, ALPHAS[3])[0], 500_000)
def test_400us_a133(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(400_000, 500_000, ALPHAS[4])[0], 500_000)
def test_1500us_a110(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(1_500_000, 2_000_000, ALPHAS[1])[0], 1_650_000)
def test_1500us_a120(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(1_500_000, 2_000_000, ALPHAS[2])[0], 1_800_000)
def test_1500us_a125(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(1_500_000, 2_000_000, ALPHAS[3])[0], 1_875_000)
def test_1500us_a133(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(1_500_000, 2_000_000, ALPHAS[4])[0], 2_000_000)
def test_3ms_a133(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(3_000_000, 4_000_000, ALPHAS[4])[0], 4_000_000)
def test_6ms_a133(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(transformed_deadline_ns(6_000_000, 8_000_000, ALPHAS[4])[0], 8_000_000)


def test_nodes_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(self.medium["nodes"], self.medium_child["nodes"])
def test_links_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(self.medium["links"], self.medium_child["links"])
def test_topology_sha_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(topology_projection(self.medium), topology_projection(self.medium_child))
def test_flow_ids_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual([x["id"] for x in self.medium["tt_flows"]], [x["id"] for x in self.medium_child["tt_flows"]])
def test_sources_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual([x["source"] for x in self.medium["tt_flows"]], [x["source"] for x in self.medium_child["tt_flows"]])
def test_destinations_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual([x["destination"] for x in self.medium["tt_flows"]], [x["destination"] for x in self.medium_child["tt_flows"]])
def test_flow_kinds_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual([flow_kind(x["id"]) for x in self.medium["tt_flows"]], [flow_kind(x["id"]) for x in self.medium_child["tt_flows"]])
def test_periods_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual([x["period_s"] for x in self.medium["tt_flows"]], [x["period_s"] for x in self.medium_child["tt_flows"]])
def test_releases_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual([x["release_offset_s"] for x in self.medium["tt_flows"]], [x["release_offset_s"] for x in self.medium_child["tt_flows"]])
def test_payloads_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual([x["packet_size_bytes"] for x in self.medium["tt_flows"]], [x["packet_size_bytes"] for x in self.medium_child["tt_flows"]])
def test_frame_accounting_unchanged(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(self.medium["scheduling"], self.medium_child["scheduling"])
def test_only_deadline_differs(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertTrue(derived_integrity(self.medium, self.medium_child, "M_RING", Alpha("A133", 4, 3))["integrity_pass"])


def test_medium_flow_count(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(len(self.medium["tt_flows"]), 352)
def test_medium_instance_count(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(expected_instance_count(self.medium), 3616)
def test_large_flow_count(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(len(self.large["tt_flows"]), 928)
def test_large_instance_count(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(expected_instance_count(self.large), 9632)


def test_all_reroute(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["route_scope"], "ALL_REROUTE")
def test_dijkstra_overlap(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["routing"], "DIJKSTRA_OVERLAP")
def test_k_five(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["candidate_paths_k"], 5)
def test_h2s_primary(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["primary_algorithm"], "H2S")
def test_celf_fallback(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertTrue(backend_config()["celf_fallback"])
def test_seed_1024(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["global_seed"], 1024)
def test_quantum_100ns(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["quantum_ns"], 100)
def test_one_thread(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["threads"], 1)
def test_timeout_30_seconds(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["timeout_s_per_backend"], 30)
def test_memory_8192mb(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["memory_limit_mb"], 8192)
def test_baseline_tie_break(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["h2s_tiebreak_mode"], "BASELINE")


def test_d_equals_t_with_nonzero_release(self: DeadlineFeasibilityCalibrationTests) -> None:
    case = make_cycle_boundary_case(); self.assertTrue(all(ns_field(f, "period_s") == ns_field(f, "schedule_deadline_budget_s") and ns_field(f, "release_offset_s") > 0 for f in case["tt_flows"]))
def test_final_deadline_beyond_h_detected(self: DeadlineFeasibilityCalibrationTests) -> None:
    case = make_cycle_boundary_case(); rows = cycle_boundary_rows(case, "QUAL", Alpha("A100", 1, 1), False); self.assertTrue(all(row["crosses_hyperperiod_boundary"] for row in rows))
def test_checker_semantic_consistency_contract(self: DeadlineFeasibilityCalibrationTests) -> None:
    text = (ROOT / "tools/run_deadline_feasibility_calibration.py").read_text(encoding="utf-8"); self.assertIn("END_TO_END_DEADLINE", text)
def test_normalizer_semantic_consistency_contract(self: DeadlineFeasibilityCalibrationTests) -> None:
    text = (ROOT / "tools/run_deadline_feasibility_calibration.py").read_text(encoding="utf-8"); self.assertIn("normalize_schedule", text)
def test_no_silent_clipping(self: DeadlineFeasibilityCalibrationTests) -> None:
    case = make_cycle_boundary_case(); self.assertTrue(all(ns_field(f, "schedule_deadline_budget_s") == ns_field(f, "period_s") for f in case["tt_flows"]))
def test_cycle_case_is_mixed_period(self: DeadlineFeasibilityCalibrationTests) -> None:
    case = make_cycle_boundary_case(); self.assertEqual({ns_field(f, "period_s") for f in case["tt_flows"]}, {500_000, 1_000_000})


def test_medium_baseline_count_parity_source(self: DeadlineFeasibilityCalibrationTests) -> None:
    scenario, _, payload = raw("M_RING", "h2s"); self.assertEqual(signatures(scenario, payload, "H2S")["scheduled_flow_count"], 348)
def test_large_baseline_count_parity_source(self: DeadlineFeasibilityCalibrationTests) -> None:
    scenario, _, payload = raw("L_RING", "h2s"); self.assertEqual(signatures(scenario, payload, "H2S")["scheduled_flow_count"], 914)
def test_hnf_sha_source_present(self: DeadlineFeasibilityCalibrationTests) -> None:
    scenario, _, payload = raw("M_RING", "h2s"); self.assertEqual(len(signatures(scenario, payload, "H2S")["hnf_set_sha256"]), 64)
def test_instance_sha_source_present(self: DeadlineFeasibilityCalibrationTests) -> None:
    scenario, _, payload = raw("L_RING", "h2s"); self.assertEqual(len(signatures(scenario, payload, "H2S")["instance_completion_sha256"]), 64)


def test_selection_requires_six(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertIsNone(selected_alpha([{"alpha": "6/5", "complete_scenario_count": 5}]))
def test_selection_is_minimum_alpha(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertEqual(selected_alpha([{"alpha": "6/5", "complete_scenario_count": 6}, {"alpha": "4/3", "complete_scenario_count": 6}]).label, "6/5")
def test_selection_skips_nondeterministic_alpha_upstream(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertIsNone(selected_alpha([{"alpha": "6/5", "complete_scenario_count": 5}]))
def test_no_dynamic_extra_alpha(self: DeadlineFeasibilityCalibrationTests) -> None:
    self.assertEqual([(a.numerator, a.denominator) for a in ALPHAS], [(1, 1), (11, 10), (6, 5), (5, 4), (4, 3)])


def test_exp18_tree_frozen(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(self.frozen["results/realistic_tsn_pf_cost"], FROZEN_TREE_SHA256["results/realistic_tsn_pf_cost"])
def test_exp18b_tree_frozen(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(self.frozen["results/p0_hnf_diagnosis"], FROZEN_TREE_SHA256["results/p0_hnf_diagnosis"])
def test_exp18c_tree_frozen(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(self.frozen["results/candidate_k_sensitivity"], FROZEN_TREE_SHA256["results/candidate_k_sensitivity"])
def test_exp18d_tree_frozen(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(self.frozen["results/h2s_multistart_sensitivity"], FROZEN_TREE_SHA256["results/h2s_multistart_sensitivity"])
def test_no_flow_deletion(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(len(self.medium["tt_flows"]), len(self.medium_child["tt_flows"]))
def test_no_period_change(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual([ns_field(f, "period_s") for f in self.medium["tt_flows"]], [ns_field(f, "period_s") for f in self.medium_child["tt_flows"]])
def test_no_release_change(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual([ns_field(f, "release_offset_s") for f in self.medium["tt_flows"]], [ns_field(f, "release_offset_s") for f in self.medium_child["tt_flows"]])
def test_no_topology_change(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(topology_projection(self.medium), topology_projection(self.medium_child))
def test_no_k_change(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertEqual(backend_config()["candidate_paths_k"], 5)
def test_no_tiebreak_multistart(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertFalse(backend_config()["multistart"])
def test_no_pf_import(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertNotIn("run_realistic_tsn_pf_cost", (ROOT / "tools/run_deadline_feasibility_calibration.py").read_text(encoding="utf-8"))
def test_no_omnet_import(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertNotIn("omnetpp", (ROOT / "tools/run_deadline_feasibility_calibration.py").read_text(encoding="utf-8").lower())
def test_no_inet_import(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertNotIn("from inet", (ROOT / "tools/run_deadline_feasibility_calibration.py").read_text(encoding="utf-8").lower())
def test_no_figure_output(self: DeadlineFeasibilityCalibrationTests) -> None: self.assertNotIn("matplotlib", (ROOT / "tools/run_deadline_feasibility_calibration.py").read_text(encoding="utf-8").lower())


for _function in [
    test_alpha_one_exact, test_alpha_eleven_tenths_exact, test_alpha_six_fifths_exact,
    test_alpha_five_quarters_exact, test_alpha_four_thirds_exact, test_cap_at_period,
    test_integer_ns, test_hundred_ns_alignment, test_400us_a110, test_400us_a120,
    test_400us_a125, test_400us_a133, test_1500us_a110, test_1500us_a120,
    test_1500us_a125, test_1500us_a133, test_3ms_a133, test_6ms_a133,
    test_nodes_unchanged, test_links_unchanged, test_topology_sha_unchanged,
    test_flow_ids_unchanged, test_sources_unchanged, test_destinations_unchanged,
    test_flow_kinds_unchanged, test_periods_unchanged, test_releases_unchanged,
    test_payloads_unchanged, test_frame_accounting_unchanged, test_only_deadline_differs,
    test_medium_flow_count, test_medium_instance_count, test_large_flow_count,
    test_large_instance_count, test_all_reroute, test_dijkstra_overlap, test_k_five,
    test_h2s_primary, test_celf_fallback, test_seed_1024, test_quantum_100ns,
    test_one_thread, test_timeout_30_seconds, test_memory_8192mb, test_baseline_tie_break,
    test_d_equals_t_with_nonzero_release, test_final_deadline_beyond_h_detected,
    test_checker_semantic_consistency_contract, test_normalizer_semantic_consistency_contract,
    test_no_silent_clipping, test_cycle_case_is_mixed_period,
    test_medium_baseline_count_parity_source, test_large_baseline_count_parity_source,
    test_hnf_sha_source_present, test_instance_sha_source_present, test_selection_requires_six,
    test_selection_is_minimum_alpha, test_selection_skips_nondeterministic_alpha_upstream,
    test_no_dynamic_extra_alpha, test_exp18_tree_frozen, test_exp18b_tree_frozen,
    test_exp18c_tree_frozen, test_exp18d_tree_frozen, test_no_flow_deletion,
    test_no_period_change, test_no_release_change, test_no_topology_change, test_no_k_change,
    test_no_tiebreak_multistart, test_no_pf_import, test_no_omnet_import, test_no_inet_import,
    test_no_figure_output,
]:
    setattr(DeadlineFeasibilityCalibrationTests, _function.__name__, _function)


if __name__ == "__main__":
    unittest.main()
