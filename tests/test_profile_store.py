import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tools.profile_store import (
    MODEL_VERSION, PROFILE_SCHEMA_VERSION, ProfileStoreError, build_store,
    canonical_bytes, profile_content_hash, semantic_profile_hash, store_metrics,
    validate_store, write_json,
)
from tools.scenario_compiler import compile_scenario


ROOT = Path(__file__).resolve().parents[1]


def raw_profile(profile_id="PF_l_s1_s2", interface="eth1"):
    return {
        "schema_version": 1, "scenario_sha256": "replaced", "profile_id": profile_id,
        "logical_routes": [{"flow_id": "TT", "node_path": ["source", "s1", "s3", "s4", "destination"],
                            "link_path": ["l_source_s1", "l_s1_s3", "l_s3_s4", "l_s4_destination"]}],
        "routes": [{"flow_id": "TT", "switch": "s1", "destination": "destination",
                    "interface": interface, "logical_link": "l_s1_s3"}],
        "gate_schedules": [{"gate_path": "s1.eth[1]", "traffic_class": 1, "initially_open": False,
                            "offset_s": 0.0, "durations_s": [0.0001, 0.0009]}],
    }


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.generated = compile_scenario(ROOT / "configs/scenarios/diamond.yaml", self.temp.name)
        self.scenario = json.loads((self.generated / "scenario.json").read_text())
        self.port_map = json.loads((self.generated / "port_map.json").read_text())
        root = self.generated / "profiles/per_failure"
        (root / "raw").mkdir(parents=True)
        profile = raw_profile()
        profile["scenario_sha256"] = self.scenario["scenario_sha256"]
        write_json(root / "raw/l_s1_s2.raw.json", profile)
        second = deepcopy(profile); second["profile_id"] = "PF_l_s2_s4"
        write_json(root / "raw/l_s2_s4.raw.json", second)
        rows = {
            "l_s1_s2": self.row("SAT", ["TT"], "l_s1_s2.raw.json"),
            "l_s1_s3": self.row("NO_AFFECTED_TT", []),
            "l_s2_s4": self.row("SAT", ["TT"], "l_s2_s4.raw.json"),
            "l_s3_s4": self.row("NO_AFFECTED_TT", []),
        }
        write_json(root / "precompute_report.json", {"schema_version": 1,
            "scenario_sha256": self.scenario["scenario_sha256"],
            "total_precompute_wall_s": .012, "faults": rows})
        (self.generated / "profiles/profile0.json").write_text('{"profile_id":"P0"}\n')
        self.store = build_store(self.generated)
        self.path = root / "store.json"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def row(status, affected, raw=""):
        return {"status": status, "affected_flows": affected, "route_solver_wall_us": 1,
                "smt_solver_wall_us": 2, "profile_compile_wall_us": 3,
                "serialization_wall_us": 4, "total_precompute_wall_us": 10,
                "objective": 12 if status == "SAT" else -1, "profile_file_raw": raw,
                "diagnostic": "fixture"}

    def rewrite_store(self, mutate):
        value = json.loads(self.path.read_text()); mutate(value); write_json(self.path, value)

    def test_all_candidate_faults_iterated(self):
        self.assertEqual(list(self.store["faults"]), self.scenario["fault_candidates"])

    def test_no_affected_classification_retained(self):
        self.assertEqual(self.store["faults"]["l_s1_s3"]["status"], "NO_AFFECTED_TT")

    def test_no_route_classification_retained(self):
        self.rewrite_store(lambda value: value["faults"]["l_s1_s2"].update(status="NO_ROUTE"))
        runtime_path = self.path.parent / "runtime_store.json"; runtime = json.loads(runtime_path.read_text()); runtime["faults"]["l_s1_s2"]["status"] = "NO_ROUTE"; write_json(runtime_path, runtime)
        self.assertEqual(validate_store(self.path, self.scenario, self.port_map)["faults"]["l_s1_s2"]["status"], "NO_ROUTE")

    def test_unsat_classification_retained(self):
        self.rewrite_store(lambda value: value["faults"]["l_s1_s2"].update(status="UNSAT"))
        runtime_path = self.path.parent / "runtime_store.json"; runtime = json.loads(runtime_path.read_text()); runtime["faults"]["l_s1_s2"]["status"] = "UNSAT"; write_json(runtime_path, runtime)
        self.assertEqual(validate_store(self.path, self.scenario, self.port_map)["faults"]["l_s1_s2"]["status"], "UNSAT")

    def test_sat_profile_serialized_per_fault(self):
        self.assertTrue((self.path.parent / "profiles/l_s1_s2.json").is_file())

    def test_identical_semantics_not_deduplicated(self):
        self.assertEqual(store_metrics(self.path, self.generated / "profiles/profile0.json")["recovery_profile_count"], 2)

    def test_identical_semantics_have_same_hash(self):
        self.assertEqual(self.store["faults"]["l_s1_s2"]["semantic_profile_hash"], self.store["faults"]["l_s2_s4"]["semantic_profile_hash"])

    def test_semantic_hash_ignores_metadata(self):
        profile = json.loads((self.path.parent / "profiles/l_s1_s2.json").read_text())
        changed = deepcopy(profile); changed["fault_id"] = "other"; changed["profile_id"] = "other"; changed["smt_solver_wall_us_precompute"] = 999
        self.assertEqual(semantic_profile_hash(profile), semantic_profile_hash(changed))

    def test_semantic_hash_detects_forwarding_change(self):
        profile = json.loads((self.path.parent / "profiles/l_s1_s2.json").read_text())
        changed = deepcopy(profile); changed["routes"][0]["interface"] = "eth9"
        self.assertNotEqual(semantic_profile_hash(profile), semantic_profile_hash(changed))

    def test_profile_content_hash_ignores_self_field(self):
        profile = json.loads((self.path.parent / "profiles/l_s1_s2.json").read_text())
        changed = deepcopy(profile); changed["profile_sha256"] = "anything"
        self.assertEqual(profile_content_hash(profile), profile_content_hash(changed))

    def test_stale_scenario_hash_rejected(self):
        self.rewrite_store(lambda value: value.update(scenario_sha256="stale"))
        with self.assertRaisesRegex(ProfileStoreError, "scenario_sha256"): validate_store(self.path, self.scenario, self.port_map)

    def test_stale_solver_hash_rejected(self):
        self.rewrite_store(lambda value: value.update(solver_config_hash="stale"))
        with self.assertRaisesRegex(ProfileStoreError, "solver_config_hash"): validate_store(self.path, self.scenario, self.port_map)

    def test_stale_port_map_hash_rejected(self):
        self.rewrite_store(lambda value: value.update(port_map_sha256="stale"))
        with self.assertRaisesRegex(ProfileStoreError, "port_map_sha256"): validate_store(self.path, self.scenario, self.port_map)

    def test_corrupted_profile_file_rejected(self):
        with (self.path.parent / "profiles/l_s1_s2.json").open("a") as handle: handle.write(" ")
        with self.assertRaisesRegex(ProfileStoreError, "file hash"): validate_store(self.path, self.scenario, self.port_map)

    def test_corrupted_profile_content_hash_rejected(self):
        profile_path = self.path.parent / "profiles/l_s1_s2.json"
        profile = json.loads(profile_path.read_text()); profile["profile_sha256"] = "stale"; write_json(profile_path, profile)
        store = json.loads(self.path.read_text()); store["faults"]["l_s1_s2"]["profile_file_sha256"] = __import__('hashlib').sha256(profile_path.read_bytes()).hexdigest(); write_json(self.path, store)
        with self.assertRaisesRegex(ProfileStoreError, "content hash"): validate_store(self.path, self.scenario, self.port_map)

    def test_missing_store_fails_fast(self):
        with self.assertRaisesRegex(ProfileStoreError, "run precompute_profiles.py first"): validate_store(self.path.parent / "missing.json", self.scenario, self.port_map)

    def test_bad_store_schema_rejected(self):
        self.rewrite_store(lambda value: value.update(schema_version=99))
        with self.assertRaisesRegex(ProfileStoreError, "schema version"): validate_store(self.path, self.scenario, self.port_map)

    def test_bad_profile_schema_rejected(self):
        self.rewrite_store(lambda value: value.update(profile_schema_version=99))
        with self.assertRaisesRegex(ProfileStoreError, "schema version"): validate_store(self.path, self.scenario, self.port_map)

    def test_bad_strategy_rejected(self):
        self.rewrite_store(lambda value: value.update(strategy="cluster"))
        with self.assertRaisesRegex(ProfileStoreError, "strategy"): validate_store(self.path, self.scenario, self.port_map)

    def test_candidate_set_mismatch_rejected(self):
        def remove(value): value["faults"].pop("l_s3_s4")
        self.rewrite_store(remove)
        with self.assertRaisesRegex(ProfileStoreError, "candidate faults/order"): validate_store(self.path, self.scenario, self.port_map)

    def test_storage_bytes_deterministic(self):
        first = store_metrics(self.path, self.generated / "profiles/profile0.json")
        second = store_metrics(self.path, self.generated / "profiles/profile0.json")
        self.assertEqual(first, second)

    def test_p0_and_recovery_metrics_are_separate(self):
        metrics = store_metrics(self.path, self.generated / "profiles/profile0.json")
        self.assertEqual(metrics["initial_profile_count"], 1); self.assertEqual(metrics["recovery_profile_count"], 2)

    def test_total_storage_is_explicit_sum(self):
        metrics = store_metrics(self.path, self.generated / "profiles/profile0.json")
        self.assertEqual(metrics["total_profile_storage_bytes"], metrics["initial_profile_storage_bytes"] + metrics["recovery_profile_storage_bytes"] + metrics["profile_store_metadata_bytes"])

    def test_runtime_store_preloads_every_fault(self):
        runtime = json.loads((self.path.parent / "runtime_store.json").read_text())
        self.assertEqual(list(runtime["faults"]), self.scenario["fault_candidates"])
        self.assertIn("profile", runtime["faults"]["l_s1_s2"])

    def test_runtime_no_action_has_no_profile_blob(self):
        runtime = json.loads((self.path.parent / "runtime_store.json").read_text())
        self.assertNotIn("profile", runtime["faults"]["l_s1_s3"])

    def test_corrupted_runtime_profile_rejected(self):
        runtime_path = self.path.parent / "runtime_store.json"; runtime = json.loads(runtime_path.read_text())
        runtime["faults"]["l_s1_s2"]["profile"]["routes"][0]["interface"] = "eth9"; write_json(runtime_path, runtime)
        with self.assertRaisesRegex(ProfileStoreError, "runtime embedded profile"): validate_store(self.path, self.scenario, self.port_map)

    def test_canonical_encoding_is_stable(self):
        self.assertEqual(canonical_bytes({"b": 1, "a": 2}), canonical_bytes({"a": 2, "b": 1}))

    def test_model_version_recorded(self):
        self.assertEqual(self.store["model_version"], MODEL_VERSION)

    def test_profile_schema_recorded(self):
        profile = json.loads((self.path.parent / "profiles/l_s1_s2.json").read_text())
        self.assertEqual(profile["profile_schema_version"], PROFILE_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
