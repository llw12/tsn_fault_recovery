#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE="$(cd -- "$ROOT/.." && pwd)"
OUTPUT="$ROOT/results/topology_redundancy"

inside() {
  local run_id="$1" implementation temp_dir
  cd "$ROOT"
  echo "PHASE 1 repo / environment / clean-tree preflight"
  git diff --quiet && git diff --cached --quiet || { echo "Formal exp12 requires a clean tracked working tree" >&2; exit 1; }
  [[ ! -e "$OUTPUT" ]] || { echo "Formal exp12 output already exists: $OUTPUT" >&2; exit 1; }
  implementation="$(git rev-parse HEAD)"
  z3 --version

  echo "PHASE 2 tests + selected semantic regression"
  python3 -m unittest discover -s tests -v
  make MODE=release -j2
  (cd simulations/smt_validation && "$ROOT/tsn_fault_recovery" -u Cmdenv -n "$ROOT/src:$WORKSPACE/inet-4.7.0/src" -l "$WORKSPACE/inet-4.7.0/src/INET" -f omnetpp.ini -c SmtUnitTests --cmdenv-express-mode=true --result-dir=/tmp/exp12-smt-unit-results)
  python3 tools/check_smt_semantic_regression.py
  python3 tools/analyze_critical_links.py --scenario configs/scenarios/diamond.yaml --inside-environment --skip-build >/dev/null
  python3 tools/analyze_critical_links.py --scenario configs/scenarios/mesh10.yaml --inside-environment --skip-build >/dev/null
  python3 tools/analyze_critical_links.py --scenario configs/scenarios/structured20_auto.yaml --inside-environment --skip-build >/dev/null

  echo "PHASE 3 generate R0-R4"
  python3 tools/generate_redundancy_scenarios.py >/dev/null
  echo "PHASE 4 nested topology audit"
  echo "PHASE 5 workload identity audit"
  echo "PHASE 6 R1 current structured40 edge-set audit"
  python3 -m unittest tests.test_topology_redundancy -q
  echo "PHASE 7 R0 P0 route/schedule preflight"
  echo "PHASE 8 freeze primary routes"
  echo "PHASE 9 candidate / affected-set / Jaccard / group identity audit"
  echo "PHASE 10 topology redundancy metrics"
  echo "PHASE 11 PF sweep R0-R4"
  echo "PHASE 12 J100 shared campaign"
  echo "PHASE 13 J040 shared campaign"
  echo "PHASE 14 J020 shared campaign"
  python3 tools/run_redundancy_campaign.py --run-id "$run_id" --implementation-commit "$implementation" --inside-environment
  echo "PHASE 15 cross-level connectivity transitions"
  echo "PHASE 16 route edge-layer usage"
  echo "PHASE 17 quality comparison"
  echo "PHASE 18 native-P0 diagnostic"
  echo "PHASE 19 analysis + figures"
  python3 tools/analyze_topology_redundancy.py --results "$OUTPUT"
  echo "PHASE 20 deterministic artifact audit"
  temp_dir="$(mktemp -d)"
  cp -a "$OUTPUT" "$temp_dir/output"
  python3 tools/analyze_topology_redundancy.py --results "$temp_dir/output" >/dev/null
  while IFS= read -r artifact; do cmp "$OUTPUT/$artifact" "$temp_dir/output/$artifact"; done < <(
    python3 - "$OUTPUT/analysis_manifest.json" <<'PY'
import json,sys
x=json.load(open(sys.argv[1]))
for name in sorted(x["artifact_sha256"]):
    if name not in {"campaign.json","controlled_variables.json","frozen_primary_routes.json"}: print(name)
print("analysis_manifest.json")
PY
  )
  rm -rf -- "$temp_dir"
  echo "PHASE 21 disk / manifest audit"
  python3 - "$OUTPUT" <<'PY'
import csv,hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); manifest=json.load(open(root/"analysis_manifest.json")); campaign=json.load(open(root/"campaign.json"))
assert manifest["figure_count"] == 14
assert len(list((root/"figures").glob("*.png"))) == 14
assert campaign["implementation_commit"] == campaign["identity"]["implementation_commit"]
assert hashlib.sha256(open("results/scalability/campaign.json","rb").read()).hexdigest() == "93ffb1fab5670075fe9d74899844b584481e4b4e68b2dbda9f5371beff31c278"
assert hashlib.sha256(open("results/smt_scalability/campaign.json","rb").read()).hexdigest() == "c7b85e2258851bb6f65a4c55d23d07d0883348a723f38827b04dd34e8cce48d1"
assert not any(p.stat().st_size > 20*1024*1024 for p in root.rglob("*") if p.is_file())
top=list(csv.DictReader((root/"topology_metrics.csv").open()))
assert [int(r["switch_edge_count"]) for r in top] == [67,75,80,100,120]
print("EXP12 artifact audit PASS")
PY
  echo "EXP12 PASS"
}

if [[ "${1:-}" == "--inside-environment" ]]; then inside "$2"; exit 0; fi
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
source "$WORKSPACE/.venv/bin/activate"
source "$WORKSPACE/.nix-profile/etc/profile.d/nix.sh"
cd "$WORKSPACE"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$run_id'"
