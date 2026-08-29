#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)"
OUTPUT="$PROJECT_ROOT/results/critical_link_analysis"

inside_environment() {
    local run_id="$1" code_commit scratch
    cd "$PROJECT_ROOT"
    if [[ "${TSN_ALLOW_DIRTY:-0}" != "1" ]]; then
        git diff --quiet && git diff --cached --quiet || { echo "Formal exp07 requires a clean tracked working tree" >&2; exit 1; }
    fi
    code_commit="$(git rev-parse HEAD)"
    echo "PHASE 1/8 exp01-exp06 regression"
    bash experiments/exp06_offline_per_failure/run.sh --inside-environment "${run_id}_regression"
    git restore -- results/recovery_metrics results/joint_profile_activation results/online_joint_recovery results/smt_schedule results/scenario_framework results/offline_per_failure
    echo "PHASE 2/8 compile auto scenarios"
    make -j"$(nproc)"
    python3 -m unittest discover -s tests -v
    echo "PHASE 3/8 critical-link discovery"
    python3 tools/analyze_critical_links.py --scenario configs/scenarios/diamond_auto.yaml --inside-environment --skip-build
    python3 tools/analyze_critical_links.py --scenario configs/scenarios/mesh10_auto.yaml --inside-environment --skip-build
    scratch="$(mktemp -d)"
    cp generated/diamond_auto/fault_analysis/candidate_faults.json "$scratch/diamond.json"
    cp generated/diamond_auto/fault_analysis/critical_link_analysis.csv "$scratch/diamond.csv"
    cp generated/mesh10_auto/fault_analysis/candidate_faults.json "$scratch/mesh10.json"
    cp generated/mesh10_auto/fault_analysis/critical_link_analysis.csv "$scratch/mesh10.csv"
    echo "PHASE 4/8 auto per-failure precompute"
    python3 tools/precompute_profiles.py --scenario configs/scenarios/diamond_auto.yaml --inside-environment --skip-build
    python3 tools/precompute_profiles.py --scenario configs/scenarios/mesh10_auto.yaml --inside-environment --skip-build
    echo "PHASE 5/8 fault-equivalence dataset"
    python3 tools/prepare_fault_dataset.py --scenario configs/scenarios/diamond_auto.yaml
    python3 tools/prepare_fault_dataset.py --scenario configs/scenarios/mesh10_auto.yaml
    echo "PHASE 6/8 Jaccard and profile diagnostics"
    echo "PHASE 7/8 figures and technical summary"
    mkdir -p "$OUTPUT"
    python3 scripts/analyze_critical_link_experiment.py --output-dir "$OUTPUT" --code-commit "$code_commit"
    echo "PHASE 8/8 deterministic repeatability"
    python3 tools/analyze_critical_links.py --scenario configs/scenarios/diamond_auto.yaml --inside-environment --skip-build
    python3 tools/analyze_critical_links.py --scenario configs/scenarios/mesh10_auto.yaml --inside-environment --skip-build
    cmp "$scratch/diamond.json" generated/diamond_auto/fault_analysis/candidate_faults.json
    cmp "$scratch/diamond.csv" generated/diamond_auto/fault_analysis/critical_link_analysis.csv
    cmp "$scratch/mesh10.json" generated/mesh10_auto/fault_analysis/candidate_faults.json
    cmp "$scratch/mesh10.csv" generated/mesh10_auto/fault_analysis/critical_link_analysis.csv
    rm -rf -- "$scratch"
    python3 - "$OUTPUT/manifest.json" "$code_commit" <<'PY'
import json,sys
path, expected = sys.argv[1:]
manifest=json.load(open(path))
assert manifest["git_commit"] == expected
print("EXP01-EXP06_REGRESSION PASS")
print("EXP07_REPEATABILITY PASS")
print("EXP07 PASS")
PY
}

if [[ "${1:-}" == "--inside-environment" ]]; then inside_environment "$2"; exit 0; fi
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
source "$WORKSPACE_ROOT/.venv/bin/activate"
source "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE_ROOT"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$RUN_ID'"
echo "Completed exp07 run $RUN_ID"
