#!/usr/bin/env bash
set -euo pipefail

# exp18f is an H2S built-in-sorter qualification only. It has no PF, OMNeT++,
# INET, profile-store, or fault-enumeration phase.
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_root"

if [[ "${1:-}" == "--quick" && $# -eq 1 ]]; then
    python3 -m tools.run_h2s_primary_policy_sensitivity --quick
elif [[ $# -eq 0 ]]; then
    python3 -m tools.run_h2s_primary_policy_sensitivity --implementation-commit "$(git rev-parse HEAD)"
else
    echo "usage: $0 [--quick]" >&2
    exit 64
fi
