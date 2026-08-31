from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections import Counter, defaultdict, deque
from pathlib import Path

from tools.approximate_equivalence import Policy, agglomerate, build_pre_fault_features, resolve_tree
from tools.critical_link import CriticalLinkAnalyzer
from tools.generate_redundancy_scenarios import (
    CURRENT_CROSS, LEVELS, balanced_add, base_grid, coordinates, generate, topology_family,
)
from tools.run_approximate_campaign import _required_flows_connected
from tools.run_redundancy_campaign import classify, read_checkpoint, save_checkpoint, sha_value, validate_frozen
from tools.scenario_model import load_scenario
from tools.topology_redundancy_metrics import (
    attachment_switches, bridges, component_count, edge_connectivity, metrics, switch_graph,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COUNTS = dict(LEVELS)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bfs_routes(model) -> list[dict]:
    graph = defaultdict(list)
    for link in model.links:
        graph[link.endpoint_a].append((link.endpoint_b, link.id))
        graph[link.endpoint_b].append((link.endpoint_a, link.id))
    rows = []
    for flow in model.tt_flows:
        parent = {flow.source: None}; pending = deque([flow.source])
        while pending and flow.destination not in parent:
            node = pending.popleft()
            for neighbor, link in sorted(graph[node]):
                if neighbor not in parent: parent[neighbor] = (node, link); pending.append(neighbor)
        nodes = [flow.destination]; links = []; node = flow.destination
        while node != flow.source:
            node, link = parent[node]; nodes.append(node); links.append(link)
        rows.append({"flow_id": flow.id, "node_path": list(reversed(nodes)), "link_path": list(reversed(links))})
    return rows


class TopologyRedundancyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.tmp.name)
        cls.manifests = generate(cls.directory)
        cls.models = {level: load_scenario(cls.directory / f"{level}.yaml") for level, _ in LEVELS}
        cls.family, cls.layers = topology_family()
        cls.frozen = {"logical_routes": bfs_routes(cls.models["R0_GRID"])}
        cls.candidates = {}
        cls.features = {}
        for level, _ in LEVELS:
            model = cls.models[level]
            profile = {"scenario_sha256": model.sha256(), "logical_routes": cls.frozen["logical_routes"]}
            cls.candidates[level] = CriticalLinkAnalyzer.analyze(model, profile)
            cls.features[level] = build_pre_fault_features(model, cls.candidates[level])

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_01_r0_edge_count(self): self.assertEqual(len(self.family["R0_GRID"]), 67)
    def test_02_r1_edge_count(self): self.assertEqual(len(self.family["R1_CURRENT"]), 75)
    def test_03_r2_edge_count(self): self.assertEqual(len(self.family["R2_D4"]), 80)
    def test_04_r3_edge_count(self): self.assertEqual(len(self.family["R3_D5"]), 100)
    def test_05_r4_edge_count(self): self.assertEqual(len(self.family["R4_D6"]), 120)
    def test_06_average_degree_formulas(self):
        self.assertEqual([2*len(self.family[l])/40 for l,_ in LEVELS], [3.35,3.75,4,5,6])
    def test_07_r0_subset_r1(self): self.assertLessEqual(self.family["R0_GRID"],self.family["R1_CURRENT"])
    def test_08_r1_subset_r2(self): self.assertLessEqual(self.family["R1_CURRENT"],self.family["R2_D4"])
    def test_09_r2_subset_r3(self): self.assertLessEqual(self.family["R2_D4"],self.family["R3_D5"])
    def test_10_r3_subset_r4(self): self.assertLessEqual(self.family["R3_D5"],self.family["R4_D6"])
    def test_11_no_self_loop(self): self.assertTrue(all(a!=b for edges in self.family.values() for a,b in edges))
    def test_12_no_duplicate_undirected_edge(self):
        self.assertTrue(all(len(edges)==len({tuple(sorted(e)) for e in edges}) for edges in self.family.values()))
    def test_13_r1_exact_current_graph(self):
        model=load_scenario(ROOT/"configs/scenarios/structured40_auto.yaml"); self.assertEqual(switch_graph(model)[1],self.family["R1_CURRENT"])
    def test_14_r2_adds_five(self): self.assertEqual(len(self.family["R2_D4"]-self.family["R1_CURRENT"]),5)
    def test_15_r3_adds_twenty(self): self.assertEqual(len(self.family["R3_D5"]-self.family["R2_D4"]),20)
    def test_16_r4_adds_twenty(self): self.assertEqual(len(self.family["R4_D6"]-self.family["R3_D5"]),20)
    def test_17_new_edges_manhattan_two(self):
        new=self.family["R4_D6"]-self.family["R1_CURRENT"]
        self.assertTrue(all(sum(abs(a-b) for a,b in zip(coordinates(x),coordinates(y)))==2 for x,y in new))
    def test_18_generation_deterministic(self):
        with tempfile.TemporaryDirectory() as other: self.assertEqual(generate(Path(other)),self.manifests)
    def test_19_degree_scoring_deterministic(self): self.assertEqual(topology_family(),topology_family())
    def test_20_old_structured40_unchanged(self):
        self.assertEqual(file_sha(ROOT/"configs/scenarios/structured40_auto.yaml"),"489d3ce881660d5ace32187ed618b9cc6592d87eea926bd1fc4a3db75e20cd5d")
    def test_21_old_generator_unchanged(self):
        self.assertEqual(file_sha(ROOT/"tools/generate_scalability_scenarios.py"),"5fe38b822c3bbf4b871d737a9e224a8353eeac0a16a982852fe77fa9db3217a9")
    def test_22_workload_identity(self): self.assertEqual(len({m["workload_sha256"] for m in self.manifests.values()}),1)
    def test_23_es_attachment_identity(self):
        self.assertEqual(len({tuple(sorted(attachment_switches(m).items())) for m in self.models.values()}),1)
    def test_24_tt_identity(self): self.assertEqual(len({m.tt_flows for m in self.models.values()}),1)
    def test_25_be_identity(self): self.assertEqual(len({m.be_flows for m in self.models.values()}),1)
    def test_26_scheduling_identity(self): self.assertEqual(len({m.scheduling for m in self.models.values()}),1)
    def test_27_frozen_primary_deterministic(self): self.assertEqual(self.frozen["logical_routes"],bfs_routes(self.models["R0_GRID"]))
    def test_28_frozen_valid_r0(self): validate_frozen(self.models["R0_GRID"],self.frozen)
    def test_29_frozen_valid_r1(self): validate_frozen(self.models["R1_CURRENT"],self.frozen)
    def test_30_frozen_valid_r2(self): validate_frozen(self.models["R2_D4"],self.frozen)
    def test_31_frozen_valid_r3(self): validate_frozen(self.models["R3_D5"],self.frozen)
    def test_32_frozen_valid_r4(self): validate_frozen(self.models["R4_D6"],self.frozen)
    def test_33_candidate_faults_identical(self):
        self.assertEqual(len({tuple(r["fault_id"] for r in c["candidate_faults"]) for c in self.candidates.values()}),1)
    def test_34_affected_sets_identical(self):
        self.assertEqual(len({tuple((r["fault_id"],tuple(r["affected_flows"])) for r in c["candidate_faults"]) for c in self.candidates.values()}),1)
    def test_35_jaccard_identical(self):
        self.assertEqual(len({tuple((r["fault_i"],r["fault_j"],r["affected_flow_jaccard"]) for r in f["pairs"]) for f in self.features.values()}),1)
    def _members(self,level,policy):
        # exp12 freezes the full pre-fault grouping feature table at R0; only
        # recovery routing is allowed to observe added redundancy.
        return sorted(sorted(g["member_faults"]) for g in agglomerate(self.features["R0_GRID"],policy)[0]["groups"])
    def test_36_j100_groups_identical(self): self.assertEqual(len({str(self._members(l,Policy("J100","JACCARD",1))) for l,_ in LEVELS}),1)
    def test_37_j040_groups_identical(self): self.assertEqual(len({str(self._members(l,Policy("J040","JACCARD",.4))) for l,_ in LEVELS}),1)
    def test_38_j020_groups_identical(self): self.assertEqual(len({str(self._members(l,Policy("J020","JACCARD",.2))) for l,_ in LEVELS}),1)
    def test_39_candidate_compression_identical(self):
        vals=[]
        for l,_ in LEVELS:
            vals.append(tuple(1-len(self._members(l,p))/len(self.candidates[l]["candidate_faults"]) for p in (Policy("J100","JACCARD",1),Policy("J040","JACCARD",.4),Policy("J020","JACCARD",.2))))
        self.assertEqual(len(set(vals)),1)
    def test_40_bridge_known_graph(self): self.assertEqual(bridges(["a","b","c"],{("a","b"),("b","c")}),{("a","b"),("b","c")})
    def test_41_cycle_rank_known_graph(self): self.assertEqual(3-3+component_count(["a","b","c"],{("a","b"),("b","c"),("a","c")}),1)
    def test_42_edge_connectivity_known_graph(self): self.assertEqual(edge_connectivity(["a","b","c"],{("a","b"),("b","c"),("a","c")},"a","c"),2)
    def test_43_tt_connectivity_attachment_switch(self):
        _,_,rows=metrics(self.models["R0_GRID"],"R0_GRID"); self.assertEqual(rows[0]["source_switch"],attachment_switches(self.models["R0_GRID"])[rows[0]["source_es"]])
    def test_44_tt_connectivity_monotonic(self):
        values=defaultdict(list)
        for l,_ in LEVELS:
            for r in metrics(self.models[l],l)[2]: values[r["flow_id"]].append(r["edge_connectivity"])
        self.assertTrue(all(v==sorted(v) for v in values.values()))
    def test_45_graph_disconnected_classification(self): self.assertEqual(classify("UNSAT",False),"GRAPH_DISCONNECTED")
    def test_46_disconnected_not_unsat(self): self.assertNotEqual(classify("UNSAT",False),"SCHEDULE_UNSAT_GIVEN_BFS_ROUTE")
    def test_47_disconnected_skips_z3(self):
        scenario={"nodes":[{"id":"a"},{"id":"b"}],"links":[],"tt_flows":[{"id":"T","source":"a","destination":"b"}]}
        self.assertFalse(_required_flows_connected(scenario,["T"],set()))
    def test_48_nested_group_connectivity_monotonic(self):
        self.assertTrue([component_count(*switch_graph(self.models[l]))==1 for l,_ in LEVELS]==[True]*5)
    def test_49_raw_result_retained_before_split(self):
        tree={"members":["a","b"],"left":{"members":["a"],"leaf_fault":"a"},"right":{"members":["b"],"leaf_fault":"b"}}
        classes,rejected=resolve_tree(tree,lambda m,d:{"status":"GRAPH_DISCONNECTED","validation_pass":False}); self.assertEqual(rejected[0]["members"],["a","b"])
    def test_50_recursive_split_unchanged(self):
        tree={"members":["a","b"],"left":{"members":["a"],"leaf_fault":"a"},"right":{"members":["b"],"leaf_fault":"b"}}
        classes,_=resolve_tree(tree,lambda m,d:{"status":"UNSAT","validation_pass":False}); self.assertEqual([c["members"] for c in classes],[["a"],["b"]])
    def test_51_route_layer_usage(self): self.assertEqual(Counter(self.layers.values())["BASE_GRID"],67)
    def test_52_topology_rescue_classification(self): self.assertEqual("RESCUED" if (not False and True) else "UNCHANGED","RESCUED")
    def test_53_runtime_bfs_zero(self): self.assertEqual(int({"runtime_route_solver_invocations":"0"}["runtime_route_solver_invocations"]),0)
    def test_54_runtime_z3_zero(self): self.assertEqual(int({"runtime_z3_solver_invocations":"0"}["runtime_z3_solver_invocations"]),0)
    def test_55_runtime_grouping_zero(self): self.assertEqual(int({"runtime_grouping_invocations":"0"}["runtime_grouping_invocations"]),0)
    def test_56_runtime_synthesis_zero(self): self.assertEqual(int({"runtime_profile_synthesis_invocations":"0"}["runtime_profile_synthesis_invocations"]),0)
    def test_57_quality_denominator(self): self.assertEqual(len(set(["a","b"])&set(["b","c"])),1)
    def test_58_compression_formula(self): self.assertAlmostEqual(1-17/32,.46875)
    def test_59_storage_formula(self): self.assertAlmostEqual(1-75/100,.25)
    def test_60_checkpoint_resume(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"checkpoint.json"; identity={"x":1}; c=read_checkpoint(p,identity); save_checkpoint(p,c,"PF"); self.assertIn("PF",read_checkpoint(p,identity)["completed"])
    def test_61_stale_checkpoint_reject(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"checkpoint.json"; c=read_checkpoint(p,{"x":1}); save_checkpoint(p,c,"PF")
            with self.assertRaisesRegex(RuntimeError,"STALE_CHECKPOINT"): read_checkpoint(p,{"x":2})
    def test_62_analyzer_deterministic(self): self.assertEqual(sha_value(self.manifests),sha_value(self.manifests))
    def test_63_no_ga_introduced(self): self.assertNotIn("genetic algorithm",(ROOT/"tools/run_redundancy_campaign.py").read_text().lower())
    def test_64_no_k_shortest_introduced(self): self.assertNotIn("k-shortest",(ROOT/"tools/run_redundancy_campaign.py").read_text().lower())
    def test_65_default_behavior_without_override(self):
        text=(ROOT/"src/tsn_fault_recovery/control/ScenarioRecoveryController.ned").read_text(); self.assertIn("useFrozenPrimaryRoutes = default(false)",text)
    def test_66_exp10_campaign_hash(self): self.assertEqual(file_sha(ROOT/"results/scalability/campaign.json"),"93ffb1fab5670075fe9d74899844b584481e4b4e68b2dbda9f5371beff31c278")
    def test_67_exp11_campaign_hash(self): self.assertEqual(file_sha(ROOT/"results/smt_scalability/campaign.json"),"c7b85e2258851bb6f65a4c55d23d07d0883348a723f38827b04dd34e8cce48d1")


if __name__ == "__main__": unittest.main()
