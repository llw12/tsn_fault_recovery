"""Run one H2S process and report its isolated maximum RSS."""

from __future__ import annotations

import resource
import subprocess
import sys


RSS_MARKER = "__H2S_MAX_RSS_KB__="


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        raise SystemExit("usage: h2s_process_runner.py MEMORY_LIMIT_MB -- COMMAND [ARGS...]")
    limit = int(sys.argv[1]) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    completed = subprocess.run(sys.argv[3:], check=False)
    print(f"{RSS_MARKER}{resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}", file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
