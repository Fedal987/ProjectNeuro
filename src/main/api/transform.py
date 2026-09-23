from copy import deepcopy
from dataclasses import fields
from typing import Any, Mapping
from urllib.parse import urlsplit

from src.main.model_config import ModelCapabilities, ModelOptions


PROVIDER_DEFAULTS: dict[str, ModelOptions] = {
    "openai_compatible": ModelOptions(thinking_format="none"),
    "deepseek": ModelOptions(thinking_format="thinking"),
    "siliconflow": ModelOptions(thinking_format="enable_thinking"),
}


def resolve_provider(provider: str, base_url: str) -> str:
    if provider != "auto":
        return provider
    hostname = (urlsplit(base_url).hostname or "").lower()
    if hostname == "api.deepseek.com":
        return "deepseek"
    if hostname in {"siliconflow.cn", "siliconflow.com"} or hostname.endswith(
        (".siliconflow.cn", ".siliconflow.com")
    ):
        return "siliconflow"
    return "openai_compatible"


def _merge_body(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_body(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


class RequestTransform:
    def __init__(self, *, provider: str = "openai_compatible",
                 defaults: ModelOptions | None = None,
                 models: Mapping[str, ModelOptions] | None = None) -> None:
        self.provider = provider
        self.defaults = deepcopy(defaults) if defaults is not None else ModelOptions()
        self.models = deepcopy(dict(models or {}))

    def options_for(self, model: str) -> ModelOptions:
        capabilities = {item.name: True for item in fields(ModelCapabilities)}
        thinking_format = "none"
        api_model = model
        extra_body: dict[str, Any] = {}
        for options in (
            PROVIDER_DEFAULTS.get(self.provider, PROVIDER_DEFAULTS["openai_compatible"]),
            self.defaults,
            self.models.get(model, ModelOptions()),
        ):
            if options.thinking_format is not None:
                thinking_format = options.thinking_format
            if options.api_model is not None:
                api_model = options.api_model
            for item in fields(ModelCapabilities):
                value = getattr(options.capabilities, item.name)
                if value is not None:
                    capabilities[item.name] = value
            extra_body = _merge_body(extra_body, options.extra_body)
        return ModelOptions(
            api_model=api_model, thinking_format=thinking_format,
            capabilities=ModelCapabilities(**capabilities), extra_body=extra_body,
        )

    def apply(self, messages: list[dict[str, Any]], *, model: str,
              temperature: float | None, tools: list[dict[str, Any]] | None,
              thinking: bool, reasoning_effort: str, stream: bool) -> dict[str, Any]:
        options = self.options_for(model)
        capabilities = options.capabilities
        payload = deepcopy(options.extra_body)
        payload.update(model=options.api_model, messages=messages, stream=stream)
        if capabilities.temperature and temperature is not None:
            payload["temperature"] = temperature
        if stream and capabilities.stream_usage:
            payload["stream_options"] = {"include_usage": True}
        if tools and capabilities.tools:
            payload.update(tools=tools, tool_choice="auto")
        if capabilities.reasoning:
            if reasoning_effort and capabilities.reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
            if options.thinking_format == "thinking":
                payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
            elif options.thinking_format == "enable_thinking":
                payload["enable_thinking"] = thinking
        return payload
