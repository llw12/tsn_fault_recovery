#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM_REPOSITORY="https://github.com/gepperho/AdvancedFlowScheduler.git"
UPSTREAM_COMMIT="650a9665e7bafb70fcf19c9f0a247e1d7b885ffd"
CHECKOUT="$ROOT/.external/AdvancedFlowScheduler"
PATCH="$ROOT/third_party_patches/advanced_flow_scheduler/exp15_semantics.patch"
ROUTE_LOCK_PATCH="$ROOT/third_party_patches/advanced_flow_scheduler/exp16_route_lock.patch"
CMAKE="$ROOT/.venv-h2s/bin/cmake"

if [[ ! -x "$CMAKE" ]]; then
  echo "Missing $CMAKE; run: uv venv --python 3.14 .venv-h2s && uv pip install --python .venv-h2s/bin/python -r requirements-h2s.txt" >&2
  exit 2
fi
if [[ ! -d "$CHECKOUT/.git" ]]; then
  git clone "$UPSTREAM_REPOSITORY" "$CHECKOUT"
fi
git -C "$CHECKOUT" fetch --quiet origin "$UPSTREAM_COMMIT"
git -C "$CHECKOUT" reset --hard "$UPSTREAM_COMMIT"
git -C "$CHECKOUT" clean -fdx
git -C "$CHECKOUT" apply --check "$PATCH"
git -C "$CHECKOUT" apply "$PATCH"
git -C "$CHECKOUT" apply --check "$ROUTE_LOCK_PATCH"
git -C "$CHECKOUT" apply "$ROUTE_LOCK_PATCH"
H2S_CMAKE_EXTRA=()
if [[ -d "$CHECKOUT/build-release/_deps/nlohmann_json-src" &&
      -d "$CHECKOUT/build-release/_deps/namedtype-src" &&
      -d "$CHECKOUT/build-release/_deps/cli11-src" &&
      -d "$CHECKOUT/build-release/_deps/fmt-src" ]]; then
  H2S_CMAKE_EXTRA=(-DFETCHCONTENT_FULLY_DISCONNECTED=ON)
fi
"$CMAKE" -S "$CHECKOUT" -B "$CHECKOUT/build-release" -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTS=OFF "${H2S_CMAKE_EXTRA[@]}"
"$CMAKE" --build "$CHECKOUT/build-release" --parallel 2
test "$(git -C "$CHECKOUT" rev-parse HEAD)" = "$UPSTREAM_COMMIT"
sha256sum "$PATCH" "$ROUTE_LOCK_PATCH" "$CHECKOUT/build-release/AdvancedFlowSchedulerExec"
