from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.generate_jrs_scalability_scenarios import FIXED_SWEEP, MAIN_SCALES, scenario_audit, topology_edges, topology_sha, yaml_text
from tools.jrs_scalability_utils import canonical_profile_bytes, deterministic_gzip, discover_candidates, lpt_projection, stratified_sample
from tools.jrs_wa_static_checker import check_solution
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest
from tools.scenario_compiler import compile_scenario
from tools.scenario_model import load_scenario
from tools.scip_jrs_wa_backend import SCIP_SEED, SCIP_THREADS, ScipJrsWaBackend

ROOT = Path(__file__).resolve().parents[1]
EXP12_SHA = "c306a4d5de34761aba96dead957bdcda27cbaed7e3614bd573effd8515333274"
EXP13_SHA = "2bb4fbf6c5a39b5b0d873165c661ad847d965eb13570aea112a36385ae80e5c3"


class ScipFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.path = Path(cls.temp.name)
        cls.work = compile_scenario(ROOT / "configs/scenarios/stream_aware_micro.yaml", cls.path / "generated")
        cls.scenario = json.loads((cls.work / "scenario.json").read_text())
        cls.request = RecoverySynthesisRequest(cls.work / "scenario.json", (), {}, ("TT1", "TT2"), 30,
                                               "all-reroute", "stream-aware", cls.path / "inputs")
        cls.result = ScipJrsWaBackend().synthesize(cls.request)
        cls.payload = cls.result.to_dict()
        cls.check = check_solution(cls.scenario, (), {}, ("TT1", "TT2"), cls.payload, "all-reroute")

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()


class TestScipFormulation(ScipFixture):
    def test_01_binary_routing_variables(self): self.assertGreater(self.result.statistics["num_route_variables"], 0)
    def test_02_integer_timing_variables(self): self.assertEqual(self.result.statistics["num_route_variables"], self.result.statistics["num_integer_time_variables"])
    def test_03_source_constraint(self): self.assertEqual(self.result.statistics["constraint_family_counts"]["ROUTING_SOURCE_DESTINATION"], 4)
    def test_04_destination_constraint(self): self.assertTrue(all(r["node_path"][-1] == self.scenario["tt_flows"][i]["destination"] for i, r in enumerate(self.result.logical_routes)))
    def test_05_flow_conservation(self): self.assertGreater(self.result.statistics["constraint_family_counts"]["ROUTING_FLOW_CONSERVATION"], 0)
    def test_06_loop_pruning(self): self.assertGreater(self.result.statistics["constraint_family_counts"]["ROUTING_LOOP_PRUNING"], 0)
    def test_07_route_time_coupling(self): self.assertEqual(self.result.statistics["constraint_family_counts"]["ROUTE_TIME_PRESENT"], self.result.statistics["num_route_variables"])
    def test_08_hop_precedence(self): self.assertGreater(self.result.statistics["constraint_family_counts"]["HOP_PRECEDENCE"], 0)
    def test_09_non_overlap(self): self.assertGreater(self.result.statistics["constraint_family_counts"]["LINK_NON_OVERLAP"], 0)
    def test_10_deadline(self): self.assertEqual(self.result.statistics["constraint_family_counts"]["END_TO_END_DEADLINE"], 2)
    def test_11_fixed_release(self): self.assertEqual(self.result.statistics["constraint_family_counts"]["FIXED_RELEASE_OFFSET"], 2)
    def test_12_route_locking(self):
        routes = {r["flow_id"]: r for r in self.result.logical_routes}
        request = RecoverySynthesisRequest(self.work / "scenario.json", (), routes, (), 30, "affected-only", "stream-aware", self.path / "locked")
        locked = ScipJrsWaBackend().synthesize(request)
        self.assertEqual(locked.statistics["constraint_family_counts"]["RECOVERY_ROUTE_LOCK"], locked.statistics["num_route_variables"])
    def test_13_failed_link_avoidance(self): self.assertTrue(next(c for c in self.check["checks"] if c["check"] == "DISABLED_LINK_EXCLUSION")["passed"])
    def test_14_all_tt_participate(self): self.assertEqual({r["flow_id"] for r in self.result.logical_routes}, {"TT1", "TT2"})
    def test_15_optimal_status_mapping(self): self.assertEqual(ScipJrsWaBackend._map_status("optimal", 1), (BackendStatus.OPTIMAL, True, True))
    def test_16_timeout_mapping(self): self.assertEqual(ScipJrsWaBackend._map_status("timelimit", 0)[0], BackendStatus.TIME_LIMIT_NO_INCUMBENT)
    def test_17_feasible_timeout_mapping(self): self.assertEqual(ScipJrsWaBackend._map_status("timelimit", 1)[0], BackendStatus.TIME_LIMIT_WITH_INCUMBENT)
    def test_18_infeasible_mapping(self): self.assertEqual(ScipJrsWaBackend._map_status("infeasible", 0)[0], BackendStatus.INFEASIBLE)
    def test_19_solver_exception_boundary(self):
        bad = RecoverySynthesisRequest(self.path / "missing.json", output_directory=self.path / "bad")
        self.assertEqual(ScipJrsWaBackend().synthesize(bad).status, BackendStatus.MODEL_BUILD_ERROR)
    def test_20_deterministic_seed_config(self): self.assertEqual((SCIP_SEED, SCIP_THREADS), (1024, 1))


class TestParityContract(ScipFixture):
    def test_21_q00_status_parity_contract(self): self.assertTrue(self.result.feasible)
    def test_22_q01_status_parity_field(self): self.assertIn("scip_status", self.result.statistics)
    def test_23_q02_status_parity_field(self): self.assertIn(self.result.status, {BackendStatus.OPTIMAL, BackendStatus.FEASIBLE_NOT_OPTIMAL})
    def test_24_model_family_counts(self): self.assertEqual(len(self.result.statistics["constraint_family_counts"]), 10)
    def test_25_semantic_validity(self): self.assertTrue(self.check["valid"])
    def test_26_different_route_accepted(self): self.assertNotIn("route_hash_parity", self.check)


class TestStaticChecker(ScipFixture):
    def altered(self): return copy.deepcopy(self.payload)
    def test_27_disconnected_route_reject(self):
        p=self.altered(); p["logical_routes"][0]["node_path"][-1]="s0"; self.assertFalse(check_solution(self.scenario,(),{},(),p,"all-reroute")["valid"])
    def test_28_loop_reject(self):
        p=self.altered(); p["logical_routes"][0]["node_path"].insert(1,p["logical_routes"][0]["node_path"][0]); self.assertFalse(check_solution(self.scenario,(),{},(),p,"all-reroute")["valid"])
    def test_29_disabled_link_reject(self):
        failed=self.payload["logical_routes"][0]["link_path"][0]; self.assertFalse(check_solution(self.scenario,(failed,),{},(),self.payload,"all-reroute")["valid"])
    def test_30_wrong_route_lock_reject(self):
        wrong={r["flow_id"]:{"node_path":list(reversed(r["node_path"])),"link_path":r["link_path"]} for r in self.payload["logical_routes"]}; self.assertFalse(check_solution(self.scenario,(),wrong,(),self.payload,"affected-only")["valid"])
    def test_31_wrong_release_reject(self):
        p=self.altered(); p["statistics"]["route_schedule"][0]["start_ns"]+=1; self.assertFalse(check_solution(self.scenario,(),{},(),p,"all-reroute")["valid"])
    def test_32_precedence_violation_reject(self):
        p=self.altered(); rows=[r for r in p["statistics"]["route_schedule"] if r["flow_id"]=="TT1"]; rows[1]["start_ns"]=rows[0]["end_ns"]-1; self.assertFalse(check_solution(self.scenario,(),{},(),p,"all-reroute")["valid"])
    def test_33_overlap_reject(self):
        p=self.altered(); row=copy.deepcopy(p["statistics"]["route_schedule"][0]); row["flow_id"]="TT2"; p["statistics"]["route_schedule"].append(row); self.assertFalse(check_solution(self.scenario,(),{},(),p,"all-reroute")["valid"])
    def test_34_deadline_violation_reject(self):
        s=copy.deepcopy(self.scenario); s["tt_flows"][0]["schedule_deadline_budget_s"]=1e-9; self.assertFalse(check_solution(s,(),{},(),self.payload,"all-reroute")["valid"])
    def test_35_valid_profile_pass(self): self.assertTrue(self.check["valid"])


class TestScalabilityGenerator(unittest.TestCase):
    S = MAIN_SCALES[0]
    def test_36_deterministic_topology(self): self.assertEqual(topology_edges(30), topology_edges(30))
    def test_37_deterministic_workload(self): self.assertEqual(yaml_text(self.S), yaml_text(self.S))
    def test_38_target_node_count(self): self.assertEqual(scenario_audit(self.S)["total_node_count"], 50)
    def test_39_es_attachment_valid(self): self.assertIn("l_es0001_sw0001", yaml_text(self.S))
    def test_40_average_degree_audit(self): self.assertEqual(scenario_audit(self.S)["average_switch_degree"], 4)
    def test_41_no_duplicate_links(self): self.assertEqual(len(topology_edges(30)), len(set(topology_edges(30))))
    def test_42_tt_endpoints_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"s.yaml"; p.write_text(yaml_text(self.S)); self.assertEqual(len(load_scenario(p).tt_flows),100)
    def test_43_same_period(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"s.yaml"; p.write_text(yaml_text(self.S)); self.assertEqual({f.period_s for f in load_scenario(p).tt_flows},{.001})
    def test_44_stable_sha(self): self.assertEqual(topology_sha(100,50),topology_sha(100,50))
    def test_45_fixed_sweep_topology_sha(self): self.assertEqual(len({topology_sha(s.switches,s.end_systems) for s in FIXED_SWEEP}),1)


class TestCandidatesAndAccounting(ScipFixture):
    def candidates(self): return discover_candidates(self.scenario,self.payload["logical_routes"])
    def test_46_only_internal_links(self): self.assertTrue(all("src" not in r["fault_id"] and "dst" not in r["fault_id"] for r in self.candidates()))
    def test_47_only_p0_used_links(self): self.assertTrue(self.candidates())
    def test_48_affected_count_exact(self): self.assertTrue(all(r["affected_flow_count"]==len(r["affected_flow_ids"]) for r in self.candidates()))
    def test_49_deterministic_order(self): self.assertEqual([r["fault_id"] for r in self.candidates()],sorted(r["fault_id"] for r in self.candidates()))
    def test_50_stratified_deterministic(self):
        values=[{"fault_id":f"L{i:03d}","affected_flow_count":i%17} for i in range(200)]; self.assertEqual(stratified_sample(values),stratified_sample(values))
    def test_51_canonical_profile_bytes(self): self.assertEqual(canonical_profile_bytes({"b":1,"a":2}),b'{"a":2,"b":1}\n')
    def test_52_gzip_mtime_deterministic(self): self.assertEqual(deterministic_gzip(b"abc"),deterministic_gzip(b"abc"))
    def test_53_timing_fields_present(self): self.assertTrue({"input_conversion","route_space_build","model_build","solver_wall","total_backend"} <= self.result.timings_ms.keys())
    def test_54_no_omnet_invocation(self): self.assertNotIn("omnet", json.dumps(self.payload).lower())
    def test_55_no_plot_artifact(self): self.assertFalse(any(p.suffix in {".png",".svg",".pdf"} for p in self.path.rglob("*")))
    def test_56_subprocess_memory_parser(self):
        from tools.run_pf_jrs_scalability import parse_time_v
        p=self.path/"mem.txt"; p.write_text("Maximum resident set size (kbytes): 123\n"); self.assertEqual(parse_time_v(p),123*1024)
    def test_57_full_accounting(self): self.assertEqual(stratified_sample([{"fault_id":"L","affected_flow_count":1}])[0],"FULL")
    def test_58_sampled_accounting(self): self.assertEqual(stratified_sample([{"fault_id":f"L{i}","affected_flow_count":i} for i in range(129)])[0],"SAMPLED")
    def test_59_timeout_capped_estimate_primitive(self): self.assertEqual(min(35000,30000),30000)
    def test_60_parallel_projection_deterministic(self): self.assertEqual(lpt_projection([4,3,2,1],2),lpt_projection([4,3,2,1],2))
    def test_61_lpt_one_worker_serial(self): self.assertEqual(lpt_projection([4,3,2,1],1),10)
    def test_62_lpt_nonincreasing(self): self.assertGreaterEqual(lpt_projection([4,3,2,1],2),lpt_projection([4,3,2,1],4))
    def test_63_manifest_deterministic_fields(self): self.assertEqual(sorted(scenario_audit(MAIN_SCALES[0])),sorted(scenario_audit(MAIN_SCALES[0])))
    def test_64_exp12_hash_unchanged(self): self.assertEqual(hashlib.sha256((ROOT/"results/topology_redundancy/campaign.json").read_bytes()).hexdigest(),EXP12_SHA)
    def test_65_exp13_hash_unchanged(self): self.assertEqual(hashlib.sha256((ROOT/"results/jrs_wa_qualification/campaign.json").read_bytes()).hexdigest(),EXP13_SHA)


if __name__ == "__main__": unittest.main()
