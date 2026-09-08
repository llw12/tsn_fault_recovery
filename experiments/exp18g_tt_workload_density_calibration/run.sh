#!/usr/bin/env bash
set -euo pipefail

# This is healthy-P0 density calibration only: it never starts a PF, OMNeT++,
# INET, fault-enumeration, profile-store, or parallelism campaign.
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_root"

if [[ "${1:-}" == "--quick" && $# -eq 1 ]]; then
    python3 -m tools.run_tt_workload_density_calibration --quick
elif [[ $# -eq 0 ]]; then
    python3 -m tools.run_tt_workload_density_calibration --implementation-commit "$(git rev-parse HEAD)"
else
    echo "usage: $0 [--quick]" >&2
    exit 64
fi
