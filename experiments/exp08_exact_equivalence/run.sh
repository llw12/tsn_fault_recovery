#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)"
OUTPUT="$PROJECT_ROOT/results/exact_equivalence"
SCENARIOS=(diamond_auto mesh10_auto structured20_auto)

fingerprint() {
    python3 - "$1" <<'PY'
import json, pathlib, sys
out={}
for scenario in ("diamond_auto","mesh10_auto","structured20_auto"):
    p=pathlib.Path("generated")/scenario/"profiles/exact_equivalence/store.json"
    s=json.loads(p.read_text())
    out[scenario]={
      "groups":[{"id":g["candidate_group_id"],"affected":g["affected_flows"],"members":g["members"],"sat":g["sat_members"],"status":g["candidate_shared_synthesis_status"]} for g in s["candidate_groups"]],
      "classes":{k:{"members":v["members"],"type":v["class_type"],"profile_sha256":v["profile_sha256"],"semantic_profile_hash":v["semantic_profile_hash"]} for k,v in s["classes"].items()},
      "fault_to_class":s["fault_to_class"],
    }
pathlib.Path(sys.argv[1]).write_text(json.dumps(out,sort_keys=True,indent=2)+"\n")
PY
}

inside_environment() {
    local run_id="$1" code_commit scratch
    cd "$PROJECT_ROOT"
    git diff --quiet && git diff --cached --quiet || { echo "Formal exp08 requires a clean tracked working tree" >&2; exit 1; }
    [[ ! -e "$OUTPUT" ]] || { echo "Formal exp08 output already exists: $OUTPUT" >&2; exit 1; }
    code_commit="$(git rev-parse HEAD)"
    echo "PHASE 1/12 exp01-exp07 regression"
    bash experiments/exp07_critical_link_analysis/run.sh --inside-environment "${run_id}_regression"
    git restore -- results/recovery_metrics results/joint_profile_activation results/online_joint_recovery results/smt_schedule results/scenario_framework results/offline_per_failure results/critical_link_analysis
    echo "PHASE 2/12 compile and validate structured20"
    make -j"$(nproc)"
    python3 -m unittest discover -s tests -v
    python3 tools/generate_scenario.py configs/scenarios/structured20_auto.yaml
    echo "PHASE 3/12 automatic candidate discovery"
    for scenario in "${SCENARIOS[@]}"; do python3 tools/analyze_critical_links.py --scenario "configs/scenarios/${scenario}.yaml" --inside-environment --skip-build; done
    echo "PHASE 4/12 Per-Failure precompute"
    for scenario in "${SCENARIOS[@]}"; do python3 tools/precompute_profiles.py --scenario "configs/scenarios/${scenario}.yaml" --inside-environment --skip-build; done
    echo "PHASE 5/12 exact affected-set grouping"
    echo "PHASE 6/12 shared robust Profile synthesis"
    echo "PHASE 7/12 deterministic Class Store generation"
    for scenario in "${SCENARIOS[@]}"; do python3 tools/precompute_exact_equivalence.py --scenario "configs/scenarios/${scenario}.yaml" --inside-environment --skip-build; done
    echo "PHASE 8/12 per-member class validation"
    echo "PHASE 9/12 matched Offline Per-Failure and Exact runtime sweeps"
    for scenario in "${SCENARIOS[@]}"; do python3 tools/run_exact_sweep.py --scenario "configs/scenarios/${scenario}.yaml" --run-id "$run_id"; done
    echo "PHASE 10/12 compression and quality analysis"
    echo "PHASE 11/12 figures and technical report"
    python3 scripts/analyze_exact_equivalence.py --run-id "$run_id" --output-dir "$OUTPUT" --code-commit "$code_commit"
    echo "PHASE 12/12 deterministic repeatability"
    scratch="$(mktemp -d)"
    fingerprint "$scratch/before.json"
    for scenario in "${SCENARIOS[@]}"; do python3 tools/precompute_exact_equivalence.py --scenario "configs/scenarios/${scenario}.yaml" --inside-environment --skip-build; done
    fingerprint "$scratch/after.json"
    cmp "$scratch/before.json" "$scratch/after.json"
    for scenario in "${SCENARIOS[@]}"; do python3 tools/validate_exact_equivalence.py --scenario-name "$scenario" --run-id "$run_id"; done
    rm -rf -- "$scratch"
    python3 - "$OUTPUT/manifest.json" "$code_commit" <<'PY'
import json,sys
manifest=json.load(open(sys.argv[1]))
assert manifest["git_commit"] == sys.argv[2]
print("EXP01-EXP07_REGRESSION PASS")
print("EXP08_REPEATABILITY PASS")
print("EXP08 PASS")
PY
}

if [[ "${1:-}" == "--inside-environment" ]]; then inside_environment "$2"; exit 0; fi
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
source "$WORKSPACE_ROOT/.venv/bin/activate"
source "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE_ROOT"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$RUN_ID'"
echo "Completed exp08 run $RUN_ID"
