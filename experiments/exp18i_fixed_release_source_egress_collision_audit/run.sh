#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

# This runner is static-only.  It reads frozen scenario/result bytes and never
# invokes H2S, CELF, PF, OMNeT++, or INET.
exec python3 -m tools.run_fixed_release_collision_audit "$@"
