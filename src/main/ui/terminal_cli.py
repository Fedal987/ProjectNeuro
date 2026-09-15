"""
    Neuro-cli
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/neuro-cli-py
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from io import StringIO
from queue import Empty, Queue
from threading import Lock
from typing import Any, Mapping, TextIO

from prompt_toolkit.application import Application, get_app, get_app_session
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea
from pygments.lexers import PythonLexer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from src.main.ui.i18n import LANGUAGE_NAMES, get_language, set_language, tr
from src.main.tool.toolcall_utils import get_current_path


@dataclass(frozen=True)
class UsageSnapshot:
    total_tokens: int
    cached_tokens: int
    prompt_tokens: int

    @property
    def cache_hit_rate(self) -> float:
        if self.prompt_tokens == 0:
            return 0.0
        return self.cached_tokens / self.prompt_tokens * 100


class UsageTracker:
    def __init__(self) -> None:
        self._total_tokens = 0
        self._cached_tokens = 0
        self._prompt_tokens = 0
        self._lock = Lock()

    def record(self, usage: Mapping[str, Any] | None) -> None:
        if not usage:
            return
        prompt_tokens = self._token_count(usage.get("prompt_tokens"))
        completion_tokens = self._token_count(usage.get("completion_tokens"))
        total_tokens = self._token_count(usage.get("total_tokens"))
        cached_tokens = min(
            prompt_tokens,
            self._token_count(usage.get("prompt_cache_hit_tokens")),
        )
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        with self._lock:
            self._total_tokens += total_tokens
            self._cached_tokens += cached_tokens
            self._prompt_tokens += prompt_tokens

    def snapshot(self) -> UsageSnapshot:
        with self._lock:
            return UsageSnapshot(
                total_tokens=self._total_tokens,
                cached_tokens=self._cached_tokens,
                prompt_tokens=self._prompt_tokens,
            )

    @staticmethod
    def _token_count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0


class DoubleEscapeDetector:
    def __init__(self) -> None:
        self._waiting_for_second = False

    def press(self) -> bool:
        if self._waiting_for_second:
            self._waiting_for_second = False
            return True
        self._waiting_for_second = True
        return False

    def reset(self) -> None:
        self._waiting_for_second = False


class _ScreenOutput:
    """Apply keyboard modes to the alternate screen, where input is read."""

    def __init__(self, output, on_enter, on_exit):
        self.output = output
        self.on_enter = on_enter
        self.on_exit = on_exit

    def __getattr__(self, name):
        return getattr(self.output, name)

    def enter_alternate_screen(self):
        self.output.enter_alternate_screen()
        self.output.flush()
        self.on_enter()

    def quit_alternate_screen(self):
        self.on_exit()
        self.output.quit_alternate_screen()


class ConversationInput:
    def __init__(
        self,
        prompt: Callable[[], str],
        on_submit: Callable[[str], None],
        on_first_escape: Callable[[], None],
        on_interrupt: Callable[[], None],
        bottom_toolbar: Callable[[], str] | None = None,
        history: Any = None,
        on_screen_enter: Callable[[], None] = lambda: None,
        on_screen_exit: Callable[[], None] = lambda: None,
        toolkit_input: Any = None,
        toolkit_output: Any = None,
    ) -> None:
        self.prompt = prompt
        self.on_submit = on_submit
        self.on_first_escape = on_first_escape
        self.on_interrupt = on_interrupt
        self.bottom_toolbar = bottom_toolbar
        self.history = history
        self.on_screen_enter = on_screen_enter
        self.on_screen_exit = on_screen_exit
        self.toolkit_input = toolkit_input
        self.toolkit_output = toolkit_output
        self._stop_event = threading.Event()
        self._responding = threading.Event()
        self._messages: Queue[str] = Queue()
        self._thread_error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._application: Any = None
        self._application_lock = threading.Lock()
        self._draft = ""
        self._transcript = ""
        self._transcript_lock = threading.Lock()
        self._blocks: list[dict[str, Any]] = []
        self._input_area: TextArea | None = None
        self._output_area: Window | None = None
        self._escape_detector = DoubleEscapeDetector()
        self._render_condition = threading.Condition()
        self._revision = 0
        self._displayed_revision = 0
        self._rendered_revision = 0

    def read_input(self) -> str:
        """Wait for input without leaving or rebuilding the full-screen UI."""
        self.finish_response()
        if self._thread_error is not None:
            raise self._thread_error
        self.start()
        while True:
            if self._thread_error is not None:
                raise self._thread_error
            try:
                return self._messages.get(timeout=0.1)
            except Empty:
                if self._thread is None:
                    raise EOFError()

    def begin_response(self) -> None:
        self._responding.set()

    def finish_response(self) -> None:
        self._responding.clear()
        self._escape_detector.reset()

    def clear_output(self) -> None:
        with self._transcript_lock:
            self._transcript = ""
            self._blocks.clear()
        self._publish_update(wait=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread_error = None
        self._escape_detector.reset()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._application_lock:
            application = self._application
            if self._input_area is not None:
                self._draft = self._input_area.text
        if application is not None:
            self._exit_application(application)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.5)
            if not thread.is_alive():
                self._thread = None
        self._escape_detector.reset()

    @property
    def transcript(self) -> str:
        with self._transcript_lock:
            return self._transcript

    def append_output(self, text: str, style: str = "") -> None:
        if not text:
            return
        self.finalize_markdown()
        with self._transcript_lock:
            self._transcript += text
            if (
                self._blocks
                and self._blocks[-1]["kind"] == "plain"
                and self._blocks[-1]["style"] == style
            ):
                self._blocks[-1]["text"] += text
            else:
                self._blocks.append(
                    {"kind": "plain", "style": style, "text": text}
                )
        self._publish_update()

    def append_markdown(self, text: str) -> None:
        if not text:
            return
        with self._transcript_lock:
            self._transcript += text
            if self._blocks and self._blocks[-1]["kind"] == "markdown":
                self._blocks[-1]["text"] += text
            else:
                self._blocks.append(
                    {
                        "kind": "markdown",
                        "text": text,
                        "rendered": [],
                        "rendered_text": "",
                    }
                )
        self._publish_update()

    def finalize_markdown(self) -> None:
        with self._transcript_lock:
            if not self._blocks or self._blocks[-1]["kind"] != "markdown":
                return
            index = len(self._blocks) - 1
            text = self._blocks[index]["text"]
            if self._blocks[index].get("rendered_text") == text:
                return
        rendered = self._markdown_fragments(text)
        with self._transcript_lock:
            if index < len(self._blocks) and self._blocks[index]["text"] == text:
                self._blocks[index]["rendered"] = rendered
                self._blocks[index]["rendered_text"] = text
        self._publish_update(wait=False)

    def render_transcript(self, console: Console) -> None:
        with self._transcript_lock:
            blocks = [dict(block) for block in self._blocks]
        rich_styles = {
            "class:reasoning": "dim italic cyan",
            "class:reasoning-title": "bold italic cyan",
            "class:answer-title": "bold magenta",
            "class:tool": "dim yellow",
            "class:tool-result": "dim green",
            "class:user": "bold cyan",
            "class:error": "bold red",
            "class:interrupt": "bold yellow",
        }
        for block in blocks:
            if block["kind"] == "markdown":
                console.print(Markdown(block["text"]))
            else:
                console.print(
                    Text.from_ansi(block["text"]),
                    style=rich_styles.get(block["style"]),
                    markup=False,
                    end="",
                )

    def _publish_update(self, wait: bool = True) -> None:
        with self._render_condition:
            self._revision += 1
            revision = self._revision
        active = self._invalidate()
        if not wait or not active or threading.current_thread() is self._thread:
            return
        with self._render_condition:
            self._render_condition.wait_for(
                lambda: self._rendered_revision >= revision,
                timeout=0.15,
            )

    def _invalidate(self) -> bool:
        with self._application_lock:
            application = self._application
            output_area = self._output_area
        if application is None or output_area is None:
            return False

        loop = getattr(application, "loop", None)
        if loop is not None and loop.is_running():
            application.invalidate()
            return True
        return False

    def _formatted_output(self):
        with self._transcript_lock:
            blocks = [dict(block) for block in self._blocks]
        with self._render_condition:
            self._displayed_revision = self._revision
        fragments: list[tuple[str, str]] = []
        for index, block in enumerate(blocks):
            if block["kind"] == "plain":
                if "\x1b" in block["text"]:
                    fragments.extend(to_formatted_text(ANSI(block["text"])))
                else:
                    fragments.append((block["style"], block["text"]))
                continue
            text = block["text"]
            rendered_text = block.get("rendered_text", "")
            rendered = block.get("rendered", [])
            complete_length = text.rfind("\n") + 1
            complete_text = text[:complete_length]
            if complete_text != rendered_text and not rendered_text.startswith(complete_text):
                rendered = self._markdown_fragments(complete_text)
                rendered_text = complete_text
                with self._transcript_lock:
                    if index < len(self._blocks) and self._blocks[index]["text"] == text:
                        self._blocks[index]["rendered"] = rendered
                        self._blocks[index]["rendered_text"] = rendered_text
            fragments.extend(rendered)
            if len(rendered_text) < len(text):
                fragments.append(("", text[len(rendered_text):]))
        # Keep the scroll target in the same snapshot as the rendered lines.
        fragments.append(("[SetCursorPosition]", ""))
        return FormattedText(fragments)

    @staticmethod
    def _markdown_fragments(text: str) -> list[tuple[str, str]]:
        target = StringIO()
        renderer = Console(
            file=target,
            force_terminal=True,
            color_system="truecolor",
            width=100,
        )
        renderer.print(Markdown(text), end="")
        raw_fragments = list(to_formatted_text(ANSI(target.getvalue())))
        compacted: list[tuple[str, str]] = []
        line: list[tuple[str, str]] = []
        for style, value in raw_fragments:
            for character in value:
                if character == "\n":
                    while line and line[-1][1].isspace():
                        line.pop()
                    compacted.extend(line)
                    compacted.append(("", "\n"))
                    line = []
                else:
                    line.append((style, character))
        while line and line[-1][1].isspace():
            line.pop()
        compacted.extend(line)
        merged: list[tuple[str, str]] = []
        for style, value in compacted:
            if merged and merged[-1][0] == style:
                merged[-1] = (style, merged[-1][1] + value)
            else:
                merged.append((style, value))
        return merged

    def _thread_main(self) -> None:
        try:
            self._run()
        except BaseException as exc:
            self._thread_error = exc
        finally:
            if self._thread is threading.current_thread():
                self._thread = None

    def _run(self) -> None:
        bindings = KeyBindings()

        @bindings.add("escape")
        def handle_escape(_event) -> None:
            if not self._responding.is_set():
                return
            if self._escape_detector.press():
                self.on_interrupt()
            else:
                self.on_first_escape()

        @bindings.add("c-c", eager=True)
        def force_exit(_event) -> None:
            if not self._responding.is_set():
                _event.app.exit(exception=KeyboardInterrupt())
                return
            os.kill(os.getpid(), signal.SIGINT)

        @bindings.add("c-d")
        def handle_eof(event) -> None:
            if not event.current_buffer.text:
                if not self._responding.is_set():
                    event.app.exit(exception=EOFError())
                else:
                    force_exit(event)
            else:
                event.current_buffer.delete()

        @bindings.add("enter")
        def submit(event) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        @bindings.add("c-j")
        def newline(event) -> None:
            event.current_buffer.insert_text("\n")

        def accept_input(buffer) -> bool:
            text = buffer.text
            if not text.strip():
                return True
            self._draft = ""
            self._escape_detector.reset()
            if not self._responding.is_set():
                self._messages.put(text)
            else:
                self.on_submit(text)
            return False

        output_area = Window(
            content=FormattedTextControl(self._formatted_output),
            wrap_lines=True,
            always_hide_cursor=True,
            height=Dimension(min=1, weight=1),
        )
        def input_prefix(line_number: int, wrap_count: int) -> FormattedText:
            prompt = self.prompt()
            if line_number == 0 and wrap_count == 0:
                return FormattedText([("class:user", prompt)])
            return FormattedText([("", " " * get_cwidth(prompt))])

        input_area = TextArea(
            text=self._draft,
            get_line_prefix=input_prefix,
            multiline=True,
            history=self.history,
            auto_suggest=AutoSuggestFromHistory(),
            lexer=PygmentsLexer(PythonLexer),
            wrap_lines=True,
            style="class:input-field",
            accept_handler=accept_input,
            height=Dimension.exact(2),
        )
        toolbar = Window(
            content=FormattedTextControl(
                lambda: FormattedText(
                    [
                        (
                            "class:bottom-toolbar",
                            self.bottom_toolbar() if self.bottom_toolbar else "",
                        )
                    ]
                )
            ),
            height=1,
            style="class:bottom-toolbar",
        )
        application = Application(
            layout=Layout(
                HSplit([
                    output_area,
                    HSplit([
                        Window(height=1, style="class:input-field"),
                        VSplit([
                            Window(width=1, style="class:input-field"),
                            input_area,
                            Window(width=1, style="class:input-field"),
                        ]),
                        Window(height=1, style="class:input-field"),
                    ], style="class:input-field"),
                    Window(height=1),
                    toolbar,
                ]),
                focused_element=input_area,
            ),
            key_bindings=bindings,
            full_screen=True,
            erase_when_done=True,
            max_render_postpone_time=None,
            refresh_interval=0.03,
            after_render=self._after_render,
            input=self.toolkit_input,
            output=_ScreenOutput(
                self.toolkit_output or get_app_session().output,
                self.on_screen_enter,
                self.on_screen_exit,
            ),
            style=Style.from_dict(
                {
                    "reasoning": "fg:#5fafd7 italic",
                    "reasoning-title": "fg:#5fafd7 bold italic",
                    "answer-title": "fg:#d75fd7 bold",
                    "tool": "fg:#d7af5f",
                    "tool-result": "fg:#5faf87",
                    "user": "fg:#5fd7d7 bold",
                    "error": "fg:#ff5f5f bold",
                    "interrupt": "fg:#d7af5f bold",
                    "bottom-toolbar": "reverse",
                    "input-field": "bg:#000000 fg:#eeeeee",
                }
            ),
        )
        application.ttimeoutlen = 0.05
        with self._application_lock:
            self._input_area = input_area
            self._output_area = output_area
        try:
            application.run(
                pre_run=self._capture_application, handle_sigint=False
            )
        finally:
            with self._application_lock:
                self._application = None
                self._input_area = None
                self._output_area = None

    def _capture_application(self) -> None:
        application = get_app()
        with self._application_lock:
            self._application = application
        if self._stop_event.is_set():
            application.exit(result="")

    def _after_render(self, _application) -> None:
        with self._render_condition:
            self._rendered_revision = max(
                self._rendered_revision,
                self._displayed_revision,
            )
            self._render_condition.notify_all()

    @staticmethod
    def _exit_application(application: Any) -> None:
        def exit_now() -> None:
            if not application.is_done:
                application.exit(result="")

        future = getattr(application, "future", None)
        loop = future.get_loop() if future is not None else None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(exit_now)
        elif future is not None:
            exit_now()


for shift_enter_sequence in ("\x1b[27;2;13~", "\x1b[13;2u"):
    ANSI_SEQUENCES[shift_enter_sequence] = (Keys.Escape, Keys.ControlM)
for letter_index, letter in enumerate("abcdefghijklmnopqrstuvwxyz", start=1):
    control_key = getattr(Keys, f"Control{letter.upper()}")
    ANSI_SEQUENCES[f"\x1b[{ord(letter)};5u"] = control_key
    ANSI_SEQUENCES[f"\x1b[{ord(letter)};6u"] = control_key
ANSI_SEQUENCES["\x1b[27u"] = Keys.Escape
ANSI_SEQUENCES["\x1b[32;5u"] = Keys.ControlAt
ANSI_SEQUENCES["\x1b[91;5u"] = Keys.Escape
ANSI_SEQUENCES["\x1b[92;5u"] = Keys.ControlBackslash
ANSI_SEQUENCES["\x1b[93;5u"] = Keys.ControlSquareClose
def enable_enhanced_keyboard_protocol(
    stream: TextIO | None = None,
) -> str | None:
    output = stream or sys.stdout
    if not output.isatty():
        return None

    term = os.environ.get("TERM", "").lower()
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    kitty_protocol = (
        any(name in term for name in ("kitty", "foot", "ghostty", "wezterm"))
        or term_program in {"kitty", "foot", "ghostty", "wezterm"}
        or any(
            variable in os.environ
            for variable in ("KITTY_WINDOW_ID", "WEZTERM_PANE", "GHOSTTY_RESOURCES_DIR")
        )
    )
    if kitty_protocol:
        output.write("\x1b[>1u")
        protocol = "kitty"
    elif term and term != "dumb":
        output.write("\x1b[>4;2m")
        protocol = "xterm"
    else:
        return None
    output.flush()
    return protocol

def disable_enhanced_keyboard_protocol(
    protocol: str | None,
    stream: TextIO | None = None,
) -> None:
    if protocol is None:
        return
    output = stream or sys.stdout
    output.write("\x1b[<u" if protocol == "kitty" else "\x1b[>4;0m")
    output.flush()
console = Console()
LOGO = r"""
███╗   ██╗███████╗██╗   ██╗██████╗  ██████╗      ██████╗██╗     ██╗
████╗  ██║██╔════╝██║   ██║██╔══██╗██╔═══██╗    ██╔════╝██║     ██║
██╔██╗ ██║█████╗  ██║   ██║██████╔╝██║   ██║    ██║     ██║     ██║
██║╚██╗██║██╔══╝  ██║   ██║██╔══██╗██║   ██║    ██║     ██║     ██║
██║ ╚████║███████╗╚██████╔╝██║  ██║╚██████╔╝    ╚██████╗███████╗██║
╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝      ╚═════╝╚══════╝╚═╝
"""

def build_bottom_toolbar(handler=None) -> str:
    from src.main.api.api_manager import MODEL, REASONING_EFFORT, REASONING_ENABLED

    model = handler.agent.model if handler is not None else MODEL
    effort = handler.agent.reasoning_effort if handler is not None else REASONING_EFFORT
    enabled = (handler.reasoning_enabled and handler.agent.thinking) if handler is not None else REASONING_ENABLED
    reasoning_effort = effort or tr("reasoning_default")
    if not enabled:
        reasoning_effort = tr("reasoning_off")
    return (
        f" {model} {reasoning_effort} · {get_current_path()}"
    )


def render_welcome() -> None:
    """Clear the terminal and redraw the startup panel in the active language."""
    from src.main.api.api_manager import BASE_URL, MODEL

    console.clear()
    content = (
        f"[cyan]{LOGO}[/cyan]\n\n{tr('tagline')}\n"
        f"{tr('help_hint')}\n\n\n"
        f"{tr('base_url')}: {BASE_URL}\n"
        f"{tr('model')}: {MODEL}\n"
        f"{tr('current_dir')}: {get_current_path()}\n"
    )
    console.print(Panel.fit(content, border_style="cyan"))
    console.print()


def build_exit_message() -> str:
    from src.main.api.api_manager import USAGE_TRACKER

    usage = USAGE_TRACKER.snapshot()
    return tr(
        "exit_summary",
        total_tokens=f"{usage.total_tokens:,}",
        cached_tokens=f"{usage.cached_tokens:,}",
        cache_hit_rate=f"{usage.cache_hit_rate:.2f}%",
    )


def main():
    from src.main.msg.command_utils import CommandManager
    from src.main.msg.session_manager import SessionManager

    session_manager = SessionManager()
    command_manager = CommandManager(
        console,
        session_manager,
        translator=tr,
        language_getter=get_language,
        language_setter=set_language,
        language_names=LANGUAGE_NAMES,
        language_changed_callback=render_welcome,
        exit_message_getter=build_exit_message,
    )
    keyboard_protocol = None

    def enter_screen():
        nonlocal keyboard_protocol
        keyboard_protocol = enable_enhanced_keyboard_protocol()

    def exit_screen():
        nonlocal keyboard_protocol
        disable_enhanced_keyboard_protocol(keyboard_protocol)
        keyboard_protocol = None

    conversation_input = ConversationInput(
        lambda: tr("user_prompt"),
        lambda text: session_manager.current_handler.queue_user_message(text),
        lambda: conversation_input.append_output(
            f"\n{tr('escape_interrupt_hint')}\n", "class:interrupt"
        ),
        lambda: session_manager.current_handler.interrupt(),
        bottom_toolbar=lambda: build_bottom_toolbar(session_manager.current_handler),
        history=session_manager.prompt_history,
        on_screen_enter=enter_screen,
        on_screen_exit=exit_screen,
    )
    with console.capture() as welcome:
        render_welcome()
    conversation_input.append_output(welcome.get())
    try:
        while True:
            msg_handler = None
            try:
                user_input = conversation_input.read_input()
                if not user_input.strip():
                    continue
                conversation_input.append_output(
                    f"\n{tr('user_prompt')}{user_input}\n", "class:user"
                )
                if user_input.startswith("/"):
                    if user_input.strip().split()[0].lower() == "/clear":
                        conversation_input.clear_output()
                    with console.capture() as command_output:
                        should_exit = command_manager.execute(user_input)
                    conversation_input.append_output(command_output.get())
                    if should_exit:
                        break
                    continue
                session_manager.ensure_current_session()
                session_manager.activate_current_session()
                msg_handler = session_manager.current_handler
                msg_handler.set_interaction_callbacks(
                    conversation_input.stop,
                    conversation_input.start,
                )
                conversation_input.begin_response()
                if msg_handler.use_stream:
                    if msg_handler.reasoning_enabled:
                        displayed_kind = None
                        for event in msg_handler.get_response_events(user_input):
                            if event.kind == "reasoning":
                                if displayed_kind != "reasoning":
                                    conversation_input.append_output(
                                        f"\n{tr('thinking')}\n",
                                        "class:reasoning-title",
                                    )
                                    displayed_kind = "reasoning"
                                conversation_input.append_output(
                                    event.content,
                                    "class:reasoning",
                                )
                            elif event.kind == "content":
                                if displayed_kind != "content":
                                    conversation_input.append_output(
                                        "\nNeuro >\n",
                                        "class:answer-title",
                                    )
                                    displayed_kind = "content"
                                conversation_input.append_markdown(event.content)
                            elif event.kind in {"tool", "tool_result"}:
                                label = tr("tool_call") if event.kind == "tool" else tr("tool_result")
                                conversation_input.append_output(
                                    f"\n{label} > {event.content}\n",
                                    "class:tool" if event.kind == "tool" else "class:tool-result",
                                )
                                displayed_kind = event.kind
                            elif event.kind == "interrupted":
                                conversation_input.append_output(
                                    f"\n{event.content}\n",
                                    "class:interrupt",
                                )
                                displayed_kind = "interrupted"
                            elif event.kind == "queued_user":
                                conversation_input.append_output(
                                    f"\n{tr('user_prompt')}{event.content}\n",
                                    "class:user",
                                )
                                displayed_kind = "queued_user"
                            else:
                                conversation_input.append_output(
                                    f"\n{tr('error')} > {event.content}\n",
                                    "class:error",
                                )
                                displayed_kind = "error"
                        if displayed_kind is None:
                            conversation_input.append_output(
                                "\nNeuro >\n",
                                "class:answer-title",
                            )
                    else:
                        conversation_input.append_output(
                            "\nNeuro >\n",
                            "class:answer-title",
                        )
                        for chunk in msg_handler.get_response_stream(user_input):
                            conversation_input.append_markdown(chunk)
                    conversation_input.append_output("\n")
                else:
                    reply = msg_handler.get_response(user_input)
                    conversation_input.append_output(
                        "\nNeuro >\n",
                        "class:answer-title",
                    )
                    conversation_input.append_markdown(reply)
                    conversation_input.append_output("\n")
            except (KeyboardInterrupt, EOFError):
                conversation_input.append_output(f"\n{build_exit_message()}\n")
                break
            finally:
                conversation_input.finish_response()
                if msg_handler is not None:
                    msg_handler.set_interaction_callbacks(None, None)
                try:
                    session_manager.save_current_session()
                except (OSError, TypeError, ValueError) as exc:
                    conversation_input.append_output(
                        f"\n{tr('session_save_failed', error=exc)}\n", "class:error"
                    )
    finally:
        conversation_input.stop()
    conversation_input.render_transcript(console)


if __name__ == "__main__":
    main()
