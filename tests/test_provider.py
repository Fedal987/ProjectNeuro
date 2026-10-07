import json
import unittest
from io import BytesIO
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from src.main.agent.agent import Agent
from src.main.api.api_manager import create_runtime, get_completion, list_models
from src.main.api.exceptions import (
    ProviderConnectionError,
    ProviderResponseError,
)
from src.main.api.openai_compatible import OpenAICompatibleProvider
from src.main.api.usage import UsageTracker
from src.main.config import APIConfig, AppConfig
from src.main.msg.session_manager import SessionManager


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.runtime = create_runtime(AppConfig(APIConfig("https://example.invalid/v1", "test", "model")))
        self.provider = self.runtime.provider
        self.addCleanup(self.runtime.close)
        self.agent = Agent(self.provider, Path.cwd(), "test", model="model")

    def response(self, data=None, chunks=None, status=200):
        response = requests.Response()
        response.status_code = status
        if chunks is None:
            body = json.dumps(data)
        else:
            body = ''.join('data: ' + (chunk if isinstance(chunk, str) else json.dumps(chunk)) + '\n\n' for chunk in chunks)
        response.raw = BytesIO(body.encode('utf-8'))
        response.close = Mock(wraps=response.close)
        return response

    def test_optional_temperature_in_http_requests(self):
        for stream in (False, True):
            for temperature in (None, 0, 0.7):
                with self.subTest(stream=stream, temperature=temperature):
                    response = (self.response(chunks=['[DONE]']) if stream else
                                self.response(dict(choices=[dict(message=dict(content="ok"))])))
                    with patch.object(self.provider.session, 'request', return_value=response) as request:
                        result = get_completion([{'role': 'user', 'content': 'hello'}],
                                                stream=stream, temperature=temperature, runtime=self.runtime)
                        if stream:
                            list(result)
                    payload = request.call_args.kwargs['json']
                    if temperature is None:
                        self.assertNotIn('temperature', payload)
                    else:
                        self.assertEqual(payload['temperature'], temperature)

    def test_complete_usage_and_agent_event_are_not_double_counted(self):
        usage = dict(prompt_tokens=10, completion_tokens=5, total_tokens=15, prompt_cache_hit_tokens=4)
        response = self.response(dict(choices=[dict(message=dict(content="answer", reasoning_content="reason"))], usage=usage))
        self.agent.event_sink = Mock()
        with patch.object(self.provider.session, 'request', return_value=response):
            self.assertEqual(self.agent.run('hello'), 'answer')
        self.assertEqual(self.agent.messages[-1]['reasoning_content'], 'reason')
        self.assertEqual(self.runtime.usage_tracker.snapshot().total_tokens, 15)
        self.assertEqual(self.runtime.usage_tracker.snapshot().cached_tokens, 4)
        self.agent.event_sink.assert_any_call('token_usage', usage)
        response.close.assert_called_once()

    def test_stream_reasoning_tools_usage_and_followup(self):
        first = self.response(chunks=[
            {'choices': [{'delta': {'reasoning_content': 'reason'}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call', 'function': {'name': 'read_file', 'arguments': '{"path":'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"a"}'}}]}}]},
            {'choices': [], 'usage': {'total_tokens': 3}}, '[DONE]'])
        second = self.response(chunks=[{'choices': [{'delta': {'content': 'done'}}]},
                                      {'choices': [], 'usage': {'total_tokens': 4}}, '[DONE]'])
        with patch.object(self.provider.session, 'request', side_effect=[first, second]), patch.object(self.agent, '_execute_tool_call', return_value='ok') as execute:
            events = list(self.agent.run_stream_events('hello'))
        self.assertIn(('reasoning', 'reason'), [(e.kind, e.content) for e in events])
        self.assertIn(('content', 'done'), [(e.kind, e.content) for e in events])
        self.assertEqual(execute.call_args.args[0]['function']['arguments'], '{"path":"a"}')
        self.assertEqual(self.runtime.usage_tracker.snapshot().total_tokens, 7)
        self.assertTrue(first.raw.closed and second.raw.closed)

    def test_openai_cached_usage_supports_both_formats_without_double_counting(self):
        for details, legacy, expected in [
            ({'cached_tokens': 6656}, None, 6656),
            ({'cached_tokens': 6656}, 6656, 6656),
            ({'cached_tokens': 9000}, None, 7133),
            (None, 42, 42),
            ('invalid', None, 0),
        ]:
            with self.subTest(details=details, legacy=legacy):
                tracker = UsageTracker()
                tracker.record({'prompt_tokens': 7133, 'completion_tokens': 87,
                                'prompt_tokens_details': details,
                                'prompt_cache_hit_tokens': legacy})
                self.assertEqual(tracker.snapshot().cached_tokens, expected)
                self.assertEqual(tracker.snapshot().total_tokens, 7220)

    def test_provider_options_and_endpoint(self):
        for base, field, enabled, disabled in [
            ('https://api.deepseek.com/chat/completions', 'thinking', {'type': 'enabled'}, {'type': 'disabled'}),
            ('https://api.siliconflow.cn/v1/', 'enable_thinking', True, False),
        ]:
            for thinking, expected in [(True, enabled), (False, disabled)]:
                provider = OpenAICompatibleProvider(base_url=base, api_key='key', usage_tracker=UsageTracker())
                self.addCleanup(provider.close)
                response = self.response(chunks=['[DONE]'])
                with patch.object(provider.session, 'request', return_value=response) as request:
                    list(provider.stream([], model='chosen', tools=[{'type': 'function'}], thinking=thinking, reasoning_effort='high'))
                payload = request.call_args.kwargs['json']
                self.assertEqual(payload[field], expected)
                self.assertEqual(payload['reasoning_effort'], 'high')
                self.assertEqual(payload['tool_choice'], 'auto')
                self.assertEqual(payload['stream_options'], {'include_usage': True})
                self.assertEqual(request.call_args.args[1].count('/chat/completions'), 1)
                self.assertEqual(provider.session.headers['authorization'], 'Bearer key')

    def test_stream_diagnostics_distinguish_done_from_eof_and_keep_finish_reason(self):
        for done in (True, False):
            with self.subTest(done=done):
                chunks = [
                    {'id': 'response-1', 'model': 'upstream-model',
                     'choices': [{'index': 0, 'delta': {'content': 'short answer'}}]},
                    {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'length'}]},
                ]
                if done:
                    chunks.append('[DONE]')
                response = self.response(chunks=chunks)
                response.headers['x-request-id'] = 'request-1'
                response.headers['authorization'] = 'must-not-be-logged'
                self.agent.event_sink = Mock()
                with patch.object(self.provider.session, 'request', return_value=response):
                    events = list(self.agent.run_stream_events('hello'))
                self.assertEqual(''.join(e.content for e in events if e.kind == 'content'), 'short answer')
                captured = [call.args[1] for call in self.agent.event_sink.call_args_list
                            if call.args[0] == 'response_metadata']
                self.assertEqual(len(captured), 1)
                metadata = captured[0]
                self.assertEqual(metadata['request_id'], 'request-1')
                self.assertEqual(metadata['model'], 'upstream-model')
                self.assertEqual(metadata['finish_reasons'], [{'index': 0, 'reason': 'length'}])
                self.assertEqual(metadata['transport_end'], 'done' if done else 'eof')
                self.assertNotIn('must-not-be-logged', json.dumps(metadata))

    def test_nonstream_response_metadata_keeps_finish_reason(self):
        response = self.response({'id': 'response-1', 'model': 'model',
                                  'choices': [{'message': {'content': 'answer'}, 'finish_reason': 'stop'}]})
        self.agent.event_sink = Mock()
        with patch.object(self.provider.session, 'request', return_value=response):
            self.assertEqual(self.agent.run('hello'), 'answer')
        captured = [call.args[1] for call in self.agent.event_sink.call_args_list
                    if call.args[0] == 'response_metadata']
        self.assertEqual(captured[0]['transport_end'], 'complete')
        self.assertEqual(captured[0]['finish_reasons'], [{'index': 0, 'reason': 'stop'}])

    def test_list_models(self):
        response = self.response({'data': [{'id': 'z'}, {'id': 'a'}, {'id': 'z'}, {'id': ''}, {'id': None}]})
        with patch.object(self.provider.session, 'request', return_value=response) as request:
            self.assertEqual(list_models(runtime=self.runtime), ['a', 'z'])
        self.assertEqual(request.call_args.args, ('GET', 'https://example.invalid/v1/models'))
        self.assertEqual(request.call_args.kwargs['timeout'], 15)
        response.close.assert_called_once()

    def test_http_network_and_format_errors(self):
        cases = [self.response({'error': 'denied'}, status=401), self.response([]),
                 self.response({'choices': []}), self.response({'choices': [{'message': 'bad'}]}),
                 self.response(chunks=['bad json']), self.response(chunks=[{'error': 'failed'}]),
                 self.response(chunks=[{'choices': 'bad'}])]
        for index, response in enumerate(cases):
            with self.subTest(index=index), patch.object(self.provider.session, 'request', return_value=response):
                with self.assertRaises(ProviderResponseError):
                    if index >= 4:
                        list(self.provider.stream([], model='model'))
                    else:
                        self.provider.complete([], model='model')
                response.close.assert_called_once()
        with patch.object(self.provider.session, 'request', side_effect=requests.ConnectionError('offline')):
            with self.assertRaises(ProviderConnectionError):
                get_completion([], runtime=self.runtime)
            self.assertTrue(self.agent.run('hello'))
            self.assertEqual(list(self.agent.run_stream_events('hello'))[-1].kind, 'error')

    def test_invalid_completion_json_is_response_error(self):
        response = self.response(None)
        response.raw = BytesIO(b'not json')
        with patch.object(self.provider.session, 'request', return_value=response):
            with self.assertRaises(ProviderResponseError):
                self.provider.complete([], model='model')
        response.close.assert_called_once()

    def test_stream_error_and_early_close_release_response(self):
        for fail in [True, False]:
            response = Mock()
            def lines(**kwargs):
                yield 'data: {"choices":[{"delta":{"content":"hello"}}]}'
                raise requests.ConnectionError('disconnected')
            response.iter_lines.side_effect = lines
            with patch.object(self.provider.session, 'request', return_value=response):
                self.agent.event_sink = Mock()
                result = self.provider.stream([], model='model', request_context=self.agent._request_context)
                self.assertEqual(next(result)['choices'][0]['delta']['content'], 'hello')
                if fail:
                    with self.assertRaises(ProviderConnectionError):
                        next(result)
                else:
                    result.close()
            response.close.assert_called_once()
            captured = [call.args[1] for call in self.agent.event_sink.call_args_list
                        if call.args[0] == 'response_metadata']
            self.assertEqual(captured[0]['transport_end'], 'error' if fail else 'consumer_closed')

    def test_interrupt_blocked_stream_and_reuse_provider(self):
        ready, released = Event(), Event()
        response = Mock()
        response.close.side_effect = released.set
        def lines(**kwargs):
            ready.set()
            if not released.wait(2):
                raise AssertionError('interrupt did not close response')
            raise requests.ConnectionError('closed')
            yield
        response.iter_lines.side_effect = lines
        events = []
        with patch.object(self.provider.session, 'request', return_value=response):
            worker = Thread(target=lambda: events.extend(self.agent.run_stream_events('hello')))
            worker.start()
            self.assertTrue(ready.wait(2))
            self.agent.interrupt()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(events[-1].kind, 'interrupted')
        response = self.response({'choices': [{'message': {'content': 'again'}}]})
        with patch.object(self.provider.session, 'request', return_value=response):
            self.assertEqual(self.agent.run('continue'), 'again')

    def test_naming_uses_provider_usage_and_failure_is_nonfatal(self):
        manager = SimpleNamespace(current_handler=SimpleNamespace(runtime=self.runtime))
        response = self.response({'choices': [{'message': {'content': '标题'}}], 'usage': {'total_tokens': 8}})
        with patch.object(self.provider.session, 'request', return_value=response):
            self.assertEqual(SessionManager._generate_name_with_llm(manager, [{'role': 'user', 'content': 'hello'}]), '标题')
        self.assertEqual(self.runtime.usage_tracker.snapshot().total_tokens, 8)
        with patch.object(self.provider.session, 'request', side_effect=requests.ConnectionError('offline')):
            self.assertIsNone(SessionManager._generate_name_with_llm(manager, []))
