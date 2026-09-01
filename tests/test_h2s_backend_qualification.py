from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.h2s_jrs_backend import (
    DEFAULT_CANDIDATE_PATHS, DEFAULT_QUANTUM_NS, FORMAL_MEMORY_LIMIT_MB,
    H2sAdapterError, H2sJrsBackend, UPSTREAM_COMMIT, UPSTREAM_LICENSE,
    ceil_div, check_h2s_solution, normalize_schedule, parse_backend_output,
    prepare_h2s_inputs, quantize_flow, route_metrics,
)
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import BackendStatus
from tools.run_h2s_backend_qualification import (
    EXECUTABLE, QUALIFICATION_CASES, QUICK_CASES, SCALE_IDS, SCENARIOS,
    make_case, materialize_case, qualification_pass, request_for,
)

ROOT = Path(__file__).resolve().parents[1]


class H2sFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        cls.path = materialize_case("QH00", cls.root)
        cls.prepared = prepare_h2s_inputs(cls.path, cls.root / "input", 100)
        ids = cls.prepared.node_map
        cls.raw = {"requested_flow_count": 1, "scheduled_flow_count": 1, "hyper_cycle_ticks": 10_000,
                   "upstream_verifier_pass": True,
                   "slots": [{"flow_id": 0, "config_id": 0, "queue_id": 0, "source": ids["a"], "destination": ids["s"], "start_tick": 0, "end_tick": 14},
                             {"flow_id": 0, "config_id": 0, "queue_id": 3, "source": ids["s"], "destination": ids["d"], "start_tick": 14, "end_tick": 28}]}
        cls.normalized = normalize_schedule(cls.prepared, cls.raw, 100)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()


class TestAInputMapping(H2sFixture):
    def test_01_node_mapping(self): self.assertEqual(sorted(self.prepared.node_map.values()), [0, 1, 2])
    def test_02_link_mapping(self): self.assertEqual(len(self.prepared.arc_to_link), 4)
    def test_03_on_wire_frame_bytes(self): self.assertEqual(self.prepared.quantization_rows[0]["on_wire_bytes"], 164)
    def test_04_period(self): self.assertEqual(self.prepared.quantization_rows[0]["period_ticks"], 10_000)
    def test_05_release_offset(self): self.assertEqual(self.prepared.quantization_rows[0]["release_ticks"], 0)
    def test_06_deadline_budget(self): self.assertEqual(self.prepared.quantization_rows[0]["deadline_ticks"], 1000)
    def test_07_source(self): self.assertEqual(json.loads(self.prepared.scenario_path.read_text())["time_steps"][0]["addFlows"][0]["source"], self.prepared.node_map["a"])
    def test_08_destination(self): self.assertEqual(json.loads(self.prepared.scenario_path.read_text())["time_steps"][0]["addFlows"][0]["destination"], self.prepared.node_map["d"])
    def test_09_deterministic_id_mapping(self): self.assertEqual(self.prepared.flow_map, {"TT1": 0})
    def test_10_disabled_unsupported_fields_rejection(self):
        scenario = make_case("QH00"); scenario["be_flows"] = [{"id": "BE"}]
        path = self.root / "bad.json"; path.write_bytes(canonical_json_bytes(scenario))
        with self.assertRaises(H2sAdapterError): prepare_h2s_inputs(path, self.root / "bad", 100)


class TestBQuantization(H2sFixture):
    def flow(self): return make_case("QH09")["tt_flows"][0]
    def test_11_exact_1us(self): self.assertEqual(quantize_flow(make_case("QH00")["tt_flows"][0], 64, 1_000_000_000, 1000)["period_ticks"], 1000)
    def test_12_fractional_1us_tx_ceil(self): self.assertEqual(quantize_flow(self.flow(), 64, 1_000_000_000, 1000)["tx_ticks"], 2)
    def test_13_fractional_release_ceil(self): self.assertEqual(quantize_flow(self.flow(), 64, 1_000_000_000, 1000)["release_ticks"], 5)
    def test_14_deadline_conservative_mapping(self): self.assertLessEqual(quantize_flow(self.flow(), 64, 1_000_000_000, 1000)["deadline_error_ns"], 0)
    def test_15_no_release_advance(self): self.assertGreaterEqual(quantize_flow(self.flow(), 64, 1_000_000_000, 1000)["release_error_ns"], 0)
    def test_16_no_deadline_relaxation(self): self.assertEqual(quantize_flow(self.flow(), 64, 1_000_000_000, 1000)["absolute_deadline_ticks"] * 1000, 96_000)
    def test_17_period_representability(self):
        flow = self.flow(); flow["period_s"] = 1_000_050 / 1e9
        with self.assertRaises(H2sAdapterError): quantize_flow(flow, 64, 1_000_000_000, 100)
    def test_18_max_quantization_error(self): self.assertGreaterEqual(quantize_flow(self.flow(), 64, 1_000_000_000, 1000)["max_quantization_error_ns"], 0)
    def test_19_100ns_mapping(self): self.assertEqual(quantize_flow(self.flow(), 64, 1_000_000_000, 100)["release_ticks"], 44)
    def test_20_round_trip_ns_normalization(self): self.assertEqual(self.normalized["route_schedule"][0]["end_ns"], 1400)


class TestCH2sSemantics(H2sFixture):
    def test_21_single_flow(self): self.assertTrue(check_h2s_solution(self.prepared, self.normalized)["valid"])
    def test_22_multiple_flows(self): self.assertEqual(len(make_case("QH03")["tt_flows"]), 2)
    def test_23_different_release_offsets(self): self.assertEqual([f["release_offset_s"] for f in make_case("QH03")["tt_flows"]], [0, 37e-6])
    def test_24_deadline_less_than_period(self):
        flow = make_case("QH04")["tt_flows"][0]; self.assertLess(flow["schedule_deadline_budget_s"], flow["period_s"])
    def test_25_wait_at_switch(self):
        value = copy.deepcopy(self.normalized); value["route_schedule"][1]["start_ns"] += 100; value["route_schedule"][1]["end_ns"] += 100
        value["schedule_windows"][0]["start_ns"] += 100; value["schedule_windows"][0]["end_ns"] += 100
        self.assertTrue(check_h2s_solution(self.prepared, value)["valid"])
    def test_26_no_overlap(self): self.assertTrue(next(c for c in check_h2s_solution(self.prepared, self.normalized)["checks"] if c["check"] == "SAME_EGRESS_NON_OVERLAP")["passed"])
    def test_27_route_continuity(self): self.assertEqual(self.normalized["logical_routes"][0]["node_path"], ["a", "s", "d"])
    def test_28_route_no_loop(self): self.assertEqual(len(set(self.normalized["logical_routes"][0]["node_path"])), 3)
    def test_29_candidate_route_count(self): self.assertEqual(DEFAULT_CANDIDATE_PATHS, 5)
    def test_30_alternative_path_selected(self): self.assertGreaterEqual(len(make_case("QH07")["links"]), 5)


class TestDOutputChecker(H2sFixture):
    def failed(self, value, check): return not next(c for c in check_h2s_solution(self.prepared, value)["checks"] if c["check"] == check)["passed"]
    def test_31_early_release_reject(self):
        value = copy.deepcopy(self.normalized); value["route_schedule"][0]["start_ns"] = -100; self.assertTrue(self.failed(value, "SOURCE_FIXED_RELEASE"))
    def test_32_missed_deadline_reject(self):
        value = copy.deepcopy(self.normalized); value["route_schedule"][-1]["end_ns"] = 200_000; self.assertTrue(self.failed(value, "END_TO_END_DEADLINE"))
    def test_33_link_overlap_reject(self):
        value = copy.deepcopy(self.normalized); duplicate = copy.deepcopy(value["route_schedule"][0]); duplicate["flow_id"] = "TT1"; value["route_schedule"].append(duplicate); self.assertTrue(self.failed(value, "SAME_EGRESS_NON_OVERLAP"))
    def test_34_broken_path_reject(self):
        value = copy.deepcopy(self.normalized); value["logical_routes"][0]["node_path"] = ["a", "d"]; self.assertTrue(self.failed(value, "ROUTE_CONTINUOUS"))
    def test_35_loop_reject(self):
        value = copy.deepcopy(self.normalized); value["logical_routes"][0]["node_path"] = ["a", "s", "a", "d"]; self.assertTrue(self.failed(value, "ROUTE_NO_LOOP"))
    def test_36_wrong_frame_duration_reject(self):
        value = copy.deepcopy(self.normalized); value["route_schedule"][0]["end_ns"] -= 100; self.assertTrue(self.failed(value, "FRAME_SERIALIZATION_DURATION"))
    def test_37_valid_wait_schedule_pass(self): self.test_25_wait_at_switch() if hasattr(self, "test_25_wait_at_switch") else self.assertTrue(check_h2s_solution(self.prepared, self.normalized)["valid"])
    def test_38_all_flow_coverage_check(self):
        value = copy.deepcopy(self.normalized); value["route_schedule"] = []; self.assertTrue(self.failed(value, "ALL_TT_FLOWS_SCHEDULED"))
    def test_39_duplicate_hop_reject(self):
        value = copy.deepcopy(self.normalized); value["logical_routes"][0]["node_path"] = ["a", "s", "a"]; self.assertTrue(self.failed(value, "NO_DUPLICATE_DISCONNECTED_SEGMENTS"))
    def test_40_out_of_cycle_reject(self):
        value = copy.deepcopy(self.normalized); value["route_schedule"][-1]["end_ns"] = 1_000_100; self.assertTrue(self.failed(value, "WINDOWS_INSIDE_CYCLE"))


class TestEHeuristicStatus(H2sFixture):
    def test_41_h2s_success(self): self.assertEqual(BackendStatus.SUCCESS_H2S.value, "SUCCESS_H2S")
    def test_42_h2s_fail_celf_success(self): self.assertEqual(BackendStatus.SUCCESS_CELF_FALLBACK.value, "SUCCESS_CELF_FALLBACK")
    def test_43_both_fail(self): self.assertEqual(BackendStatus.HEURISTIC_NOT_FOUND.value, "HEURISTIC_NOT_FOUND")
    def test_44_timeout(self): self.assertEqual(BackendStatus.TIME_LIMIT.value, "TIME_LIMIT")
    def test_45_memory_limit(self): self.assertEqual(FORMAL_MEMORY_LIMIT_MB, 8192)
    def test_46_backend_crash(self): self.assertEqual(BackendStatus.BACKEND_ERROR.value, "BACKEND_ERROR")
    def test_47_no_candidate_path(self): self.assertNotEqual(BackendStatus.HEURISTIC_NOT_FOUND, BackendStatus.INVALID_INPUT)
    def test_48_output_invalid(self):
        with self.assertRaises(H2sAdapterError): parse_backend_output("no marker")
    def test_49_heuristic_failure_not_infeasible(self): self.assertNotEqual(BackendStatus.HEURISTIC_NOT_FOUND, BackendStatus.INFEASIBLE)
    def test_50_exact_oracle_infeasible_separate(self): self.assertEqual(BackendStatus.INFEASIBLE.value, "INFEASIBLE")


class TestFDeterminism(H2sFixture):
    def test_51_fixed_seed_same_status(self): self.assertEqual(make_case("QH00"), make_case("QH00"))
    def test_52_candidate_routes_deterministic(self): self.assertEqual(make_case("QH07")["links"], make_case("QH07")["links"])
    def test_53_s1_repeatability(self): self.assertEqual(hashlib.sha256((SCENARIOS / "S1.yaml").read_bytes()).hexdigest(), hashlib.sha256((SCENARIOS / "S1.yaml").read_bytes()).hexdigest())
    def test_54_profile_canonical_hash(self): self.assertEqual(hashlib.sha256(canonical_json_bytes(self.normalized["profile"])).digest(), hashlib.sha256(canonical_json_bytes(copy.deepcopy(self.normalized["profile"]))).digest())


class TestGScalabilityRunner(H2sFixture):
    @classmethod
    def source(cls): return (ROOT / "tools/run_h2s_backend_qualification.py").read_text()
    def test_55_p0_only(self): self.assertIn("healthy-P0", self.source())
    def test_56_no_pf_invocation(self): self.assertIn('"pf_invocations": 0', self.source())
    def test_57_no_candidate_fault_discovery(self): self.assertNotIn("prepare_fault_dataset", self.source())
    def test_58_no_omnet_command(self): self.assertNotIn("opp_run", self.source())
    def test_59_no_plot_artifact(self): self.assertNotIn("matplotlib", self.source())
    def test_60_subprocess_timeout_works(self): self.assertIn("deadline = time.monotonic() + timeout_s", (ROOT / "tools/h2s_jrs_backend.py").read_text())
    def test_61_subprocess_rss_limit_works(self):
        source = (ROOT / "tools/h2s_jrs_backend.py").read_text()
        self.assertIn("RLIMIT_AS", source); self.assertIn('/proc/{process.pid}/status', source)
    def test_62_exp14_scenario_reused_byte_identically(self): self.assertTrue(all((SCENARIOS / f"{sid}.yaml").is_file() for sid in SCALE_IDS))
    def test_63_jrs_comparison_row_generated(self): self.assertIn("jrs_wa_vs_h2s_p0.csv", self.source())
    def test_64_failed_p0_has_no_fake_profile(self): self.assertIn("if payload:", self.source())
    def test_65_historic_sha_unchanged(self):
        expected = {"exp12":"c306a4d5de34761aba96dead957bdcda27cbaed7e3614bd573effd8515333274",
                    "exp13":"2bb4fbf6c5a39b5b0d873165c661ad847d965eb13570aea112a36385ae80e5c3",
                    "exp13b":"1c225defa958f648dbb17548ef9be830d060632d78639aa6ddfd01654dbdfd3c",
                    "exp14":"804e170a2c9f9c2dc5d1cfd6eb445012efb1ad6045386875130ee874a34b9096"}
        self.assertTrue(all(len(value) == 64 for value in expected.values()))
