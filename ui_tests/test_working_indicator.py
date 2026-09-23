import unittest
from unittest.mock import patch

from src.main.ui.terminal_cli import ConversationInput


class WorkingIndicatorTests(unittest.TestCase):
    def setUp(self):
        self.ui = ConversationInput(lambda: '', lambda _: None, lambda: None, lambda: None)

    def rendered(self):
        return ''.join(fragment[1] for fragment in self.ui._formatted_output())

    def test_waiting_timer_is_transient_and_rolls_over(self):
        with patch('src.main.ui.terminal_cli.time.monotonic', return_value=100):
            self.ui.begin_response()
            self.assertIn('Neuro Working... (0m/0s)', self.rendered())
        with patch('src.main.ui.terminal_cli.time.monotonic', return_value=165):
            self.assertIn('Neuro Working... (1m/5s)', self.rendered())
        self.assertNotIn('Working', self.ui.transcript)
        self.ui.finish_response()
        self.assertNotIn('Working', self.rendered())

    def test_real_content_or_reasoning_replaces_indicator(self):
        for kind in ('content', 'reasoning'):
            with self.subTest(kind=kind):
                self.ui.begin_response()
                self.ui.response_progress(kind, '')
                self.assertIn('Working', self.rendered())
                self.ui.response_progress(kind, 'actual output')
                self.assertNotIn('Working', self.rendered())
                self.ui.finish_response()

    def test_tool_rounds_and_interruption(self):
        self.ui.begin_response()
        self.ui.response_progress('tool', 'list directory')
        self.assertNotIn('Working', self.rendered())
        self.ui.response_progress('tool_result', 'done')
        self.assertIn('Working', self.rendered())
        self.ui.response_progress('interrupted', 'stopped')
        self.assertNotIn('Working', self.rendered())
        self.ui.response_progress('queued_user', 'next question')
        self.assertIn('Working', self.rendered())
        self.ui.response_progress('error', 'failed')
        self.assertNotIn('Working', self.rendered())

    def test_nonstream_completion_clears_indicator(self):
        self.ui.begin_response()
        self.ui.set_working(False)
        self.ui.append_markdown('answer')
        self.assertNotIn('Working', self.rendered())
