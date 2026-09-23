from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Mapping, Protocol

CommandResult = subprocess.CompletedProcess[bytes]


class SandboxRunner(Protocol):
    def run(
        self, args: list[str], *, workspace: Path, timeout: int, writable: bool,
    ) -> CommandResult: ...


def execute(
    args: list[str], *, workspace: Path, timeout: int,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    return subprocess.run(
        args, cwd=workspace, capture_output=True, timeout=timeout,
        check=False, shell=False, env=env,
    )
