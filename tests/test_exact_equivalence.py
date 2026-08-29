import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tools.exact_equivalence import (
    ExactEquivalenceError, affected_set_hash, build_candidate_groups, build_class_store,
    class_store_metrics, finalize_class_store, synthesis_plan, validate_class_store,
    validate_shared_routes,
)
from tools.profile_store import (
    file_sha256, profile_content_hash, semantic_profile_hash, solver_config_hash, write_json,
)


def profile(profile_id, scenario_hash, affected, link="safe"):
    value = {
        "profile_schema_version": 2, "profile_id": profile_id, "strategy": "per-failure",
        "scenario_sha256": scenario_hash, "fault_id": profile_id.removeprefix("PF_"),
        "affected_flows": affected,
        "logical_routes": [
            {"flow_id": "TT1", "node_path": ["a", "b"], "link_path": [link]},
            {"flow_id": "TT2", "node_path": ["c", "d"], "link_path": ["stable"]},
        ],
        "routes": [{"flow_id": "TT1", "switch": "a", "destination": "b", "interface": "eth0", "logical_link": link}],
        "gate_schedules": [{"gate_path": "a.gate", "traffic_class": 1, "initially_open": True,
                            "offset_s": 0.0, "durations_s": [0.0005, 0.0005]}],
        "schedule_status": "SAT", "schedule_objective": 1,
    }
    value["semantic_profile_hash"] = semantic_profile_hash(value)
    value["profile_sha256"] = profile_content_hash(value)
    return value


class ExactEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {"policy": {"mode": "auto"}, "candidate_set_sha256": "candidate", "candidate_faults": [
            {"fault_id": "f1", "affected_flows": ["TT1"]},
            {"fault_id": "f2", "affected_flows": ["TT1"]},
            {"fault_id": "f3", "affected_flows": ["TT2"]},
        ]}
        self.pf = {"faults": {
            "f1": {"status": "SAT", "semantic_profile_hash": "h1"},
            "f2": {"status": "SAT", "semantic_profile_hash": "h2"},
            "f3": {"status": "SAT", "semantic_profile_hash": "h3"},
        }}

    def groups(self): return build_candidate_groups(self.candidate, self.pf)

    def test_01_grouping_deterministic(self): self.assertEqual(self.groups(), self.groups())
    def test_02_grouping_uses_exact_set(self): self.assertEqual(sorted(len(g["members"]) for g in self.groups()), [1, 2])
    def test_03_grouping_ignores_semantic_hash(self):
        changed = deepcopy(self.pf); changed["faults"]["f2"]["semantic_profile_hash"] = "h1"
        self.assertEqual([g["members"] for g in self.groups()], [g["members"] for g in build_candidate_groups(self.candidate, changed)])
    def test_04_status_does_not_select_candidate_group(self):
        changed = deepcopy(self.pf); changed["faults"]["f2"]["status"] = "UNSAT"
        self.assertEqual([g["members"] for g in self.groups()], [g["members"] for g in build_candidate_groups(self.candidate, changed)])
    def test_05_hash_order_independent(self): self.assertEqual(affected_set_hash(["B", "A"]), affected_set_hash(["A", "B"]))
    def test_06_group_ids_stable(self): self.assertEqual([g["candidate_group_id"] for g in self.groups()], ["G0001", "G0002"])
    def test_07_synthesis_plan_only_multi_sat(self): self.assertEqual(len(synthesis_plan(self.groups())), 1)
    def test_08_union_disabled_set_is_members(self):
        item = synthesis_plan(self.groups())[0]; self.assertEqual(item["disabled_links"], ["f1", "f2"])
    def test_09_non_sat_member_retained(self):
        changed = deepcopy(self.pf); changed["faults"]["f2"]["status"] = "NO_ROUTE"
        group = next(g for g in build_candidate_groups(self.candidate, changed) if len(g["members"]) == 2)
        self.assertEqual(group["members"], ["f1", "f2"]); self.assertEqual(group["sat_members"], ["f1"])
    def test_10_unaffected_route_preserved(self):
        p0 = profile("P0", "s", ["TT1"]); shared = deepcopy(p0); shared["logical_routes"][0]["link_path"] = ["detour"]
        validate_shared_routes(shared, p0, ["TT1"], ["failed"])
    def test_11_unaffected_reroute_rejected(self):
        p0 = profile("P0", "s", ["TT1"]); shared = deepcopy(p0); shared["logical_routes"][1]["link_path"] = ["changed"]
        with self.assertRaisesRegex(ExactEquivalenceError, "unaffected"): validate_shared_routes(shared, p0, ["TT1"], ["failed"])
    def test_12_class_link_use_rejected(self):
        p0 = profile("P0", "s", ["TT1"]); shared = deepcopy(p0); shared["logical_routes"][0]["link_path"] = ["failed"]
        with self.assertRaisesRegex(ExactEquivalenceError, "fault link"): validate_shared_routes(shared, p0, ["TT1"], ["failed"])
    def test_13_incomplete_route_set_rejected(self):
        p0 = profile("P0", "s", ["TT1"]); shared = deepcopy(p0); shared["logical_routes"].pop()
        with self.assertRaisesRegex(ExactEquivalenceError, "incomplete"): validate_shared_routes(shared, p0, ["TT1"], ["failed"])


class ExactStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.generated = Path(self.temp.name) / "x"
        (self.generated / "fault_analysis").mkdir(parents=True); (self.generated / "profiles/per_failure/profiles").mkdir(parents=True)
        self.scenario = {
            "scenario_name": "x", "scenario_sha256": "scenario", "fault_candidates": ["f1", "f2", "f3"],
            "simulation": {"cycle_time_s": .001, "time_quantum_s": .000001}, "scheduling": {},
            "tt_flows": [], "nodes": [], "links": [],
        }
        self.port_map = {"links": {}}
        write_json(self.generated / "scenario.json", self.scenario); write_json(self.generated / "port_map.json", self.port_map)
        candidate = {"policy": {"mode": "auto"}, "candidate_set_sha256": "candidate", "candidate_faults": [
            {"fault_id": "f1", "affected_flows": ["TT1"]}, {"fault_id": "f2", "affected_flows": ["TT1"]},
            {"fault_id": "f3", "affected_flows": ["TT2"]}]}
        write_json(self.generated / "fault_analysis/candidate_faults.json", candidate)
        p0 = profile("P0", "scenario", ["TT1"]); write_json(self.generated / "profiles/profile0.json", p0)
        faults = {}
        for fault in ("f1", "f2", "f3"):
            p = profile("PF_" + fault, "scenario", ["TT1"] if fault != "f3" else ["TT2"])
            path = self.generated / f"profiles/per_failure/profiles/{fault}.json"; write_json(path, p)
            faults[fault] = {"status": "SAT", "profile_id": p["profile_id"], "profile_file": f"profiles/{fault}.json",
                             "profile_sha256": p["profile_sha256"], "semantic_profile_hash": p["semantic_profile_hash"],
                             "profile_bytes": path.stat().st_size}
        pf = {"faults": faults, "recovery_precompute_wall_ms": 1.0}; write_json(self.generated / "profiles/per_failure/store.json", pf)
        from tools.exact_equivalence import build_candidate_groups
        groups = build_candidate_groups(candidate, pf); multi = next(g for g in groups if len(g["members"]) == 2)
        raw_dir = self.generated / "profiles/exact_equivalence/raw" / multi["candidate_group_id"]; raw_dir.mkdir(parents=True)
        raw = profile("raw", "scenario", ["TT1"], "detour")
        write_json(raw_dir / "profile.raw.json", {k: raw[k] for k in ("logical_routes", "routes", "gate_schedules")})
        self.report = {multi["candidate_group_id"]: {"status": "SAT", "objective": 1, "route_solver_wall_us": 1,
                       "smt_solver_wall_us": 2, "profile_compile_wall_us": 3,
                       "total_class_synthesis_wall_us": 7, "diagnostic": "sat"}}

    def tearDown(self): self.temp.cleanup()
    def build(self): return build_class_store(self.generated, self.report)
    def test_14_shared_and_singleton_partition(self):
        store=self.build(); self.assertEqual(sorted(e["class_type"] for e in store["classes"].values()), ["MULTI_FAULT_SHARED", "SINGLETON"])
    def test_15_singleton_reuses_per_failure_hash(self):
        store=self.build(); c=next(e for e in store["classes"].values() if e["class_type"]=="SINGLETON"); self.assertEqual(c["profile_source"],"PER_FAILURE_REUSE")
    def test_16_shared_profile_has_union_metadata(self):
        store=self.build(); c=next(e for e in store["classes"].values() if e["class_type"]=="MULTI_FAULT_SHARED"); self.assertEqual(c["union_disabled_links"],["f1","f2"])
    def test_17_fault_to_class_complete(self): self.assertEqual(sorted(self.build()["fault_to_class"]), ["f1","f2","f3"])
    def test_18_same_members_load_same_sha(self):
        store=self.build(); self.assertEqual(len({store["classes"][store["fault_to_class"][f]]["profile_sha256"] for f in ("f1","f2")}),1)
    def test_19_store_validation(self):
        store=self.build(); self.assertEqual(validate_class_store(self.generated/"profiles/exact_equivalence/store.json",self.scenario,self.port_map), store)
    def test_20_stale_scenario_rejected(self):
        self.build(); changed=deepcopy(self.scenario); changed["scenario_sha256"]="stale"
        with self.assertRaisesRegex(ExactEquivalenceError,"scenario_sha256"): validate_class_store(self.generated/"profiles/exact_equivalence/store.json",changed,self.port_map)
    def test_21_stale_candidate_rejected(self):
        self.build(); p=self.generated/"fault_analysis/candidate_faults.json"; value=json.loads(p.read_text()); value["candidate_set_sha256"]="stale"; write_json(p,value)
        with self.assertRaisesRegex(ExactEquivalenceError,"candidate_set_sha256"): validate_class_store(self.generated/"profiles/exact_equivalence/store.json",self.scenario,self.port_map)
    def test_22_stale_per_failure_rejected(self):
        self.build(); p=self.generated/"profiles/per_failure/store.json"; value=json.loads(p.read_text()); value["changed"]=1; write_json(p,value)
        with self.assertRaisesRegex(ExactEquivalenceError,"per_failure_store_sha256"): validate_class_store(self.generated/"profiles/exact_equivalence/store.json",self.scenario,self.port_map)
    def test_23_stale_solver_rejected(self):
        store=self.build(); p=self.generated/"profiles/exact_equivalence/store.json"; value=json.loads(p.read_text()); value["solver_config_hash"]="stale"; write_json(p,value)
        with self.assertRaisesRegex(ExactEquivalenceError,"solver_config_hash"): validate_class_store(p,self.scenario,self.port_map)
    def test_23b_stale_candidate_policy_rejected(self):
        self.build(); p=self.generated/"fault_analysis/candidate_faults.json"; value=json.loads(p.read_text()); value["policy"]={"mode":"explicit"}; write_json(p,value)
        with self.assertRaisesRegex(ExactEquivalenceError,"candidate_policy"): validate_class_store(self.generated/"profiles/exact_equivalence/store.json",self.scenario,self.port_map)
    def test_24_profile_count_excludes_p0(self): self.assertEqual(class_store_metrics(self.generated/"profiles/exact_equivalence/store.json")["final_equivalence_class_count"] if self.build() else None,2)
    def test_25_storage_excludes_metadata(self):
        self.build(); m=class_store_metrics(self.generated/"profiles/exact_equivalence/store.json"); self.assertNotEqual(m["equivalence_profile_bytes"],m["class_store_metadata_bytes"])
    def test_26_failed_synthesis_falls_back(self):
        report=deepcopy(self.report); next(iter(report.values()))["status"]="UNSAT"; store=build_class_store(self.generated,report); self.assertEqual(len(store["classes"]),3)
    def test_26b_no_route_synthesis_falls_back(self):
        report=deepcopy(self.report); next(iter(report.values()))["status"]="NO_ROUTE"; store=build_class_store(self.generated,report); self.assertEqual(len(store["classes"]),3)
    def test_26c_forwarding_conflict_falls_back(self):
        report=deepcopy(self.report); next(iter(report.values()))["status"]="FORWARDING_CONFLICT"; store=build_class_store(self.generated,report); self.assertEqual(len(store["classes"]),3)
    def test_27_validation_promotes_shared(self):
        store=self.build(); c=next(k for k,v in store["classes"].items() if v["class_type"]=="MULTI_FAULT_SHARED"); e=store["classes"][c]
        rows=[{"class_id":c,"fault_id":f,"profile_sha256":e["profile_sha256"],"validation_pass":True} for f in e["members"]]
        self.assertEqual(finalize_class_store(self.generated/"profiles/exact_equivalence/store.json",rows)["classes"][c]["status"],"VALIDATED_SHARED")
    def test_28_validation_failure_falls_back(self):
        store=self.build(); c=next(k for k,v in store["classes"].items() if v["class_type"]=="MULTI_FAULT_SHARED"); e=store["classes"][c]
        rows=[{"class_id":c,"fault_id":f,"profile_sha256":e["profile_sha256"],"validation_pass":f==e["members"][0]} for f in e["members"]]
        final=finalize_class_store(self.generated/"profiles/exact_equivalence/store.json",rows); self.assertEqual(len(final["classes"]),3)


if __name__ == "__main__": unittest.main()
