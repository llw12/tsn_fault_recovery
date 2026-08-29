#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)"
OUTPUT="$PROJECT_ROOT/results/offline_per_failure"

inside_environment() {
    local run_id="$1" code_commit
    cd "$PROJECT_ROOT"
    git diff --quiet && git diff --cached --quiet || { echo "Formal exp06 requires a clean tracked working tree" >&2; exit 1; }
    code_commit="$(git rev-parse HEAD)"
    make -j"$(nproc)"
    python3 -m unittest discover -s tests -v
    bash experiments/exp01_recovery_metrics/run.sh --inside-environment "${run_id}_exp01"
    bash experiments/exp02_joint_profile_activation/run.sh --inside-environment "${run_id}_exp02"
    bash experiments/exp03_online_joint_recovery/run.sh --inside-environment "${run_id}_exp03"
    bash experiments/exp04_smt_schedule/run.sh --inside-environment "${run_id}_exp04"
    bash experiments/exp05_scenario_framework/run.sh --inside-environment "${run_id}_exp05"
    python3 tools/precompute_profiles.py --scenario configs/scenarios/diamond.yaml --strategy per-failure --inside-environment --skip-build
    python3 tools/precompute_profiles.py --scenario configs/scenarios/mesh10.yaml --strategy per-failure --inside-environment --skip-build
    python3 tools/run_experiment.py --scenario configs/scenarios/diamond.yaml --mode all --fault l_s1_s2 --inside-environment --skip-build --run-id "$run_id"
    python3 tools/run_experiment.py --scenario configs/scenarios/diamond.yaml --mode all --fault l_s1_s3 --inside-environment --skip-build --run-id "$run_id"
    python3 tools/run_experiment.py --scenario configs/scenarios/diamond.yaml --mode all --fault l_s2_s4 --inside-environment --skip-build --run-id "$run_id"
    python3 tools/run_experiment.py --scenario configs/scenarios/diamond.yaml --mode all --fault l_s3_s4 --inside-environment --skip-build --run-id "$run_id"
    python3 tools/run_experiment.py --scenario configs/scenarios/mesh10.yaml --mode all --fault l_sw2_sw5 --inside-environment --skip-build --run-id "$run_id"
    python3 tools/run_experiment.py --scenario configs/scenarios/mesh10.yaml --mode all --fault l_sw3_sw6 --inside-environment --skip-build --run-id "$run_id"
    python3 tools/run_experiment.py --scenario configs/scenarios/mesh10.yaml --mode all --fault l_sw6_sw8 --inside-environment --skip-build --run-id "$run_id"
    python3 tools/run_experiment.py --scenario configs/scenarios/mesh10.yaml --mode all --fault l_sw7_sw9 --inside-environment --skip-build --run-id "$run_id"
    python3 tools/run_experiment.py --scenario configs/scenarios/diamond.yaml --mode offline-per-failure --fault l_s1_s2 --inside-environment --skip-build --run-id "${run_id}_lookup100us" --offline-lookup-delay-us 100
    python3 tools/run_experiment.py --scenario configs/scenarios/mesh10.yaml --mode offline-per-failure --fault l_sw2_sw5 --inside-environment --skip-build --run-id "${run_id}_lookup100us" --offline-lookup-delay-us 100
    mkdir -p "$OUTPUT/runs/$run_id"
    python3 scripts/analyze_offline_per_failure.py --run-id "$run_id" --code-commit "$code_commit" --output-dir "$OUTPUT/runs/$run_id"
    python3 - "$PROJECT_ROOT" "$run_id" <<'PY'
import csv, shutil, sys
from pathlib import Path
root, run_id = Path(sys.argv[1]), sys.argv[2]
out = root / "results/offline_per_failure"
run = out / "runs" / run_id
for item in run.iterdir():
    if item.is_file(): shutil.copy2(item, out / item.name)
for name in ("manifests", "profile_stores"):
    target = out / name
    if target.exists(): shutil.rmtree(target)
    shutil.copytree(run / name, target)
rows=[]
for scenario, fault in (("diamond","l_s1_s2"),("mesh10","l_sw2_sw5")):
    path=root/f"results/scenarios/{scenario}/offline-per-failure/{fault}/{run_id}_lookup100us/summary.csv"
    with path.open() as handle: row=next(csv.DictReader(handle))
    rows.append({"scenario":scenario,"fault_id":fault,"offline_lookup_delay_us":100,
                 "recovery_duration_us":float(row["recovery_duration_s"])*1e6,
                 "tt_lost":row["tt_lost"],"deadline_miss_count":row["deadline_miss_count"],
                 "runtime_route_solver_invocations":row["runtime_route_solver_invocations"],
                 "runtime_z3_solver_invocations":row["runtime_z3_solver_invocations"]})
with (out/"lookup_delay_sensitivity.csv").open("w",newline="") as handle:
    writer=csv.DictWriter(handle,fieldnames=list(rows[0]),lineterminator="\n");writer.writeheader();writer.writerows(rows)
PY
}

if [[ "${1:-}" == "--inside-environment" ]]; then inside_environment "$2"; exit 0; fi
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
source "$WORKSPACE_ROOT/.venv/bin/activate"
source "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE_ROOT"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$RUN_ID'"
echo "Completed exp06 run $RUN_ID"
