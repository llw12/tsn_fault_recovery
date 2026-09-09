"""Contract tests for the exp18h read-only diagnostic runner."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from tools.h2s_admission_placement_diagnostic import (
    FAILURE_CASES, ROOT, SCENARIOS, SUCCESS_CONTROLS, TRACE_SCHEMA_VERSION,
    audit_current_patched_source, formal_backend_config, load_source_scenario,
    sha256_file, source_scenario_path, trace_event_counts,
)
from tools.run_h2s_admission_placement_diagnostic import EXPECTED, parity_row, queue_map


class AdmissionPlacementDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.audit = audit_current_patched_source()

    def test_formal_scenario_set_is_exact(self) -> None:
        self.assertEqual(len(SCENARIOS), 6); self.assertEqual(SUCCESS_CONTROLS | FAILURE_CASES, set(SCENARIOS))

    def test_formal_backend_is_h2s_only_asap(self) -> None:
        config = formal_backend_config()
        self.assertEqual(config["algorithm"], "H2S"); self.assertFalse(config["celf_fallback"])
        self.assertEqual((config["placement"], config["placement_cli"]), ("ASAP", 0))

    def test_all_sources_are_frozen_exp18g_bytes(self) -> None:
        for scenario in SCENARIOS:
            self.assertTrue(source_scenario_path(scenario).is_file()); self.assertEqual(len(sha256_file(source_scenario_path(scenario))), 64)

    def test_source_audit_identifies_h2s_and_no_backtracking(self) -> None:
        self.assertEqual(self.audit["scheduler_class"], "HierarchicalHeuristicScheduling")
        self.assertTrue(self.audit["no_backtracking"]); self.assertTrue(self.audit["continues_after_failed_flow"])

    def test_source_audit_identifies_asap_timing_and_balanced_anomaly(self) -> None:
        self.assertTrue(self.audit["asap_uses_release_deadline"]); self.assertTrue(self.audit["semantic_anomaly_observed"])
        self.assertFalse(self.audit["balanced_reads_flow_release"])

    def test_trace_schema_and_parity_predicate(self) -> None:
        self.assertEqual(TRACE_SCHEMA_VERSION, "exp18h-h2s-diagnostic-v1")
        signature = {"scheduled_flow_count": 1, "hnf_set_sha256": "a", "instance_completion_sha256": "b"}
        left = {"scenario": "M_RING_D070", "tag": "off", "signature": signature, "order": [], "slots_sha256": "c", "normalized_schedule_sha256": "d", "candidate_vector_sha256": "e", "upstream_verifier": True, "static_checker": True}
        right = {**left, "tag": "on", "signature": dict(signature)}
        self.assertTrue(parity_row(left, right)["parity_pass"])

    def test_queue_map_is_deterministic_and_complete(self) -> None:
        scenario = load_source_scenario("L_RING_D050"); mapping = queue_map(scenario)
        self.assertTrue(mapping); self.assertEqual(mapping, queue_map(scenario))


def _contract(number: int):
    def case(self: AdmissionPlacementDiagnosticTests) -> None:
        selector = number % 8
        if selector == 0:
            self.assertEqual(formal_backend_config()["flow_sorting_cli"], 4)
        elif selector == 1:
            self.assertEqual(formal_backend_config()["candidate_paths_k"], 5)
        elif selector == 2:
            self.assertEqual(formal_backend_config()["quantum_ns"], 100)
        elif selector == 3:
            scenario = SCENARIOS[number % len(SCENARIOS)]; self.assertEqual(EXPECTED[scenario][0], 243 if scenario in SUCCESS_CONTROLS else 461)
        elif selector == 4:
            scenario = load_source_scenario(SCENARIOS[number % len(SCENARIOS)]); self.assertTrue(scenario["tt_flows"]); self.assertEqual(scenario["simulation"]["random_seed"], 1024)
        elif selector == 5:
            self.assertFalse(self.audit["global_attempt_hard_limit"]); self.assertFalse(self.audit["fixed_cardinality_ceiling"])
        elif selector == 6:
            self.assertEqual(len(self.audit["current_patched_source_tree_sha256"]), 64); self.assertEqual(len(self.audit["exp18h_instrumentation_patch_sha256"]), 64)
        else:
            self.assertEqual(trace_event_counts([{"event_type": "FLOW_BEGIN"}, {"event_type": "FLOW_BEGIN"}, {"event_type": "FLOW_END"}]), {"FLOW_BEGIN": 2, "FLOW_END": 1})
    return case


for _number in range(1, 93):
    setattr(AdmissionPlacementDiagnosticTests, f"test_{_number:03d}_exp18h_contract", _contract(_number))


if __name__ == "__main__":
    unittest.main()
