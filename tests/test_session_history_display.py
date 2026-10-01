from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from rich.console import Console
from src.main.msg.command_utils import CommandManager
from src.main.ui.i18n import tr


class SessionHistoryDisplayTests(unittest.TestCase):
    def setUp(self):
        self.output = StringIO()
        self.history = [
            {'role': 'system', 'content': 'internal system instructions'},
            {'role': 'user', 'content': 'First question [red]literal[/red]'},
            {'role': 'assistant', 'content': '**First answer**', 'reasoning_content': 'internal reasoning'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'tool-1'}]},
            {'role': 'tool', 'content': 'internal tool result'},
            {'role': 'user', 'content': 'Second question'},
            {'role': 'assistant', 'content': '```python\nprint(42)\n```'},
        ]
        self.manager = Mock(current_workspace=Path.cwd())
        self.manager.switch_session.return_value = SimpleNamespace(
            name='saved', handler=SimpleNamespace(history=self.history))
        self.commands = CommandManager(Console(file=self.output, width=100),
                                       self.manager, translator=tr)

    def test_switch_by_number_or_name_displays_history_in_order(self):
        for target in ('9', 'saved'):
            with self.subTest(target=target):
                self.output.seek(0)
                self.output.truncate()
                original = repr(self.history)
                self.assertFalse(self.commands.execute('/session ' + target))
                text = self.output.getvalue()
                self.assertIn('First question [red]literal[/red]', text)
                self.assertIn('First answer', text)
                self.assertIn('print(42)', text)
                self.assertLess(text.index('First question'), text.index('First answer'))
                self.assertLess(text.index('First answer'), text.index('Second question'))
                self.assertNotIn('internal', text)
                self.assertEqual(repr(self.history), original)
                self.manager.switch_session.assert_called_with(
                    int(target) if target.isdigit() else target, workspace=Path.cwd())

    def test_failed_switch_does_not_replay_history(self):
        self.manager.switch_session.side_effect = ValueError('cannot restore')
        self.commands.execute('/session saved')
        self.assertIn('cannot restore', self.output.getvalue())
        self.assertNotIn('First question', self.output.getvalue())

    def test_empty_session_switch_still_succeeds(self):
        self.manager.switch_session.return_value.handler.history = self.history[:1]
        self.assertFalse(self.commands.execute('/session saved'))
        self.assertIn('saved', self.output.getvalue())
        self.assertNotIn('internal', self.output.getvalue())
