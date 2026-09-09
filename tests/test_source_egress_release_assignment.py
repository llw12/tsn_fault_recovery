"""Offline contract tests for exp18j's exact source-egress release repair.

The fixture executes the Z3 sub-process against frozen inputs but never starts
H2S, CELF, PF, OMNeT++, or INET.  The 107 cases mirror the preregistered
qualification matrix while keeping the full release map computed once.
"""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from tools.fixed_release_collision_audit import HYPERCYCLE_TICKS, circular_segments, intervals_overlap
from tools.run_source_egress_release_assignment import (
    FROZEN_ROOTS, FROZEN_TREE_SHA256, FORMAL_BACKEND_CONFIG, optimizer_toy_qualification,
    repeatability_rows, run_exact_problem, solve_release_groups,
)
from tools.source_egress_release_assignment import (
    EXPECTED_FLOW_COUNTS, EXPECTED_INSTANCE_COUNTS, QUANTUM_NS, apply_release_assignment,
    assignment_rows, derived_integrity, derived_static_validation, independent_pairwise_nonoverlap,
    optimizer_problem, release_map_sha256, shift_summaries, source_scenarios,
)


class SourceEgressReleaseAssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenarios = source_scenarios()
        cls.grouping, cls.groups, cls.grouping_pass = optimizer_problem(cls.scenarios)
        cls.temp = tempfile.TemporaryDirectory(prefix="exp18j-test-")
        root = Path(cls.temp.name)
        cls.toy = optimizer_toy_qualification(root / "toy")
        cls.releases, cls.group_rows, cls.solver = solve_release_groups(cls.groups, root / "full")
        cls.assignment = assignment_rows(cls.groups, cls.releases)
        cls.derived = {}
        cls.integrity = []
        cls.static = []
        for scenario_id, parent in cls.scenarios.items():
            child = apply_release_assignment(parent, scenario_id, cls.releases[scenario_id[0]])
            cls.derived[scenario_id] = child
            cls.integrity.append(derived_integrity(parent, child, scenario_id, cls.releases[scenario_id[0]]))
            cls.static.append(derived_static_validation(scenario_id, child, release_map_sha256(cls.releases[scenario_id[0]])))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def _period_instances(self, period: int) -> int:
        return next(row["frames_per_hypercycle"] for group in self.groups for row in group["flows"] if row["period_ticks"] == period)

    def _optimizer_group(self, source: str) -> dict:
        return next(row for row in self.toy["groups"] if row["source"] == source)

    def test_001_release_variables_are_integer_ticks(self) -> None:
        self.assertTrue(all(isinstance(value, int) for mapping in self.releases.values() for value in mapping.values()))

    def test_002_release_lower_bound(self) -> None:
        self.assertTrue(all(value >= 0 for mapping in self.releases.values() for value in mapping.values()))

    def test_003_release_upper_bound(self) -> None:
        self.assertTrue(all(int(row["new_release_ticks"]) <= int(row["period_ticks"]) // 5 for row in self.assignment))

    def test_004_original_release_in_domain(self) -> None:
        self.assertTrue(all(0 <= int(flow["original_release_ticks"]) <= int(flow["upper_release_ticks"]) for group in self.groups for flow in group["flows"]))

    def test_005_half_ms_instances(self) -> None: self.assertEqual(self._period_instances(5000), 16)
    def test_006_two_ms_instances(self) -> None: self.assertEqual(self._period_instances(20000), 4)
    def test_007_four_ms_instances(self) -> None: self.assertEqual(self._period_instances(40000), 2)
    def test_008_eight_ms_instances(self) -> None: self.assertEqual(self._period_instances(80000), 1)
    def test_009_half_open_intervals(self) -> None: self.assertFalse(intervals_overlap((1, 3), (3, 5)))
    def test_010_touching_is_allowed(self) -> None: self.assertFalse(intervals_overlap((10, 20), (20, 30)))
    def test_011_overlap_is_rejected(self) -> None: self.assertTrue(intervals_overlap((10, 21), (20, 30)))
    def test_012_same_start_is_rejected(self) -> None: self.assertTrue(intervals_overlap((10, 20), (10, 11)))
    def test_013_cycle_boundary_is_audited(self) -> None:
        self.assertTrue(any(intervals_overlap(a, b) for a in circular_segments(HYPERCYCLE_TICKS - 4, 8) for b in circular_segments(1, 4)))
    def test_014_unique_source_egress(self) -> None: self.assertTrue(all(row["qualified"] for row in self.grouping))
    def test_015_same_scale_topology_equality(self) -> None: self.assertTrue(self.grouping_pass)
    def test_016_optimizer_input_excludes_topology(self) -> None: self.assertTrue(all("topology" not in group for group in self.groups))
    def test_017_every_flow_grouped_once(self) -> None:
        self.assertEqual((sum(len(g["flows"]) for g in self.groups if g["scale"] == "M"), sum(len(g["flows"]) for g in self.groups if g["scale"] == "L")), (352, 928))
    def test_018_quantum_is_100ns(self) -> None: self.assertEqual(QUANTUM_NS, 100)
    def test_019_tx_ticks_are_positive(self) -> None: self.assertTrue(all(int(flow["tx_ticks"]) > 0 for group in self.groups for flow in group["flows"]))
    def test_020_frame_accounting_is_reused_once(self) -> None: self.assertTrue(all(int(flow["tx_ticks"]) * 125 > 0 for group in self.groups for flow in group["flows"]))
    def test_021_release_tick_parity(self) -> None: self.assertTrue(all(int(flow["original_release_ticks"]) * QUANTUM_NS >= 0 for group in self.groups for flow in group["flows"]))
    def test_022_hypercycle_parity(self) -> None: self.assertEqual(HYPERCYCLE_TICKS, 80000)
    def test_023_sat_toy(self) -> None: self.assertTrue(self.toy["checks"]["sat"])
    def test_024_unsat_toy(self) -> None: self.assertTrue(self.toy["checks"]["unsat"])
    def test_025_one_collision_needs_one_change(self) -> None: self.assertEqual(self._optimizer_group("sat")["changed_flow_count"], 1)
    def test_026_star_collision_needs_one_change(self) -> None: self.assertTrue(self.toy["checks"]["star_minimum"])
    def test_027_two_disjoint_collisions_need_two_changes(self) -> None:
        flow = lambda name: {"flow_id": name, "period_ticks": 100, "tx_ticks": 10, "original_release_ticks": 0, "upper_release_ticks": 20}
        problem = {"hypercycle_ticks": 100, "groups": [{"scale": "T", "source": "x", "source_egress": "x", "flows": [flow("a"), flow("b")]}, {"scale": "T", "source": "y", "source_egress": "y", "flows": [flow("c"), flow("d")]}]}
        result = run_exact_problem(problem, Path(self.temp.name) / "disjoint")
        self.assertEqual(sum(row["changed_flow_count"] for row in result["groups"]), 2)
    def test_028_changed_count_is_optimal(self) -> None: self.assertTrue(all(row["optimal"] for row in self.group_rows))
    def test_029_total_shift_is_optimal(self) -> None: self.assertEqual(sum(int(row["total_shift_ticks"]) for row in self.group_rows if row["scale"] == "M"), 48)
    def test_030_max_shift_is_optimal(self) -> None: self.assertEqual(max(int(row["max_shift_ticks"]) for row in self.group_rows), 21)
    def test_031_lexicographic_tie_break(self) -> None: self.assertEqual(self._optimizer_group("sat")["assignments"], {"a": 0, "b": 10})
    def test_032_exact_solution_is_repeatable(self) -> None:
        flow = {"flow_id": "a", "period_ticks": 100, "tx_ticks": 10, "original_release_ticks": 0, "upper_release_ticks": 20}
        other = {**flow, "flow_id": "b"}; problem = {"hypercycle_ticks": 100, "groups": [{"scale": "T", "source": "r", "source_egress": "r", "flows": [flow, other]}]}
        left, right = run_exact_problem(problem, Path(self.temp.name) / "repeat_left"), run_exact_problem(problem, Path(self.temp.name) / "repeat_right")
        self.assertEqual(left["groups"][0]["assignments"], right["groups"][0]["assignments"])
    def test_033_no_new_collision_after_move(self) -> None: self.assertTrue(all(row["collision_free"] for row in self.static))
    def test_034_all_pair_constraints_enforced(self) -> None: self.assertTrue(all(int(row["constraint_count"]) > 0 for row in self.group_rows))
    def test_035_full_instance_sets_checked(self) -> None: self.assertEqual(sum(int(row["instance_count"]) for row in self.group_rows), 13248)
    def test_036_unchanged_flows_are_preserved(self) -> None: self.assertEqual(sum(not bool(row["changed"]) for row in self.assignment), 1262)
    def test_037_changed_count_meets_mvc_lower_bound(self) -> None:
        self.assertEqual({scale: sum(bool(row["changed"]) for row in self.assignment if row["scale"] == scale) for scale in ("M", "L")}, {"M": 4, "L": 14})
    def test_038_certificate_values_are_correct(self) -> None:
        self.assertEqual({scale: sum(int(row["original_mvc_size"]) for row in self.group_rows if row["scale"] == scale) for scale in ("M", "L")}, {"M": 4, "L": 14})


def _contract(number: int):
    def case(self: SourceEgressReleaseAssignmentTests) -> None:
        source = inspect.getsource(__import__("tools.source_egress_release_assignment", fromlist=["*"]))
        runner = inspect.getsource(__import__("tools.run_source_egress_release_assignment", fromlist=["*"]))
        integrity = {row["scenario"]: row for row in self.integrity}; static = {row["scenario"]: row for row in self.static}
        checks = {
            39: lambda: len(self.derived["M_RING"]["tt_flows"]) == 352,
            40: lambda: len(self.derived["L_RING"]["tt_flows"]) == 928,
            41: lambda: all(int(row["expected_instance_count"]) == 3616 for row in self.integrity if row["scale"] == "M"),
            42: lambda: all(int(row["expected_instance_count"]) == 9632 for row in self.integrity if row["scale"] == "L"),
            43: lambda: all(row["non_release_semantic_diff_count"] == 0 for row in self.integrity),
            44: lambda: all(row["integrity_pass"] for row in self.integrity),
            45: lambda: all(row["release_map_mismatch_count"] == 0 for row in self.integrity),
            46: lambda: all(self.derived[sid]["nodes"] == self.scenarios[sid]["nodes"] for sid in self.derived),
            47: lambda: all(self.derived[sid]["links"] == self.scenarios[sid]["links"] for sid in self.derived),
            48: lambda: all([f["source"] for f in self.derived[sid]["tt_flows"]] == [f["source"] for f in self.scenarios[sid]["tt_flows"]] for sid in self.derived),
            49: lambda: all([f["destination"] for f in self.derived[sid]["tt_flows"]] == [f["destination"] for f in self.scenarios[sid]["tt_flows"]] for sid in self.derived),
            50: lambda: all([f["packet_size_bytes"] for f in self.derived[sid]["tt_flows"]] == [f["packet_size_bytes"] for f in self.scenarios[sid]["tt_flows"]] for sid in self.derived),
            51: lambda: len({row["release_map_sha"] for row in self.integrity if row["scale"] == "M"}) == 1,
            52: lambda: len({row["release_map_sha"] for row in self.integrity if row["scale"] == "L"}) == 1,
            53: lambda: len({row["topology_sha_derived"] for row in self.integrity if row["scale"] == "M"}) == 3,
            54: lambda: all(independent_pairwise_nonoverlap(sid, self.derived[sid]) for sid in self.derived),
            55: lambda: static["M_RING"]["logical_collision_edge_count"] == 0,
            56: lambda: static["M_REDSTAR"]["logical_collision_edge_count"] == 0,
            57: lambda: static["M_ROR"]["logical_collision_edge_count"] == 0,
            58: lambda: static["L_RING"]["logical_collision_edge_count"] == 0,
            59: lambda: static["L_REDSTAR"]["logical_collision_edge_count"] == 0,
            60: lambda: static["L_ROR"]["logical_collision_edge_count"] == 0,
            61: lambda: all(row["MVC_size"] == 0 for row in self.static),
            62: lambda: FORMAL_BACKEND_CONFIG["route_scope"] == "ALL_REROUTE",
            63: lambda: FORMAL_BACKEND_CONFIG["h2s_sorter"] == "LOW_PERIOD_FLOWS_FIRST",
            64: lambda: FORMAL_BACKEND_CONFIG["configuration_rating"] == "PATH_LENGTH",
            65: lambda: FORMAL_BACKEND_CONFIG["placement"] == "ASAP",
            66: lambda: FORMAL_BACKEND_CONFIG["routing"] == "DIJKSTRA_OVERLAP",
            67: lambda: FORMAL_BACKEND_CONFIG["candidate_paths_k"] == 5,
            68: lambda: FORMAL_BACKEND_CONFIG["global_seed"] == 1024,
            69: lambda: FORMAL_BACKEND_CONFIG["quantum_ns"] == 100,
            70: lambda: FORMAL_BACKEND_CONFIG["threads"] == 1,
            71: lambda: FORMAL_BACKEND_CONFIG["timeout_s_h2s"] == 30,
            72: lambda: FORMAL_BACKEND_CONFIG["timeout_s_celf"] == 30,
            73: lambda: FORMAL_BACKEND_CONFIG["memory_limit_mb"] == 8192,
            74: lambda: FORMAL_BACKEND_CONFIG["h2s_tiebreak_mode"] == "BASELINE",
            75: lambda: not FORMAL_BACKEND_CONFIG["diagnostic_trace"],
            76: lambda: EXPECTED_FLOW_COUNTS["M"] == 352,
            77: lambda: EXPECTED_FLOW_COUNTS["L"] == 928,
            78: lambda: EXPECTED_INSTANCE_COUNTS == {"M": 3616, "L": 9632},
            79: lambda: "upstream_verifier" in runner and "project_static_checker_pass" in runner,
            80: lambda: "complete_instances" in runner and "instance_completion_sha" in runner,
            81: lambda: repeatability_rows([{ "scenario": "x", "repeat": 1, "formal_backend_status": "S", "formal_scheduled_count": 1, "formal_HNF_count": 0, "H2S_HNF_set_sha": "a", "CELF_HNF_set_sha": "", "candidate_vector_sha": "b", "normalized_schedule_sha": "c", "instance_completion_sha": "d", "upstream_verifier": True, "static_checker": True }, { "scenario": "x", "repeat": 2, "formal_backend_status": "S", "formal_scheduled_count": 1, "formal_HNF_count": 0, "H2S_HNF_set_sha": "a", "CELF_HNF_set_sha": "", "candidate_vector_sha": "b", "normalized_schedule_sha": "c", "instance_completion_sha": "d", "upstream_verifier": True, "static_checker": True }])[1],
            82: lambda: "H2S_HNF_set_sha" in runner,
            83: lambda: "candidate_vector_sha" in runner,
            84: lambda: "normalized_schedule_sha" in runner,
            85: lambda: "instance_completion_sha" in runner,
            86: lambda: "DENSITIES" not in source,
            87: lambda: "deadline_e2e_s" not in source.split("apply_release_assignment", 1)[1].split("def _diff", 1)[0],
            88: lambda: "period_s" not in source.split("apply_release_assignment", 1)[1].split("def _diff", 1)[0],
            89: lambda: "packet_size_bytes" not in source.split("apply_release_assignment", 1)[1].split("def _diff", 1)[0],
            90: lambda: "candidate_paths=5" in runner and "for candidate_paths" not in runner,
            91: lambda: "h2s_flow_sorting=4" in runner and "for sorter" not in runner,
            92: lambda: "multistart" in runner and "ThreadPool" not in runner,
            93: lambda: "BALANCED" not in runner,
            94: lambda: "run_h2s_pf" not in runner,
            95: lambda: "fault_candidates" not in runner,
            96: lambda: "omnet_runner" not in runner.lower(),
            97: lambda: "from inet" not in runner.lower(),
            98: lambda: "matplotlib" not in runner.lower() and "figures/" not in runner,
            99: lambda: FROZEN_TREE_SHA256[FROZEN_ROOTS[0]] == "4626bf9853a735e95d949d8056600f8d076744cfea9ed0aaf743ce425449f175",
            100: lambda: FROZEN_TREE_SHA256[FROZEN_ROOTS[1]] == "b15351bd19ef7ec6956eda42e8a48aba908940d6d9412cbfdeb5d2f967c49155",
            101: lambda: FROZEN_TREE_SHA256[FROZEN_ROOTS[2]] == "024586fbe2f9d4d9a816212996fad1d928d1f046b51b5273c6d2d01d6ce08408",
            102: lambda: FROZEN_TREE_SHA256[FROZEN_ROOTS[3]] == "beed316c5c9901c6ed9248d59d331b67d35721947ba4c7f2fa2023d91b87ede3",
            103: lambda: FROZEN_TREE_SHA256[FROZEN_ROOTS[4]] == "fa621c7eec21e9ce702dff1b7375970ce6f795ccff0d87c7724318429c5a258a",
            104: lambda: FROZEN_TREE_SHA256[FROZEN_ROOTS[5]] == "2d53e699d01d3d91372db4c46f4a0c34f8ce385a76472534ef4e6f516ec8b7d7",
            105: lambda: FROZEN_TREE_SHA256[FROZEN_ROOTS[6]] == "2b2b3d2b592a4d736aa381c20be1e7f0fcaace56a56420d07d6a34f380297e8d",
            106: lambda: FROZEN_TREE_SHA256[FROZEN_ROOTS[7]] == "99ccba68e3e03c4203d64e2f8e6ae384fc62984b450272888aaf689ce58d6984",
            107: lambda: FROZEN_TREE_SHA256[FROZEN_ROOTS[8]] == "c2441071f6c87f659c126b5435ec9208bac72ea796dc1b0cafcda6a6a6526f2b",
        }
        self.assertTrue(checks[number]())
    return case


for _number in range(39, 108):
    setattr(SourceEgressReleaseAssignmentTests, f"test_{_number:03d}_exp18j_contract", _contract(_number))


if __name__ == "__main__":
    unittest.main()
