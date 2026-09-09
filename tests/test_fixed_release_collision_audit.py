"""Qualification tests for the exp18i static collision audit.

The detector module is intentionally exercised before any historical HNF join.
The generated methods below keep the preregistered test matrix individually
visible to unittest while sharing compact deterministic fixtures.
"""
from __future__ import annotations

import csv
import inspect
import json
import unittest
from pathlib import Path

from tools.fixed_release_collision_audit import (
    HYPERCYCLE_TICKS, ScenarioRef, TimedFlow, analyze_scenario, circular_segments,
    formal_scenario_refs, graph_sha256, intervals_overlap, is_independent, is_vertex_cover,
    load_scenario, logical_conflicts, minimum_vertex_cover, scenario_ref, source_egress_audit,
    timing_audit,
)
from tools.run_fixed_release_collision_audit import FROZEN_ROOTS, OUT, frozen_preflight, h2s_raw_signature

ROOT = Path(__file__).resolve().parents[1]
DENSITY_RESULTS = ROOT / "results" / "tt_workload_density_calibration"


def edge_graph(vertices: str, edges: list[tuple[str, str]]):
    return minimum_vertex_cover(tuple(vertices), edges)[1]


class FixedReleaseCollisionAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.medium = analyze_scenario(scenario_ref("M_RING", "D070"))
        cls.large = analyze_scenario(scenario_ref("L_RING", "D050"))

    def test_interval_nonoverlap(self) -> None: self.assertFalse(intervals_overlap((1, 3), (3, 5)))
    def test_interval_touching_is_not_collision(self) -> None: self.assertFalse(intervals_overlap((10, 20), (20, 30)))
    def test_interval_simple_overlap(self) -> None: self.assertTrue(intervals_overlap((10, 21), (20, 30)))
    def test_interval_contained_overlap(self) -> None: self.assertTrue(intervals_overlap((10, 30), (12, 20)))
    def test_interval_same_start(self) -> None: self.assertTrue(intervals_overlap((10, 20), (10, 11)))
    def test_interval_wrap_overlap(self) -> None: self.assertTrue(any(intervals_overlap(a, b) for a in circular_segments(HYPERCYCLE_TICKS - 4, 8) for b in circular_segments(1, 4)))
    def test_interval_wrap_nonoverlap(self) -> None: self.assertFalse(any(intervals_overlap(a, b) for a in circular_segments(HYPERCYCLE_TICKS - 4, 3) for b in circular_segments(4, 5)))
    def test_interval_segments_are_half_open(self) -> None: self.assertEqual(circular_segments(10, 5), [(10, 15)])

    def test_instance_count_half_ms(self) -> None: self.assertEqual(next(row for row in self.large["timing_rows"] if row["period_ticks"] == 5000)["frames_per_hyper_cycle"], 16)
    def test_instance_count_two_ms(self) -> None: self.assertEqual(next(row for row in self.large["timing_rows"] if row["period_ticks"] == 20000)["frames_per_hyper_cycle"], 4)
    def test_instance_count_four_ms(self) -> None: self.assertEqual(next(row for row in self.large["timing_rows"] if row["period_ticks"] == 40000)["frames_per_hyper_cycle"], 2)
    def test_instance_count_eight_ms(self) -> None: self.assertEqual(next(row for row in self.large["timing_rows"] if row["period_ticks"] == 80000)["frames_per_hyper_cycle"], 1)
    def test_release_is_included(self) -> None: self.assertTrue(all(row["release_ticks"] >= 0 for row in self.large["timing_rows"]))
    def test_period_ticks_exact(self) -> None: self.assertTrue(all(HYPERCYCLE_TICKS % row["period_ticks"] == 0 for row in self.large["timing_rows"]))
    def test_hypercycle_ticks_exact(self) -> None: self.assertEqual(HYPERCYCLE_TICKS, 80_000)

    def test_quantum_is_100ns(self) -> None: self.assertTrue(all(row["backend_quantum_ns"] == 100 for row in self.large["timing_rows"]))
    def test_frame_accounting_once(self) -> None: self.assertTrue(all(row["frame_size"] == row["upstream_equivalent_bytes"] for row in self.large["timing_rows"]))
    def test_tx_ticks_positive(self) -> None: self.assertTrue(all(row["tx_ticks"] > 0 for row in self.large["timing_rows"]))
    def test_release_tick_parity_model(self) -> None: self.assertTrue(all(row["release_error_ns"] >= 0 for row in self.large["timing_rows"]))
    def test_deadline_is_not_collision_coordinate(self) -> None: self.assertTrue(all("deadline_ticks" not in row or row["deadline_ticks"] > 0 for row in self.large["timing_rows"]))
    def test_fixed_release_required(self) -> None: self.assertTrue(all(row["fixed_release"] for row in self.large["instance_rows"]))

    def test_unique_source_egress_is_qualified(self) -> None: self.assertTrue(all(row["qualification_pass"] for row in self.large["source_egress_rows"]))
    def test_multiple_source_egress_unqualified(self) -> None:
        scenario = {"nodes": [{"id": "S", "type": "end_system"}, {"id": "A"}, {"id": "B"}, {"id": "D"}], "links": [{"id": "x", "endpoint_a": "S", "endpoint_b": "A"}, {"id": "y", "endpoint_a": "S", "endpoint_b": "B"}], "tt_flows": [{"id": "SF_S", "source": "S", "destination": "D"}]}
        rows, _ = source_egress_audit(ScenarioRef("M_RING", "D100", Path("unused")), scenario); self.assertFalse(rows[0]["qualification_pass"])
    def test_candidate_choice_not_used_as_proof(self) -> None: self.assertEqual(next(row for row in self.large["source_egress_rows"] if row["flow_id"].startswith("CMD_"))["candidate_first_egress_count"], "")
    def test_source_egress_canonicalizes_to_source(self) -> None: self.assertTrue(all(row["canonical_source_egress"] == row["source"] for row in self.large["source_egress_rows"]))
    def test_exp18h_hnf_first_hop_static_parity(self) -> None:
        links = {flow.flow_id: flow.source_egress_link for flow in self.large["flows"]}; self.assertEqual(links["CMD_PLC_C06_M02_A2_C06_M02"], "a_PLC_C06_M02_SW_C06_M02_CTRL")

    def test_one_event_creates_one_logical_edge(self) -> None: self.assertEqual(len(logical_conflicts([{ "scenario": "M_RING", "density": "D100", "source_egress": "S", "flow_a": "a", "flow_b": "b", "kind_a": "x", "kind_b": "y", "instance_a": 0, "instance_b": 0, "start_a": 0, "start_b": 0, "overlap_ticks": 1 }])), 1)
    def test_multiple_events_create_one_logical_edge(self) -> None:
        row = {"scenario": "M_RING", "density": "D100", "source_egress": "S", "flow_a": "a", "flow_b": "b", "kind_a": "x", "kind_b": "y", "instance_a": 0, "instance_b": 0, "start_a": 0, "start_b": 0, "overlap_ticks": 1}; self.assertEqual(logical_conflicts([row, {**row, "instance_a": 1}])[0]["collision_event_count"], 2)
    def test_different_egress_is_distinct_edge_key(self) -> None:
        row = {"scenario": "M_RING", "density": "D100", "source_egress": "S", "flow_a": "a", "flow_b": "b", "kind_a": "x", "kind_b": "y", "instance_a": 0, "instance_b": 0, "start_a": 0, "start_b": 0, "overlap_ticks": 1}; self.assertEqual(len(logical_conflicts([row, {**row, "source_egress": "T"}])), 2)
    def test_large_d050_has_three_edges(self) -> None: self.assertEqual(len(self.large["edges"]), 3)
    def test_graph_hash_is_deterministic(self) -> None: self.assertEqual(graph_sha256(self.large["vertices"], self.large["edges"]), self.large["graph_summary"]["graph_sha256"])
    def test_cross_topology_canonical_flow_ids(self) -> None: self.assertEqual(self.medium["vertices"], analyze_scenario(scenario_ref("M_ROR", "D070"))["vertices"])

    def test_mvc_empty(self) -> None: self.assertEqual(edge_graph("ab", [])["mvc_size"], 0)
    def test_mvc_single_edge(self) -> None: self.assertEqual(edge_graph("ab", [("a", "b")])["mvc_size"], 1)
    def test_mvc_path3(self) -> None: self.assertEqual(edge_graph("abc", [("a", "b"), ("b", "c")])["mvc_size"], 1)
    def test_mvc_triangle(self) -> None: self.assertEqual(edge_graph("abc", [("a", "b"), ("a", "c"), ("b", "c")])["mvc_size"], 2)
    def test_mvc_cycle4(self) -> None: self.assertEqual(edge_graph("abcd", [("a", "b"), ("b", "c"), ("c", "d"), ("a", "d")])["mvc_size"], 2)
    def test_mvc_cycle5(self) -> None: self.assertEqual(edge_graph("abcde", [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"), ("a", "e")])["mvc_size"], 3)
    def test_mvc_star(self) -> None: self.assertEqual(edge_graph("abcde", [("a", "b"), ("a", "c"), ("a", "d"), ("a", "e")])["mvc_size"], 1)
    def test_mvc_disconnected(self) -> None: self.assertEqual(edge_graph("abcd", [("a", "b"), ("c", "d")])["mvc_size"], 2)
    def test_mvc_cover_is_valid(self) -> None: self.assertTrue(is_vertex_cover(self.large["edges"], self.large["mvc"]["one_minimum_cover_flow_ids"].split(";")))
    def test_mvc_lower_bound_not_above_exact(self) -> None: self.assertLessEqual(self.large["mvc"]["mvc_lower_bound"], self.large["mvc"]["mvc_size"])
    def test_mvc_exact_not_above_upper(self) -> None: self.assertLessEqual(self.large["mvc"]["mvc_size"], self.large["mvc"]["mvc_upper_bound"])
    def test_mvc_is_deterministic(self) -> None:
        other = analyze_scenario(scenario_ref("L_RING", "D050"))["mvc"]
        self.assertEqual((self.large["mvc"]["mvc_size"], self.large["mvc"]["one_minimum_cover_flow_ids"]), (other["mvc_size"], other["one_minimum_cover_flow_ids"]))

    def test_hnf_cover_true(self) -> None: self.assertTrue(is_vertex_cover(self.large["edges"], {"CMD_PLC_C06_M02_A2_C06_M02", "CMD_PLC_C06_M02_A4_C06_M02", "COORD_CC_C05_PLC_C05_M02"}))
    def test_hnf_cover_false(self) -> None: self.assertFalse(is_vertex_cover(self.large["edges"], {"CMD_PLC_C06_M02_A2_C06_M02"}))
    def test_scheduled_set_is_independent(self) -> None: self.assertTrue(is_independent(self.large["edges"], set(self.large["vertices"]) - {"CMD_PLC_C06_M02_A2_C06_M02", "CMD_PLC_C06_M02_A4_C06_M02", "COORD_CC_C05_PLC_C05_M02"}))
    def test_scheduled_conflict_detected(self) -> None: self.assertFalse(is_independent(self.large["edges"], {"CMD_PLC_C06_M02_A1_C06_M02", "CMD_PLC_C06_M02_A2_C06_M02"}))
    def test_hnf_minus_mvc_zero(self) -> None: self.assertEqual(3 - self.large["mvc"]["mvc_size"], 0)

    def test_outcome_independent_module_has_no_hnf_import(self) -> None:
        source = inspect.getsource(__import__("tools.fixed_release_collision_audit", fromlist=["*"])); self.assertNotIn("run_p0_hnf", source)
    def test_detector_has_no_solver_invocation(self) -> None:
        source = inspect.getsource(__import__("tools.fixed_release_collision_audit", fromlist=["*"])); self.assertNotIn("H2sJrsBackend", source)
    def test_graph_build_precedes_history_in_runner(self) -> None:
        source = inspect.getsource(__import__("tools.run_fixed_release_collision_audit", fromlist=["*"])); run = source[source.index("def run"):]; self.assertLess(run.index("analyses ="), run.index("historical_cover_checks"))

    def test_no_formal_results_overwrite_by_tests(self) -> None: self.assertFalse((OUT / "collision_verdict.json").exists())
    def test_frozen_roots_preflight_count(self) -> None: self.assertEqual(len(FROZEN_ROOTS), 8)
    def test_full_cohort_has_48_scenario_density_inputs(self) -> None: self.assertEqual(len(formal_scenario_refs()), 48)
    def test_static_module_has_no_celf_runner(self) -> None: self.assertNotIn("CELF", inspect.getsource(__import__("tools.fixed_release_collision_audit", fromlist=["*"])))
    def test_static_module_has_no_pf_runner(self) -> None: self.assertNotIn("h2s_pf", inspect.getsource(__import__("tools.fixed_release_collision_audit", fromlist=["*"])))
    def test_static_module_has_no_omnet_runner(self) -> None: self.assertNotIn("omnet_runner", inspect.getsource(__import__("tools.fixed_release_collision_audit", fromlist=["*"])))
    def test_static_module_has_no_scenario_writer(self) -> None: self.assertNotIn("write_text", inspect.getsource(__import__("tools.fixed_release_collision_audit", fromlist=["*"])))


def _density_test(scale: str, density: str, expected: int):
    def test(self: FixedReleaseCollisionAuditTests) -> None:
        self.assertEqual(len(load_scenario(scenario_ref(f"{scale}_RING", density))["tt_flows"]), expected)
    return test


for _scale, _counts in (("M", (352, 331, 314, 295, 278, 243, 207, 176)), ("L", (928, 879, 832, 785, 738, 647, 553, 464))):
    for _density, _count in zip(("D100", "D095", "D090", "D085", "D080", "D070", "D060", "D050"), _counts):
        setattr(FixedReleaseCollisionAuditTests, f"test_density_{_scale}_{_density}", _density_test(_scale, _density, _count))


def _deficit_test(scale: str, density: str, expected: int):
    def test(self: FixedReleaseCollisionAuditTests) -> None:
        self.assertEqual(len(h2s_raw_signature(f"{scale}_RING", density)["hnf_flow_ids"]), expected)
    return test


for _scale, _values in (("M", (4, 3, 2, 2, 1, 0)), ("L", (14, 13, 13, 12, 9, 8, 6, 3))):
    _tags = ("D100", "D095", "D090", "D085", "D080", "D070") if _scale == "M" else ("D100", "D095", "D090", "D085", "D080", "D070", "D060", "D050")
    for _density, _value in zip(_tags, _values):
        setattr(FixedReleaseCollisionAuditTests, f"test_historical_deficit_{_scale}_{_density}", _deficit_test(_scale, _density, _value))


def _freeze_test(root: str):
    def test(self: FixedReleaseCollisionAuditTests) -> None:
        self.assertEqual(len(frozen_preflight()[root]), 64)
    return test


for _root in FROZEN_ROOTS:
    setattr(FixedReleaseCollisionAuditTests, "test_frozen_" + _root.replace("/", "_").replace("-", "_"), _freeze_test(_root))


if __name__ == "__main__":
    unittest.main()
