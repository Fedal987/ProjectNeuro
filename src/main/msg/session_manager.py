"""
    NeuroCode
    author@Fedal987
    Powered by HeronStudio
    08/17/2026  Ij1chi-Nijika
"""

from __future__ import annotations

import copy
from uuid import uuid4
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit.history import History
from src.main.ui.i18n import tr
from src.main.session import LocalSessionStore, SessionStore, SessionEvent
from src.main.session.migration import import_legacy

if TYPE_CHECKING:
    from src.main.msg.message_handler import MessageHandler


@dataclass
class Session:
    name: str
    handler: MessageHandler | None
    workspace: Path = field(default_factory=lambda: Path.cwd().resolve())
    input_history: list[str] = field(default_factory=list)
    auto_name_pending: bool = False
    temporary: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    last_used_at: datetime = field(default_factory=datetime.now)
    session_id: str = field(default_factory=lambda: str(uuid4()))
    persisted_messages: list[dict[str, Any]] = field(default_factory=list, repr=False)
    persisted_inputs: int = 0
    persisted_metadata: dict[str, Any] = field(default_factory=dict, repr=False)
    persisted: bool = False
    checkpoint_seq: int = 0
    persisted_seq: int = 0


class SessionPromptHistory(History):
    def __init__(self, manager: SessionManager) -> None:
        super().__init__()
        self.manager = manager

    def load_history_strings(self) -> Iterable[str]:
        if not self.manager.has_current_session:
            return
        yield from reversed(self.manager.current_session.input_history)

    def store_string(self, string: str) -> None:
        if (
            self.manager.current_session.temporary
            and string.lstrip().startswith("/")
        ):
            return
        self.manager.append_input_history(string)

    def select_current_session(self) -> None:
        self._loaded = False
        self._loaded_strings = []


class SessionManager:
    def __init__(
        self,
        session_factory: Callable[[], MessageHandler] | None = None,
        default_name: str = "default",
        workspace_provider: Callable[[], Path] = Path.cwd,
        storage_root: str | Path | None = None,
        name_generator: Callable[[list[dict[str, Any]]], str | None] | None = None,
        store: SessionStore | None = None,
    ) -> None:
        if session_factory is None:
            from src.main.msg.message_handler import MessageHandler
            session_factory = MessageHandler
        self._session_factory = session_factory
        self._workspace_provider = workspace_provider
        self._name_generator = name_generator or self._generate_name_with_llm
        self._default_name = default_name
        self.storage_root = Path(storage_root or Path.cwd()).expanduser().resolve()
        self.session_directory = self.storage_root / "session"
        self._sessions: dict[str, Session] = {}
        self._current_name: str | None = None
        self.prompt_history = SessionPromptHistory(self)
        self.load_errors: list[str] = []
        self.naming_errors: list[str] = []
        self.store = store if store is not None else LocalSessionStore(self.session_directory)
        if store is None:
            self.load_errors.extend(import_legacy(self.store, self.session_directory))
            self.load_errors.extend(self.store.errors)
        self._load_sessions()
        self._create_temporary_session()

    @property
    def has_current_session(self) -> bool:
        return self._current_name is not None

    @property
    def current_name(self) -> str | None:
        return self._current_name

    @property
    def current_session(self) -> Session:
        if self._current_name is None:
            raise RuntimeError(tr("session_none_available"))
        session = self._sessions[self._current_name]
        self._ensure_handler(session)
        return session

    @property
    def current_handler(self) -> MessageHandler:
        return self.current_session.handler

    @property
    def current_workspace(self) -> Path:
        return Path(self._workspace_provider()).expanduser().resolve()

    def list_sessions(
        self,
        workspace: str | Path | None = None,
    ) -> tuple[Session, ...]:
        sessions = tuple(
            sorted(self._sessions.values(), key=lambda item: item.created_at.timestamp())
        )
        if workspace is None:
            return sessions
        resolved_workspace = Path(workspace).expanduser().resolve()
        return tuple(
            session
            for session in sessions
            if session.workspace == resolved_workspace
        )

    def create_session(
        self,
        name: str | None = None,
        *,
        switch: bool = True,
        save: bool = True,
        auto_name: bool | None = None,
        temporary: bool = False,
    ) -> Session:
        replace_temporary = (
            switch
            and not temporary
            and self.has_current_session
            and self.current_session.temporary
        )
        if name is not None:
            session_name = self._normalise_name(name)
            duplicate = session_name in self._sessions and not (
                replace_temporary and session_name == self.current_name
            )
            if duplicate:
                raise ValueError(tr("session_exists", name=session_name))
        if replace_temporary:
            self._discard_current_temporary()
        if name is None:
            session_name = self._normalise_name(self._next_name())
        if session_name in self._sessions:
            raise ValueError(tr("session_exists", name=session_name))

        new_session = Session(
            name=session_name,
            handler=self._session_factory(),
            workspace=self.current_workspace,
            auto_name_pending=name is None if auto_name is None else auto_name,
            temporary=temporary,
        )
        self._sessions[session_name] = new_session
        if switch:
            self._current_name = session_name
            self.prompt_history.select_current_session()
        if save and not temporary:
            self.save_session(new_session)
        return new_session

    def ensure_current_session(self) -> Session:
        if self.has_current_session:
            return self.current_session
        return self._create_temporary_session()

    def activate_current_session(self) -> Session:
        session = self.ensure_current_session()
        if session.temporary:
            session.temporary = False
            session.last_used_at = datetime.now()
            self.save_session(session)
        return session

    def switch_session(
        self,
        target: str | int,
        *,
        workspace: str | Path | None = None,
    ) -> Session:
        name = self._resolve_target(target, workspace=workspace)
        if (
            self.has_current_session
            and self.current_session.temporary
            and self.current_name != name
        ):
            self._discard_current_temporary()
        if self.has_current_session:
            self.save_current_session()
        selected = self._sessions[name]
        self._ensure_handler(selected)
        selected.last_used_at = datetime.now()
        self._current_name = name
        self.prompt_history.select_current_session()
        self.save_session(selected)
        return selected

    def select_session(
        self,
        choice: str | int | None = None,
        *,
        input_func: Callable[[str], str] = input,
        output_func: Callable[[str], None] = print,
    ) -> Session | None:
        if choice is not None:
            return self.switch_session(choice)
        output_func(tr("sessions_available"))
        for index, session in enumerate(self.list_sessions(), start=1):
            marker = " *" if session.name == self.current_name else ""
            output_func(f"  {index}. {session.name}{marker}")
        selected = input_func(tr("session_select_prompt")).strip()
        if not selected or selected.lower() == "q":
            return None
        target: str | int = int(selected) if selected.isdigit() else selected
        try:
            return self.switch_session(target)
        except (IndexError, KeyError, ValueError) as exc:
            raise ValueError(tr("session_select_failed", selected=selected)) from exc

    def append_input_history(self, text: str) -> None:
        self.activate_current_session()
        self.current_session.input_history.append(text)
        self.current_session.last_used_at = datetime.now()
        self.save_current_session()

    def reset_current_session(self) -> None:
        if not self.has_current_session:
            return
        self.current_handler.reset()
        session = self.current_session
        if session.persisted:
            self._emit(session, "context_reset", self._snapshot(session))
            session.persisted_messages = copy.deepcopy(session.handler.history)
            session.persisted_inputs = len(session.input_history)
        self.current_session.last_used_at = datetime.now()
        self.save_current_session()

    def auto_name_current_session(self) -> str | None:
        if not self.has_current_session:
            return None
        session = self.current_session
        if not session.auto_name_pending:
            return None
        roles = {
            message.get("role")
            for message in session.handler.history
            if isinstance(message, dict)
        }
        if not {"user", "assistant"}.issubset(roles):
            return None
        try:
            generated = self._name_generator(session.handler.history)
            title = self._clean_generated_name(generated)
        except Exception as exc:  # Naming must never interrupt the conversation.
            self.naming_errors.append(str(exc))
            return None
        if not title:
            return None
        unique_title = self._unique_name(title, exclude=session.name)
        old_name = session.name
        self._rename_session(old_name, unique_title)
        session.auto_name_pending = False
        self.save_session(session)
        return unique_title

    def save_current_session(self) -> None:
        if not self.has_current_session:
            return
        if self.current_session.auto_name_pending:
            self.auto_name_current_session()
        self.save_session(self.current_session)

    def save_all(self) -> None:
        for session in self._sessions.values():
            self.save_session(session)

    def _metadata(self, session):
        agent = getattr(session.handler, "agent", None)
        return {
            "title": session.name,
            "model": getattr(agent, "model", None),
            "reasoning_effort": getattr(agent, "reasoning_effort", None),
            "thinking": getattr(agent, "thinking", None),
            "reasoning_enabled": getattr(session.handler, "reasoning_enabled", None),
            "auto_name_pending": session.auto_name_pending,
        }

    def _snapshot(self, session):
        return dict(
            messages=copy.deepcopy(session.handler.history),
            input_history=list(session.input_history),
            auto_name_pending=session.auto_name_pending,
            reasoning_effort=self._metadata(session)["reasoning_effort"],
            thinking=self._metadata(session)["thinking"],
            reasoning_enabled=self._metadata(session)["reasoning_enabled"],
        )

    def _emit(self, session, kind, payload):
        result = self.store.append_event(SessionEvent(
            session.session_id, kind, copy.deepcopy(payload), seq=session.persisted_seq + 1,
        ))
        session.persisted_seq = result.event.seq
        return result

    def _bind_events(self, session):
        agent = getattr(session.handler, "agent", None)
        if agent is None:
            return

        def persist(kind, payload):
            self._emit(session, kind, payload)
            if "message" in payload:
                session.persisted_messages.append(copy.deepcopy(payload["message"]))
        agent.event_sink = persist

    def _ensure_handler(self, session):
        if session.handler is not None:
            return
        state = self.store.resume_session(session.session_id)
        pending = set()
        for message in state.messages:
            if message.get("role") == "assistant":
                pending.update(call["id"] for call in message.get("tool_calls", []))
            elif message.get("role") == "tool":
                pending.discard(message.get("tool_call_id"))
        if pending:
            raise ValueError("Session contains unfinished tool calls; inspect the rollout or fork an earlier completed event")
        session.handler = self._session_factory()
        session.handler.history[:] = state.messages
        session.input_history = state.input_history
        session.auto_name_pending = state.auto_name_pending
        agent = getattr(session.handler, "agent", None)
        if agent is not None:
            agent.workspace = session.workspace
            if state.metadata.model:
                agent.model = state.metadata.model
            if state.reasoning_effort is not None:
                agent.reasoning_effort = state.reasoning_effort
            if state.thinking is not None:
                agent.thinking = state.thinking
            if state.reasoning_enabled is not None:
                session.handler.reasoning_enabled = state.reasoning_enabled
        session.persisted_messages = copy.deepcopy(state.messages)
        session.persisted_inputs = len(state.input_history)
        session.checkpoint_seq = state.checkpoint_seq
        session.persisted_seq = state.metadata.last_seq
        session.persisted_metadata = self._metadata(session)
        self._bind_events(session)

    def save_session(self, session: Session) -> None:
        if session.temporary or session.handler is None:
            return
        metadata = self._metadata(session)
        if not session.persisted:
            created = self.store.create_session(
                session_id=session.session_id, workspace=str(session.workspace),
                title=session.name, model=metadata["model"],
                created_at=session.created_at.isoformat(), **self._snapshot(session),
            )
            session.persisted = True
            session.checkpoint_seq = 1
            session.persisted_seq = created.last_seq
            session.persisted_messages = copy.deepcopy(session.handler.history)
            session.persisted_inputs = len(session.input_history)
            session.persisted_metadata = metadata
            self._bind_events(session)
            return
        if metadata != session.persisted_metadata:
            self._emit(session, "session_metadata_updated", metadata)
            session.persisted_metadata = metadata
        history = session.handler.history
        count = len(session.persisted_messages)
        if history[:count] != session.persisted_messages:
            # Reset or a caller replacing context creates a new recovery boundary.
            self._emit(session, "context_reset", self._snapshot(session))
            session.persisted_messages = copy.deepcopy(history)
            session.persisted_inputs = len(session.input_history)
        else:
            for message in history[count:]:
                kind = {"user": "user_message", "assistant": "assistant_message", "tool": "tool_result", "system": "system_message"}[message["role"]]
                self._emit(session, kind, {"message": message})
                session.persisted_messages.append(copy.deepcopy(message))
        for text in session.input_history[session.persisted_inputs:]:
            self._emit(session, "input_submitted", {"text": text})
            session.persisted_inputs += 1
        current = self.store.get_session(session.session_id)
        if current.last_seq - session.checkpoint_seq >= 100:
            self._emit(session, "checkpoint", self._snapshot(session))
            session.checkpoint_seq = current.last_seq + 1

    def checkpoint_current_session(self):
        self.save_current_session()
        session = self.current_session
        if session.persisted:
            result = self._emit(session, "checkpoint", self._snapshot(session))
            session.checkpoint_seq = result.event.seq

    def close(self):
        try:
            self.save_all()
            for session in self._sessions.values():
                if session.persisted and session.handler is not None:
                    self._emit(session, "session_end", {"reason": "runtime_closed"})
        finally:
            close = getattr(self.store, "close", None)
            if close:
                close()

    def _load_sessions(self) -> None:
        for item in self.store.list_sessions(limit=None):
            # Titles remain compatible with name-based CLI selection.
            name = self._unique_name(item.title)
            self._sessions[name] = Session(
                name=name, handler=None, session_id=item.session_id,
                workspace=Path(item.workspace), persisted=True,
                created_at=self._parse_datetime(item.created_at),
                last_used_at=self._parse_datetime(item.updated_at),
            )

    def _resolve_target(
        self,
        target: str | int,
        *,
        workspace: str | Path | None = None,
    ) -> str:
        sessions = self.list_sessions(workspace=workspace)
        if isinstance(target, bool):
            raise ValueError(tr("session_number_positive"))
        if isinstance(target, int):
            if target < 1 or target > len(sessions):
                raise IndexError(tr("session_number_out_of_range", target=target))
            return sessions[target - 1].name
        if isinstance(target, str):
            name = self._normalise_name(target)
            if not any(session.name == name for session in sessions):
                raise KeyError(tr("session_not_found", name=name))
            return name
        raise TypeError(tr("session_selection_type"))

    def _create_temporary_session(self) -> Session:
        name = None if self._sessions else self._default_name
        return self.create_session(
            name,
            switch=True,
            save=False,
            auto_name=True,
            temporary=True,
        )

    def _discard_current_temporary(self) -> None:
        if not self.has_current_session or not self.current_session.temporary:
            return
        temporary_name = self.current_session.name
        del self._sessions[temporary_name]
        self._current_name = None
        self.prompt_history.select_current_session()

    def _rename_session(self, old_name: str, new_name: str) -> None:
        session = self._sessions[old_name]
        session.name = new_name
        self._sessions = {
            (new_name if name == old_name else name): item
            for name, item in self._sessions.items()
        }
        if self._current_name == old_name:
            self._current_name = new_name

    def _unique_name(self, requested: str, *, exclude: str | None = None) -> str:
        existing = set(self._sessions)
        if exclude is not None:
            existing.discard(exclude)
        if requested not in existing:
            return requested
        index = 2
        while f"{requested}-{index}" in existing:
            index += 1
        return f"{requested}-{index}"

    @staticmethod
    def _generate_name_with_llm(messages: list[dict[str, Any]]) -> str | None:
        from src.main.api.api_manager import get_completion

        excerpts: list[str] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                continue
            label = "用户" if role == "user" else "助手"
            excerpts.append(f"{label}: {content}")
        conversation = "\n".join(excerpts)[:6000]
        result = get_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "你是会话标题生成器。根据对话生成一个准确、简短的中文标题，"
                        "建议 2 到 12 个汉字。只输出标题，不要引号、标点、解释或前缀。"
                    ),
                },
                {"role": "user", "content": conversation},
            ],
            stream=False,
            temperature=0.2,
        )
        if not isinstance(result, str) or result.startswith("API Error:"):
            return None
        return result

    @staticmethod
    def _clean_generated_name(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None
        title = value.splitlines()[0].strip(" `\"'“”‘’《》")
        title = re.sub(r"^(会话)?标题\s*[:：]\s*", "", title)
        title = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "", title)
        title = re.sub(r"\s+", "-", title).strip(" .-")
        return title[:40] or None

    def _next_name(self) -> str:
        index = 1
        while f"session-{index}" in self._sessions:
            index += 1
        return f"session-{index}"

    @staticmethod
    def _normalise_name(name: str) -> str:
        if not isinstance(name, str):
            raise TypeError(tr("session_name_string"))
        normalised = name.strip()
        if not normalised:
            raise ValueError(tr("session_name_empty"))
        return normalised

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return datetime.now()
