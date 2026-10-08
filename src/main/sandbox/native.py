"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .base import CommandResult, execute


def safe_environment() -> dict[str, str]:
    allowed = {"PATH", "LANG", "TERM"}
    if os.name == "nt":
        allowed.update({"SYSTEMROOT", "WINDIR", "PATHEXT"})
    return {
        key: value for key, value in os.environ.items()
        if key.upper() in allowed or key.startswith("LC_")
    }


class NativeRunner:

    def run(
        self, args: list[str], *, workspace: Path, timeout: int, writable: bool,
    ) -> CommandResult:
        return execute(args, workspace=workspace, timeout=timeout)


class FallbackRunner:

    def run(
        self, args: list[str], *, workspace: Path, timeout: int, writable: bool,
    ) -> CommandResult:
        with tempfile.TemporaryDirectory(prefix="neuro-command-") as directory:
            root = Path(directory)
            home, tmp = root / "home", root / "tmp"
            home.mkdir()
            tmp.mkdir()
            env = safe_environment()
            env.update(HOME=str(home), USERPROFILE=str(home), TMPDIR=str(tmp),
                       TMP=str(tmp), TEMP=str(tmp))
            return execute(args, workspace=workspace, timeout=timeout, env=env)
