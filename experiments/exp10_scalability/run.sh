#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE="$(cd -- "$ROOT/.." && pwd)"
OUTPUT="$ROOT/results/scalability"

analyze_existing() {
  local campaign="$OUTPUT/campaign.json" temp_dir analysis_commit
  cd "$ROOT"
  [[ -f "$campaign" ]] || { echo "Missing formal campaign: $campaign" >&2; exit 1; }
  analysis_commit="$(git rev-parse HEAD)"
  echo "PHASE 13/16 raw artifact audit"
  python3 - "$campaign" <<'PY'
import hashlib,json,sys
path=sys.argv[1]; data=json.load(open(path))
assert data["implementation_commit"] == "ed97466cf46d79a74171833af0ea69114d3fdb48"
assert data["policies"] == ["J100","J060","J040","J020"]
assert [row["switch_count"] for row in data["scenarios"]] == [20,30,40,50]
print("source_campaign_sha256=" + hashlib.sha256(open(path,"rb").read()).hexdigest())
PY
  echo "PHASE 14/16 deterministic results analysis"
  python3 scripts/analyze_scalability.py --campaign "$campaign" --output "$OUTPUT" --analysis-code-commit "$analysis_commit"
  echo "PHASE 15/16 analysis repeatability"
  temp_dir="$(mktemp -d)"
  python3 scripts/analyze_scalability.py --campaign "$campaign" --output "$temp_dir" --analysis-code-commit "$analysis_commit"
  while IFS= read -r artifact; do cmp "$OUTPUT/$artifact" "$temp_dir/$artifact"; done < <(
    python3 - "$OUTPUT/analysis_manifest.json" <<'PY'
import json,sys
manifest=json.load(open(sys.argv[1]))
for path in manifest["generated_artifact_paths"]: print(path)
print("analysis_manifest.json")
PY
  )
  rm -rf -- "$temp_dir"
  echo "PHASE 16/16 strict artifact and disk audit"
  python3 scripts/analyze_scalability.py --campaign "$campaign" --output "$OUTPUT" --validate-only
  [[ $(find "$OUTPUT" -maxdepth 1 -type f -size +20M | wc -l) -eq 0 ]] || { echo "Unexpected large formal artifact" >&2; exit 1; }
  echo "EXP10 PASS"
}

inside() {
  local run_id="$1" code_commit
  cd "$ROOT"
  git diff --quiet && git diff --cached --quiet || { echo "Formal exp10 requires a clean tracked working tree" >&2; exit 1; }
  [[ ! -e "$OUTPUT" ]] || { echo "Formal exp10 output already exists: $OUTPUT" >&2; exit 1; }
  code_commit="$(git rev-parse HEAD)"
  echo "PHASE 1/16 regression and tests"
  # exp09's formal result directory is immutable baseline evidence.  Re-run the
  # exp01--08 harness, then validate exp09's frozen 42-point artifact rather
  # than deleting/recreating that tracked formal evidence in place.
  bash experiments/exp07_critical_link_analysis/run.sh --inside-environment "${run_id}_regression"
  git restore -- results/recovery_metrics results/joint_profile_activation results/online_joint_recovery results/smt_schedule results/scenario_framework results/offline_per_failure results/critical_link_analysis
  python3 - <<'PY'
import csv,json,pathlib
root=pathlib.Path("results/approximate_equivalence")
assert json.loads((root/"manifest.json").read_text())["git_commit"] == "819b19915c390145419b9ea91db39e4ec8e11462"
assert len(list(csv.DictReader((root/"policy_summary.csv").open()))) == 42
print("EXP09_FROZEN_REGRESSION PASS")
PY
  python3 -m unittest discover -s tests -v
  echo "PHASE 2/16 generate and preflight frozen scale scenarios"
  for spec in "30 5 6 15 30 6" "40 5 8 20 40 8" "50 5 10 25 50 10"; do
    read -r switches rows cols ends tt be <<<"$spec"
    python3 tools/generate_scalability_scenarios.py --switches "$switches" --rows "$rows" --cols "$cols" --end-systems "$ends" --tt-flows "$tt" --be-flows "$be" --seed 0 >/dev/null
  done
  echo "PHASE 3-12/16 serial P0, candidates, PF, J-only policies, synthesis and validation"
  python3 tools/run_scalability_campaign.py --scenario configs/scenarios/structured20_auto.yaml --scenario configs/scenarios/structured30_auto.yaml --scenario configs/scenarios/structured40_auto.yaml --scenario configs/scenarios/structured50_auto.yaml --run-id "$run_id" --output "$OUTPUT/campaign.json"
  analyze_existing
}

if [[ "${1:-}" == "--analyze-existing" ]]; then analyze_existing; exit 0; fi
if [[ "${1:-}" == "--inside-environment" ]]; then inside "$2"; exit 0; fi
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
source "$WORKSPACE/.venv/bin/activate"
source "$WORKSPACE/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$run_id'"
