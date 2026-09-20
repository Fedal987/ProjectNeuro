"""Thread-safe usage accounting, independent of configuration and UI."""
from dataclasses import dataclass
from threading import Lock
from typing import Any, Mapping


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


