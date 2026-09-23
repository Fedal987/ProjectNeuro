from __future__ import annotations

from pathlib import Path

from src.main.ui.i18n import tr
from .base import BaseTool, ToolError


class ApprovalPolicy(BaseTool):
    @staticmethod
    def _is_low_risk_command(arguments: list[str]) -> bool:
        command = Path(arguments[0]).name
        if any(
            argument.startswith(("/", "~")) or ".." in Path(argument).parts
            for argument in arguments[1:]
        ):
            return False

        if command in {
            "basename",
            "cat",
            "cut",
            "dirname",
            "du",
            "file",
            "grep",
            "head",
            "id",
            "ls",
            "pwd",
            "stat",
            "tail",
            "uname",
            "uniq",
            "wc",
            "whoami",
        }:
            return True

        if command == "rg":
            return not any(
                argument == "--pre" or argument.startswith("--pre=")
                for argument in arguments[1:]
            )

        if command == "sed":
            return not any(
                argument == "-i"
                or argument.startswith("-i")
                or argument == "--in-place"
                or argument.startswith("--in-place=")
                for argument in arguments[1:]
            )

        if command == "sort":
            return not any(
                argument == "-o"
                or argument.startswith("-o")
                or argument.startswith("--output=")
                for argument in arguments[1:]
            )

        if command == "git" and len(arguments) >= 2:
            read_only_subcommand = arguments[1] in {
                "diff",
                "grep",
                "log",
                "rev-parse",
                "show",
                "status",
            }
            unsafe_options = {
                "--ext-diff",
                "--textconv",
                "--open-files-in-pager",
            }
            writes_output = any(
                argument == "--output" or argument.startswith("--output=")
                for argument in arguments[2:]
            )
            return (
                read_only_subcommand
                and not writes_output
                and not unsafe_options.intersection(arguments[2:])
            )
        return False

    def _require_approval(self, description: str) -> None:
        if self.context.auto_approve:
            return
        if not self.context.confirm(description):
            raise ToolError(tr("operation_denied", description=description))

    def _terminal_confirm(self, description: str) -> bool:
        if self.context._interaction_paused is not None:
            self.context._interaction_paused()
        try:
            answer = input(
                "\n" + tr("agent_permission_prompt", description=description)
            ).strip().lower()
        finally:
            if self.context._interaction_resumed is not None:
                self.context._interaction_resumed()
        if answer == "fc":
            self.context.auto_approve = True
            return True
        return answer in {"y", "yes", "是", "はい", "oui", "ja", "sim", "да"}
