from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .base import CommandResult, execute


def safe_environment() -> dict[str, str]:
    """Allow only execution/locale settings, never arbitrary application secrets."""
    allowed = {"PATH", "LANG", "TERM"}
    if os.name == "nt":
        allowed.update({"SYSTEMROOT", "WINDIR", "PATHEXT"})
    return {
        key: value for key, value in os.environ.items()
        if key.upper() in allowed or key.startswith("LC_")
    }


class NativeRunner:
    """Unrestricted execution for Full Control Mode."""

    def run(
        self, args: list[str], *, workspace: Path, timeout: int, writable: bool,
    ) -> CommandResult:
        return execute(args, workspace=workspace, timeout=timeout)


class FallbackRunner:
    """Native compatibility fallback, NOT an OS security boundary.

    HOME/TMP redirection and environment filtering are best effort only. Commands
    can still access host files, network and processes, regardless of writable.
    """

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
