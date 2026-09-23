"""Explicit API runtime initialization; importing this module performs no I/O."""
from dataclasses import dataclass, field

from src.main.api.provider import ModelProvider
from src.main.api.factory import create_provider

from src.main.config import AppConfig, load_config
from src.main.api.usage import UsageTracker


@dataclass
class APIRuntime:
    config: AppConfig
    provider: ModelProvider
    usage_tracker: UsageTracker = field(default_factory=UsageTracker)

    def close(self) -> None:
        global _default_runtime
        try:
            self.provider.close()
        finally:
            if _default_runtime is self:
                _default_runtime = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


_default_runtime: APIRuntime | None = None


def create_runtime(config: AppConfig, *, provider: ModelProvider | None = None,
                   usage_tracker: UsageTracker | None = None) -> APIRuntime:
    """Create independent dependencies without reading files or changing defaults."""
    tracker = usage_tracker if usage_tracker is not None else UsageTracker()
    return APIRuntime(
        config,
        provider if provider is not None else create_provider(config.api, tracker),
        tracker,
    )


def initialize(config: AppConfig, *, provider: ModelProvider | None = None) -> APIRuntime:
    """Install an explicitly configured default for callers using module helpers."""
    global _default_runtime
    if _default_runtime is not None:
        raise RuntimeError("API runtime is already initialized; close it before reinitializing")
    _default_runtime = create_runtime(config, provider=provider)
    return _default_runtime


def get_runtime() -> APIRuntime:
    if _default_runtime is None:
        raise RuntimeError("API runtime is not initialized; call initialize(load_config()) or pass runtime explicitly")
    return _default_runtime


def list_models(*, runtime: APIRuntime | None = None) -> list[str]:
    runtime = runtime if runtime is not None else get_runtime()
    return runtime.provider.list_models()


def _stream_chunks(response):
    try:
        for chunk in response:
            choices = chunk.get("choices") or []
            if choices and choices[0]["delta"].get("content"):
                yield choices[0]["delta"]["content"]
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


def get_completion(messages, stream=False, temperature=None, *, runtime: APIRuntime | None = None):
    runtime = runtime if runtime is not None else get_runtime()
    settings = runtime.config.api
    use_stream = settings.stream if stream is None else stream
    options = dict(model=settings.model,
                   temperature=settings.temperature if temperature is None else temperature)
    if use_stream:
        return _stream_chunks(runtime.provider.stream(messages, **options))
    return runtime.provider.complete(messages, **options).get("content")


def get_completion_stream(messages, temperature=None, *, runtime: APIRuntime | None = None):
    yield from get_completion(messages, stream=True, temperature=temperature, runtime=runtime)


if __name__ == "__main__":
    with initialize(load_config()) as runtime:
        print(get_completion([{"role": "user", "content": "你好，可以给我做个自我介绍吗?"}], runtime=runtime))
