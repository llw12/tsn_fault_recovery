#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)"
OUTPUT_ROOT="$PROJECT_ROOT/results/smt_schedule"

run_scenario() {
    local key="$1" simulation_dir="$2" config="$3" raw_root="$4"
    local destination="$raw_root/$key"
    mkdir -p "$destination"
    echo "Running $key ($config)..."
    (cd "$simulation_dir" && "$PROJECT_ROOT/tsn_fault_recovery" -u Cmdenv \
        -n "$PROJECT_ROOT/src:$WORKSPACE_ROOT/inet-4.7.0/src" \
        -i "$WORKSPACE_ROOT/inet-4.7.0/images" -f omnetpp.ini -c "$config" \
        --cmdenv-express-mode=false --result-dir="$destination") >"$destination/run.log" 2>&1
}

inside_environment() {
    local run_id="$1"
    local raw_root="$SCRIPT_DIR/raw/$run_id"
    local run_output="$OUTPUT_ROOT/runs/$run_id"
    make -C "$PROJECT_ROOT" MODE=release -j"$(nproc)"

    run_scenario unit "$PROJECT_ROOT/simulations/smt_validation" SmtUnitTests "$raw_root"
    run_scenario single "$PROJECT_ROOT/simulations/smt_validation" SmtSingleFlowCompatibility "$raw_root"
    run_scenario multi_sat "$PROJECT_ROOT/simulations/smt_validation" SmtMultiFlowSat "$raw_root"
    run_scenario multi_unsat "$PROJECT_ROOT/simulations/smt_validation" SmtMultiFlowUnsat "$raw_root"
    run_scenario online_delay_0_1 "$PROJECT_ROOT/simulations/online_smt_recovery" OnlineSmtDelay01 "$raw_root"
    run_scenario online_delay_1 "$PROJECT_ROOT/simulations/online_smt_recovery" OnlineSmtDelay1 "$raw_root"
    run_scenario online_delay_5 "$PROJECT_ROOT/simulations/online_smt_recovery" OnlineSmtDelay5 "$raw_root"
    run_scenario online_delay_10 "$PROJECT_ROOT/simulations/online_smt_recovery" OnlineSmtDelay10 "$raw_root"

    python3 "$PROJECT_ROOT/scripts/analyze_smt_schedule.py" --raw-root "$raw_root" --output-dir "$run_output"
    mkdir -p "$OUTPUT_ROOT"
    cp "$run_output"/{solver_summary.csv,flow_summary.csv,packet_results.csv,gcl.csv,solver_delay_sensitivity.csv,summary.md,chart_map.md,gcl_timeline.png,recovery_vs_solver_delay.png} "$OUTPUT_ROOT/"

    local previous
    previous="$(find "$OUTPUT_ROOT/runs" -mindepth 2 -maxdepth 2 -type f -name solver_summary.csv \
        ! -path "$run_output/solver_summary.csv" -printf '%h\n' | sort | tail -n 1)"
    if [[ -n "$previous" ]]; then
        python3 "$PROJECT_ROOT/scripts/compare_smt_runs.py" \
            --first "$previous" --second "$run_output" \
            --output "$OUTPUT_ROOT/reproducibility.csv" \
            --wall-output "$OUTPUT_ROOT/wall_clock_runs.csv"
    fi
}

if [[ "${1:-}" == "--inside-environment" ]]; then
    inside_environment "$2"
    exit 0
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SN)"
source "$WORKSPACE_ROOT/.venv/bin/activate"
source "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE_ROOT"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$RUN_ID'"
echo "Completed exp04 run $RUN_ID"
