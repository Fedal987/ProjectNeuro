"""Command runners: OS isolation where available, explicit native fallback."""
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
        # Fail closed if an installed bwrap cannot establish isolation. Never
        # retry a failed sandboxed command natively.
        return BubblewrapRunner(executable)
    logging.getLogger(__name__).warning(
        "Bubblewrap unavailable: commands use a native fallback with filtered "
        "environment and temporary HOME/TMP; filesystem, write permissions and "
        "network access are not isolated."
    )
    return FallbackRunner()


__all__ = ["CommandResult", "SandboxRunner", "NativeRunner", "sandbox_runner"]
