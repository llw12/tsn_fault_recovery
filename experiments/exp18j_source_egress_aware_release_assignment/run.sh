#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

# The runner invokes the existing project virtual environment only for its
# exact Z3 sub-process.  It never installs dependencies.
exec python3 -m tools.run_source_egress_release_assignment "$@"
