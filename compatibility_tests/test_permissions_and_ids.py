from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import requests

from src.main.api.exceptions import ProviderResponseError
from src.main.api.openai_compatible import OpenAICompatibleProvider
from src.main.api.tool_id_compat import retry_messages
from src.main.api.usage import UsageTracker
from src.main.tools.approval import ApprovalPolicy
from src.main.tools.base import ToolError
from src.main.tools.filesystem import FilesystemTools
from src.main.tools.search import SearchTools

ERROR = "[invalid_id_prefix] Invalid 'input[5].id': 'call_one'. Expected an ID that begins with 'fc_'."


class PermissionsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.outside = self.root / 'outside'
        self.outside.mkdir()
        (self.outside / 'readme').write_text('hello')
        self.context = SimpleNamespace(workspace=self.workspace, auto_approve=False,
            confirm=Mock(return_value=True), _read_paths=set(), command_timeout=5)
        self.context.approval = ApprovalPolicy(self.context)
        self.tool = FilesystemTools(self.context)

    def test_external_list_read_write_replace_search(self):
        self.assertIn('readme', self.tool._list_directory('../outside'))
        self.assertIn(str(self.outside), self.context.confirm.call_args.args[0])
        self.assertIn('hello', self.tool._read_file('../outside/readme'))
        self.tool._replace_in_file('../outside/readme', 'hello', 'world')
        self.tool._write_file('../outside/new', 'new')
        self.assertEqual((self.outside / 'readme').read_text(), 'world')
        self.assertEqual((self.outside / 'new').read_text(), 'new')
        with patch('src.main.tools.search.sys.platform', 'win32'):
            self.assertIn(str(self.outside / 'readme'),
                          SearchTools(self.context)._search_files('world', '../outside'))

    def test_denied_and_no_persistent_grant(self):
        self.tool._list_directory('../outside')
        self.context.confirm.return_value = False
        with self.assertRaises(ToolError):
            self.tool._read_file('../outside/readme')
        with self.assertRaises(ToolError):
            self.tool._write_file('../outside/new', 'blocked')
        self.assertFalse((self.outside / 'new').exists())

    def test_internal_paths_and_full_control_skip_prompts(self):
        self.tool._list_directory('.')
        self.context.confirm.assert_not_called()
        self.context.auto_approve = True
        self.tool._list_directory('../outside')
        self.tool._read_file('../outside/readme')
        self.tool._write_file('../outside/new', 'allowed')
        self.context.confirm.assert_not_called()

    def test_symlink_resolved_before_confirmation(self):
        link = self.workspace / 'link'
        try:
            link.symlink_to(self.outside, target_is_directory=True)
        except OSError:
            self.skipTest('symlinks unavailable')
        self.context.confirm.return_value = False
        with self.assertRaises(ToolError):
            self.tool._read_file('link/readme')
        self.assertIn(str(self.outside / 'readme'), self.context.confirm.call_args.args[0])


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {'role': 'assistant', 'tool_calls': [
                {'id': 'call_one', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}},
                {'id': 'fc_existing', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'call_one', 'content': 'result'},
            {'role': 'tool', 'tool_call_id': 'fc_existing', 'content': 'result'},
        ]
        self.provider = OpenAICompatibleProvider(base_url='https://example.invalid/v1',
                                                 api_key='test', usage_tracker=UsageTracker())
        self.addCleanup(self.provider.close)

    def test_mapping_pairs_and_history_unchanged(self):
        before = deepcopy(self.messages)
        mapped = retry_messages(self.messages, ProviderResponseError(ERROR))
        new_id = mapped[0]['tool_calls'][0]['id']
        self.assertTrue(new_id.startswith('fc_'))
        self.assertEqual(new_id, mapped[1]['tool_call_id'])
        self.assertEqual(mapped[0]['tool_calls'][1]['id'], 'fc_existing')
        self.assertEqual(self.messages, before)
        self.assertIsNone(retry_messages(self.messages, Exception('unrelated error')))

    def test_complete_retry_only_once(self):
        with patch.object(self.provider, '_complete_once', side_effect=[
                ProviderResponseError(ERROR), {'content': 'ok'}]) as once:
            self.assertEqual(self.provider.complete(self.messages, model='test'), {'content': 'ok'})
            self.assertEqual(once.call_count, 2)
            self.assertTrue(once.call_args.args[0][1]['tool_call_id'].startswith('fc_'))
        with patch.object(self.provider, '_complete_once', side_effect=ProviderResponseError(ERROR)) as once:
            with self.assertRaises(ProviderResponseError):
                self.provider.complete(self.messages, model='test')
            self.assertEqual(once.call_count, 2)

    def test_stream_error_before_and_after_content(self):
        def failed():
            raise ProviderResponseError(ERROR)
            yield
        chunk = {'choices': [{'delta': {'content': 'ok'}}]}
        with patch.object(self.provider, '_stream_once', side_effect=[failed(), iter([chunk])]) as once:
            self.assertEqual(list(self.provider.stream(self.messages, model='test')), [chunk])
            self.assertEqual(once.call_count, 2)
        def partial():
            yield chunk
            raise ProviderResponseError(ERROR)
        with patch.object(self.provider, '_stream_once', return_value=partial()) as once:
            stream = self.provider.stream(self.messages, model='test')
            self.assertEqual(next(stream), chunk)
            with self.assertRaises(ProviderResponseError):
                next(stream)
            once.assert_called_once()

    def test_http_and_sse_gateway_errors(self):
        before = deepcopy(self.messages)
        for prefix in ('fc', 'fc_'):
            for streaming in (False, True):
                with self.subTest(prefix=prefix, streaming=streaming):
                    error = {
                        'code': 'upstream_error',
                        'message': "All upstream attempts failed for model: gpt-6-astra. "
                            "Last error: upstream status 400 Bad Request: [ApiIdParam] "
                            "[input[3].id] [invalid_id_prefix] Invalid 'input[3].id': "
                            f"'call_one'. Expected an ID that begins with '{prefix}'.",
                        'upstream_status': 502,
                    }
                    bad = Mock(headers={}, status_code=200)
                    bad.json.return_value = {'error': error}
                    bad.iter_lines.return_value = ['data: ' + json.dumps({'error': error})]
                    ok = Mock(headers={}, status_code=200)
                    ok.json.return_value = {'choices': [{'message': {'content': 'ok'}}]}
                    ok.iter_lines.return_value = [
                        'data: {"choices":[{"delta":{"content":"ok"}}]}', 'data: [DONE]']
                    with patch.object(self.provider.session, 'request', side_effect=[bad, ok]) as request:
                        if streaming:
                            self.assertEqual(len(list(self.provider.stream(self.messages, model='test'))), 1)
                        else:
                            self.assertEqual(self.provider.complete(self.messages, model='test'), {'content': 'ok'})
                        self.assertEqual(request.call_count, 2)
                        sent = request.call_args.kwargs['json']['messages']
                        self.assertEqual(sent[0]['tool_calls'][0]['id'], sent[1]['tool_call_id'])
                        self.assertTrue(sent[1]['tool_call_id'].startswith('fc_'))
                        self.assertEqual(self.messages, before)

    def test_prefix_detection_is_specific_to_expected_prefix(self):
        for prefix in ('fc', 'fc_'):
            for quote in ("'", '"'):
                error = ERROR.replace("'fc_'", quote + prefix + quote)
                self.assertIsNotNone(retry_messages(self.messages, Exception(error)))
        for prefix in ('msg', 'fc_other', 'call'):
            error = ERROR.replace("'fc_'", repr(prefix)) + " unrelated ID 'fc_'"
            self.assertIsNone(retry_messages(self.messages, Exception(error)))
        self.assertIsNone(retry_messages(self.messages, Exception(
            ERROR.replace('invalid_id_prefix', 'another_error'))))

    def test_http_failure_retries_and_closes_response(self):
        bad = requests.Response()
        bad.status_code = 502
        bad._content = json.dumps({'error': ERROR}).encode()
        bad.close = Mock()
        ok = Mock(headers={}, status_code=200)
        ok.json.return_value = {'choices': [{'message': {'content': 'ok'}}]}
        with patch.object(self.provider.session, 'request', side_effect=[bad, ok]) as request:
            self.assertEqual(self.provider.complete(self.messages, model='test'), {'content': 'ok'})
            self.assertEqual(request.call_count, 2)
            bad.close.assert_called_once()

    def test_normal_success_and_other_errors_not_retried(self):
        with patch.object(self.provider, '_complete_once', return_value={'content': 'ok'}) as once:
            self.provider.complete(self.messages, model='test')
            self.assertIs(once.call_args.args[0], self.messages)
            once.assert_called_once()
        with patch.object(self.provider, '_complete_once', side_effect=ProviderResponseError('bad key')) as once:
            with self.assertRaises(ProviderResponseError):
                self.provider.complete(self.messages, model='test')
            once.assert_called_once()
