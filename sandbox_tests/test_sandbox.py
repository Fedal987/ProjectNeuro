import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.main.sandbox import sandbox_runner
from src.main.sandbox.bubblewrap import BubblewrapRunner
from src.main.sandbox.native import FallbackRunner, NativeRunner
from src.main.tools.approval import ApprovalPolicy
from src.main.tools.base import ToolError
from src.main.tools.command import CommandTool


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.context = SimpleNamespace(workspace=self.workspace, command_timeout=5,
                                       auto_approve=False, confirm=Mock(return_value=True))
        self.context.approval = ApprovalPolicy(self.context)
        self.tool = CommandTool(self.context)

    def test_approval_controls_write_mount_and_denial(self):
        runner = Mock()
        runner.run.return_value = subprocess.CompletedProcess([], 0, b'ok', b'')
        with patch('src.main.tools.command.sandbox_runner', return_value=runner):
            self.assertIn('ok', self.tool._run_command('cat example.txt'))
            self.context.confirm.assert_not_called()
            self.assertFalse(runner.run.call_args.kwargs['writable'])
            self.tool._run_command('python script.py')
            self.context.confirm.assert_called_once()
            self.assertTrue(runner.run.call_args.kwargs['writable'])
            self.context.confirm.return_value = False
            runner.reset_mock()
            with self.assertRaises(ToolError):
                self.tool._run_command('python script.py')
            runner.run.assert_not_called()

    def test_full_control_native_including_former_blacklist(self):
        self.context.auto_approve = True
        with patch('src.main.sandbox.base.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 0, b'ok', b'')) as run, \
                patch('src.main.tools.command.sandbox_runner') as sandbox:
            self.tool._run_command('rm harmless-placeholder')
        self.context.confirm.assert_not_called()
        sandbox.assert_not_called()
        self.assertIsNone(run.call_args.kwargs['env'])
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertEqual(run.call_args.args[0], ['rm', 'harmless-placeholder'])

    def test_full_control_enabled_during_confirmation(self):
        def confirm(_):
            self.context.auto_approve = True
            return True
        self.context.confirm = confirm
        with patch('src.main.tools.command.NativeRunner') as native, \
                patch('src.main.tools.command.sandbox_runner') as sandbox:
            native.return_value.run.return_value = subprocess.CompletedProcess([], 0, b'', b'')
            self.tool._run_command('python script.py')
            native.return_value.run.assert_called_once()
            sandbox.assert_not_called()

    def test_fallback_environment_and_private_temp(self):
        original = dict(os.environ)
        with patch.dict(os.environ, {'NEURO_TEST_SECRET': 'secret', 'SSH_AUTH_SOCK': '/host/socket'}):
            result = FallbackRunner().run([sys.executable, '-c',
                'import os,tempfile; from pathlib import Path; '
                'assert "NEURO_TEST_SECRET" not in os.environ; '
                'assert "SSH_AUTH_SOCK" not in os.environ; '
                'assert Path.home().is_dir(); '
                'assert tempfile.gettempdir() == os.environ["TMPDIR"]; '
                'print(os.environ["HOME"])'], workspace=self.workspace, timeout=5, writable=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(Path(result.stdout.decode().strip()).exists())
        self.assertEqual(dict(os.environ), original)

    def test_fallback_selection_and_windows(self):
        for platform in ('linux', 'win32'):
            with patch('src.main.sandbox.sys.platform', platform), \
                    patch('src.main.sandbox.shutil.which', return_value=None), \
                    self.assertLogs('src.main.sandbox', level='WARNING') as logs:
                self.assertIsInstance(sandbox_runner(), FallbackRunner)
                self.assertIn('not isolated', logs.output[0])

    def test_timeout_and_decode(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            FallbackRunner().run([sys.executable, '-c', 'import time; time.sleep(5)'],
                                 workspace=self.workspace, timeout=0.1, writable=False)
        with patch('src.main.tools.command.sandbox_runner') as factory:
            factory.return_value.run.side_effect = subprocess.TimeoutExpired('tool', 5)
            with self.assertRaisesRegex(ToolError, '5'):
                self.tool._run_command('cat file')
            factory.return_value.run.side_effect = None
            factory.return_value.run.return_value = subprocess.CompletedProcess(
                [], 0, '中文\r\n'.encode('utf-16'), '警告'.encode('gbk'))
            self.assertIn('中文\n\n警告', self.tool._run_command('cat file'))

    def test_bubblewrap_mounts_namespaces_and_environment(self):
        for writable in (False, True):
            with patch('src.main.sandbox.base.subprocess.run') as run, \
                    patch.dict(os.environ, {'API_KEY': 'secret'}):
                BubblewrapRunner('/usr/bin/bwrap').run(['cat', 'file'],
                    workspace=self.workspace, timeout=3, writable=writable)
            args = run.call_args.args[0]
            i = args.index(str(self.workspace))
            self.assertEqual(args[i-1], '--bind' if writable else '--ro-bind')
            self.assertIn('--unshare-all', args)
            self.assertIn('--die-with-parent', args)
            self.assertIn('--new-session', args)
            self.assertNotIn('API_KEY', run.call_args.kwargs['env'])
            self.assertEqual(args[-3:], ['--', 'cat', 'file'])

    def test_native_retains_host_access_and_environment(self):
        outside = self.workspace.parent / (self.workspace.name + '-outside')
        outside.write_text('outside')
        self.addCleanup(outside.unlink)
        with patch.dict(os.environ, {'NEURO_TEST_SECRET': 'native'}):
            result = NativeRunner().run([sys.executable, '-c',
                'import os,sys; from pathlib import Path; '
                'print(Path(sys.argv[1]).read_text(), os.environ["NEURO_TEST_SECRET"])',
                str(outside)], workspace=self.workspace, timeout=5, writable=False)
        self.assertEqual(result.stdout.strip(), b'outside native')

    def test_missing_system_directories_and_no_native_retry(self):
        with patch('src.main.sandbox.bubblewrap.Path.exists', return_value=False), \
                patch('src.main.sandbox.base.subprocess.run') as run:
            BubblewrapRunner('/bwrap').run(['tool'], workspace=self.workspace,
                                         timeout=5, writable=False)
        self.assertNotIn('/lib64', run.call_args.args[0])
        with patch('src.main.sandbox.sys.platform', 'linux'), \
                patch('src.main.sandbox.shutil.which', return_value='/bwrap'), \
                patch('src.main.sandbox.base.subprocess.run', return_value=
                      subprocess.CompletedProcess([], 1, b'', b'isolation failed')) as run:
            with self.assertRaisesRegex(ToolError, 'isolation failed'):
                self.tool._run_command('cat input')
            run.assert_called_once()


class BubblewrapIntegrationTests(unittest.TestCase):
    def test_real_isolation(self):
        executable = shutil.which('bwrap') if sys.platform == 'linux' else None
        if not executable:
            self.skipTest('Bubblewrap not installed')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'workspace'
            workspace.mkdir()
            secret = root / 'outside-secret'
            secret.write_text('host-only')
            (workspace / 'link').symlink_to(secret)
            (workspace / 'input').write_text('hello')
            runner = BubblewrapRunner(executable)
            probe = runner.run(['/bin/true'], workspace=workspace, timeout=5, writable=False)
            if probe.returncode:
                self.skipTest('Host disallows Bubblewrap namespaces: ' + probe.stderr.decode())
            result = runner.run(['/bin/cat', 'input'], workspace=workspace, timeout=5, writable=False)
            self.assertEqual(result.stdout, b'hello')
            for target in (str(secret), 'link'):
                result = runner.run(['/bin/cat', target], workspace=workspace, timeout=5, writable=True)
                self.assertNotEqual(result.returncode, 0)
            for writable in (False, True):
                result = runner.run(['/bin/sh', '-c', 'echo changed > output'],
                                    workspace=workspace, timeout=5, writable=writable)
                self.assertEqual(result.returncode == 0, writable)
            with patch.dict(os.environ, {'NEURO_TEST_SECRET': 'secret'}):
                result = runner.run(['/bin/sh', '-c',
                    'test -z "$NEURO_TEST_SECRET" && test "$HOME" = /home/sandbox '
                    '&& test ! -e "$1" && echo private > /tmp/neuro-private', 'sh', str(secret)],
                    workspace=workspace, timeout=5, writable=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = runner.run(['/bin/sh', '-c', 'test ! -e /tmp/neuro-private'],
                                workspace=workspace, timeout=5, writable=False)
            self.assertEqual(result.returncode, 0)
            result = runner.run(['/bin/readlink', '/proc/self/ns/net'],
                                workspace=workspace, timeout=5, writable=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(result.stdout.decode().strip(), os.readlink('/proc/self/ns/net'))
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run(['/bin/sleep', '5'], workspace=workspace,
                           timeout=0.1, writable=False)
