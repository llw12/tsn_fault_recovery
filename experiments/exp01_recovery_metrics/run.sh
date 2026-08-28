#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)"
OUTPUT_ROOT="$PROJECT_ROOT/results/recovery_metrics"

run_scenario() {
    local scenario="$1"
    local simulation_dir="$2"
    local config_name="$3"
    local raw_root="$4"
    local result_dir="$raw_root/$scenario"
    mkdir -p "$result_dir"

    local command=(
        "$PROJECT_ROOT/tsn_fault_recovery"
        -u Cmdenv
        -n "$PROJECT_ROOT/src:$WORKSPACE_ROOT/inet-4.7.0/src"
        -i "$WORKSPACE_ROOT/inet-4.7.0/images"
        -f omnetpp.ini
        --cmdenv-express-mode=true
        --result-dir="$result_dir"
    )
    if [[ -n "$config_name" ]]; then
        command+=( -c "$config_name" )
    fi

    echo "Running $scenario..."
    (
        cd "$simulation_dir"
        "${command[@]}"
    )
}

run_inside_environment() {
    local run_id="$1"
    local raw_root="$SCRIPT_DIR/raw/$run_id"

    echo "Building tsn_fault_recovery (release)..."
    make -C "$PROJECT_ROOT" MODE=release -j"$(nproc)"

    run_scenario baseline "$PROJECT_ROOT/simulations/baseline" "" "$raw_root"
    run_scenario failure "$PROJECT_ROOT/simulations/failure" LinkFailure "$raw_root"
    run_scenario recovery "$PROJECT_ROOT/simulations/recovery" ManualRecovery "$raw_root"

    python3 "$PROJECT_ROOT/scripts/analyze_recovery.py" \
        --raw-root "$raw_root" \
        --output-dir "$OUTPUT_ROOT" \
        --check-regression
}

if [[ "${1:-}" == "--inside-environment" ]]; then
    run_inside_environment "$2"
    exit 0
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ ! -f "$WORKSPACE_ROOT/.venv/bin/activate" ]]; then
    echo "Missing opp_env virtual environment: $WORKSPACE_ROOT/.venv" >&2
    exit 1
fi
if [[ ! -f "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh" ]]; then
    echo "Missing Nix profile: $WORKSPACE_ROOT/.nix-profile" >&2
    exit 1
fi

source "$WORKSPACE_ROOT/.venv/bin/activate"
source "$WORKSPACE_ROOT/.nix-profile/etc/profile.d/nix.sh"

cd "$WORKSPACE_ROOT"
opp_env run inet-4.7.0 -q -c \
    "bash '$SCRIPT_DIR/run.sh' --inside-environment '$RUN_ID'"

echo "Completed run $RUN_ID"
echo "Raw OMNeT++ results: $SCRIPT_DIR/raw/$RUN_ID"
echo "Analyzed results: $OUTPUT_ROOT"
