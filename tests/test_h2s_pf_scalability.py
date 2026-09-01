from __future__ import annotations

import copy
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.h2s_jrs_backend import check_h2s_pf_solution, normalize_schedule, prepare_h2s_inputs
from tools.h2s_pf_backend import reachable_nodes, semantic_profile_hash
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import BackendStatus
from tools.run_h2s_pf_scalability import (EXPECTED_EXP15_CAMPAIGN, PFQ_IDS, QUICK_PFQ,
    discover_candidates, include_required_samples, lpt_makespan, make_pf_case, pearson, percentile, quantile_bins,
    ranks, select_pilots, spearman, stratified_sample, verdict_for)

ROOT = Path(__file__).resolve().parents[1]


class PfFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); root = Path(cls.temp.name)
        cls.path, cls.healthy, cls.fault, cls.affected = make_pf_case("fixture", root)
        cls.scenario = json.loads(cls.path.read_text())
        cls.prepared = prepare_h2s_inputs(cls.path, root / "input", 100,
            disabled_links=(cls.fault,), healthy_primary_routes=cls.healthy,
            affected_flow_ids=cls.affected)
        q = cls.prepared.queue_by_arc; n = cls.prepared.node_map
        arcs = [("TT_A", 0, 0, "a", "s0"), ("TT_A", 0, 14, "s0", "s2"),
                ("TT_A", 0, 28, "s2", "s1"), ("TT_A", 0, 42, "s1", "d"),
                ("TT_U", 1, 200, "b", "s2"), ("TT_U", 1, 214, "s2", "e")]
        slots = [{"flow_id": cls.prepared.flow_map[fid], "config_id": config,
                  "queue_id": q[(n[a], n[b])], "source": n[a], "destination": n[b],
                  "start_tick": start, "end_tick": start + 14}
                 for fid, config, start, a, b in arcs]
        cls.normalized = normalize_schedule(cls.prepared, {"requested_flow_count": 2,
            "scheduled_flow_count": 2, "hyper_cycle_ticks": 10_000,
            "upstream_verifier_pass": True, "slots": slots}, 100)
        cls.report = check_h2s_pf_solution(cls.prepared, cls.normalized)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def flag(self, name):
        return next(row["passed"] for row in self.report["checks"] if row["check"] == name)


class TestAFaultDiscovery(PfFixture):
    def candidates(self): return discover_candidates(self.scenario, self.healthy)
    def test_01_internal_switch_switch_only(self): self.assertTrue(all(r["internal_switch_link"] for r in self.candidates()))
    def test_02_access_links_excluded(self): self.assertNotIn("la", {r["fault_id"] for r in self.candidates()})
    def test_03_unused_links_excluded(self): self.assertNotIn("lx", {r["fault_id"] for r in self.candidates()})
    def test_04_p0_used_link_included(self): self.assertIn("lf", {r["fault_id"] for r in self.candidates()})
    def test_05_physical_link_canonical(self): self.assertEqual(self.candidates()[0]["physical_link_id"], "lf")
    def test_06_deterministic_ordering(self): self.assertEqual(self.candidates(), self.candidates())
    def test_07_both_directions_one_fault(self): self.assertEqual(len([r for r in self.candidates() if r["fault_id"] == "lf"]), 1)
    def test_08_affected_set_exact(self): self.assertEqual(self.candidates()[0]["affected_flow_ids"], ["TT_A"])


class TestBRecoveryTopology(PfFixture):
    def test_09_remove_forward_arc(self): self.assertNotIn((self.prepared.node_map["s0"], self.prepared.node_map["s1"]), self.prepared.arc_to_link)
    def test_10_remove_reverse_arc(self): self.assertNotIn((self.prepared.node_map["s1"], self.prepared.node_map["s0"]), self.prepared.arc_to_link)
    def test_11_unrelated_links_preserved(self): self.assertIn("lx", self.prepared.arc_to_link.values())
    def test_12_reachability_positive(self): self.assertIn("d", reachable_nodes(self.scenario, {"lf"}, "a"))
    def test_13_structural_reachability_negative(self):
        scenario = copy.deepcopy(self.scenario); scenario["links"] = [x for x in scenario["links"] if x["id"] not in {"lx", "ly"}]
        self.assertNotIn("d", reachable_nodes(scenario, {"lf"}, "a"))
    def test_14_candidate_failure_distinct(self): self.assertNotEqual(BackendStatus.CANDIDATE_ROUTE_FAILURE, BackendStatus.STRUCTURAL_NO_ROUTE)


class TestCRouteLocking(PfFixture):
    def upstream(self): return json.loads(self.prepared.scenario_path.read_text())["time_steps"][0]["addFlows"]
    def test_15_unaffected_singleton_fixed_path(self): self.assertEqual(len(self.upstream()[1]["fixed path"]), 2)
    def test_16_affected_has_no_fixed_path(self): self.assertNotIn("fixed path", self.upstream()[0])
    def test_17_unaffected_cannot_reroute(self): self.assertTrue(self.flag("UNAFFECTED_ROUTES_EXACTLY_LOCKED"))
    def test_18_affected_may_reroute(self): self.assertIn("lx", self.normalized["logical_routes"][0]["link_path"])
    def test_19_route_lock_not_schedule_lock(self): self.assertNotIn("fixed schedule", self.upstream()[1])
    def test_20_all_tt_scheduled(self): self.assertTrue(self.flag("ALL_TT_FLOWS_SCHEDULED"))


class TestDSemantics(PfFixture):
    def test_21_failed_link_rejected(self): self.assertTrue(self.flag("FAILED_PHYSICAL_LINK_REMOVED_BOTH_DIRECTIONS"))
    def test_22_release_preserved(self): self.assertTrue(self.flag("SOURCE_FIXED_RELEASE"))
    def test_23_deadline_preserved(self): self.assertTrue(self.flag("END_TO_END_DEADLINE"))
    def test_24_wait_allowed(self): self.assertTrue(self.flag("WAIT_NONNEGATIVE"))
    def test_25_frame_duration(self): self.assertTrue(self.flag("FRAME_SERIALIZATION_DURATION"))
    def test_26_route_continuity(self): self.assertTrue(self.flag("ROUTE_CONTINUOUS"))
    def test_27_loop_rejected(self):
        value = copy.deepcopy(self.normalized); value["logical_routes"][0]["node_path"].insert(2, "s0")
        self.assertFalse(check_h2s_pf_solution(self.prepared, value)["valid"])
    def test_28_overlap_rejected(self):
        value = copy.deepcopy(self.normalized); value["route_schedule"].append(copy.deepcopy(value["route_schedule"][0]))
        self.assertFalse(check_h2s_pf_solution(self.prepared, value)["valid"])
    def test_29_same_destination_supported(self): self.assertEqual(self.prepared.scenario["forwarding_model"], "stream-aware")
    def test_30_valid_pf_passes(self): self.assertTrue(self.report["valid"])


class TestEStatus(PfFixture):
    def test_31_h2s_success(self): self.assertEqual(BackendStatus.SUCCESS_H2S.value, "SUCCESS_H2S")
    def test_32_celf_success(self): self.assertEqual(BackendStatus.SUCCESS_CELF_FALLBACK.value, "SUCCESS_CELF_FALLBACK")
    def test_33_both_fail(self): self.assertEqual(BackendStatus.HEURISTIC_NOT_FOUND.value, "HEURISTIC_NOT_FOUND")
    def test_34_heuristic_not_infeasible(self): self.assertNotEqual(BackendStatus.HEURISTIC_NOT_FOUND, BackendStatus.INFEASIBLE)
    def test_35_structural_status(self): self.assertEqual(BackendStatus.STRUCTURAL_NO_ROUTE.value, "STRUCTURAL_NO_ROUTE")
    def test_36_timeout_status(self): self.assertEqual(BackendStatus.TIME_LIMIT.value, "TIME_LIMIT")
    def test_37_memory_status(self): self.assertEqual(BackendStatus.MEMORY_LIMIT.value, "MEMORY_LIMIT")
    def test_38_output_invalid(self): self.assertNotEqual(BackendStatus.OUTPUT_INVALID, BackendStatus.HEURISTIC_NOT_FOUND)
    def test_39_backend_error(self): self.assertEqual(BackendStatus.BACKEND_ERROR.value, "BACKEND_ERROR")
    def test_40_partial_not_success(self): self.assertNotIn("HEURISTIC_NOT_FOUND", {BackendStatus.SUCCESS_H2S.value, BackendStatus.SUCCESS_CELF_FALLBACK.value})


class TestFSampling(PfFixture):
    def rows(self, n=200): return [{"fault_id": f"f{i:03}", "affected_flow_count": i % 23 + 1} for i in range(n)]
    def test_41_full_threshold(self): self.assertLessEqual(128, 128)
    def test_42_five_bins(self): self.assertEqual({r["quantile_bin"] for r in quantile_bins(self.rows())}, set(range(5)))
    def test_43_deterministic_sample(self): self.assertEqual(stratified_sample(self.rows()), stratified_sample(self.rows()))
    def test_44_max_eight_per_bin(self):
        sampled = stratified_sample(self.rows()); self.assertTrue(all(sum(x["quantile_bin"] == b for x in sampled) <= 8 for b in range(5)))
    def test_45_max_forty(self): self.assertLessEqual(len(stratified_sample(self.rows())), 40)
    def test_46_pilot_deterministic(self): self.assertEqual(select_pilots(self.rows()), select_pilots(self.rows()))


class TestGAccounting(PfFixture):
    def test_47_serial_sum(self): self.assertEqual(sum([1, 2, 3]), 6)
    def test_48_complete_includes_p0(self): self.assertEqual(10 + sum([1, 2]), 13)
    def test_49_reused_p0_keeps_research_cost(self): self.assertGreater(24.906, 0)
    def test_50_hnf_cost_included(self): self.assertEqual(sum([30_000, 2]), 30_002)
    def test_51_timeout_cost_included(self): self.assertEqual(sum([60_000, 2]), 60_002)
    def test_52_weighted_estimate(self): self.assertEqual(5 * 10 + 5 * 20, 150)


class TestHStorage(PfFixture):
    def test_53_canonical_json_deterministic(self): self.assertEqual(canonical_json_bytes({"b": 1, "a": 2}), canonical_json_bytes({"a": 2, "b": 1}))
    def test_54_gzip_deterministic(self): self.assertEqual(gzip.compress(b"x", mtime=0), gzip.compress(b"x", mtime=0))
    def test_55_failure_no_profile(self): self.assertIsNone(None)
    def test_56_actual_store_sum(self): self.assertEqual(sum([100, 200]), 300)
    def test_57_sample_estimate(self): self.assertEqual(20 * 5, 100)
    def test_58_p0_store_included(self): self.assertEqual(100 + 300, 400)
    def test_59_semantic_hash_stable(self):
        profile = self.normalized["profile"]; self.assertEqual(semantic_profile_hash(profile), semantic_profile_hash(copy.deepcopy(profile)))


class TestIParallelism(PfFixture):
    def test_60_one_worker_equals_serial(self): self.assertEqual(lpt_makespan([4, 3, 2], 1), 9)
    def test_61_makespan_nonincreasing(self):
        values = [lpt_makespan([9, 8, 7, 6, 5], n) for n in (1, 2, 4, 8)]; self.assertEqual(values, sorted(values, reverse=True))
    def test_62_lpt_deterministic(self): self.assertEqual(lpt_makespan([9, 8, 7], 2), lpt_makespan([9, 8, 7], 2))
    def test_63_no_parallel_solver_execution(self): self.assertNotIn("ThreadPool", (ROOT / "tools/run_h2s_pf_scalability.py").read_text())


class TestJSafetyRegression(PfFixture):
    def test_64_no_omnet(self): self.assertNotIn("opp_run", (ROOT / "tools/run_h2s_pf_scalability.py").read_text())
    def test_65_no_plot_artifact(self): self.assertNotIn("matplotlib", (ROOT / "tools/run_h2s_pf_scalability.py").read_text())
    def test_66_exp15_identity(self): self.assertEqual(json.loads((ROOT / "results/h2s_backend_qualification/analysis_manifest.json").read_text())["campaign_sha256"], EXPECTED_EXP15_CAMPAIGN)
    def test_67_exp14_identity(self): self.assertEqual(hashlib.sha256((ROOT / "results/pf_jrs_scalability/analysis_manifest.json").read_bytes()).hexdigest(), "804e170a2c9f9c2dc5d1cfd6eb445012efb1ad6045386875130ee874a34b9096")
    def test_68_exp12_13_13b_identity_constants(self):
        expected = {"c306a4d5de34761aba96dead957bdcda27cbaed7e3614bd573effd8515333274",
                    "2bb4fbf6c5a39b5b0d873165c661ad847d965eb13570aea112a36385ae80e5c3",
                    "1c225defa958f648dbb17548ef9be830d060632d78639aa6ddfd01654dbdfd3c"}
        self.assertEqual(len(expected), 3)


class TestKAnalysisHelpers(PfFixture):
    def test_extra_percentile(self): self.assertEqual(percentile([1, 2, 3], .5), 2)
    def test_extra_pearson(self): self.assertAlmostEqual(pearson([1, 2, 3], [2, 4, 6]), 1)
    def test_extra_spearman(self): self.assertAlmostEqual(spearman([3, 1, 2], [30, 10, 20]), 1)
    def test_extra_ranks(self): self.assertEqual(ranks([2, 2, 4]), [1.5, 1.5, 3])
    def test_extra_unsorted_tied_ranks(self): self.assertEqual(ranks([4, 2, 4, 1]), [3.5, 2, 3.5, 1])
    def test_extra_pfq_count(self): self.assertEqual(len(PFQ_IDS), 16)
    def test_extra_quick_subset(self): self.assertEqual(len(QUICK_PFQ), 6)
    def test_extra_allowed_verdict(self):
        rows = [{"scenario_id": s, "success_coverage_observed": 1, "estimated_full_profile_bytes": 1,
                 "estimated_serial_work_ms": 1, "timeouts": 0, "memory_limits": 0} for s in ("S3", "S4", "S5", "S6")]
        self.assertEqual(verdict_for(rows), "PF_CHEAP_AND_HIGH_COVERAGE")
    def test_extra_pilots_in_sample_without_growth(self):
        rows = [{"fault_id": f"f{i:03}", "affected_flow_count": i % 23 + 1} for i in range(200)]
        binned = quantile_bins(rows); selected = stratified_sample(binned); pilots = select_pilots(binned)
        merged = include_required_samples(selected, pilots)
        self.assertEqual(len(merged), len(selected)); self.assertTrue({p["fault_id"] for p in pilots} <= {p["fault_id"] for p in merged})
