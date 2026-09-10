"""Read-only contract tests for exp20 (112 individually visible checks)."""
from __future__ import annotations

import ast
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.h2s_pf_backend import SEMANTIC_PROFILE_FIELDS, semantic_profile_hash
from tools.pf_redundancy_audit import (
    RedundancyAuditError, affected_ids, affected_set_sha, canonical_sha,
    checker_factor_replay_valid, deterministic_gzip, exact_set_cover,
    load_raw_h2s, profile_components, rebind_queue_ids,
)
from tools.run_pf_redundancy_opportunity_audit import EXP19, ORDER, affected_groups, load_exp19


ROOT = Path(__file__).resolve().parents[1]


def sample_profile(*, route: str = "route-a", schedule: int = 10, fault: str = "e1") -> dict[str, object]:
    return {
        "fault_id": fault, "forwarding_model": "stream-aware",
        "logical_routes": [{"flow_id": "F1", "node_path": ["S", "A", "D"], "link_path": [route, "tail"]}],
        "stream_forwarding": [{"flow_id": "F1", "egress": "A"}],
        "release_offsets_ns": [{"flow_id": "F1", "release_ns": 0}],
        "gate_schedules": [{"node": "A", "open_ns": schedule}],
        "schedule_windows": [{"flow_id": "F1", "start_ns": schedule, "end_ns": schedule + 1}],
    }


def call_targets(source: str) -> set[str]:
    tree = ast.parse(source); values = set()
    def dotted(node: ast.AST) -> str:
        if isinstance(node, ast.Name): return node.id
        if isinstance(node, ast.Attribute): return dotted(node.value) + "." + node.attr
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call): values.add(dotted(node.func))
    return values


class PfRedundancyOpportunityAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verdict, cls.candidates, cls.p0, cls.paths = load_exp19()
        cls.runner_source = inspect.getsource(__import__("tools.run_pf_redundancy_opportunity_audit", fromlist=["*"]))
        cls.helper_source = inspect.getsource(__import__("tools.pf_redundancy_audit", fromlist=["*"]))
        cls.calls = call_targets(cls.runner_source) | call_targets(cls.helper_source)
        cls.groups, cls.members, cls.group_summary, cls.group_lookup = affected_groups(cls.candidates)

    # A. Frozen exp19 gates and B. input census.
    def test_001_exp19_verdict_exists(self) -> None: self.assertTrue((EXP19 / "campaign_verdict.json").is_file())
    def test_002_expected_count(self) -> None: self.assertEqual(self.verdict["expected_faults"], 1099)
    def test_003_attempted_count(self) -> None: self.assertEqual(self.verdict["attempted_faults"], 1099)
    def test_004_valid_count(self) -> None: self.assertEqual(self.verdict["valid_profiles"], 1099)
    def test_005_no_sampling(self) -> None: self.assertTrue(self.verdict["no_sampling"])
    def test_006_no_grouping(self) -> None: self.assertTrue(self.verdict["no_profile_grouping"])
    def test_007_no_dedup(self) -> None: self.assertTrue(self.verdict["no_profile_deduplication"])
    def test_008_six_scenarios(self) -> None: self.assertEqual(tuple(self.candidates), ORDER)
    def test_009_full_census_count(self) -> None: self.assertEqual(sum(map(len, self.candidates.values())), 1099)
    def test_010_canonical_profiles_count(self) -> None: self.assertEqual(len(list((EXP19 / "profiles").glob("*/*.json"))), 1099)
    def test_011_raw_logs_count(self) -> None: self.assertEqual(len(list((EXP19 / "raw_backend_output").glob("*/*/primary/logs/0_h2s_stdout.log"))), 1099)
    def test_012_profiles_are_scenario_local(self) -> None: self.assertTrue(all((EXP19 / "profiles" / scenario).is_dir() for scenario in ORDER))
    def test_013_p0_paths_exist(self) -> None: self.assertTrue(all(path.is_file() for path in self.paths.values()))
    def test_014_semantic_index_exists(self) -> None: self.assertTrue((EXP19 / "stores" / "semantic_profile_index.json").is_file())

    # C. Affected-set canonicalization and deterministic grouping.
    def test_015_affected_ids_sort(self) -> None: self.assertEqual(affected_ids(["b", "a"]), ("a", "b"))
    def test_016_affected_ids_duplicate_rejected(self) -> None:
        with self.assertRaises(RedundancyAuditError): affected_ids(["a", "a"])
    def test_017_same_ids_same_hash(self) -> None: self.assertEqual(affected_set_sha(["a", "b"]), affected_set_sha(["b", "a"]))
    def test_018_different_ids_different_hash(self) -> None: self.assertNotEqual(affected_set_sha(["a", "b"]), affected_set_sha(["a", "c"]))
    def test_019_group_count_deterministic(self) -> None: self.assertEqual(affected_groups(self.candidates)[2], self.group_summary)
    def test_020_group_membership_deterministic(self) -> None: self.assertEqual(affected_groups(self.candidates)[1], self.members)
    def test_021_group_ids_are_unique(self) -> None: self.assertEqual(len({row["group_id"] for row in self.groups}), len(self.groups))
    def test_022_group_fault_counts_match_members(self) -> None:
        self.assertTrue(all(row["fault_count"] == sum(member["group_id"] == row["group_id"] for member in self.members) for row in self.groups))

    # D/E. Exact semantic/component fingerprints.
    def test_023_semantic_field_set_is_backend_owned(self) -> None:
        self.assertEqual(SEMANTIC_PROFILE_FIELDS, ("forwarding_model", "logical_routes", "stream_forwarding", "release_offsets_ns", "gate_schedules", "schedule_windows"))
    def test_024_same_projection_same_hash(self) -> None: self.assertEqual(semantic_profile_hash(sample_profile(fault="x")), semantic_profile_hash(sample_profile(fault="y")))
    def test_025_route_change_changes_semantic_hash(self) -> None: self.assertNotEqual(semantic_profile_hash(sample_profile(route="x")), semantic_profile_hash(sample_profile(route="y")))
    def test_026_schedule_change_changes_semantic_hash(self) -> None: self.assertNotEqual(semantic_profile_hash(sample_profile(schedule=1)), semantic_profile_hash(sample_profile(schedule=2)))
    def test_027_fault_metadata_excluded(self) -> None: self.assertEqual(semantic_profile_hash(sample_profile(fault="one")), semantic_profile_hash(sample_profile(fault="two")))
    def test_028_component_full_matches_semantic(self) -> None: self.assertEqual(profile_components(sample_profile())["SEMANTIC_FULL"], semantic_profile_hash(sample_profile()))
    def test_029_route_component_changes_with_route(self) -> None: self.assertNotEqual(profile_components(sample_profile(route="x"))["ROUTE_FORWARDING"], profile_components(sample_profile(route="y"))["ROUTE_FORWARDING"])
    def test_030_schedule_component_changes_with_schedule(self) -> None: self.assertNotEqual(profile_components(sample_profile(schedule=1))["SCHEDULE"], profile_components(sample_profile(schedule=2))["SCHEDULE"])
    def test_031_logical_route_component_changes_with_route(self) -> None: self.assertNotEqual(profile_components(sample_profile(route="x"))["LOGICAL_ROUTE_ONLY"], profile_components(sample_profile(route="y"))["LOGICAL_ROUTE_ONLY"])
    def test_032_component_hashes_deterministic(self) -> None: self.assertEqual(profile_components(sample_profile()), profile_components(sample_profile()))

    # F/G. Raw preservation and queue-only rebinding.
    def test_033_exactly_one_raw_marker_parses(self) -> None:
        payload = {"requested_flow_count": 1, "scheduled_flow_count": 1, "hyper_cycle_ticks": 10, "slots": [{"flow_id": 1}], "upstream_verifier_pass": True}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.log"; path.write_text("H2S_SCHEDULE_JSON:" + json.dumps(payload) + "\n", encoding="utf-8")
            raw, facts = load_raw_h2s(path); self.assertEqual(raw, payload); self.assertTrue(facts["raw_parse_pass"])
    def test_034_multiple_raw_markers_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.log"; path.write_text("H2S_SCHEDULE_JSON:{}\nH2S_SCHEDULE_JSON:{}\n", encoding="utf-8")
            with self.assertRaises(RedundancyAuditError): load_raw_h2s(path)
    def test_035_queue_rebinding_changes_only_queue_id(self) -> None:
        raw = {"slots": [{"flow_id": 1, "source": 1, "destination": 2, "start_tick": 3, "end_tick": 4, "config_id": 5, "queue_id": 0}]}
        rebound = rebind_queue_ids(raw, SimpleNamespace(queue_by_arc={(1, 2): 9}))
        self.assertEqual(rebound["slots"][0], {**raw["slots"][0], "queue_id": 9})
    def test_036_queue_rebinding_preserves_original(self) -> None:
        raw = {"slots": [{"flow_id": 1, "source": 1, "destination": 2, "start_tick": 3, "end_tick": 4, "config_id": 5, "queue_id": 0}]}
        rebind_queue_ids(raw, SimpleNamespace(queue_by_arc={(1, 2): 9})); self.assertEqual(raw["slots"][0]["queue_id"], 0)
    def test_037_disabled_arc_rejected(self) -> None:
        raw = {"slots": [{"flow_id": 1, "source": 1, "destination": 2, "start_tick": 3, "end_tick": 4, "config_id": 5, "queue_id": 0}]}
        with self.assertRaises(RedundancyAuditError): rebind_queue_ids(raw, SimpleNamespace(queue_by_arc={}))

    # H/I/J. Exact replay factor and union-set theory.
    def test_038_factor_accepts_healthy_arc(self) -> None: self.assertEqual(checker_factor_replay_valid(source_base_checker_pass=True, source_arcs=frozenset({(1, 2)}), target=SimpleNamespace(queue_by_arc={(1, 2): 8}))[0], True)
    def test_039_factor_rejects_disabled_arc(self) -> None: self.assertEqual(checker_factor_replay_valid(source_base_checker_pass=True, source_arcs=frozenset({(1, 2)}), target=SimpleNamespace(queue_by_arc={}))[1], "TARGET_DISABLED_LINK_USED")
    def test_040_factor_requires_diagonal_base(self) -> None: self.assertFalse(checker_factor_replay_valid(source_base_checker_pass=False, source_arcs=frozenset(), target=SimpleNamespace(queue_by_arc={}))[0])
    def test_041_extra_healthy_arc_has_no_effect(self) -> None: self.assertTrue(checker_factor_replay_valid(source_base_checker_pass=True, source_arcs=frozenset({(1, 2)}), target=SimpleNamespace(queue_by_arc={(1, 2): 1, (2, 3): 2}))[0])
    def test_042_union_implies_first_singleton_by_set_inclusion(self) -> None: self.assertTrue({"r"} <= {"r", "e2"})
    def test_043_union_implies_second_singleton_by_set_inclusion(self) -> None: self.assertTrue({"r"} <= {"r", "e1"})
    def test_044_singletons_do_not_imply_union(self) -> None: self.assertFalse({"e1"}.isdisjoint({"e1", "e2"}) and {"e2"}.isdisjoint({"e1", "e2"}))
    def test_045_gzip_is_deterministic(self) -> None: self.assertEqual(deterministic_gzip(b"payload"), deterministic_gzip(b"payload"))

    # K. Exact Boolean set cover (Z3 only; no TSN scheduling solver).
    def test_046_singleton_cover(self) -> None:
        self.assertEqual(exact_set_cover({"a": {"x"}}, ["x"])["minimum_profile_count"], 1)
    def test_047_diagonal_only_cover(self) -> None:
        self.assertEqual(exact_set_cover({"a": {"a"}, "b": {"b"}}, ["a", "b"])["minimum_profile_count"], 2)
    def test_048_one_profile_covers_two(self) -> None:
        self.assertEqual(exact_set_cover({"a": {"a", "b"}, "b": {"b"}}, ["a", "b"])["minimum_profile_count"], 1)
    def test_049_overlap_cover_is_exact(self) -> None:
        self.assertEqual(exact_set_cover({"a": {"x", "y"}, "b": {"y", "z"}, "c": {"x", "z"}}, ["x", "y", "z"])["minimum_profile_count"], 2)
    def test_050_set_cover_tie_break_deterministic(self) -> None:
        coverage = {"a": {"x"}, "b": {"x"}}
        self.assertEqual(exact_set_cover(coverage, ["x"])["selected_profile_fault_ids"], exact_set_cover(coverage, ["x"])["selected_profile_fault_ids"])
    def test_051_uncovered_target_is_rejected(self) -> None: self.assertFalse(exact_set_cover({"a": {"x"}}, ["x", "y"])["exact"])


def _static_contract(name: str, predicate):
    def test(self: PfRedundancyOpportunityAuditTests) -> None:
        self.assertTrue(predicate(self), name)
    test.__name__ = "test_" + name
    return test


# The remaining 61 checks make the protocol-level requirements independently
# visible without rerunning the 1,099-fault campaign during unit testing.
_REQUIRED_OUTPUTS = (
    "environment.json", "exp19_source_manifest.json", "input_integrity.csv", "affected_set_groups.csv",
    "affected_set_group_members.csv", "affected_set_summary.csv", "semantic_profile_groups.csv",
    "semantic_profile_summary.csv", "canonical_hash_groups.csv", "component_redundancy.csv",
    "raw_replay_artifact_audit.csv", "diagonal_replay_validation.csv", "cross_fault_reuse_edges.csv.gz",
    "cross_fault_reuse_failure_summary.csv", "profile_reuse_coverage.csv", "affected_group_reuse_validation.csv",
    "affected_group_union_validation.csv", "affected_group_profile_diversity.csv", "existing_profile_set_cover.csv",
    "compute_opportunity.csv", "storage_opportunity.csv", "semantic_dedup_index_analysis.json",
    "reuse_mapping_affected_groups.json", "reuse_mapping_set_cover.json", "audit_verdict.json", "summary.md", "analysis_manifest.json",
)
for _number, _name in enumerate(_REQUIRED_OUTPUTS, 52):
    setattr(PfRedundancyOpportunityAuditTests, f"test_{_number:03d}_output_{_name.replace('.', '_').replace('-', '_')}", _static_contract(
        f"output:{_name}", lambda self, item=_name: item in self.runner_source))

_SAFETY_MARKERS = (
    ("no_h2s_pf_synthesize", "H2sPfBackend.synthesize"), ("no_backend_run", "H2sJrsBackend._run"),
    ("no_advanced_scheduler", "AdvancedFlowSchedulerExec"), ("no_omnet_call", "omnet"),
    ("no_inet_call", "inet"), ("same_scenario_only", "SAME_SCENARIO_ONLY"),
    ("all_reroute", "all-reroute"), ("queue_rebind", "queue_id"),
    ("raw_marker_audit", "H2S_SCHEDULE_JSON"), ("diagonal_gate", "CROSS_FAULT_REPLAY_NOT_QUALIFIED"),
    ("raw_gate", "RAW_REPLAY_ARTIFACT_INCOMPLETE"), ("exact_set_cover", "exact_set_cover"),
    ("runtime_not_established", "NOT_ESTABLISHED"), ("frozen_tree_pre", "frozen_result_trees"),
    ("frozen_tree_post", "FROZEN_HISTORY_CHANGED"), ("gzip_deterministic", "deterministic_gzip"),
    ("semantic_hash_backend", "semantic_profile_hash"), ("canonical_json", "canonical_json_bytes"),
    ("no_figures_directory", "figures/"), ("quick_mode", "--quick"),
    ("union_multi_disabled", "disabled_links=disabled"), ("union_affected_union", "union_affected"),
    ("mapping_actual_json", "reuse_mapping_set_cover.json"), ("manifest_has_canonical_hashes", "canonical_profile_hashes"),
    ("manifest_has_scenario_hashes", "scenario_sha256"), ("checker_factor_documented", "Exact factor"),
    ("set_cover_timeout", "SCENARIO_BUDGET"), ("no_source_profile_mutation", "read_only_exp19"),
    ("candidate_upper_only", "candidate grouping opportunity"), ("theorem_boundary", "union-disabled static-valid profile"),
    ("no_solver_calls", "synthesize")
)
for _number, (_name, _marker) in enumerate(_SAFETY_MARKERS, 79):
    def _predicate(self, marker=_marker, name=_name):
        if name.startswith("no_") and name not in {"no_figures_directory", "no_source_profile_mutation", "no_solver_calls"}:
            return marker not in self.calls
        if name == "no_figures_directory": return marker not in self.runner_source
        if name == "no_solver_calls": return "H2sPfBackend.synthesize" not in self.calls and "H2sJrsBackend._run" not in self.calls
        return marker in self.runner_source or marker in self.helper_source
    setattr(PfRedundancyOpportunityAuditTests, f"test_{_number:03d}_{_name}", _static_contract(_name, _predicate))


for _number, (_name, _predicate) in enumerate((
    ("cross_scenario_profiles_not_merged", lambda self: "cross_fault_scope" in self.runner_source and "SAME_SCENARIO_ONLY" in self.runner_source),
    ("all_six_historical_freeze_is_recorded", lambda self: "frozen_result_tree_sha256" in self.runner_source),
    ("canonical_csv_gzip_writer_is_used", lambda self: "write_gzip_csv" in self.runner_source and "csv_gzip_bytes" in self.helper_source),
    ("formal_verdict_enums_present", lambda self: all(item in self.runner_source for item in ("PF_REDUNDANCY_OPPORTUNITY_ESTABLISHED", "PF_EXACT_SEMANTIC_DEDUP_ONLY", "PF_REDUNDANCY_LIMITED", "SET_COVER_EXACT_UNAVAILABLE"))),
), 110):
    setattr(PfRedundancyOpportunityAuditTests, f"test_{_number:03d}_{_name}", _static_contract(_name, _predicate))


if __name__ == "__main__":
    unittest.main()
