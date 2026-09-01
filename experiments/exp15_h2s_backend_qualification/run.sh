#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE="full"
if [[ "${1:-}" == "--quick" ]]; then MODE="quick"; shift; fi
if [[ "${1:-}" == "--qualification" ]]; then MODE="qualification"; shift; fi
cd "$ROOT"
exec .venv-jrs/bin/python -m tools.run_h2s_backend_qualification --mode "$MODE" "$@"
