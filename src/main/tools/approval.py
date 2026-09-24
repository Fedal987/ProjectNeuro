from __future__ import annotations

from pathlib import Path, PureWindowsPath

from src.main.ui.i18n import tr
from .base import BaseTool, ToolError


class ApprovalPolicy(BaseTool):
    @staticmethod
    def _is_low_risk_command(arguments: list[str]) -> bool:
        if not arguments:
            return False
        command = arguments[0]
        # A workspace executable must not inherit a trusted utility's policy.
        if Path(command).name != command or PureWindowsPath(command).name != command:
            return False
        if any(
            argument.startswith(("/", "~", "\\"))
            or PureWindowsPath(argument).drive
            or ".." in Path(argument).parts
            or ".." in PureWindowsPath(argument).parts
            for argument in arguments[1:]
        ):
            return False

        if command in {
            "basename",
            "cat",
            "cut",
            "dirname",
            "du",
            "grep",
            "head",
            "id",
            "ls",
            "pwd",
            "stat",
            "tail",
            "uname",
            "wc",
            "whoami",
        }:
            return True

        if command == "rg":
            return not any(
                argument == "--pre" or argument.startswith("--pre=")
                or argument.startswith("--hostname-bin")
                for argument in arguments[1:]
            )

        # sed scripts can execute commands (e) and write files (w), including
        # scripts loaded with -f. uniq accepts a positional output file, and
        # file can invoke external decompressors. These require explicit approval.
        if command in {"sed", "uniq", "file"}:
            return False

        if command == "sort":
            return not any(
                argument == "-o"
                or argument.startswith("-o")
                or argument == "--output"
                or argument.startswith("--output=")
                or (argument.startswith("--") and argument.split("=", 1)[0] not in {
                    "--", "--reverse", "--numeric-sort", "--human-numeric-sort",
                    "--general-numeric-sort", "--unique", "--stable", "--check",
                    "--ignore-case", "--ignore-leading-blanks", "--version", "--help",
                })
                or (argument.startswith("-") and not argument.startswith("--")
                    and "o" in argument[1:])
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
                and not any(
                    argument.split("=", 1)[0] in unsafe_options
                    or argument.startswith("--open-files-in-pager")
                    or (arguments[1] == "grep" and argument.startswith("-O"))
                    for argument in arguments[2:]
                )
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
