"""Regression checks for the read-only exp18b mechanism-diagnosis parser."""
from __future__ import annotations

import unittest

from tools.run_p0_hnf_mechanism_diagnosis import source_rows


class MechanismDiagnosisTest(unittest.TestCase):
    def test_medium_ring_raw_schedule_signatures(self) -> None:
        """The raw attempt classification remains aligned with frozen P0 evidence."""
        _, _, h2s = source_rows("M_RING", "h2s")
        _, _, celf = source_rows("M_RING", "celf")
        self.assertEqual((h2s["scheduled_flow_count"], h2s["requested_flow_count"], h2s["hnf_flow_count"]), (348, 352, 4))
        self.assertEqual((celf["scheduled_flow_count"], celf["requested_flow_count"], celf["hnf_flow_count"]), (348, 352, 4))
        self.assertNotEqual(h2s["hnf_set_sha256"], celf["hnf_set_sha256"])
        self.assertTrue(all(row["flow_completion_class"] == "ZERO_SCHEDULED"
                            for row in h2s["identities"] if row["flow_id"] in h2s["hnf_flow_ids"]))


if __name__ == "__main__":
    unittest.main()
