"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.main.agent.agent import Agent
from src.main.api.openai_compatible import OpenAICompatibleProvider
from src.main.api.usage import UsageTracker
from src.main.tool.toolcall_utils import Agent as LegacyAgent


class AgentToolsTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.workspace = Path(temp_dir.name)
        self.agent = Agent(provider=OpenAICompatibleProvider(api_key='test', base_url='https://example.invalid/v1', usage_tracker=UsageTracker()), workspace=self.workspace,
                           system_prompt='Test', model='test',
                           confirm=Mock(return_value=True))
        self.addCleanup(self.agent.provider.session.close)

    def call(self, name, **arguments):
        return self.agent._execute_tool_call(self.tool_call(name, **arguments))

    @staticmethod
    def tool_call(name, **arguments):
        return {'id': 'call-1', 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments)}}

    def test_legacy_import_and_registry(self):
        self.assertIs(LegacyAgent, Agent)
        self.assertEqual({t['function']['name'] for t in self.agent.tools},
                         set(self.agent.tool_handlers))
        self.assertEqual(len(self.agent.tools), 6)

    def test_read_before_write_approval_and_repeat_failure(self):
        target = self.workspace / 'example.txt'
        target.write_text('before', encoding='utf-8')
        self.assertIn('执行失败', self.call('write_file', path='example.txt', content='after'))
        self.assertIn('已阻止', self.call('write_file', path='example.txt', content='after'))
        self.agent.confirm.assert_not_called()
        self.assertIn('1: before', self.call('read_file', path='example.txt'))
        self.call('replace_in_file', path='example.txt', old_content='before', new_content='after')
        self.assertEqual(target.read_text(), 'after')
        self.agent.confirm.assert_called_once()
        self.agent.reset()
        self.assertIn('执行失败', self.call('write_file', path='example.txt', content='reset'))

    def test_denied_write_and_workspace_escape(self):
        self.agent.confirm.return_value = False
        self.assertIn('执行失败', self.call('write_file', path='new.txt', content='data'))
        self.assertFalse((self.workspace / 'new.txt').exists())
        self.assertIn('路径超出工作目录', self.call('read_file', path='../outside.txt'))

    def test_tools_follow_resumed_workspace_and_search_fallback(self):
        resumed = self.workspace / 'resumed'
        resumed.mkdir()
        (resumed / 'example.txt').write_text('needle', encoding='utf-8')
        self.agent.workspace = resumed
        self.assertIn('1: needle', self.call('read_file', path='example.txt'))
        with patch('src.main.tools.search.subprocess.run', side_effect=FileNotFoundError):
            self.assertEqual(self.call('search_files', query='needle'), 'example.txt:1:needle')
        self.assertIn('example.txt', self.call('list_directory', path='.'))

    def test_command_approval_and_dispatch_errors(self):
        with patch('src.main.tools.command.subprocess.run', return_value=
                   subprocess.CompletedProcess(['ls'], 0, stdout='example.txt', stderr='')) as run:
            self.assertIn('example.txt', self.call('run_command', command='ls'))
            self.agent.confirm.assert_not_called()
            self.call('run_command', command='python --version')
            self.agent.confirm.assert_called_once()
            self.assertEqual(run.call_args.kwargs['cwd'], self.workspace)
        self.assertIn('未知工具', self.call('missing'))
        self.assertIn('执行失败', self.agent._execute_tool_call({
            'function': {'name': 'read_file', 'arguments': '[]'}}))

    def test_sync_loop_dispatches_and_records_tool_result(self):
        (self.workspace / 'example.txt').write_text('data')
        with patch.object(self.agent, '_request_completion', side_effect=[
            {'tool_calls': [self.tool_call('read_file', path='example.txt')]},
            {'content': 'done'},
        ]):
            self.assertEqual(self.agent.run('read it'), 'done')
        self.assertEqual(self.agent.messages[-2]['role'], 'tool')
        self.assertEqual(self.agent.messages[-2]['content'], '1: data')

    def test_stream_loop_dispatches_fragmented_tool_call(self):
        (self.workspace / 'example.txt').write_text('data')
        chunks = [
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call-1',
                'function': {'name': 'read_file', 'arguments': '{"path":'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0,
                'function': {'arguments': '"example.txt"}'}}]}}]},
        ]
        with patch.object(self.agent, '_request_completion_stream', side_effect=[
            iter(chunks), iter([{'choices': [{'delta': {'content': 'done'}}]}]),
        ]):
            events = list(self.agent.run_stream_events('read it'))
        self.assertEqual([e.kind for e in events], ['tool', 'tool_result', 'content'])
        self.assertEqual(events[-1].content, 'done')
        self.assertEqual(self.agent.messages[-2]['content'], '1: data')
