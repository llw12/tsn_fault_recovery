#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_path="${JRS_VENV:-${repo_root}/.venv-jrs}"

if command -v uv >/dev/null 2>&1; then
    if [[ ! -x "${venv_path}/bin/python" ]]; then
        uv venv --python python3 "${venv_path}"
    fi
    CC="${CC:-gcc}" CXX="${CXX:-g++}" uv pip install --python "${venv_path}/bin/python" -r "${repo_root}/requirements-jrs.txt"
else
    if [[ ! -x "${venv_path}/bin/python" ]]; then
        python3 -m venv "${venv_path}"
    fi
    "${venv_path}/bin/python" -m pip install --upgrade pip
    "${venv_path}/bin/python" -m pip install -r "${repo_root}/requirements-jrs.txt"
fi
"${venv_path}/bin/python" - <<'PY'
import gurobipy
import tsnkit

print("TSNKit import: PASS")
print("gurobipy:", ".".join(map(str, gurobipy.gurobi.version())))
PY
