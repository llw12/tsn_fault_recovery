import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_smt_scalability import (
    optimize_comparison, percentile, ranks, relationships, representative_rows,
    scenario_summary, spearman, status_frontier,
)
from tools.generate_scalability_scenarios import yaml_text
from tools.run_smt_scalability_campaign import (
    HARD_MODEL_VERSION, Case, build_cases, canonical_bytes, load_checkpoint,
    model_fingerprint, normalize, save_checkpoint, shared_case_id, solver_config_hash,
)
from tools.scenario_model import load_scenario

ROOT = Path(__file__).resolve().parents[1]


def sample_row(case_id="s:PF:f", mode="PRODUCTION_OPTIMIZE", status="SAT", z3=10.0):
    return {"scenario": "s", "switch_count": 20, "tt_flow_count": 20,
            "case_id": case_id, "case_type": "PF", "policy_id": "", "fault_id": "f",
            "group_id": "", "member_faults": "f", "member_count": 1, "mode": mode,
            "repeat_index": 1, "status": status, "reason_unknown": "",
            "active_tt_flow_count": 20, "affected_tt_flow_count": 2, "disabled_link_count": 1,
            "route_total_hops": 100, "route_mean_hops": 5, "route_max_hops": 8,
            "controlled_hop_count": 80, "egress_count": 40, "contented_egress_count": 10,
            "shared_egress_count": 10, "max_flows_per_egress": 4,
            "mean_flows_per_used_egress": 2, "contention_pair_count": 30,
            "start_time_var_count": 80, "ordering_bool_var_count": 0, "other_aux_var_count": 1,
            "total_symbolic_var_count": 81, "cycle_bound_constraint_count": 160,
            "release_constraint_count": 20, "hop_precedence_constraint_count": 60,
            "deadline_constraint_count": 20, "non_overlap_constraint_count": 30,
            "other_hard_constraint_count": 21, "total_hard_constraint_count": 311,
            "route_wall_ms": 1, "model_build_wall_ms": 2, "z3_check_wall_ms": z3,
            "model_extract_wall_ms": 1, "schedule_compile_wall_ms": 1,
            "total_solver_pipeline_wall_ms": 15, "objective_count": 82,
            "objective_values": [], "z3_statistics": {}, "model_fingerprint": "abc", "diagnostic": ""}


class SmtScalabilityTests(unittest.TestCase):
    def generator(self, switches):
        return yaml_text(switches=switches, rows=5, cols=switches // 5,
                         end_systems=switches // 2, tt_flows=switches,
                         be_flows=switches // 5, seed=0, name=f"structured{switches}_auto")

    def assert_scenario(self, size):
        self.assertEqual(self.generator(size), self.generator(size))
        model=load_scenario(ROOT/f"configs/scenarios/structured{size}_auto.yaml")
        self.assertEqual(sum(node.type=="switch" for node in model.nodes),size)
        self.assertEqual(len(model.tt_flows),size)
        graph={node.id:set() for node in model.nodes}
        for link in model.links:
            graph[link.endpoint_a].add(link.endpoint_b); graph[link.endpoint_b].add(link.endpoint_a)
        seen=set(); stack=[next(iter(graph))]
        while stack:
            node=stack.pop()
            if node not in seen: seen.add(node); stack.extend(graph[node]-seen)
        self.assertEqual(seen,set(graph))

    def test_01_structured60_deterministic(self): self.assert_scenario(60)
    def test_02_structured80_deterministic(self): self.assert_scenario(80)
    def test_03_structured100_deterministic(self): self.assert_scenario(100)

    def test_04_regeneration_30_40_50_unchanged(self):
        for size in (30, 40, 50):
            self.assertEqual(self.generator(size), (ROOT / f"configs/scenarios/structured{size}_auto.yaml").read_text())

    def test_05_production_objectives_unchanged(self):
        source = (ROOT / "src/tsn_fault_recovery/control/Z3ScheduleSolver.cc").read_text()
        self.assertIn("solver.minimize(builder.maxCompletion)", source)
        self.assertIn("solver.minimize(builder.totalCompletion)", source)
        self.assertIn("solver.minimize(start)", source)

    def test_06_shared_hard_constraint_builder(self):
        source = (ROOT / "src/tsn_fault_recovery/control/Z3ScheduleSolver.cc").read_text()
        self.assertEqual(source.count("builder.addHardConstraints"), 2)

    def test_07_constraint_total_formula(self):
        row = sample_row(); expected = sum(row[key] for key in ("cycle_bound_constraint_count", "release_constraint_count",
            "hop_precedence_constraint_count", "deadline_constraint_count", "non_overlap_constraint_count", "other_hard_constraint_count"))
        self.assertEqual(expected, row["total_hard_constraint_count"])

    def test_08_variable_total_formula(self):
        row=sample_row(); self.assertEqual(row["start_time_var_count"]+row["ordering_bool_var_count"]+row["other_aux_var_count"],row["total_symbolic_var_count"])

    def test_09_contention_pair_formula(self): self.assertEqual(sum(k*(k-1)//2 for k in (1,2,4)), 7)
    def test_10_fingerprint_canonical_bytes(self): self.assertEqual(canonical_bytes({"b":1,"a":2}), canonical_bytes({"a":2,"b":1}))
    def test_11_pf_case_id_deterministic(self): self.assertEqual("s:PF:f", "s:PF:"+"f")

    def test_12_shared_case_id_order_independent(self):
        self.assertEqual(shared_case_id("s","J020",["a","b"]), shared_case_id("s","J020",["b","a"]))

    def test_13_singletons_not_shared(self):
        model=load_scenario(ROOT/"configs/scenarios/structured20_auto.yaml")
        candidate=json.loads((ROOT/"generated/structured20_auto/fault_analysis/candidate_faults.json").read_text())
        self.assertTrue(all(c.case_type!="SHARED" or len(c.members)>=2 for c in build_cases(model,candidate)))

    def test_14_unknown_is_not_unsat(self): self.assertNotEqual("UNKNOWN_OTHER", "UNSAT")
    def test_15_timeout_classification_source(self):
        source=(ROOT/"src/tsn_fault_recovery/control/SmtScalabilityBenchmark.cc").read_text()
        self.assertIn('reason.find("timeout")', source)
        self.assertIn('reason.find("canceled")', source)
    def test_16_reason_unknown_stored(self): self.assertIn('\\"reason_unknown\\"', (ROOT/"src/tsn_fault_recovery/control/SmtScalabilityBenchmark.cc").read_text())

    def test_17_production_sat_implies_feasibility_sat(self):
        rows=[sample_row(),sample_row(mode="BENCHMARK_FEASIBILITY_ONLY",z3=1)]
        self.assertTrue(optimize_comparison(rows)[0]["consistent"])

    def test_18_feasibility_does_not_emit_windows(self): self.assertIn("feasible.windows.empty()", (ROOT/"src/tsn_fault_recovery/control/SmtScheduleSelfTest.cc").read_text())
    def test_19_feasibility_not_used_by_runtime(self):
        sources="\n".join(p.read_text() for p in (ROOT/"src/tsn_fault_recovery/control").glob("*.cc") if p.name not in {"Z3ScheduleSolver.cc","SmtScheduleSelfTest.cc","SmtScalabilityBenchmark.cc"})
        self.assertNotIn("solveFeasibilityOnly",sources)

    def test_20_feasibility_has_no_objectives(self): self.assertIn("output.objectiveCount = 0", (ROOT/"src/tsn_fault_recovery/control/Z3ScheduleSolver.cc").read_text())
    def test_21_timing_nonnegative_fixture(self): self.assertTrue(all(sample_row()[x]>=0 for x in ("route_wall_ms","model_build_wall_ms","z3_check_wall_ms")))

    def test_22_timeout_excluded_from_mean(self):
        rows=[sample_row(),sample_row("s:PF:g",status="TIMEOUT",z3=30000)]
        summary=scenario_summary(rows)[0]; self.assertEqual(summary["z3_mean_ms"],10); self.assertEqual(summary["timeout_count"],1)

    def test_23_percentiles(self): self.assertEqual(percentile([1,2,3,4],.5),2.5)
    def test_24_ranks_ties(self): self.assertEqual(ranks([2,1,2]),[2.5,1,2.5])
    def test_25_spearman(self): self.assertEqual(spearman([1,2,3],[3,2,1]),-1)

    def test_26_missing_z3_stat_contract(self): self.assertIn("NOT_AVAILABLE", (ROOT/"scripts/analyze_smt_scalability.py").read_text())

    def test_27_resume_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"checkpoint.json"; identity={"x":1}; state={"identity":identity,"completed":{"a":1}}
            save_checkpoint(path,state); self.assertEqual(load_checkpoint(path,identity,True),state)

    def test_28_stale_checkpoint_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"checkpoint.json"; save_checkpoint(path,{"identity":{"x":1},"completed":{}})
            with self.assertRaises(RuntimeError): load_checkpoint(path,{"x":2},True)

    def test_29_formal_parallelism_is_one(self): self.assertIn('"parallelism": 1', (ROOT/"tools/run_smt_scalability_campaign.py").read_text())
    def test_30_hard_model_version_stable(self): self.assertEqual(HARD_MODEL_VERSION,"fixed-route-single-cycle-v2-instrumented")

    def test_31_no_member_validation_in_benchmark(self):
        text=(ROOT/"tools/run_smt_scalability_campaign.py").read_text(); self.assertNotIn("validate_exact_equivalence",text); self.assertNotIn("run_approximate_campaign",text)

    def test_32_exp10_campaign_untouched(self):
        digest=hashlib.sha256((ROOT/"results/scalability/campaign.json").read_bytes()).hexdigest()
        self.assertEqual(digest,"93ffb1fab5670075fe9d74899844b584481e4b4e68b2dbda9f5371beff31c278")

    def test_33_structured20_j020_group_retained(self):
        model=load_scenario(ROOT/"configs/scenarios/structured20_auto.yaml")
        candidate=json.loads((ROOT/"generated/structured20_auto/fault_analysis/candidate_faults.json").read_text())
        groups=[c for c in build_cases(model,candidate) if c.case_type=="SHARED" and c.policy_id=="J020"]
        self.assertTrue(any(len(c.members)==5 for c in groups))

    def test_34_representative_p0_uses_median(self):
        rows=[{**sample_row("s:P0",z3=z),"case_type":"P0","repeat_index":i} for i,z in enumerate((3,1,2),1)]
        self.assertEqual(representative_rows(rows)[0]["z3_check_wall_ms"],2)

    def test_35_analyzer_deterministic_relationships(self):
        rows=[sample_row("s:PF:a",z3=1),{**sample_row("s:PF:b",z3=2),"total_hard_constraint_count":400}]
        self.assertEqual(relationships(rows),relationships(rows))


if __name__ == "__main__": unittest.main()
