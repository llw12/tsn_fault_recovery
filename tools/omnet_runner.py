"""Small OMNeT++ Cmdenv process adapter shared by experiment CLIs."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_omnet(generated: Path, config: str, result_dir: Path, log: Path,
              overrides: list[str] | None = None) -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    command = [str(ROOT / "tsn_fault_recovery"), "-u", "Cmdenv", "-n",
               f"{generated.parent}:{ROOT / 'src'}:/home/opp_env/inet-4.7.0/src",
               "-l", "/home/opp_env/inet-4.7.0/src/INET", "-f", "omnetpp.ini", "-c", config,
               f"--result-dir={result_dir}"]
    command += overrides or []
    completed = subprocess.run(command, cwd=generated, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"OMNeT++ config {config} failed; see {log}")
