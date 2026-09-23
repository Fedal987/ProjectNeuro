from typing import Callable

from src.main.config import APIConfig
from .openai_compatible import OpenAICompatibleProvider
from .provider import ModelProvider
from .transform import RequestTransform, resolve_provider
from .usage import UsageTracker


def _openai_compatible(config: APIConfig, tracker: UsageTracker) -> ModelProvider:
    return OpenAICompatibleProvider(
        base_url=config.base_url, api_key=config.api_key, usage_tracker=tracker,
        transform=RequestTransform(
            provider=resolve_provider(config.provider, config.base_url),
            defaults=config.defaults, models=config.models,
        ),
    )


PROTOCOL_FACTORIES: dict[str, Callable[[APIConfig, UsageTracker], ModelProvider]] = {
    "openai_compatible": _openai_compatible,
}


def create_provider(config: APIConfig, tracker: UsageTracker) -> ModelProvider:
    factory = PROTOCOL_FACTORIES.get(config.protocol)
    if factory is None:
        raise ValueError(f"Unsupported model API protocol: {config.protocol}")
    return factory(config, tracker)
