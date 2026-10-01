import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.main.tools.approval import ApprovalPolicy
from src.main.tools.base import ToolError
from src.main.tools.command import CommandTool


class CommandPolicyTests(unittest.TestCase):
    def test_read_only_commands(self):
        for args in (["ls", "-la"], ["cat", "example.txt"],
                     ["rg", "needle", "."], ["sort", "-rn", "input"],
                     ["git", "diff", "--stat"], ["git", "status"]):
            with self.subTest(args=args):
                self.assertTrue(ApprovalPolicy._is_low_risk_command(args))

    def test_commands_requiring_approval(self):
        cases = [
            [], ["./ls"], ["/tmp/cat", "input"], [r".\ls"],
            ["cat", r"C:\private.txt"], ["cat", r"..\private.txt"],
            ["sed", "e touch output", "input"],
            ["sed", "-n", "w output", "input"], ["sed", "-f", "script"],
            ["uniq", "input", "output"], ["file", "-z", "input"],
            ["sort", "--output", "output", "input"],
            ["sort", "--out=output", "input"],
            ["sort", "-rooutput", "input"],
            ["sort", "--compress-program=helper", "input"],
            ["rg", "--pre=helper", "needle"],
            ["rg", "--hostname-bin=helper", "needle"],
            ["git", "grep", "-Ohelper", "needle"],
            ["git", "grep", "--open-files-in-pager=helper", "needle"],
            ["git", "diff", "--output=output"],
            ["git", "show", "--ext-diff"],
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertFalse(ApprovalPolicy._is_low_risk_command(args))

    def context(self, auto_approve=False):
        context = SimpleNamespace(auto_approve=auto_approve, workspace=Path.cwd(),
                                  command_timeout=10, confirm=Mock(return_value=False))
        context.approval = ApprovalPolicy(context)
        return context

    def test_dangerous_paths_blocked_before_runner(self):
        with patch('src.main.tools.command.sandbox_runner') as runner:
            for command in ('rm file', '/bin/rm file', './sudo id',
                            '/sbin/mkfs.ext4 disk', 'SHUTDOWN.EXE'):
                with self.subTest(command=command):
                    context = self.context()
                    with self.assertRaises(ToolError):
                        CommandTool(context)._run_command(command)
                    context.confirm.assert_not_called()
            runner.assert_not_called()

    def test_denial_prevents_execution(self):
        context = self.context()
        with patch('src.main.tools.command.sandbox_runner') as runner:
            with self.assertRaises(ToolError):
                CommandTool(context)._run_command('sort --output output input')
            context.confirm.assert_called_once()
            runner.assert_not_called()

    def test_full_control_uses_native_runner(self):
        context = self.context(auto_approve=True)
        with patch('src.main.tools.command.NativeRunner') as runner:
            runner.return_value.run.return_value = subprocess.CompletedProcess(
                [], 0, stdout=b'', stderr=b'')
            CommandTool(context)._run_command('/bin/rm file')
            runner.return_value.run.assert_called_once()
            context.confirm.assert_not_called()
