from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable

from src.main.api.provider import RequestContext


def get_current_path() -> str:
    return os.getcwd()


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    content: str


class AgentContext:
    def _initialize_context(self) -> None:
        self.event_sink: Callable[[str, dict[str, Any]], None] | None = None
        self._read_paths: set[Path] = set()
        self._last_failed_call: str | None = None
        self._cancel_event = Event()
        self._request_context = RequestContext(
            self._cancel_event, lambda usage: self._emit_event("token_usage", usage),
            lambda metadata: self._emit_event("response_metadata", metadata),
        )
        self._interaction_paused: Callable[[], None] | None = None
        self._interaction_resumed: Callable[[], None] | None = None
        self._pending_user_messages: deque[str] = deque()
        self._pending_messages_lock = Lock()
        self.reset()

    def reset(self) -> None:
        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self.system_prompt + f"\n\n当前工作目录: {self.workspace}",
            }
        ]
        self._read_paths.clear()
        self._last_failed_call = None

    def _emit_event(self, kind: str, payload: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(kind, payload)

    def _append_message(self, message: dict[str, Any]) -> None:
        kind = {"user": "user_message", "assistant": "assistant_message", "tool": "tool_result", "system": "system_message"}[message["role"]]
        self._emit_event(kind, {"message": message})
        self.messages.append(message)

    def add_user_message(self, text: str) -> None:
        self._append_message({"role": "user", "content": text})

    def queue_user_message(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._pending_messages_lock:
            self._pending_user_messages.append(text)

    def _append_pending_user_messages(self) -> tuple[str, ...]:
        with self._pending_messages_lock:
            pending = tuple(self._pending_user_messages)
            self._pending_user_messages.clear()
        for text in pending:
            self.add_user_message(text)
        return pending

    def interrupt(self) -> None:
        self._request_context.cancel()

    def set_interaction_callbacks(
        self,
        paused: Callable[[], None] | None,
        resumed: Callable[[], None] | None,
    ) -> None:
        self._interaction_paused = paused
        self._interaction_resumed = resumed

    def _record_error(self, content: str) -> str:
        self._append_message({"role": "assistant", "content": content})
        return content
