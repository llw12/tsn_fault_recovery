#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)"
OUTPUT_ROOT="$PROJECT_ROOT/results/online_joint_recovery"

run_scenario() {
    local key="$1" simulation_dir="$2" config="$3" raw_root="$4"
    local destination="$raw_root/$key"
    mkdir -p "$destination"
    local command=("$PROJECT_ROOT/tsn_fault_recovery" -u Cmdenv
        -n "$PROJECT_ROOT/src:$WORKSPACE_ROOT/inet-4.7.0/src"
        -i "$WORKSPACE_ROOT/inet-4.7.0/images" -f omnetpp.ini
        --cmdenv-express-mode=false --result-dir="$destination")
    [[ -n "$config" ]] && command+=(-c "$config")
    echo "Running $key..."
    (cd "$simulation_dir" && "${command[@]}") >"$destination/run.log" 2>&1
}

inside_environment() {
    local run_id="$1"
    local raw_root="$SCRIPT_DIR/raw/$run_id"
    local run_output="$OUTPUT_ROOT/runs/$run_id"
    make -C "$PROJECT_ROOT" MODE=release -j"$(nproc)"
    run_scenario baseline "$PROJECT_ROOT/simulations/baseline" "" "$raw_root"
    run_scenario failure "$PROJECT_ROOT/simulations/failure" LinkFailure "$raw_root"
    run_scenario online "$PROJECT_ROOT/simulations/online_joint_recovery" OnlineJointRecovery "$raw_root"
    python3 "$PROJECT_ROOT/scripts/analyze_joint_experiment.py" --mode online --raw-root "$raw_root" --output-dir "$run_output"
    mkdir -p "$OUTPUT_ROOT"
    cp "$run_output"/{summary.csv,online_tt_packets.csv,summary.md,tt_timeline.png,timing.csv,gcl_activation_log.csv} "$OUTPUT_ROOT/"
    local previous
    previous="$(find "$OUTPUT_ROOT/runs" -mindepth 1 -maxdepth 1 -type d ! -name "$run_id" | sort | tail -n 1)"
    if [[ -n "$previous" ]]; then
        python3 "$PROJECT_ROOT/scripts/compare_experiment_runs.py" --first "$previous/summary.csv" --second "$run_output/summary.csv" --output "$OUTPUT_ROOT/reproducibility.csv" --timing-output "$OUTPUT_ROOT/wall_clock_runs.csv"
    fi
}

if [[ "${1:-}" == "--inside-environment" ]]; then
    inside_environment "$2"
    exit 0
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%S%NZ)"
source "$WORKSPACE_ROOT/.venv/bin/activate"
source "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE_ROOT"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$RUN_ID'"
echo "Completed exp03 run $RUN_ID"
