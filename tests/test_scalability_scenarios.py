import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.generate_scalability_scenarios import yaml_text
from tools.scenario_model import load_scenario


class ScalabilityScenarioTests(unittest.TestCase):
    def model(self, switches, rows, cols, es, tt, be):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.yaml"
            path.write_text(yaml_text(switches=switches, rows=rows, cols=cols,
                                      end_systems=es, tt_flows=tt, be_flows=be,
                                      seed=0, name=f"structured{switches}_auto"))
            return load_scenario(path)

    def test_generator_is_byte_deterministic(self):
        args = dict(switches=30, rows=5, cols=6, end_systems=15, tt_flows=30,
                    be_flows=6, seed=0, name="structured30_auto")
        first, second = yaml_text(**args), yaml_text(**args)
        self.assertEqual(first, second)
        self.assertNotEqual(hashlib.sha256(first.encode()).hexdigest(), hashlib.sha256(
            yaml_text(**{**args, "switches": 40, "cols": 8, "end_systems": 20,
                       "tt_flows": 40, "be_flows": 8, "name": "structured40_auto"}).encode()).hexdigest())

    def test_formal_sizes_and_switch_only_scope(self):
        for values in ((30,5,6,15,30,6), (40,5,8,20,40,8), (50,5,10,25,50,10)):
            switches, rows, cols, ends, tt, be = values; model = self.model(*values)
            self.assertEqual(sum(node.type == "switch" for node in model.nodes), switches)
            self.assertEqual(sum(node.type == "end_system" for node in model.nodes), ends)
            self.assertEqual(len(model.tt_flows), tt); self.assertEqual(len(model.be_flows), be)
            self.assertEqual(model.candidate_selection.scope, "switch-switch")
            self.assertTrue(all(flow.source != flow.destination for flow in model.tt_flows))
            self.assertLess(max(flow.release_offset_s for flow in model.tt_flows), .0004)
            self.assertEqual(len({link.id for link in model.links}), len(model.links))

    def test_topology_is_connected(self):
        model = self.model(50, 5, 10, 25, 50, 10)
        graph = {node.id: set() for node in model.nodes}
        for link in model.links:
            graph[link.endpoint_a].add(link.endpoint_b); graph[link.endpoint_b].add(link.endpoint_a)
        seen, stack = set(), [next(iter(graph))]
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node); stack.extend(graph[node] - seen)
        self.assertEqual(seen, set(graph))

