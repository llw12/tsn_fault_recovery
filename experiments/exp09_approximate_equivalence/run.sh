#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)"
OUTPUT="$PROJECT_ROOT/results/approximate_equivalence"
SCENARIOS=(diamond_auto mesh10_auto structured20_auto)

fingerprint() {
    python3 - "$1" <<'PY'
import json,pathlib,sys
result={}
for scenario in ("diamond_auto","mesh10_auto","structured20_auto"):
    result[scenario]={}
    for policy in ("J100","J080","J060","J040","J020","JE080_D0","JE080_D1","JE080_D2","JE060_D0","JE060_D1","JE060_D2","JE040_D0","JE040_D1","JE040_D2"):
        root=pathlib.Path("generated")/scenario/"profiles/approximate_equivalence"/policy
        store=json.loads((root/"store.json").read_text())
        grouping=json.loads((root/"candidate_groups.json").read_text())
        result[scenario][policy]={
          "groups":[(g["group_id"],g["member_faults"],g["merge_tree"]) for g in grouping["groups"]],
          "classes":sorted((v["class_type"],v["members"],v["semantic_profile_hash"]) for v in store["classes"].values()),
          "mapping":store["fault_to_class"],
          "rejected":sorted((r["status"],r["members"],r["split_left"],r["split_right"]) for r in store["rejected_groups"]),
          "attempts":sorted((a["status"],a["members"],bool(a.get("validation_pass"))) for a in store["synthesis_attempts"]),
        }
pathlib.Path(sys.argv[1]).write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
PY
}

inside_environment() {
    local run_id="$1" code_commit scratch
    cd "$PROJECT_ROOT"
    git diff --quiet && git diff --cached --quiet || { echo "Formal exp09 requires a clean tracked working tree" >&2; exit 1; }
    [[ ! -e "$OUTPUT" ]] || { echo "Formal exp09 output already exists: $OUTPUT" >&2; exit 1; }
    code_commit="$(git rev-parse HEAD)"
    echo "PHASE 1/14 exp01-exp08 regression"
    bash experiments/exp07_critical_link_analysis/run.sh --inside-environment "${run_id}_regression"
    git restore -- results/recovery_metrics results/joint_profile_activation results/online_joint_recovery results/smt_schedule results/scenario_framework results/offline_per_failure results/critical_link_analysis
    make -j"$(nproc)"
    python3 -m unittest discover -s tests -v
    for scenario in "${SCENARIOS[@]}"; do
        python3 tools/analyze_critical_links.py --scenario "configs/scenarios/${scenario}.yaml" --inside-environment --skip-build
        python3 tools/precompute_profiles.py --scenario "configs/scenarios/${scenario}.yaml" --inside-environment --skip-build
        python3 tools/precompute_exact_equivalence.py --scenario "configs/scenarios/${scenario}.yaml" --inside-environment --skip-build
        python3 tools/run_exact_sweep.py --scenario "configs/scenarios/${scenario}.yaml" --run-id "${run_id}_exp08"
    done
    find "$PROJECT_ROOT/results/scenarios" -depth -type d -name "${run_id}*" -exec rm -rf -- {} +
    echo "PHASE 2/14 load fixed scenarios"
    python3 - <<'PY'
import pathlib,shutil
root=pathlib.Path.cwd().resolve()
for scenario in ("diamond_auto","mesh10_auto","structured20_auto"):
    for target in (root/"generated"/scenario/"profiles/approximate_equivalence",
                   root/"results/scenarios"/scenario/"offline-approx-equivalence"):
        resolved=target.resolve()
        if root not in resolved.parents: raise SystemExit(f"unsafe cleanup target: {resolved}")
        if target.exists(): shutil.rmtree(target)
PY
    echo "PHASE 3/14 pre-fault pairwise features"
    echo "PHASE 4/14 generate 14 policy candidate partitions"
    echo "PHASE 5/14 generate deterministic merge trees"
    echo "PHASE 6/14 shared robust synthesis with cache"
    echo "PHASE 7/14 per-member validation with cache"
    echo "PHASE 8/14 recursive split"
    echo "PHASE 9/14 final Class Stores"
    for scenario in "${SCENARIOS[@]}"; do
        python3 tools/run_approximate_campaign.py --scenario "configs/scenarios/${scenario}.yaml" --run-id "$run_id" --skip-build
    done
    echo "PHASE 10/14 compression and feasibility analysis"
    echo "PHASE 11/14 quality comparison"
    echo "PHASE 12/14 observed Pareto analysis"
    echo "PHASE 13/14 figures and technical report"
    python3 scripts/analyze_approximate_equivalence.py --run-id "$run_id" --code-commit "$code_commit" --output-dir "$OUTPUT"
    echo "PHASE 14/14 deterministic repeatability"
    scratch="$(mktemp -d)"
    fingerprint "$scratch/before.json"
    for scenario in "${SCENARIOS[@]}"; do
        python3 tools/run_approximate_campaign.py --scenario "configs/scenarios/${scenario}.yaml" --run-id "${run_id}_repeat" --skip-build
    done
    fingerprint "$scratch/after.json"
    cmp "$scratch/before.json" "$scratch/after.json"
    rm -rf -- "$scratch"
    python3 - "$OUTPUT/manifest.json" "$code_commit" <<'PY'
import csv,json,pathlib,sys
manifest=json.load(open(sys.argv[1])); expected=sys.argv[2]
assert manifest["git_commit"] == expected
rows=list(csv.DictReader(open(pathlib.Path(sys.argv[1]).with_name("class_validation.csv"))))
assert len(rows)==504 and all(r["validation_pass"].lower()=="true" for r in rows)
for row in rows:
    assert all(int(row[field])==0 for field in ("runtime_route_solver_invocations","runtime_z3_solver_invocations","runtime_profile_synthesis_invocations","runtime_grouping_invocations"))
print("EXP01-EXP08_REGRESSION PASS")
print("EXP09_REPEATABILITY PASS")
print("EXP09 PASS")
PY
}

if [[ "${1:-}" == "--inside-environment" ]]; then inside_environment "$2"; exit 0; fi
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
source "$WORKSPACE_ROOT/.venv/bin/activate"
source "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE_ROOT"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$RUN_ID'"
echo "Completed exp09 run $RUN_ID"
