"""exp18 staged runner; P0 is deliberately a separate hard gate from PF."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from pathlib import Path
from typing import Any

from tools.generate_realistic_tsn_scenarios import FORMAL_SCALES, TOPOLOGIES, build_scenario, digest
from tools.h2s_jrs_backend import (DEFAULT_CANDIDATE_PATHS, DEFAULT_QUANTUM_NS, FORMAL_MEMORY_LIMIT_MB,
    FORMAL_SEED, FORMAL_THREADS, UPSTREAM_COMMIT, H2sJrsBackend)
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/realistic_tsn_pf_cost"
EXECUTABLE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
ORDER = [(scale, topology) for scale in ("M", "L") for topology in TOPOLOGIES]

def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields=sorted({key for row in rows for key in row}); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8",newline="") as handle:
        writer=csv.DictWriter(handle,fields,lineterminator="\n");writer.writeheader();writer.writerows(rows)

def write_json(path: Path,value:Any)->None:
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(canonical_json_bytes(value))

def p0_phase() -> int:
    if not EXECUTABLE.is_file(): raise SystemExit("missing qualified H2S executable")
    for part in ("scenarios","profiles","raw_backend_output","logs"): (RESULTS/part).mkdir(parents=True,exist_ok=True)
    rows=[]; audits=[]; identities=[]
    for scale, topology in ORDER:
        scenario,audit=build_scenario(scale,topology); identifier=scenario["scenario_name"]
        scenario_path=RESULTS/"scenarios"/f"{identifier}.json"; scenario_path.write_bytes(canonical_json_bytes(scenario))
        result=H2sJrsBackend(EXECUTABLE).synthesize(RecoverySynthesisRequest(scenario_path,solver_timeout_s=30,
            route_scope="all-reroute",forwarding_model="stream-aware",output_directory=RESULTS/"raw_backend_output"/identifier/"P0"))
        write_attempt_logs(RESULTS/"logs"/identifier/"P0",result)
        profile_path=""; profile_sha=""
        if result.profile:
            output=RESULTS/"profiles"/f"{identifier}_P0.json";output.write_bytes(canonical_json_bytes(result.profile));profile_path=str(output.relative_to(ROOT));profile_sha=hashlib.sha256(output.read_bytes()).hexdigest()
        rows.append({"scenario_id":identifier,"scale":scale,"topology_type":topology,"P0_status":result.status.value,
            "P0_semantic_valid":result.statistics.get("semantic_valid",False),"P0_scheduled_flow_count":result.statistics.get("scheduled_flow_count",0),
            "P0_scheduled_flow_ratio":result.statistics.get("scheduled_flow_ratio",0),"P0_ms":result.timings_ms.get("total_backend",0),
            "P0_h2s_ms":result.timings_ms.get("h2s_wall",0),"P0_celf_ms":result.timings_ms.get("celf_wall",0),
            "profile_path":profile_path,"profile_sha256":profile_sha,"diagnostic":result.diagnostic})
        audits.append(audit);identities.append({"scenario_id":identifier,"scale":scale,"logical_factory_sha256":audit["logical_factory_sha256"],"workload_sha256":audit["workload_sha256"],"topology_sha256":audit["topology_sha256"]})
        write_csv(RESULTS/"p0_summary.csv",rows);write_csv(RESULTS/"topology_audit.csv",audits);write_csv(RESULTS/"workload_identity.csv",identities)
    write_json(RESULTS/"environment.json",{"platform":platform.platform(),"upstream_commit":UPSTREAM_COMMIT,"backend_quantum_ns":DEFAULT_QUANTUM_NS,"candidate_paths_k":DEFAULT_CANDIDATE_PATHS,"seed":FORMAL_SEED,"threads":FORMAL_THREADS,"timeout_s":30,"memory_limit_mb":FORMAL_MEMORY_LIMIT_MB})
    return 0

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--phase",choices=("p0",),default="p0");args=parser.parse_args()
    return p0_phase()
if __name__=="__main__":raise SystemExit(main())
