from __future__ import annotations

import shlex
import subprocess
from pathlib import PureWindowsPath
from src.main.ui.i18n import tr
from src.main.encoding import decode_output
from src.main.sandbox import NativeRunner, sandbox_runner

from .base import BaseTool, ToolError


class CommandTool(BaseTool):
    def _run_command(self, command: str) -> str:
        try:
            arguments = shlex.split(command)
        except ValueError as exc:
            raise ToolError(f"命令解析失败: {exc}") from exc
        if not arguments:
            raise ToolError("命令不能为空")
        command_name = PureWindowsPath(arguments[0]).name.lower()
        if command_name.endswith(".exe"):
            command_name = command_name[:-4]
        if not self.context.auto_approve and (
            command_name in {"rm", "sudo", "su", "shutdown", "reboot", "mkfs", "dd"}
            or command_name.startswith("mkfs.")
        ):
            raise ToolError(f"出于安全原因不允许执行命令: {arguments[0]}")
        writable = not self.context.approval._is_low_risk_command(arguments)
        if not self.context.auto_approve and writable:
            self.context.approval._require_approval(tr("approval_run_command", command=command))
        try:
            runner = NativeRunner() if self.context.auto_approve else sandbox_runner()
            completed = runner.run(
                arguments,
                workspace=self.context.workspace,
                timeout=self.context.command_timeout,
                writable=writable,
            )
        except FileNotFoundError as exc:
            raise ToolError(f"找不到命令: {arguments[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"命令执行超过 {self.context.command_timeout} 秒") from exc
        output = decode_output(completed.stdout)
        if completed.stderr:
            output += ("\n" if output else "") + decode_output(completed.stderr)
        result = f"退出码: {completed.returncode}\n{output.strip()}"
        if completed.returncode != 0:
            raise ToolError(self._truncate(result))
        return self._truncate(result)
