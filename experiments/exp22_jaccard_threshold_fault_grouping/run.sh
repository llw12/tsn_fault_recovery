#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"
python_bin=".venv-jrs/bin/python"
[[ -x "$python_bin" ]] || python_bin="python3"
exec "$python_bin" -m tools.run_jaccard_threshold_fault_grouping "$@"
