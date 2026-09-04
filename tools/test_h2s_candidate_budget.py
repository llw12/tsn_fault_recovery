"""Regression checks for candidate-budget provenance without invoking upstream."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.h2s_jrs_backend import DEFAULT_CANDIDATE_PATHS, H2sAdapterError, prepare_h2s_inputs
from tools.run_h2s_backend_qualification import materialize_case


class CandidateBudgetTest(unittest.TestCase):
    def test_input_manifest_records_default_and_requested_budget(self) -> None:
        with tempfile.TemporaryDirectory(prefix="candidate-budget-") as temporary:
            root = Path(temporary)
            scenario = materialize_case("QH00", root / "case")
            prepare_h2s_inputs(scenario, root / "default")
            prepare_h2s_inputs(scenario, root / "k8", candidate_path_budget=8)
            default = json.loads((root / "default" / "input_manifest.json").read_text())
            k8 = json.loads((root / "k8" / "input_manifest.json").read_text())
            self.assertEqual(default["requested_candidate_route_budget"], DEFAULT_CANDIDATE_PATHS)
            self.assertEqual(k8["requested_candidate_route_budget"], 8)
            self.assertEqual(default["routing_algorithm"], "DIJKSTRA_OVERLAP")

    def test_budget_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="candidate-budget-") as temporary:
            root = Path(temporary)
            scenario = materialize_case("QH00", root / "case")
            with self.assertRaises(H2sAdapterError):
                prepare_h2s_inputs(scenario, root / "bad", candidate_path_budget=0)


if __name__ == "__main__":
    unittest.main()
