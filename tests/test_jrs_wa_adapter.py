from __future__ import annotations

import csv
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tools.jrs_wa_adapter import (
    AdapterError,
    TSNKIT_COMMIT,
    TSNKIT_INTERNAL_TIME_UNIT_NS,
    TSNKIT_LICENSE,
    TSNKIT_VERSION,
    canonical_json_bytes,
    compile_gate_schedules,
    deterministic_stream_handles,
    exact_equivalent_size_bytes,
    merge_intervals,
    prepare_inputs,
    seconds_to_ns,
    sha256_value,
    tsnkit_rate_code,
)
from tools.recovery_backend import BackendStatus, LegacyBfsZ3Backend, RecoverySynthesisRequest
from tools.scenario_compiler import build_stream_handle_map, compile_scenario, render_ini
from tools.scenario_model import ScenarioValidationError, load_scenario


ROOT = Path(__file__).resolve().parents[1]
MICRO = ROOT / "configs/scenarios/stream_aware_micro.yaml"


class AdapterFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.generated = compile_scenario(MICRO, cls.root)
        cls.scenario_path = cls.generated / "scenario.json"
        cls.input_dir = cls.root / "inputs"
        cls.prepared = prepare_inputs(cls.scenario_path, cls.input_dir)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()


class TestStreamIdentity(AdapterFixture):
    def test_01_legacy_default_unchanged(self):
        self.assertEqual(load_scenario(ROOT / "configs/scenarios/diamond.yaml").forwarding_model, "destination-mac")

    def test_02_micro_is_stream_aware(self):
        self.assertEqual(load_scenario(MICRO).forwarding_model, "stream-aware")

    def test_03_stable_handle_order(self):
        self.assertEqual(deterministic_stream_handles(["TT2", "TT1"]), {"TT1": 1, "TT2": 2})

    def test_04_handle_repeatability(self):
        self.assertEqual(deterministic_stream_handles(["B", "A"]), deterministic_stream_handles(["A", "B"]))

    def test_05_handle_range_start(self):
        self.assertEqual(deterministic_stream_handles(["TT"])["TT"], 1)

    def test_06_handle_range_end(self):
        handles = deterministic_stream_handles([f"F{i:04d}" for i in range(4094)])
        self.assertEqual(max(handles.values()), 4094)

    def test_07_handle_overflow_fails(self):
        with self.assertRaises(AdapterError):
            deterministic_stream_handles([f"F{i:04d}" for i in range(4095)])

    def test_08_duplicate_handle_input_fails(self):
        with self.assertRaises(AdapterError):
            deterministic_stream_handles(["TT", "TT"])

    def test_09_compiler_map_matches_adapter(self):
        model = load_scenario(MICRO)
        self.assertEqual(build_stream_handle_map(model)["flow_to_stream_handle"], self.prepared.stream_handles)

    def test_10_stream_map_is_written(self):
        self.assertTrue((self.generated / "stream_handle_map.json").exists())

    def test_11_source_encoder_contains_vlan(self):
        ini = render_ini(load_scenario(MICRO))
        self.assertIn('stream: "TT1", pcp: 4, vlan: 1', ini)

    def test_12_switch_decoder_contains_vlan(self):
        ini = render_ini(load_scenario(MICRO))
        self.assertIn('*.s0.bridging.streamCoder.decoder.mapping = [{vlan: 1, stream: "TT1"}', ini)

    def test_13_destination_decoder_enabled(self):
        self.assertIn("*.destination.hasIncomingStreams = true", render_ini(load_scenario(MICRO)))

    def test_14_be_has_no_unique_vlan(self):
        ini = render_ini(load_scenario(MICRO))
        self.assertIn('{stream: "BE", pcp: 0}', ini)
        self.assertNotIn('{stream: "BE", pcp: 0, vlan:', ini)

    def test_15_unknown_forwarding_model_fails(self):
        text = MICRO.read_text().replace("stream-aware", "not-a-model", 1)
        path = self.root / "bad_model.yaml"
        path.write_text(text)
        with self.assertRaises(ScenarioValidationError):
            load_scenario(path)


class TestInputMapping(AdapterFixture):
    def test_16_node_mapping_sorted(self):
        self.assertEqual(list(self.prepared.node_map), sorted(self.prepared.node_map))

    def test_17_node_mapping_successive(self):
        self.assertEqual(sorted(self.prepared.node_map.values()), list(range(len(self.prepared.node_map))))

    def test_18_reverse_node_roundtrip(self):
        for logical, numeric in self.prepared.node_map.items():
            self.assertEqual(self.prepared.reverse_node_map[numeric], logical)

    def test_19_link_mapping_has_two_arcs(self):
        self.assertTrue(all(len(arcs) == 2 for arcs in self.prepared.link_map.values()))

    def test_20_bidirectional_arcs_are_reverse(self):
        for arcs in self.prepared.link_map.values():
            self.assertEqual((arcs[0]["source"], arcs[0]["destination"]),
                             (arcs[1]["destination"], arcs[1]["source"]))

    def test_21_disabled_link_removes_both_rows(self):
        disabled = prepare_inputs(self.scenario_path, self.root / "disabled", ("l_s0_a",))
        with disabled.topology_csv.open() as handle:
            rows = list(csv.DictReader(handle))
        arcs = {str((item["source"], item["destination"])) for item in disabled.link_map["l_s0_a"]}
        self.assertFalse(arcs & {row["link"] for row in rows})

    def test_22_disabled_link_states_recorded(self):
        disabled = prepare_inputs(self.scenario_path, self.root / "disabled2", ("l_s0_a",))
        self.assertEqual({item["state"] for item in disabled.link_map["l_s0_a"]}, {"disabled"})

    def test_23_access_links_preserved(self):
        self.assertIn("l_src1_s0", self.prepared.link_map)
        self.assertIn("l_d_dst", self.prepared.link_map)

    def test_24_unknown_disabled_link_fails(self):
        with self.assertRaises(AdapterError):
            prepare_inputs(self.scenario_path, self.root / "unknown", ("does_not_exist",))

    def test_25_rate_1g(self): self.assertEqual(tsnkit_rate_code(1_000_000_000), 1)
    def test_26_rate_100m(self): self.assertEqual(tsnkit_rate_code(100_000_000), 10)
    def test_27_rate_10m(self): self.assertEqual(tsnkit_rate_code(10_000_000), 100)
    def test_28_rate_1m(self): self.assertEqual(tsnkit_rate_code(1_000_000), 1000)

    def test_29_unsupported_rate_fails(self):
        with self.assertRaises(AdapterError): tsnkit_rate_code(2_500_000_000)

    def test_30_frame_overhead_added_once(self):
        audit = self.prepared.manifest["serialization_audit"][0]
        self.assertEqual(audit["on_wire_bytes"], audit["payload_bytes"] + audit["frame_overhead_bytes"])

    def test_31_1g_equivalent_size(self): self.assertEqual(exact_equivalent_size_bytes(264, 1_000_000_000), 264)
    def test_32_100m_equivalent_size(self): self.assertEqual(exact_equivalent_size_bytes(264, 100_000_000), 2640)

    def test_33_nonintegral_equivalent_size_fails(self):
        with self.assertRaises(AdapterError): exact_equivalent_size_bytes(1, 3_000_000_000)

    def test_34_serialization_roundtrip_exact(self):
        self.assertTrue(all(row["exact_match"] for row in self.prepared.manifest["serialization_audit"]))

    def test_35_task_uses_schedule_deadline(self):
        with self.prepared.task_csv.open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(int(rows[0]["deadline"]), 700_000)

    def test_36_release_is_manifested(self):
        releases = {row["flow_id"]: row["release_offset_ns"] for row in self.prepared.manifest["serialization_audit"]}
        self.assertEqual(releases, {"TT1": 100_000, "TT2": 300_000})

    def test_37_hyperperiod_equals_cycle(self):
        self.assertEqual(self.prepared.manifest["hyperperiod_ns"], self.prepared.manifest["project_cycle_ns"])

    def test_38_time_unit_is_one_ns(self): self.assertEqual(TSNKIT_INTERNAL_TIME_UNIT_NS, 1)

    def test_39_float_artifact_rounds_exactly(self):
        self.assertEqual(seconds_to_ns(0.0006999999999999999, "deadline"), 700_000)

    def test_40_sub_ns_value_fails(self):
        with self.assertRaises(AdapterError): seconds_to_ns("0.0000000005", "half_ns")


class TestGclAndProfile(AdapterFixture):
    def test_41_intervals_sorted(self):
        self.assertEqual(merge_intervals([(20, 30), (0, 10)], 100), [(0, 10), (20, 30)])

    def test_42_adjacent_intervals_merge(self):
        self.assertEqual(merge_intervals([(0, 10), (10, 20)], 100), [(0, 20)])

    def test_43_overlap_fails(self):
        with self.assertRaises(AdapterError): merge_intervals([(0, 11), (10, 20)], 100)

    def test_44_zero_window_fails(self):
        with self.assertRaises(AdapterError): merge_intervals([(1, 1)], 100)

    def test_45_negative_window_fails(self):
        with self.assertRaises(AdapterError): merge_intervals([(-1, 1)], 100)

    def test_46_cycle_overrun_fails(self):
        with self.assertRaises(AdapterError): merge_intervals([(90, 101)], 100)

    def test_47_disjoint_windows_supported(self):
        windows = [{"egress_path": "s.eth[0]", "start_ns": 10, "end_ns": 20},
                   {"egress_path": "s.eth[0]", "start_ns": 40, "end_ns": 50}]
        schedules = compile_gate_schedules(windows, 100, 1, 0)
        self.assertEqual(len(schedules[0]["durations_s"]), 4)

    def test_48_tt_be_complement(self):
        windows = [{"egress_path": "s.eth[0]", "start_ns": 10, "end_ns": 20}]
        tt, be = compile_gate_schedules(windows, 100, 1, 0)
        self.assertTrue(tt["initially_open"])
        self.assertFalse(be["initially_open"])
        self.assertEqual(tt["durations_s"], be["durations_s"])

    def test_49_durations_cover_cycle(self):
        windows = [{"egress_path": "s.eth[0]", "start_ns": 10, "end_ns": 20}]
        tt = compile_gate_schedules(windows, 100, 1, 0)[0]
        self.assertAlmostEqual(sum(tt["durations_s"]), 100e-9)

    def test_50_wraparound_offset(self):
        windows = [{"egress_path": "s.eth[0]", "start_ns": 10, "end_ns": 20}]
        self.assertEqual(compile_gate_schedules(windows, 100, 1, 0)[0]["offset_s"], 90e-9)

    def test_51_semantic_hash_deterministic(self):
        value = {"b": 2, "a": 1}
        self.assertEqual(sha256_value(value), sha256_value({"a": 1, "b": 2}))

    def test_52_canonical_json_has_newline(self): self.assertTrue(canonical_json_bytes({"a": 1}).endswith(b"\n"))

    def test_53_no_plot_artifacts_generated(self):
        suffixes = {path.suffix.lower() for path in self.input_dir.rglob("*") if path.is_file()}
        self.assertFalse(suffixes & {".png", ".svg", ".pdf", ".jpg", ".html"})

    def test_54_input_generation_deterministic(self):
        second = prepare_inputs(self.scenario_path, self.root / "inputs2")
        self.assertEqual(self.prepared.topology_csv.read_bytes(), second.topology_csv.read_bytes())
        self.assertEqual(self.prepared.task_csv.read_bytes(), second.task_csv.read_bytes())

    def test_55_manifest_hashes_inputs(self):
        self.assertEqual(len(self.prepared.manifest["topology_sha256"]), 64)
        self.assertEqual(len(self.prepared.manifest["task_sha256"]), 64)


class TestBackendContract(AdapterFixture):
    def test_56_pinned_version(self): self.assertEqual(TSNKIT_VERSION, "0.3.0")
    def test_57_pinned_commit(self): self.assertEqual(len(TSNKIT_COMMIT), 40)
    def test_58_license_audited(self): self.assertEqual(TSNKIT_LICENSE, "GPL-3.0")

    def test_59_request_default_timeout(self):
        request = RecoverySynthesisRequest(self.scenario_path)
        self.assertEqual(request.solver_timeout_s, 30)

    def test_60_request_default_scope(self):
        request = RecoverySynthesisRequest(self.scenario_path)
        self.assertEqual(request.route_scope, "affected-only")

    def test_61_request_default_forwarding(self):
        request = RecoverySynthesisRequest(self.scenario_path)
        self.assertEqual(request.forwarding_model, "stream-aware")

    def test_62_legacy_descriptor_does_not_reimplement_cpp(self):
        result = LegacyBfsZ3Backend().synthesize(RecoverySynthesisRequest(self.scenario_path))
        self.assertEqual(result.status, BackendStatus.UNSUPPORTED)

    def test_63_backend_statuses_distinguish_timeout(self):
        self.assertNotEqual(BackendStatus.TIME_LIMIT_NO_INCUMBENT, BackendStatus.INFEASIBLE)

    def test_64_backend_statuses_distinguish_incumbent(self):
        self.assertNotEqual(BackendStatus.TIME_LIMIT_WITH_INCUMBENT, BackendStatus.TIME_LIMIT_NO_INCUMBENT)

    def test_65_raw_inputs_present(self):
        for name in ("task.csv", "topology.csv", "node_map.json", "link_map.json", "case_manifest.json"):
            self.assertTrue((self.input_dir / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
