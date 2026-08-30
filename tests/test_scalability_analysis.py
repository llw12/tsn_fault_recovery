import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_scalability import (
    CSV_NAMES, NOT_CAPTURED, PNG_NAMES, POLICIES, build_tables, load_campaign, percentile,
    pf_profile_bytes,
    render_summary, sha256, strict_validate, write_csv,
)

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "results/scalability/campaign.json"


class ScalabilityAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_campaign(CAMPAIGN)
        cls.tables = build_tables(cls.data)

    def test_campaign_parser_and_policy_order(self):
        self.assertEqual([row["switch_count"] for row in self.data["scenarios"]], [20,30,40,50])
        self.assertEqual(tuple(self.data["policies"]), POLICIES)

    def test_class_size_stats(self):
        rows = self.tables["class_scaling.csv"][0]
        row = next(item for item in rows if item["scenario"] == "structured20_auto" and item["policy_id"] == "J020")
        self.assertEqual(row["class_size_max"], 6)
        self.assertEqual(row["class_size_p50"], percentile(self.data["scenarios"][0]["policy"]["J020"]["class_sizes"], .5))

    def test_compression_gap(self):
        rows = self.tables["compression_scaling.csv"][0]
        row = next(item for item in rows if item["scenario"] == "structured20_auto" and item["policy_id"] == "J020")
        self.assertAlmostEqual(row["compression_gap"], row["candidate_compression"] - row["realized_compression"])

    def test_coverage_denominators_are_distinct(self):
        row = self.tables["coverage_scaling.csv"][0][0]
        self.assertEqual(row["PF_recovery_coverage"], row["recoverable_fault_count"] / row["candidate_fault_count"])
        self.assertNotEqual(row["shared_fault_coverage"], row["equivalence_profile_coverage"])

    def test_csv_and_summary_are_deterministic(self):
        rows, fields = self.tables["scale_summary.csv"]
        with tempfile.TemporaryDirectory() as directory:
            first=Path(directory)/"a.csv"; second=Path(directory)/"b.csv"
            write_csv(first,rows,fields); write_csv(second,rows,fields)
            self.assertEqual(first.read_bytes(),second.read_bytes())
        self.assertEqual(render_summary(self.data),render_summary(self.data))

    def test_missing_optional_metrics_are_not_captured(self):
        self.assertEqual(self.tables["representative_online_audit.csv"][0][0]["status"], NOT_CAPTURED)
        self.assertEqual(self.tables["memory_scaling.csv"][0][0]["status"], NOT_CAPTURED)
        self.assertEqual(self.tables["failure_status_scaling.csv"][0][0]["FORWARDING_CONFLICT"], NOT_CAPTURED)

    def test_source_campaign_is_immutable_and_sha_known(self):
        before=sha256(CAMPAIGN); load_campaign(CAMPAIGN); build_tables(self.data)
        self.assertEqual(before,sha256(CAMPAIGN))
        self.assertEqual(before,"93ffb1fab5670075fe9d74899844b584481e4b4e68b2dbda9f5371beff31c278")

    def test_plot_underlying_data_is_deterministic(self):
        self.assertEqual(build_tables(self.data),build_tables(self.data))

    def test_analyzer_has_no_simulation_invocation(self):
        source=(ROOT/"scripts/analyze_scalability.py").read_text()
        self.assertNotIn("run_omnet",source); self.assertNotIn("opp_run",source)
        self.assertNotIn("run_scalability_campaign",source)

    def test_no_negative_wall_times_and_valid_compression(self):
        for scenario in self.data["scenarios"]:
            self.assertGreaterEqual(scenario["pf_total_precompute_wall_ms"],0)
            for policy in POLICIES:
                self.assertGreaterEqual(scenario["policy"][policy]["compression"],0)
                self.assertLessEqual(scenario["policy"][policy]["compression"],1)

    def test_strict_validation_detects_missing_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,"missing exp10 analysis artifacts"):
                strict_validate(Path(directory),self.data,sha256(CAMPAIGN))

    def test_pf_storage_derivation_is_cross_policy_consistent(self):
        for scenario in self.data["scenarios"]:
            value=pf_profile_bytes(scenario)
            self.assertGreater(value,0)
            for policy in POLICIES:
                item=scenario["policy"][policy]
                self.assertAlmostEqual(item["profile_bytes"]/(1-item["storage_compression"]),value,delta=1)

    def test_output_table_grains(self):
        self.assertEqual(len(self.tables["profile_scaling.csv"][0]),20)
        self.assertEqual(len(self.tables["class_scaling.csv"][0]),16)
        self.assertEqual(len(self.tables["failure_status_scaling.csv"][0]),16)

    def test_candidate_compression_bounds_realized(self):
        for row in self.tables["compression_scaling.csv"][0]:
            self.assertGreaterEqual(row["candidate_compression"],row["realized_compression"])

    def test_summary_records_provenance_and_missing_metrics(self):
        summary=render_summary(self.data,"analysis-commit-test")
        self.assertIn("ed97466cf46d79a74171833af0ea69114d3fdb48",summary)
        self.assertIn("20260830T133528Z",summary)
        self.assertIn("analysis-commit-test",summary)
        self.assertIn("NOT_CAPTURED",summary)

    def test_all_required_csv_tables_are_built(self):
        self.assertEqual(set(self.tables),set(CSV_NAMES))

    def test_rows_follow_scale_and_policy_order(self):
        rows=self.tables["compression_scaling.csv"][0]
        self.assertEqual([(row["switch_count"],row["policy_id"]) for row in rows[:4]],[(20,p) for p in POLICIES])

    def _complete_temp_output(self,directory: Path):
        for name in CSV_NAMES:
            if name=="compression_scaling.csv":
                (directory/name).write_text("realized_compression,profile_count\n0.5,1\n")
            else: (directory/name).write_text("status\nOK\n")
        for name in PNG_NAMES: (directory/name).write_bytes(b"PNG")
        (directory/"summary.md").write_text("summary\n")
        generated=sorted(list(CSV_NAMES)+list(PNG_NAMES)+["summary.md"])
        manifest={"source_campaign_sha256":sha256(CAMPAIGN),
                  "generated_artifact_sha256":{name:sha256(directory/name) for name in generated}}
        (directory/"analysis_manifest.json").write_text(json.dumps(manifest))

    def test_manifest_hashes_generated_artifacts(self):
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw); self._complete_temp_output(directory)
            strict_validate(directory,self.data,sha256(CAMPAIGN))
            (directory/"summary.md").write_text("tampered\n")
            with self.assertRaisesRegex(ValueError,"hash mismatch"):
                strict_validate(directory,self.data,sha256(CAMPAIGN))

    def test_strict_validation_detects_missing_summary(self):
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw); self._complete_temp_output(directory); (directory/"summary.md").unlink()
            with self.assertRaisesRegex(ValueError,"summary.md"):
                strict_validate(directory,self.data,sha256(CAMPAIGN))

    def test_strict_validation_detects_missing_csv(self):
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw); self._complete_temp_output(directory); (directory/"z3_scaling.csv").unlink()
            with self.assertRaisesRegex(ValueError,"z3_scaling.csv"):
                strict_validate(directory,self.data,sha256(CAMPAIGN))
