"""Explicit API runtime initialization; importing this module performs no I/O."""
from dataclasses import dataclass, field

from openai import OpenAI

from src.main.config import AppConfig, load_config
from src.main.api.usage import UsageTracker


@dataclass
class APIRuntime:
    config: AppConfig
    client: OpenAI
    usage_tracker: UsageTracker = field(default_factory=UsageTracker)

    def close(self) -> None:
        global _default_runtime
        try:
            self.client.close()
        finally:
            if _default_runtime is self:
                _default_runtime = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


_default_runtime: APIRuntime | None = None


def create_runtime(config: AppConfig, *, client: OpenAI | None = None,
                   usage_tracker: UsageTracker | None = None) -> APIRuntime:
    """Create independent dependencies without reading files or changing defaults."""
    return APIRuntime(
        config,
        client if client is not None else OpenAI(base_url=config.api.base_url, api_key=config.api.api_key),
        usage_tracker if usage_tracker is not None else UsageTracker(),
    )


def initialize(config: AppConfig, *, client: OpenAI | None = None) -> APIRuntime:
    """Install an explicitly configured default for callers using module helpers."""
    global _default_runtime
    if _default_runtime is not None:
        raise RuntimeError("API runtime is already initialized; close it before reinitializing")
    _default_runtime = create_runtime(config, client=client)
    return _default_runtime


def get_runtime() -> APIRuntime:
    if _default_runtime is None:
        raise RuntimeError("API runtime is not initialized; call initialize(load_config()) or pass runtime explicitly")
    return _default_runtime


def list_models(*, runtime: APIRuntime | None = None) -> list[str]:
    runtime = runtime if runtime is not None else get_runtime()
    page = runtime.client.with_options(timeout=15.0, max_retries=0).models.list()
    return sorted({model.id for model in page.data if isinstance(model.id, str) and model.id.strip()})


def _error_chunks(message):
    yield message


def _stream_chunks(response, runtime):
    try:
        for chunk in response:
            if chunk.usage is not None:
                runtime.usage_tracker.record(chunk.usage.model_dump())
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as exc:
        yield f"API Error: {exc}"
    finally:
        response.close()


def get_completion(messages, stream=False, temperature=None, *, runtime: APIRuntime | None = None):
    runtime = runtime if runtime is not None else get_runtime()
    settings = runtime.config.api
    use_stream = settings.stream if stream is None else stream
    try:
        response = runtime.client.chat.completions.create(
            model=settings.model, messages=messages, stream=use_stream,
            temperature=settings.temperature if temperature is None else temperature,
        )
        if use_stream:
            return _stream_chunks(response, runtime)
        if response.usage is not None:
            runtime.usage_tracker.record(response.usage.model_dump())
        return response.choices[0].message.content
    except Exception as exc:
        # Bind text now: Python clears exc when this exception handler exits.
        message = f"API Error: {exc}"
        return _error_chunks(message) if use_stream else message


def get_completion_stream(messages, temperature=None, *, runtime: APIRuntime | None = None):
    yield from get_completion(messages, stream=True, temperature=temperature, runtime=runtime)


if __name__ == "__main__":
    with initialize(load_config()) as runtime:
        print(get_completion([{"role": "user", "content": "你好，可以给我做个自我介绍吗?"}], runtime=runtime))
