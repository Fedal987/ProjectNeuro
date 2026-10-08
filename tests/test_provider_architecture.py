"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.main.api.api_manager import create_runtime, get_completion
from src.main.api.factory import PROTOCOL_FACTORIES, create_provider
from src.main.api.transform import RequestTransform, resolve_provider
from src.main.api.usage import UsageTracker
from src.main.config import APIConfig, AppConfig, load_config
from src.main.model_config import ModelCapabilities, ModelOptions
from src.main.msg.message_handler import MessageHandler


class ProviderArchitectureTests(unittest.TestCase):
    def config(self, **kwargs):
        return APIConfig('https://proxy.example/v1', 'secret', 'chat', **kwargs)

    def payload(self, transform, model='chat', **kwargs):
        options = dict(temperature=0.2, tools=[{'type': 'function'}], thinking=True,
                       reasoning_effort='high', stream=True)
        options.update(kwargs)
        return transform.apply([{'role': 'user', 'content': 'hello'}], model=model, **options)

    def test_explicit_provider_overrides_hostname(self):
        self.assertEqual(resolve_provider('deepseek', 'https://proxy.example/v1'), 'deepseek')
        self.assertEqual(resolve_provider('openai_compatible', 'https://api.deepseek.com'), 'openai_compatible')
        runtime = create_runtime(AppConfig(self.config(provider='deepseek')))
        self.addCleanup(runtime.close)
        payload = self.payload(runtime.provider.transform)
        self.assertEqual(payload['thinking'], {'type': 'enabled'})
        self.assertIs(runtime.provider.usage_tracker, runtime.usage_tracker)

    def test_auto_detection_uses_hostname_not_substrings(self):
        for url, expected in [
            ('https://api.deepseek.com/v1', 'deepseek'),
            ('https://API.DEEPSEEK.COM:443/v1', 'deepseek'),
            ('https://api.siliconflow.cn/v1', 'siliconflow'),
            ('https://api.siliconflow.com/v1', 'siliconflow'),
            ('https://api.deepseek.com.attacker.test/v1', 'openai_compatible'),
            ('https://example.test/siliconflow', 'openai_compatible'),
            ('https://api.deepseek.com@example.test/v1', 'openai_compatible'),
        ]:
            with self.subTest(url=url):
                self.assertEqual(resolve_provider('auto', url), expected)

    def test_capability_overrides_are_applied_per_model(self):
        transform = RequestTransform(provider='deepseek', models={
            'basic': ModelOptions(capabilities=ModelCapabilities(
                tools=False, reasoning=False, temperature=False, stream_usage=False)),
            'reasoner': ModelOptions(thinking_format='enable_thinking',
                                     capabilities=ModelCapabilities(reasoning_effort=False)),
        })
        basic = self.payload(transform, 'basic')
        self.assertEqual(set(basic), {'model', 'messages', 'stream'})
        reasoner = self.payload(transform, 'reasoner')
        self.assertTrue(reasoner['enable_thinking'])
        self.assertNotIn('thinking', reasoner)
        self.assertNotIn('reasoning_effort', reasoner)
        ordinary = self.payload(transform, 'unconfigured-model')
        self.assertIn('thinking', ordinary)
        self.assertIn('tools', ordinary)
        self.assertIn('reasoning_effort', ordinary)

    def test_defaults_model_precedence_and_recursive_merge(self):
        defaults = ModelOptions(thinking_format='none',
            capabilities=ModelCapabilities(tools=False, temperature=False),
            extra_body={'custom': {'a': 1, 'b': 2}, 'max_tokens': 100})
        model = ModelOptions(api_model='remote/model', thinking_format='thinking',
            capabilities=ModelCapabilities(tools=True),
            extra_body={'custom': {'b': 3}, 'max_tokens': 200})
        transform = RequestTransform(provider='siliconflow', defaults=defaults, models={'chat': model})
        payload = self.payload(transform)
        self.assertEqual(payload['model'], 'remote/model')
        self.assertEqual(payload['custom'], {'a': 1, 'b': 3})
        self.assertEqual(payload['max_tokens'], 200)
        self.assertEqual(payload['thinking'], {'type': 'enabled'})
        self.assertNotIn('enable_thinking', payload)
        self.assertNotIn('temperature', payload)
        self.assertIn('tools', payload)
        self.assertNotIn('thinking', self.payload(transform, 'other'))
        payload['custom']['a'] = 9
        self.assertEqual(self.payload(transform)['custom']['a'], 1)
        defaults.extra_body['custom']['a'] = 8
        self.assertEqual(self.payload(transform)['custom']['a'], 1)

    def test_generic_custom_provider_accepts_model_options(self):
        transform = RequestTransform(provider='my-gateway', models={
            'chat': ModelOptions(thinking_format='enable_thinking', extra_body={'max_tokens': 256}),
        })
        payload = self.payload(transform, thinking=False)
        self.assertFalse(payload['enable_thinking'])
        self.assertEqual(payload['max_tokens'], 256)
        self.assertNotIn('thinking', self.payload(RequestTransform()))

    def test_config_parses_nested_tables(self):
        text = '''[API_MANAGER]
BASE_URL = "https://proxy.example/v1"
API_KEY = "secret"
MODEL = "chat"
PROTOCOL = "openai_compatible"
PROVIDER = "deepseek"
[API_MANAGER.DEFAULTS.CAPABILITIES]
TEMPERATURE = false
[API_MANAGER.DEFAULTS.EXTRA_BODY]
max_tokens = 512
[API_MANAGER.MODELS.chat]
API_MODEL = "remote/model"
THINKING_FORMAT = "none"
[API_MANAGER.MODELS.chat.CAPABILITIES]
REASONING = false
[API_MANAGER.MODELS.chat.EXTRA_BODY]
max_tokens = 256
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text(text)
            config = load_config(path)
        runtime = create_runtime(config)
        self.addCleanup(runtime.close)
        payload = self.payload(runtime.provider.transform)
        self.assertEqual(payload['model'], 'remote/model')
        self.assertEqual(payload['max_tokens'], 256)
        self.assertNotIn('thinking', payload)
        self.assertNotIn('reasoning_effort', payload)
        self.assertNotIn('temperature', payload)
        self.assertNotIn('secret', repr(config))

    def test_invalid_config_is_rejected(self):
        for kwargs in [dict(protocol=''), dict(provider=2), dict(defaults={}),
                       dict(models={'bad name': ModelOptions()}), dict(models={'x': {}})]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.config(**kwargs)
        for mapping in [dict(THINKING_FORMAT='unknown'), dict(CAPABILITIES={'TOOLS': 'false'}),
                        dict(CAPABILITIES={'UNKNOWN': True}), dict(UNKNOWN=True),
                        dict(EXTRA_BODY={'messages': []}), dict(EXTRA_BODY={'thinking': True}),
                        dict(EXTRA_BODY={'value': float('nan')}), dict(API_MODEL='')]:
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                ModelOptions.from_mapping(mapping)

    def test_unknown_protocol_fails_before_opening_session(self):
        with patch('requests.Session') as session, self.assertRaisesRegex(ValueError, 'Unsupported'):
            create_runtime(AppConfig(self.config(protocol='unsupported')))
        session.assert_not_called()

    def test_factory_selects_by_protocol_not_provider(self):
        adapter = Mock()
        factory = Mock(return_value=adapter)
        tracker = UsageTracker()
        config = self.config(protocol='test-protocol', provider='deepseek')
        with patch.dict(PROTOCOL_FACTORIES, {'test-protocol': factory}):
            self.assertIs(create_provider(config, tracker), adapter)
        factory.assert_called_once_with(config, tracker)

    def test_agent_model_switch_reselects_capabilities_and_api_model(self):
        runtime = create_runtime(AppConfig(self.config(provider='deepseek', models={
            'basic': ModelOptions(api_model='remote/basic', capabilities=ModelCapabilities(tools=False, reasoning=False)),
        })))
        self.addCleanup(runtime.close)
        handler = MessageHandler(runtime=runtime, system_prompt='test')
        response = Mock()
        response.json.return_value = {'choices': [{'message': {'content': 'answer'}}]}
        with patch.object(runtime.provider.session, 'request', return_value=response) as request:
            self.assertEqual(handler.get_response('hello'), 'answer')
            self.assertIn('tools', request.call_args.kwargs['json'])
            handler.set_model('basic')
            self.assertEqual(handler.get_response('again'), 'answer')
            payload = request.call_args.kwargs['json']
            self.assertEqual(payload['model'], 'remote/basic')
            self.assertNotIn('tools', payload)
            self.assertNotIn('thinking', payload)
            self.assertEqual(get_completion([], runtime=runtime), 'answer')
            self.assertEqual(request.call_args.kwargs['json']['model'], 'chat')
            self.assertNotIn('tools', request.call_args.kwargs['json'])

    def test_sync_and_stream_use_same_transform(self):
        runtime = create_runtime(AppConfig(self.config(provider='siliconflow', models={
            'chat': ModelOptions(api_model='remote/chat', extra_body={'max_tokens': 256}),
        })))
        self.addCleanup(runtime.close)
        response = Mock()
        response.json.return_value = {'choices': [{'message': {'content': 'answer'}}]}
        response.iter_lines.return_value = ['data: [DONE]']
        with patch.object(runtime.provider.session, 'request', return_value=response) as request:
            runtime.provider.complete([], model='chat', thinking=True)
            sync = request.call_args.kwargs['json']
            list(runtime.provider.stream([], model='chat', thinking=True))
            stream = request.call_args.kwargs['json']
        self.assertFalse(sync.pop('stream'))
        self.assertTrue(stream.pop('stream'))
        self.assertEqual(stream.pop('stream_options'), {'include_usage': True})
        self.assertEqual(sync, stream)
        self.assertEqual(sync['model'], 'remote/chat')
        self.assertTrue(sync['enable_thinking'])
