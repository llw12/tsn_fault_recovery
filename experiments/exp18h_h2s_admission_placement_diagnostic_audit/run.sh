#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec python3 -m tools.run_h2s_admission_placement_diagnostic "$@"
