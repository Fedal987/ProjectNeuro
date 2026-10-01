from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.main.api.exceptions import ProviderConnectionError

from rich.console import Console

from src.main.msg.command_utils import CommandManager
from src.main.msg.message_handler import MessageHandler
from src.main.api.api_manager import create_runtime
from src.main.config import APIConfig, AppConfig
from src.main.ui.i18n import tr
from src.main.ui.terminal_cli import build_bottom_toolbar


class ModelCommandTests(unittest.TestCase):
    def setUp(self):
        runtime = create_runtime(AppConfig(APIConfig("https://example.invalid/v1", "test-key", "test-model")), provider=Mock())
        self.addCleanup(runtime.close)
        self.handler = MessageHandler(runtime=runtime)
        self.output = StringIO()
        self.commands = CommandManager(
            Console(file=self.output, width=120),
            SimpleNamespace(current_handler=self.handler), translator=tr,
        )

    def test_switch_updates_payload_and_toolbar_preserving_history(self):
        self.handler.add_user_message("Keep this conversation")
        history = list(self.handler.history)
        self.assertFalse(self.commands.execute("/model example-model high"))
        self.handler.agent._request_completion()
        payload = self.handler.runtime.provider.complete.call_args.kwargs
        self.assertEqual(payload["model"], "example-model")
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertEqual(self.handler.history, history)
        self.assertIn("example-model high", build_bottom_toolbar(self.handler))

    def test_effort_only_and_default(self):
        model = self.handler.agent.model
        self.commands.execute("/model effort low")
        self.assertEqual(self.handler.agent.model, model)
        self.assertEqual(self.handler.agent.reasoning_effort, "low")
        self.commands.execute("/model effort default")
        self.assertEqual(self.handler.agent.reasoning_effort, "")

    def test_invalid_arguments_do_not_change_settings(self):
        model, effort = self.handler.agent.model, self.handler.agent.reasoning_effort
        for command in ("/model new-model invalid", "/model effort", "/model a b c"):
            with self.subTest(command=command):
                self.commands.execute(command)
                self.assertEqual((self.handler.agent.model, self.handler.agent.reasoning_effort), (model, effort))

    def test_model_only_preserves_effort(self):
        self.commands.execute("/model effort medium")
        self.commands.execute("/model another-model")
        self.assertEqual(self.handler.agent.reasoning_effort, "medium")

    def test_show_current_settings(self):
        self.commands.execute("/model")
        self.assertIn(self.handler.agent.model, self.output.getvalue())
        self.assertIn("/model effort", self.output.getvalue())

    def test_list_fetches_models_and_marks_current_without_switching(self):
        model = self.handler.agent.model
        self.handler.runtime.provider.list_models.return_value = [model, "zz-test-model"]
        self.assertFalse(self.commands.execute("/model list"))
        self.handler.runtime.provider.list_models.assert_called_once_with()
        output = self.output.getvalue()
        self.assertIn(f"{model} *", output)
        self.assertEqual(output.count(f"{model} *"), 1)
        self.assertIn("zz-test-model", output)
        self.assertEqual(self.handler.agent.model, model)

    def test_empty_model_list(self):
        with patch("src.main.api.api_manager.list_models", return_value=[]):
            self.commands.execute("/model list")
        self.assertIn(tr("model_list_empty"), self.output.getvalue())

    def test_list_connection_failure_is_reported_without_switching(self):
        model = self.handler.agent.model
        error = ProviderConnectionError("connection failed")
        with patch("src.main.api.api_manager.list_models", side_effect=error):
            self.assertFalse(self.commands.execute("/model list"))
        self.assertIn(str(error), self.output.getvalue())
        self.assertEqual(self.handler.agent.model, model)

    def test_list_rejects_extra_arguments(self):
        model = self.handler.agent.model
        with patch("src.main.api.api_manager.list_models") as list_models:
            self.commands.execute("/model list high")
            list_models.assert_not_called()
        self.assertEqual(self.handler.agent.model, model)


if __name__ == "__main__":
    unittest.main()
