#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)"
OUTPUT="$PROJECT_ROOT/results/scenario_framework"

inside_environment() {
    make -C "$PROJECT_ROOT" -j"$(nproc)"
    (cd "$PROJECT_ROOT" && python3 -m unittest discover -s tests -v)
    (cd "$PROJECT_ROOT/simulations/scenario_framework_tests" && "$PROJECT_ROOT/tsn_fault_recovery" -u Cmdenv -n "$PROJECT_ROOT/src:$WORKSPACE_ROOT/inet-4.7.0/src" -l "$WORKSPACE_ROOT/inet-4.7.0/src/INET" -f omnetpp.ini)
    (cd "$PROJECT_ROOT" && python3 tools/run_experiment.py --scenario configs/scenarios/diamond.yaml --mode all --fault l_s1_s2 --inside-environment --skip-build)
    (cd "$PROJECT_ROOT" && python3 tools/run_experiment.py --scenario configs/scenarios/mesh10.yaml --mode all --fault l_sw2_sw5 --inside-environment --skip-build)
    local diamond_no diamond_on mesh_no mesh_on
    diamond_no="$(find "$PROJECT_ROOT/results/scenarios/diamond/no-recovery/l_s1_s2" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
    diamond_on="$(find "$PROJECT_ROOT/results/scenarios/diamond/online/l_s1_s2" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
    mesh_no="$(find "$PROJECT_ROOT/results/scenarios/mesh10/no-recovery/l_sw2_sw5" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
    mesh_on="$(find "$PROJECT_ROOT/results/scenarios/mesh10/online/l_sw2_sw5" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
    python3 "$PROJECT_ROOT/scripts/analyze_scenario_framework.py" --output-dir "$OUTPUT" --run-dir "$diamond_no" --run-dir "$diamond_on" --run-dir "$mesh_no" --run-dir "$mesh_on"
}

if [[ "${1:-}" == "--inside-environment" ]]; then inside_environment; exit 0; fi
source "$WORKSPACE_ROOT/.venv/bin/activate"
source "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE_ROOT"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment"
