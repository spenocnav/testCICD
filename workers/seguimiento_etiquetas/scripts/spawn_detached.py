"""Spawn one managed Python child without inheriting the caller's pipe handles.

Windows PowerShell 5 may pass its redirected stdout/stderr handles through
``Start-Process``.  A caller waiting with captured output then never observes
EOF while the long-running child is alive.  Python's ``close_fds`` plus explicit
log handles provides the narrow handle allowlist needed by the process scripts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--stdout-log", type=Path, required=True)
    parser.add_argument("--stderr-log", type=Path, required=True)
    parser.add_argument("child_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    script = args.script.resolve()
    stdout_log = args.stdout_log.resolve()
    stderr_log = args.stderr_log.resolve()
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.parent.mkdir(parents=True, exist_ok=True)
    child_arguments = list(args.child_arguments)
    if child_arguments[:1] == ["--"]:
        child_arguments = child_arguments[1:]

    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )
    with stdout_log.open("ab") as stdout_handle, stderr_log.open("ab") as stderr_handle:
        process = subprocess.Popen(
            [args.python, "-u", str(script), *child_arguments],
            cwd=script.parent,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            close_fds=True,
            creationflags=creation_flags,
        )
    print(json.dumps({"pid": process.pid}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
