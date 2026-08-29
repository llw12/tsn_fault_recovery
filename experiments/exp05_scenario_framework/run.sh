#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)"
OUTPUT="$PROJECT_ROOT/results/scenario_framework"

inside_environment() {
    local run_id="$1"
    make -C "$PROJECT_ROOT" -j"$(nproc)"
    (cd "$PROJECT_ROOT" && python3 -m unittest discover -s tests -v)
    (cd "$PROJECT_ROOT/simulations/scenario_framework_tests" && "$PROJECT_ROOT/tsn_fault_recovery" -u Cmdenv -n "$PROJECT_ROOT/src:$WORKSPACE_ROOT/inet-4.7.0/src" -l "$WORKSPACE_ROOT/inet-4.7.0/src/INET" -f omnetpp.ini)
    (cd "$PROJECT_ROOT" && python3 tools/run_experiment.py --scenario configs/scenarios/diamond.yaml --mode no-recovery --fault l_s1_s2 --inside-environment --skip-build --run-id "$run_id")
    (cd "$PROJECT_ROOT" && python3 tools/run_experiment.py --scenario configs/scenarios/diamond.yaml --mode online --fault l_s1_s2 --inside-environment --skip-build --run-id "$run_id")
    (cd "$PROJECT_ROOT" && python3 tools/run_experiment.py --scenario configs/scenarios/mesh10.yaml --mode no-recovery --fault l_sw2_sw5 --inside-environment --skip-build --run-id "$run_id")
    (cd "$PROJECT_ROOT" && python3 tools/run_experiment.py --scenario configs/scenarios/mesh10.yaml --mode online --fault l_sw2_sw5 --inside-environment --skip-build --run-id "$run_id")
    local diamond_no diamond_on mesh_no mesh_on
    diamond_no="$PROJECT_ROOT/results/scenarios/diamond/no-recovery/l_s1_s2/$run_id"
    diamond_on="$PROJECT_ROOT/results/scenarios/diamond/online/l_s1_s2/$run_id"
    mesh_no="$PROJECT_ROOT/results/scenarios/mesh10/no-recovery/l_sw2_sw5/$run_id"
    mesh_on="$PROJECT_ROOT/results/scenarios/mesh10/online/l_sw2_sw5/$run_id"
    python3 "$PROJECT_ROOT/scripts/analyze_scenario_framework.py" --output-dir "$OUTPUT" --run-dir "$diamond_no" --run-dir "$diamond_on" --run-dir "$mesh_no" --run-dir "$mesh_on"
}

if [[ "${1:-}" == "--inside-environment" ]]; then inside_environment "$2"; exit 0; fi
source "$WORKSPACE_ROOT/.venv/bin/activate"
source "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE_ROOT"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$RUN_ID'"
