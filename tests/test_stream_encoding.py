"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

import json
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import requests

from src.main.agent.agent import Agent
from src.main.api.openai_compatible import OpenAICompatibleProvider
from src.main.api.usage import UsageTracker


class StreamEncodingTests(unittest.TestCase):
    def test_utf8_sse_with_missing_or_incorrect_charset(self):
        expected = "你好！有什么可以帮你？🙂"
        chunk = {"choices": [{"delta": {"content": expected}}]}
        body = ("data: " + json.dumps(chunk, ensure_ascii=False)
                + "\n\ndata: [DONE]\n\n").encode("utf-8")
        for content_type in ("text/event-stream", "text/event-stream; charset=ISO-8859-1",
                             "text/event-stream; charset=utf-8"):
            with self.subTest(content_type=content_type):
                response = requests.Response()
                response.status_code = 200
                response.headers["Content-Type"] = content_type
                response.encoding = requests.utils.get_encoding_from_headers(response.headers)
                response.raw = BytesIO(body)
                agent = Agent(provider=OpenAICompatibleProvider(api_key="test", base_url="https://example.invalid/v1", usage_tracker=UsageTracker()), workspace=Path.cwd(), system_prompt="Test",
                              model="test-model")
                try:
                    with patch.object(agent.provider.session, "request", return_value=response):
                        chunks = list(agent._request_completion_stream())
                    self.assertEqual(chunks, [chunk])
                    self.assertTrue(response.raw.closed)
                finally:
                    agent.provider.session.close()
