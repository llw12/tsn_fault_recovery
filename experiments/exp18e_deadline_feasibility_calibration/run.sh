#!/usr/bin/env bash
set -euo pipefail

# exp18e has no PF, OMNeT++, or INET stage.  --quick is deliberately isolated
# below quick_validation/; full is the sole 6 x 5 x 2 evidence campaign.
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_root"

if [[ "${1:-}" == "--quick" && $# -eq 1 ]]; then
    python3 -m tools.run_deadline_feasibility_calibration --quick
elif [[ $# -eq 0 ]]; then
    python3 -m tools.run_deadline_feasibility_calibration --implementation-commit "$(git rev-parse HEAD)"
else
    echo "usage: $0 [--quick]" >&2
    exit 64
fi
