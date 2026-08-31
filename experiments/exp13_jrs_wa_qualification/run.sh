#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
ENV_ROOT="$(cd -- "$ROOT/.." && pwd)"
OUTPUT="$ROOT/results/jrs_wa_qualification"
JRS_PYTHON="$ROOT/.venv-jrs/bin/python"

inside() {
  local run_id="$1" implementation="$2"
  cd "$ROOT"
  echo "PHASE 1 repo state / exp12 artifact preflight"
  git diff --quiet && git diff --cached --quiet || { echo "Formal exp13 requires a clean tracked working tree" >&2; exit 1; }
  [[ "$(git rev-parse HEAD)" == "$implementation" ]] || { echo "Implementation commit mismatch" >&2; exit 1; }
  [[ ! -e "$OUTPUT" ]] || { echo "Formal exp13 output already exists: $OUTPUT" >&2; exit 1; }

  echo "PHASE 2 pinned TSNKit / Gurobi environment"
  [[ -x "$JRS_PYTHON" ]] || { echo "Run scripts/setup_jrs_backend.sh first" >&2; exit 1; }
  PYTHONPATH= "$JRS_PYTHON" -c 'import gurobipy,tsnkit; print("gurobipy", gurobipy.gurobi.version()); print("tsnkit", tsnkit.__version__)'

  echo "PHASE 3 unit, C++, SMT, and legacy regression"
  python3 -m unittest discover -s tests -v
  make MODE=release -j2
  (cd simulations/smt_validation && "$ROOT/tsn_fault_recovery" -u Cmdenv -n "$ROOT/src:$ENV_ROOT/inet-4.7.0/src" -l "$ENV_ROOT/inet-4.7.0/src/INET" -f omnetpp.ini -c SmtUnitTests --cmdenv-express-mode=true --result-dir=/tmp/exp13-smt-unit-results)
  python3 tools/check_smt_semantic_regression.py

  echo "PHASE 4-15 deterministic selection, JRS-WA synthesis, profile conversion, and OMNeT validation"
  PYTHONPATH= "$JRS_PYTHON" tools/run_jrs_wa_qualification.py --run-id "$run_id" --implementation-commit "$implementation"

  echo "PHASE 16 structured analysis (no plots)"
  python3 tools/analyze_jrs_wa_qualification.py --results "$OUTPUT"

  echo "PHASE 17 deterministic analyzer and artifact audit"
  cp "$OUTPUT/analysis_manifest.json" /tmp/exp13-analysis-manifest.json
  python3 tools/analyze_jrs_wa_qualification.py --results "$OUTPUT" >/tmp/exp13-analysis-repeat.log
  cmp /tmp/exp13-analysis-manifest.json "$OUTPUT/analysis_manifest.json"
  PYTHONPATH= "$JRS_PYTHON" - "$OUTPUT" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); manifest=json.loads((root/'analysis_manifest.json').read_text())
assert manifest['plot_file_count'] == 0
assert manifest['implementation_commit'] == json.loads((root/'campaign.json').read_text())['implementation_commit']
assert hashlib.sha256(pathlib.Path('results/topology_redundancy/campaign.json').read_bytes()).hexdigest() == 'c306a4d5de34761aba96dead957bdcda27cbaed7e3614bd573effd8515333274'
assert len(list((root/'backend_results').glob('Q*.json'))) == 10
print('EXP13 artifact audit PASS')
PY
  echo "EXP13 formal qualification complete"
}

if [[ "${1:-}" == "--inside-environment" ]]; then inside "$2" "$3"; exit 0; fi
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
implementation="$(git -C "$ROOT" rev-parse HEAD)"
source "$ENV_ROOT/.venv/bin/activate"
source "$ENV_ROOT/.nix-profile/etc/profile.d/nix.sh"
cd "$ENV_ROOT"
opp_env run inet-4.7.0 -q -c "bash '$SCRIPT_DIR/run.sh' --inside-environment '$run_id' '$implementation'"
