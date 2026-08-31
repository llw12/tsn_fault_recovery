#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE="$(cd -- "$ROOT/.." && pwd)"
OUTPUT="$ROOT/results/smt_scalability"
PYTHON="python3"

analyze_existing() {
  local analysis_commit temp_dir
  cd "$ROOT"
  [[ -f "$OUTPUT/campaign.json" ]] || { echo "Missing exp11 campaign.json" >&2; exit 1; }
  analysis_commit="$(git rev-parse HEAD)"
  echo "PHASE 11 status and consistency audit"
  "$PYTHON" - "$OUTPUT/campaign.json" <<'PY'
import json,sys
campaign=json.load(open(sys.argv[1])); rows=campaign["solver_cases"]
assert campaign["parallelism"] == 1 and campaign["solver_timeout_ms"] == 30000
assert campaign["solver_modes"] == ["PRODUCTION_OPTIMIZE","BENCHMARK_FEASIBILITY_ONLY"]
by_case={}
for row in rows: by_case.setdefault(row["case_id"],{}).setdefault(row["mode"],[]).append(row)
for case,modes in by_case.items():
    fingerprints={r["model_fingerprint"] for values in modes.values() for r in values if r["model_fingerprint"]}
    assert len(fingerprints) <= 1, (case,fingerprints)
    if modes.get("PRODUCTION_OPTIMIZE",[{}])[0].get("status") == "SAT":
        assert modes["BENCHMARK_FEASIBILITY_ONLY"][0]["status"] == "SAT", case
print(f"logical_cases={len(by_case)} measurements={len(rows)}")
PY
  echo "PHASE 12 model-complexity analysis"
  "$PYTHON" scripts/analyze_smt_scalability.py --campaign "$OUTPUT/campaign.json" --output "$OUTPUT" --analysis-code-commit "$analysis_commit"
  echo "PHASE 13 figures generated"
  for image in z3_check_time_vs_scale.png p0_z3_vs_scale.png optimize_vs_feasibility.png model_constraints_vs_scale.png z3_vs_constraints.png z3_vs_contention.png pf_vs_shared_z3.png solver_status_vs_scale.png; do
    [[ -s "$OUTPUT/$image" ]]
  done
  echo "PHASE 14 deterministic model fingerprint and analyzer audit"
  temp_dir="$(mktemp -d)"
  cp "$OUTPUT/campaign.json" "$temp_dir/campaign.json"
  "$PYTHON" scripts/analyze_smt_scalability.py --campaign "$temp_dir/campaign.json" --output "$temp_dir" --analysis-code-commit "$analysis_commit"
  "$PYTHON" - "$OUTPUT/analysis_manifest.json" <<'PY' | while IFS= read -r artifact; do cmp "$OUTPUT/$artifact" "$temp_dir/$artifact"; done
import json,sys
manifest=json.load(open(sys.argv[1]))
for name in manifest["generated_artifact_paths"]: print(name)
print("analysis_manifest.json")
PY
  rm -rf -- "$temp_dir"
  echo "PHASE 15 artifact audit"
  "$PYTHON" scripts/analyze_smt_scalability.py --output "$OUTPUT" --validate-only
  "$PYTHON" - "$OUTPUT" <<'PY'
import csv,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); campaign=json.load(open(root/"campaign.json"))
assert [s["scenario"] for s in campaign["scenarios"]] == [f"structured{x}_auto" for x in (20,30,40,50,60,80,100)]
required={"campaign.json","solver_cases.csv","model_complexity.csv","scenario_solver_summary.csv","optimize_vs_feasibility.csv","pf_vs_shared_scaling.csv","status_frontier.csv","summary.md","analysis_manifest.json"}
assert required <= {p.name for p in root.iterdir()}
rows=list(csv.DictReader((root/"solver_cases.csv").open()))
assert any(r["scenario"]=="structured20_auto" and r["policy_id"]=="J020" and r["status"]=="NO_ROUTE" for r in rows)
assert not any(p.stat().st_size > 20*1024*1024 for p in root.iterdir() if p.is_file())
print("EXP11 artifact audit PASS")
PY
  echo "EXP11 PASS"
}

inside() {
  local run_id="$1"
  cd "$ROOT"
  git diff --quiet && git diff --cached --quiet || { echo "Formal exp11 requires a clean tracked working tree" >&2; exit 1; }
  [[ ! -e "$OUTPUT" ]] || { echo "Formal exp11 output already exists: $OUTPUT" >&2; exit 1; }
  echo "PHASE 1 repo and environment preflight"
  git rev-parse HEAD
  z3 --version
  echo "PHASE 2 tests and semantic regression"
  "$PYTHON" -m unittest discover -s tests -v
  make MODE=release -j2
  (cd simulations/smt_validation && "$ROOT/tsn_fault_recovery" -u Cmdenv -n "$ROOT/src:$WORKSPACE/inet-4.7.0/src" -l "$WORKSPACE/inet-4.7.0/src/INET" -f omnetpp.ini -c SmtUnitTests --cmdenv-express-mode=true --result-dir=/tmp/exp11-smt-unit-results)
  "$PYTHON" tools/check_smt_semantic_regression.py
  echo "PHASE 3 generate and freeze structured60/80/100"
  "$PYTHON" tools/generate_scalability_scenarios.py --switches 30 --rows 5 --cols 6 --end-systems 15 --tt-flows 30 --be-flows 6 --seed 0 >/dev/null
  "$PYTHON" tools/generate_scalability_scenarios.py --switches 40 --rows 5 --cols 8 --end-systems 20 --tt-flows 40 --be-flows 8 --seed 0 >/dev/null
  "$PYTHON" tools/generate_scalability_scenarios.py --switches 50 --rows 5 --cols 10 --end-systems 25 --tt-flows 50 --be-flows 10 --seed 0 >/dev/null
  git diff --exit-code -- configs/scenarios/structured30_auto.yaml configs/scenarios/structured30_auto.manifest.json configs/scenarios/structured40_auto.yaml configs/scenarios/structured40_auto.manifest.json configs/scenarios/structured50_auto.yaml configs/scenarios/structured50_auto.manifest.json
  "$PYTHON" tools/generate_scalability_scenarios.py --switches 60 --rows 5 --cols 12 --end-systems 30 --tt-flows 60 --be-flows 12 --seed 0 >/dev/null
  "$PYTHON" tools/generate_scalability_scenarios.py --switches 80 --rows 5 --cols 16 --end-systems 40 --tt-flows 80 --be-flows 16 --seed 0 >/dev/null
  "$PYTHON" tools/generate_scalability_scenarios.py --switches 100 --rows 5 --cols 20 --end-systems 50 --tt-flows 100 --be-flows 20 --seed 0 >/dev/null
  echo "PHASE 4 P0 preflight"
  echo "PHASE 5 candidate discovery"
  echo "PHASE 6 build P0 benchmark cases"
  echo "PHASE 7 build full PF benchmark cases"
  echo "PHASE 8 build J100/J040/J020 raw shared cases"
  echo "PHASE 9 production Optimize serial sweep"
  echo "PHASE 10 feasibility-only serial sweep"
  "$PYTHON" tools/run_smt_scalability_campaign.py \
    --scenario configs/scenarios/structured20_auto.yaml \
    --scenario configs/scenarios/structured30_auto.yaml \
    --scenario configs/scenarios/structured40_auto.yaml \
    --scenario configs/scenarios/structured50_auto.yaml \
    --scenario configs/scenarios/structured60_auto.yaml \
    --scenario configs/scenarios/structured80_auto.yaml \
    --scenario configs/scenarios/structured100_auto.yaml \
    --run-id "$run_id" --output "$OUTPUT/campaign.json"
  analyze_existing
}

if [[ "${1:-}" == "--analyze-existing" ]]; then analyze_existing; exit 0; fi
if [[ "${1:-}" == "--inside-environment" ]]; then inside "$2"; exit 0; fi
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
source "$WORKSPACE/.venv/bin/activate"
source "$WORKSPACE/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$run_id'"
