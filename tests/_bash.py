"""Absolute path of a real POSIX bash for the suites' subprocess calls.

Windows resolves a bare "bash" through System32 before PATH, which is the WSL
launcher; on a host with no distro (GitHub's windows runner) it only prints
"Windows Subsystem for Linux has no installed distributions". Same rule as
scripts/goals.py `_shell()`, but walking PATH so Git Bash is found even when
System32 comes first.
"""

import os
import shutil


def _find() -> str:
    for d in os.environ.get("PATH", "").split(os.pathsep):
        b = shutil.which("bash", path=d) if d else None
        if b and not (os.name == "nt" and ("system32" in b.lower() or "windowsapps" in b.lower())):
            return b
    return "bash"


BASH = _find()
