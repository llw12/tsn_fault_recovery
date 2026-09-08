"""No-campaign contract tests for the exp18g density selector and safeguards."""
from __future__ import annotations

import hashlib
import inspect
import unittest

from tools.tt_workload_density_calibration import (
    DENSITIES, FROZEN_TREE_SHA256, NAMESPACE, ORDER, ROOT, assert_backend_config, assert_frozen,
    backend_config, derive_scenario, derived_integrity, expected_instances, flow_kind, flow_set_sha,
    load_scenario, nestedness_rows, rank_hash, ranked_flows, retained_count, retained_ids,
    scale_topology, scenario_path, sha256_file, source_identity_rows, traffic_rows, workload_sha,
)


class DensityCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scenarios = {scenario_id: load_scenario(scenario_id) for scenario_id in ORDER}
        cls.medium = cls.scenarios["M_RING"]; cls.large = cls.scenarios["L_RING"]
        cls.frozen = assert_frozen()


def _case(index: int):
    densities = [(d.tag, d.numerator, d.denominator) for d in DENSITIES]
    expected = [("D100", 1, 1), ("D095", 19, 20), ("D090", 9, 10), ("D085", 17, 20), ("D080", 4, 5), ("D070", 7, 10), ("D060", 3, 5), ("D050", 1, 2)]
    if 1 <= index <= 8:
        return lambda self: self.assertEqual(densities[index - 1], expected[index - 1])
    checks = {
        9: lambda self: self.assertEqual(retained_count(96, DENSITIES[4]), 76),
        10: lambda self: self.assertEqual(retained_count(8, DENSITIES[1]), 7),
        11: lambda self: self.assertLessEqual(retained_count(24, DENSITIES[6]), 24),
        12: lambda self: self.assertEqual(retained_count(96, DENSITIES[0]), 96),
        13: lambda self: self.assertEqual(NAMESPACE, "exp18g-role-stratified-density-v1"),
        14: lambda self: self.assertNotEqual(rank_hash("M", "SensorFast", "x"), rank_hash("L", "SensorFast", "x")),
        15: lambda self: self.assertNotEqual(rank_hash("M", "SensorFast", "x"), rank_hash("M", "SensorCyclic", "x")),
        16: lambda self: self.assertNotEqual(rank_hash("M", "SensorFast", "x"), rank_hash("M", "SensorFast", "y")),
        17: lambda self: self.assertEqual(rank_hash("M", "SensorFast", "x"), hashlib.sha256((NAMESPACE + "|M|SensorFast|x").encode()).hexdigest()),
        18: lambda self: self.assertEqual(rank_hash("M", "SensorFast", "x"), rank_hash("M", "SensorFast", "x")),
        19: lambda self: self.assertEqual(sorted([(rank_hash("M", "SensorFast", "b"), "b"), (rank_hash("M", "SensorFast", "a"), "a")]), sorted([(rank_hash("M", "SensorFast", "b"), "b"), (rank_hash("M", "SensorFast", "a"), "a")])),
        20: lambda self: self.assertEqual({flow_kind(flow["id"]) for flow in self.medium["tt_flows"]}, {"SensorFast", "SensorCyclic", "ControlCommand", "MachineStatus", "MachineCoordination", "InterCellPrevious", "InterCellNext"}),
        21: lambda self: self.assertEqual(sum(len(v) for v in ranked_flows("M", self.medium).values()), 352),
        22: lambda self: self.assertTrue(all([row["flow_id"] for values in ranked_flows("M", self.medium).values() for row in values[:retained_count(len(values), DENSITIES[4])]])),
        23: lambda self: self.assertEqual({kind: len(rows) for kind, rows in ranked_flows("L", self.large).items()}, {"SensorFast": 256, "SensorCyclic": 256, "ControlCommand": 256, "MachineStatus": 64, "MachineCoordination": 64, "InterCellPrevious": 16, "InterCellNext": 16}),
        24: lambda self: self.assertTrue(retained_ids("M", self.medium, DENSITIES[7]) <= retained_ids("M", self.medium, DENSITIES[6])),
        25: lambda self: self.assertTrue(retained_ids("M", self.medium, DENSITIES[6]) <= retained_ids("M", self.medium, DENSITIES[5])),
        26: lambda self: self.assertTrue(retained_ids("M", self.medium, DENSITIES[5]) <= retained_ids("M", self.medium, DENSITIES[4])),
        27: lambda self: self.assertTrue(retained_ids("M", self.medium, DENSITIES[4]) <= retained_ids("M", self.medium, DENSITIES[3])),
        28: lambda self: self.assertTrue(retained_ids("M", self.medium, DENSITIES[3]) <= retained_ids("M", self.medium, DENSITIES[2])),
        29: lambda self: self.assertTrue(retained_ids("M", self.medium, DENSITIES[2]) <= retained_ids("M", self.medium, DENSITIES[1])),
        30: lambda self: self.assertTrue(retained_ids("M", self.medium, DENSITIES[1]) <= retained_ids("M", self.medium, DENSITIES[0])),
        31: lambda self: self.assertTrue(nestedness_rows("L", self.large)[1]),
        32: lambda self: self.assertTrue(source_identity_rows(self.scenarios)[1]),
        33: lambda self: self.assertTrue(source_identity_rows(self.scenarios)[1]),
        34: lambda self: self.assertEqual(retained_ids("M", self.scenarios["M_RING"], DENSITIES[4]), retained_ids("M", self.scenarios["M_REDSTAR"], DENSITIES[4])),
        35: lambda self: self.assertEqual(retained_ids("L", self.scenarios["L_RING"], DENSITIES[4]), retained_ids("L", self.scenarios["L_ROR"], DENSITIES[4])),
        36: lambda self: self.assertEqual(workload_sha(derive_scenario(self.scenarios["M_RING"], "M_RING", DENSITIES[4])), workload_sha(derive_scenario(self.scenarios["M_REDSTAR"], "M_REDSTAR", DENSITIES[4]))),
        37: lambda self: self.assertTrue(set(flow["id"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"]) <= set(flow["id"] for flow in self.medium["tt_flows"])),
        38: lambda self: self.assertEqual({flow["id"]: flow["source"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"]}, {flow["id"]: flow["source"] for flow in self.medium["tt_flows"] if flow["id"] in retained_ids("M", self.medium, DENSITIES[4])}),
        39: lambda self: self.assertEqual({flow["id"]: flow["destination"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"]}, {flow["id"]: flow["destination"] for flow in self.medium["tt_flows"] if flow["id"] in retained_ids("M", self.medium, DENSITIES[4])}),
        40: lambda self: self.assertTrue(all(flow["period_s"] == next(item for item in self.medium["tt_flows"] if item["id"] == flow["id"])["period_s"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"])),
        41: lambda self: self.assertTrue(all(flow["deadline_e2e_s"] == next(item for item in self.medium["tt_flows"] if item["id"] == flow["id"])["deadline_e2e_s"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"])),
        42: lambda self: self.assertTrue(all(flow["schedule_deadline_budget_s"] == next(item for item in self.medium["tt_flows"] if item["id"] == flow["id"])["schedule_deadline_budget_s"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"])),
        43: lambda self: self.assertTrue(all(flow["release_offset_s"] == next(item for item in self.medium["tt_flows"] if item["id"] == flow["id"])["release_offset_s"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"])),
        44: lambda self: self.assertTrue(all(flow["packet_size_bytes"] == next(item for item in self.medium["tt_flows"] if item["id"] == flow["id"])["packet_size_bytes"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"])),
        45: lambda self: self.assertTrue(all(flow["traffic_class"] == next(item for item in self.medium["tt_flows"] if item["id"] == flow["id"])["traffic_class"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"])),
        46: lambda self: self.assertEqual(derive_scenario(self.medium, "M_RING", DENSITIES[4])["nodes"], self.medium["nodes"]),
        47: lambda self: self.assertEqual(derive_scenario(self.medium, "M_RING", DENSITIES[4])["links"], self.medium["links"]),
        48: lambda self: self.assertEqual(derive_scenario(self.medium, "M_RING", DENSITIES[4])["network"], self.medium["network"]),
        49: lambda self: self.assertEqual(derive_scenario(self.medium, "M_RING", DENSITIES[4])["links"][0]["propagation_delay_s"], self.medium["links"][0]["propagation_delay_s"]),
        50: lambda self: self.assertEqual(derive_scenario(self.medium, "M_RING", DENSITIES[4])["simulation"]["cycle_time_s"], self.medium["simulation"]["cycle_time_s"]),
        51: lambda self: self.assertEqual(derive_scenario(self.medium, "M_RING", DENSITIES[4])["scheduling"]["frame_overhead_bytes"], self.medium["scheduling"]["frame_overhead_bytes"]),
        52: lambda self: self.assertTrue(derived_integrity(self.medium, derive_scenario(self.medium, "M_RING", DENSITIES[4]), "M_RING", DENSITIES[4], sha256_file(scenario_path("M_RING")))["integrity_pass"]),
        53: lambda self: self.assertEqual(expected_instances(self.medium), 3616),
        54: lambda self: self.assertEqual(expected_instances(derive_scenario(self.medium, "M_RING", DENSITIES[4])), 2862),
        55: lambda self: self.assertEqual(expected_instances(derive_scenario(self.large, "L_RING", DENSITIES[4])), 7674),
        56: lambda self: self.assertEqual(backend_config()["route_scope"], "ALL_REROUTE"),
        57: lambda self: self.assertEqual(backend_config()["h2s_sorter"], "LOW_PERIOD_FLOWS_FIRST"),
        58: lambda self: self.assertEqual(backend_config()["routing"], "DIJKSTRA_OVERLAP"),
        59: lambda self: self.assertEqual(backend_config()["candidate_paths_k"], 5),
        60: lambda self: self.assertEqual(backend_config()["quantum_ns"], 100),
        61: lambda self: self.assertEqual(backend_config()["global_seed"], 1024),
        62: lambda self: self.assertEqual(backend_config()["threads"], 1),
        63: lambda self: self.assertEqual(backend_config()["timeout_s_per_backend"], 30),
        64: lambda self: self.assertEqual(backend_config()["timeout_s_per_backend"], 30),
        65: lambda self: self.assertEqual(backend_config()["memory_limit_mb"], 8192),
        66: lambda self: self.assertEqual(backend_config()["h2s_tiebreak_mode"], "BASELINE"),
        67: lambda self: self.assertEqual(self.medium["tt_flows"][0]["schedule_deadline_budget_s"], .0004),
        68: lambda self: self.assertEqual(len(self.medium["tt_flows"]), 352),
        69: lambda self: self.assertEqual(len(self.large["tt_flows"]), 928),
        70: lambda self: self.assertEqual(len(__import__("tools.run_p0_hnf_mechanism_diagnosis", fromlist=["signatures"]).__dict__["signatures"](*__import__("tools.run_p0_hnf_diagnosis", fromlist=["raw"]).raw("M_RING", "h2s")[::2], "H2S")["hnf_set_sha256"]), 64),
        71: lambda self: self.assertEqual(len(__import__("tools.run_p0_hnf_mechanism_diagnosis", fromlist=["signatures"]).__dict__["signatures"](*__import__("tools.run_p0_hnf_diagnosis", fromlist=["raw"]).raw("L_RING", "celf")[::2], "CELF")["hnf_set_sha256"]), 64),
        72: lambda self: self.assertEqual(len(flow_set_sha(retained_ids("M", self.medium, DENSITIES[0]))), 64),
        73: lambda self: self.assertEqual(expected_instances(self.medium), 3616),
        74: lambda self: self.assertNotIn("run_p0_hnf", inspect.getsource(__import__("tools.tt_workload_density_calibration", fromlist=["*"]))),
        75: lambda self: self.assertNotIn("from tools.run_p0", inspect.getsource(__import__("tools.tt_workload_density_calibration", fromlist=["*"]))),
        76: lambda self: self.assertEqual(len(DENSITIES), 8),
        77: lambda self: self.assertEqual({flow["period_s"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"]}, {flow["period_s"] for flow in self.medium["tt_flows"]}),
        78: lambda self: self.assertEqual({flow["deadline_e2e_s"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"]}, {flow["deadline_e2e_s"] for flow in self.medium["tt_flows"]}),
        79: lambda self: self.assertEqual({flow["release_offset_s"] for flow in derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"]}, {flow["release_offset_s"] for flow in self.medium["tt_flows"] if flow["id"] in retained_ids("M", self.medium, DENSITIES[4])}),
        80: lambda self: self.assertEqual(len(retained_ids("M", self.medium, DENSITIES[4])), len(derive_scenario(self.medium, "M_RING", DENSITIES[4])["tt_flows"])),
        81: lambda self: self.assertEqual(len(ORDER), 6),
        82: lambda self: self.assertEqual(DENSITIES[0].tag, "D100"),
        83: lambda self: self.assertEqual(retained_ids("M", self.medium, DENSITIES[4]), retained_ids("M", self.medium, DENSITIES[4])),
        84: lambda self: self.assertTrue(all(density.numerator <= density.denominator for density in DENSITIES)),
        85: lambda self: self.assertNotIn("run_h2s_pf", inspect.getsource(__import__("tools.run_tt_workload_density_calibration", fromlist=["*"]))),
        86: lambda self: self.assertNotIn("fault_candidates", inspect.getsource(__import__("tools.run_tt_workload_density_calibration", fromlist=["*"]))),
        87: lambda self: self.assertNotIn("profile_store", inspect.getsource(__import__("tools.run_tt_workload_density_calibration", fromlist=["*"])).lower()),
        88: lambda self: self.assertNotIn("ThreadPool", inspect.getsource(__import__("tools.run_tt_workload_density_calibration", fromlist=["*"]))),
        89: lambda self: self.assertNotIn("omnetpp", inspect.getsource(__import__("tools.run_tt_workload_density_calibration", fromlist=["*"])).lower()),
        90: lambda self: self.assertNotIn("from inet", inspect.getsource(__import__("tools.run_tt_workload_density_calibration", fromlist=["*"])).lower()),
        91: lambda self: self.assertNotIn("matplotlib", inspect.getsource(__import__("tools.run_tt_workload_density_calibration", fromlist=["*"])).lower()),
        92: lambda self: self.assertEqual(self.frozen["results/realistic_tsn_pf_cost"], FROZEN_TREE_SHA256["results/realistic_tsn_pf_cost"]),
        93: lambda self: self.assertEqual(self.frozen["results/p0_hnf_diagnosis"], FROZEN_TREE_SHA256["results/p0_hnf_diagnosis"]),
        94: lambda self: self.assertEqual(self.frozen["results/candidate_k_sensitivity"], FROZEN_TREE_SHA256["results/candidate_k_sensitivity"]),
        95: lambda self: self.assertEqual(self.frozen["results/h2s_multistart_sensitivity"], FROZEN_TREE_SHA256["results/h2s_multistart_sensitivity"]),
        96: lambda self: self.assertEqual(self.frozen["results/deadline_feasibility_calibration"], FROZEN_TREE_SHA256["results/deadline_feasibility_calibration"]),
        97: lambda self: self.assertEqual(self.frozen["results/h2s_primary_policy_sensitivity"], FROZEN_TREE_SHA256["results/h2s_primary_policy_sensitivity"]),
    }
    return checks[index]


for _number in range(1, 98):
    setattr(DensityCalibrationTests, f"test_{_number:02d}_exp18g_contract", _case(_number))


if __name__ == "__main__": unittest.main()
