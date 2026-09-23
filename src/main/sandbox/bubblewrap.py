from __future__ import annotations

from pathlib import Path

from .base import CommandResult, execute
from .native import safe_environment


class BubblewrapRunner:
    def __init__(self, executable: str):
        self.executable = executable

    def run(
        self, args: list[str], *, workspace: Path, timeout: int, writable: bool,
    ) -> CommandResult:
        command = [
            self.executable, "--die-with-parent", "--new-session",
            "--unshare-all", "--cap-drop", "ALL",
        ]
        for location in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
            if Path(location).exists():
                command.extend(["--ro-bind", location, location])
        command.extend([
            "--bind" if writable else "--ro-bind", str(workspace.resolve()), "/workspace",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--dir", "/home", "--tmpfs", "/home/sandbox",
            "--chdir", "/workspace", "--", *args,
        ])
        env = safe_environment()
        # Host PATH may reference private directories or workspace executables.
        env.update(PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                   HOME="/home/sandbox", TMPDIR="/tmp", TMP="/tmp", TEMP="/tmp")
        return execute(command, workspace=workspace, timeout=timeout, env=env)
