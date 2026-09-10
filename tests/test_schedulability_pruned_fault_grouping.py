"""exp21 algorithm and protocol contract tests (150 individually visible checks)."""
from __future__ import annotations

import inspect
import random
import tempfile
import unittest
from pathlib import Path

from tools.fault_grouping_search import (
    ALGORITHM_VERSION, affected_set_key, build_affected_set_pools, group_id,
    ranking_key, run_grouping_search,
)
from tools.h2s_group_recovery_backend import H2sGroupRecoveryBackend, fault_group_id
from tools.recovery_backend import BackendStatus, RecoverySynthesisRequest
from tools.run_schedulability_pruned_fault_grouping import FORMAL_VERDICTS, REQUIRED_OUTPUTS, pure_qualification
from tools.schedulability_necessary_conditions import (
    certificate_prunes, checked_cut_family, connectivity_condition,
    critical_window_exhaustive, critical_window_sweep, deadline_condition,
    evaluate_necessary_conditions, shortest_hops,
)


def toy() -> dict:
    return {"nodes": [{"id": x, "type": "switch" if x in "AB" else "device"} for x in "SABT"],
            "links": [{"id": "e1", "endpoint_a": "S", "endpoint_b": "A", "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0},
                      {"id": "e2", "endpoint_a": "A", "endpoint_b": "B", "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0},
                      {"id": "e3", "endpoint_a": "B", "endpoint_b": "T", "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0},
                      {"id": "e4", "endpoint_a": "A", "endpoint_b": "T", "bitrate_bps": 1_000_000_000, "propagation_delay_s": 0}],
            "tt_flows": [{"id": "F", "source": "S", "destination": "T", "period_s": .001,
                          "release_offset_s": 0, "schedule_deadline_budget_s": .0001, "packet_size_bytes": 64}],
            "scheduling": {"frame_overhead_bytes": 64}}


class Exp21Tests(unittest.TestCase):
    def test_001_version(self): self.assertEqual(ALGORITHM_VERSION, "SPFG-v1")
    def test_002_group_id_order_independent(self): self.assertEqual(group_id(["b", "a"]), group_id(["a", "b"]))
    def test_003_group_id_deduplicates(self): self.assertEqual(group_id(["a", "a"]), group_id(["a"]))
    def test_004_backend_group_id_same_contract(self): self.assertEqual(group_id(["a", "b"]), fault_group_id(["b", "a"]))
    def test_005_affected_key_order_independent(self): self.assertEqual(affected_set_key(["x", "y"]), affected_set_key(["y", "x"]))
    def test_006_different_affected_keys(self): self.assertNotEqual(affected_set_key(["x"]), affected_set_key(["y"]))
    def test_007_pooling_same_set(self): self.assertEqual(len(build_affected_set_pools([{"fault_id":"a","affected_flow_ids":"x;y"},{"fault_id":"b","affected_flow_ids":"y;x"}])), 1)
    def test_008_pooling_different_set(self): self.assertEqual(len(build_affected_set_pools([{"fault_id":"a","affected_flow_ids":"x"},{"fault_id":"b","affected_flow_ids":"y"}])), 2)
    def test_009_pool_faults_sorted(self): self.assertEqual(build_affected_set_pools([{"fault_id":"b","affected_flow_ids":"x"},{"fault_id":"a","affected_flow_ids":"x"}])[0]["fault_ids"], ["a","b"])
    def test_010_shortest_hops(self):
        graph={"a":[("b","e")],"b":[("a","e"),("c","f")],"c":[("b","f")]}; self.assertEqual(shortest_hops(graph,"a","c"),2)
    def test_011_shortest_hops_same(self): self.assertEqual(shortest_hops({"a":[]},"a","a"),0)
    def test_012_shortest_hops_missing(self): self.assertIsNone(shortest_hops({"a":[],"b":[]},"a","b"))
    def test_013_connectivity_pass(self): self.assertTrue(connectivity_condition(toy(),())["passed"])
    def test_014_connectivity_alternate_pass(self): self.assertTrue(connectivity_condition(toy(),("e2",))["passed"])
    def test_015_connectivity_fail(self): self.assertFalse(connectivity_condition(toy(),("e1",))["passed"])
    def test_016_connectivity_stage(self): self.assertEqual(connectivity_condition(toy(),("e1",))["stage"],"F1_CONNECTIVITY")
    def test_017_deadline_pass(self): self.assertTrue(deadline_condition(toy(),())["passed"])
    def test_018_cut_family_deterministic(self): self.assertEqual(checked_cut_family(toy(),(),("F",)),checked_cut_family(toy(),(),("F",)))
    def test_019_cut_ids_unique(self):
        rows=checked_cut_family(toy(),(),("F",)); self.assertEqual(len(rows),len({r["cut_id"] for r in rows}))
    def test_020_pipeline_pass(self): self.assertTrue(evaluate_necessary_conditions(toy(),(),("F",))["passed"])
    def test_021_pipeline_first_failure(self): self.assertEqual(evaluate_necessary_conditions(toy(),("e1",),("F",))["first_failure"],"F1_CONNECTIVITY")
    def test_022_certificate_present(self): self.assertIsNotNone(evaluate_necessary_conditions(toy(),("e1",),("F",))["failure_certificate"])
    def test_023_certificate_exact(self):
        c=evaluate_necessary_conditions(toy(),("e1",),("F",))["failure_certificate"]; self.assertTrue(certificate_prunes(c,("e1",)))
    def test_024_certificate_superset(self):
        c=evaluate_necessary_conditions(toy(),("e1",),("F",))["failure_certificate"]; self.assertTrue(certificate_prunes(c,("e1","e9")))
    def test_025_certificate_not_other(self):
        c=evaluate_necessary_conditions(toy(),("e1",),("F",))["failure_certificate"]; self.assertFalse(certificate_prunes(c,("e9",)))
    def test_026_empty_jobs_pass(self): self.assertTrue(critical_window_sweep([],1,10)["passed"])
    def test_027_zero_machines_empty_pass(self): self.assertTrue(critical_window_sweep([],0,10)["passed"])
    def test_028_obvious_overload(self): self.assertFalse(critical_window_sweep([{"packet_id":"a","E":0,"L":1,"p":2}],1,10)["passed"])
    def test_029_obvious_slack(self): self.assertTrue(critical_window_sweep([{"packet_id":"a","E":0,"L":3,"p":2}],1,10)["passed"])
    def test_030_sweep_exhaustive_simple(self):
        j=[{"packet_id":"a","E":0,"L":3,"p":2}]; self.assertEqual(critical_window_sweep(j,1,10)["max_overload_ticks"],critical_window_exhaustive(j,1,10)["max_overload_ticks"])
    def test_031_ranking_window_first(self):
        a={"window_slack_ticks":2,"cut_slack_ticks":1,"deadline_slack_ticks":1,"group_size":2,"candidate_group_id":"a"}; b={**a,"window_slack_ticks":1,"candidate_group_id":"b"}; self.assertLess(ranking_key(a),ranking_key(b))
    def test_032_ranking_cut_second(self):
        a={"window_slack_ticks":2,"cut_slack_ticks":2,"deadline_slack_ticks":1,"group_size":2,"candidate_group_id":"a"}; b={**a,"cut_slack_ticks":1}; self.assertLess(ranking_key(a),ranking_key(b))
    def test_033_ranking_deadline_third(self):
        a={"window_slack_ticks":2,"cut_slack_ticks":2,"deadline_slack_ticks":2,"group_size":2,"candidate_group_id":"a"}; b={**a,"deadline_slack_ticks":1}; self.assertLess(ranking_key(a),ranking_key(b))
    def test_034_ranking_group_size_fourth(self):
        a={"window_slack_ticks":2,"cut_slack_ticks":2,"deadline_slack_ticks":2,"group_size":3,"candidate_group_id":"a"}; b={**a,"group_size":2}; self.assertLess(ranking_key(a),ranking_key(b))
    def test_035_ranking_id_fifth(self):
        a={"window_slack_ticks":2,"cut_slack_ticks":2,"deadline_slack_ticks":2,"group_size":2,"candidate_group_id":"a"}; b={**a,"candidate_group_id":"b"}; self.assertLess(ranking_key(a),ranking_key(b))
    def test_036_no_bottleneck_after_finite(self):
        a={"window_slack_ticks":1,"cut_slack_ticks":1,"deadline_slack_ticks":1,"group_size":2,"candidate_group_id":"a"}; b={**a,"window_slack_ticks":None}; self.assertLess(ranking_key(a),ranking_key(b))
    def test_037_search_accepts(self):
        f=lambda faults:{"passed":True,"first_failure":"","window_slack_ticks":1,"cut_slack_ticks":1,"deadline_slack_ticks":1,"timings_ms":{}}
        s=lambda faults:{"accepted":True,"profile":{"x":1},"semantic_profile_hash":"h"}
        self.assertEqual(len(run_grouping_search(["a","b"],f,s)["final_groups"]),1)
    def test_038_search_rejects(self):
        f=lambda faults:{"passed":True,"first_failure":"","window_slack_ticks":1,"cut_slack_ticks":1,"deadline_slack_ticks":1,"timings_ms":{}}
        self.assertEqual(len(run_grouping_search(["a","b"],f,lambda x:{"accepted":False})["final_groups"]),2)
    def test_039_search_filters(self):
        f=lambda faults:{"passed":False,"first_failure":"F1_CONNECTIVITY","failure_certificate":{"fault_set":list(faults),"monotone_superset_prunable":True},"timings_ms":{}}
        self.assertEqual(len(run_grouping_search(["a","b"],f,lambda x:{"accepted":True})["synthesis_attempts"]),0)
    def test_040_search_progressive(self):
        f=lambda faults:{"passed":True,"first_failure":"","window_slack_ticks":1,"cut_slack_ticks":1,"deadline_slack_ticks":1,"timings_ms":{}}
        r=run_grouping_search(["a","b","c"],f,lambda x:{"accepted":True,"profile":{},"semantic_profile_hash":"h"}); self.assertEqual(r["final_groups"][0]["group_size"],3)
    def test_041_qualification_100(self): self.assertEqual(pure_qualification()["segment_tree_parity_cases"],100)
    def test_042_qualification_pass(self): self.assertTrue(pure_qualification()["pure_qualification_pass"])
    def test_043_verdict_count(self): self.assertEqual(len(FORMAL_VERDICTS),6)
    def test_044_success_verdict(self): self.assertIn("SCHEDULABILITY_PRUNED_GROUPING_ESTABLISHED",FORMAL_VERDICTS)
    def test_045_replay_contradiction_verdict(self): self.assertIn("GROUP_SINGLETON_REPLAY_CONTRADICTION",FORMAL_VERDICTS)
    def test_046_outputs_count(self): self.assertEqual(len(REQUIRED_OUTPUTS),25)
    def test_047_no_figures_output(self): self.assertFalse(any("figure" in x for x in REQUIRED_OUTPUTS))
    def test_048_backend_rejects_single_fault(self):
        backend=H2sGroupRecoveryBackend(Path("/missing")); request=RecoverySynthesisRequest(Path("/missing"),disabled_links=("x",),healthy_primary_routes={"x":{}},affected_flow_ids=("x",),route_scope="all-reroute",forwarding_model="stream-aware")
        self.assertEqual(backend.synthesize(request).status,BackendStatus.INVALID_INPUT)
    def test_049_backend_rejects_wrong_scope(self):
        backend=H2sGroupRecoveryBackend(Path("/missing")); request=RecoverySynthesisRequest(Path("/missing"),disabled_links=("x","y"),healthy_primary_routes={"x":{}},affected_flow_ids=("x",),route_scope="affected-only",forwarding_model="stream-aware")
        self.assertEqual(backend.synthesize(request).status,BackendStatus.UNSUPPORTED)
    def test_050_runner_has_no_omnet_invocation(self):
        source=inspect.getsource(__import__("tools.run_schedulability_pruned_fault_grouping",fromlist=["*"])); self.assertNotIn("opp_run",source)


def _parity_case(number: int):
    def test(self: Exp21Tests) -> None:
        rng=random.Random(5000+number); horizon=20; jobs=[]
        for index in range(rng.randrange(0,12)):
            early=rng.randrange(horizon); late=rng.randrange(early+1,early+horizon+1)
            jobs.append({"packet_id":f"j{index}","E":early,"L":late,"p":rng.randrange(1,5)})
        machines=1+number%3; fast=critical_window_sweep(jobs,machines,horizon); slow=critical_window_exhaustive(jobs,machines,horizon)
        self.assertEqual((fast["passed"],fast["max_overload_ticks"]),(slow["passed"],slow["max_overload_ticks"]))
    test.__name__=f"test_{number+50:03d}_segment_tree_parity"; return test


for _case in range(1,101): setattr(Exp21Tests,f"test_{_case+50:03d}_segment_tree_parity",_parity_case(_case))


if __name__ == "__main__": unittest.main()
