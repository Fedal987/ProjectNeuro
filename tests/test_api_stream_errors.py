import unittest
from unittest.mock import Mock

from src.main.api import api_manager
from src.main.api.exceptions import ProviderConnectionError
from src.main.config import APIConfig, AppConfig


class APIStreamErrorTests(unittest.TestCase):
    def setUp(self):
        self.runtime = api_manager.create_runtime(
            AppConfig(APIConfig("https://example.invalid/v1", "test-key", "test-model", stream=True)), provider=Mock()
        )
        self.addCleanup(self.runtime.close)

    def test_midstream_error_preserves_prefix(self):
        def chunks():
            yield {"choices": [{"delta": {"content": "hello"}}]}
            raise ProviderConnectionError("disconnected")
        self.runtime.provider.stream.return_value = chunks()
        result = api_manager.get_completion([], stream=True, runtime=self.runtime)
        self.assertEqual(next(result), "hello")
        with self.assertRaises(ProviderConnectionError):
            next(result)

    def test_nonstream_error_propagates(self):
        self.runtime.provider.complete.side_effect = ProviderConnectionError("failed")
        with self.assertRaises(ProviderConnectionError):
            api_manager.get_completion([], stream=False, runtime=self.runtime)

    def test_default_stream_setting(self):
        self.runtime.provider.stream.return_value = iter([])
        result = api_manager.get_completion([], stream=None, runtime=self.runtime)
        self.runtime.provider.stream.assert_called_once()
        self.assertIsNotNone(result)
