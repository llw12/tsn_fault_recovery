#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_path="${JRS_SCIP_VENV:-${repo_root}/.venv-jrs-scip}"

if command -v uv >/dev/null 2>&1; then
    [[ -x "${venv_path}/bin/python" ]] || uv venv --python python3 --seed "${venv_path}"
    uv pip install --python "${venv_path}/bin/python" -r "${repo_root}/requirements-jrs-scip.txt"
else
    [[ -x "${venv_path}/bin/python" ]] || python3 -m venv "${venv_path}"
    "${venv_path}/bin/python" -m pip install -r "${repo_root}/requirements-jrs-scip.txt"
fi

"${venv_path}/bin/python" - <<'PY'
import pyscipopt
from pyscipopt import Model

model = Model()
print("PySCIPOpt:", pyscipopt.__version__)
print("SCIP:", f"{model.getMajorVersion()}.{model.getMinorVersion()}.{model.getTechVersion()}")
PY
