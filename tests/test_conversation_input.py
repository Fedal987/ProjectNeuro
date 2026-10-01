import threading
import unittest

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from src.main.ui.terminal_cli import ConversationInput


class ScreenOutput(DummyOutput):
    def __init__(self):
        self.entries = 0
        self.exits = 0

    def enter_alternate_screen(self):
        self.entries += 1

    def quit_alternate_screen(self):
        self.exits += 1


class ConversationInputTests(unittest.TestCase):
    def setUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.output = ScreenOutput()
        self.submitted = threading.Event()
        self.messages = []
        self.ui = ConversationInput(
            lambda: "You > ", self.on_submit, lambda: None, lambda: None,
            history=InMemoryHistory(), toolkit_input=self.pipe,
            toolkit_output=self.output,
        )
        self.addCleanup(self.ui.stop)

    def on_submit(self, text):
        self.messages.append(text)
        self.submitted.set()

    def test_screen_survives_submission_and_response(self):
        self.pipe.send_text("hello\r")
        self.assertEqual(self.ui.read_input(), "hello")
        application = self.ui._application
        self.ui.begin_response()
        self.ui.append_markdown("**Reply**\n")
        self.pipe.send_text("queued\x1b\rmessage\r")
        self.assertTrue(self.submitted.wait(3))
        self.assertEqual(self.messages, ["queued\nmessage"])
        self.ui.finish_response()
        self.pipe.send_text("next\r")
        self.assertEqual(self.ui.read_input(), "next")
        self.assertIs(self.ui._application, application)
        self.assertEqual(self.output.entries, 1)
        self.assertEqual(self.output.exits, 0)
        self.ui.stop()
        self.assertEqual(self.output.exits, 1)

    def test_input_background_is_black(self):
        self.pipe.send_text("hello\r")
        self.ui.read_input()
        style = self.ui._application.style
        self.assertEqual(
            style.get_attrs_for_style_str("class:input-field").bgcolor, "000000"
        )

    def test_eof_reaches_caller(self):
        self.pipe.send_text("\x04")
        with self.assertRaises(EOFError):
            self.ui.read_input()

    def assert_multiline_draft(self, keystrokes, expected):
        rendered = threading.Event()
        after_render = self.ui._after_render

        def on_render(application):
            after_render(application)
            if self.ui._input_area.text == expected:
                rendered.set()

        self.ui._after_render = on_render
        self.ui.start()
        self.pipe.send_text(keystrokes)
        self.assertTrue(rendered.wait(3), repr(self.ui._thread_error))
        self.assertTrue(self.ui._messages.empty())
        self.assertEqual(self.ui._input_area.window.render_info.window_height, 2)
        self.assertGreater(self.ui._input_area.window.vertical_scroll, 0)
        self.pipe.send_text("\r")
        self.assertEqual(self.ui.read_input(), expected)

    def test_shift_enter_and_ctrl_j_scroll_multiline_draft(self):
        self.assert_multiline_draft(
            "第一行\x1b[13;2u第二行\x1b[27;2;13~第三行\n第四行",
            "第一行\n第二行\n第三行\n第四行",
        )

    def test_pasted_multiline_draft(self):
        text = "第一行\n第二行\n第三行\n第四行"
        self.assert_multiline_draft("\x1b[200~" + text + "\x1b[201~", text)

    def test_scroll_position_uses_cached_output_snapshot(self):
        self.pipe.send_text("hello\r")
        self.ui.read_input()
        control = self.ui._output_area.content
        self.ui.stop()
        self.ui.append_output("first line")
        application = Application(input=self.pipe, output=self.output)
        with set_app(application):
            control.create_content(80, 20)
            self.ui.append_output("\nnew line\nanother line")
            content = control.create_content(80, 20)
            self.assertLess(content.cursor_position.y, content.line_count)
            content.get_line(content.cursor_position.y)

    def test_keyboard_mode_follows_alternate_screen_lifecycle(self):
        transitions = []
        self.ui.on_screen_enter = lambda: transitions.append(
            ("enable", self.output.entries, self.output.exits)
        )
        self.ui.on_screen_exit = lambda: transitions.append(
            ("disable", self.output.entries, self.output.exits)
        )
        self.pipe.send_text("hello\r")
        self.ui.read_input()
        self.ui.stop()
        self.pipe.send_text("resumed\r")
        self.assertEqual(self.ui.read_input(), "resumed")
        self.ui.stop()
        self.assertEqual(transitions, [
            ("enable", 1, 0), ("disable", 1, 0),
            ("enable", 2, 1), ("disable", 2, 1),
        ])


if __name__ == "__main__":
    unittest.main()
