"""exp22 JSPFG-v1 unit and protocol contract tests (160 visible checks)."""
from __future__ import annotations

import inspect
import random
import unittest

from tools.jaccard_fault_grouping import (
    THRESHOLDS, Threshold, affected_union, candidate_compare, canonical_affected,
    compare_fraction_desc, jaccard_counts, run_jaccard_grouping_search, same_affected_set,
)
from tools.run_jaccard_threshold_fault_grouping import FORMAL_VERDICTS, ROOT_OUTPUTS, _candidate_trace, pareto, qualification


class JaccardThresholdTests(unittest.TestCase):
    def test_001_preregistered_count(self): self.assertEqual(len(THRESHOLDS), 6)
    def test_002_order(self): self.assertEqual([x.identifier for x in THRESHOLDS], ["tau_100", "tau_080", "tau_060", "tau_040", "tau_020", "tau_positive"])
    def test_003_same_set(self): self.assertEqual(jaccard_counts(("a","b"),("b","a")), (2,2))
    def test_004_disjoint(self): self.assertEqual(jaccard_counts(("a",),("b",)), (0,2))
    def test_005_partial(self): self.assertEqual(jaccard_counts(("a","b"),("b","c")), (1,3))
    def test_006_subset(self): self.assertEqual(jaccard_counts(("a",),("a","b")), (1,2))
    def test_007_canonical_sorted(self): self.assertEqual(canonical_affected(("b","a","a")), ("a","b"))
    def test_008_union(self): self.assertEqual(affected_union(("a","b"),("b","c")), ("a","b","c"))
    def test_009_equality_true(self): self.assertTrue(same_affected_set(("a","b"),("b","a")))
    def test_010_equality_false(self): self.assertFalse(same_affected_set(("a",),("a","b")))
    def test_011_tau1_equal(self): self.assertTrue(Threshold("x",1,1).eligible(2,2))
    def test_012_tau1_not_equal(self): self.assertFalse(Threshold("x",1,1).eligible(1,2))
    def test_013_tau060_boundary(self): self.assertTrue(Threshold("x",3,5).eligible(3,5))
    def test_014_tau060_below(self): self.assertFalse(Threshold("x",3,5).eligible(2,4))
    def test_015_tau020_boundary(self): self.assertTrue(Threshold("x",1,5).eligible(1,5))
    def test_016_positive_one(self): self.assertTrue(Threshold("x",0,1,True).eligible(1,999))
    def test_017_positive_zero(self): self.assertFalse(Threshold("x",0,1,True).eligible(0,1))
    def test_018_fraction_desc(self): self.assertLess(compare_fraction_desc(3,5,1,2),0)
    def test_019_fraction_equal(self): self.assertEqual(compare_fraction_desc(1,2,2,4),0)
    def test_020_fraction_asc(self): self.assertGreater(compare_fraction_desc(1,3,1,2),0)
    def test_021_no_float_gate_source(self): self.assertNotIn("/",inspect.getsource(Threshold.eligible))
    def test_022_ranking_jaccard_first(self):
        a={"jaccard_num":3,"jaccard_den":5,"window_slack_ticks":0,"cut_slack_ticks":0,"deadline_slack_ticks":0,"union_group_size":2,"candidate_group_id":"a"}; b={**a,"jaccard_num":1,"jaccard_den":2}; self.assertLess(candidate_compare(a,b),0)
    def test_023_ranking_window_second(self):
        a={"jaccard_num":1,"jaccard_den":2,"window_slack_ticks":2,"cut_slack_ticks":0,"deadline_slack_ticks":0,"union_group_size":2,"candidate_group_id":"a"}; b={**a,"window_slack_ticks":1}; self.assertLess(candidate_compare(a,b),0)
    def test_024_ranking_size_fifth(self):
        a={"jaccard_num":1,"jaccard_den":2,"window_slack_ticks":2,"cut_slack_ticks":2,"deadline_slack_ticks":2,"union_group_size":3,"candidate_group_id":"a"}; b={**a,"union_group_size":2}; self.assertLess(candidate_compare(a,b),0)
    def test_025_qualification(self): self.assertTrue(qualification()["qualification_pass"])
    def test_026_outputs(self): self.assertEqual(len(ROOT_OUTPUTS),29)
    def test_027_verdicts(self): self.assertIn("TAU1_EXP21_PARITY_FAILED", FORMAL_VERDICTS)
    def test_028_no_best_verdict(self): self.assertFalse(any("BEST" in x for x in FORMAL_VERDICTS))

    def test_029_jaccard_reject_skips_evaluate(self):
        calls=[]; run_jaccard_grouping_search({"a":("x",),"b":("y",)},Threshold("x",1,5),lambda f,a:calls.append(f) or {"passed":True,"timings_ms":{}},lambda f,a,r:{"accepted":False}); self.assertEqual(calls,[])
    def test_030_jaccard_accepts_evaluate(self):
        calls=[]; run_jaccard_grouping_search({"a":("x",),"b":("x","y")},Threshold("x",1,5),lambda f,a:calls.append(f) or {"passed":False,"first_failure":"F1_CONNECTIVITY","timings_ms":{},"failure_certificate":{"fault_set":list(f),"monotone_superset_prunable":True}},lambda f,a,r:{"accepted":False}); self.assertEqual(len(calls),1)
    def test_031_reject_has_no_certificate(self):
        r=run_jaccard_grouping_search({"a":("x",),"b":("y",)},Threshold("x",1,5),lambda f,a:{"passed":True,"timings_ms":{}},lambda f,a,r:{"accepted":False}); self.assertEqual(r["failure_certificates"],[])
    def test_032_f1_has_certificate(self):
        r=run_jaccard_grouping_search({"a":("x",),"b":("x",)},Threshold("x",1,1),lambda f,a:{"passed":False,"first_failure":"F1_CONNECTIVITY","timings_ms":{},"failure_certificate":{"fault_set":list(f),"monotone_superset_prunable":True}},lambda f,a,r:{"accepted":False}); self.assertEqual(len(r["failure_certificates"]),1)
    def test_033_h2s_failure_no_certificate(self):
        r=run_jaccard_grouping_search({"a":("x",),"b":("x",)},Threshold("x",1,1),lambda f,a:{"passed":True,"timings_ms":{}},lambda f,a,r:{"accepted":False,"status":"HEURISTIC_NOT_FOUND"}); self.assertEqual(r["failure_certificates"],[])
    def test_034_size2(self):
        r=run_jaccard_grouping_search({"a":("x",),"b":("x",)},Threshold("x",1,1),lambda f,a:{"passed":True,"timings_ms":{}},lambda f,a,r:{"accepted":True,"profile":{},"semantic_profile_hash":"x"}); self.assertEqual(r["final_groups"][0]["group_size"],2)
    def test_035_size3(self):
        r=run_jaccard_grouping_search({"a":("x",),"b":("x",),"c":("x",)},Threshold("x",1,1),lambda f,a:{"passed":True,"timings_ms":{}},lambda f,a,r:{"accepted":True,"profile":{},"semantic_profile_hash":"x"}); self.assertEqual(r["final_groups"][0]["group_size"],3)
    def test_036_dynamic_union(self):
        r=run_jaccard_grouping_search({"a":("1","2"),"b":("2","3"),"c":("3","4")},Threshold("x",1,5),lambda f,a:{"passed":True,"timings_ms":{}},lambda f,a,row:{"accepted":True,"profile":{},"semantic_profile_hash":"x"}); self.assertEqual(r["final_groups"][0]["affected_flow_ids"],["1","2","3","4"])
    def test_037_alternate_after_failure(self):
        seen=[]
        def synth(f,a,r): seen.append(f); return {"accepted":f != ("a","b"),"profile":{},"semantic_profile_hash":"x"}
        r=run_jaccard_grouping_search({"a":("x",),"b":("x",),"c":("x",)},Threshold("x",1,1),lambda f,a:{"passed":True,"timings_ms":{}},synth); self.assertGreaterEqual(len(seen),2)
    def test_038_stale(self):
        r=run_jaccard_grouping_search({"a":("x",),"b":("x",),"c":("x",)},Threshold("x",1,1),lambda f,a:{"passed":True,"timings_ms":{}},lambda f,a,r:{"accepted":True,"profile":{},"semantic_profile_hash":"x"}); self.assertGreater(r["funnel"]["stale_after_accept"],0)
    def test_039_threshold_independence(self): self.assertNotEqual(THRESHOLDS[0].identifier,THRESHOLDS[1].identifier)
    def test_040_group_id_deterministic(self):
        r1=run_jaccard_grouping_search({"a":("x",),"b":("x",)},Threshold("x",1,1),lambda f,a:{"passed":True,"timings_ms":{}},lambda f,a,r:{"accepted":True,"profile":{},"semantic_profile_hash":"x"}); r2=run_jaccard_grouping_search({"b":("x",),"a":("x",)},Threshold("x",1,1),lambda f,a:{"passed":True,"timings_ms":{}},lambda f,a,r:{"accepted":True,"profile":{},"semantic_profile_hash":"x"}); self.assertEqual(r1["final_groups"],r2["final_groups"])
    def test_200_candidate_trace_csv_type_parity(self):
        native={"iteration":1,"left_group_id":"a","right_group_id":"b","candidate_group_id":"g","fault_ids":"a;b","jaccard_num":1,"jaccard_den":2,"threshold_pass":True,"first_failure":"","filter_pass":False,"decision":"JACCARD_REJECT","rank":None}; text={key:("True" if value is True else "False" if value is False else "" if value is None else str(value)) for key,value in native.items()}; self.assertEqual(_candidate_trace([native]),_candidate_trace([text]))
    def test_201_pareto_comparison_cost_alias(self):
        rows=pareto([{"threshold_id":"a","final_profiles":2,"total_algorithm_ms":2,"complete":True},{"threshold_id":"b","final_profiles":3,"total_algorithm_ms":3,"complete":True}]); self.assertTrue(rows[0]["pareto_optimal"]); self.assertFalse(rows[1]["pareto_optimal"])


def _random_case(number: int):
    def test(self: JaccardThresholdTests) -> None:
        rng=random.Random(number); left={str(rng.randrange(20)) for _ in range(8)}; right={str(rng.randrange(20)) for _ in range(8)}
        inter, union=jaccard_counts(left,right)
        for threshold in THRESHOLDS[:-1]: self.assertEqual(threshold.eligible(inter,union),threshold.denominator*inter>=threshold.numerator*union)
        self.assertEqual(THRESHOLDS[-1].eligible(inter,union),inter>0)
    test.__name__=f"test_{number+40:03d}_exact_rational_random"; return test


for _number in range(1,121): setattr(JaccardThresholdTests,f"test_{_number+40:03d}_exact_rational_random",_random_case(_number))


if __name__ == "__main__": unittest.main()
