from copy import deepcopy
from dataclasses import dataclass, field, fields
import json
from typing import Any, Mapping


@dataclass(frozen=True)
class ModelCapabilities:
    tools: bool | None = None
    reasoning: bool | None = None
    reasoning_effort: bool | None = None
    temperature: bool | None = None
    stream_usage: bool | None = None

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is not None and type(value) is not bool:
                raise ValueError(f"CAPABILITIES.{item.name.upper()} must be a boolean")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ModelCapabilities":
        names = {item.name.upper() for item in fields(cls)}
        if not isinstance(data, dict) or data.keys() - names:
            raise ValueError("Invalid model CAPABILITIES table")
        return cls(**{key.lower(): value for key, value in data.items()})


MANAGED_PARAMETERS = frozenset({
    "model", "messages", "stream", "stream_options", "tools", "tool_choice",
    "temperature", "thinking", "enable_thinking", "reasoning_effort",
})


@dataclass(frozen=True)
class ModelOptions:
    api_model: str | None = None
    thinking_format: str | None = None
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    extra_body: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.api_model is not None and (
            not isinstance(self.api_model, str) or not self.api_model.strip()
        ):
            raise ValueError("API_MODEL must be a non-empty string")
        if self.thinking_format is not None and self.thinking_format not in (
            "none", "thinking", "enable_thinking",
        ):
            raise ValueError("THINKING_FORMAT must be none, thinking or enable_thinking")
        if not isinstance(self.capabilities, ModelCapabilities):
            raise ValueError("CAPABILITIES must contain model capabilities")
        if not isinstance(self.extra_body, dict) or any(
            not isinstance(key, str) for key in self.extra_body
        ):
            raise ValueError("EXTRA_BODY must be a table with string keys")
        if MANAGED_PARAMETERS.intersection(self.extra_body):
            raise ValueError("EXTRA_BODY cannot override managed request parameters")
        try:
            json.dumps(self.extra_body, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("EXTRA_BODY must contain finite JSON values") from None
        object.__setattr__(self, "extra_body", deepcopy(self.extra_body))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ModelOptions":
        names = {"API_MODEL", "THINKING_FORMAT", "CAPABILITIES", "EXTRA_BODY"}
        if not isinstance(data, dict) or data.keys() - names:
            raise ValueError("Invalid model options table")
        return cls(
            api_model=data.get("API_MODEL"), thinking_format=data.get("THINKING_FORMAT"),
            capabilities=ModelCapabilities.from_mapping(data.get("CAPABILITIES", {})),
            extra_body=data.get("EXTRA_BODY", {}),
        )


def parse_model_options(data: Mapping[str, Any]) -> dict[str, ModelOptions]:
    if not isinstance(data, dict):
        raise ValueError("API_MANAGER.MODELS must be a table")
    return {name: ModelOptions.from_mapping(options) for name, options in data.items()}
