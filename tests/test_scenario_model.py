import tempfile
import unittest
from pathlib import Path

from tools.scenario_compiler import build_port_map, compile_scenario, render_ini, render_ned
from tools.scenario_model import ScenarioValidationError, load_scenario
from tools.simple_yaml import loads
from tools.units import UnitError, parse_bitrate, parse_bytes, parse_time


ROOT = Path(__file__).resolve().parents[1]


class UnitTests(unittest.TestCase):
    def test_supported_units(self):
        self.assertEqual(parse_time("1ms"), 0.001)
        self.assertEqual(parse_bytes("2KB"), 2000)
        self.assertEqual(parse_bitrate("1Gbps"), 1_000_000_000)

    def test_rejects_ambiguous_units(self):
        with self.assertRaises(UnitError):
            parse_time("1")


class YamlTests(unittest.TestCase):
    def test_sequence_mapping_continuation(self):
        self.assertEqual(loads("items:\n  - id: a\n    endpoints: [x, y]\n"), {"items": [{"id": "a", "endpoints": ["x", "y"]}]})

    def test_inline_mapping_with_nested_array(self):
        self.assertEqual(loads("items:\n  - {id: a, endpoints: [x, y]}\n"), {"items": [{"id": "a", "endpoints": ["x", "y"]}]})


class ScenarioTests(unittest.TestCase):
    def rejected_replacement(self, old, new, message):
        text = (ROOT / "configs/scenarios/diamond.yaml").read_text().replace(old, new, 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"; path.write_text(text)
            with self.assertRaisesRegex(ScenarioValidationError, message): load_scenario(path)

    def test_reference_scenarios(self):
        diamond = load_scenario(ROOT / "configs/scenarios/diamond.yaml")
        mesh = load_scenario(ROOT / "configs/scenarios/mesh10.yaml")
        self.assertEqual((len(diamond.nodes), len(diamond.links), len(diamond.tt_flows)), (6, 6, 1))
        self.assertEqual((len(mesh.nodes), len(mesh.links), len(mesh.tt_flows)), (16, 22, 10))

    def test_structured20_scale_and_workload(self):
        model = load_scenario(ROOT / "configs/scenarios/structured20_auto.yaml")
        self.assertEqual((sum(n.type == "switch" for n in model.nodes), sum(n.type == "end_system" for n in model.nodes)), (20, 10))
        self.assertEqual((len(model.links), len(model.tt_flows), len(model.be_flows)), (45, 20, 4))
        self.assertGreater(len({flow.source for flow in model.tt_flows}), 5)
        self.assertGreater(len({flow.destination for flow in model.tt_flows}), 5)

    def test_deadline_budget_is_explicit(self):
        model = load_scenario(ROOT / "configs/scenarios/diamond.yaml")
        self.assertAlmostEqual(model.tt_flows[0].schedule_deadline_budget_s,
                               model.tt_flows[0].deadline_e2e_s - model.scheduling.endpoint_budget_s)

    def test_missing_endpoint_rejected(self):
        text = (ROOT / "configs/scenarios/diamond.yaml").read_text().replace("destination: destination", "destination: absent", 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(text)
            with self.assertRaisesRegex(ScenarioValidationError, "destination does not exist"):
                load_scenario(path)

    def test_duplicate_node_rejected(self):
        self.rejected_replacement("- id: s2", "- id: s1", "duplicate node id")

    def test_invalid_flow_source_rejected(self):
        self.rejected_replacement("source: source", "source: absent", "source does not exist")

    def test_invalid_fault_rejected(self):
        self.rejected_replacement("- l_s1_s2", "- absent_link", "fault candidate link does not exist")

    def test_unsupported_period_rejected(self):
        self.rejected_replacement("period: 1ms", "period: 2ms", "unsupported by scheduler v1")

    def test_canonical_json_deterministic(self):
        first = load_scenario(ROOT / "configs/scenarios/diamond.yaml")
        second = load_scenario(ROOT / "configs/scenarios/diamond.yaml")
        self.assertEqual(first.canonical_json(), second.canonical_json())

    def test_deterministic_compilation(self):
        model = load_scenario(ROOT / "configs/scenarios/diamond.yaml")
        self.assertEqual(render_ned(model), render_ned(model))
        self.assertEqual(render_ini(model), render_ini(model))
        port_map = build_port_map(model)
        self.assertEqual(port_map["links"]["l_s1_s2"]["a"]["interface"], "eth0")
        self.assertEqual(port_map, build_port_map(model))
        ini = render_ini(model)
        self.assertIn("destPort = 11001", ini)
        self.assertIn("destPort = 11000", ini)
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            a = compile_scenario(ROOT / "configs/scenarios/diamond.yaml", left)
            b = compile_scenario(ROOT / "configs/scenarios/diamond.yaml", right)
            for name in ("scenario.json", "port_map.json", "ScenarioNetwork.ned", "base.ini", "omnetpp.ini"):
                self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
