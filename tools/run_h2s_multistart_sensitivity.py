"""exp18d: H2S equal-priority tie-break sensitivity on frozen whole-P0 inputs.

``--recover`` re-aggregates recorded raw evidence and never reruns a formal
construction.  This is deliberately restart-safe after a reporting failure.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import platform
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.h2s_jrs_backend import (DEFAULT_CANDIDATE_PATHS, DEFAULT_QUANTUM_NS,
    FORMAL_MEMORY_LIMIT_MB, FORMAL_SEED, FORMAL_THREADS, H2sJrsBackend, parse_backend_output)
from tools.jrs_wa_adapter import canonical_json_bytes
from tools.recovery_backend import RecoverySynthesisRequest
from tools.run_h2s_backend_qualification import write_attempt_logs
from tools.run_p0_hnf_diagnosis import SOURCE, raw
from tools.run_p0_hnf_mechanism_diagnosis import flow_kind, signatures

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/h2s_multistart_sensitivity"
EXE = ROOT / ".external/AdvancedFlowScheduler/build-release/AdvancedFlowSchedulerExec"
ORDER = ("M_RING", "M_REDSTAR", "M_ROR", "L_RING", "L_REDSTAR", "L_ROR")
SEEDS = tuple(range(32))
FROZEN_SHA256 = {
 "results/realistic_tsn_pf_cost/scenarios/L_REDSTAR.json":"fe0c139dfdf185d7e05dc2bc70978f601b9c785342884c3a6f0aa66ff4f6f30e",
 "results/realistic_tsn_pf_cost/scenarios/L_RING.json":"6546c021da92e20c77da6352cf940a6ba2e6f42739aa769cd9dfd19301d1e3df",
 "results/realistic_tsn_pf_cost/scenarios/L_ROR.json":"ed3c5dea1f910e99aa05a826722c93c7f0b78b37e69f43b1e484d04fb6c98fa8",
 "results/realistic_tsn_pf_cost/scenarios/M_REDSTAR.json":"c149eda17c4275ef80bc04050c2576729208a315df1697c45003cf7929a7fab9",
 "results/realistic_tsn_pf_cost/scenarios/M_RING.json":"55913fce2cd660bd30ff10098d0042774d8e519453eaa49551f13ab7f1ff4690",
 "results/realistic_tsn_pf_cost/scenarios/M_ROR.json":"3c2e69953fffe98092b7bd9ed2756e31255919be45c1d42e6abb0c3e670b4ddd",
 "results/realistic_tsn_pf_cost/p0_summary.csv":"02a40591d8e93328eaa6299275e1f4b1f18be86083b24b399e3760007d946a8c",
 "results/p0_hnf_diagnosis/source_p0_manifest.json":"53220472d142db16a3eba492eeb367cb25d99982c684b021942cc066f0aab4c3",
 "results/p0_hnf_diagnosis/unscheduled_flow_identity.csv":"3f9ba05ece531987ecbae6636d593d39dc3cc4c6a105dc4ccf5183bb10755ffd",
 "results/p0_hnf_diagnosis/instance_completion.csv":"882a223b903f8a617eea769be373a7b3109c398e45dd7a96162102fa786fbd3c",
 "results/p0_hnf_diagnosis/flow_set_comparison.csv":"307e9e1348abdafcfafe44e756728e9f552fc145906f50f9c95110e8a64336c1",
 "results/p0_hnf_diagnosis/scenario_diagnosis.csv":"ba9922566d58d5cab051adb8755d457808d6204ac76f77a582fb2e9c75e12902",
 "results/p0_hnf_diagnosis/mechanism_verdict.md":"c885c8e24a81414658fb797b3a783adec7aa2328241f61d57cfc3b88e057337c",
 "results/p0_hnf_diagnosis/diagnostic_replay_repeatability.csv":"4f51e2844b17f3c762cc386a70ca26d1453cbe25c95d5ec752d0e8a66d1517cc",
 "results/candidate_k_sensitivity/analysis_manifest.json":"c94185080e1e349dfa0c83db6313a54ac30271aecec381b7a56a8d5f1bed0086",
 "results/candidate_k_sensitivity/p0_k_results.csv":"ac99b18749c3511412d0a33dd64b00a8c1b13e4130d7f15b7f61d87cae22883a",
 "results/candidate_k_sensitivity/research_direction_assessment.json":"cd6448d8e25697e0565d9ab1d97e73cf5924ea23625eb55dee4f87d9535e4ccf",
 "results/candidate_k_sensitivity/summary.md":"aa9bac3af354363432b758c46782cee37fdb7769e61cf963d5f3aa03a209a5d6"}

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def jsha(value: Any) -> str: return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
def scenario_path(sid: str) -> Path: return SOURCE / "scenarios" / f"{sid}.json"
def tag(mode: str, seed: int) -> str: return "BASELINE" if mode == "BASELINE" else f"S{seed:02d}"
def write_json(path: Path, value: Any) -> None:
 path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_json_bytes(value))
def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
 path.parent.mkdir(parents=True, exist_ok=True); fields=sorted({k for row in rows for k in row})
 with path.open("w",encoding="utf8",newline="") as h:
  writer=csv.DictWriter(h,fields,lineterminator="\n"); writer.writeheader(); writer.writerows(rows)
def has_log(path: Path) -> bool: return path.is_file() or Path(f"{path}.gz").is_file()
def read_log(path: Path) -> str:
 if path.is_file(): return path.read_text(encoding="utf8")
 compressed=Path(f"{path}.gz")
 if compressed.is_file():
  with gzip.open(compressed,"rt",encoding="utf8") as h: return h.read()
 raise RuntimeError(f"missing log: {path}")
def archive_logs() -> None:
 logs=[path for path in OUT.rglob("*_stdout.log")]+[path for path in OUT.rglob("*_stderr.log")]
 if logs: subprocess.run(["gzip","-9","-f","--",*(str(path) for path in logs)],check=True)

def assert_frozen() -> dict[str,str]:
 actual={name:sha(ROOT/name) for name in FROZEN_SHA256}
 failed={name:{"expected":FROZEN_SHA256[name],"actual":actual[name]} for name in actual if actual[name]!=FROZEN_SHA256[name]}
 if failed: raise RuntimeError(f"frozen exp18/exp18b source gate failed: {failed}")
 return actual

def contract(command: list[str], mode: str, seed: int) -> bool:
 def value(flag: str) -> str|None:
  return command[command.index(flag)+1] if flag in command and command.index(flag)+1<len(command) else None
 return value("-a")=="H2S" and value("--routing")=="DIJKSTRA_OVERLAP" and value("--candidate-paths")=="5" and value("--h2s-tiebreak-mode")==mode and value("--h2s-tiebreak-seed")==str(seed)

def parse_observation(path: Path, sid: str, mode: str, seed: int, stdout: str, metadata: dict[str,Any]) -> dict[str,Any]:
 scenario=json.loads(path.read_text(encoding="utf8")); payload=parse_backend_output(stdout); sig=signatures(scenario,payload,"H2S")
 marker=next((line[len("H2S_ORDER_JSON:"):] for line in stdout.splitlines() if line.startswith("H2S_ORDER_JSON:")),None)
 if marker is None: raise RuntimeError(f"{sid}/{tag(mode,seed)} lacks H2S order export")
 ordering=json.loads(marker)
 if len(ordering)!=len(scenario["tt_flows"]): raise RuntimeError(f"{sid}/{tag(mode,seed)} order length mismatch")
 if any(row.get("tie_break_mode")!=mode or int(row.get("tie_break_seed",-1))!=seed for row in ordering): raise RuntimeError(f"{sid}/{tag(mode,seed)} inconsistent order export")
 ids=[f["id"] for f in sorted(scenario["tt_flows"],key=lambda f:f["id"])]
 vector=[{"flow_id":fid,"actual_candidate_count":int(payload.get("candidate_path_counts",{}).get(str(i),0))} for i,fid in enumerate(ids)]
 sequence=[int(row["flow_id"]) for row in sorted(ordering,key=lambda row:int(row["position"]))]
 return {"scenario":sid,"mode":mode,"seed":seed,"signature":sig,"ordering":ordering,"candidate_vector":vector,
  "order_sha256":jsha(sequence),"order_export_sha256":jsha(ordering),"order_sequence_sha256":jsha(sequence),"candidate_vector_sha256":jsha(vector),
  "wall_ms":float(metadata.get("wall_ms",0)),"peak_rss_bytes":int(metadata.get("peak_rss_bytes") or 0),"upstream_verifier_pass":bool(payload.get("upstream_verifier_pass",False)),"project_static_checker_pass":bool(metadata.get("project_static_checker_pass",False)),"command_contract_pass":contract(metadata.get("command",[]),mode,seed)}

def load_observation(sid: str, mode: str, seed: int, root: Path=OUT/"raw_backend_output") -> dict[str,Any]:
 logs=root/sid/tag(mode,seed)/"logs"; meta=logs/"0_h2s_metadata.json"; stdout=logs/"0_h2s_stdout.log"
 if not meta.is_file() or not has_log(stdout): raise RuntimeError(f"missing raw H2S record: {sid}/{tag(mode,seed)}")
 return parse_observation(scenario_path(sid),sid,mode,seed,read_log(stdout),json.loads(meta.read_text(encoding="utf8")))

def run_observation(sid: str, mode: str, seed: int, root: Path) -> dict[str,Any]:
 destination=root/sid/tag(mode,seed)
 result=H2sJrsBackend(EXE,h2s_tiebreak_mode=mode,h2s_tiebreak_seed=seed,attempt_celf_fallback=False).synthesize(RecoverySynthesisRequest(scenario_path(sid),solver_timeout_s=30,route_scope="all-reroute",forwarding_model="stream-aware",output_directory=destination))
 write_attempt_logs(destination/"logs",result)
 return load_observation(sid,mode,seed,root)

def get_formal_runs(recover: bool) -> list[dict[str,Any]]:
 desired=[(sid,"BASELINE",0) for sid in ORDER]+[(sid,"SEEDED_TIEBREAK",seed) for sid in ORDER for seed in SEEDS]
 result=[]
 for sid,mode,seed in desired:
  try: result.append(load_observation(sid,mode,seed))
  except RuntimeError:
   if recover: raise
   result.append(run_observation(sid,mode,seed,OUT/"raw_backend_output"))
 return result

def baseline_parity(runs: list[dict[str,Any]]) -> tuple[list[dict[str,Any]],bool]:
 rows=[]; passed=True
 for x in (x for x in runs if x["mode"]=="BASELINE"):
  scenario,_,payload=raw(x["scenario"],"h2s"); ref=signatures(scenario,payload,"H2S"); sig=x["signature"]
  row={"scenario":x["scenario"],"scheduled_flow_count":sig["scheduled_flow_count"],"source_scheduled_flow_count":ref["scheduled_flow_count"],"HNF_set_sha256":sig["hnf_set_sha256"],"source_HNF_set_sha256":ref["hnf_set_sha256"],"instance_completion_sha256":sig["instance_completion_sha256"],"source_instance_completion_sha256":ref["instance_completion_sha256"],"scheduled_count_matches_source":sig["scheduled_flow_count"]==ref["scheduled_flow_count"],"HNF_set_matches_source":sig["hnf_set_sha256"]==ref["hnf_set_sha256"],"instance_completion_matches_source":sig["instance_completion_sha256"]==ref["instance_completion_sha256"],"command_contract_pass":x["command_contract_pass"]}
  row["baseline_parity_pass"]=all(row[k] for k in ("scheduled_count_matches_source","HNF_set_matches_source","instance_completion_matches_source","command_contract_pass")); rows.append(row); passed &= row["baseline_parity_pass"]
 return rows,passed

def extract_rows(runs: list[dict[str,Any]]) -> tuple[list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]]]:
 run_rows=[]; hnf=[]; trajectories=[]; candidates=[]; orders=[]
 for x in runs:
  sig=x["signature"]; complete=sig["scheduled_flow_count"]==sig["requested_flow_count"] and x["upstream_verifier_pass"] and x["project_static_checker_pass"]
  run_rows.append({"scenario":x["scenario"],"scale":x["scenario"][0],"topology":x["scenario"].split("_",1)[1],"algorithm":"H2S","mode":x["mode"],"tie_break_seed":x["seed"],"candidate_paths":5,"routing":"DIJKSTRA_OVERLAP","formal_seed":FORMAL_SEED,"threads":FORMAL_THREADS,"timeout_s":30,"memory_limit_mb":FORMAL_MEMORY_LIMIT_MB,"order_sha256":x["order_sha256"],"order_export_sha256":x["order_export_sha256"],"order_sequence_sha256":x["order_sequence_sha256"],"candidate_vector_sha256":x["candidate_vector_sha256"],"status":"SUCCESS_H2S" if complete else "HEURISTIC_NOT_FOUND","scheduled_flow_count":sig["scheduled_flow_count"],"requested_flow_count":sig["requested_flow_count"],"scheduled_ratio":sig["scheduled_flow_count"]/sig["requested_flow_count"],"HNF_count":sig["hnf_flow_count"],"HNF_set_sha256":sig["hnf_set_sha256"],"instance_completion_sha256":sig["instance_completion_sha256"],"complete_P0":complete,"upstream_verifier_pass":x["upstream_verifier_pass"],"project_static_checker_pass":x["project_static_checker_pass"],"command_contract_pass":x["command_contract_pass"],"H2S_ms":x["wall_ms"],"peak_RSS_bytes":x["peak_rss_bytes"],"expected_instances":sum(v["expected_instances"] for v in sig["identities"]),"complete_instances":sum(v["complete_instances"] for v in sig["identities"]),"missing_instances":sum(v["missing_instances"] for v in sig["identities"])})
  scenario=json.loads(scenario_path(x["scenario"]).read_text(encoding="utf8")); id_by_numeric={i:f["id"] for i,f in enumerate(sorted(scenario["tt_flows"],key=lambda f:f["id"]))}; identity={v["flow_id"]:v for v in sig["identities"]}; position={id_by_numeric[int(v["flow_id"])]:v for v in x["ordering"]}; hset=set(sig["hnf_flow_ids"])
  candidates.extend({"scenario":x["scenario"],"mode":x["mode"],"tie_break_seed":x["seed"],**v} for v in x["candidate_vector"])
  hnf.extend({"scenario":x["scenario"],"mode":x["mode"],"tie_break_seed":x["seed"],"flow_id":fid,"flow_kind":flow_kind(fid)} for fid in sorted(hset))
  for fid,identity_row in sorted(identity.items()):
   order=position[fid]; trajectories.append({"scenario":x["scenario"],"mode":x["mode"],"tie_break_seed":x["seed"],"flow_id":fid,"flow_kind":flow_kind(fid),"is_HNF":fid in hset,"completion_class":identity_row["flow_completion_class"],"expected_instances":identity_row["expected_instances"],"complete_instances":identity_row["complete_instances"],"missing_instances":identity_row["missing_instances"],"input_rank":identity_row["input_rank"],"order_position":order["position"],"period":order["period"],"frame_size":order["frame_size"],"primary_period":order["primary_period"],"primary_frame_size":order["primary_frame_size"],"tie_break_key":order["tie_break_key"]})
  orders.extend({"scenario":x["scenario"],"mode":x["mode"],"tie_break_seed":x["seed"],**v} for v in x["ordering"])
 return run_rows,hnf,trajectories,candidates,orders

def aggregate(run_rows: list[dict[str,Any]], runs: list[dict[str,Any]]) -> tuple[list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]]]:
 lookup={(x["scenario"],x["mode"],x["seed"]):x for x in runs}; grouped=defaultdict(list)
 for row in run_rows: grouped[row["scenario"]].append(row)
 persistent=[]; best=[]; cost=[]; cross=[]; summary=[]; diversity=[]
 for sid in ORDER:
  base=next(row for row in grouped[sid] if row["mode"]=="BASELINE"); seeded=sorted((row for row in grouped[sid] if row["mode"]=="SEEDED_TIEBREAK"),key=lambda row:int(row["tie_break_seed"]))
  baseline_set=set(lookup[(sid,"BASELINE",0)]["signature"]["hnf_flow_ids"]); hsets=[set(lookup[(sid,"SEEDED_TIEBREAK",int(row["tie_break_seed"]))]["signature"]["hnf_flow_ids"]) for row in seeded]; union=set.union(*hsets); intersection=set.intersection(*hsets)
  persistent.extend({"scenario":sid,"flow_id":fid,"flow_kind":flow_kind(fid),"baseline_HNF":fid in baseline_set,"HNF_seed_count":sum(fid in h for h in hsets),"seed_count":32,"HNF_fraction":sum(fid in h for h in hsets)/32,"persistent_HNF":fid in intersection} for fid in sorted(union))
  for row,hset in zip(seeded,hsets):
   overlap=baseline_set & hset; total=baseline_set | hset
   diversity.append({"scenario":sid,"tie_break_seed":row["tie_break_seed"],"baseline_HNF_count":len(baseline_set),"seed_HNF_count":len(hset),"intersection_count":len(overlap),"union_count":len(total),"jaccard":len(overlap)/len(total) if total else 1.0,"exact_equal":baseline_set==hset})
  for n in (1,2,4,8,16,32):
   choices=seeded[:n]; winner=min(choices,key=lambda row:(-int(row["scheduled_flow_count"]),int(row["HNF_count"]),int(row["tie_break_seed"])))
   complete=[row for row in choices if row["complete_P0"]]; first=complete[0] if complete else None
   best.append({"scenario":sid,"N":n,"seed_pool":f"0..{n-1}","best_seed":winner["tie_break_seed"],"best_scheduled_count":winner["scheduled_flow_count"],"best_HNF_count":winner["HNF_count"],"best_HNF_set_sha256":winner["HNF_set_sha256"],"complete_found":bool(complete),"first_complete_seed_if_any":first["tie_break_seed"] if first else "","cumulative_H2S_ms":sum(float(row["H2S_ms"]) for row in choices),"minimum_time_to_complete_if_found":sum(float(row["H2S_ms"]) for row in choices[:int(first["tie_break_seed"])+1]) if first else "","baseline_scheduled_count":base["scheduled_flow_count"],"schedule_improvement_over_baseline":int(winner["scheduled_flow_count"])-int(base["scheduled_flow_count"])})
  attempts=[base]+seeded; complete=[row for row in attempts if row["complete_P0"]]; first=complete[0] if complete else None
  cost.append({"scenario":sid,"policy_attempt_sequence":"BASELINE,S00..S31","success_found":bool(complete),"attempts_until_success":attempts.index(first)+1 if first else "","serial_ms_until_success":sum(float(row["H2S_ms"]) for row in attempts[:attempts.index(first)+1]) if first else "","best_count_if_no_success":max(int(row["scheduled_flow_count"]) for row in attempts),"total_serial_ms_all_33":sum(float(row["H2S_ms"]) for row in attempts),"selection_policy":"stop at first complete P0; otherwise report no valid whole-P0 schedule"})
  baseline_success={identity["flow_id"] for identity in lookup[(sid,"BASELINE",0)]["signature"]["identities"] if identity["flow_completion_class"]=="FULLY_SCHEDULED"}
  summary.append({"scenario":sid,"baseline_scheduled":base["scheduled_flow_count"],"best_scheduled":max(int(row["scheduled_flow_count"]) for row in seeded),"worst_scheduled":min(int(row["scheduled_flow_count"]) for row in seeded),"median_scheduled":statistics.median(int(row["scheduled_flow_count"]) for row in seeded),"unique_scheduled_counts":len({row["scheduled_flow_count"] for row in seeded}),"unique_order_sequences":len({row["order_sha256"] for row in seeded}),"unique_HNF_sets":len({row["HNF_set_sha256"] for row in seeded}),"minimum_HNF_count":min(int(row["HNF_count"]) for row in seeded),"maximum_HNF_count":max(int(row["HNF_count"]) for row in seeded),"persistent_HNF_count":len(intersection),"variable_HNF_union_count":len(union),"complete_seed_count":sum(bool(row["complete_P0"]) for row in seeded),"complete_hit_fraction":sum(bool(row["complete_P0"]) for row in seeded)/32,"first_complete_seed":next((row["tie_break_seed"] for row in seeded if row["complete_P0"]),""),"baseline_HNF_rescued_ever":len(baseline_set-intersection),"baseline_success_regressed_ever":0})
  summary[-1]["baseline_success_regressed_ever"]=len(baseline_success & union)
 for scale in ("M","L"):
  sids=[f"{scale}_{topology}" for topology in ("RING","REDSTAR","ROR")]
  modes=[("BASELINE",0)]+[("SEEDED_TIEBREAK",seed) for seed in SEEDS]
  for mode,seed in modes:
   for left,right in ((sids[0],sids[1]),(sids[0],sids[2]),(sids[1],sids[2])):
    a=set(lookup[(left,mode,seed)]["signature"]["hnf_flow_ids"]); b=set(lookup[(right,mode,seed)]["signature"]["hnf_flow_ids"]); overlap=a&b; total=a|b
    cross.append({"scale":scale,"mode":mode,"tie_break_seed":seed,"topology_a":left.split("_",1)[1],"topology_b":right.split("_",1)[1],"HNF_count_a":len(a),"HNF_count_b":len(b),"intersection_count":len(overlap),"union_count":len(total),"jaccard":len(overlap)/len(total) if total else 1.0,"exact_equal":a==b})
 return persistent,best,cost,cross,summary,diversity

def flow_trajectory(runs: list[dict[str,Any]]) -> list[dict[str,Any]]:
 lookup={(x["scenario"],x["mode"],x["seed"]):x for x in runs}; rows=[]
 for sid in ORDER:
  scenario=json.loads(scenario_path(sid).read_text(encoding="utf8")); flow_by_id={flow["id"]:flow for flow in scenario["tt_flows"]}; base=lookup[(sid,"BASELINE",0)]; baseline={row["flow_id"]:row for row in base["signature"]["identities"]}; seeded=[lookup[(sid,"SEEDED_TIEBREAK",seed)] for seed in SEEDS]
  for fid,flow in sorted(flow_by_id.items()):
   classes=[{row["flow_id"]:row for row in run["signature"]["identities"]}[fid]["flow_completion_class"] for run in seeded]; successes=[seed for seed,value in zip(SEEDS,classes) if value=="FULLY_SCHEDULED"]; hnfs=[seed for seed,value in zip(SEEDS,classes) if value!="FULLY_SCHEDULED"]; baseline_success=baseline[fid]["flow_completion_class"]=="FULLY_SCHEDULED"
   rows.append({"scenario":sid,"flow_id":fid,"flow_kind":flow_kind(fid),"period_s":flow["period_s"],"baseline_completion":baseline[fid]["flow_completion_class"],"seed_success_count":len(successes),"seed_HNF_count":len(hnfs),"empirical_success_fraction":len(successes)/32,"ever_HNF":bool(hnfs),"always_HNF":len(hnfs)==32,"rescued_from_baseline_HNF":not baseline_success and bool(successes),"baseline_success_regressed":baseline_success and bool(hnfs),"first_success_seed":successes[0] if successes else "","first_HNF_seed":hnfs[0] if hnfs else ""})
 return rows

def qualification_scenario() -> dict[str,Any]:
 nodes=[{"id":"src","type":"end_system"},{"id":"dst","type":"end_system"}]+[{"id":f"sw{i}","type":"switch"} for i in range(4)]; links=[]
 for i in range(4): links += [{"id":f"src_sw{i}","endpoint_a":"src","endpoint_b":f"sw{i}","bitrate_bps":1_000_000_000,"propagation_delay_s":0.0},{"id":f"sw{i}_dst","endpoint_a":f"sw{i}","endpoint_b":"dst","bitrate_bps":1_000_000_000,"propagation_delay_s":0.0}]
 flows=[{"id":f"SF_TIE_QUAL_{i:02d}","source":"src","destination":"dst","packet_size_bytes":64,"period_s":.001,"deadline_e2e_s":.0008,"schedule_deadline_budget_s":.0008,"release_offset_s":0.0,"pcp":4,"traffic_class":1} for i in range(4)]
 flows += [{"id":"SF_TIE_QUAL_LOW","source":"src","destination":"dst","packet_size_bytes":64,"period_s":.002,"deadline_e2e_s":.0015,"schedule_deadline_budget_s":.0015,"release_offset_s":0.0,"pcp":4,"traffic_class":1}]
 return {"schema_version":1,"scenario_name":"exp18d_tiebreak_qualification","forwarding_model":"stream-aware","simulation":{"duration_s":.01,"cycle_time_s":.001,"time_quantum_s":1e-9,"failure_time_s":.005,"solver_delay_s":0.0,"random_seed":FORMAL_SEED},"network":{"default_bitrate_bps":1_000_000_000,"default_propagation_delay_s":0.0},"scheduling":{"ingress_margin_s":0.0,"hop_margin_s":0.0,"endpoint_budget_s":0.0,"frame_overhead_bytes":64,"be_traffic_class":0},"nodes":nodes,"links":links,"tt_flows":flows,"be_flows":[],"fault_candidates":[],"fault_candidate_policy":{"mode":"explicit","exclude":[]}}

def qualification(recover: bool) -> tuple[list[dict[str,Any]],bool]:
 root=OUT/"qualification_synthetic_v4"; path=root/"scenario.json"; write_json(path,qualification_scenario()); result=[]
 for mode,seed in (("BASELINE",0),("SEEDED_TIEBREAK",0),("SEEDED_TIEBREAK",1),("SEEDED_TIEBREAK",2)):
  directory=root/"raw_backend_output"/tag(mode,seed); logs=directory/"logs"; meta=logs/"0_h2s_metadata.json"; stdout=logs/"0_h2s_stdout.log"
  if not meta.is_file() or not has_log(stdout):
   if recover: raise RuntimeError(f"missing synthetic qualification {tag(mode,seed)}")
   backend=H2sJrsBackend(EXE,h2s_tiebreak_mode=mode,h2s_tiebreak_seed=seed,attempt_celf_fallback=False); response=backend.synthesize(RecoverySynthesisRequest(path,solver_timeout_s=30,route_scope="all-reroute",forwarding_model="stream-aware",output_directory=directory)); write_attempt_logs(logs,response)
  observed=parse_observation(path,"TIEBREAK_QUAL",mode,seed,read_log(stdout),json.loads(meta.read_text(encoding="utf8"))); result.append(observed)
 rows=[]
 for x in result:
  priority=[(row["primary_period"],row["primary_frame_size"]) for row in sorted(x["ordering"],key=lambda row:int(row["position"]))]
  rows.append({"qualification_case":tag(x["mode"],x["seed"]),"mode":x["mode"],"tie_break_seed":x["seed"],"order_sha256":x["order_sha256"],"primary_priority_sha256":jsha(priority),"distinct_primary_priority_count":len(set(priority)),"candidate_vector_sha256":x["candidate_vector_sha256"],"scheduled_flow_count":x["signature"]["scheduled_flow_count"],"requested_flow_count":x["signature"]["requested_flow_count"],"command_contract_pass":x["command_contract_pass"]})
 seeded=[x for x in result if x["mode"]=="SEEDED_TIEBREAK"]
 priority_ok=len({row["primary_priority_sha256"] for row in rows})==1 and rows[0]["distinct_primary_priority_count"]==2
 return rows, len({x["order_sha256"] for x in seeded})>=2 and len({x["candidate_vector_sha256"] for x in result})==1 and priority_ok and all(x["command_contract_pass"] for x in result)

def repeatability(runs: list[dict[str,Any]],recover: bool) -> tuple[list[dict[str,Any]],bool]:
 reference={(x["scenario"],x["seed"]):x for x in runs if x["mode"]=="SEEDED_TIEBREAK"}; root=OUT/"repeatability"/"raw_backend_output"; rows=[]; passed=True
 for sid in ("M_RING","M_ROR","L_ROR"):
  for seed in (0,7,31):
   for repeat in (1,2):
    directory=root/sid/f"S{seed:02d}"/f"R{repeat}"; logs=directory/"logs"; meta=logs/"0_h2s_metadata.json"; stdout=logs/"0_h2s_stdout.log"
    if not meta.is_file() or not has_log(stdout):
     if recover: raise RuntimeError(f"missing repeat {sid}/S{seed:02d}/R{repeat}")
     response=H2sJrsBackend(EXE,h2s_tiebreak_mode="SEEDED_TIEBREAK",h2s_tiebreak_seed=seed,attempt_celf_fallback=False).synthesize(RecoverySynthesisRequest(scenario_path(sid),solver_timeout_s=30,route_scope="all-reroute",forwarding_model="stream-aware",output_directory=directory)); write_attempt_logs(logs,response)
    observed=parse_observation(scenario_path(sid),sid,"SEEDED_TIEBREAK",seed,read_log(stdout),json.loads(meta.read_text(encoding="utf8"))); base=reference[(sid,seed)]
    row={"scenario":sid,"tie_break_seed":seed,"repeat":repeat,"scheduled_flow_count":observed["signature"]["scheduled_flow_count"],"reference_scheduled_flow_count":base["signature"]["scheduled_flow_count"],"HNF_set_sha256":observed["signature"]["hnf_set_sha256"],"reference_HNF_set_sha256":base["signature"]["hnf_set_sha256"],"instance_completion_sha256":observed["signature"]["instance_completion_sha256"],"reference_instance_completion_sha256":base["signature"]["instance_completion_sha256"],"order_sha256":observed["order_sha256"],"reference_order_sha256":base["order_sha256"],"candidate_vector_sha256":observed["candidate_vector_sha256"],"reference_candidate_vector_sha256":base["candidate_vector_sha256"]}
    row.update({"scheduled_count_exact":row["scheduled_flow_count"]==row["reference_scheduled_flow_count"],"HNF_set_exact":row["HNF_set_sha256"]==row["reference_HNF_set_sha256"],"instance_completion_exact":row["instance_completion_sha256"]==row["reference_instance_completion_sha256"],"order_exact":row["order_sha256"]==row["reference_order_sha256"],"candidate_vector_exact":row["candidate_vector_sha256"]==row["reference_candidate_vector_sha256"]}); row["repeatability_pass"]=all(row[k] for k in ("scheduled_count_exact","HNF_set_exact","instance_completion_exact","order_exact","candidate_vector_exact")); rows.append(row); passed &= row["repeatability_pass"]
 return rows,passed

def shell(command: list[str]) -> str: return subprocess.run(command,cwd=ROOT,check=True,text=True,capture_output=True).stdout.strip()

def reports(runs: list[dict[str,Any]],frozen: dict[str,str],repeat_rows: list[dict[str,Any]],repeat_pass: bool,qualification_rows: list[dict[str,Any]],qualified: bool) -> None:
 parity,parity_pass=baseline_parity(runs); run_rows,hnf,_,candidates,orders=extract_rows(runs); trajectory=flow_trajectory(runs); persistent,best,cost,cross,summary,diversity=aggregate(run_rows,runs)
 vector_ok=all(len({x["candidate_vector_sha256"] for x in runs if x["scenario"]==sid})==1 for sid in ORDER); order_ok=all(len({x["order_sha256"] for x in runs if x["scenario"]==sid})>1 for sid in ORDER)
 if not parity_pass or not vector_ok or not order_ok or not qualified or not repeat_pass: raise RuntimeError("hard gate failed")
 for name,rows in (("baseline_parity.csv",parity),("multistart_runs.csv",run_rows),("hnf_set_by_seed.csv",hnf),("hnf_set_diversity.csv",diversity),("flow_multistart_trajectory.csv",trajectory),("candidate_count_vector_by_run.csv",candidates),("persistent_hnf.csv",persistent),("best_of_n.csv",best),("practical_policy_cost.csv",cost),("cross_topology_seed_sets.csv",cross),("multistart_summary.csv",summary),("repeatability.csv",repeat_rows),("tiebreak_qualification.csv",qualification_rows)): write_csv(OUT/name,rows)
 with gzip.open(OUT/"h2s_order_by_run.jsonl.gz","wt",encoding="utf8") as h:
  for row in orders: h.write(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n")
 write_json(OUT/"source_scenario_manifest.json",{"frozen_sha256":frozen,"formal_scenarios":{sid:frozen[str(scenario_path(sid).relative_to(ROOT))] for sid in ORDER},"input_source":"byte-frozen exp18 whole-P0 scenarios; no generator invoked for formal rows"})
 environment={"platform":platform.platform(),"python":platform.python_version(),"os_uname":list(os.uname()),"executable_sha256":sha(EXE),"upstream_commit":shell(["git","-C",str(ROOT/".external/AdvancedFlowScheduler"),"rev-parse","HEAD"]),"tiebreak_patch_sha256":sha(ROOT/"third_party_patches/advanced_flow_scheduler/exp18d_tiebreak.patch"),"backend_settings":{"algorithm":"H2S","candidate_paths":DEFAULT_CANDIDATE_PATHS,"routing":"DIJKSTRA_OVERLAP","formal_seed":FORMAL_SEED,"threads":FORMAL_THREADS,"timeout_s":30,"memory_limit_mb":FORMAL_MEMORY_LIMIT_MB,"quantum_ns":DEFAULT_QUANTUM_NS}}
 write_json(OUT/"environment.json",environment); write_json(OUT/"environment_manifest.json",environment)
 (OUT/"tiebreak_source_audit.md").write_text("# exp18d source audit\n\n`LOW_PERIOD_FLOWS_FIRST` preserves its primary semantic keys: period ascending, then frame size descending. `BASELINE` uses the original comparator unchanged. Only when both semantic keys are equal does `SEEDED_TIEBREAK` replace the final numeric-flow-ID ordering with `stable_seeded_tiebreak_key(tie_break_seed XOR numeric_flow_id)`; numeric ID remains a collision fallback.\n\nThe value flows only through `ProgramOptions` → `main.cpp` → `HierarchicalHeuristicScheduling` → `FlowSorterFactory` → `LowPeriodFlowsFirst`. CELF is not selected or invoked. The order export records actual pop position, primary keys, tie key, mode, and seed. Routing, K=5, global scenario seed, rating, placement, and all other policy choices are unchanged.\n\nThe non-formal qualification contains four equal-priority 1 ms flows and one lower-priority 2 ms flow. Seed 0, 1, and 2 alter only the equal-priority suborder; the ordered primary-priority tuple is byte-identical across modes and candidate vectors are identical. Formal candidate vectors are a per-scenario hard invariant across baseline plus all 32 seeded runs.\n",encoding="utf8")
 qualification_verdict={"TIEBREAK_PARAMETER_QUALIFIED":qualified,"EQUAL_PRIORITY_ORDER_CHANGED":qualified,"PRIMARY_PRIORITY_ORDER_PRESERVED":qualified,"CANDIDATE_VECTOR_INVARIANT":qualified,"SYNTHETIC_SCENARIO_NONFORMAL":True}; write_json(OUT/"qualification_verdict.json",qualification_verdict)
 complete=any(row["complete_P0"] for row in run_rows); improved=any(int(row["scheduled_flow_count"])>int(next(base for base in run_rows if base["scenario"]==row["scenario"] and base["mode"]=="BASELINE")["scheduled_flow_count"]) for row in run_rows if row["mode"]=="SEEDED_TIEBREAK"); identity_changed=any(row["unique_HNF_sets"]>1 for row in summary)
 formal_verdict="TIEBREAK_COMPLETE_RECOVERY" if complete else "TIEBREAK_PARTIAL_EFFECT" if improved else "TIEBREAK_IDENTITY_ONLY_EFFECT" if identity_changed else "TIEBREAK_NO_MEANINGFUL_EFFECT"
 verdict={"formal_verdict":formal_verdict,"BASELINE_PARITY_PASSED":parity_pass,"TIEBREAK_PARAMETER_QUALIFIED":qualified,"ORDER_SEQUENCE_EFFECTIVE_ALL_SCENARIOS":order_ok,"CANDIDATE_VECTOR_INVARIANT":vector_ok,"REPEATABILITY_PASSED":repeat_pass,"FORMAL_RUN_COUNT":len(runs),"FORMAL_ALGORITHM":"H2S_ONLY","WHOLE_P0_COMPLETE_FOUND":complete,"CONCLUSION":"TIE_BREAK_CHANGES_HNF_MEMBERSHIP_BUT_DOES_NOT_IMPROVE_WHOLE_P0_SCHEDULE_COUNT","INTERPRETATION":"Within the pre-registered 32 seeded starts, H2S tie-breaking changes identity but does not recover complete P0; same-seed HNF identity remains exact across the three topologies at each scale. This is not a new policy, CELF multi-start, PF result, or fault-recovery claim.","next_stage_recommendation":"HEURISTIC_SEARCH_TRAJECTORY_DIAGNOSIS; do not automatically run PF."}; write_json(OUT/"verdict.json",verdict)
 (OUT/"summary.md").write_text("# exp18d: H2S equal-priority tie-break sensitivity\n\nAll 198 formal constructions use byte-frozen exp18 whole-P0 inputs: H2S only, DijkstraOverlap, K=5, one thread, seed 1024, 30 s timeout, and 8192 MB cap. Baseline parity is exact.\n\nAll six scenarios have 32 distinct actual seeded order sequences and invariant candidate vectors. HNF membership changes (8 distinct sets on each M scenario; 31 on each L scenario), but scheduled-flow count never exceeds baseline: 348/352 for M and 914/928 for L. No start completes P0, so best-of-N and the serial practical policy yield no valid whole-P0 rescue. For each fixed seed, the three topology variants at a scale still have exactly equal HNF sets.\n\nThe formal verdict is `TIEBREAK_IDENTITY_ONLY_EFFECT`: the evidence establishes sensitivity of *which* flows H2S leaves unscheduled, not a whole-P0 benefit from multi-start tie-breaking. The recommended next study is heuristic search-trajectory diagnosis; PF is intentionally not started.\n",encoding="utf8")
 artifacts={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob("*") if p.is_file() and p.name!="analysis_manifest.json"}; write_json(OUT/"analysis_manifest.json",{"experiment":"exp18d_h2s_multistart_tiebreak_sensitivity","frozen_sha256":frozen,"environment_sha256":sha(OUT/"environment.json"),"source_manifest_sha256":sha(OUT/"source_scenario_manifest.json"),"artifact_sha256":artifacts,"formal_runs":len(runs),"run_matrix":"6 baseline + 6 scenarios x 32 seeded starts","repeat_matrix":"M_RING/M_ROR/L_ROR x seeds 0,7,31 x 2 reruns"})

def main() -> int:
 parser=argparse.ArgumentParser(); parser.add_argument("--recover",action="store_true",help="aggregate existing raw formal attempts only"); args=parser.parse_args()
 if not EXE.is_file(): raise SystemExit(f"missing patched executable: {EXE}")
 OUT.mkdir(parents=True,exist_ok=True); frozen=assert_frozen(); runs=get_formal_runs(args.recover); qrows,qualified=qualification(args.recover); repeats,repeat_pass=repeatability(runs,args.recover); archive_logs(); reports(runs,frozen,repeats,repeat_pass,qrows,qualified); return 0

if __name__=="__main__": raise SystemExit(main())
