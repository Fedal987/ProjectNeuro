from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.main.api import api_manager
from src.main.config import APIConfig, AppConfig, ReasoningConfig, load_config
from src.main.msg.message_handler import MessageHandler


class ConfigurationRuntimeTests(unittest.TestCase):
    def config(self, model="test-model"):
        return AppConfig(APIConfig("https://example.invalid/v1", "test-secret", model))

    def test_import_does_not_read_config_or_create_client(self):
        root = str(Path(__file__).resolve().parents[1])
        script = textwrap.dedent(f'''
            import builtins
            from pathlib import Path
            import sys
            from unittest.mock import patch
            sys.path.insert(0, {root!r})
            original_open = builtins.open
            original_path_open = Path.open
            def check(path):
                if str(path).endswith('.toml'):
                    raise AssertionError('import attempted to read configuration')
            def guarded_open(path, *args, **kwargs):
                check(path)
                return original_open(path, *args, **kwargs)
            def guarded_path_open(path, *args, **kwargs):
                check(path)
                return original_path_open(path, *args, **kwargs)
            with patch('builtins.open', guarded_open), patch.object(Path, 'open', guarded_path_open), patch('requests.Session') as client:
                from src.main.api import api_manager
                from src.main.msg.message_handler import MessageHandler
                from src.main.msg.session_manager import SessionManager
                from src.main.ui import terminal_cli
                client.assert_not_called()
                assert api_manager._default_runtime is None
                assert 'src.main.msg.information_handler' not in sys.modules
                try:
                    MessageHandler()
                except RuntimeError as error:
                    assert 'not initialized' in str(error)
                else:
                    raise AssertionError('missing runtime was not reported')
        ''')
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, "-c", script], cwd=cwd, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_load_explicit_config_preserves_legacy_temperature(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            path.write_text('[API_MANAGER]\nBASE_URL="https://example.invalid/v1"\nAPI_KEY="secret"\nMODEL="chosen"\nSTREAM=false\nTEMPREATURE=0.7\n[REASONING]\nENABLED=false\nMAX_STEPS=5\n', encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.api.temperature, 0.7)
        self.assertFalse(config.api.stream)
        self.assertFalse(config.reasoning.enabled)
        self.assertEqual(config.reasoning.max_steps, 5)
        self.assertNotIn("secret", repr(config))

    def test_temperature_is_optional_and_correct_spelling_takes_precedence(self):
        api = dict(BASE_URL="https://example.invalid/v1", API_KEY="secret", MODEL="test")
        self.assertIsNone(AppConfig.from_mapping({"API_MANAGER": api}).api.temperature)
        api.update(TEMPREATURE=0.7, TEMPERATURE=0)
        self.assertEqual(AppConfig.from_mapping({"API_MANAGER": api}).api.temperature, 0)

    def test_missing_explicit_path_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                load_config(Path(directory) / "missing.toml")

    def test_invalid_settings_are_rejected_without_exposing_secrets(self):
        for kwargs in ({"stream": "false"}, {"temperature": float("nan")}, {"model": ""}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError) as error:
                    replace(self.config().api, **kwargs)
                self.assertNotIn("test-secret", str(error.exception))
        with self.assertRaises(ValueError):
            ReasoningConfig(max_steps=0)

    def test_independent_runtimes_do_not_share_settings_or_usage(self):
        first = api_manager.create_runtime(self.config("first"), provider=Mock())
        second_config = AppConfig(replace(self.config("second").api, stream=False), ReasoningConfig(enabled=False))
        second = api_manager.create_runtime(second_config, provider=Mock())
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        left = MessageHandler(runtime=first, system_prompt="test")
        right = MessageHandler(runtime=second, system_prompt="test")
        self.assertEqual(left.agent.model, "first")
        self.assertEqual(right.agent.model, "second")
        self.assertTrue(left.reasoning_enabled)
        self.assertFalse(right.reasoning_enabled)
        self.assertFalse(right.use_stream)
        first.usage_tracker.record({"total_tokens": 10})
        self.assertEqual(second.usage_tracker.snapshot().total_tokens, 0)

    def test_explicit_initialization_and_close(self):
        with patch.object(api_manager, "_default_runtime", None), patch.object(api_manager, "create_provider") as factory:
            with self.assertRaisesRegex(RuntimeError, "not initialized"):
                api_manager.get_runtime()
            with api_manager.initialize(self.config()) as runtime:
                self.assertIs(api_manager.get_runtime(), runtime)
                factory.assert_called_once_with(runtime.config.api, runtime.usage_tracker)
            factory.return_value.close.assert_called_once()
            self.assertIsNone(api_manager._default_runtime)

    def test_startup_failure_does_not_create_session(self):
        from src.main.ui import terminal_cli
        with patch("src.main.config.load_config", side_effect=ValueError("invalid settings")), patch.object(terminal_cli, "_run_cli") as run, patch.object(terminal_cli.console, "print") as output:
            with self.assertRaises(SystemExit) as error:
                terminal_cli.main()
            self.assertEqual(error.exception.code, 1)
        run.assert_not_called()
        output.assert_called_once_with("invalid settings", style="red", markup=False)

    def test_startup_reads_once_and_closes_client_on_runtime_failure(self):
        from src.main.ui import terminal_cli
        with patch.object(api_manager, "_default_runtime", None), patch("src.main.config.load_config", return_value=self.config()) as load, patch.object(api_manager, "create_provider") as client, patch.object(terminal_cli, "_run_cli", side_effect=RuntimeError("UI failed")):
            with self.assertRaisesRegex(RuntimeError, "UI failed"):
                terminal_cli.main("explicit.toml")
            load.assert_called_once_with("explicit.toml")
            client.return_value.close.assert_called_once()
            self.assertIsNone(api_manager._default_runtime)
