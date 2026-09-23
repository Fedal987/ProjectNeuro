
from __future__ import annotations
from collections.abc import Callable
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from src.main.api.exceptions import ProviderError
from src.main.msg.session_manager import SessionManager


class CommandManager:
    def __init__(
        self,
        console: Console,
        session_manager: SessionManager,
        *,
        translator: Callable[..., str] | None = None,
        language_getter: Callable[[], str] | None = None,
        language_setter: Callable[[str], str] | None = None,
        language_names: dict[str, str] | None = None,
        language_changed_callback: Callable[[], None] | None = None,
        exit_message_getter: Callable[[], str] | None = None,
    ) -> None:
        self.console = console
        self.session_manager = session_manager
        self.tr = translator or (lambda key, **_values: key)
        self.language_getter = language_getter or (lambda: "en")
        self.language_setter = language_setter
        self.language_names = language_names or {"en": "English"}
        self.language_changed_callback = language_changed_callback
        self.exit_message_getter = exit_message_getter or (lambda: self.tr("goodbye"))
        self._commands: dict[str, Callable[[str], bool]] = {
            "/help": self._show_help,
            "/lang": self._language,
            "/language": self._language,
            "/exit": self._exit,
            "/clear": self._clear,
            "/reset": self._reset,
            "/echo": self._echo,
            "/session": self._session,
            "/model": self._model,
        }

    def execute(self, user_input: str) -> bool:
        parts = user_input.strip().split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else ""
        handler = self._commands.get(command)
        if handler is None:
            self.console.print(
                f"[red]{self.tr('unknown_command', command=command)}[/red]"
            )
            return False
        return handler(argument)

    def _show_help(self, _argument: str) -> bool:
        rows = (
            ("/help", "help_help"),
            (self.tr("help_lang_command"), "help_lang"),
            ("/model [model] [effort]", "help_model"),
            ("/model list", "help_model_list"),
            ("/exit", "help_exit"),
            ("/clear", "help_clear"),
            ("/reset", "help_reset"),
            (self.tr("help_echo_command"), "help_echo"),
            ("/session", "help_session"),
            ("/session list", "help_session_list"),
            (self.tr("help_session_switch_command"), "help_session_switch"),
            (self.tr("help_session_new_command"), "help_session_new"),
        )
        table_rows = "\n".join(
            f"| `{command}` | {self.tr(description)} |"
            for command, description in rows
        )
        help_text = (
            f"## {self.tr('help_title')}\n\n"
            f"| {self.tr('help_command')} | {self.tr('help_description')} |\n"
            "|---|---|\n"
            f"{table_rows}\n\n"
            f"**{self.tr('help_input_label')}**: {self.tr('help_input')}\n\n"
            f"**{self.tr('help_history_label')}**: {self.tr('help_history')}\n\n"
            f"**{self.tr('help_syntax_label')}**: {self.tr('help_syntax')}"
        )
        self.console.print(Markdown(help_text))
        return False

    def _language(self, argument: str) -> bool:
        requested = argument.strip()
        if not requested or requested.lower() == "list":
            current = self.language_getter()
            self.console.print(
                f"[bold]{self.tr('language_current')}:[/bold] "
                f"{self.language_names.get(current, current)} ({current})"
            )
            self.console.print(f"[bold]{self.tr('language_available')}:[/bold]")
            for code, name in self.language_names.items():
                self.console.print(f"  {code:<5} {name}")
            self.console.print(f"[dim]{self.tr('language_usage')}[/dim]")
            return False
        if self.language_setter is None:
            self.console.print(f"[red]{self.tr('language_unavailable')}[/red]")
            return False
        try:
            selected = self.language_setter(requested)
        except ValueError:
            self.console.print(
                f"[red]{self.tr('language_invalid', language=requested)}[/red]"
            )
            return False
        name = self.language_names.get(selected, selected)
        if self.language_changed_callback is not None:
            self.language_changed_callback()
        self.console.print(
            f"[green]{self.tr('language_changed', language=name, code=selected)}[/green]"
        )
        return False

    def _model(self, argument: str) -> bool:
        handler = self.session_manager.current_handler
        parts = argument.split()
        if parts and parts[0].lower() == "list":
            if len(parts) != 1:
                self.console.print(self.tr("model_usage"), markup=False)
                return False
            from src.main.api.api_manager import list_models

            try:
                models = list_models(runtime=handler.runtime)
            except (ProviderError, ValueError, TypeError, AttributeError) as exc:
                self.console.print(
                    self.tr("model_list_failed", error=exc), style="red", markup=False,
                )
                return False
            if not models:
                self.console.print(self.tr("model_list_empty"), markup=False)
                return False
            self.console.print(self.tr("help_model_list"), style="bold", markup=False)
            for model in models:
                marker = " *" if model == handler.agent.model else ""
                self.console.print(f"  {model}{marker}", markup=False)
            self.console.print(self.tr("model_list_hint"), style="dim", markup=False)
            return False
        if parts:
            if parts[0].lower() == "effort":
                if len(parts) != 2:
                    self.console.print(self.tr("model_usage"), markup=False)
                    return False
                model, effort = None, parts[1].lower()
            elif len(parts) <= 2:
                model = parts[0]
                effort = parts[1].lower() if len(parts) == 2 else None
            else:
                self.console.print(self.tr("model_usage"), markup=False)
                return False
            try:
                handler.set_model(model, effort)
            except ValueError as exc:
                self.console.print(str(exc), style="red", markup=False)
                return False
        effort = handler.agent.reasoning_effort or self.tr("reasoning_default")
        if not handler.reasoning_enabled or not handler.agent.thinking:
            effort = self.tr("reasoning_off")
        self.console.print(
            self.tr("model_current", model=handler.agent.model, effort=effort),
            style="cyan", markup=False,
        )
        if not parts:
            self.console.print(self.tr("model_usage"), markup=False)
        return False

    def _exit(self, _argument: str) -> bool:
        self.console.print(f"[bold yellow]{self.exit_message_getter()}[/bold yellow]")
        return True

    def _clear(self, _argument: str) -> bool:
        self.console.clear()
        self.session_manager.reset_current_session()
        return False

    def _reset(self, _argument: str) -> bool:
        if not self.session_manager.has_current_session:
            self.console.print(f"[dim]{self.tr('no_active_session')}[/dim]")
            return False
        self.session_manager.reset_current_session()
        self.console.print(
            f"[green]{self.tr('session_reset', name=self.session_manager.current_name)}[/green]"
        )
        return False

    def _echo(self, argument: str) -> bool:
        if not argument.strip():
            self.console.print(f"[yellow]{self.tr('echo_required')}[/yellow]")
        else:
            self.console.print(
                Panel(
                    argument.strip(),
                    title=f"[bold]{self.tr('echo_title')}[/bold]",
                    border_style="cyan",
                )
            )
        return False

    def _session(self, argument: str) -> bool:
        argument = argument.strip()
        if not argument or argument.lower() == "list":
            self._show_sessions()
            return False

        subcommand = argument.split(maxsplit=1)
        if subcommand[0].lower() == "new":
            name = subcommand[1] if len(subcommand) > 1 else None
            try:
                selected = self.session_manager.create_session(name)
            except (OSError, TypeError, ValueError) as exc:
                self.console.print(
                    f"[red]{self.tr('session_create_failed', error=exc)}[/red]"
                )
                return False
            self.console.print(
                f"[green]{self.tr('session_created', name=selected.name)}[/green]"
            )
            return False

        target: str | int = int(argument) if argument.isdigit() else argument
        try:
            selected = self.session_manager.switch_session(
                target,
                workspace=self.session_manager.current_workspace,
            )
        except (OSError, TypeError, ValueError, IndexError, KeyError) as exc:
            self.console.print(
                f"[red]{self.tr('session_switch_failed', error=exc)}[/red]"
            )
            return False
        self.console.print(
            f"[green]{self.tr('session_switched', name=selected.name)}[/green]"
        )
        return False

    def _show_sessions(self) -> None:
        workspace = self.session_manager.current_workspace
        sessions = self.session_manager.list_sessions(workspace=workspace)
        self.console.print(
            f"[bold]{self.tr('sessions_title')}:[/bold] {workspace}"
        )
        if not sessions:
            self.console.print(f"[dim]{self.tr('no_sessions')}[/dim]")
            return
        for index, session in enumerate(sessions, start=1):
            temporary = (
                f" [yellow]({self.tr('session_temporary')})[/yellow]"
                if session.temporary else ""
            )
            marker = f" [green]({self.tr('session_current')})[/green]" if (
                session.name == self.session_manager.current_name
            ) else ""
            self.console.print(f"  {index}. {session.name}{temporary}{marker}")
