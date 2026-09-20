import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.main.tool.toolcall_utils import Agent


class AgentSessionEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = Agent(api_key="unused", workspace=Path(self.temp.name),
                           system_prompt="test", base_url="https://example.invalid", model="test")
        self.events = []
        self.agent.event_sink = lambda kind, payload: self.events.append((kind, payload))
        self.call = {"id": "call1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}

    def tearDown(self):
        self.agent.session.close()
        self.temp.cleanup()

    def execute(self, call):
        self.assertEqual(self.events[-1][0], "tool_call")
        self.assertEqual(self.events[-1][1]["tool_call_id"], "call1")
        return "result"

    def test_nonstream_records_call_before_effect_and_result_after(self):
        replies = [{"content": "", "tool_calls": [self.call]}, {"content": "done"}]
        with patch.object(self.agent, "_request_completion", side_effect=replies), patch.object(self.agent, "_execute_tool_call", side_effect=self.execute):
            self.assertEqual(self.agent.run("hello"), "done")
        self.assertEqual([kind for kind, _ in self.events], ["user_message", "assistant_message", "tool_call", "tool_result", "assistant_message"])

    def test_stream_records_same_semantic_events(self):
        call = dict(self.call, index=0)
        responses = [iter([{"choices": [{"delta": {"tool_calls": [call]}}]}]),
                     iter([{"choices": [{"delta": {"content": "done"}}]}])]
        with patch.object(self.agent, "_request_completion_stream", side_effect=responses), patch.object(self.agent, "_execute_tool_call", side_effect=self.execute):
            list(self.agent.run_stream_events("hello"))
        self.assertEqual([kind for kind, _ in self.events], ["user_message", "assistant_message", "tool_call", "tool_result", "assistant_message"])

    def test_durability_failure_prevents_tool_execution(self):
        def sink(kind, payload):
            if kind == "tool_call":
                raise OSError("disk full")
        self.agent.event_sink = sink
        with patch.object(self.agent, "_request_completion", return_value={"tool_calls": [self.call]}), patch.object(self.agent, "_execute_tool_call") as execute:
            with self.assertRaises(OSError):
                self.agent.run("hello")
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
