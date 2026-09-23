from threading import Event, Lock
from typing import Any, Callable, Iterator, Protocol

from .exceptions import ProviderInterrupted


class RequestContext:
    def __init__(self, cancelled: Event, on_usage: Callable[[dict[str, Any]], None],
                 on_response: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.cancelled = cancelled
        self.on_usage = on_usage
        self.on_response = on_response
        self._lock = Lock()
        self._close: Callable[[], None] | None = None

    def check(self) -> None:
        if self.cancelled.is_set():
            raise ProviderInterrupted("Model request interrupted")

    def attach(self, close: Callable[[], None]) -> None:
        with self._lock:
            self._close = close
        if self.cancelled.is_set():
            close()
            self.check()

    def detach(self) -> None:
        with self._lock:
            self._close = None

    def cancel(self) -> None:
        self.cancelled.set()
        with self._lock:
            close = self._close
        if close is not None:
            close()


class ModelProvider(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], *, model: str,
        temperature: float = 0.2, tools: list[dict[str, Any]] | None = None,
        thinking: bool = False, reasoning_effort: str = "",
        request_context: RequestContext | None = None,
    ) -> dict[str, Any]:
        ...

    def stream(
        self, messages: list[dict[str, Any]], *, model: str,
        temperature: float = 0.2, tools: list[dict[str, Any]] | None = None,
        thinking: bool = False, reasoning_effort: str = "",
        request_context: RequestContext | None = None,
    ) -> Iterator[dict[str, Any]]:
        ...

    def list_models(self) -> list[str]: ...

    def close(self) -> None: ...
