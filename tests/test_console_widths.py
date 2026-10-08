"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

from __future__ import annotations

import os
import unicodedata
import unittest
from io import StringIO
from unittest.mock import patch

from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.utils import get_cwidth
from rich.cells import cell_len
from rich.console import Console
from rich.panel import Panel

from src.main.ui import widths
from src.main.ui.widths import (
    ConsoleWidthOracle,
    apply_width_overrides,
    clear_width_overrides,
    display_width,
    is_disabled,
)


def console_like(char: str) -> int:
    if char in "\u00b7\u2014\u2026\u2019\u2192\u00b0\u00b1\u00d7":
        return 2
    if char.isascii():
        return 1
    if unicodedata.category(char).startswith("M"):
        return 0
    if "\u0400" <= char <= "\u045f":
        return 2
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


CONSOLE_WIDER = "\u00b7\u2014\u2026\u2019\u2192\u00b0\u00b1\u00d7\u0416\u0451"

RU_LONG = "\u0412\u044b\u0441\u043e\u043a\u043e\u043f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0435"
RU_PHRASE = RU_LONG + " \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435"


class WidthOracleTests(unittest.TestCase):
    def test_measures_only_characters_the_console_can_differ_on(self):
        measured: list[str] = []

        def measurer(char: str) -> int:
            measured.append(char)
            return 2

        oracle = ConsoleWidthOracle(measurer)
        self.assertIsNone(oracle.width("A"))
        self.assertIsNone(oracle.width("~"))
        self.assertIsNone(oracle.width("\u0301"))
        self.assertEqual(measured, [])

        self.assertEqual(oracle.width("\u00b7"), 2)
        self.assertEqual(oracle.width("\u00b7"), 2)
        self.assertEqual(measured, ["\u00b7"])

    def test_agrees_measures_on_demand_for_unseen_characters(self):
        oracle = ConsoleWidthOracle(console_like)
        self.assertTrue(oracle.agrees("abc"))
        self.assertFalse(oracle.agrees("a\u00b7b"))
        self.assertFalse(oracle.agrees("\u0416"))

    def test_text_width_uses_console_widths(self):
        oracle = ConsoleWidthOracle(console_like)
        self.assertEqual(oracle.text_width("ab"), 2)
        self.assertEqual(oracle.text_width("a\u00b7b"), 1 + 2 + 1)
        self.assertEqual(oracle.text_width("\u4f60\u597d"), 4)
        self.assertEqual(oracle.text_width("e\u0301"), 1)

    def test_oracle_without_measurer_defers_everything(self):
        oracle = ConsoleWidthOracle(None)
        self.assertFalse(oracle.measurer_available)
        self.assertIsNone(oracle.width("\u00b7"))
        self.assertTrue(oracle.agrees("anything \u00b7 at all"))

    def test_disable_flag_reads_the_environment(self):
        for value in ("0", "false", "no", "off", "FALSE", " off "):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {widths.DISABLE_ENV: value}),
            ):
                self.assertTrue(is_disabled())
        for value in ("1", "true", "", "yes"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {widths.DISABLE_ENV: value}),
            ):
                self.assertFalse(is_disabled())


class OverrideTests(unittest.TestCase):

    def setUp(self):
        self.addCleanup(clear_width_overrides)
        clear_width_overrides()
        self.oracle = ConsoleWidthOracle(console_like)

    def test_overrides_align_rich_and_prompt_toolkit(self):
        for char in CONSOLE_WIDER:
            with self.subTest(char=char):
                expected = console_like(char)
                message = "fixture is not a difference"
                self.assertNotEqual(cell_len(char), expected, message)
                self.assertNotEqual(get_cwidth(char), expected, message)

        self.assertTrue(apply_width_overrides(self.oracle))

        for char in CONSOLE_WIDER:
            with self.subTest(char=char):
                expected = console_like(char)
                self.assertEqual(cell_len(char), expected)
                self.assertEqual(get_cwidth(char), expected)

    def test_agreement_between_libraries_and_console_for_every_catalog_character(self):
        apply_width_overrides(self.oracle)
        for char in CONSOLE_WIDER:
            with self.subTest(char=char):
                self.assertEqual(cell_len(char), console_like(char))
                self.assertEqual(get_cwidth(char), console_like(char))

    def test_unchanged_characters_keep_their_widths(self):
        unchanged = "A~ \u4f60\u2500\u2502"
        before = {char: (cell_len(char), get_cwidth(char)) for char in unchanged}
        apply_width_overrides(self.oracle)
        for char, (rich_before, ptk_before) in before.items():
            with self.subTest(char=char):
                self.assertEqual(cell_len(char), rich_before)
                self.assertEqual(get_cwidth(char), ptk_before)

    def test_clear_restores_the_library_tables(self):
        original = {char: cell_len(char) for char in CONSOLE_WIDER}
        apply_width_overrides(self.oracle)
        self.assertEqual(cell_len(CONSOLE_WIDER[0]), console_like(CONSOLE_WIDER[0]))
        clear_width_overrides()
        for char, width in original.items():
            with self.subTest(char=char):
                self.assertEqual(cell_len(char), width)
                self.assertEqual(cell_len(char), width)

    def test_apply_is_idempotent(self):
        self.assertTrue(apply_width_overrides(self.oracle))
        self.assertTrue(apply_width_overrides(ConsoleWidthOracle(console_like)))
        self.assertEqual(cell_len("\u00b7"), 2)

    def test_apply_without_a_measurer_does_nothing(self):
        self.assertFalse(apply_width_overrides(ConsoleWidthOracle(None)))
        self.assertEqual(cell_len("\u00b7"), 1)


class PanelAlignmentTests(unittest.TestCase):

    def setUp(self):
        self.addCleanup(clear_width_overrides)
        clear_width_overrides()

    def border_columns(self, rendered: str) -> list[int]:
        columns = []
        for raw in rendered.splitlines():
            fragments = to_formatted_text(ANSI(raw))
            plain = "".join(fragment for _style, fragment, *_ in fragments)
            if not plain:
                continue
            columns.append(sum(console_like(char) for char in plain))
        return columns

    def render_panel(self, content: str) -> str:
        buffer = StringIO()
        Console(
            file=buffer, force_terminal=True, color_system="truecolor", width=100
        ).print(Panel.fit(content, border_style="cyan"))
        return buffer.getvalue()

    def test_uncalibrated_panel_is_ragged(self):
        rendered = self.render_panel(f"{RU_LONG}\nshort")
        self.assertGreater(len(set(self.border_columns(rendered))), 1)

    def test_calibrated_panel_borders_align(self):
        apply_width_overrides(ConsoleWidthOracle(console_like))
        rendered = self.render_panel(f"{RU_PHRASE}\nshort")
        columns = self.border_columns(rendered)
        self.assertEqual(len(columns), 4)
        self.assertEqual(len(set(columns)), 1, f"borders ragged: {columns}")

    def test_display_width_matches_the_console(self):
        apply_width_overrides(ConsoleWidthOracle(console_like))
        self.assertEqual(display_width("\u0412\u044b"), 4)
        self.assertEqual(display_width("ab\u00b7"), 4)


class WelcomeRedrawTests(unittest.TestCase):

    def setUp(self):
        self.addCleanup(clear_width_overrides)

    @staticmethod
    def runtime():
        from types import SimpleNamespace

        return SimpleNamespace(
            config=SimpleNamespace(
                api=SimpleNamespace(
                    base_url="http://localhost:8080/v1", model="test-model"
                )
            )
        )

    def test_welcome_text_renders_a_panel_without_clearing_screen(self):
        from src.main.ui.terminal_cli import render_welcome_text

        rendered = render_welcome_text(runtime=self.runtime())
        self.assertIn("test-model", rendered)
        self.assertNotIn("\x1b[2J", rendered)

    def test_replace_output_discards_previous_content(self):
        from src.main.ui.terminal_cli import ConversationInput

        ui = ConversationInput(
            lambda: "You > ", lambda text: None, lambda: None, lambda: None
        )
        ui.append_output("old panel\n")
        self.assertIn("old panel", ui.transcript)
        ui.replace_output("new panel\n")
        self.assertEqual(ui.transcript, "new panel\n")
        self.assertNotIn("old panel", ui.transcript)

    def test_language_change_replaces_instead_of_stacking(self):
        from types import SimpleNamespace

        from src.main.msg.command_utils import CommandManager
        from src.main.ui.terminal_cli import ConversationInput, render_welcome_text

        runtime = SimpleNamespace(
            config=SimpleNamespace(
                api=SimpleNamespace(
                    base_url="http://localhost:8080/v1", model="test-model"
                )
            )
        )
        ui = ConversationInput(
            lambda: "You > ", lambda text: None, lambda: None, lambda: None
        )
        ui.append_output(render_welcome_text(runtime=runtime))

        language = {"code": "en"}

        def set_language(code: str) -> str:
            language["code"] = code
            return code

        def callback() -> None:
            ui.replace_output(render_welcome_text(runtime=runtime))

        manager = CommandManager(
            Console(file=StringIO(), width=100),
            SimpleNamespace(current_workspace=None),
            translator=lambda key, **values: key,
            language_getter=lambda: language["code"],
            language_setter=set_language,
            language_names={"en": "English", "ru": "Russian"},
            language_changed_callback=callback,
        )
        manager.execute("/lang ru")
        self.assertEqual(language["code"], "ru")
        self.assertEqual(
            ui.transcript.count("test-model"), 1, "welcome panel was stacked"
        )
        self.assertEqual(
            ui.transcript.count("http://localhost:8080/v1"),
            1,
            "welcome panel was stacked",
        )

    def test_concurrent_replace_and_append_stay_consistent(self):
        from src.main.ui.terminal_cli import ConversationInput

        ui = ConversationInput(
            lambda: "You > ", lambda text: None, lambda: None, lambda: None
        )
        for index in range(40):
            ui.append_output(f"line {index}\n")
        ui.replace_output("panel\n")
        self.assertEqual(ui.transcript, "panel\n")
        self.assertEqual(len(ui._blocks), 1)


class CalibrationEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(clear_width_overrides)
        clear_width_overrides()

    def test_no_calibration_without_a_console(self):
        from src.main.ui import widths as widths_module

        with patch.object(widths_module, "_stdout_is_console", return_value=False):
            self.assertFalse(widths_module.calibrate_console_widths())

    def test_no_calibration_when_disabled(self):
        from src.main.ui import widths as widths_module

        with (
            patch.dict(os.environ, {widths_module.DISABLE_ENV: "0"}),
            patch.object(widths_module, "_stdout_is_console", return_value=True),
        ):
            self.assertFalse(widths_module.calibrate_console_widths())

    def test_calibration_uses_the_console_measurer_when_one_is_available(self):
        from src.main.ui import widths as widths_module

        with (
            patch.dict(os.environ, {widths_module.DISABLE_ENV: "1"}),
            patch.object(widths_module, "_stdout_is_console", return_value=True),
            patch.object(
                widths_module, "_windows_measurer_factory", return_value=console_like
            ),
        ):
            self.assertTrue(widths_module.calibrate_console_widths())
            self.assertEqual(cell_len("\u00b7"), 2)
            self.assertTrue(widths_module.calibrate_console_widths())

    def test_calibration_declines_when_the_measurer_is_unusable(self):
        from src.main.ui import widths as widths_module

        with (
            patch.object(widths_module, "_stdout_is_console", return_value=True),
            patch.object(
                widths_module, "_windows_measurer_factory", return_value=lambda _c: None
            ),
        ):
            self.assertFalse(widths_module.calibrate_console_widths())
            self.assertEqual(cell_len("\u00b7"), 1)


if __name__ == "__main__":
    unittest.main()
