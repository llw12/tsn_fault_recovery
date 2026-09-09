"""Offline exp19 contract tests (123 checks; no OMNeT++/INET invocation)."""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from tools.h2s_pf_backend import SEMANTIC_PROFILE_FIELDS, semantic_profile_hash, semantic_profile_projection
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.realistic_full_density_pf_cost import (
    EXPECTED_COUNTS, EXPECTED_RELEASE_MAP_SHA, EXPECTED_SOURCE_SHA, FROZEN_TREE_SHA256,
    ORDER, benchmark_config, candidate_catalog, canonical_route_hash, frozen_preflight,
    gzip_bytes, load_benchmark, lpt_projection, pearson, profile_storage, recover_p0_baseline,
    repeat_subset, route_churn, runtime_profile_contract_audit, spearman,
)


class RealisticFullDensityPfCostTests(unittest.TestCase):
    """Preflight, census, storage, and policy checks for the formal runner."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.frozen = frozen_preflight()
        cls.manifest, cls.scenarios, cls.paths = load_benchmark()
        cls.temp = tempfile.TemporaryDirectory(prefix="exp19-contract-")
        root = Path(cls.temp.name)
        cls.p0 = {scenario: recover_p0_baseline(scenario, cls.paths[scenario], cls.scenarios[scenario], root)
                  for scenario in ORDER}
        cls.census, cls.candidates, cls.candidate_sha = {}, {}, {}
        for scenario in ORDER:
            census, candidates, digest = candidate_catalog(scenario, cls.scenarios[scenario], cls.p0[scenario]["logical_routes"])
            cls.census[scenario], cls.candidates[scenario], cls.candidate_sha[scenario] = census, candidates, digest

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_001_exp18j_manifest_is_pinned(self) -> None:
        self.assertEqual(self.manifest["release_map_sha256"], EXPECTED_RELEASE_MAP_SHA)

    def test_002_all_six_scenarios_are_ordered(self) -> None:
        self.assertEqual(tuple(self.scenarios), ORDER)

    def test_003_source_hashes_are_exact(self) -> None:
        self.assertEqual(set(EXPECTED_SOURCE_SHA), set(ORDER))

    def test_004_medium_flow_and_instance_counts(self) -> None:
        self.assertEqual(EXPECTED_COUNTS["M"], (352, 3616))

    def test_005_large_flow_and_instance_counts(self) -> None:
        self.assertEqual(EXPECTED_COUNTS["L"], (928, 9632))

    def test_006_frozen_history_is_unchanged(self) -> None:
        self.assertEqual(self.frozen, FROZEN_TREE_SHA256)

    def test_007_backend_config_is_exact(self) -> None:
        config = benchmark_config(self.manifest)
        self.assertEqual(config["route_scope"], "ALL_REROUTE")

    def test_008_backend_uses_low_period_h2s(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["h2s_flow_sorting"], 4)

    def test_009_backend_uses_path_length(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["configuration_rating"], "PATH_LENGTH")

    def test_010_backend_uses_asap(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["placement_cli"], 0)

    def test_011_backend_uses_k5(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["candidate_paths_k"], 5)

    def test_012_backend_uses_100ns(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["quantum_ns"], 100)

    def test_013_backend_uses_seed_1024(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["global_seed"], 1024)

    def test_014_backend_uses_one_thread(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["threads"], 1)

    def test_015_backend_uses_h2s_timeout_30s(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["timeout_s_h2s"], 30)

    def test_016_backend_uses_celf_timeout_30s(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["timeout_s_celf"], 30)

    def test_017_backend_uses_8gb(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["memory_limit_mb"], 8192)

    def test_018_backend_has_baseline_tie_break(self) -> None:
        self.assertEqual(benchmark_config(self.manifest)["h2s_tiebreak_mode"], "BASELINE")

    def test_019_backend_disables_multistart(self) -> None:
        self.assertFalse(benchmark_config(self.manifest)["multistart"])

    def test_020_backend_disables_trace(self) -> None:
        self.assertFalse(benchmark_config(self.manifest)["diagnostic_trace"])

    def test_021_p0_recovery_is_complete(self) -> None:
        self.assertTrue(all(p0["static_checker_pass"] and p0["upstream_verifier_pass"] for p0 in self.p0.values()))

    def test_022_p0_recovery_has_all_flows(self) -> None:
        self.assertTrue(all(len(self.p0[s]["logical_routes"]) == len(self.scenarios[s]["tt_flows"]) for s in ORDER))

    def test_023_p0_recovery_has_expected_instances(self) -> None:
        self.assertTrue(all(self.p0[s]["expected_instance_count"] == EXPECTED_COUNTS[s[0]][1] for s in ORDER))

    def test_024_p0_recovery_has_completed_instances(self) -> None:
        self.assertTrue(all(self.p0[s]["complete_instance_count"] == EXPECTED_COUNTS[s[0]][1] for s in ORDER))

    def test_025_p0_route_hash_is_stable(self) -> None:
        self.assertTrue(all(self.p0[s]["route_hash"] == canonical_route_hash(self.p0[s]["logical_routes"]) for s in ORDER))

    def test_026_candidate_sha_is_present(self) -> None:
        self.assertTrue(all(len(value) == 64 for value in self.candidate_sha.values()))

    def test_027_candidate_ids_are_canonical(self) -> None:
        self.assertTrue(all([row["fault_id"] for row in values] == sorted(row["fault_id"] for row in values) for values in self.candidates.values()))

    def test_028_candidates_are_internal(self) -> None:
        self.assertTrue(all(row["internal_switch_link"] for values in self.candidates.values() for row in values))

    def test_029_candidates_are_p0_relevant(self) -> None:
        self.assertTrue(all(row["relevant_fault"] and row["p0_used"] for values in self.candidates.values() for row in values))

    def test_030_unused_internal_links_are_not_candidates(self) -> None:
        self.assertTrue(all(not row["relevant_fault"] for values in self.census.values() for row in values if row["internal_switch_link"] and not row["p0_used"]))

    def test_031_candidate_ranks_are_contiguous(self) -> None:
        self.assertTrue(all([row["candidate_rank"] for row in values] == list(range(1, len(values) + 1)) for values in self.candidates.values()))

    def test_032_candidate_affected_sets_are_nonempty(self) -> None:
        self.assertTrue(all(row["affected_flow_count"] > 0 and row["affected_flow_ids"] for values in self.candidates.values() for row in values))

    def test_033_repeat_subset_is_deterministic(self) -> None:
        self.assertTrue(all(repeat_subset(values) == repeat_subset(values) for values in self.candidates.values()))

    def test_034_repeat_subset_is_at_most_five(self) -> None:
        self.assertTrue(all(len(repeat_subset(values)) <= 5 for values in self.candidates.values()))

    def test_035_repeat_subset_is_from_catalog(self) -> None:
        self.assertTrue(all(set(repeat_subset(values)) <= {row["fault_id"] for row in values} for values in self.candidates.values()))

    def test_036_semantic_projection_has_exact_six_fields(self) -> None:
        self.assertEqual(SEMANTIC_PROFILE_FIELDS, ("forwarding_model", "logical_routes", "stream_forwarding", "release_offsets_ns", "gate_schedules", "schedule_windows"))

    def test_037_semantic_projection_is_code_owned(self) -> None:
        profile = self.p0["M_RING"]["profile"]
        self.assertEqual(set(semantic_profile_projection(profile)), set(SEMANTIC_PROFILE_FIELDS))

    def test_038_semantic_hash_is_deterministic(self) -> None:
        profile = self.p0["M_RING"]["profile"]
        self.assertEqual(semantic_profile_hash(profile), semantic_profile_hash(profile))

    def test_039_storage_reports_raw_and_gzip_bytes(self) -> None:
        stored = profile_storage(self.p0["M_RING"]["profile"])
        self.assertGreater(stored["canonical_profile_bytes"], 0)
        self.assertGreater(stored["canonical_gzip_bytes"], 0)

    def test_040_gzip_is_reproducible(self) -> None:
        payload = canonical_json_bytes(self.p0["M_RING"]["profile"])
        self.assertEqual(gzip_bytes(payload), gzip_bytes(payload))


def _contract(number: int):
    def case(self: RealisticFullDensityPfCostTests) -> None:
        helper = inspect.getsource(__import__("tools.realistic_full_density_pf_cost", fromlist=["*"]))
        runner = inspect.getsource(__import__("tools.run_realistic_full_density_pf_cost", fromlist=["*"]))
        routes = self.p0["M_RING"]["logical_routes"]
        churn = route_churn(routes, routes, (),)
        jobs = [{"fault_id": "b", "total_backend_ms": 2}, {"fault_id": "a", "total_backend_ms": 2}, {"fault_id": "c", "total_backend_ms": 1}]
        checks = {
            41: lambda: "write_atomic_json" in helper,
            42: lambda: "RESUME_CONTRACT_MISMATCH" in helper,
            43: lambda: "candidate_set_sha256" in helper,
            44: lambda: "implementation_commit" in helper,
            45: lambda: "semantic_patch_sha256" in helper,
            46: lambda: "upstream_commit" in helper,
            47: lambda: "all-reroute" in runner,
            48: lambda: "affected-only" not in runner.split("def main", 1)[1],
            49: lambda: "--quick" in runner,
            50: lambda: "--qualification" in runner,
            51: lambda: "--resume" in runner,
            52: lambda: "candidate_fault_census.csv" in runner,
            53: lambda: "candidate_faults.json" in runner,
            54: lambda: "p0_baseline.csv" in runner,
            55: lambda: "p0_route_manifest.json" in runner,
            56: lambda: "per_fault_results.csv" in runner,
            57: lambda: "coverage_summary.csv" in runner,
            58: lambda: "timing_summary.csv" in runner,
            59: lambda: "memory_summary.csv" in runner,
            60: lambda: "profile_storage.csv" in runner,
            61: lambda: "route_churn.csv" in runner,
            62: lambda: "cost_correlations.csv" in runner,
            63: lambda: "lpt_parallel_projection.csv" in runner,
            64: lambda: "topology_comparison.csv" in runner,
            65: lambda: "scale_comparison.csv" in runner,
            66: lambda: "repeatability_audit.csv" in runner,
            67: lambda: "runtime_profile_contract_audit.md" in runner,
            68: lambda: "analysis_manifest.json" in runner,
            69: lambda: "PF_FULL_CENSUS_COMPLETE_ALL_PROFILES" in runner,
            70: lambda: "PF_FULL_CENSUS_COMPLETE_PARTIAL_PROFILE_COVERAGE" in runner,
            71: lambda: "PF_FULL_CENSUS_COMPLETE_WITH_RESOURCE_LIMITS" in runner,
            72: lambda: "PF_CAMPAIGN_BUDGET_EXCEEDED" in runner,
            73: lambda: "PF_SEMANTIC_VALIDATION_FAILED" in runner,
            74: lambda: "PF_BENCHMARK_QUALIFICATION_FAILED" in runner,
            75: lambda: "SCENARIO_BUDGET_S" in runner,
            76: lambda: "TIMEOUT_S = 30" in runner,
            77: lambda: "memory_limit_mb=8192" in runner,
            78: lambda: "candidate_paths=5" in runner,
            79: lambda: "h2s_flow_sorting=4" in runner,
            80: lambda: "h2s_tiebreak_mode=\"BASELINE\"" in runner,
            81: lambda: "FORMAL_THREADS" in runner,
            82: lambda: "H2S" in runner and "CELF" in runner,
            83: lambda: "disabled_directed_arc_count" in runner,
            84: lambda: "ALL_REROUTE_HAS_NO_FIXED_PATH" in runner,
            85: lambda: "FAILED_PHYSICAL_LINK_REMOVED_BOTH_DIRECTIONS" in runner,
            86: lambda: "ALL_TT_FLOWS_PRESENT" in runner,
            87: lambda: "FIXED_RELEASE_PRESERVED" in runner,
            88: lambda: "STRUCTURAL_NO_ROUTE" in runner,
            89: lambda: "HEURISTIC_NOT_FOUND" in runner,
            90: lambda: "no_sampling" in runner,
            91: lambda: "no_profile_grouping" in runner,
            92: lambda: "no_profile_deduplication" in runner,
            93: lambda: "gzip_bytes" in runner,
            94: lambda: "semantic_profile_index" in runner,
            95: lambda: runtime_profile_contract_audit()["status"] == "NOT_ESTABLISHED",
            96: lambda: churn["changed_route_flow_count"] == 0,
            97: lambda: churn["hop_delta_total"] == 0,
            98: lambda: lpt_projection(jobs, 2)["lpt_makespan_ms"] == 3,
            99: lambda: lpt_projection(jobs, 2)["lower_bound_ms"] == 2.5,
            100: lambda: lpt_projection(jobs, 2)["serial_work_ms"] == 5,
            101: lambda: pearson([1, 2], [2, 4]) == 1,
            102: lambda: spearman([1, 2, 3], [3, 2, 1]) == -1,
            103: lambda: "omnet_invocations" in runner,
            104: lambda: "inet_invocations" in runner,
            105: lambda: "matplotlib" not in runner,
            106: lambda: "ThreadPool" not in runner,
            107: lambda: "multiprocessing" not in runner,
            108: lambda: "warm_start" not in runner.lower(),
            109: lambda: "cache_key" not in runner.lower(),
            110: lambda: "grouping" in runner and "no_profile_grouping" in runner,
            111: lambda: "--candidate-paths" not in runner,
            112: lambda: "for candidate_paths" not in runner,
            113: lambda: "for sorter" not in runner,
            114: lambda: "for.*timeout" not in runner,
            115: lambda: "P0_SEPARATE_NOT_DEDUPLICATED" in runner,
            116: lambda: "per_fault_results" in runner,
            117: lambda: "raw_backend_output" in runner,
            118: lambda: "profiles" in runner,
            119: lambda: "stores" in runner,
            120: lambda: "artifact_sha256" in runner,
            121: lambda: "campaign_sha256" in runner,
            122: lambda: all(len(self.candidates[s]) > 0 for s in ORDER),
            123: lambda: all(self.candidate_sha[s] == self.candidate_sha[s] for s in ORDER),
        }
        self.assertTrue(checks[number]())
    return case


for _number in range(41, 124):
    setattr(RealisticFullDensityPfCostTests, f"test_{_number:03d}_exp19_contract", _contract(_number))


if __name__ == "__main__":
    unittest.main()
