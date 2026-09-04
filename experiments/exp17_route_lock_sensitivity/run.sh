#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
mode=full
if [[ ${1:-} == --quick ]]; then mode=quick; fi
python3 -m tools.run_route_lock_sensitivity --mode "$mode"
