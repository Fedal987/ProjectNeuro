"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

from __future__ import annotations

import logging
import shutil
import sys

from .base import CommandResult, SandboxRunner
from .bubblewrap import BubblewrapRunner
from .native import FallbackRunner, NativeRunner


def sandbox_runner() -> SandboxRunner:
    executable = shutil.which("bwrap") if sys.platform == "linux" else None
    if executable:
        return BubblewrapRunner(executable)
    logging.getLogger(__name__).warning(
        "Bubblewrap unavailable: commands use a native fallback with filtered "
        "environment and temporary HOME/TMP; filesystem, write permissions and "
        "network access are not isolated."
    )
    return FallbackRunner()


__all__ = ["CommandResult", "NativeRunner", "SandboxRunner", "sandbox_runner"]
