#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

# This command is intentionally an analysis-only runner.  It never invokes an
# H2S/CELF backend, an upstream scheduler executable, OMNeT++, or INET.  The
# existing JRS environment contributes only Z3 for Boolean set cover.
python_bin=".venv-jrs/bin/python"
if [[ ! -x "$python_bin" ]]; then
    python_bin="python3"
fi
exec "$python_bin" -m tools.run_pf_redundancy_opportunity_audit "$@"
