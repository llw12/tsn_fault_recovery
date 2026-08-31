from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.run_jrs_wa_qualification import EXP12_CAMPAIGN_SHA256, affected_flows, select_cases
from tools.scenario_compiler import compile_scenario
from tools.scenario_model import load_scenario


ROOT = Path(__file__).resolve().parents[1]


class StreamAwareForwardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = select_cases(ROOT)

    def test_historical_scenario_default_is_legacy(self):
        self.assertEqual(load_scenario(ROOT / "configs/scenarios/diamond.yaml").forwarding_model, "destination-mac")

    def test_micro_explicitly_uses_stream_aware(self):
        self.assertEqual(load_scenario(ROOT / "configs/scenarios/stream_aware_micro.yaml").forwarding_model, "stream-aware")

    def test_override_does_not_modify_source_model(self):
        source = ROOT / "configs/scenarios/diamond.yaml"
        before = source.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            output = compile_scenario(source, directory, forwarding_model_override="stream-aware", scenario_name_override="override")
            scenario = json.loads((output / "scenario.json").read_text())
            self.assertEqual(scenario["forwarding_model"], "stream-aware")
        self.assertEqual(source.read_bytes(), before)

    def test_stream_handle_map_is_emitted_only_for_stream_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = compile_scenario(ROOT / "configs/scenarios/diamond.yaml", directory,
                                      forwarding_model_override="stream-aware", scenario_name_override="stream")
            legacy = compile_scenario(ROOT / "configs/scenarios/diamond.yaml", directory,
                                      scenario_name_override="legacy")
            self.assertTrue((stream / "stream_handle_map.json").exists())
            self.assertFalse((legacy / "stream_handle_map.json").exists())

    def test_micro_mapping_is_lexical_and_one_based(self):
        mapping = json.loads((ROOT / "generated/stream_aware_micro/stream_handle_map.json").read_text())
        self.assertEqual(mapping["flow_to_stream_handle"], {"TT1": 1, "TT2": 2})

    def test_compiled_ini_has_native_vlan_encoder(self):
        ini = (ROOT / "generated/stream_aware_micro/base.ini").read_text()
        self.assertIn("bridging.streamCoder.encoder.mapping", ini)
        self.assertIn("vlan: 1", ini)
        self.assertIn("vlan: 2", ini)

    def test_compiled_ini_keeps_be_on_vid_zero(self):
        ini = (ROOT / "generated/stream_aware_micro/base.ini").read_text()
        self.assertIn("BE", ini)
        self.assertNotIn('stream = "BE"', ini)

    def test_exactly_ten_qualification_cases_selected(self):
        self.assertEqual([case.case_id for case in self.cases], [f"Q{i:02d}" for i in range(10)])

    def test_forwarding_conflict_selection_is_lexical(self):
        self.assertEqual([(c.level, c.disabled_links) for c in self.cases[6:8]],
                         [("R2_D4", ("l_sw09_sw10",)), ("R2_D4", ("l_sw10_sw11",))])

    def test_shared_conflict_is_selected_from_raw_artifact(self):
        self.assertEqual(self.cases[8].disabled_links, ("l_sw07_sw15", "l_sw15_sw23"))

    def test_rescued_group_is_selected_from_transition_artifact(self):
        self.assertEqual(self.cases[9].level, "R1_CURRENT")
        self.assertEqual(self.cases[9].disabled_links, ("l_sw01_sw02", "l_sw01_sw09", "l_sw02_sw03"))

    def test_affected_flow_union_is_deterministic(self):
        routes = {"B": {"link_path": ["x"]}, "A": {"link_path": ["y", "x"]}, "C": {"link_path": ["z"]}}
        self.assertEqual(affected_flows(routes, ("x",)), ("A", "B"))

    def test_exp12_campaign_hash_is_unchanged(self):
        digest = hashlib.sha256((ROOT / "results/topology_redundancy/campaign.json").read_bytes()).hexdigest()
        self.assertEqual(digest, EXP12_CAMPAIGN_SHA256)

    def test_no_plot_code_in_exp13_tools(self):
        text = (ROOT / "tools/analyze_jrs_wa_qualification.py").read_text()
        self.assertNotIn("matplotlib", text)
        self.assertNotIn("plotly", text)


if __name__ == "__main__":
    unittest.main()
